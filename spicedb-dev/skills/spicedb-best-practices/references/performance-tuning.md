# Performance Tuning

Advanced performance optimization for SpiceDB client operations.

## Quick Reference: Operation Choice

| Operation | Use When | Expected Latency Target |
|-----------|----------|------------------------|
| `CheckPermission` (single) | Single resource, single user | < 10ms p95 |
| `CheckBulkPermissions` | Multiple resources or permissions | < 10ms p95 total |
| `LookupResources` (small) | "What can I access?" up to ~10k resources | < 100ms p95 |
| `LookupResources` + pagination | Large resource sets | varies |
| `WriteRelationships` | Up to 1,000 updates | < 50ms p95 |
| `ImportBulkRelationships` | Initial data load, large imports | throughput-bound |

---

## Batching

### WriteRelationships Batch Limit

`WriteRelationships` accepts a maximum of **1,000 updates per request** by default.
Batch multiple writes in one request for efficiency (single round-trip, single transaction):

```go
updates := make([]*v1.RelationshipUpdate, 0, len(relationships))
for _, rel := range relationships {
    updates = append(updates, &v1.RelationshipUpdate{
        Operation:    v1.RelationshipUpdate_OPERATION_TOUCH,
        Relationship: rel,
    })
}
// Split into chunks of 1000 if needed
_, err := client.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{Updates: updates})
```

### ImportBulkRelationships for Large Imports

For initial data loads or imports > 1,000 relationships, use `ImportBulkRelationships`:

```go
stream, err := client.ImportBulkRelationships(ctx)
if err != nil {
    return err
}
// Stream relationships in chunks
for _, chunk := range chunks {
    err := stream.Send(&v1.ImportBulkRelationshipsRequest{Relationships: chunk})
    if err != nil { return err }
}
resp, err := stream.CloseAndRecv()
// Commits as one transaction when stream closes
```

Key characteristics of `ImportBulkRelationships`:
- Streaming gRPC -- no per-request limit
- Commits as one transaction when the stream ends
- `CREATE`-only semantics (no upsert) -- use `RetryableClient` to handle conflicts gracefully
- Much higher throughput than looping `WriteRelationships`

---

## Bulk Permission Checking

**It's always preferable to perform one call to `CheckBulkPermissions` with N checks
than N calls to `CheckPermission`.**

```go
// ❌ SLOW: N round-trips
for _, docID := range docIDs {
    resp, _ := client.CheckPermission(ctx, buildRequest(userID, docID))
    // ...
}

// ✅ FAST: one round-trip, parallelized internally
resp, err := client.CheckBulkPermissions(ctx, &v1.CheckBulkPermissionsRequest{
    Items: buildBulkItems(userID, docIDs),
})
```

For protecting list endpoints, the three-tier strategy:

1. **`LookupResources`** -- simple, suitable for up to ~10k resources
2. **`CheckBulkPermissions` with cursor pagination** -- for large result sets;
   run all checks at the same `AtExactSnapshot` ZedToken for consistent results
3. **Authzed Materialize** -- pre-computed permission sets for maximum scale

---

## Caching

**Pattern:** Cache permission results with a short TTL.

```go
type CachedChecker struct {
    client spicedb.Client
    cache  *ttlcache.Cache[string, bool]
}

func (c *CachedChecker) Check(ctx context.Context, subject, permission, resource string) (bool, error) {
    key := fmt.Sprintf("%s|%s|%s", subject, permission, resource)

    if entry, ok := c.cache.Get(key); ok {
        return entry, nil
    }

    resp, err := c.client.CheckPermission(ctx, buildRequest(subject, permission, resource))
    if err != nil {
        return false, err  // fail-safe: deny on error
    }

    result := resp.Permissionship == v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION
    c.cache.Set(key, result, ttl)
    return result, nil
}
```

**Considerations:**
- Invalidate cache entries on relationship changes (e.g., via event or short TTL)
- Use short TTL (seconds to minutes) -- correctness > performance
- Do NOT cache for security-critical operations without careful invalidation
- Do NOT cache `CONDITIONAL_PERMISSION` results (they depend on request context)

---

## Retry Policy

Retry on these gRPC status codes with exponential backoff:

| Code | Reason |
|------|--------|
| `UNAVAILABLE` | SpiceDB not reachable, network issue |
| `DEADLINE_EXCEEDED` | Request timed out |
| `RESOURCE_EXHAUSTED` | SpiceDB OOM protection rejected request |
| `ABORTED` | Conflict (write collision) |

Do NOT retry:
- `INVALID_ARGUMENT` -- bad request; retrying won't help
- `PERMISSION_DENIED` -- invalid token
- `NOT_FOUND` -- resource/schema doesn't exist

```go
import "github.com/cenkalti/backoff/v4"

retryable := func() error {
    _, err := client.CheckPermission(ctx, req)
    if err != nil {
        code := status.Code(err)
        if code == codes.Unavailable ||
           code == codes.DeadlineExceeded ||
           code == codes.ResourceExhausted ||
           code == codes.Aborted {
            return err  // retryable
        }
        return backoff.Permanent(err)  // non-retryable
    }
    return nil
}

err := backoff.Retry(retryable, backoff.NewExponentialBackOff())
```

---

## Schema-Level Optimizations

Performance problems often start in the schema. See `spicedb-schema-design` skill for:
- Limit arrow chain depth (prefer 2-3 levels; use recursive definitions for deep hierarchies)
- Avoid expensive patterns (arrows over relations with subject relations)
- Use `use typechecking` to catch silent always-false permissions at schema write time

---

## Caveats Performance

Caveats add overhead at check time because SpiceDB must evaluate the CEL expression
for each check:
- Each caveated relationship the traversal touches adds one CEL evaluation
- Prefer static relations over caveats when the condition is not truly dynamic
- Prefer SpiceDB v1.40+ native expiration over time-based caveats for temporary access
  (native expiration uses indexed GC; caveated relationships accumulate until explicitly deleted)
