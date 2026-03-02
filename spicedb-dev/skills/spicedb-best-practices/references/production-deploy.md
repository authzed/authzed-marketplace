# Deploying SpiceDB to Production

This guide covers the steps between "SpiceDB implemented locally" and "SpiceDB enforcing
permissions in production."

---

## Pre-Production Gate

Verify all of the following before enabling SpiceDB checks in production:

- [ ] All existing data bootstrapped into SpiceDB (see `references/bootstrapping.md`)
- [ ] Spot-checked with `FullyConsistent` on known-good user/resource pairs across all resource types
- [ ] ZedToken threading verified end-to-end (write → capture `WrittenAt` → pass in header → use as `AtLeastAsFresh`)
- [ ] Error baseline established: run a load test and confirm error rate <1% at expected QPS
- [ ] `CONDITIONAL_PERMISSION` rate is expected (near zero unless your schema uses caveats)
- [ ] Rollback plan documented: feature flag disables SpiceDB enforcement, fallback behavior defined
- [ ] SpiceDB client configured with production TLS (not `INSECURE_PLAINTEXT_CREDENTIALS`)

---

## Deployment Sequence

### 1. Shadow Mode

Enable SpiceDB relationship writes behind a feature flag but do NOT enforce permission checks.
Dual-write: every new user/resource creation writes to both your database and SpiceDB.

Goal: verify SpiceDB is receiving writes correctly without affecting request outcomes.

### 2. Bootstrap

Import all existing database relationships using `references/bootstrapping.md` patterns.
Write in hierarchy order (orgs → teams → projects → resources → memberships).

### 3. Verify

Spot-check expected results using `FullyConsistent` on a representative sample of
known-good and known-denied user/resource pairs. Sample across all resource types and
permission levels.

```go
// Verification spot-check (FullyConsistent is appropriate here -- one-time check only)
resp, err := client.CheckPermission(ctx, &v1.CheckPermissionRequest{
    Resource:    &v1.ObjectReference{ObjectType: "document", ObjectId: "known-doc-id"},
    Permission:  "view",
    Subject:     &v1.SubjectReference{Object: &v1.ObjectReference{ObjectType: "user", ObjectId: "known-user-id"}},
    Consistency: &v1.Consistency{Requirement: &v1.Consistency_FullyConsistent{FullyConsistent: true}},
})
// Expect: HAS_PERMISSION for known-good pair, NO_PERMISSION for known-denied pair
```

Do not use `FullyConsistent` in production request paths -- this is a one-time deployment check.

### 4. Enable

Flip the feature flag to enforce SpiceDB checks. Monitor error rates closely for the
first 30 minutes.

### 5. Dual-Write Window

Continue writing to both systems. If a SpiceDB write fails after a DB commit, the system
is temporarily inconsistent -- implement a retry queue or reconciliation job.

### 6. Retire

Once SpiceDB is authoritative and the dual-write window has passed without issues, remove:
- Legacy permission checks
- Dual-write code
- Bootstrap scripts (keep as a template for disaster recovery)

---

## Post-Deployment Monitoring

Key signals to watch in the first week:

| Signal | Target | Action if exceeded |
|---|---|---|
| CheckPermission error rate | <0.1% | Check SpiceDB health, inspect error codes |
| CheckPermission p95 latency | <10ms | Review consistency model, check for FullyConsistent misuse |
| CONDITIONAL_PERMISSION rate | ~0% (unless using caveats) | Check caveat context is being supplied |
| WriteRelationships error rate | <0.1% | Check retry queue depth, inspect for schema mismatches |

---

## Rollback Plan

Wrap all SpiceDB enforcement in a feature flag:

```go
if featureFlags.SpiceDBEnforced(ctx) {
    resp, err := s.spicedb.CheckPermission(ctx, req)
    if err != nil || resp.Permissionship != v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION {
        return nil, status.Error(codes.PermissionDenied, "access denied")
    }
}
// Without flag: fall through to legacy check or allow
```

If SpiceDB is unavailable or erroring at high rate:
1. Disable the feature flag to fall back to legacy checks
2. Keep writing relationships (dual-write) so SpiceDB stays up-to-date
3. Re-enable once SpiceDB is healthy

**Do not fail-open** (allow all requests) when SpiceDB is down -- fail-safe means deny
or fall back to the legacy check, not allow.
