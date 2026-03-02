---
name: implement-spicedb-relationships
description: Add SpiceDB relationship writes and deletes to application code (WriteRelationships, DeleteRelationships)
argument-hint: "[target-files]"
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - Task
---

# Implement SpiceDB Relationship Management

Add SpiceDB client calls for writing and deleting relationships to application code.

## Step 0: Verify or Generate Client

Before adding operation calls, verify a SpiceDB client exists in the codebase (search for
`authzed.NewClient`, `v1.NewClient`, or equivalent). If not, generate client initialization
code using the connection management pattern from
`skills/spicedb-best-practices/references/client-patterns.md`. The client should be a
singleton initialized at application startup, not per-request. For production TLS, see
the same reference.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each task `in_progress` when starting and `completed` when done.

## Supported Operations

- **WriteRelationships** -- create or update relationships using `OPERATION_TOUCH`
- **DeleteRelationships** -- remove relationships on revocation or resource deletion

## Process

### Step 1: Understand the Context

Ask the user using AskUserQuestion:
1. **Operation type**: What relationship operation to implement?
   - Grant access (WriteRelationships)
   - Revoke access (targeted DeleteRelationships)
   - Resource deletion cleanup (DeleteRelationships with object filter)

2. **Programming language**: What language?
   - Go
   - TypeScript/JavaScript
   - Python

3. **Target location**: Where to add the code?
   - Specific file/function
   - Let checkpoint-identifier agent analyze and suggest
   - New file/module

### Step 2: Locate Target Code

If user specified files, read them. Otherwise:
- Use Task tool to launch `checkpoint-identifier` agent
- Agent will analyze codebase and suggest where to add relationship management
- Focus on resource creation/deletion handlers and access grant/revoke endpoints

### Step 3: Load Best Practices

Load the `spicedb-best-practices` skill to understand:
- Client connection patterns
- Error handling and retry strategy
- Idempotency with `OPERATION_TOUCH`
- Write ordering with respect to the primary database

### Step 4: Generate Relationship Code

#### Write Ordering (Critical)

**Write to your database first, commit, then write to SpiceDB.**

```
1. Begin DB transaction
2. Write resource/membership record to your database
3. Commit DB transaction
4. Write relationship to SpiceDB with OPERATION_TOUCH
```

Do NOT use a distributed transaction (2PC) spanning your DB and SpiceDB.
`TOUCH` is idempotent -- if the SpiceDB write fails after your DB commit, it is safe to
retry without risk of duplicate or inconsistent state. If the write repeatedly fails, the
system is in a temporarily inconsistent state (DB committed, SpiceDB not updated); implement
a reconciliation job or retry queue for production systems.

#### Subject ID Selection

When setting `ObjectId` for the subject:
```go
// Use a stable, immutable identifier as the SpiceDB object ID.
// Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.
// See: skills/spicedb-best-practices references/client-patterns.md
```

#### Relationship Lifecycle

- **When to write:** After your database transaction commits. Call `WriteRelationships` with
  `OPERATION_TOUCH` immediately after committing the DB record.
- **When to delete on revocation:** Use a targeted `DeleteRelationships` call that specifies
  the exact resource, relation, and subject.
- **When to delete on resource deletion:** SpiceDB does not cascade deletes. When deleting a
  resource from your database, first call `DeleteRelationships` with an object filter to clean
  up all relationships where that resource is the object.

### WriteRelationships

Always use `OPERATION_TOUCH` -- it is idempotent and safe to retry on failure.

**Go:**
```go
func (s *Service) GrantAccess(ctx context.Context, userID, documentID, role string) error {
    // Use a stable, immutable identifier as the SpiceDB object ID.
    // Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.

    // Write to DB first, then SpiceDB (see Write Ordering above).
    resp, err := s.spicedb.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{
        Updates: []*v1.RelationshipUpdate{{
            // OPERATION_TOUCH is idempotent -- safe to retry on failure.
            Operation: v1.RelationshipUpdate_OPERATION_TOUCH,
            Relationship: &v1.Relationship{
                Resource: &v1.ObjectReference{
                    ObjectType: "document",
                    ObjectId:   documentID,
                },
                Relation: role,
                Subject: &v1.SubjectReference{
                    Object: &v1.ObjectReference{
                        ObjectType: "user",
                        ObjectId:   userID,
                    },
                },
            },
        }},
    })
    if err != nil {
        return fmt.Errorf("failed to grant access: %w", err)
    }

    // Capture the ZedToken for read-your-writes consistency.
    // Return resp.WrittenAt to the caller and include it in the HTTP response
    // header (e.g., X-SpiceDB-Token) so the next request can use AtLeastAsFresh.
    _ = resp.WrittenAt

    return nil
}
```

**TypeScript:**
```typescript
async function grantAccess(userId: string, documentId: string, role: string): Promise<v1.ZedToken | undefined> {
    // Use a stable, immutable identifier as the SpiceDB object ID.
    // Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.

    // Write to DB first, then SpiceDB (see Write Ordering above).
    const response = await client.writeRelationships({
        updates: [{
            // OPERATION_TOUCH is idempotent -- safe to retry on failure.
            operation: v1.RelationshipUpdate_Operation.TOUCH,
            relationship: {
                resource: { objectType: 'document', objectId: documentId },
                relation: role,
                subject: { object: { objectType: 'user', objectId: userId } },
            },
        }],
    });

    // Return the ZedToken for read-your-writes consistency.
    // Include it in the HTTP response header (e.g., X-SpiceDB-Token).
    return response.writtenAt;
}
```

**Python:**
```python
from authzed.api.v1 import (
    Client,
    WriteRelationshipsRequest,
    RelationshipUpdate,
    Relationship,
    ObjectReference,
    SubjectReference,
)

def grant_access(user_id: str, document_id: str, role: str):
    # Use a stable, immutable identifier as the SpiceDB object ID.
    # Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.

    # Write to DB first, then SpiceDB (see Write Ordering above).
    response = client.WriteRelationships(WriteRelationshipsRequest(
        updates=[RelationshipUpdate(
            # TOUCH is idempotent -- safe to retry on failure.
            operation=RelationshipUpdate.OPERATION_TOUCH,
            relationship=Relationship(
                resource=ObjectReference(object_type="document", object_id=document_id),
                relation=role,
                subject=SubjectReference(
                    object=ObjectReference(object_type="user", object_id=user_id)
                ),
            ),
        )],
    ))
    # Return the ZedToken for read-your-writes consistency.
    return response.written_at
```

### DeleteRelationships -- Targeted (Revocation)

Use when revoking a specific user's access to a specific resource.

**Go:**
```go
func (s *Service) RevokeAccess(ctx context.Context, userID, documentID, role string) error {
    _, err := s.spicedb.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{
        Updates: []*v1.RelationshipUpdate{{
            Operation: v1.RelationshipUpdate_OPERATION_DELETE,
            Relationship: &v1.Relationship{
                Resource: &v1.ObjectReference{ObjectType: "document", ObjectId: documentID},
                Relation: role,
                Subject: &v1.SubjectReference{
                    Object: &v1.ObjectReference{ObjectType: "user", ObjectId: userID},
                },
            },
        }},
    })
    return err
}
```

### DeleteRelationships -- Object Filter (Resource Deletion)

Use when deleting a resource. SpiceDB does not cascade -- call this before or immediately
after deleting the resource from your database to avoid orphaned relationships.

**Go:**
```go
func (s *Service) DeleteDocumentRelationships(ctx context.Context, documentID string) error {
    // SpiceDB does not cascade deletes. When deleting a resource, call DeleteRelationships
    // with an object filter to clean up all relationships for that object.
    _, err := s.spicedb.DeleteRelationships(ctx, &v1.DeleteRelationshipsRequest{
        RelationshipFilter: &v1.RelationshipFilter{
            ResourceType:       "document",
            OptionalResourceId: documentID,
        },
    })
    if err != nil {
        return fmt.Errorf("failed to clean up document relationships: %w", err)
    }
    return nil
}
```

**TypeScript:**
```typescript
async function deleteDocumentRelationships(documentId: string): Promise<void> {
    // SpiceDB does not cascade deletes. Clean up all relationships for this object.
    await client.deleteRelationships({
        relationshipFilter: {
            resourceType: 'document',
            optionalResourceId: documentId,
        },
    });
}
```

**Python:**
```python
from authzed.api.v1 import DeleteRelationshipsRequest, RelationshipFilter

def delete_document_relationships(document_id: str):
    # SpiceDB does not cascade deletes. Clean up all relationships for this object.
    client.DeleteRelationships(DeleteRelationshipsRequest(
        relationship_filter=RelationshipFilter(
            resource_type="document",
            optional_resource_id=document_id,
        )
    ))
```

### Step 5: Cross-Reference Against Schema

Before inserting the generated code, verify object types and **relation names** against
the schema file:

1. Look for `schema.zed` or any `*.zed` file in the working directory
2. If found, read it and extract all definition names and their declared **relation** names
   (not permission names -- WriteRelationships uses the `Relation` field)
3. Check every `ObjectType` and `Relation` value in the generated code against the schema
   -- each must exactly match a declared definition name and relation name
4. If any mismatch is found (e.g., code writes `relation: "own"` but schema has
   `relation owner`), flag it -- this is a silent failure: SpiceDB accepts the write but
   the relationship never fires in permission evaluation
5. If no schema file is found, note this and remind the user to verify relation names
   manually before deploying

### Step 6: Add Lifecycle and Cleanup Patterns

Review the user's resource lifecycle and ensure:
- Relationships are written after DB commit (not in the same transaction)
- Every resource creation handler writes the initial relationships (e.g., owner)
- Every resource deletion handler calls `DeleteRelationships` with an object filter
- Every access revocation handler calls a targeted relationship delete
- `WriteRelationships` always uses `OPERATION_TOUCH`

### Step 7: Insert and Verify

Use Edit tool to insert the generated code:
- Add imports at file top
- Add client initialization if needed
- Add operation implementation
- Preserve existing code structure
- Add comments explaining the write ordering and lifecycle rules

Suggest the user:
1. Test the implementation
2. Run `/spicedb-dev:test-permissions` to generate test cases
3. Review the write ordering for each resource lifecycle event
4. Consider adding a reconciliation job for production SpiceDB write failures

## See Also

- For permission checks and lookups: `/spicedb-dev:implement-spicedb-checks`
- For finding resource creation and deletion handlers: `checkpoint-identifier` agent
