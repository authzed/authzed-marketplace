// Package main demonstrates SpiceDB client patterns in Go.
//
// Run with: go run go-client.go
// Requires: SPICEDB_ENDPOINT and SPICEDB_TOKEN environment variables.
package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"os"
	"strings"
	"time"

	authzed "github.com/authzed/authzed-go/v1"
	v1 "github.com/authzed/authzed-go/v1/proto/authzed/api/v1"
	"github.com/authzed/grpcutil"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/types/known/structpb"
)

// newClient creates a reusable SpiceDB client.
// In production, use TLS credentials. This example uses insecure for local dev.
func newClient() *authzed.Client {
	endpoint := os.Getenv("SPICEDB_ENDPOINT")
	if endpoint == "" {
		endpoint = "localhost:50051"
	}
	token := os.Getenv("SPICEDB_TOKEN")
	if token == "" {
		token = "dev-token"
	}

	// Development: no TLS
	client, err := authzed.NewClient(
		endpoint,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpcutil.WithInsecureBearerToken(token),
	)
	if err != nil {
		log.Fatalf("failed to create SpiceDB client: %v", err)
	}
	return client
}

// writeRelationship writes a single relationship and returns the ZedToken.
// Use the ZedToken in subsequent checks for read-your-writes consistency.
func writeRelationship(ctx context.Context, client *authzed.Client,
	resourceType, resourceID, relation, subjectType, subjectID string,
) (*v1.ZedToken, error) {
	resp, err := client.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{
		Updates: []*v1.RelationshipUpdate{
			{
				Operation: v1.RelationshipUpdate_OPERATION_TOUCH, // idempotent upsert
				Relationship: &v1.Relationship{
					Resource: &v1.ObjectReference{
						ObjectType: resourceType,
						ObjectId:   resourceID,
					},
					Relation: relation,
					Subject: &v1.SubjectReference{
						Object: &v1.ObjectReference{
							ObjectType: subjectType,
							ObjectId:   subjectID,
						},
					},
				},
			},
		},
	})
	if err != nil {
		return nil, fmt.Errorf("writeRelationship: %w", err)
	}
	return resp.WrittenAt, nil
}

// checkPermission checks if a subject has a permission on a resource.
// zedToken is optional; if non-nil, uses AtLeastAsFresh for read-your-writes.
// Fail-safe: returns false on error (deny by default).
func checkPermission(ctx context.Context, client *authzed.Client,
	resourceType, resourceID, permission, subjectType, subjectID string,
	zedToken *v1.ZedToken,
) (bool, error) {
	consistency := &v1.Consistency{
		Requirement: &v1.Consistency_MinimizeLatency{MinimizeLatency: true},
	}
	if zedToken != nil {
		consistency = &v1.Consistency{
			Requirement: &v1.Consistency_AtLeastAsFresh{AtLeastAsFresh: zedToken},
		}
	}

	resp, err := client.CheckPermission(ctx, &v1.CheckPermissionRequest{
		Resource: &v1.ObjectReference{
			ObjectType: resourceType,
			ObjectId:   resourceID,
		},
		Permission: permission,
		Subject: &v1.SubjectReference{
			Object: &v1.ObjectReference{
				ObjectType: subjectType,
				ObjectId:   subjectID,
			},
		},
		Consistency: consistency,
	})
	if err != nil {
		// Fail-safe: deny on any error
		return false, fmt.Errorf("checkPermission: %w", err)
	}

	return resp.Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION, nil
}

// bulkCheck checks multiple (resource, permission) pairs for one subject.
// Always prefer this over looping checkPermission.
func bulkCheck(ctx context.Context, client *authzed.Client,
	subjectType, subjectID string,
	checks []struct{ ResourceType, ResourceID, Permission string },
) (map[string]bool, error) {
	items := make([]*v1.CheckBulkPermissionsRequestItem, len(checks))
	for i, c := range checks {
		items[i] = &v1.CheckBulkPermissionsRequestItem{
			Resource:   &v1.ObjectReference{ObjectType: c.ResourceType, ObjectId: c.ResourceID},
			Permission: c.Permission,
			Subject: &v1.SubjectReference{
				Object: &v1.ObjectReference{ObjectType: subjectType, ObjectId: subjectID},
			},
		}
	}

	resp, err := client.CheckBulkPermissions(ctx, &v1.CheckBulkPermissionsRequest{Items: items})
	if err != nil {
		return nil, fmt.Errorf("bulkCheck: %w", err)
	}

	results := make(map[string]bool, len(resp.Pairs))
	for i, pair := range resp.Pairs {
		key := checks[i].ResourceType + ":" + checks[i].ResourceID + "#" + checks[i].Permission
		results[key] = pair.GetItem().Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION
	}
	return results, nil
}

// lookupResources returns all resource IDs of a given type that the subject can access.
func lookupResources(ctx context.Context, client *authzed.Client,
	resourceType, permission, subjectType, subjectID string,
) ([]string, error) {
	stream, err := client.LookupResources(ctx, &v1.LookupResourcesRequest{
		ResourceObjectType: resourceType,
		Permission:         permission,
		Subject: &v1.SubjectReference{
			Object: &v1.ObjectReference{ObjectType: subjectType, ObjectId: subjectID},
		},
		Consistency: &v1.Consistency{
			Requirement: &v1.Consistency_MinimizeLatency{MinimizeLatency: true},
		},
	})
	if err != nil {
		return nil, fmt.Errorf("lookupResources: %w", err)
	}

	var ids []string
	for {
		resp, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("lookupResources stream: %w", err)
		}
		ids = append(ids, resp.ResourceObjectId)
	}
	return ids, nil
}

// checkWithCaveat demonstrates checking a caveated permission with runtime context.
func checkWithCaveat(ctx context.Context, client *authzed.Client) {
	ctx2, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	context, _ := structpb.NewStruct(map[string]interface{}{
		"now": time.Now().Unix(),
	})

	resp, err := client.CheckPermission(ctx2, &v1.CheckPermissionRequest{
		Resource:   &v1.ObjectReference{ObjectType: "document", ObjectId: "doc-123"},
		Permission: "view",
		Subject: &v1.SubjectReference{
			Object: &v1.ObjectReference{ObjectType: "user", ObjectId: "alice"},
		},
		Context: context,
	})
	if err != nil {
		log.Printf("caveat check error: %v", err)
		return
	}

	switch resp.Permissionship {
	case v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION:
		fmt.Println("alice has view permission")
	case v1.CheckPermissionResponse_PERMISSIONSHIP_NO_PERMISSION:
		fmt.Println("alice does not have view permission")
	case v1.CheckPermissionResponse_PERMISSIONSHIP_CONDITIONAL_PERMISSION:
		fmt.Println("alice's permission is conditional on caveat context not provided")
	}
}

func main() {
	ctx := context.Background()
	client := newClient()

	// 1. Write a relationship
	fmt.Println("Writing relationship...")
	token, err := writeRelationship(ctx, client, "document", "doc-1", "viewer", "user", "alice")
	if err != nil {
		log.Fatalf("write failed: %v", err)
	}
	fmt.Printf("Written at token: %s\n", token.Token[:min(20, len(token.Token))])

	// 2. Check permission (read-your-writes with the ZedToken)
	fmt.Println("\nChecking permission with ZedToken...")
	allowed, err := checkPermission(ctx, client, "document", "doc-1", "view", "user", "alice", token)
	if err != nil {
		log.Fatalf("check failed: %v", err)
	}
	fmt.Printf("alice can view doc-1: %v\n", allowed)

	// 3. Bulk check
	fmt.Println("\nBulk checking permissions...")
	results, err := bulkCheck(ctx, client, "user", "alice", []struct {
		ResourceType, ResourceID, Permission string
	}{
		{"document", "doc-1", "view"},
		{"document", "doc-2", "view"},
	})
	if err != nil {
		log.Fatalf("bulk check failed: %v", err)
	}
	for k, v := range results {
		parts := strings.SplitN(k, "#", 2)
		fmt.Printf("  %s can %s: %v\n", "alice", parts[1], v)
	}

	// 4. Lookup resources
	fmt.Println("\nLooking up accessible documents...")
	docs, err := lookupResources(ctx, client, "document", "view", "user", "alice")
	if err != nil {
		log.Fatalf("lookup failed: %v", err)
	}
	fmt.Printf("alice can access: %v\n", docs)
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
