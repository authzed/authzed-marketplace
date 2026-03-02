---
name: SpiceDB Best Practices
description: Use when implementing SpiceDB client calls, choosing a consistency model,
  handling errors and retries, or optimizing permission check performance - provides
  language-specific patterns for Go/TypeScript/Python with fail-safe error handling
---

# SpiceDB Best Practices

This skill provides guidance on using SpiceDB client libraries effectively, understanding consistency guarantees, optimizing performance, and implementing robust authorization code.

## Overview

SpiceDB is a distributed authorization system. To use it effectively:
- Understand consistency models (when reads reflect writes)
- Handle errors and retries properly
- Use appropriate client patterns for your language
- Optimize for performance without sacrificing correctness
- Use caveats judiciously for dynamic permissions

## Quick Reference

| Need to... | Read This |
|-----------|-----------|
| See client setup for Go/TS/Python | `references/client-patterns.md` |
| Understand consistency / zookies | `references/consistency-deep-dive.md` |
| Optimize performance or add caching | `references/performance-tuning.md` |
| Bootstrap existing data into SpiceDB | `references/bootstrapping.md` |
| Deploying SpiceDB to production | `references/production-deploy.md` |
| See a complete Go client example | `examples/go-client.go` |
| See a complete TypeScript example | `examples/typescript-client.ts` |
| See a complete Python example | `examples/python-client.py` |

## Client Library Usage

SpiceDB provides official clients for multiple languages. Each follows similar patterns but with language-specific idioms.

See `references/client-patterns.md` for full code examples (Go, TypeScript, Python) covering:
- Connection management (development vs. production TLS)
- Writing relationships (`TOUCH` for idempotency)
- Checking permissions (single and bulk)
- Lookup operations (`LookupResources`, `LookupSubjects`)

See `examples/go-client.go`, `examples/typescript-client.ts`, `examples/python-client.py`
for complete, runnable demos with all four operations.

**Core rules:**
- Reuse clients -- don't create one per request
- Use `OPERATION_TOUCH` for writes (idempotent, safe to retry)
- Check all three permissionship values: `HAS_PERMISSION` grants access; both `NO_PERMISSION`
  and `CONDITIONAL_PERMISSION` deny -- treat them identically unless your code explicitly
  supplied caveat context (see `/spicedb-dev:implement-spicedb-checks` for handling)
- Save `WrittenAt` ZedToken after writes (see Consistency section)

## Consistency Guarantees

SpiceDB offers four consistency levels. Choosing correctly avoids both stale reads and
unnecessary latency. See `references/consistency-deep-dive.md` for the full guide including
API defaults, ZedToken routing pattern, and storage recommendations.

**Summary:**
- Default for reads: `minimize_latency` (may be stale)
- Default for writes: `fully_consistent` (always committed)
- Read-your-writes: use `at_least_as_fresh` with the `WrittenAt` ZedToken from your write
- `fully_consistent` on reads **bypasses the cache** -- only for admin/debug/audit
- `at_exact_snapshot` can fail with "Snapshot Expired" if the GC window has passed

**ZedToken routing (the correct read-your-writes pattern):**
Write → capture `WrittenAt` token → pass in HTTP response header → use as `AtLeastAsFresh`
in next `CheckPermission`. Store ZedTokens as `text` / `varchar(1024)` in your database.

## Write Ordering

Write to your database first, commit, then write to SpiceDB with `OPERATION_TOUCH`.
Do NOT use a distributed transaction (2PC) spanning your DB and SpiceDB.

`TOUCH` is idempotent -- if the SpiceDB write fails after your DB commit, it is safe to
retry without risk of duplicate or inconsistent state. If the write repeatedly fails, the
system is in a temporarily inconsistent state (DB committed, SpiceDB not updated); implement
a reconciliation job or retry queue for production systems.

## Red Flags

If you find yourself:
- Copying connection setup code from memory → Read `references/client-patterns.md` for exact patterns
- Unsure whether to use FullyConsistent or AtLeastAsFresh → Read `references/consistency-deep-dive.md`
- Seeing flaky permission checks after writes → You need zookies; read `references/consistency-deep-dive.md`
- Checking permissions in a tight loop → Read `references/performance-tuning.md` for batch/cache patterns
- Designing a schema inside this skill → Switch to `spicedb-schema-design` skill

## What This Skill Does NOT Do

- Design SpiceDB schemas -- use `spicedb-schema-design` skill for that
- Write authorization tests -- use `authorization-testing` skill for that
- Generate production client code interactively -- use `/spicedb-dev:implement-spicedb-checks` or `/spicedb-dev:implement-spicedb-relationships` for that

## Error Handling

Fail-safe on any error: deny access when SpiceDB is unavailable. Retry transient errors
(`Unavailable`, `DeadlineExceeded`, `ResourceExhausted`) with exponential backoff. Do not
retry client errors (`InvalidArgument`, `PermissionDenied`).

See `references/client-patterns.md` for Go code patterns for each gRPC status code and
the exponential backoff retry pattern.

## Performance Optimization

See `references/performance-tuning.md` for detailed guidance including:
- `CheckBulkPermissions` vs looping `CheckPermission` (always prefer bulk)
- `ImportBulkRelationships` for large data loads (>1,000 relationships)
- Caching patterns with TTL and invalidation
- Retry policy (include `RESOURCE_EXHAUSTED` and `ABORTED` alongside `UNAVAILABLE`)
- Three-tier list strategy (LookupResources → CheckBulkPermissions → Materialize)

**Key rules:** Batch writes up to 1,000 per request; use `ImportBulkRelationships` for larger imports.
Use `CheckBulkPermissions` for any list-filtering scenario. Profile before optimizing.

## Caveats Best Practices

Caveats add dynamic, context-aware conditions evaluated at check time.

**Use caveats for:** Time-based conditions, request context (IP, user agent), simple attributes.
**Don't use caveats for:** Static relationships (use relations), complex business logic, multi-step workflows.

Supply caveat context in `CheckPermissionRequest.Context` at check time. Handle
`PERMISSIONSHIP_CONDITIONAL_PERMISSION` when context was not fully provided.

**Recommendation:** Start without caveats; add only when dynamic runtime context is truly needed.
For temporary access (SpiceDB v1.40+), prefer native expiration over time-based caveats.

## Additional Resources

### Reference Files

For detailed implementation patterns and advanced techniques:
- **`references/client-patterns.md`** - Language-specific client patterns
- **`references/consistency-deep-dive.md`** - Detailed consistency guarantees
- **`references/performance-tuning.md`** - Advanced performance optimization

### Example Code

Working examples in `examples/`:
- **`examples/go-client.go`** - Complete Go client example
- **`examples/typescript-client.ts`** - Complete TypeScript example
- **`examples/python-client.py`** - Complete Python example

### External Resources

- [SpiceDB Clients](https://authzed.com/docs/spicedb/getting-started/clients) - Official client documentation
- [Consistency Explained](https://authzed.com/docs/spicedb/concepts/consistency) - Consistency model details
- [Caveats Guide](https://authzed.com/docs/spicedb/concepts/caveats) - Caveats documentation

---

**Workflow summary:** Initialize client (once, reuse) → Write relationships (TOUCH for idempotency) → Check permissions (choose consistency level) → Handle errors (fail-safe: deny on error) → Optimize (batch, cache, profile before tuning). Security principle: deny by default on any error.
