# Source Adapter: OpenFGA

This file implements the seam `differential-harness.md` defines and declines to fill in:
"a per-source `source-adapter.md` (the seam a pack implements -- given a question in this
file's vocabulary, ask the source system and translate its answer back into that same
vocabulary)." Everything about *what* a `Question`, an `Outcome`, and a `DifferentialRecord`
are -- the five-state vocabulary, the `Diff` rule ordering, the safety property -- belongs to
that file. **This file conforms to that contract and does not redefine any of it.** Where a
claim below depends on `differential-harness.md`'s own record shape or `Diff` rule, it is
cited by name, not restated.

Likewise, `code-mapping.md` already carries the call-by-call mapping from an OpenFGA SDK
method to a SpiceDB client method, the relation-split obligation, and the identifier
obligation -- the exact rules a converted call site follows. **This file cites those rules
rather than re-deriving them**, and adds only what neither file supplies: which OpenFGA call
answers which of the harness's comparable operations, how to translate an OpenFGA answer into
the five-state `Outcome` vocabulary, and -- the detail most likely to be got wrong -- which
*direction* `migration-map.json`'s naming and ID maps run for each of the two harness
capabilities that touch this adapter.

**Consumed by `/spicedb-dev:migrate-verify`**, the command that emits a working harness into a
customer's project: this file states the adapter shape that command emits from, the same way
`code-mapping.md` states the mapping `/spicedb-dev:migrate-code` emits from. Both commands are
shipped.

**What is OpenFGA-specific here, and what a future pack (OSO Cloud, next in this plugin's
source registry) copies unchanged:** the two-entry-point shape in "One seam, two entry
points" below, the operation-comparability table, and the five-state mapping's *structure*
(which states a source can carry, and why) are the reusable shape -- a second pack fills in
its own call names, its own error-vs-denial signal, and its own answer to "can this source
produce a fourth state." The forward/reverse lookup-table mechanics, the OpenFGA call
transcripts, and the specific claim that this source's check API is strictly boolean are
OpenFGA's own answers to that shape, not part of it.

**Versions used for every live verification below:** `openfga/openfga:latest` (reports
`v1.18.3`, build `81c6202`), `@openfga/sdk` `0.9.6`, `fga` CLI `v0.7.20` -- identical to
`code-mapping.md`'s pinned versions, so every claim here composes with that file's without a
version mismatch. `spicedb serve-testing` `v1.56.0` (floor: v1.52.0), `zed` `v0.31.1`, started
with `--endpoint`/`--token` passed explicitly -- `serve-testing` takes no
`--grpc-preshared-key` in v1.56.0, this pack's existing convention. Every transcript below is
a real call against a real container; none is illustrative.

## One seam, two entry points

`differential-harness.md`'s own one-line description of this seam -- "ask the source system
and translate its answer back into that same vocabulary" -- describes the general shape.
Two of the four capabilities it defines use that shape differently enough that this adapter
exposes it as **two entry points sharing one pair of lookup tables**, not one function:

- **`observe(native_request, native_response_or_error) -> (Question, Outcome)`** -- the
  **forward** direction: translate an OpenFGA call site's own request and response *into*
  `differential-harness.md`'s vocabulary. This is the only entry point **Dual-run** uses, and
  it never places a call to OpenFGA -- Dual-run's safety property forbids that ("`source.outcome`
  is populated by **observing** the production request's own already-computed decision, not
  by issuing a second call to the source system," `differential-harness.md`, "The record
  shape"). It only translates a call that already happened, so it adds no read load and no
  risk to the source system -- the property that section states as the whole reason
  `source.outcome` is populated this way.
- **`ask(question: Question) -> Outcome`** -- the **reverse** direction: translate a stored
  `Question` (always phrased in SpiceDB vocabulary, per that record's own field comment: "the
  source adapter's job is mapping this onto whatever the source calls the same object") *back
  into* an OpenFGA call, issue it, and translate the response. This is the literal "ask the
  source system" operation the contract names in general terms, and it is the only entry
  point **Replay**'s parity mode uses ("Re-ask both sides fresh -- functionally a batched
  dual-run over a captured corpus of questions," `differential-harness.md`, "Replay"). Because
  this is the one place any call reaches OpenFGA, it is also the one place the harness adds load
  to a system that is still authoritative for real users: `differential-harness.md`'s safety
  property (point 1) scopes it to offline, operator-initiated batches under an explicit rate
  limit or against a read replica, never a request path -- an obligation on how a caller drives
  `ask()`, cited here rather than restated because it is the contract's, not this adapter's.

**Regression replay never calls this adapter at all.** It "re-ask[s] only SpiceDB, and
diff[s] the new answer against the *recorded* target outcome from capture time -- not against
a fresh source call" (`differential-harness.md`, "Replay") -- there is nothing for an OpenFGA
adapter to do on that path, and a pack implementation that fires an OpenFGA call during
regression replay is not implementing regression replay.

Both entry points are built from the same two lookup tables, run in opposite directions:

| | `observe()` (Dual-run) | `ask()` (Replay, parity mode) |
|---|---|---|
| Direction | OpenFGA-native names/ids &rarr; SpiceDB vocabulary | SpiceDB vocabulary &rarr; OpenFGA-native names/ids |
| Table used | `migration-map.json`'s maps, applied forward -- **identical to `code-mapping.md`'s own conversion rules** | The same maps, inverted once at adapter start-up |
| Places a call to OpenFGA? | No -- translates an already-observed call | Yes -- this is the one place this adapter calls OpenFGA |
| Consumed by | Dual-run only | Replay's parity mode only |

## The direction most likely to be got wrong

`differential-harness.md`'s `Question.resource`/`Question.subject` fields are, by that file's
own comment, "SpiceDB-side vocabulary" -- every stored `Question` names a SpiceDB type,
permission, and (if encoded) an encoded id, never the source's own names. That single design
choice is why the two entry points above run in opposite directions, and why getting the
direction backwards produces a coherent-looking answer that is simply wrong.

**`observe()`'s forward direction is not a new rule -- it is `code-mapping.md`'s own
conversion rules, applied to the fields of an already-executed call instead of to a rewritten
call site:**

| `Question` field, from an observed OpenFGA `check`/`batchCheck` request | Rule (cited, not restated) |
|---|---|
| `resource.type` / `subject.type` (non-userset) | `migration-map.json`'s `types` map, forward |
| `resource` id / non-userset `subject` id | `code-mapping.md`'s identifier obligation -- `encode(source_type, source_id)` |
| `permission` | `code-mapping.md`'s relation-split obligation, **check-path row**: `relation_splits[T][R].permission`, falling back to `permissions[T][R]` when `R` never split |
| `subject`'s relation, for a userset subject (`T#R`) | The relation-split obligation's **subject-side row**: `permissions[T][R]` -- **never** `relation_splits[T][R].relation`, on the subject side same as the resource side |
| `context` | Passed through, canonicalized the way `test-mapping.md` canonicalizes `check.context` (`json.dumps(context, sort_keys=True, separators=(",", ":"))`) -- see "Question.context" below |
| `origin` | Not a lookup -- the observed call shape itself: `CHECK` for `check` (and for each of `listRelations`' decomposed questions), `BATCH_CHECK` per `batchCheck` item, `LIST_SAMPLED` for an ID sampled out of a `listObjects`/`listUsers` result. Required, never defaulted -- see the `listObjects`/`listUsers` section for why it cannot be recovered later |

**What `observe()` does when the map has no entry for the observed relation.** Both directions
above assume the name being translated is *in* `migration-map.json`. A live OpenFGA store can
serve a relation that isn't: a Class C relation with no conversion target at all
(`findings-report.md`'s own category for "call sites or endpoints with no conversion target"), or
a relation added to the source's authorization model after the map was generated -- which the
cutover playbook makes likely rather than exotic, since it runs shadow-read "for a full cycle of
how the product is actually used" (`cutover-strategies.md` step 7's bar, cited by
`differential-harness.md`'s "Sampling and volume") while the source system stays authoritative
and under active development.

`differential-harness.md`'s "Untranslatable questions" section is the rule, cited not restated,
and this adapter follows it without softening: `observe()` returns an explicit *untranslatable*
signal instead of a `(Question, Outcome)` pair, the harness tallies it by `(source type, source
relation)`, and the count is part of that file's health gate. **Never** reconstruct the SpiceDB
name -- not by assuming the source relation's own name carries over, not by appending or
stripping a split suffix -- which is the same "read it from the map, never construct it"
discipline this file already states for the reverse index below, applied to the case where the
map simply has nothing to read. **Never** emit a `DifferentialRecord` with an invented
`question.permission`: a fabricated name either resolves to nothing (a `FailedPrecondition`
recorded as `TARGET_ERROR`, mislabelling a mapping gap as a target fault) or, worse, resolves to
some *real* permission and produces a confident comparison of two different questions.

**`ask()`'s reverse direction inverts the same tables** -- and this is the half a naive
implementation gets wrong, because it is tempting to reconstruct a source name instead of
looking one up:

| OpenFGA call field, built from a stored `Question` | Rule |
|---|---|
| Object/subject type | Invert `types` (source key &rarr; SpiceDB value, so build `{spicedb: source}` once) |
| Object id / subject id | `decode(source_type, spicedb_id)` -- `data-mapping.md`'s codec contract states `decode` takes the **source** type as its key, which is exactly what the inverted `types` lookup just produced |
| Relation name | Find the source relation `R` in `relation_splits[T]` whose `.permission` equals the `Question`'s permission, else the source relation in `permissions[T]` whose value equals it. **Never `relation_splits[T][R].relation`** |

**Building the reverse index is well-defined because the forward map is guaranteed
injective.** `findings-report.md`'s `migration-map.json` section states the invariant this
inversion depends on: "the mapping must be injective within each namespace -- globally for
`types`, and within one source type for `permissions` **and** `relation_splits` together...
Two source names sharing one SpiceDB name silently merges them." Injectivity is exactly
"invertible" restated -- build `{spicedb_name: source_name}` once, at adapter start-up, from
`permissions[T]` and `relation_splits[T][*].permission` together (never `.relation` -- see
below for why that field never appears in a reverse lookup at all), and every SpiceDB
permission name in a stored `Question` resolves to exactly one source relation name.

**Do not assume the source key and its `.permission` value are the same string.** In this
file's own worked example below they happen to coincide (`viewer` splits to
`{"relation": "viewer__direct", "permission": "viewer"}` -- the permission keeps the source's
original name unchanged). That is the common case, not a guarantee: whenever
`naming-normalization.md`'s reduction actually changes a name (an illegal character, a
reserved word, a length violation), the source key and the SpiceDB permission value diverge,
and a reverse lookup that reconstructs the source name by guessing (stripping a suffix,
assuming identity) breaks exactly where normalization did its job. Read the reverse index from
`migration-map.json`'s own entries, always -- the same "never construct it, read it" discipline
`code-mapping.md` states for the forward direction ("Read the name out of the map; never
construct it by appending `__direct`") applies in reverse, for the same reason.

**Why `relation_splits[T][R].relation` -- the write-target name -- never appears anywhere in
this adapter.** Dual-run "makes exactly one call per sampled question -- `CheckPermission`
(or the bulk/lookup equivalent...) -- and never a write" (`differential-harness.md`,
"Dual-run"), and Replay's parity mode re-asks the identical check/lookup shape, never a
relationship read or write. Every comparable operation this file covers below resolves a
name through `.permission`; the write-target name that a `Transaction.create()`/`.touch()`
or a `ReadRelationships` filter would need has no consumer in the harness at all. A source
adapter that reaches for `.relation` anywhere in its check/lookup path is reaching for the
wrong half of the split.

### What getting it wrong looks like, live

Everything above is abstract until it produces a wrong verdict on a correct migration.
Reusing `code-mapping.md`'s own running example (`document`/`folder`/`group`, the split
`document.viewer` &rarr; `{"relation": "viewer__direct", "permission": "viewer"}`), seeded
identically on both a real `openfga/openfga` store and a real `spicedb serve-testing`
instance: `folder:1` owned by `user:bob`, `document:1`'s `parent` is `folder:1`, and `bob` has
no *direct* grant on `document:1` at all -- he is a viewer only through the `parent->owner`
arrow. `Question`: "is `user:bob` a viewer of `document:1`?"

```
$ curl -s -X POST http://localhost:28082/stores/$STORE/check \
    -d '{"tuple_key":{"user":"user:bob","relation":"viewer","object":"document:1"}}'
{"allowed":true,"resolution":""}
```

Correct `ask()` -- reads the target's permission through `relation_splits.document.viewer.permission`:

```
$ zed permission check document:1 viewer user:bob --endpoint localhost:50897 --token task3demo --insecure
true
```

Both sides `ALLOWED` -&gt; `AGREE`, exactly as they should for a faithful conversion. Now the
same `Question`, with the reverse lookup wrongly reaching for `.relation` instead of
`.permission` (the bug: treating the SpiceDB-side name as if the write-target and the
check-target were interchangeable):

```
$ zed permission check document:1 viewer__direct user:bob --endpoint localhost:50897 --token task3demo --insecure
false
```

**One line of code changed which field of the same map it read, and the record flips from
`AGREE` to a candidate `DISAGREE`** -- on a migration that converted `bob`'s access
correctly. `viewer__direct` only holds directly-granted subjects; it has no visibility into
the `parent->owner` arrow the permission `viewer` computes over, so checking the relation
instead of the permission silently narrows the answer -- the exact "silent, wrong answer"
failure mode `code-mapping.md`'s "How it fails, live" section demonstrates for a converted
call site, reproduced here for a harness's own call site instead. This is what the brief for
this file calls "worse than no harness at all": a harness that reports this as a genuine
authorization regression will send a real, correctly-converted resource type back for
rework, and every future run against the same `(resource type, permission)` pair repeats the
false alarm until someone notices the adapter, not the conversion, is wrong.

**A second, sharper failure mode: hardcoding the suffix instead of reading it from the map at
all.** The demonstration above got the right *field* wrong (`.relation` instead of
`.permission`) but the right *string* -- `viewer__direct` is a real relation on this schema,
so the mistake produces a silent, wrong `false`. A project that configured a different split
suffix at the gate (`/spicedb-dev:migrate` step 5, row 4, per `code-mapping.md`'s own citation)
-- `__base` instead of the pack's `__direct` default -- and an `ask()` implementation that
constructs `f"{permission}__direct"` instead of reading `relation_splits[T][R].permission`
from `migration-map.json`, reaches for a relation name that was never written to the schema at
all. Reproduced live, the identical `document`/`folder` schema redeployed with `__base` in
place of `__direct` everywhere:

```
$ zed permission check document:1 viewer__direct user:bob --endpoint localhost:50897 --token task3demobase --insecure
{"level":"error","error":"rpc error: code = FailedPrecondition desc = relation/permission `viewer__direct` not found under definition `document`","message":"terminated with errors"}
```

**This is louder, not quieter, than the first demonstration** -- a hardcoded suffix fails
every check it touches with a hard `FailedPrecondition`, the instant the harness runs against
a project that didn't use the default, rather than narrowing silently. It is a better failure
mode than the first only in the sense that it is impossible to miss; it is not a safe
fallback, and it still means the harness cannot compare a single question until the adapter is
fixed. Both demonstrations are instances of the same rule, stated once above and worth
repeating at the site of the evidence: read the name out of `migration-map.json`, every time,
for both the field chosen and the string itself -- never assume either.

## Which operations are comparable at all

| OpenFGA operation | Comparable? | Mode |
|---|---|---|
| `check` | Yes | Exact, one `Question` &rarr; one `DifferentialRecord` |
| `batchCheck` / `clientBatchCheck` | Yes | Exact, per item -- see correlation below |
| `listRelations` | Yes | Decomposes into N `check`-shaped `Question`s -- no new comparison mode, see below |
| `listObjects` / `streamedListObjects` | As a set | Advisory, sampled -- see below |
| `listUsers` | As a set | Advisory, sampled -- see below |
| `expand` | No | Structural mismatch -- no comparison offered |
| `contextual_tuples`, `authorization_model_id` pinning, store CRUD, AuthZEN, Permissions Index, `readAssertions`/`writeAssertions` | No | No SpiceDB target at all -- nothing to compare |

### `check` / `batchCheck`

**`check`** maps directly onto one `Question`/`Outcome` pair. `observe()` reads the
OpenFGA-native `{user, relation, object, context?}` request through the forward table above;
`ask()` builds the identical shape from a stored `Question` through the reverse table.
`CheckPermissionRequest`'s `Context` channel and OpenFGA's own per-check `context` field are
the same kind of thing on both sides -- request-time values for a condition/caveat expression,
not extra relationships (`code-mapping.md`'s own citation: "`CheckPermissionRequest.Context`
is a *different* channel... not where a stored tuple's own condition lives"). Verified live,
using the exact channel a `Question.context` value would populate: a tuple written with a
condition bound at write time (`expires_at`) and the remaining parameter
(`current_time`) supplied only at check time --

```
$ curl -s -X POST .../check -d '{"tuple_key": {...}, "context": {"current_time":"2026-08-15T00:00:00Z"}}'
{"allowed":true,"resolution":""}
```

confirms OpenFGA's request-time `context` is the right target for `Question.context` on this
adapter's forward and reverse paths alike.

**`batchCheck`** compares cleanly per item, but the two systems correlate results
differently, and `code-mapping.md`'s "`batchCheck` ordering" section is the authority on the
mismatch -- cited, not restated, here. What that section does not cover, because it is
specific to this adapter, is what happens to a **per-item error** inside a batch, which
matters directly for the `Outcome` mapping below. Verified live, raw `/batch-check`, one
allowed item, one denied item, one item whose condition is missing required context:

```
$ curl -s -X POST .../batch-check -d '{"checks": [
    {"tuple_key": {...direct grant...}, "correlation_id":"allowed-1"},
    {"tuple_key": {...no relationship...}, "correlation_id":"denied-1"},
    {"tuple_key": {...conditioned, no context supplied...}, "correlation_id":"errored-1"}
  ]}'
{"result":{
  "allowed-1":{"allowed":true},
  "denied-1":{"allowed":false},
  "errored-1":{"error":{"input_error":"validation_error","message":"failed to evaluate relationship condition: 'non_expired' - tuple 'document:conditional-doc#conditional_viewer@user:mallory' is missing context parameters '[current_time]'"}}
}}
```

**Each item's result is one of exactly two shapes, `{"allowed": bool}` or `{"error": {...}}`
-- the wire format itself keeps a per-item denial and a per-item failure apart, the same way
`differential-harness.md` and `code-mapping.md` already showed SpiceDB's own wire protocol
does for a whole-call denial vs. failure.** A batch-check consumer built against this adapter
maps each item through this same shape distinction (`allowed` present &rarr; `ALLOWED`/`DENIED`
by its boolean value, `error` present &rarr; `ERRORED`, `detail` = the error's `message`) --
never by defaulting a missing `allowed` key to `false`.

### `listRelations` -- decomposes into N `check`s, not a new comparison mode

**Ruling: comparable, and already covered.** `listRelations({user, object, relations: [R1..Rn]})`
answers the same question `check` does, N times, against the same resource and subject --
"does `Ri` hold" for each `Ri` in the list. Decompose it into N ordinary `check`-shaped
`Question`s, one per relation, each resolving its `permission` through the exact same
forward/reverse table `check` already uses (`relation_splits[T][Ri].permission`, falling back
to `permissions[T][Ri]`). No new record shape, no new `Outcome` rule, and no new entry point
is needed -- every decomposed `Question` flows through `check`'s own `observe()`/`ask()` and
the five-state mapping below unchanged. `ask()`'s reverse path needs nothing special either:
a stored `Question` is already single-check-shaped, so Replay's parity mode re-asks each one
as an ordinary `check` and never reconstructs a `listRelations` call.

**Where this adapter's `observe()` needs care, because it is watching a call shape with its
own documented error behavior.** `code-mapping.md`'s "`listRelations` error convention"
section already establishes, live, that `listRelations`' per-item error handling is SDK-version-
dependent -- cited, not re-verified here, but its consequence for *this* adapter's
observation hook is new and belongs here:

- **Pre-0.2.8 `@openfga/sdk`**: a per-relation failure is swallowed to `allowed: false`
  *inside the SDK's own `listRelations` implementation*, before the call ever returns.
  `observe()` wrapping `listRelations` itself (rather than each relation's underlying `check`)
  has nothing to recover -- the swallow already happened below its observation point, the same
  limitation `differential-harness.md`'s record-shape section states in general terms, except
  here the swallowing party is the idiomatic SDK, not application code. This is a *stronger*
  instance of the app-level swallow hazard documented under `ERRORED` below, not a new one.
- **Current `@openfga/sdk` (0.9.6)**: the whole call throws on the *first* per-item error,
  discarding every result, including relations that would otherwise have answered cleanly.
  An `observe()` hook watching this call shape sees one of exactly two outcomes for the
  *entire* batch of N decomposed `Question`s: all N get real `ALLOWED`/`DENIED` values (the
  call resolved), or all N must be recorded `ERRORED` (the call threw) -- there is no
  partial-success data to decompose a subset of them from, even though N-1 of the N relations
  may well have "would have" resolved cleanly. Recording only the one relation that actually
  triggered the throw as `ERRORED`, and the rest as if they'd been asked and denied, would
  invent data the call never returned.

### `listObjects` / `listUsers` -- compare as sets, never as ordered lists, and prefer sampling over full-set diff

`differential-harness.md`'s own position on enumeration-shaped operations is narrow, and it
is the position this adapter follows, not a looser one: "sample check-shaped assertions *out
of* a list result... rather than diffing two full sets, treat that as a distinct,
lower-confidence comparison mode, and never feed its records into snapshot-to-assertions"
("What is not comparable at all"). Concretely: fire `listObjects`/`listUsers` on the source
side (observed, same as `check`) and `LookupResources`/`LookupSubjects` on the target side,
draw a small sample of IDs out of one side's result, and re-ask each sampled ID as an
ordinary `check`-shaped `Question` against the *other* side -- the resulting records are
ordinary `DifferentialRecord`s from `check`'s own comparison path, not a new record shape.
This reuses `check`'s own `Outcome` mapping entirely; nothing new is defined here for it.

**One field does differ, and it is the only thing marking these records as the
lower-confidence mode they are: `question.origin` is `LIST_SAMPLED`, never `CHECK`.** A sampled
ID re-asked as a check produces a `Question` identical in every other field to one observed at a
real `check` call site, so the origin has to be stamped where the sample is drawn -- it cannot be
recovered later. See "Never feed a list-derived record into snapshot-to-assertions," below.

The **name mapping** for the underlying `listObjects`/`LookupResources` call (and the
`listUsers`/`LookupSubjects` call) is the permission side of the same table above --
`code-mapping.md`'s own row: "The permission argument is the split's `permission`, never its
`relation`." The id codec applies to the subject id going in (`encode`) and to every
resource/subject id coming out of either side (`decode`), exactly as `check`'s does.

**If a harness build samples IDs *from* a list result at all -- which is the only sanctioned
use of a list call in this contract -- the set each side's result is drawn from must be
deduplicated before sampling, or the sample is drawn from an inflated, unrepresentative
population.** `spicedb-client-integration/references/core-concepts.md`'s "Product-level
limits of `LookupResources`" is the authority for the target side, cited rather than
re-verified here: a resource reachable through more than one relation feeding the same
permission is returned more than once, "even in a single, non-paginated call," and the same
reference's larger measurement (12,242 raw results, 12,001 unique, 241 duplicated, 240 of
them split across two different pages) shows this is not confined to a cursor boundary.

**The source side does not exhibit the identical failure -- verified live, freshly, since
`core-concepts.md`'s finding is target-only.** The same dual-path shape (`document:dual1`
reachable through two different relations, both feeding a `viewer` permission via `or`),
seeded on OpenFGA and queried in one call:

```
$ curl -s -X POST .../list-objects -d '{"type":"document","relation":"viewer","user":"user:carol"}'
{"objects":["document:dual1","document:solo1"]}
```

`document:dual1` -- reachable through both `direct_viewer` and `group_viewer` -- appears
exactly once, not twice. **Treat this as one observation, not a documented guarantee**:
nothing in OpenFGA's API reference commits to deduplicating `listObjects`, the same way
nothing in SpiceDB's proto commits to *not* deduplicating `LookupResources` --
`core-concepts.md` makes that same point about the target side ("this isn't documented, it
has to be verified"). The asymmetry is real as measured, but a harness should still
deduplicate both sides defensively rather than assume the OpenFGA side never needs it.

**The asymmetry is invisible at a small fixture's scale, and only shows up once the result
set is large enough to force real pagination -- verified live, at scale, on both sides.** A
2-item or 5-item dual-path fixture (as above) is too small to ever cross SpiceDB's
1,000-per-call page boundary, so a small fixture cannot exercise the cross-page duplication
`core-concepts.md` documents -- a customer who verifies this asymmetry only with a toy fixture
will observe no duplicates on *either* side and conclude, wrongly, that neither system
duplicates. Reproduced at scale: 1,200 documents, each granted `direct` to one subject, 48 of
them (every 25th) *also* granted `indirect` to the same subject, both relations feeding one
`viewer` permission via `+` -- the same dual-path shape as above, just past the page boundary.
Paging SpiceDB's `LookupResources` at the wire level (`OptionalLimit=1000`, two calls):

```
page 1: 1000 items
page 2: 248 items
raw items: 1248, unique: 1200, duplicated ids: 48
duplicate occurrences: same-page=7, cross-page=41
```

The identical shape against OpenFGA -- 1,200 documents, 50 of them (every 20th, among the
first 1,000) dual-path, using `streamed-list-objects` so the result is not truncated by the
non-streaming endpoint's own 1,000-result cap (below) -- returns every object exactly once:

```
raw items: 1200, unique: 1200, duplicated ids: 0
```

**A second, related, and freshly-discovered asymmetry: OpenFGA's *non-streaming* `listObjects`
truncates silently past 1,000 results, with no cursor and no truncation flag in the
response.** `--listObjects-max-results` (server default `1000`, confirmed from the container's
own `--help` text: "the maximum results to return in non-streaming ListObjects API responses")
caps the call outright -- the same 1,200-document store above, queried through the
non-streaming `/list-objects` endpoint rather than the streaming one, returns exactly `1000`
objects with **no** field anywhere in the response (`{"objects": [...]}` is the entire body)
indicating 200 were dropped. This is the mirror image of `core-concepts.md`'s point 1 for
`LookupResources` ("never returns a total count") -- except SpiceDB's cap is at least visible
as a cursor a caller can choose to keep paging past, where OpenFGA's non-streaming cap gives no
signal to page against at all. A harness sampling from the non-streaming call on a large result
set is sampling from a silently-truncated population, not the full one; prefer the streaming
call (`streamedListObjects`) wherever the harness can consume a stream, precisely because it is
the one used above to get the complete, truncation-free count.

**Ordering carries no comparison meaning on either side.** SpiceDB's `LookupResources` is a
stream with no ordering guarantee stated anywhere in the proto -- **verified directly against
`authzed/api/v1/permission_service.proto` at the pinned clients commit**, not cited to
`core-concepts.md`, whose "Product-level limits of `LookupResources`" section covers counts,
duplicates, and pagination and says nothing about order at all (the string "order" does not
appear in that file). The rpc's own comment is "LookupResources returns all the resources of a
given type that a subject can access whether via a computed permission or relation membership,"
and `LookupResourcesResponse`'s is "contains a single matching resource object ID for the
requested object type, permission, and subject" -- neither mentions ordering, and the same proto
*does* commit to an order elsewhere when it means to (`ExportBulkRelationships` "will return
results in an order determined by the server"; `CheckBulkPermissions`' "ordering of the items in
the response is maintained"), which is what makes the silence here meaningful rather than merely
unstated. OpenFGA's own SDK internals aren't order-stable either -- `code-mapping.md`'s
`listRelations` section documents a pre-0.2.8 `@openfga/sdk` version whose `batchCheck` "fans
the underlying `check` calls out via `asyncPool`," so "a re-run can just as easily print
`[\"writer\",\"viewer\"]`" instead of the reverse. Neither side's list is safe to zip
positionally against the other's; compare as sets (after deduplication), never as sequences.

**Never feed a list-derived record into snapshot-to-assertions -- and mark it so that rule can
actually be applied.** `differential-harness.md`'s own eligibility rule for that capability is
`verdict: AGREE` **plus** `question.origin` in `{CHECK, BATCH_CHECK}`; a sampled,
lower-confidence list-derived record was never a check-shaped comparison, and freezing one into
a regression suite would misrepresent it as ordinary check parity. This adapter's obligation is
the one half of that rule it owns: **every `Question` this adapter produces from a sampled
`listObjects`/`listUsers` result must carry `origin: LIST_SAMPLED`**, set at the point the
sample is drawn. Once re-asked as a check, such a record is otherwise byte-identical to one
from a real `check` call site -- the field is the only thing that keeps them apart downstream.
Correspondingly, a `Question` from an observed `check` carries `origin: CHECK` and one from a
`batchCheck` item carries `origin: BATCH_CHECK`; `listRelations`' decomposed questions
(above) are ordinary checks against a single resource and subject, not samples drawn from an
enumeration, and carry `origin: CHECK`.

**Verified matching-set example**, same running example, `user:anne` (a viewer of
`document:1` only through group membership, no direct grant):

```
$ curl -s -X POST .../list-objects -d '{"type":"document","relation":"viewer","user":"user:anne"}'
{"objects":["document:1"]}
$ zed permission lookup-resources document viewer user:anne --endpoint localhost:50897 --token task3demo --insecure
1
```

`{document:1}` on both sides, after applying `code-mapping.md`'s identifier obligation to
either result (a no-op here, since this example's `id_encoding.mode` is `"none"`).

### `expand` -- not comparable at all

`code-mapping.md`'s "`expand` tree shape" section already establishes the structural fact
this rests on, cited rather than re-verified: OpenFGA's expand tree can contain a
`Leaf.tupleToUserset` node requiring a second, recursive call to resolve, and "SpiceDB's tree
has no node kind that corresponds to `tupleToUserset` at all -- the whole tree is resolved
server-side in one call, always." The two trees are not two encodings of the same
information; one can require a client-side walk the other structurally cannot express. This
adapter offers no comparison function for `expand`, and none should be built by analogy to
`check`'s tree-of-booleans intuition -- a tree diff here would be comparing incompatible
shapes, not measuring parity.

### Six operations with no SpiceDB target

`code-mapping.md`'s "Operations with no SpiceDB target -- halt, don't guess" is the authority
for all six (store CRUD, AuthZEN, Permissions Index, `contextual_tuples`,
`authorization_model_id` pinning, `readAssertions`/`writeAssertions`) and is cited, not
restated. None of the six has a SpiceDB RPC to answer the equivalent question with, so none
has anything for a differential record to compare -- there is no `observe()`/`ask()` entry
point for any of them in this adapter, and a customer's harness build should not expect one.
Saying so here, plainly, is the point: a harness that stays silent about these six implies
coverage it does not have.

## Mapping OpenFGA's answers onto the five record states

`differential-harness.md`'s `Outcome` vocabulary states which states a **source** may ever
carry: `ALLOWED`, `DENIED`, `ERRORED`, `NOT_ANSWERED` -- `CAVEATED` is "target only." Every
row below is this adapter's answer to "what OpenFGA response produces which of those four,"
plus why the fifth is structurally unreachable from this source.

| OpenFGA signal | `Outcome` | Evidence |
|---|---|---|
| `{"allowed": true}`, HTTP 200 | `ALLOWED` | Verified live throughout this file |
| `{"allowed": false}`, HTTP 200 | `DENIED` | Verified live throughout this file |
| Any non-2xx response / thrown SDK exception | `ERRORED` | See below |
| Adapter's own observation hook never fired | `NOT_ANSWERED` | Not an OpenFGA fact -- see below |
| *(unreachable from this source)* | `CAVEATED` | See below |

### `ALLOWED` / `DENIED`

**A real boolean, not merely a falsy value.** Verified live with the idiomatic client, typed:

```
DENIED (no relationship): resolved, allowed=false (type: boolean)
ALLOWED (real relationship): resolved, allowed=true (type: boolean)
```

`allowed` is a genuine `boolean` in both cases -- distinguishable, at the type level, from a
thrown exception. This matters because the `ERRORED` case below is not "a falsy `allowed`" at
all; it is a different code path entirely.

### `ERRORED`

**OpenFGA's own wire protocol already separates "no" from "failed," the same discipline
`differential-harness.md` and `code-mapping.md` document for SpiceDB's side of the same
comparison -- verified live, fresh, on this side.** A genuine denial is HTTP 200 with a
boolean body; a genuine failure is a 4xx with a structured error body, never a 200:

```
$ curl -s -o /dev/null -w "HTTP status: %{http_code}\n" -X POST .../check \
    -d '{"tuple_key": {"user":"user:nobody","relation":"direct_viewer","object":"document:dual1"}}'
HTTP status: 200
$ curl -s -o /dev/null -w "HTTP status: %{http_code}\n" -X POST .../check \
    -d '{"tuple_key": {"user":"user:carol","relation":"nonexistent_relation","object":"document:dual1"}}'
HTTP status: 400
```

And at the idiomatic SDK layer, the same two cases surface as a resolved boolean vs. a thrown
exception, never a resolved `false`:

```
DENIED (no relationship): resolved, allowed=false (type: boolean)
ERRORED (nonexistent relation): THREW FgaApiValidationError - FGA API Validation Error: post check : Error invalid relation: relation 'document#nonexistent_relation' not found
ERRORED (condition, missing context): THREW FgaApiValidationError - FGA API Validation Error: post check : Error failed to evaluate relationship condition: 'non_expired' - tuple 'document:conditional-doc#conditional_viewer@user:mallory' is missing context parameters '[current_time]'
```

Map any non-2xx response (raw HTTP observation point) or any thrown exception (SDK
observation point) to `ERRORED`. Populate `Outcome.detail` from whichever layer the
observation point sits at: the raw body's `{code, message}` at the HTTP layer, or the
exception's `constructor.name` plus `.message` at the SDK layer -- both are shown above and
either is a faithful `detail` value.

**The discriminator between `ERRORED` and `NOT_ANSWERED` is whether a response came back, not
whether a call was attempted -- and this adapter follows the contract's own wording rather than
a looser reading of it.** `differential-harness.md` reserves `ERRORED` for "a response that
actually came back reporting failure," and lists "a client-side deadline expired before any
response arrived" among the things that are `NOT_ANSWERED`. Applied to OpenFGA:

| What happened at this adapter's observation point | `Outcome` |
|---|---|
| A non-2xx response, or an SDK exception carrying one (`FgaApiValidationError` and friends, above) | `ERRORED` -- a response came back, reporting failure |
| A 5xx from the server | `ERRORED` -- same reason; the failure is the server's answer |
| The client's own deadline/timeout expired with nothing on the wire | `NOT_ANSWERED` -- the contract names this case explicitly |
| The call never reached OpenFGA at all (connection refused, DNS failure, TLS handshake failure) | `NOT_ANSWERED` -- nothing came back to read a failure out of |

The last two rows are the correction: an earlier revision of this file ruled a timeout
`ERRORED` on the reasoning that "the call was attempted and failed to produce an answer," which
is a real distinction but not the one this contract draws. Both rows land on `INCONCLUSIVE`
through `Diff` rule 1 either way, so no verdict changes -- what changes is the `reason` token
(`SOURCE_ERROR` vs. `SOURCE_NOT_ANSWERED`) and therefore which triage bucket the record groups
into, which is exactly what that precedence exists to make identical across implementations.
Where an SDK collapses "no response" and "error response" into one exception type and the
observation point genuinely cannot tell them apart, record `ERRORED` and **say so** for that
integration, rather than guessing per-record -- the same "state which case a given integration
is in" discipline this section already applies to the app-level swallow below.

**The app-level swallow-to-`DENIED` hazard is real, and it lives above this adapter's
observation point, not inside the SDK.** `differential-harness.md` names the risk directly:
"if a source's own client already swallows an error into a false denial *before* the point a
source adapter observes it... the adapter never sees `ERRORED` to report -- it sees whatever
the client handed back, already collapsed." Verified live, the exact mechanism, using the
same `nonexistent_relation` call the SDK transcript above throws on:

```ts
async function appLevelSwallow(req) {
  let allowed;
  try { ({ allowed } = await client.check(req)); }
  catch (e) { allowed = false; }   // the anti-pattern
  return allowed;
}
```
```
Swallowed ERRORED (nonexistent relation) -> app sees: app-level wrapper returns allowed=false
```

The idiomatic SDK itself does not do this -- it throws, faithfully, as shown above. The
collapse happens only when a call site wraps that throw in its own `try`/`catch` and defaults
to `false`, which is a pattern in application code, not an SDK behavior. **This adapter's own
obligation follows directly: the observation hook must sit at the SDK call boundary itself
(wrapping `client.check`/`client.checkPermissions` directly), above any such call-site
`try`/`catch`, not downstream of it.** Where it can be placed there, `ERRORED` is visible,
exactly as demonstrated. Where a call site's own error handling already ran before the hook
observes anything -- the hook wraps a helper function that itself swallows, rather than the
raw client call -- the adapter inherits whatever that call site already decided and cannot
recover the distinction after the fact; state which case a given integration is in rather
than implying uniform visibility. This is the same limitation `differential-harness.md`'s
"record shape" section states in general terms -- that the contract "can only guarantee the
source's path so far as the adapter's observation point sits above the swallowing" -- made
concrete for OpenFGA's own idiomatic client.

**Why this distinction is load-bearing, not pedantic.** If a swallowed `ERRORED` is recorded
as `DENIED` instead, `Diff` rule 1 -- "Either side `ERRORED` or `NOT_ANSWERED` &rarr;
`INCONCLUSIVE`. This check runs first and unconditionally" -- never fires for that record.
The record instead proceeds to rules 3/4 as if the source had genuinely answered: a target
that also says `DENIED` reports a false `AGREE` (the source never actually answered, but the
record claims parity), and a target that says `ALLOWED` reports a spurious `DISAGREE`
requiring staleness reconciliation for a comparison that was never valid to begin with. The
pipeline's fail-closed discipline depends on `ERRORED` staying visible as `ERRORED` all the
way to `Diff`; a collapsed record defeats it silently.

### `NOT_ANSWERED`

**Mostly a fact about this adapter's own dispatch -- but not only that.**
`differential-harness.md` defines it precisely, quoted in full: "the question was sampled out,
a queue was full before dispatch, **a client-side deadline expired before any response
arrived**, the observation point that captures the production decision missed it, or a replay
batch skipped it deliberately."

Four of those five are facts about dispatch: for this adapter, `NOT_ANSWERED` covers the
`observe()` hook never running for a given production request at all -- sampling excluded it,
the hook was never wired into that call site, or the harness's own recording pipeline dropped
it before persistence. **The third is not**, and it is the clause an earlier revision of this
section quoted around: a client-side deadline expiring with nothing on the wire is
`NOT_ANSWERED` even though a call really was attempted, because nothing came back to read a
failure out of. See the `ERRORED` table above, which rules on the transport cases in one place.

What remains true without qualification: `NOT_ANSWERED` is never populated from anything
OpenFGA itself *returned*. If a response came back -- a 4xx, a 5xx, an SDK exception carrying
one -- that is `ERRORED` (above), not this.

### `CAVEATED` -- unreachable from this source, and why the two ways it could look reachable aren't

OpenFGA's check API is strictly boolean by construction -- every transcript in this file
resolves to `true`, `false`, or a thrown error, never a third value -- so `observe()` and
`ask()` can never produce `CAVEATED` on the source side. `differential-harness.md` states the
same fact from the target side ("has no independent equivalent on a source whose check API is
strictly boolean") and `test-mapping.md` states it for the test-conversion side ("OpenFGA's
own check API is strictly boolean -- there is no 'caveated'/'conditional' third state on the
source side to map from"); this is the harness-side instance of the identical,
already-established fact -- cited, not re-derived.

**A SpiceDB `CAVEATED` outcome against this source is a real divergence class, not a bug
report, and it arises from two structurally different situations that both end up at the same
target state for different reasons:**

1. **The common case: the source relationship was never conditioned at all.** A caveat that
   is new to the SpiceDB schema -- added during or after migration, with no `condition:`
   block on the corresponding OpenFGA tuple -- means OpenFGA's own check answers a plain,
   uncaveated `ALLOWED`/`DENIED`, with no awareness that the target's schema now requires
   context it was never asked to supply. `Diff` rule 2 -- "Target `CAVEATED` &rarr;
   `INCONCLUSIVE`, `reason: CAVEAT_GAP`... never `AGREE` and never `DISAGREE`, regardless of
   what the source answered" -- is exactly this case, and it is the one
   `differential-harness.md`'s own "What counts as a disagreement" section describes as "a
   real, measurable coverage gap," not a defect.
2. **A narrower case, verified live here: the source relationship *is* conditioned, and the
   `Question`'s context omits a parameter the condition requires.** This is not a `CAVEATED`
   analog at all -- it is a source-side `ERRORED`, demonstrated above (the missing-`current_time`
   transcript): OpenFGA does not degrade to some third value when a condition it knows about
   is missing context, it hard-errors. Because `Diff` rule 1 runs "first and unconditionally,"
   *before* rule 2 ever inspects the target's outcome, a record in this shape is finalized
   `INCONCLUSIVE`, `reason: SOURCE_ERROR` regardless of what the target answered -- whether
   the target also came back `CAVEATED` (plausible, if the same condition converted faithfully
   to a caveat) is not something this adapter needs to determine, because rule 1's precedence
   over rule 2 makes it irrelevant to the verdict either way.

## Constructing a `DifferentialRecord`, worked

Putting the forward table, `check`'s comparison, and the five-state mapping together, the
`AGREE` half of "What getting it wrong looks like, live" above, expressed as the record
`differential-harness.md` defines (real `checkedAt` token, captured with `zed`'s own `--json`
flag against the same call):

```yaml
question:
  resource: "document:1"
  permission: "viewer"
  subject: "user:bob"
  context: {}
  request_id: "req-demo-001"
  asked_at: "2026-08-15T05:11:47Z"
  origin: CHECK   # an observed single `check` -- BATCH_CHECK for a `batchCheck` item,
                   # LIST_SAMPLED for an ID sampled out of a listObjects/listUsers result

source:
  outcome: ALLOWED
  marker: null   # OpenFGA's check response carries no revision/token field at all --
                  # verified above (`{"allowed":true,"resolution":""}`) -- so there is
                  # nothing to populate here; see code-mapping.md's Consistency section
                  # for the same absence, cited there for a different purpose

target:
  outcome: ALLOWED
  zedtoken: "Gh8KEzE3ODY3NzA3MDU0MTAwMDAwMDASCGFiNTcwNzUy"

comparison:
  verdict: AGREE
  disposition: null
  reason: null
```

`source.marker` is documented as optional ("if the source exposes one"); OpenFGA's `check`
response has no analog to a `ZedToken` (`code-mapping.md`'s own Consistency section makes the
same observation, for the different purpose of motivating why a converted call site should
thread a real SpiceDB ZedToken rather than leave read-your-writes unsupported the way the
source did), so this adapter leaves it unset rather than inventing a value the source never
provided.

## What this file does not do

- **It does not redefine `differential-harness.md`'s record shape, `Diff` rules, safety
  property, or sampling guidance.** Every one of those is cited above by name; none is
  restated here, and none should be re-derived by a future pack copying this file's shape.
- **It does not cover writes.** Dual-run and Replay both stop at `check`/`batchCheck`/lookup
  calls -- "Which operations are comparable at all," above, is the table that establishes that,
  and "Why `relation_splits[T][R].relation` -- the write-target name -- never appears anywhere in
  this adapter" is the section that draws the consequence. ("What getting it wrong looks like,
  live" is about choosing the wrong *field* on a check path, not about writes.) Nothing in this
  adapter builds a `Transaction`/`Txn`, and `relation_splits[T][R].relation` correspondingly
  never appears in it.
- **It does not implement snapshot-to-assertions' `relationships:` export.** That capability
  reads relationships from the *target* (SpiceDB) at the recorded `zedtoken`
  (`differential-harness.md`, "Snapshot-to-assertions"), not from OpenFGA -- there is no
  source-adapter role in it at all.
- **It does not implement sampling, dual-write, or reconciliation.** Those belong to
  `cutover-strategies.md` step 4's operational description and to the customer's own
  deployment; this file supplies only the per-`Question` translation the harness runs on top
  of.

## Deliberately not written yet

Known gaps, held open on purpose, matching this pack's existing convention
(`code-mapping.md`'s own closing section, spec decision D11).

- **Only the idiomatic TypeScript/JS client and raw HTTP were exercised for the `Outcome`
  mapping above.** `code-mapping.md`'s "Per-language check-signature divergence" table
  already documents that a source-side sync client (Python's `openfga_sdk.sync`) and an
  async-only target create their own hazard independent of this adapter; this file has not
  independently re-verified that each of the seven target languages' *observation* hook can
  be placed above every language's own call-site error handling the way the TypeScript
  example above demonstrates.
- **Batching multiple sampled `Question`s into one OpenFGA `batchCheck` call for Dual-run
  itself** (as opposed to `check`'s single-item shape used throughout this file) is permitted
  by `differential-harness.md`'s phrasing ("`CheckPermission` (or the bulk/lookup
  equivalent...)") but not worked out in detail here beyond the per-item `Outcome` mapping
  above -- fanning a batch response back into N separate `DifferentialRecord`s needs the same
  `correlation_id`-vs-position care `code-mapping.md`'s "`batchCheck` ordering" section
  already documents for a converted call site.
- **No corpus of real shadow traffic exercises this file.** Every transcript above is a
  purpose-built live call against a fresh store, the same category of gap
  `code-mapping.md`'s own "Deliberately not written yet" section states for itself: real,
  messy production traffic is a different and harder test than a clean worked example, and
  this file has not been run against any.
