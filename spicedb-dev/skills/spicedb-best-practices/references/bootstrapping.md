# Bootstrapping: Writing Existing Data to SpiceDB

When integrating SpiceDB into an existing application that already has users,
resources, and access data in a database, you need to write all existing
relationships to SpiceDB before going live.

---

## The Problem

SpiceDB starts empty. Your database has existing user/resource/permission data.
Until SpiceDB reflects that data, all CheckPermission calls will return NO_PERMISSION
for relationships that exist in your DB but not yet in SpiceDB.

---

## WriteRelationships vs. ImportBulkRelationships

| Method | Limit | Use for |
|---|---|---|
| `WriteRelationships` | 1,000 updates per call | Normal application writes |
| `ImportBulkRelationships` (Go, streaming) | Unlimited | Initial data load |

For datasets over a few thousand relationships, use `ImportBulkRelationships` in Go.
For TypeScript and Python (where bulk import is not yet exposed as streaming),
batch `WriteRelationships` calls at 1,000 items each.

---

## Relationship Write Ordering

Write in hierarchical order -- parents before children:

1. Top-level tenants and organizations
2. Teams and groups
3. Resource containers (workspaces, projects, folders)
4. Leaf resources (documents, files, records)
5. User memberships and direct assignments

SpiceDB does not enforce ordering, but writing parents first means permission
checks work correctly as soon as each batch completes.

---

## Go: ImportBulkRelationships (Recommended for Large Datasets)

```go
func importAllRelationships(ctx context.Context, client *authzed.Client, rels []*v1.Relationship) error {
    stream, err := client.ImportBulkRelationships(ctx)
    if err != nil {
        return fmt.Errorf("failed to open import stream: %w", err)
    }

    const batchSize = 1000
    for i := 0; i < len(rels); i += batchSize {
        end := i + batchSize
        if end > len(rels) {
            end = len(rels)
        }
        if err := stream.Send(&v1.ImportBulkRelationshipsRequest{
            Relationships: rels[i:end],
        }); err != nil {
            return fmt.Errorf("failed to send batch starting at %d: %w", i, err)
        }
    }

    resp, err := stream.CloseAndRecv()
    if err != nil {
        return fmt.Errorf("import failed: %w", err)
    }
    log.Printf("imported %d relationships", resp.NumLoaded)
    return nil
}
```

---

## TypeScript: Batched WriteRelationships

```typescript
async function bootstrapRelationships(
    client: ReturnType<typeof v1.NewClient>,
    relationships: v1.Relationship[],
    batchSize = 1000
): Promise<void> {
    for (let i = 0; i < relationships.length; i += batchSize) {
        const batch = relationships.slice(i, i + batchSize);
        await client.writeRelationships({
            updates: batch.map(rel => ({
                operation: v1.RelationshipUpdate_Operation.TOUCH,
                relationship: rel,
            })),
        });
        console.log(`Bootstrapped ${i + batch.length} of ${relationships.length} relationships`);
    }
}
```

---

## Python: Batched WriteRelationships

```python
def bootstrap_relationships(client, relationships: list, batch_size: int = 1000) -> None:
    """Write all relationships in batches. Use for initial data load."""
    for i in range(0, len(relationships), batch_size):
        batch = relationships[i:i + batch_size]
        client.WriteRelationships(WriteRelationshipsRequest(
            updates=[RelationshipUpdate(
                operation=RelationshipUpdate.OPERATION_TOUCH,
                relationship=rel,
            ) for rel in batch]
        ))
        print(f"Bootstrapped {i + len(batch)} of {len(relationships)} relationships")
```

---

## Verification Before Cutover

After importing, spot-check that relationships landed correctly. Use `FullyConsistent`
here -- this is a one-time verification step, not a production check:

```go
resp, err := client.CheckPermission(ctx, &v1.CheckPermissionRequest{
    Resource:   &v1.ObjectReference{ObjectType: "document", ObjectId: "known-doc-id"},
    Permission: "view",
    Subject:    &v1.SubjectReference{Object: &v1.ObjectReference{ObjectType: "user", ObjectId: "known-user-id"}},
    Consistency: &v1.Consistency{
        Requirement: &v1.Consistency_FullyConsistent{FullyConsistent: true},
    },
})
// Must return HAS_PERMISSION for a known-good user/resource pair.
```

Check a representative sample across all resource types before enabling SpiceDB
checks in production.

---

## Cutover Strategy

1. **Shadow mode**: Write new relationships to both DB and SpiceDB, behind a feature
   flag. Do not enforce SpiceDB checks yet.
2. **Bootstrap**: Run the import for all existing DB data.
3. **Verify**: Spot-check expected permission results against known-good pairs.
4. **Enable**: Flip the feature flag to enforce SpiceDB checks on incoming requests.
5. **Dual-write window**: Continue writing to both systems during rollout.
6. **Retire old system**: Once SpiceDB is authoritative, remove legacy permission
   checks and dual-write code.

---

## Key Rules

1. Import in hierarchy order (parents before children)
2. Use `ImportBulkRelationships` for Go datasets over 1,000 rows
3. Batch to 1,000 per `WriteRelationships` call for TypeScript/Python
4. Verify with `FullyConsistent` spot-checks before cutover (only appropriate for
   this one-time verification; use `MinimizeLatency` in production)
5. Use a shadow/dual-write window -- avoid a hard cutover
