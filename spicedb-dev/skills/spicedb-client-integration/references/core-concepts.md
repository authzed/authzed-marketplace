# Core Concepts

The vocabulary in this file is shared by all seven SpiceDB clients (Go, Python, TypeScript, C#,
Java, Rust, Ruby). Learn it once here; the per-language references only need to cover naming
and idiom on top of it.

Client API facts below (types, method names, consistency helper names) were verified against
the vendored clients' real source -- their compiled signatures, not their README/DESIGN prose --
at commit `549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`. See "Trust the code, not the docs" below
for why that distinction matters. The three product-level limits in the second half of this file were verified live
against `spicedb serve-testing` v1.56.0 for this reference; see `references/installation.md` for
how to stand up the same server.

---

## `Relationship`, `Filter`, and `Transaction`

Every client models the same three things, structurally identical across all seven languages,
just cased per language convention (`PascalCase` in Go/C#, `camelCase` in TypeScript/Java,
`snake_case` in Python/Rust/Ruby).

**`Relationship`** -- one tuple: a resource, a relation, and a subject, with two optional
extras. Nine fields, confirmed field-by-field identical across all seven clients:

| Field | Meaning |
|---|---|
| resource type / resource id | what's being related to |
| resource relation | the relation on the resource (`viewer`, `editor`, ...) |
| subject type / subject id | who or what holds the relation |
| subject relation | for subjects that are themselves a relation on another object (`group:eng#member`) |
| caveat name / caveat context | optional: makes the relationship conditionally true, evaluated at check time |
| expiration | optional: the relationship stops existing automatically at a point in time |

**`Filter`** -- the same shape minus the subject relation's pairing constraints, used to select
relationships for reads/deletes/watches: resource type, resource id (or an id prefix), relation,
subject type, subject id, subject relation. Go additionally exposes the raw v1 proto filter as
an escape hatch (`V1Filter`) for filter shapes the idiomatic type doesn't cover yet.

**`Transaction`** -- a batch of writes, submitted atomically. Every language exposes the same
core operations: `Create` (fails if the relationship already exists), `Touch` (idempotent
upsert -- the one to reach for by default), `Delete`, and two write preconditions,
`MustMatch`/`MustNotMatch` (fail the whole write if a filter does/doesn't match existing data,
useful for optimistic-concurrency-style guards). TypeScript's `Transaction` additionally exposes
`withMetadata` for attaching tracing metadata to the write; the other six don't have an
equivalent yet.

## Consistency helpers

Six helpers, same six semantics, same names modulo casing, in every language:

| Concept | Go | Python / Rust / Ruby | TypeScript / Java | Use for |
|---|---|---|---|---|
| Newest committed data, bypass cache | `Full()` | `full()` | `full()` | One-off admin/debug/audit reads. Not the request path at scale -- it bypasses the cache. |
| Fastest available replica, may be stale | `MinLatency()` | `min_latency()` | `minLatency()` | Default choice for reads that tolerate a little staleness -- most checks. |
| At least as fresh as a given revision | `AtLeast(rev)` | `at_least(rev)` | `atLeast(rev)` | Read-your-writes: pass the token a prior write returned, get a result that reflects at least that write, without paying `Full`'s cache-bypass cost. |
| At least `rev`, or `Full` if no revision given | `AtLeastOrFull(rev)` | `at_least_or_full(rev)` | `atLeastOrFull(rev)` | Same as `AtLeast`, but safe to call before you have a token yet (first request in a session). |
| At least `rev`, or `MinLatency` if no revision given | `AtLeastOrMinLatency(rev)` | `at_least_or_min_latency(rev)` | `atLeastOrMinLatency(rev)` | Same fallback shape, but falls back to the cheaper default instead of the expensive one -- prefer this over `AtLeastOrFull` unless you specifically need the strict fallback. **A read-after-write check with no revision in hand always needs the strict fallback** -- use `AtLeastOrFull` there instead, never this one (`openfga-to-spicedb/references/code-mapping.md`'s "Consistency" section has the live-verified failure this causes). |
| Pinned to an exact revision | `Snapshot(rev)` | `snapshot(rev)` | `snapshot(rev)` | Paginating one result set within a short window. Don't persist the token -- it expires once the datastore garbage-collects that revision. |

Live-verified (against this pinned commit): TypeScript's `minLatency()` and
`atLeastOrFull('')` both construct the expected `Consistency` proto shape, confirming the names
above are real, not aspirational.

Writes (`WriteRelationships`, `DeleteRelationships`, `WriteSchema`) are always fully
consistent -- there's no consistency parameter to get wrong on the write path. The choice above
only applies to reads: `CheckPermission`, `CheckBulkPermissions`, `LookupResources`,
`LookupSubjects`, `ReadRelationships` all default to `MinLatency` if you don't specify.

## Iteration over streaming results

`LookupResources`, `LookupSubjects`, `ReadRelationships`, and `Watch`/`Updates` are all
server-streaming RPCs under the hood. How much of that streaming nature survives into the
idiomatic client differs by language:

| Language | Idiomatic shape | Streams lazily? |
|---|---|---|
| Go | `iter.Seq2[T, error]` | Yes |
| Python | `async def ... yield` (async generator) | Yes |
| TypeScript | `async *method()` (async generator) | Yes |
| C# | `IAsyncEnumerable<T>` via `yield return` | Yes |
| Ruby | `Enumerator.new` with a lazy yielder | Yes |
| Java | `java.util.stream.Stream` over a lazy paging `Iterator` | Yes |
| Rust | `Vec<T>` -- fully buffered, not a stream | **No** |

Six of the seven stream lazily -- you can `break`/short-circuit early and the client stops
pulling more pages. Rust is the exception: `read_relationships`, `lookup_resources`,
`lookup_subjects`, `export_relationships`, and `updates` all fully drain the underlying gRPC
stream into a `Vec` before returning. For a large result set, that means Rust holds the entire
result in memory and gives you nothing until it's all arrived -- a materially different
performance and memory profile than the other six languages for the exact same call.

### Trust the code, not the docs

One of these clients' own documentation disagrees with what it actually does. Recorded here
specifically because "the client's own comments say X" is not the same claim as "the client's
compiled signature does X," and this project has been burned by exactly that gap before:

- **Rust's own doc comments claim streaming that its code doesn't provide.** `client.rs:44` and
  `:305` say, verbatim, `` Returns `impl Stream<Item = Result<T, SpiceDBError>>` ``, and five
  more doc comments elsewhere in the same file (lines 298, 396, 460, 822, 882) describe a
  "stream" in prose. The actual signatures (table above) return `Vec<T>`. Live-verified:
  `let rels: Vec<Relationship> = client.read_relationships(...).await?;` compiles and runs as a
  plain `Vec` assignment -- no `Stream`, `.next()`, or `poll_next()` involved anywhere.

Relatedly, and not just a Go quirk: **Go is the only one of the seven clients with neither a
typed error hierarchy nor built-in retry.** It has exactly three sentinel errors, for input
validation only (`ErrInvalidResource`, `ErrInvalidRelation`, `ErrInvalidSubject`); every gRPC
failure comes back as a plain wrapped error (`fmt.Errorf("spicedb: ...: %w", err)`), not a typed
one, and there is zero retry/backoff logic anywhere in `client/*.go` or `consistency/*.go`, nor a
retry dependency in `go.mod`. Live-verified: `CheckOne` against an unreachable port returned in
24.4ms -- a single immediate failure, not the multi-second delay a real retry-with-backoff
implementation would produce. Python, TypeScript, C#, Java, Rust, and Ruby all have both a typed
error hierarchy *and* built-in retry (3-5 attempts, exponential backoff, confirmed live for
Python: an unreachable port raised a typed `UnavailableError` after 0.705s, matching its
`0.1*2^0 + 0.1*2^1 + 0.1*2^2` backoff formula exactly). If you're writing Go first and porting
patterns to another language (or vice versa), don't assume Go's bare-error, no-retry behavior
generalizes -- it's the outlier, not the baseline.

---

## Product-level limits of `LookupResources`

These three shape any UI or product built on `LookupResources`, and none of them are specific to
one language -- they're properties of the SpiceDB API itself. All three were verified live
against `spicedb serve-testing` v1.56.0 for this reference (setup in `references/installation.md`);
reproduce them yourself if either version has moved since.

### 1. `LookupResources` never returns a total count

Confirmed two ways. Structurally: `LookupResourcesResponse` has exactly five fields --
`looked_up_at`, `resource_object_id`, `permissionship`, `partial_caveat_info`,
`after_result_cursor` -- across every language's generated proto, with no count field anywhere
on this message or any other message the call can return. Its own doc comment says as much:
"`LookupResourcesResponse` contains a single matching resource object ID for the requested
object type, permission, and subject" -- one resource per message, never a summary.

Empirically: a live call against a subject with 5 accessible resources, with `--json` output,
showed each result as an independent object (`resourceObjectId`, `permissionship`,
`lookedUpAt`, `afterResultCursor`) and nothing resembling a count anywhere in the stream.

**A "Showing 1-20 of 150" UI is not implementable against this API as written.** That's a real
product constraint, not a client gap to work around -- if you need a count, get it a different
way (a count materialized and updated on write, or bound a `CheckBulkPermissions` pass over a
capped candidate set) rather than expecting `LookupResources` to hand you one.

### 2. Duplicate resource IDs are possible -- not only across cursor pages

If a resource is reachable through **more than one relation** feeding the same permission (e.g.
`permission view = direct_viewer + group_viewer`, and a subject holds both), `LookupResources`
does not deduplicate it by resource ID. This is stronger than "paginate carefully" -- but be
precise about how much stronger, because the measurement below is what supports it and it is
easy to overstate: **duplicates are overwhelmingly a cross-page phenomenon.** Of 241 observed,
**240 were split across pages and exactly one landed twice on the same page.** A single,
non-paginated call *can* return a duplicate, and one did, but it is rare.

**That rarity is the trap, not a reason to relax.** A quick probe -- one small schema, a
handful of relationships, one uncursored call -- will almost certainly come back with distinct
results and look like proof that deduplication is unnecessary. Confirmed while writing this:
a resource reachable by three independent paths (a direct relation, a group userset, and a
parent arrow) came back exactly once from a single call on v1.56.0. That is the expected
outcome at that scale and says nothing about the 240.

Live-verified, small case: a subject with 5 accessible resources, 2 of them reachable through
two different relations, in **one single call** (page-limit far above the 5-item total, no
cursor involved at all):

```
carol-doc1
carol-doc2
carol-doc3
carol-doc4
carol-doc5
carol-doc4
carol-doc5
```

`carol-doc4` and `carol-doc5` -- the two dual-path resources -- each appear twice, in one
continuous, unpaginated response stream.

Live-verified, larger case: 12,000 resources reachable through one relation, 240 of them (every
50th) *also* reachable through a second relation via group membership, plus one more isolated
dual-path resource. Walking the full result set with the raw `OptionalLimit=1000` /
`OptionalCursor` pagination the wire protocol actually uses (not an idiomatic wrapper -- several
of those hide cursoring entirely; see below):

| | |
|---|---|
| Calls needed | 13 (twelve returned exactly 1,000 items; the 13th returned the remaining 242) |
| Raw items emitted | 12,242 |
| Unique resource IDs | 12,001 |
| Resource IDs emitted more than once | 241 |
| ...of which, both occurrences on the same page | 1 |
| ...of which, occurrences split across two different pages | 240 |

240 of 241 duplicates were genuinely **cross-page**: the query appears to exhaust one relation's
contribution to the permission before moving on to the next, so a resource whose second path
only gets evaluated later lands in a much later page than its first occurrence -- often many
pages later. Client-side deduplication by resource ID is mandatory whenever more than one
relation can grant the same permission, regardless of whether you're paginating with a cursor or
reading a single response. The proto's own field comments (on `optional_limit`,
`optional_cursor`, and `LookupResourcesResponse` itself) say nothing about deduplication
guarantees either way -- this isn't documented, it has to be verified, which is what the numbers
above are.

One more reason this is easy to miss in testing: **not every idiomatic client exposes cursoring
at all.** Go's `LookupResources(ctx, cs, resourceType, permission, subjectType, subjectID)` and
Ruby's `lookup_resources(consistency, resource_type, permission, subject_type, subject_id)` take
no limit or cursor parameter -- the lazy iterator handles paging internally and you never see a
page boundary. If your test dataset is small enough to fit in one underlying page, you can
exercise these clients and never observe cross-page duplication at all, even though the
underlying multi-path duplication (the `carol-doc4`/`carol-doc5` case above) is already present
regardless of dataset size.

### 3. Page limit hard-capped at 1,000; treat ~10,000 total as a practical ceiling

The 1,000-per-call cap is a hard, confirmed limit, not a soft one. Confirmed three ways at this
pinned commit: the server's own startup config (`MaxLookupResourcesLimit: 1000`), its CLI help
(`--max-lookup-resources-limit uint32 ... maximum number of resources that can be looked up in a
single request (default 1000)`), and live at the wire level --

```
OptionalLimit=1000: accepted, first item received without error
OptionalLimit=1001: stream.Recv() failed: rpc error: code = InvalidArgument
  desc = provided limit 1001 is greater than maximum allowed of 1000
```

-- a request for one more than the cap is rejected outright, not silently clamped.

The "~10,000 resources per subject" figure is softer, and worth being precise about what it is
and isn't: it is **not** a server-enforced limit anywhere in SpiceDB's source (no config flag,
no hardcoded constant tied to "10,000" or "per subject" exists). In this reference's live test
against a local, single-node, in-memory `serve-testing` instance, paging all the way through
12,242 raw results (13 calls) showed no sharp technical cliff -- every call completed in well
under 150ms, with no growth trend as the cursor advanced. Treat "~10,000" as a **practical**,
not a hard, ceiling: at the 1,000-per-call cap, reaching 10,000 total results already costs 10
sequential round trips, each one needing to extend the client-side dedup set from constraint 2
above, and each one paying real network and dispatch latency that a local in-memory test doesn't
reproduce. Past roughly that scale, prefer a different strategy -- materialize/cache the result
set, or narrow the query -- over trying to page through `LookupResources` to completion. If you
need the current, exact number for your own deployment and scale, re-run the pagination-timing
check in `references/installation.md`'s live-server setup against your own server rather than
trusting a number written down here.
