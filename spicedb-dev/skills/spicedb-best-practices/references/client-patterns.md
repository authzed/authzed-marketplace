# Client Patterns

Language-specific patterns for connecting to and using SpiceDB client libraries.

## Language / SDK Comparison

These are Authzed's **established, published** clients -- installed normally, from the
registry, in every example below.

| Language | Package | Import |
|----------|---------|--------|
| Go | `authzed-go` | `authzed "github.com/authzed/authzed-go/v1"` |
| TypeScript | `authzed-node` | `import { v1 } from '@authzed/authzed-node'` |
| Python | `authzed` | `from authzed.api.v1 import Client` |
| C# | `Authzed.Net` | `using Authzed.Api.V1;` |

> **There is a second, different family of clients, and they are not interchangeable.** The
> `spicedb-client-integration` skill covers a **prototype** client set spanning seven
> languages (the four above plus Java, Rust, and Ruby), which is unpublished, vendored at a
> pinned commit, and labeled by its own repository "not for production use." It has a
> different API from the packages tabled here -- different method names, its own
> `Relationship`/`Filter`/`Transaction` types, its own consistency helpers -- so patterns do
> not port between the two by renaming. Use the published packages above unless you
> specifically need the prototype's API or one of the three languages only it covers. The
> `/spicedb-dev:migrate-code` phase of an OpenFGA migration targets the prototype; everything
> else in this plugin targets these.

---

## Connection Management

**Best practice:** Create one client per application, reuse across requests.

### Go

```go
import (
    authzed "github.com/authzed/authzed-go/v1"
    "github.com/authzed/grpcutil"
    "google.golang.org/grpc"
    "google.golang.org/grpc/credentials/insecure"
)

// Production (TLS)
client, err := authzed.NewClient(
    endpoint,
    grpcutil.WithBearerToken(token),
    grpc.WithTransportCredentials(tls.NewClientTLSFromCert(nil, "")),
)

// Development (no TLS)
client, err := authzed.NewClient(
    endpoint,
    grpc.WithTransportCredentials(insecure.NewCredentials()),
    grpcutil.WithInsecureBearerToken(token),
)
if err != nil {
    log.Fatalf("failed to create client: %v", err)
}
defer client.Close()
```

### TypeScript

```typescript
import { v1 } from '@authzed/authzed-node';

// Production (TLS)
const client = v1.NewClient(token, endpoint, v1.ClientSecurity.SECURE);

// Development (no TLS)
const client = v1.NewClient(
    token,
    endpoint,
    v1.ClientSecurity.INSECURE_PLAINTEXT_CREDENTIALS
);
```

### Python

```python
from authzed.api.v1 import Client
from grpcutil import bearer_token_credentials, insecure_bearer_token_credentials

# Production (TLS)
client = Client(endpoint, bearer_token_credentials(token))

# Development (no TLS)
client = Client(endpoint, insecure_bearer_token_credentials(token))
```

---

## Getting the Subject ID

The `sub` claim from a JWT/OIDC token is the correct value to use as the SpiceDB object ID
for users. It is stable and immutable. Do not use `email` -- email addresses contain `@`
(invalid in SpiceDB object IDs by default) and can change when a user updates their email.

### Go (using golang-jwt / jwt-go)

```go
// Extract subject from JWT claims in a gin/echo/chi middleware context
claims := ctx.Value("claims").(jwt.MapClaims)
userID := claims["sub"].(string)  // stable OIDC subject -- use this, not claims["email"]
```

### TypeScript (Express/Fastify)

```typescript
// Extract subject from decoded JWT payload
const userID = (req as any).user.sub as string;  // stable OIDC subject -- not req.user.email
```

### Python (FastAPI/Flask)

```python
# Extract subject from JWT payload (e.g., decoded by python-jose or PyJWT)
user_id = token_payload["sub"]  # stable OIDC subject -- not token_payload["email"]
```

---

## Writing Relationships

**Use `TOUCH` (upsert) for most operations** -- idempotent and safe to retry.

### Go

```go
import v1 "github.com/authzed/authzed-go/proto/authzed/api/v1"

resp, err := client.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{
    Updates: []*v1.RelationshipUpdate{
        {
            Operation: v1.RelationshipUpdate_OPERATION_TOUCH,
            Relationship: &v1.Relationship{
                Resource: &v1.ObjectReference{
                    ObjectType: "document",
                    ObjectId:   "doc-123",
                },
                Relation: "viewer",
                Subject: &v1.SubjectReference{
                    Object: &v1.ObjectReference{
                        ObjectType: "user",
                        ObjectId:   "alice",
                    },
                },
            },
        },
    },
})
// Save resp.WrittenAt (ZedToken) for read-your-writes consistency
```

### TypeScript

```typescript
await client.writeRelationships(v1.WriteRelationshipsRequest.create({
    updates: [{
        operation: v1.RelationshipUpdate_Operation.TOUCH,
        relationship: v1.Relationship.create({
            resource: v1.ObjectReference.create({ objectType: "document", objectId: "doc-123" }),
            relation: "viewer",
            subject: v1.SubjectReference.create({
                object: v1.ObjectReference.create({ objectType: "user", objectId: "alice" }),
            }),
        }),
    }],
}));
```

### Python

```python
from authzed.api.v1 import (
    WriteRelationshipsRequest, RelationshipUpdate,
    Relationship, ObjectReference, SubjectReference,
)

resp = client.WriteRelationships(WriteRelationshipsRequest(
    updates=[RelationshipUpdate(
        operation=RelationshipUpdate.OPERATION_TOUCH,
        relationship=Relationship(
            resource=ObjectReference(object_type="document", object_id="doc-123"),
            relation="viewer",
            subject=SubjectReference(
                object=ObjectReference(object_type="user", object_id="alice")
            ),
        ),
    )]
))
# resp.written_at is the ZedToken
```

**Operations:**
- `TOUCH` -- create or update (idempotent, prefer this)
- `CREATE` -- create only (fails if exists)
- `DELETE` -- remove relationship

**Batch limit:** Default 1,000 updates per `WriteRelationships` request. For larger
imports, use `ImportBulkRelationships` (see `references/performance-tuning.md`).

---

## Checking Permissions

### Go

```go
resp, err := client.CheckPermission(ctx, &v1.CheckPermissionRequest{
    Resource: &v1.ObjectReference{
        ObjectType: "document",
        ObjectId:   "doc-123",
    },
    Permission: "view",
    Subject: &v1.SubjectReference{
        Object: &v1.ObjectReference{
            ObjectType: "user",
            ObjectId:   "alice",
        },
    },
    Consistency: &v1.Consistency{
        Requirement: &v1.Consistency_MinimizeLatency{MinimizeLatency: true},
    },
})
if err != nil {
    // Fail-safe: deny access on any error
    return false, err
}
return resp.Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION, nil
```

### TypeScript

```typescript
const resp = await client.checkPermission(v1.CheckPermissionRequest.create({
    resource: v1.ObjectReference.create({ objectType: "document", objectId: "doc-123" }),
    permission: "view",
    subject: v1.SubjectReference.create({
        object: v1.ObjectReference.create({ objectType: "user", objectId: "alice" }),
    }),
    consistency: v1.Consistency.create({
        requirement: { oneofKind: "minimizeLatency", minimizeLatency: true },
    }),
}));
return resp.permissionship === v1.CheckPermissionResponse_Permissionship.HAS_PERMISSION;
```

### Python

```python
from authzed.api.v1 import CheckPermissionRequest, Consistency, CheckPermissionResponse

resp = client.CheckPermission(CheckPermissionRequest(
    resource=ObjectReference(object_type="document", object_id="doc-123"),
    permission="view",
    subject=SubjectReference(object=ObjectReference(object_type="user", object_id="alice")),
    consistency=Consistency(minimize_latency=True),
))
return resp.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION
```

**Permission values:**
- `HAS_PERMISSION` -- access granted
- `NO_PERMISSION` -- access denied
- `CONDITIONAL_PERMISSION` -- caveat exists; result depends on context provided

### Caveats and CONDITIONAL_PERMISSION

If your schema uses caveats, pass `Context` in the request and handle
`CONDITIONAL_PERMISSION` as a denial (caveat context was not sufficient to resolve).
Both `NO_PERMISSION` and `CONDITIONAL_PERMISSION` should deny access unless caveat
context was explicitly supplied.

**Go:**
```go
import "google.golang.org/protobuf/types/known/structpb"

resp, err := client.CheckPermission(ctx, &v1.CheckPermissionRequest{
    Resource:    &v1.ObjectReference{ObjectType: "document", ObjectId: documentID},
    Permission:  "view",
    Subject:     &v1.SubjectReference{Object: &v1.ObjectReference{ObjectType: "user", ObjectId: userID}},
    Consistency: consistency,
    Context: &structpb.Struct{
        Fields: map[string]*structpb.Value{
            "current_time": structpb.NewStringValue(time.Now().Format(time.RFC3339)),
            "ip_address":   structpb.NewStringValue(clientIP),
        },
    },
})
if err != nil {
    return nil, fmt.Errorf("permission check failed: %w", err)
}
switch resp.Permissionship {
case v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION:
    // access granted -- continue
case v1.CheckPermissionResponse_PERMISSIONSHIP_CONDITIONAL_PERMISSION:
    // caveat context was not sufficient to resolve; deny
    return nil, status.Error(codes.PermissionDenied, "permission is conditional -- caveat context required")
default:
    return nil, status.Error(codes.PermissionDenied, "access denied")
}
```

**TypeScript:**
```typescript
const response = await client.checkPermission({
    resource: { objectType: 'document', objectId: documentId },
    permission: 'view',
    subject: { object: { objectType: 'user', objectId: userId } },
    consistency,
    context: {
        fields: {
            current_time: { stringValue: new Date().toISOString() },
            ip_address:   { stringValue: clientIP },
        },
    },
});

if (response.permissionship === v1.CheckPermissionResponse_Permissionship.HAS_PERMISSION) {
    // access granted
} else if (response.permissionship === v1.CheckPermissionResponse_Permissionship.CONDITIONAL_PERMISSION) {
    throw new Error('Permission is conditional -- caveat context required');
} else {
    throw new Error('Access denied');
}
```

**Python:**
```python
from google.protobuf import struct_pb2

response = client.CheckPermission(CheckPermissionRequest(
    resource=ObjectReference(object_type="document", object_id=document_id),
    permission="view",
    subject=SubjectReference(object=ObjectReference(object_type="user", object_id=user_id)),
    consistency=consistency,
    context=struct_pb2.Struct(fields={
        "current_time": struct_pb2.Value(string_value=datetime.utcnow().isoformat()),
        "ip_address":   struct_pb2.Value(string_value=client_ip),
    }),
))

if response.permissionship == CheckPermissionResponse.PERMISSIONSHIP_HAS_PERMISSION:
    pass  # access granted
elif response.permissionship == CheckPermissionResponse.PERMISSIONSHIP_CONDITIONAL_PERMISSION:
    raise PermissionError("Permission is conditional -- caveat context required")
else:
    raise PermissionError("Access denied")
```

---

## Bulk Checking Permissions

`CheckBulkPermissions` is **always preferable** to N individual `CheckPermission` calls.

### Go

```go
resp, err := client.CheckBulkPermissions(ctx, &v1.CheckBulkPermissionsRequest{
    Items: []*v1.CheckBulkPermissionsRequestItem{
        {
            Resource:   &v1.ObjectReference{ObjectType: "document", ObjectId: "doc-1"},
            Permission: "view",
            Subject:    &v1.SubjectReference{Object: &v1.ObjectReference{ObjectType: "user", ObjectId: "alice"}},
        },
        {
            Resource:   &v1.ObjectReference{ObjectType: "document", ObjectId: "doc-2"},
            Permission: "view",
            Subject:    &v1.SubjectReference{Object: &v1.ObjectReference{ObjectType: "user", ObjectId: "alice"}},
        },
    },
})
for _, pair := range resp.Pairs {
    if pair.GetItem().Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION {
        fmt.Printf("access granted to %s\n", pair.Request.Resource.ObjectId)
    }
}
```

**When to use:**
- Filtering lists (which documents can user view?)
- Dashboard pages with multiple permission checks
- Any time you'd call `CheckPermission` in a loop

---

## Lookup Operations

### LookupResources (find all resources a subject can access)

```go
stream, err := client.LookupResources(ctx, &v1.LookupResourcesRequest{
    ResourceObjectType: "document",
    Permission:         "view",
    Subject: &v1.SubjectReference{
        Object: &v1.ObjectReference{ObjectType: "user", ObjectId: "alice"},
    },
})
for {
    resp, err := stream.Recv()
    if err == io.EOF { break }
    if err != nil { return err }
    fmt.Println(resp.ResourceObjectId)
}
```

### Cursor Pagination

For production systems where users may have access to thousands of resources, use
cursor-based pagination: set `OptionalLimit` and `OptionalCursor` on the request,
save `AfterResultCursor` from each response, and pass it as the cursor on the next call.

**Go:**
```go
func (s *Service) ListAccessibleDocumentsPaginated(
    ctx context.Context, userID string, pageSize uint32, cursor *v1.Cursor,
) (ids []string, nextCursor *v1.Cursor, err error) {
    stream, err := s.spicedb.LookupResources(ctx, &v1.LookupResourcesRequest{
        ResourceObjectType: "document",
        Permission:         "view",
        Subject: &v1.SubjectReference{
            Object: &v1.ObjectReference{ObjectType: "user", ObjectId: userID},
        },
        OptionalLimit:  pageSize,  // 0 means no limit (collect all)
        OptionalCursor: cursor,    // nil starts from the beginning
    })
    if err != nil {
        return nil, nil, fmt.Errorf("lookup failed: %w", err)
    }
    for {
        resp, err := stream.Recv()
        if err == io.EOF {
            break
        }
        if err != nil {
            return nil, nil, err
        }
        ids = append(ids, resp.ResourceObjectId)
        nextCursor = resp.AfterResultCursor // save for next page call
    }
    return ids, nextCursor, nil
}
```

**TypeScript:**
```typescript
async function listAccessibleDocumentsPaginated(
    userId: string, pageSize: number, cursor?: v1.Cursor
): Promise<{ ids: string[]; nextCursor?: v1.Cursor }> {
    const stream = client.lookupResources({
        resourceObjectType: 'document',
        permission: 'view',
        subject: { object: { objectType: 'user', objectId: userId } },
        optionalLimit: pageSize,
        optionalCursor: cursor,
    });
    const ids: string[] = [];
    let nextCursor: v1.Cursor | undefined;
    for await (const resp of stream) {
        ids.push(resp.resourceObjectId);
        nextCursor = resp.afterResultCursor;
    }
    return { ids, nextCursor };
}
```

Pass the returned `nextCursor` as the `cursor` argument to retrieve the next page.
A nil/undefined cursor starts from the beginning.

**Consistency across pages:** Pass the same `AtLeastAsFresh` ZedToken to every page
request in a paginated sequence. This ensures all pages read from the same revision.
Without it, different pages may reflect different states of the data.

### LookupSubjects (find all subjects with permission on a resource)

```go
stream, err := client.LookupSubjects(ctx, &v1.LookupSubjectsRequest{
    Resource: &v1.ObjectReference{ObjectType: "document", ObjectId: "doc-123"},
    Permission:        "view",
    SubjectObjectType: "user",
})
```

**Scale guidance for list endpoints:**
1. `LookupResources` -- simple, suitable up to ~10k resources
2. `CheckBulkPermissions` with cursor pagination -- for large sets, run all checks
   at the same ZedToken revision for consistency
3. Authzed Materialize -- for maximum scale (pre-computed permission sets)

---

## Key Rules

1. Reuse clients -- don't create one per request
2. Use `TOUCH` for writes (idempotent)
3. Save `WrittenAt` ZedToken after writes for read-your-writes
4. Prefer `CheckBulkPermissions` over looping `CheckPermission`
5. Fail-safe on errors -- deny access when SpiceDB is unavailable
6. Use TLS in production

## Error Handling

### Not Found / Permission Denied

```go
if st, ok := status.FromError(err); ok {
    switch st.Code() {
    case codes.NotFound:
        // Resource doesn't exist
    case codes.PermissionDenied:
        // Invalid token or insufficient permissions to call the API
    }
}
```

### Invalid Argument

```go
case codes.InvalidArgument:
    // Schema violation, malformed request
    // Check object types, IDs, permission names
    // Do NOT retry -- this is a client error
```

### Unavailable

```go
case codes.Unavailable:
    // SpiceDB unavailable, network issue
    // Retry with exponential backoff
```

### Exponential Backoff Retry

```go
import "github.com/cenkalti/backoff/v4"

operation := func() error {
    _, err := client.CheckPermission(ctx, req)
    return err
}

err := backoff.Retry(operation, backoff.NewExponentialBackOff())
```

Retry transient errors (`Unavailable`, `DeadlineExceeded`, `ResourceExhausted`).
Do not retry client errors (`InvalidArgument`, `PermissionDenied`).

### Fail-Safe (Deny on Error)

```go
resp, err := client.CheckPermission(ctx, req)
if err != nil {
    // Log error
    log.Printf("permission check failed: %v", err)

    // Fail-safe: deny access on error
    return false, err
}

return resp.Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION, nil
```

**Security principle:** Deny by default on errors.
