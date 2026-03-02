# Fixture Generators

Reference for generating test fixtures and reusable test helpers for SpiceDB authorization testing.

## Fixture Structure

Organize fixtures by scenario. Each fixture file defines relationships and expected permissions:

```python
# fixtures/basic_document_access.py
RELATIONSHIPS = [
    # Alice owns doc-1
    ("document:doc-1", "owner", "user:alice"),

    # Bob is editor on doc-1
    ("document:doc-1", "editor", "user:bob"),

    # Charlie is viewer on doc-1
    ("document:doc-1", "viewer", "user:charlie"),

    # Dave has no access
]

EXPECTED_PERMISSIONS = [
    # (subject, permission, resource, expected_result)
    ("user:alice", "view", "document:doc-1", True),
    ("user:alice", "edit", "document:doc-1", True),
    ("user:alice", "delete", "document:doc-1", True),

    ("user:bob", "view", "document:doc-1", True),
    ("user:bob", "edit", "document:doc-1", True),
    ("user:bob", "delete", "document:doc-1", False),  # Negative test

    ("user:charlie", "view", "document:doc-1", True),
    ("user:charlie", "edit", "document:doc-1", False),  # Negative test
    ("user:charlie", "delete", "document:doc-1", False),  # Negative test

    ("user:dave", "view", "document:doc-1", False),  # Negative test
]
```

---

## Python Fixture Generator

For programmatic fixture generation across complex hierarchies:

```python
def generate_org_hierarchy():
    """Generate organization with teams and projects."""
    relationships = []

    # Organization
    relationships.append(("organization:acme", "admin", "user:alice"))
    relationships.append(("organization:acme", "member", "user:bob"))

    # Teams
    relationships.append(("team:engineering", "org", "organization:acme"))
    relationships.append(("team:engineering", "member", "user:bob"))
    relationships.append(("team:engineering", "member", "user:charlie"))

    # Projects (generate N projects for scale testing)
    for i in range(10):
        project_id = f"project:proj-{i}"
        relationships.append((project_id, "parent", "team:engineering"))
        relationships.append((project_id, "owner", f"user:user-{i}"))

    return relationships


def write_fixtures(client, relationships: list[tuple]) -> None:
    """Write a list of (resource, relation, subject) tuples to SpiceDB."""
    import authzed.api.v1 as v1

    updates = []
    for resource_str, relation, subject_str in relationships:
        res_type, res_id = resource_str.split(":", 1)
        sub_type, sub_id = subject_str.split(":", 1)
        updates.append(v1.RelationshipUpdate(
            operation=v1.RelationshipUpdate.OPERATION_TOUCH,
            relationship=v1.Relationship(
                resource=v1.ObjectReference(object_type=res_type, object_id=res_id),
                relation=relation,
                subject=v1.SubjectReference(
                    object=v1.ObjectReference(object_type=sub_type, object_id=sub_id)
                ),
            ),
        ))

    client.WriteRelationships(v1.WriteRelationshipsRequest(updates=updates))
```

---

## Go Test Helpers

### writeRelationships

```go
type relationship struct {
    Resource   string
    Relation   string
    Subject    string
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
```

### checkPermission

```go
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
        Consistency: &v1.Consistency{
            Requirement: &v1.Consistency_FullyConsistent{
                FullyConsistent: true,
            },
        },
    })

    if err != nil {
        t.Fatalf("failed to check permission: %v", err)
    }

    return resp.Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION
}
```

### parseRelationship (helper)

```go
func parseRelationship(rel relationship) *v1.Relationship {
    resourceParts := strings.SplitN(rel.Resource, ":", 2)
    subjectParts := strings.SplitN(rel.Subject, ":", 2)

    // Handle subject#relation format (e.g., "group:eng#member")
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
```

### setupTestClient

```go
func setupTestClient(t *testing.T) *authzed.Client {
    t.Helper()
    // Use a unique preshared key per test suite for serve-testing isolation
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
        grpc.WithInsecure(),
        grpcutil.WithBearerToken(token),
    )
    if err != nil {
        t.Fatalf("failed to create client: %v", err)
    }
    return client
}
```

---

## TypeScript Test Helpers

```typescript
import { v1 } from "@authzed/authzed-node";

export function setupTestClient(suiteName: string): ReturnType<typeof v1.NewClient> {
  // Use a unique suiteName per test suite for serve-testing isolation.
  // Each unique token maps to an isolated empty datastore on serve-testing.
  const token = process.env.SPICEDB_TOKEN ?? `test-${suiteName}`;
  const endpoint = process.env.SPICEDB_ENDPOINT ?? "localhost:50051";
  return v1.NewClient(
    token,
    endpoint,
    v1.ClientSecurity.INSECURE_PLAINTEXT_CREDENTIALS
  );
}

interface Relationship {
  resource: string;  // "type:id"
  relation: string;
  subject: string;   // "type:id" or "type:id#relation"
}

function parseRef(s: string): v1.ObjectReference {
  const [objectType, objectId] = s.split(":", 2);
  return v1.ObjectReference.create({ objectType, objectId });
}

export async function writeRelationships(
  client: ReturnType<typeof v1.NewClient>,
  rels: Relationship[]
): Promise<void> {
  const updates = rels.map((rel) => {
    const [subjectStr, optionalRelation] = rel.subject.split("#", 2);
    return v1.RelationshipUpdate.create({
      operation: v1.RelationshipUpdate_Operation.TOUCH,
      relationship: v1.Relationship.create({
        resource: parseRef(rel.resource),
        relation: rel.relation,
        subject: v1.SubjectReference.create({
          object: parseRef(subjectStr),
          optionalRelation: optionalRelation ?? "",
        }),
      }),
    });
  });

  await client.writeRelationships(
    v1.WriteRelationshipsRequest.create({ updates })
  );
}

export async function checkPermission(
  client: ReturnType<typeof v1.NewClient>,
  subject: string,
  permission: string,
  resource: string
): Promise<boolean> {
  const resp = await client.checkPermission(
    v1.CheckPermissionRequest.create({
      resource: parseRef(resource),
      permission,
      subject: v1.SubjectReference.create({ object: parseRef(subject) }),
      consistency: v1.Consistency.create({
        requirement: { oneofKind: "fullyConsistent", fullyConsistent: true },
      }),
    })
  );
  return (
    resp.permissionship ===
    v1.CheckPermissionResponse_Permissionship.HAS_PERMISSION
  );
}
```

---

## Notes

- Always use `OPERATION_TOUCH` (upsert) when writing test relationships; avoids
  failures if the same relationship is written twice in parallel test setup.
- Use `FullyConsistent` in tests to avoid flaky results from replica lag.
- Clean up relationships in `t.Cleanup()` (Go) or `finally` (Python/TypeScript) to
  keep tests independent -- or use separate preshared keys per suite with `serve-testing`.
