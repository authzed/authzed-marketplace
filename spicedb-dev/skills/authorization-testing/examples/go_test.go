// Package authz_test demonstrates SpiceDB integration testing patterns.
//
// Run with: go test ./...
// Requires SPICEDB_ENDPOINT and SPICEDB_TOKEN environment variables,
// or a local SpiceDB running at localhost:50051 with token "test-token".
//
// For isolated parallel tests, start SpiceDB with serve-testing:
//   spicedb serve-testing
// Each unique preshared key gets its own isolated empty datastore.
package authz_test

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"

	authzed "github.com/authzed/authzed-go/v1"
	v1 "github.com/authzed/authzed-go/v1/proto/authzed/api/v1"
	"github.com/authzed/grpcutil"
	"google.golang.org/grpc"
)

// relationship is a convenience type for writing test fixtures.
type relationship struct {
	Resource string // "type:id"
	Relation string
	Subject  string // "type:id" or "type:id#relation"
}

// TestDocumentAccess tests basic RBAC document permissions.
func TestDocumentAccess(t *testing.T) {
	client := setupTestClient(t)
	loadSchema(t, client, `
		definition user {}

		definition document {
			relation owner: user
			relation editor: user
			relation viewer: user

			permission view = viewer + editor + owner
			permission edit = editor + owner
			permission delete = owner
		}
	`)

	writeRelationships(t, client, []relationship{
		{"document:doc-1", "owner", "user:alice"},
		{"document:doc-1", "editor", "user:bob"},
		{"document:doc-1", "viewer", "user:charlie"},
	})

	tests := []struct {
		subject    string
		permission string
		resource   string
		expected   bool
	}{
		// Positive tests
		{"user:alice", "view", "document:doc-1", true},
		{"user:alice", "edit", "document:doc-1", true},
		{"user:alice", "delete", "document:doc-1", true},
		{"user:bob", "view", "document:doc-1", true},
		{"user:bob", "edit", "document:doc-1", true},
		{"user:charlie", "view", "document:doc-1", true},

		// Negative tests
		{"user:bob", "delete", "document:doc-1", false},
		{"user:charlie", "edit", "document:doc-1", false},
		{"user:charlie", "delete", "document:doc-1", false},
		{"user:dave", "view", "document:doc-1", false},
	}

	for _, tt := range tests {
		t.Run(fmt.Sprintf("%s-%s-%s", tt.subject, tt.permission, tt.resource), func(t *testing.T) {
			result := checkPermission(t, client, tt.subject, tt.permission, tt.resource)
			if result != tt.expected {
				t.Errorf("expected %v, got %v for %s can %s %s",
					tt.expected, result, tt.subject, tt.permission, tt.resource)
			}
		})
	}
}

// TestPermissionRevocation verifies that deleting a relationship removes access.
func TestPermissionRevocation(t *testing.T) {
	client := setupTestClient(t)
	loadSchema(t, client, `
		definition user {}
		definition document {
			relation editor: user
			permission edit = editor
		}
	`)

	writeRelationships(t, client, []relationship{
		{"document:doc-1", "editor", "user:bob"},
	})

	if !checkPermission(t, client, "user:bob", "edit", "document:doc-1") {
		t.Fatal("expected bob to have edit before revocation")
	}

	// Delete the relationship
	_, err := client.WriteRelationships(context.Background(), &v1.WriteRelationshipsRequest{
		Updates: []*v1.RelationshipUpdate{
			{
				Operation:    v1.RelationshipUpdate_OPERATION_DELETE,
				Relationship: parseRelationship(relationship{"document:doc-1", "editor", "user:bob"}),
			},
		},
	})
	if err != nil {
		t.Fatalf("failed to revoke relationship: %v", err)
	}

	if checkPermission(t, client, "user:bob", "edit", "document:doc-1") {
		t.Fatal("expected bob to lose edit after revocation")
	}
}

// --- Test Helpers ---

func setupTestClient(t *testing.T) *authzed.Client {
	t.Helper()

	// Use unique preshared key per test for serve-testing isolation
	token := os.Getenv("SPICEDB_TOKEN")
	if token == "" {
		token = "test-" + t.Name()
	}
	endpoint := os.Getenv("SPICEDB_ENDPOINT")
	if endpoint == "" {
		endpoint = "localhost:50051"
	}

	client, err := authzed.NewClient(
		endpoint,
		grpc.WithInsecure(), //nolint:staticcheck
		grpcutil.WithBearerToken(token),
	)
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}
	return client
}

func loadSchema(t *testing.T, client *authzed.Client, schema string) {
	t.Helper()
	_, err := client.WriteSchema(context.Background(), &v1.WriteSchemaRequest{
		Schema: schema,
	})
	if err != nil {
		t.Fatalf("failed to load schema: %v", err)
	}
}

func writeRelationships(t *testing.T, client *authzed.Client, rels []relationship) {
	t.Helper()
	updates := make([]*v1.RelationshipUpdate, len(rels))
	for i, rel := range rels {
		updates[i] = &v1.RelationshipUpdate{
			Operation:    v1.RelationshipUpdate_OPERATION_TOUCH,
			Relationship: parseRelationship(rel),
		}
	}

	_, err := client.WriteRelationships(context.Background(), &v1.WriteRelationshipsRequest{
		Updates: updates,
	})
	if err != nil {
		t.Fatalf("failed to write relationships: %v", err)
	}
}

func checkPermission(t *testing.T, client *authzed.Client, subject, permission, resource string) bool {
	t.Helper()
	subjectParts := strings.SplitN(subject, ":", 2)
	resourceParts := strings.SplitN(resource, ":", 2)

	resp, err := client.CheckPermission(context.Background(), &v1.CheckPermissionRequest{
		Resource: &v1.ObjectReference{
			ObjectType: resourceParts[0],
			ObjectId:   resourceParts[1],
		},
		Permission: permission,
		Subject: &v1.SubjectReference{
			Object: &v1.ObjectReference{
				ObjectType: subjectParts[0],
				ObjectId:   subjectParts[1],
			},
		},
		// Always use FullyConsistent in tests to avoid flakiness from replica lag
		Consistency: &v1.Consistency{
			Requirement: &v1.Consistency_FullyConsistent{
				FullyConsistent: true,
			},
		},
	})

	if err != nil {
		t.Fatalf("checkPermission(%s, %s, %s) failed: %v", subject, permission, resource, err)
	}

	return resp.Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION
}

func parseRelationship(rel relationship) *v1.Relationship {
	resourceParts := strings.SplitN(rel.Resource, ":", 2)
	subjectParts := strings.SplitN(rel.Subject, ":", 2)

	subjectID := subjectParts[1]
	subjectRelation := ""
	if idx := strings.Index(subjectID, "#"); idx != -1 {
		subjectRelation = subjectID[idx+1:]
		subjectID = subjectID[:idx]
	}

	sr := &v1.SubjectReference{
		Object: &v1.ObjectReference{
			ObjectType: subjectParts[0],
			ObjectId:   subjectID,
		},
	}
	if subjectRelation != "" {
		sr.OptionalRelation = subjectRelation
	}

	return &v1.Relationship{
		Resource: &v1.ObjectReference{
			ObjectType: resourceParts[0],
			ObjectId:   resourceParts[1],
		},
		Relation: rel.Relation,
		Subject:  sr,
	}
}
