---
name: implement-spicedb-checks
description: Add SpiceDB permission checks and lookup operations to application code (CheckPermission, BulkCheckPermission, LookupResources, LookupSubjects)
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

# Implement SpiceDB Permission Checks

Add SpiceDB client calls for permission checks and lookup operations to application code.

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

- **CheckPermission** -- single permission check at an authorization boundary
- **BulkCheckPermission** -- check multiple permissions in one call (for list filtering)
- **LookupResources** -- find all resources a subject can access with a given permission
- **LookupSubjects** -- find all subjects that have a given permission on a resource

## Process

### Step 1: Understand the Context

Ask the user using AskUserQuestion:
1. **Operation type**: Which check/lookup operation to implement?
   - Single permission check (CheckPermission)
   - Bulk permission check (BulkCheckPermission)
   - List accessible resources (LookupResources)
   - Find subjects with access (LookupSubjects)

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
- Agent will analyze codebase and suggest where to add authorization
- Agent uses data flow analysis to find authorization boundaries

### Step 3: Load Best Practices

Load the `spicedb-best-practices` skill to understand:
- Client connection patterns
- Error handling
- Consistency models
- Performance optimizations

### Step 4: Generate Check/Lookup Code

Based on operation type and language, generate appropriate code.

#### Consistency Model

Classify the check being generated, per call site, before picking a consistency:

- **Independent** -- no write earlier in this request, or the request immediately before it,
  feeds this check's answer. Use `MinimizeLatency`.
- **Dependent (read-after-write)** -- a write earlier in this request (or the request just
  before it) produced the relationship this check depends on -- e.g. create a resource, grant
  a relationship on it, then check a permission derived from that grant.
  1. Thread the ZedToken that write returned -- `AtLeastAsFresh`.
  2. If no ZedToken is reachable at this call site at all, use `FullyConsistent` and leave a
     `TODO` noting that a threaded ZedToken should replace it once one becomes reachable.
  3. **Never fall back to `MinimizeLatency` here.** Verified live against `spicedb
     serve-testing`: a `MinimizeLatency` check fired immediately after the write it depends on
     returns the stale, pre-write answer in the large majority of trials at a sub-millisecond
     write-to-check gap -- comfortably inside the gap between two ordinary HTTP requests. See
     `skills/spicedb-best-practices/references/consistency-deep-dive.md` for the full
     ZedToken-routing pattern.

**WARNING:** `FullyConsistent` bypasses the cache and is materially more expensive per call --
it is the correct, safe choice for an un-threadable dependent check (above), not a default for
request paths generally. Reserve it otherwise for admin/audit operations.

**Go consistency pattern:**
```go
// dependsOnPriorWrite: this check's answer depends on a write this request (or
// the immediately preceding one) made. See "Consistency Model" above.
consistency := &v1.Consistency{
    Requirement: &v1.Consistency_MinimizeLatency{MinimizeLatency: true},
}
if zedToken != nil {
    consistency = &v1.Consistency{
        Requirement: &v1.Consistency_AtLeastAsFresh{AtLeastAsFresh: zedToken},
    }
} else if dependsOnPriorWrite {
    // TODO: thread a ZedToken from the write this check depends on once one is
    // reachable here. FullyConsistent is the correct, if costlier, stand-in --
    // never MinimizeLatency: verified live to return the stale, pre-write
    // answer on a check fired immediately after its dependent write.
    consistency = &v1.Consistency{
        Requirement: &v1.Consistency_FullyConsistent{FullyConsistent: true},
    }
}
```

**TypeScript consistency pattern:**
```typescript
// dependsOnPriorWrite: this check's answer depends on a write this request (or
// the immediately preceding one) made. See "Consistency Model" above.
const consistency = zedToken
    ? { requirement: { oneofKind: 'atLeastAsFresh' as const, atLeastAsFresh: zedToken } }
    : dependsOnPriorWrite
        // TODO: thread a ZedToken from the write this check depends on once one
        // is reachable here. fullyConsistent is the correct, if costlier,
        // stand-in -- never minimizeLatency on this path.
        ? { requirement: { oneofKind: 'fullyConsistent' as const, fullyConsistent: true } }
        : { requirement: { oneofKind: 'minimizeLatency' as const, minimizeLatency: true } };
```

**Python consistency pattern:**
```python
# depends_on_prior_write: this check's answer depends on a write this request
# (or the immediately preceding one) made. See "Consistency Model" above.
if zed_token:
    consistency = Consistency(at_least_as_fresh=zed_token)
elif depends_on_prior_write:
    # TODO: thread a ZedToken from the write this check depends on once one is
    # reachable here. fully_consistent is the correct, if costlier, stand-in --
    # never minimize_latency on this path.
    consistency = Consistency(fully_consistent=True)
else:
    consistency = Consistency(minimize_latency=True)
```

#### Subject ID Selection

When setting `ObjectId` for the subject:
```go
// Use a stable, immutable identifier as the SpiceDB object ID.
// Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.
// See: skills/spicedb-best-practices references/client-patterns.md
```

#### Multi-Subject Types

Production applications often have both human users and machine clients (service accounts,
API keys, background jobs). If your schema declares multiple subject types on a relation,
you need to pass the correct `ObjectType` for each caller.

**Schema requirement** (example):
```
definition document {
    relation viewer: user | service_account
    permission view = viewer
}
```

**Go -- determining subject type from auth context:**
```go
// Determine subject type from auth context (JWT claims, API key prefix, etc.)
subjectType := "user"
if isServiceAccount(ctx) {
    subjectType = "service_account"
}

resp, err := s.spicedb.CheckPermission(ctx, &v1.CheckPermissionRequest{
    Resource:   &v1.ObjectReference{ObjectType: "document", ObjectId: documentID},
    Permission: "view",
    Subject: &v1.SubjectReference{
        Object: &v1.ObjectReference{
            ObjectType: subjectType, // "user" or "service_account"
            ObjectId:   subjectID,   // stable ID from JWT sub or API key ID
        },
    },
    Consistency: consistency,
})
```

If `service_account` is not declared in the schema's relation, CheckPermission will
return NO_PERMISSION regardless of relationships -- verify the schema first.

#### ZedToken Threading

To support read-your-writes:
1. After a `WriteRelationships` call, capture `resp.WrittenAt` as a ZedToken.
2. Return the serialized token in an HTTP response header (e.g., `X-SpiceDB-Token`).
3. On the next request, read the header and pass it as `AtLeastAsFresh` in `CheckPermission`.
4. Store ZedTokens in your database as `text`/`varchar(1024)` if you need cross-request consistency.
5. **If a call site depends on a preceding write and no ZedToken can be threaded to it,
   pass `FullyConsistent`, never a bare "no token" fallback to `MinimizeLatency`** -- see
   "Consistency Model," above, for why, and for the `dependsOnPriorWrite`/`FullyConsistent`
   branch the examples below add for exactly this case.

### CheckPermission

**Go:**
```go
import (
    v1 "github.com/authzed/authzed-go/proto/authzed/api/v1"
    "google.golang.org/grpc/codes"
    "google.golang.org/grpc/status"
)

// dependsOnPriorWrite: true when this call's answer depends on a write this
// request (or the immediately preceding one) made -- e.g. this call is
// confirming a grant the caller just created. See "Consistency Model," above.
func (s *Service) GetDocument(ctx context.Context, userID, documentID string, zedToken *v1.ZedToken, dependsOnPriorWrite bool) (*Document, error) {
    // Use a stable, immutable identifier as the SpiceDB object ID.
    // Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.
    // See: skills/spicedb-best-practices references/client-patterns.md

    consistency := &v1.Consistency{
        Requirement: &v1.Consistency_MinimizeLatency{MinimizeLatency: true},
    }
    if zedToken != nil {
        consistency = &v1.Consistency{
            Requirement: &v1.Consistency_AtLeastAsFresh{AtLeastAsFresh: zedToken},
        }
    } else if dependsOnPriorWrite {
        // TODO: thread a ZedToken from the write this check depends on once one
        // is reachable here. Never MinimizeLatency on this path -- verified live
        // to return the stale, pre-write answer immediately after the write.
        consistency = &v1.Consistency{
            Requirement: &v1.Consistency_FullyConsistent{FullyConsistent: true},
        }
    }

    resp, err := s.spicedb.CheckPermission(ctx, &v1.CheckPermissionRequest{
        Resource: &v1.ObjectReference{
            ObjectType: "document",
            ObjectId:   documentID,
        },
        Permission: "view",
        Subject: &v1.SubjectReference{
            Object: &v1.ObjectReference{
                ObjectType: "user",
                ObjectId:   userID,
            },
        },
        Consistency: consistency,
    })

    if err != nil {
        // Fail-safe: deny access on error
        return nil, fmt.Errorf("permission check failed: %w", err)
    }

    if resp.Permissionship != v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION {
        return nil, status.Error(codes.PermissionDenied, "access denied")
    }

    return s.repo.GetDocument(ctx, documentID)
}
```

**TypeScript:**
```typescript
import { v1 } from '@authzed/authzed-node';

// dependsOnPriorWrite: true when this call's answer depends on a write this
// request (or the immediately preceding one) made. See "Consistency Model," above.
async function getDocument(userId: string, documentId: string, zedToken?: v1.ZedToken, dependsOnPriorWrite = false): Promise<Document> {
    // Use a stable, immutable identifier as the SpiceDB object ID.
    // Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.

    const consistency = zedToken
        ? { requirement: { oneofKind: 'atLeastAsFresh' as const, atLeastAsFresh: zedToken } }
        : dependsOnPriorWrite
            // TODO: thread a ZedToken from the write this check depends on once
            // one is reachable here. Never minimizeLatency on this path.
            ? { requirement: { oneofKind: 'fullyConsistent' as const, fullyConsistent: true } }
            : { requirement: { oneofKind: 'minimizeLatency' as const, minimizeLatency: true } };

    const response = await client.checkPermission({
        resource: { objectType: 'document', objectId: documentId },
        permission: 'view',
        subject: { object: { objectType: 'user', objectId: userId } },
        consistency,
    });

    if (response.permissionship !== v1.CheckPermissionResponse_Permissionship.HAS_PERMISSION) {
        throw new Error('Access denied');
    }

    return repo.getDocument(documentId);
}
```

**Python:**
```python
from authzed.api.v1 import (
    Client,
    CheckPermissionRequest,
    ObjectReference,
    SubjectReference,
    Consistency,
    CheckPermissionResponse,
)

def get_document(user_id: str, document_id: str, zed_token=None, depends_on_prior_write: bool = False) -> Document:
    # Use a stable, immutable identifier as the SpiceDB object ID.
    # Prefer the OIDC 'sub' field over email -- emails contain '@' and can change.
    #
    # depends_on_prior_write: True when this call's answer depends on a write
    # this request (or the immediately preceding one) made. See "Consistency
    # Model," above.
    if zed_token:
        consistency = Consistency(at_least_as_fresh=zed_token)
    elif depends_on_prior_write:
        # TODO: thread a ZedToken from the write this check depends on once one
        # is reachable here. Never minimize_latency on this path.
        consistency = Consistency(fully_consistent=True)
    else:
        consistency = Consistency(minimize_latency=True)

    response = client.CheckPermission(CheckPermissionRequest(
        resource=ObjectReference(object_type="document", object_id=document_id),
        permission="view",
        subject=SubjectReference(
            object=ObjectReference(object_type="user", object_id=user_id)
        ),
        consistency=consistency,
    ))

    if response.permissionship != CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION:
        raise PermissionError("Access denied")

    return repo.get_document(document_id)
```

### Caveats and CONDITIONAL_PERMISSION

If your schema uses caveats, pass `caveat_context` in the request and handle
`CONDITIONAL_PERMISSION` as a denial (caveat could not be resolved with provided context).
Both `NO_PERMISSION` and `CONDITIONAL_PERMISSION` should deny access unless caveat context
was explicitly supplied.

For full Go/TypeScript/Python examples of caveat context passing and CONDITIONAL_PERMISSION
handling, see `skills/spicedb-best-practices/references/client-patterns.md`.

### BulkCheckPermission

**Go:**
```go
func (s *Service) FilterDocuments(ctx context.Context, userID string, documentIDs []string) ([]string, error) {
    items := make([]*v1.CheckBulkPermissionsRequestItem, len(documentIDs))
    for i, docID := range documentIDs {
        items[i] = &v1.CheckBulkPermissionsRequestItem{
            Resource: &v1.ObjectReference{ObjectType: "document", ObjectId: docID},
            Permission: "view",
            Subject: &v1.SubjectReference{
                Object: &v1.ObjectReference{ObjectType: "user", ObjectId: userID},
            },
        }
    }

    resp, err := s.spicedb.CheckBulkPermissions(ctx, &v1.CheckBulkPermissionsRequest{
        Items: items,
    })
    if err != nil {
        return nil, fmt.Errorf("bulk check failed: %w", err)
    }

    var permitted []string
    for _, pair := range resp.Pairs {
        if pair.Item.Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION {
            permitted = append(permitted, pair.Request.Resource.ObjectId)
        }
    }

    return permitted, nil
}
```

**TypeScript:**
```typescript
async function filterDocuments(userId: string, documentIds: string[]): Promise<string[]> {
    const response = await client.bulkCheckPermission({
        items: documentIds.map(id => ({
            resource: { objectType: 'document', objectId: id },
            permission: 'view',
            subject: { object: { objectType: 'user', objectId: userId } },
        })),
    });
    return response.pairs
        .filter(p => p.item?.permissionship === v1.CheckPermissionResponse_Permissionship.HAS_PERMISSION)
        .map(p => p.request!.resource!.objectId);
}
```

**Python:**
```python
def filter_documents(user_id: str, document_ids: list[str]) -> list[str]:
    items = [CheckBulkPermissionsRequestItem(
        resource=ObjectReference(object_type="document", object_id=doc_id),
        permission="view",
        subject=SubjectReference(object=ObjectReference(object_type="user", object_id=user_id)),
    ) for doc_id in document_ids]
    resp = client.CheckBulkPermissions(CheckBulkPermissionsRequest(items=items))
    return [pair.request.resource.object_id
            for pair in resp.pairs
            if pair.item.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION]
```

### LookupResources

**Go:**
```go
func (s *Service) ListAccessibleDocuments(ctx context.Context, userID string) ([]string, error) {
    stream, err := s.spicedb.LookupResources(ctx, &v1.LookupResourcesRequest{
        ResourceObjectType: "document",
        Permission:         "view",
        Subject: &v1.SubjectReference{
            Object: &v1.ObjectReference{ObjectType: "user", ObjectId: userID},
        },
    })
    if err != nil {
        return nil, fmt.Errorf("lookup failed: %w", err)
    }

    var documentIDs []string
    for {
        resp, err := stream.Recv()
        if err == io.EOF {
            break
        }
        if err != nil {
            return nil, err
        }
        documentIDs = append(documentIDs, resp.ResourceObjectId)
    }

    return documentIDs, nil
}
```

**TypeScript:**
```typescript
async function listAccessibleDocuments(userId: string): Promise<string[]> {
    const stream = client.lookupResources({
        resourceObjectType: 'document',
        permission: 'view',
        subject: { object: { objectType: 'user', objectId: userId } },
    });
    const ids: string[] = [];
    for await (const resp of stream) {
        ids.push(resp.resourceObjectId);
    }
    return ids;
}
```

**Python:**
```python
def list_accessible_documents(user_id: str) -> list[str]:
    responses = client.LookupResources(LookupResourcesRequest(
        resource_object_type="document",
        permission="view",
        subject=SubjectReference(object=ObjectReference(object_type="user", object_id=user_id)),
    ))
    return [resp.resource_object_id for resp in responses]
```

#### Pagination

For production systems where users may have access to thousands of resources, use
cursor-based pagination: set `OptionalLimit` and `OptionalCursor` on the request,
save `AfterResultCursor` from each response, and pass it as the cursor on the next call.

Pass the same ZedToken (`AtLeastAsFresh`) to all page requests in a paginated sequence --
all pages must read from the same revision to produce consistent results.

For full Go and TypeScript pagination examples, see
`skills/spicedb-best-practices/references/client-patterns.md`.

For very large datasets (>10k accessible resources per user), also consider querying
your database for resource IDs first and using `BulkCheckPermission` to filter -- see
`skills/spicedb-best-practices/references/performance-tuning.md`.

### LookupSubjects

**Go:**
```go
func (s *Service) ListUsersWithAccess(ctx context.Context, documentID string) ([]string, error) {
    stream, err := s.spicedb.LookupSubjects(ctx, &v1.LookupSubjectsRequest{
        Resource:          &v1.ObjectReference{ObjectType: "document", ObjectId: documentID},
        Permission:        "view",
        SubjectObjectType: "user",
    })
    if err != nil {
        return nil, fmt.Errorf("lookup subjects failed: %w", err)
    }
    var userIDs []string
    for {
        resp, err := stream.Recv()
        if err == io.EOF { break }
        if err != nil { return nil, err }
        userIDs = append(userIDs, resp.Subject.SubjectObjectId)
    }
    return userIDs, nil
}
```

**TypeScript:**
```typescript
async function listUsersWithAccess(documentId: string): Promise<string[]> {
    const stream = client.lookupSubjects({
        resource: { objectType: 'document', objectId: documentId },
        permission: 'view',
        subjectObjectType: 'user',
    });
    const userIds: string[] = [];
    for await (const resp of stream) {
        userIds.push(resp.subject.subjectObjectId);
    }
    return userIds;
}
```

**Python:**
```python
def list_users_with_access(document_id: str) -> list[str]:
    responses = client.LookupSubjects(LookupSubjectsRequest(
        resource=ObjectReference(object_type="document", object_id=document_id),
        permission="view",
        subject_object_type="user",
    ))
    return [resp.subject.subject_object_id for resp in responses]
```

### Step 5: Cross-Reference Against Schema

Before inserting the generated code, verify permission and resource names against
the schema file:

1. Look for `schema.zed` or any `*.zed` file in the working directory
2. If found, read it and extract all definition names and their declared permission names
3. Check every `ObjectType` and `Permission` value used in the generated code against
   the schema -- each must match an existing definition/permission exactly
4. If any mismatch is found (e.g., code checks `"read"` but schema only declares
   `"view"`), flag it and confirm with the user before proceeding
5. If no schema file is found, note this and proceed -- remind the user to verify
   names manually before deploying

### Step 6: Add Error Handling

Ensure generated code includes:
- Fail-safe on error: deny access if SpiceDB call fails (never silently allow)
- Retry logic for transient errors (`Unavailable`, `DeadlineExceeded`, `ResourceExhausted`)
- Do not retry client errors (`InvalidArgument`, `PermissionDenied`)
- Log errors for debugging

### Step 7: Insert and Verify

Use Edit tool to insert the generated code:
- Add imports at file top
- Add client initialization if needed
- Add operation implementation
- Preserve existing code structure
- Add comments explaining the authorization logic

Suggest the user:
1. Test the implementation
2. Run `/spicedb-dev:test-permissions` to generate test cases
3. Review error handling
4. Check performance with realistic data

## See Also

- For relationship writes and deletes: `/spicedb-dev:implement-spicedb-relationships`
- For data flow analysis and finding authorization boundaries: `checkpoint-identifier` agent
