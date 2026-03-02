# Consistency Deep Dive

Detailed guide to SpiceDB's consistency models and ZedToken (zookie) usage.

---

## API Defaults

Understanding what consistency each API uses by default:

| API | Default Consistency |
|-----|-------------------|
| `WriteRelationships` | `fully_consistent` |
| `DeleteRelationships` | `fully_consistent` |
| `WriteSchema` | `fully_consistent` |
| `CheckPermission` | `minimize_latency` |
| `CheckBulkPermissions` | `minimize_latency` |
| `LookupResources` | `minimize_latency` |
| `LookupSubjects` | `minimize_latency` |
| `ReadRelationships` | `minimize_latency` |

Writes always commit with full consistency. Reads default to low-latency (potentially stale).

---

## Consistency Models

### 1. Minimize Latency (default for reads)

```go
Consistency: &v1.Consistency{
    Requirement: &v1.Consistency_MinimizeLatency{MinimizeLatency: true},
}
```

- SpiceDB picks the fastest available replica
- May read stale data if replicas lag
- Lowest latency
- Use for: Non-critical checks, analytics, caching-backed reads

### 2. At Least As Fresh (ZedToken-based, recommended for read-your-writes)

```go
Consistency: &v1.Consistency{
    Requirement: &v1.Consistency_AtLeastAsFresh{
        AtLeastAsFresh: zedToken, // captured from a prior write response
    },
}
```

- Guarantees the check reflects at least the write that produced `zedToken`
- Lower latency than `FullyConsistent` (uses cached results from eligible replicas)
- Use for: Read-your-writes after permission changes

### 3. Fully Consistent

```go
Consistency: &v1.Consistency{
    Requirement: &v1.Consistency_FullyConsistent{FullyConsistent: true},
}
```

- Guarantees the check reflects ALL prior writes regardless of replica
- **BYPASSES the SpiceDB cache** -- significantly increases latency
- Use for: One-off admin operations, debugging, compliance audits
- ❌ Do NOT use for read-your-writes -- use `at_least_as_fresh` with a ZedToken instead
- ❌ Do NOT use in normal request paths at scale

### 4. At Exact Snapshot

```go
Consistency: &v1.Consistency{
    Requirement: &v1.Consistency_AtExactSnapshot{
        AtExactSnapshot: &v1.ZedToken{Token: snapshotToken},
    },
}
```

- Reads at the exact revision encoded in the token
- Can fail with `"Snapshot Expired"` if SpiceDB has GC'd old snapshots
  (governed by `--datastore-gc-window`, default varies by datastore)
- Use for: Paginating a single result set within a short time window
- ❌ Do NOT store snapshot tokens long-term; they expire

---

## ZedTokens (Zookies)

A ZedToken is an opaque token encoding a specific revision of the SpiceDB datastore.
They are the key to efficient read-your-writes without paying the `FullyConsistent` penalty.

### How ZedTokens Work

```
Write ──► capture WrittenAt token
              │
              └──► store in HTTP response header / session / DB column
                        │
                        └──► next CheckPermission uses AtLeastAsFresh(token)
                                   │
                                   └──► SpiceDB routes to a replica that has
                                        seen at least that revision
```

This is the correct read-your-writes pattern. It lets SpiceDB use its cache and
choose a fast replica while still guaranteeing the user sees their own change.

### Code Pattern

```go
// 1. Write and capture token
writeResp, err := client.WriteRelationships(ctx, &v1.WriteRelationshipsRequest{
    Updates: updates,
})
if err != nil {
    return err
}
zedToken := writeResp.WrittenAt  // *v1.ZedToken

// 2. Pass token to client (e.g., in HTTP response header)
w.Header().Set("X-Authz-Token", zedToken.Token)

// 3. On subsequent request, read token and use AtLeastAsFresh
zedToken := &v1.ZedToken{Token: r.Header.Get("X-Authz-Token")}

checkResp, err := client.CheckPermission(ctx, &v1.CheckPermissionRequest{
    Resource:   resource,
    Permission: "view",
    Subject:    subject,
    Consistency: &v1.Consistency{
        Requirement: &v1.Consistency_AtLeastAsFresh{
            AtLeastAsFresh: zedToken,
        },
    },
})
```

### When to Capture a New ZedToken

Capture and persist the `WrittenAt` token from `WriteRelationships` whenever:
- A resource is created or deleted
- Content changes that affect permissions (ownership changes, etc.)
- Any relationship is written

### Storing ZedTokens

- Store in Postgres as `text` or `varchar(1024)` -- tokens are base64-encoded strings
- Store per-resource or per-user in your application database
- Include in HTTP responses for client-side routing (e.g., `X-Authz-Token` header)
- Include in API response JSON for mobile/SPA clients

---

## Common Mistakes

### Using FullyConsistent for Read-Your-Writes

```go
// ❌ WRONG: expensive, bypasses cache, doesn't scale
Consistency: &v1.Consistency{
    Requirement: &v1.Consistency_FullyConsistent{FullyConsistent: true},
}

// ✅ CORRECT: use the ZedToken from the write
Consistency: &v1.Consistency{
    Requirement: &v1.Consistency_AtLeastAsFresh{AtLeastAsFresh: writtenAt},
}
```

### Storing AtExactSnapshot Tokens Long-Term

```go
// ❌ WRONG: snapshot tokens expire; using one after GC returns "Snapshot Expired"
savedToken := getFromDB()  // stored days ago
Consistency: &v1.Consistency{
    Requirement: &v1.Consistency_AtExactSnapshot{
        AtExactSnapshot: &v1.ZedToken{Token: savedToken},
    },
}

// ✅ CORRECT: only use AtExactSnapshot for pagination within a single request
```

### No Consistency Set (Omitting Field)

Omitting the `Consistency` field uses `minimize_latency` for reads. This is fine for most
cases but can cause users to not see a permission they just granted themselves. Use
`at_least_as_fresh` when serving a response immediately after a write.
