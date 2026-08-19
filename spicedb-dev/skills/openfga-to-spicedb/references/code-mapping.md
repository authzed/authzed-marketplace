# Code Mapping: OpenFGA → SpiceDB

Construct-by-construct translation rules for phase 4 (application code: OpenFGA SDK calls →
SpiceDB client calls). Unlike `data-mapping.md` and `test-mapping.md`, this file has no
corpus behind it -- `SKILL.md`'s own validation-corpus section states plainly that Tier 2
(application code) has **zero** repositories converted so far, because the reference this
file *is* did not exist yet. Everything below is verified a different way: against the real,
installed OpenFGA SDKs (`@openfga/sdk`, `@auth0/fga`, `openfga-sdk`) on one side, and the
real vendored SpiceDB client source at the pinned commit on the other, with every call shown
actually executed against a live `openfga` server and a live `spicedb serve-testing`
instance.

## Scope of this file

**This file is the mapping, consumed by `/spicedb-dev:migrate-code` (phase 4).** That
command reads `migration-map.json` and `migration-plan.md`'s **Relation splits** table
(produced by phase 1), picks the target language's reference in
`spicedb-client-integration/references/`, and applies the rules below construct by
construct -- the same relationship every other phase's command has to its own pack
reference (`/spicedb-dev:migrate-schema` to `schema-mapping.md`, `/spicedb-dev:migrate-data`
to this file's sibling `data-mapping.md`, `/spicedb-dev:migrate-tests` to `test-mapping.md`).

**This file owns "what an OpenFGA call becomes"; `spicedb-client-integration` owns "how to
use the client" once you know the target call.** Method signatures, consistency-helper
names, error-handling idiom, and streaming behavior are covered in full, per language, in
that skill's seven reference files plus `core-concepts.md` -- this file cites them rather
than re-deriving that detail, per that skill's own citation convention. Where a fact matters
to a mapping and isn't already documented there, it is derived here directly from the client
source, not inferred from `tools/migration-harness/fixtures/client-api-surface.json`'s
`source_line` field, which **truncates at the opening parenthesis for a multi-line
declaration** and therefore drops the return type of any multi-line signature -- exactly the
gap that hid Rust's `check_permission` returning a `CheckResult { has_permission }` wrapper
rather than a bare `bool` (caught and corrected in `spicedb-client-integration/references/
rust.md`, cited below). Every claim in this file that could be affected by that gap was
re-read from the client's real source, not the JSON, and is cited by `file:line`.

**Client commit pinned:** `549c4e90e7a1488adcf268e0e0033e48d5b5f0a4` (same commit
`spicedb-client-integration`'s seven references and `client-api-surface.json` were verified
against -- `installation.md` is the one file that says how to obtain this client; nothing
here repeats that). **OpenFGA-side versions used for every live sample below:** server
`openfga/openfga:latest` (reports `v1.18.3`, build `81c6202`), `@openfga/sdk` `0.9.6`
(current, published), `@auth0/fga` `0.10.0` (current, published) and `0.4.1` (historical, for
the version-evolution claims below), Python `openfga-sdk` `0.10.4`, `fga` CLI `v0.7.20`.
**SpiceDB-side:** `spicedb serve-testing` `v1.56.0` (floor: v1.52.0), `zed` `v0.31.1`,
started with `--endpoint`/`--token` passed explicitly -- `serve-testing` takes no
`--grpc-preshared-key` in v1.56.0, per this pack's existing convention. Every OpenFGA-side
snippet below runs against a real `openfga` container; every SpiceDB-side snippet runs
against a real `spicedb serve-testing` container; no output below is illustrative.

One running example threads through this file's "more than a rename" section, chosen because
it is also the OpenFGA JS SDK's own documented `Expand` example (`@openfga/sdk`'s
`OpenFgaApi.expand` doc comment, verified verbatim against the installed package below):

```
model
  schema 1.1

type user
type group
  relations
    define member: [user]
type folder
  relations
    define owner: [user]
type document
  relations
    define parent: [folder]
    define writer: [user] or owner from parent
    define viewer: [user, group#member] or writer
```

```
definition user {}
definition group {
	relation member: user
}
definition folder {
	relation owner: user
}
definition document {
	relation parent: folder
	relation direct_writer: user
	relation direct_viewer: user | group#member
	permission writer = direct_writer + parent->owner
	permission viewer = direct_viewer + writer
}
```

**One thing not to copy literally from that `.zed` block:** it spells the split relations
`direct_writer`/`direct_viewer`, a hand-written prefix chosen to keep the example readable,
where the pack's actual default is the **`__direct` suffix** (`writer__direct`,
`viewer__direct`) and the real name is whatever `migration-map.json`'s `relation_splits`
records for that relation. Read the name out of the map, never off this example -- see "The
relation-split obligation" below.

Seeded identically on both sides: `folder:1` owned by `user:bob`; `group:eng` has
`user:anne` as a member; `document:1`'s `parent` is `folder:1`; `document:1`'s
`viewer`/`direct_viewer` includes `group:eng#member`. Both sides agree on every fact this
file checks against it (`bob` is a writer and a viewer of `document:1` via the folder-owner
arrow; `anne` is a viewer only, via the group).

## Detecting the source shape

All of these shapes are live, and a codebase can contain more than one at once (a migration
mid-flight off `@auth0/fga`, or a service that kept the raw `OpenFgaApi` for one endpoint
and the flattened `OpenFgaClient` everywhere else). Detect per call site, not per
repository.

### Embedded OpenFGA server -- no client at all, and phase 4 cannot proceed as written

Some applications do not call OpenFGA over the network; they **import the server and run it
in-process**, constructing it with something like `openfgaserver.NewServerWithOpts(...)` from
`github.com/openfga/openfga`, then calling `Check`/`Write`/`BatchCheck` and lifecycle methods
(`Start`, `Stop`, `Close`, `Healthy`) directly on that object. Relationships live in the
application's **own database**, through OpenFGA's storage interface pointed at an existing
SQL connection, so there is no store to read with `fga tuple read` and often no OpenFGA
process at all. Confirmed on a real Go project.

**This is not a call-site rewrite, and treating it as one produces nonsense.** The other
shapes in this section swap one client's method for another's. Here the conversion means
*removing an embedded library and standing up a separate service*: SpiceDB has no in-process
mode, so the application gains a network dependency, a deployment unit, connection
configuration, and failure modes it did not have. The call-mapping table has no row for
`NewServerWithOpts` or any lifecycle method, and it should not -- they have no SpiceDB
equivalent.

**When phase 4 finds this shape, stop and put it to the user before rewriting anything.** It
is a Class A finding, and the decision is architectural rather than mechanical:

1. **Run SpiceDB as a service** and convert the call sites to a SpiceDB client against it --
   the intended end state, but it adds an operational component the project must own, and
   the lifecycle code has to be replaced rather than mapped.
2. **Keep the embedded server for now** and migrate only the schema and data, deferring the
   code change to a later, separately-planned step.
3. **Stop after phase 2** if the operational change is not acceptable, and record why.

**The schema phase is unaffected** -- the model converts exactly as it would for any other
shape. **The data phase is not:** as stated above, relationships in this shape live in the
application's own database rather than in a reachable store, and all of `data-mapping.md`'s
extraction methods assume `fga tuple read` against a store. Extraction here means reading
OpenFGA's storage tables directly, which that file does not cover; treat it as an unresolved
gap and record it rather than reporting phase 3 as simply deferred. A run that completes
phases 0-2, emits the codec, and halts before phase 4 has done real work, not failed. Say that plainly in the report rather than presenting phase 4 as
blocked.

### `OpenFgaClient` -- flattened camelCase inputs, snake_case responses

The idiomatic wrapper. Confirmed the exact class name is real, not a paraphrase, in **two**
independent OpenFGA SDKs -- `@openfga/sdk` (TypeScript/JS) and `openfga-sdk` (Python) both
export a class literally named `OpenFgaClient`:

```ts
import { OpenFgaClient } from "@openfga/sdk";
const flat = new OpenFgaClient({ apiUrl: "http://localhost:8082", storeId: STORE_ID });
const flatResp = await flat.check({ user: "user:bob", relation: "viewer", object: "document:1" });
console.log("OpenFgaClient.check ->", JSON.stringify(flatResp));
```
```
OpenFgaClient.check -> {"allowed":true,"resolution":""}
```

```python
from openfga_sdk.sync import OpenFgaClient
from openfga_sdk.client.configuration import ClientConfiguration
from openfga_sdk.client.models import ClientCheckRequest

cfg = ClientConfiguration(api_url="http://localhost:8082", store_id=STORE_ID)
with OpenFgaClient(cfg) as client:
    resp = client.check(body=ClientCheckRequest(user="user:bob", relation="viewer", object="document:1"))
    print("Python OpenFgaClient.check ->", resp.allowed)
```
```
Python OpenFgaClient.check -> True
```

Inputs are flat fields (`user`, `relation`, `object` directly on the request), storeId is
fixed at construction (or per-call, in newer SDK versions -- see below), and the response
surfaces plain booleans/strings. This is almost always what application code that calls
`fga.check(...)`/`client.checkPermission(...)`/`client.write(...)` with plain object
literals is using.

### `OpenFgaApi` -- raw wire shapes

The generated, un-idiomatic client -- `tuple_key`, `writes.tuple_keys`, `contextual_tuples`,
exactly the field names the OpenFGA HTTP/gRPC API itself uses:

```ts
import { OpenFgaApi, Configuration } from "@openfga/sdk";
const raw = new OpenFgaApi(new Configuration({ apiUrl: "http://localhost:8082" }));
const rawResp = await raw.check(STORE_ID, {
  tuple_key: { user: "user:bob", relation: "viewer", object: "document:1" },
});
console.log("OpenFgaApi.check(storeId, {tuple_key}) ->", JSON.stringify(rawResp.body ?? rawResp));
```
```
OpenFgaApi.check(storeId, {tuple_key}) -> {"allowed":true,"resolution":""}
```

**Store ID's position is a real, version-dependent tell, not folklore -- dated exactly.**
`@openfga/sdk`'s own `CHANGELOG.md` (installed package, v0.9.6): the v0.4.0 entry (2024-04-30)
reads "feat!: support overriding storeId per request ... [BREAKING CHANGE] the underlying
`OpenFgaApi` now expects `storeId` as the first param on relevant methods." Verified on both
sides of that boundary, live: at `@openfga/sdk@0.2.7` (installed separately for this check),
`OpenFgaApi.check(body, options?)` has **no `storeId` parameter at all** (`dist/api.d.ts:420`
of that installed version) -- it is read from `Configuration.storeId`, fixed at
construction. At the currently-published `0.9.6`, `check(storeId: string, body: CheckRequest,
options?: any)` (`dist/api.d.ts:413` of *that* version) takes it explicitly, every call.
Detect the SDK's `package.json` version if you need to know which shape a given call site
expects before rewriting it -- code written against a sub-`0.4.0` `OpenFgaApi` will not
compile against the storeId-in-config assumption and vice versa.

### `Auth0FgaApi` (`@auth0/fga`, deprecated) -- keyed on `environment`

The predecessor to hosted OpenFGA (Auth0 FGA → Okta FGA), still present in real codebases
that migrated to `@openfga/sdk` for the API but never touched their credentials plumbing, or
that never migrated at all. Its config takes an `environment` string instead of an
`apiUrl`/`apiHost`, resolved through a small hardcoded lookup table rather than a URL the
caller supplies -- and that table is **not** the fixed three-value set a shorthand reading
suggests; it changed shape release to release. Verified live at three points in its history,
using the real published package at each version (`npm pack @auth0/fga@<version>`, not a
paraphrase of the changelog):

| Version | Accepted `environment` values (from `getEnvironmentConfiguration`'s own table) |
|---|---|
| 0.4.1 (`dist/configuration.js:66`) | `default`, `us`, `playground`, `staging`, `poc` |
| 0.8.0 (`dist/configuration.js:22`) | `default`, `us`, `playground`, `staging` (`poc` dropped) |
| 0.10.0, current (`dist/configuration.js:70-106`, `dist/constants/environments.d.ts`) | `us1`, `eu1`, `au1`, `staging`, `playground` (enum `FgaEnvironment`), plus `us`/`default` still silently accepted as legacy aliases for `us1` |

```
$ node -e '... new Auth0FgaApi({..., environment: "not-a-real-env"}) ...'   # @auth0/fga 0.4.1
@auth0/fga 0.4.1 rejects unknown environment: InvalidEnvironmentError: environment is
required and must be one of the following: default, us, playground, staging, poc
```
```ts
import { Auth0FgaApi } from "@auth0/fga";   // 0.10.0, current
const legacy = new Auth0FgaApi({ storeId: "s1", clientId: "c", clientSecret: "x", environment: "us" });
console.log("environment='us' (legacy alias) constructs fine:", legacy.constructor.name);
try {
  new Auth0FgaApi({ storeId: "s1", clientId: "c", clientSecret: "x", environment: "not-a-real-env" });
} catch (e) {
  console.log(`@auth0/fga 0.10.0 rejects unknown environment: ${e.constructor.name}: ${e.message}`);
}
```
```
environment='us' (legacy alias) constructs fine: Auth0FgaApi
@auth0/fga 0.10.0 rejects unknown environment: FgaInvalidEnvironmentError: environment is
required and must be one of the following: us1, playground
```

The spec's shorthand -- `environment: "us"|"staging"|"playground"` -- is real (all three are
accepted, at every version checked) but is not a complete enumeration at any single version;
treat it as "one of a small, version-dependent, hardcoded set," and read the installed
`@auth0/fga` version's own `configuration.js` if the exact accepted set matters (e.g. to
decide which SpiceDB endpoint region, if any, a given `environment` value should map a
customer toward -- SpiceDB has no regional-endpoint concept to preserve here at all; this is
purely a config-detection signal, not something phase 4 rewrites onto anything). `@auth0/fga`
is deprecated upstream; every call site using it is a `check`/`write`/`read`/etc. call
identical in shape to `OpenFgaApi`'s (it is generated by the same tooling lineage), so once
detected, convert it exactly as `OpenFgaApi` below -- the only `Auth0FgaApi`-specific step is
recognizing it from its constructor and `environment` key, not a separate call-mapping table.

## The call mapping

Every target below is a real method on the vendored SpiceDB client, confirmed against its
source at the pinned commit (`client-api-surface.json`'s `public_methods` block for the
name, the real source for anything the JSON's truncation could hide). Names shown for Go /
Python / TypeScript, the trio `spicedb-client-integration` documents together in its
`references/go.md`, `references/python.md`, `references/typescript.md` -- C#, Java, Rust,
and Ruby follow the same pattern under that language's own casing convention (see each
file). Corrections against the spec's draft table are called out inline, marked
**Corrected** in the row itself; several of those also have a subsection in "Mappings that
are more than a rename" below, but not all do -- a row can need a correction without needing
a structural rewrite. Read the rows for the corrections and that section for the rewrites,
rather than assuming the two sets coincide.

**Read "The relation-split obligation" before applying any row of this table.** The table
maps *method names*; it does not map the relation name each method is handed. Wherever
phase 1 split a source relation, the write path and the check path take **different**
strings for what was one name in the source, and no row below can express that -- it is a
`migration-map.json` lookup per call site, exactly like the identifier codec, and it fails
at runtime rather than at build time.

| OpenFGA | SpiceDB (Go / Python / TypeScript) | Note |
|---|---|---|
| `check` | `CheckOne` / `check_permission` / `checkPermission` | Python's has **no `permission` parameter**; see "Per-language check-signature divergence" |
| `batchCheck` / `clientBatchCheck` | `Check` / `check_permissions` / `checkPermissions` | **Corrected**: all three return a positionally-ordered array (`[]bool` / `list[bool]` / `boolean[]`) over `CheckBulkPermissions`, not a map -- see "`batchCheck` ordering" below. **Go's SDK also exposes a separate, non-`Client`-prefixed `BatchCheck` builder method alongside `ClientBatchCheck`** (`go-sdk@v0.8.2`'s `client/client.go`) -- confirmed to be the same underlying server RPC, not a second construct; this row covers both Go methods, not just the one whose name matches the table's `clientBatchCheck` label |
| `listObjects` / `streamedListObjects` | `LookupResources` / `lookup_resources` / `lookupResources` | Always streaming server-side regardless of which OpenFGA call it replaces; see `core-concepts.md`'s "Product-level limits of `LookupResources`" for its own caveats (no total count, duplicate resource IDs **whether or not you paginate**, 1000-per-call cap). The permission argument is the split's `permission`, never its `relation` -- see "The relation-split obligation" |
| `listUsers` | `LookupSubjects` / `lookup_subjects` / `lookupSubjects` | direct rename of the method; the permission argument follows the same split rule as `listObjects` above |
| `listRelations` | bulk check across the permission list -- `check_permissions`/`checkPermissions` in **one** call (Python, TypeScript); `CheckOne` **per relation** in Go (no one-call path) | **Corrected**: not uniform across languages -- see "`listRelations` error convention" below |
| `expand` | `ExpandPermissionTree` / `expand_permission_tree` / `expandPermissionTree` | **Corrected**: returns an already-fully-resolved tree, not one requiring recursive client-side follow-up calls -- see "`expand` tree shape" below |
| `read` | `ReadRelationships` / `read_relationships` / `readRelationships`, with a `Filter` | `Filter`'s partial-match shape (any subset of resource/relation/subject fields) mirrors OpenFGA's partial `tuple_key` filter closely -- but the **relation name in the filter is not a rename** on a split relation: it is `relation_splits[T][R].relation`, see "The relation-split obligation" |
| `write` (transactional default) | `Write` / `write` / `write`, with a `Txn`/`Transaction` built via `.create()`/`.touch()`/`.delete()` | **Not a direct rename**: the method renames, the *relation name* does not survive unchanged on a split relation -- a write must target `relation_splits[T][R].relation`, never the source name the check side keeps (see "The relation-split obligation"). Both sides are atomic by default -- see "Non-transactional writes" for the one divergent case |
| `writeTuples` / `deleteTuples` | `Txn.Touch`/`Txn.Delete` (Go) / `Transaction().touch()`/`.delete()` (Python, TS) | **Corrected**: not separate methods on the SpiceDB side -- OpenFGA's two convenience methods both collapse into building one `Transaction`/`Txn` and calling the same `write`/`Write`. Same split-relation rewrite as `write` above |
| `readChanges` | `Updates` / `watch` / `watch` | **Corrected**: paged poll → server stream; type filtering, `start_time`, and continuation-token resumption all need rework -- see "`readChanges` → watch" below |
| `writeAuthorizationModel` | `WriteSchema` / `write_schema` / `writeSchema` | direct rename in shape (both take the complete schema text as one string) but not in semantics -- `WriteSchema` **replaces** the live schema; OpenFGA's `authorization_model_id` versioning has no SpiceDB counterpart (see "Operations with no SpiceDB target" below) |
| `readLatestAuthorizationModel` (or `getStoreAuthorizationModel`'s no-id-given form) | `ReadSchema` / `read_schema` / `readSchema` | direct rename: both return whatever model/schema is **currently live**, no id involved on either side -- confirmed against the vendored client at the pinned commit (`spicedb-go/client/schema.go:12`, also in `client-api-surface.json`'s `public_methods`). A common real call site (a client bootstrap routine checking "does a model already exist" before deciding whether to write one, e.g. a first-boot check) has a genuine SpiceDB target and is **not** one of the six no-target items below -- do not raise on it. `WriteSchema` is idempotent (rewriting an identical schema is a no-op in effect), so a rewritten bootstrap that always calls `WriteSchema` unconditionally is a valid *simplification* of a `ReadSchema`-then-maybe-`WriteSchema` call site, but it is not required -- both are legitimate, and this row exists so an agent does not have to invent the mapping under time pressure the way one real conversion run was forced to (see "Operations with no SpiceDB target" below for `readAuthorizationModel`/`readAuthorizationModels`, this method's two siblings that genuinely have no target) |
| `on_duplicate: ignore` / `error` / *(unspecified)* | `TOUCH` / `CREATE` / `CREATE` (the `Txn`/`Transaction` verb chosen when building the write, not a request parameter) | direct rename, relocated: it is a choice of *which builder method to call*, not a flag passed alongside the call. **A bare `write()` call with `on_duplicate` unspecified is not `TOUCH`** -- the SDK defaults the field to `error` (`@openfga/sdk` `dist/client.js`: `on_duplicate: conflict?.onDuplicateWrites ?? ClientWriteRequestOnDuplicateWrites.Error`), confirmed live against `openfga/openfga:latest` (`v1.18.3`) with `@openfga/sdk` `0.9.6`: a second bare `write()` of an already-written tuple throws `FgaApiValidationError` / `write_failed_due_to_invalid_input`, identically to an explicit `on_duplicate: "error"`, while `on_duplicate: "ignore"` on the same duplicate succeeds silently. The faithful conversion of a bare `write()` is `CREATE`, not `TOUCH` |
| `condition: {name, context}` on a written tuple | `caveatName` / `caveatContext` on the same `Relationship` the write already builds (`caveat_name`/`caveat_context`, `CaveatName`/`CaveatContext` -- `core-concepts.md`'s `Relationship` table) | direct rename, same position: it is a field on the relationship, stored with it, evaluated at check time. **Do not** move it to the check call -- `CheckPermissionRequest.Context` is a *different* channel (named values supplied per request), not where a stored tuple's own condition lives. The caveat must already exist in the schema phase 1 emitted; if it does not, that is a phase-1 finding, not something to add here |
| store CRUD, AuthZEN, Permissions Index, `contextual_tuples`, `authorization_model_id` pinning, `readAssertions`/`writeAssertions` | *(no target -- halt, don't guess)* | see "Operations with no SpiceDB target" below -- **six** items, not three |

## Mappings that are more than a rename

Mappings where a find-and-replace on the method name compiles cleanly and silently
changes behavior. Each below has a real before/after, executed on both sides.

### `batchCheck` ordering

**The wire response is a map keyed by `correlation_id`, not an array -- confirmed at the
protocol level, not just in the SDK's TypeScript types.** A raw HTTP call to the real
`/batch-check` endpoint, bypassing every SDK wrapper:

```
$ curl -s http://localhost:8082/stores/$STORE_ID/batch-check -d '{
    "checks": [
      {"tuple_key": {"user":"user:bob","relation":"viewer","object":"document:1"}, "correlation_id":"req-A"},
      {"tuple_key": {"user":"user:anne","relation":"viewer","object":"document:1"}, "correlation_id":"req-B"},
      {"tuple_key": {"user":"user:anne","relation":"writer","object":"document:1"}, "correlation_id":"req-C"}
    ]}'
{
    "result": {
        "req-A": {"allowed": true},
        "req-B": {"allowed": true},
        "req-C": {"allowed": false}
    }
}
```

`result` is a JSON **object**, not an array -- "the third item in the response" is not a
concept the wire format has. The `@openfga/sdk` client's own doc comment on
`OpenFgaApi.batchCheck` (installed package, `dist/api.d.ts:403`) states the same fact in its
own worked example before you ever call it live: "Note that the result map's keys are the
`correlation_id` values from the checked items in the request." The idiomatic
`OpenFgaClient.batchCheck` wrapper (`dist/client.js:507-521`) does the pairing itself, reading
`Object.entries(response)` and looking each `correlationId` up in a `Map` built from the
request -- it never trusts array position either, which is the maintainers' own evidence
that position is not part of the contract.

The SpiceDB side has no id field anywhere on the request or response -- position **is** the
only correlation mechanism:

```ts
const results = await client.checkPermissions(
  full(),
  { resourceType: "document", resourceId: "1", permission: "viewer", subjectType: "user", subjectId: "bob" },
  { resourceType: "document", resourceId: "1", permission: "viewer", subjectType: "user", subjectId: "anne" },
  { resourceType: "document", resourceId: "1", permission: "writer", subjectType: "user", subjectId: "anne" },
);
console.log("positional results [bob/viewer, anne/viewer, anne/writer]:", results);
console.log("typeof results:", Array.isArray(results) ? "array" : typeof results);
```
```
positional results [bob/viewer, anne/viewer, anne/writer]: [ true, true, false ]
typeof results: array
```

**Convert every call site that pairs a `batchCheck` result back to its request by
`correlation_id` (a lookup, a `Map`, a dict comprehension keyed by id) into one that trusts
array position instead.** If the source code also *uses* `correlation_id` for something
beyond pairing -- logging, tracing, deduplication -- that value is lost outright; there is
nothing on the SpiceDB side to carry it, and a migrating agent should flag such a use as a
Class C advisory finding (`findings-report.md`) rather than silently drop it.

### `listRelations` error convention

**Two separate, independently-verified findings here, both corrections against a flat
reading of the spec.**

**1. The OpenFGA-side "swallow as `allowed: false`" behavior is real, but historical --
fixed in the SDK almost three years before this file was written, and the fix is dated
exactly.** `@openfga/sdk`'s `CHANGELOG.md` (installed package): the v0.2.8 entry (2023-08-18)
reads "fix: list relations should throw when an underlying check errors." Verified live on
both sides of that fix, using the real published package at each version -- a relations list
mixing two real relations with one that does not exist in the model:

```
$ node ...   # @openfga/sdk 0.2.7 (pre-fix), installed separately
@openfga/sdk 0.2.7 listRelations result: {"relations":["viewer","writer"]}
```

(The two surviving relations' order in that array is not guaranteed -- this version's
`batchCheck` fans the underlying `check` calls out via `asyncPool`, so a re-run can just as
easily print `["writer","viewer"]`. The substance that matters -- `nonexistent_relation`
dropped silently, with no trace -- reproduces every time; the ordering does not.)

The `nonexistent_relation` check's error vanished with no trace -- indistinguishable from
"bob does not have `nonexistent_relation` on `document:1`." Reading `dist/client.js:451-467`
of that installed version confirms why: `batchCheck`'s per-item `.catch(err => ({allowed:
false, error: err, _request: tuple}))` explicitly maps any per-check failure to `allowed:
false`, and `listRelations` just filters on `.allowed`.

```ts
try {
  const result = await fga.listRelations({ user: "user:bob", object: "document:1", relations: ["viewer", "nonexistent_relation", "writer"] });
  console.log("result:", JSON.stringify(result));
} catch (e) {
  console.log("@openfga/sdk 0.9.6 listRelations threw:", e.constructor.name, "-", e.message);
}
```
```
@openfga/sdk 0.9.6 listRelations threw: FgaApiValidationError - FGA API Validation Error: post check : Error invalid relation: relation 'document#nonexistent_relation' not found
```

The **current, published** SDK (0.9.6) throws the first error it finds instead, discarding
*every* result, including the two valid ones, rather than silently narrowing the list. A
codebase on a pre-0.2.8 SDK inherits the swallow behavior; a codebase on 0.2.8+ inherits
fail-fast-and-lose-everything instead. Neither is "reproduce it exactly" without a decision
at the gate -- both are shapes `spicedb-best-practices`' fail-safe guidance applies to
(`schema-mapping.md`'s own citation of that same rule: "whether that reads as fail-closed
depends entirely on how the calling code handles a `Check` error... an unhandled exception is
not automatically a denial"): reproduce the source behavior deliberately, or drop it
deliberately, but do not let either language's default silently pick one.

**2. The SpiceDB-side replacement for `listRelations` -- one bulk check across the
permission list -- already has this exact same swallow-vs-throw split, and it runs the
*opposite* way in the two languages checked.** Reading each idiomatic client's own handling
of a per-pair error inside a `CheckBulkPermissions` response:

- **Python** (`spicedb-python/spicedb/client.py:159-166`): `if pair.HasField("error"): raise
  to_spicedb_error(...)` -- raises on the first per-item error, discarding the rest of the
  batch. Verified live, same nonexistent-permission shape as above:

  ```python
  results = await client.check_permissions(consistency.full(), Relationship("document", "1", "viewer", "user", "bob"), Relationship("document", "1", "nonexistent_permission", "user", "bob"), Relationship("document", "1", "writer", "user", "bob"))
  ```
  ```
  check_permissions raised: SpiceDBError: None
  ```

- **TypeScript** (`spicedb-typescript/src/client.ts:167-170`): `if (pair.response.case ===
  "error") { return false; }` -- silently maps the failing pair to `false` and keeps going,
  the *exact* pattern the OpenFGA SDK's own 0.2.8 fix moved away from. Verified live, same
  shape:

  ```ts
  const results = await client.checkPermissions(full(),
    { resourceType: "document", resourceId: "1", permission: "viewer", subjectType: "user", subjectId: "bob" },
    { resourceType: "document", resourceId: "1", permission: "nonexistent_permission", subjectType: "user", subjectId: "bob" },
    { resourceType: "document", resourceId: "1", permission: "writer", subjectType: "user", subjectId: "bob" },
  );
  console.log("TypeScript checkPermissions [viewer, nonexistent_permission, writer]:", results);
  ```
  ```
  TypeScript checkPermissions [viewer, nonexistent_permission, writer]: [ true, false, true ]
  ```

**A migrating agent cannot pick one policy for `listRelations` and assume it holds across
target languages.** Converting to Python silently gets fail-loud/lose-everything (whether or
not that is what the source code's error handling expected); converting to TypeScript
silently gets fail-closed/keep-going (reproducing the exact bug OpenFGA itself moved away
from in 2023). If the source call site relies on partial results surviving one bad relation
name, that behavior needs to be built explicitly (wrap each item in its own `try`/`except`
before calling bulk, or post-process the TypeScript array knowing `false` is ambiguous
between "denied" and "errored") -- neither client's default matches "some succeed, some
report their own error, nothing is silently conflated with a real denial."

**Three of the seven languages can build one `listRelations`-shaped call (same
resource/subject, many permissions) in a single round trip; four cannot at all.** Two of the
three -- Python and TypeScript -- can do it through the ergonomic client directly. The third,
Ruby, cannot do it through the ergonomic client either, but is the only one of the remaining
five with a documented public escape hatch to the raw proto client, so it gets there in one
call anyway. The two counts below are of different things and neither contradicts the other:
**two** languages' ergonomic clients express it, **three** languages can issue it in one
round trip. Every `CheckBulkPermissionsRequestItem` carries its own `permission` field
on the wire (`permission_service.proto:583`), but Go's `Check(ctx, cs, permission string,
rs ...rel.Relationship)` (`spicedb-go/client/checks.go:18`), Rust's `check_permissions(&self,
consistency, permission: &str, relationships: &[Relationship])` (`spicedb-rust/src/
client.rs:140`), Ruby's `check_permissions(consistency, permission, *relationships)`
(`spicedb-ruby/lib/spicedb/client.rb:126`), C#'s `CheckPermissionsAsync(consistency,
permission, cancellationToken, params relationships)` (`SpiceDBClient.cs:104`), and Java's
`checkPermissions(Consistency, String permission, Relationship...)`
(`SpiceDBClient.java:134`) all take **one shared `permission` argument applied to every item
in the batch** -- they cannot express "same resource/subject, five different permissions" in
one call. Only Python (`rel.resource_relation` read per-item, `client.py:140`) and
TypeScript (`CheckRequest.permission` is a per-item field, `spicedb-typescript/src/
types.ts:81`) can -- so the ergonomic-client count is **two**.

Of the five whose ergonomic client can't, four (Go, Rust, C#, Java) also have no public
escape hatch to the raw generated stub -- `spicedb-go`'s `Client.psc` (`client.go:15`) and
`spicedb-rust`'s `SpiceDBClient.proto` (`client.rs:51`) are both unexported/private fields,
and C#/Java follow the identical pattern (`private readonly ... _permissions` /
`private final ... permissionsStub`) -- so a `listRelations` conversion to any of those four
must issue one `CheckOne` call per relation in the list, losing the batching entirely. **Ruby
is the fifth, and the exception**: its ergonomic client shares the same shared-`permission`
limitation as the other four, so it is correctly excluded from the count of two above -- but
it is the only one of the five that can still get to one round trip, which is what makes the
one-round-trip count three rather than two.
`attr_reader :proto_client` (`spicedb-ruby/lib/spicedb/client.rb:39`, documented
"the underlying proto client for advanced use cases") lets a Ruby call site build one raw,
heterogeneous-permission `CheckBulkPermissions` request directly, bypassing the ergonomic
wrapper's shared-permission limitation without a second client instance.

### `expand` tree shape

**OpenFGA's `Expand` can return a leaf that is an unresolved pointer to another object's
relation (`tupleToUserset`), requiring a second, recursive call to actually resolve.**
Live, on `document:1#writer` (`writer: [user] or owner from parent` -- the arrow through
`parent`):

```
$ curl -s http://localhost:8082/stores/$STORE_ID/expand -d '{"tuple_key": {"object": "document:1", "relation": "writer"}}'
{
  "tree": {"root": {"name": "document:1#writer", "union": {"nodes": [
    {"name": "document:1#writer", "leaf": {"users": {"users": []}}},
    {"name": "document:1#writer", "leaf": {"tupleToUserset": {
      "tupleset": "document:1#parent",
      "computed": [{"userset": "folder:1#owner"}]
    }}}
  ]}}}
}
```

The second branch's leaf is not a set of users -- it names `document:1#parent` (a *different*
relation on the *same* object) and says "follow that, then look up `owner` on whatever it
points to." Resolving it requires issuing a **second** `Expand` call by hand:

```
$ curl -s http://localhost:8082/stores/$STORE_ID/expand -d '{"tuple_key": {"object": "folder:1", "relation": "owner"}}'
{"tree": {"root": {"name": "folder:1#owner", "leaf": {"users": {"users": ["user:bob"]}}}}}
```

**SpiceDB's tree has no node kind that corresponds to `tupleToUserset` at all -- the whole
tree is resolved server-side in one call, always.** `PermissionRelationshipTree`
(`core.proto:181-230`) is a `oneof` of exactly two kinds: `AlgebraicSubjectSet` (`union`/
`intersection`/`exclusion`, an intermediate node with `children`) and `DirectSubjectSet` (a
leaf, always a flat `repeated SubjectReference` -- concrete, resolved subjects, never a
pointer). Live, the equivalent `writer` permission, one call:

```
$ grpcurl -plaintext -H "authorization: Bearer $TOKEN" -d '{"resource":{"objectType":"document","objectId":"1"},"permission":"writer"}' localhost:50799 authzed.api.v1.PermissionsService.ExpandPermissionTree
{
  "treeRoot": {"intermediate": {"operation": "OPERATION_UNION", "children": [
    {"leaf": {}, "expandedObject": {"objectType":"document","objectId":"1"}, "expandedRelation": "direct_writer"},
    {"intermediate": {"operation": "OPERATION_UNION", "children": [
      {"leaf": {"subjects": [{"object": {"objectType":"user","objectId":"bob"}}]},
       "expandedObject": {"objectType":"folder","objectId":"1"}, "expandedRelation": "owner"}
    ]}, "expandedObject": {"objectType":"document","objectId":"1"}, "expandedRelation": "writer"}
  ]}, "expandedObject": {"objectType":"document","objectId":"1"}, "expandedRelation": "writer"}
}
```

The `parent->owner` arrow is already walked; the leaf under it is `user:bob`, a concrete
subject, in the **same** response. **Any consumer that walks an OpenFGA expand tree looking
for `leaf.tupleToUserset` and recursing on it needs that entire branch deleted, not
renamed** -- the SpiceDB tree it is walking instead is already fully resolved, and code that
expects a second round trip will simply never find the node kind it is checking for. Both
`ExpandPermissionTree`'s Go/Python/TypeScript signatures return the whole `TreeRoot`/tree
object synchronously (`client-api-surface.json`; Go: `client/expand.go:22`), so this is not
a streaming-vs-buffered distinction either -- it is a genuine shape difference in what the
tree can contain.

### `readChanges` → watch

**A paged poll scoped to one object type becomes a server-push stream keyed by revision, and
three specific capabilities do not carry over.** OpenFGA side, live -- capture a token,
perform a write, resume from the token, scoped to `type: "document"`:

```ts
const before = await fga.readChanges({ type: "document" }, { pageSize: 100 });
const token = before.continuation_token;
await fga.write({ writes: [{ user: "user:carol", relation: "viewer", object: "document:1" }] });
const after = await fga.readChanges({ type: "document" }, { pageSize: 100, continuationToken: token });
```
```
captured continuation_token: MDFNMDFCMDkxSk5IV0U1NVdXS1hWN0FIVzl8ZG9jdW1lbnQ=
changes since token: [
  {
    "tuple_key": {"user": "user:carol", "relation": "viewer", "object": "document:1", "condition": null},
    "operation": "TUPLE_OPERATION_WRITE",
    "timestamp": "2026-08-14T23:54:47.879817513Z"
  }
]
new continuation_token: MDFNMDFCNjc2N0ExTkdUMjI5MkFKOFZUSEd8ZG9jdW1lbnQ=
```

SpiceDB side, live -- a real push stream, subscribed once, that delivers a write made from a
completely separate call while it's open:

```ts
const startRev = await client.write(txn);  // a real ZedToken, not a wall-clock time
for await (const event of client.watch({ objectTypes: ["document"], startRevision: startRev })) {
  console.log("watch event:", JSON.stringify(event));
  break;
}
// ... concurrently, from a different call: client.write(txn2) ...
```
```
starting watch from revision: Gh8KEzE3ODY3NTE3MDY2NzE2NjcyNTgSCDBmOTdkYzZl
wrote follow-up relationship at revision: Gh8KEzE3ODY3NTE3MDY5ODM2NDQwOTISCDBmOTdkYzZl
watch event: {"changes":[{"operation":"touch","relationship":{"resourceType":"document","resourceId":"1","resourceRelation":"direct_viewer","subjectType":"user","subjectId":"erin"}}],"revision":"Gh8KEzE3ODY3NTE3MDY5ODM2NDQwOTISCDBmOTdkYzZl","schemaUpdated":false,"isCheckpoint":false}
```

Three specific things need rework, not renaming:

- **Resumption token type.** OpenFGA's `continuation_token` is an opaque string round-tripped
  through a request/response pair (`ReadChangesRequest.continuation_token`,
  `openfga_service.proto:1701-1706`). SpiceDB's `startRevision` (Go: `Updates(ctx, objectTypes,
  startRevision)`; TypeScript: `watch({startRevision})`) is a real `ZedToken`, obtained from
  a prior write's own return value or a previous stream event's `revision` field -- there is
  no "call once to get a token with nothing else happening" shape; you need a revision from
  an actual write or read.
- **`start_time`.** `ReadChangesRequest.start_time` (`openfga_service.proto:1708-1716`) is an
  ISO-8601 wall-clock timestamp -- "changes since 3 days ago." `WatchRequest` has no
  timestamp field anywhere; the only starting point is a revision token. A call site that
  resumes by wall-clock time (rather than a token it saved) has no direct equivalent -- it
  needs a revision from around that time, which only exists if something already recorded
  one.
- **Type filtering's shape, not its presence.** OpenFGA's `type` (`openfga_service.proto:
  1690-1692`) is a single string, one type per call. SpiceDB's `optionalObjectTypes` (the
  field backing Go's `objectTypes []string` / TypeScript's `objectTypes?: string[]`) takes a
  **list** -- trivial to adapt for the single-type case (wrap it), but a call site that reads
  the OpenFGA field name and assumes "one type in, one type out" will misread the parameter
  shape if it copies types mechanically.

The transport model itself also changes, independent of the three items above: a
request/response poll loop rewritten as a long-lived stream consumer is a structural change
to the calling code, not a substitution inside an existing loop body.

### Source-system names in configuration, and why they are not this phase's business

A converted project is left holding names that reference the system it is leaving:
configuration keys (`authorization.openfga.store.id`), a driver or backend identifier
(`"openfga"`), environment variables, feature flags, an enum constant, a field in a public API
response. **Leave every one of them alone, and say that you did.**

They are not conversion targets. Renaming a config key is a breaking change to every
deployment's configuration file and to any operator tooling that writes it; renaming a value
that appears in a public API response is a breaking change to that project's own consumers.
Neither is mechanical, neither is reversible by re-running this command, and neither has
anything to do with whether authorization decisions come out right -- which is the whole of
what this phase is responsible for. A conversion that quietly renames them turns a
behavior-preserving change into one that breaks on deploy, in a diff the reviewer is reading
for call-site correctness.

Record them instead: list the source-system names still present, with `file:line`, under
**Deferred / manual -> For the record** in `migration-plan.md`. That is a product decision
with a real migration path of its own (a deprecation window, a config alias, a release note),
and it belongs to the people who own the project's compatibility promises. If the user asks
for the rename explicitly, it is ordinary work -- do it then, as its own change, not folded
into the conversion.

### `writeAuthorizationModel` -- where the schema text comes from

The call maps to `WriteSchema`, and the shape is a direct rename: both take the complete
schema as one string. **What the table cannot tell you is what that string should be now.**
The source argument was the authorization model -- often an embedded JSON literal, or a
constant generated from a `.fga` file by a build rule -- and the converted call needs
`schema.zed`'s text instead. Neither is a rewrite of the other; the model form is dead once
phase 1 has run.

Follow the source's own provenance rather than inventing a new one:

- **Generated from a build rule** (a `Makefile` target, a `go:generate` line): repoint the
  rule at `schema.zed` and leave the generated constant in place. The call site then does not
  change at all beyond the method name, and the project keeps one mechanism for getting
  schema text into the binary. This is the least disruptive answer and usually the right one.
- **A checked-in literal with no generator**: replace the literal's contents with
  `schema.zed`'s text and note in the marker that the two must be kept in step, or introduce
  an embed (`go:embed`, a resource file) pointing at `schema.zed`. Say which was chosen.
- **Read from a file at runtime**: repoint the path at `schema.zed`.

In all three, the source's model file itself is now a migration input, not a live artifact.
Do not delete it in this phase -- phases 2 and 5 and `/spicedb-dev:migrate-verify` may still
read it -- and say so in the marker rather than leaving a reader to guess whether the stale
copy is still authoritative.

### `check` with a wildcard subject

OpenFGA accepts `*` as the **subject** of a check -- `check(user:*, viewer, document:1)`, read
as "has this been granted to everyone?" **SpiceDB rejects this outright.** `*` is a
relationship subject, valid in a write and in a schema type list, never the subject of a
`CheckPermission`; there is no "is there a public grant" check RPC. A call site doing this is
not a rename, and it is not one of the no-target operations either -- it has a target, but
which target depends on the relation's shape.

**Read the source `define` for the relation being checked, and branch on it:**

1. **Pure-direct relation** -- the `define` lists only directly-assignable types
   (`define viewer: [user, user:*]`), with no `or`, `and`, `but not`, or arrow term. Then the
   source check degenerates to tuple existence, and the faithful conversion is a
   `ReadRelationships` existence read for `(resource, <relation>, <subject type>:*)` --
   one relationship back means the grant is present. Target the **relation** side of the
   split (`relation_splits[T][R].relation`), not the permission: this is a relationship
   filter, and "The relation-split obligation" below governs which name goes in that
   position.
2. **Anything else** -- the `define` carries a union, intersection, exclusion, or arrow. The
   existence read is **not** equivalent, and substituting it is a silent behavior change: the
   source check could return true by resolving through a computed term that no single tuple
   represents, and a tuple read cannot see any of that. Treat it as a **Class A finding** and
   put it to the user with the same batching discipline the non-transactional-writes fork
   uses. Do not convert it on the assumption that case 1's rewrite generalizes -- it does not.

In both cases leave a `TODO(spicedbmigration):` marker naming which branch was taken, per
`findings-report.md`'s "Inline markers" convention. Case 1's equivalence is a property of the
relation as it is defined **today**: if someone later adds an `or` term to that `define`, the
converted call silently stops matching the source's semantics, and the marker is what makes
that discoverable.

### Non-transactional writes

**OpenFGA's default `write()` is already atomic and maps cleanly to SpiceDB's `Write` --
the divergence is scoped to the explicit `transaction.disable: true` opt-in only.** Verified:
the same mixed valid/invalid batch, through the **default** (transactional) OpenFGA `write`,
fails wholesale with nothing landing:

```ts
const result = await fga.write({
  writes: [
    { user: "user:henry", relation: "viewer", object: "document:6" },
    { user: "folder:1", relation: "viewer", object: "document:6" },  // invalid
  ],
});  // default: transaction.disable is false
```
```
default (transactional) write threw: FgaApiValidationError - FGA API Validation Error: post
write : Error Invalid tuple 'document:6#viewer@folder:1'. Reason: type 'folder' is not an
allowed type restriction for 'document#viewer'
$ fga tuple read --store-id $STORE_ID --object document:6 --output-format simple-json
[]
```

**With `transaction.disable: true` and `maxPerChunk`, the same call splits into independent
chunks and returns a per-tuple `{status, err}` result -- partial success, by design:**

```ts
const result = await fga.write(
  { writes: [
      { user: "user:frank", relation: "viewer", object: "document:2" },   // valid
      { user: "folder:1", relation: "viewer", object: "document:2" },     // invalid
      { user: "user:grace", relation: "viewer", object: "document:2" },   // valid
  ]},
  { transaction: { disable: true, maxPerChunk: 1 } },
);
```
```
writes results:
[
  {"tuple_key": {"user":"user:grace","relation":"viewer","object":"document:2"}, "status": "success"},
  {"tuple_key": {"user":"user:frank","relation":"viewer","object":"document:2"}, "status": "success"},
  {"tuple_key": {"user":"folder:1","relation":"viewer","object":"document:2"}, "status": "failure", "err": {...}}
]
$ fga tuple read --object document:2
[{"object":"document:2","relation":"viewer","user":"user:frank"},
 {"object":"document:2","relation":"viewer","user":"user:grace"}]
```

Two of three landed durably despite the third failing. **`WriteRelationships` (`Write`/
`write` on every SpiceDB client) has no equivalent mode -- it is always one transaction, full
stop.** The identical mixed batch through SpiceDB's `Transaction`/`Txn`:

```ts
const txn = new Transaction()
  .touch({ resourceType: "document", resourceId: "2", resourceRelation: "direct_viewer", subjectType: "user", subjectId: "frank" })
  .touch({ resourceType: "document", resourceId: "2", resourceRelation: "direct_viewer", subjectType: "folder", subjectId: "1" })  // invalid
  .touch({ resourceType: "document", resourceId: "2", resourceRelation: "direct_viewer", subjectType: "user", subjectId: "grace" });
await client.write(txn);
```
```
Write rejected the whole transaction: InvalidArgumentError - [invalid_argument] subjects of
type `folder` are not allowed on relation `document#direct_viewer`
$ zed relationship read document:2 direct_viewer
(empty -- nothing landed, not even the two valid ones)
```

**A call site using `transaction.disable`/`maxPerChunk` cannot be converted mechanically.**
The options are: (a) issue one SpiceDB `Write` per relationship instead of one batch call,
recovering partial-success semantics at the cost of N round trips instead of one, or (b) keep
one batched `Write` and accept that a single bad relationship now fails the whole group,
which is a **behavior change**, not a bug, if the source code relied on partial success (e.g.
"load as many of these as are valid, log the rest"). Either choice is a Class A/B decision
for `migration-plan.md`, not something to pick silently.

### Per-language check-signature divergence

**Check code cannot be ported across languages mechanically -- confirmed for two
independent, structurally different reasons, each already fully worked out with live
transcripts in `spicedb-client-integration`'s own per-language references. Cited here rather
than re-derived**, per this file's own scope statement above:

- **Python's `check_permission` has no `permission` parameter at all.**
  `check_permission(self, consistency, rel, *, context=None)` (`spicedb-python/spicedb/
  client.py:107`) reads the permission being checked from `rel.resource_relation` --
  every other language takes it as its own explicit argument. Full live transcript (the
  *same* resource/subject pair, changing only `resource_relation`, `view` → `True`, `edit` →
  `False`): `spicedb-client-integration/references/python.md`, "Checks -- the permission
  divergence."
- **Rust's `check_permission` (singular) returns a `CheckResult { has_permission: bool }`
  wrapper, not a bare `bool`.** `client.rs:121-133` -- `#[must_use] CheckResult` must be
  unwrapped via `.has_permission`; the **plural** `check_permissions` returns a plain
  `Vec<bool>` in the same file, so the wrapper is specific to the singular form. This is
  the exact fact `client-api-surface.json`'s `source_line` field cannot show (Rust's
  signature is multi-line; the JSON's truncation-at-open-paren drops everything after
  `pub async fn check_permission(`, including the return type) -- confirmed by reading
  `client.rs` directly, not the JSON. Full live transcript:
  `spicedb-client-integration/references/rust.md`, "Checks."

A table, not a re-derivation, of what a migrating agent needs to know before copying check
code from one target language to another:

| Language | Permission argument | Single-check return type | SpiceDB target sync or async |
|---|---|---|---|
| Go | explicit `string` | `bool` | sync -- `CheckOne` blocks and returns `(bool, error)` directly (`go.md`, `checks.go:48`) |
| Python | **read from `Relationship.resource_relation`** | `bool` | **async-only** -- every method is `async def`, no sync client exists (`python.md`, `client.py:107`) |
| TypeScript | explicit field on `CheckRequest` | `bool` | async-only -- `Promise<boolean>` (`typescript.md`, `client.ts:103`) |
| C# | explicit `string` | `bool` | async-only -- `Task<bool>` (`csharp.md`, `SpiceDBClient.cs:142`) |
| Java | explicit `string` | `bool` | sync -- `checkPermission` blocks and returns `boolean` directly (`java.md`, `SpiceDBClient.java:125`) |
| Rust | explicit `&str` | **`CheckResult` wrapper** (`.has_permission`) | async-only -- `Result<CheckResult, SpiceDBError>` behind `.await` (`rust.md`, `client.rs:121`) |
| Ruby | explicit positional arg | `bool` | sync -- `check_permission` blocks and returns directly (`ruby.md`, `client.rb:115`) |

Every language's own `references/<lang>.md` file in `spicedb-client-integration` has the
authoritative signature, a live-verified sample, and (for Go and Rust) the streaming/
buffering and error/retry facts that also differ by language and matter once you're
converting more than a single check call.

### Async-only target vs. sync source: the un-awaited-coroutine fail-open

**Python is the one language of the seven where a forgotten `await` on a converted check
silently answers "allowed."** The mechanism is specific to Python, not a generic
"async is risky" warning -- it takes three things being true together, and Python is the
only language in this pack's corpus where all three actually hold:

1. **The OpenFGA source can be genuinely synchronous.** `openfga_sdk.sync.OpenFgaClient`
   (cited above, "Detecting the source shape") is a real, documented sync client -- a call
   site written against it has no `await` anywhere to begin with.
2. **The SpiceDB target has no sync option.** The table above shows it: the prototype
   `spicedb-python` client is async-only, so the conversion of every call site necessarily
   *adds* an `await` that was not there in the source.
3. **A bare, un-awaited coroutine is truthy.** Python raises no error constructing one --
   `client.check_permission(...)` without `await` returns a live
   `coroutine` object immediately, and every object is truthy in an `if` unless it defines
   `__bool__`/`__len__` to say otherwise, which a coroutine does not.

Verified live end to end, against `spicedb serve-testing v1.56.0` and the prototype client's
real `check_permission` (`client.py:107`), a subject (`user:mallory`) with **no `viewer`
relationship written at all**:

```
=== Correct usage: awaited check_permission ===
awaited result = False (type: bool)
  -> takes DENIED branch (correct: mallory has no viewer relationship)

=== Bug: forgot `await` on check_permission ===
unawaited result = <coroutine object SpiceDBClient.check_permission at 0x10732a5c0> (type: coroutine)
bool(unawaited_result) = True
  -> takes ALLOWED branch  <-- FAIL OPEN: mallory has NO grant, yet the `if` treats her as allowed

(for reference: the coroutine's real resolved answer, once awaited, is False)
```

**Confirmed invisible to Python's own static type checkers, not just to an untyped
codebase** -- run against the identical fully-annotated repro (`async def check_permission()
-> bool`, called bare inside `if`): `pyright` reports `0 errors, 0 warnings, 0 informations`;
`mypy --strict` reports `Success: no issues found in 1 source file`. Neither tool models "a
bare coroutine used as a condition is always true" as a diagnosis, so step 7's build check
does not catch this even when the target project runs one of them in strict mode -- "untyped
Python" understates the gap.

**The other six languages do not share this shape**, checked live/mechanically rather than
assumed from the async-vs-sync label alone:

- **Go, Java, Ruby: no hazard, because the SpiceDB target is synchronous.** There is no
  `await` step to omit -- `CheckOne`/`checkPermission`/`check_permission` block and hand back
  a `bool` (or `(bool, error)`) on the same line they're called from. A source client's own
  sync-vs-async shape is irrelevant here because the target never offers an async form to
  under-use.
- **C# and Rust: the async target exists, but a missing `await`/`.await` fails to *compile*,
  not fails open.** Reproduced live with the minimal shape (an `async` function returning
  `bool`, called bare inside `if`, same as the Python repro above):
  - C# (`dotnet build`, .NET 8): `error CS0029: Cannot implicitly convert type
    'System.Threading.Tasks.Task<bool>' to 'bool'`.
  - Rust (`cargo build`, tokio): `error[E0308]: mismatched types ... expected 'bool', found
    future`, with the compiler's own suggested fix already naming `.await`.

  Neither language allows an arbitrary type as an `if` condition, so the exact bug shape that
  reaches production silently in Python is rejected at every build in these two.
- **TypeScript: the same runtime mechanism exists (a bare `Promise` is truthy in JS), but two
  things narrow it relative to Python.** First, `@openfga/sdk` has no sync client at all --
  every OpenFGA JS call already requires `await`, so there is no sync-calling-convention
  habit for a converted call site to carry over in the first place; the trap needs a
  synchronous source to spring from, and TypeScript's OpenFGA source never offers one.
  Second, unlike Python's type checkers, `tsc` itself flags the bare case even at default
  (non-strict) settings -- reproduced live with the same minimal shape (`async function
  checkPermission(): Promise<boolean>`, called bare inside `if`):
  ```
  check.ts(7,7): error TS2801: This condition will always return true since this 'Promise<boolean>' is always defined.
  ```
  That diagnostic only fires when the build actually runs `tsc`'s type checker -- a
  transpile-only pipeline (`ts-node --transpile-only`, esbuild, a bundler's strip-only mode)
  does not type-check and would not catch it, which is exactly why step 7 of
  `/spicedb-dev:migrate-code` requires a real type-check step rather than accepting "the code
  transpiles" as sufficient. With that real step in place, TypeScript is self-defending here;
  Python is not, at any strictness level.

**Net scope: document and guard this for Python specifically.** Do not generalize "async
targets are risky" to the other three async-capable languages (C#, Rust, TypeScript) --
two reject the bug at compile time unconditionally, and the third both lacks the sync-source
precondition and rejects it under a normal type-checked build. See
`/spicedb-dev:migrate-code`'s trap list for the step 6/7 guidance this drives: any Python
call site converted from a sync OpenFGA client must be reviewed for `await` by inspection,
because neither the build check nor a strict type checker will find a missing one.

### The synchronous-caller bridge, and a second-order deadlock in the obvious version of it

**A converted call site is not always free to become `async def` itself.** A Flask view
function, a Django view, a Celery task defined as a plain `def` -- the calling framework
owns the function signature, and the call site cannot simply add `await` the way the trap
above assumes; there is no enclosing coroutine to run it in. The standard bridge for this
shape is a **persistent background thread running its own event loop**, with every sync call
site dispatching onto it via `asyncio.run_coroutine_threadsafe(coro, loop).result()` --
schedule the coroutine onto the loop's thread from the calling (non-loop) thread, block the
calling thread until it resolves, return a plain value to code that never sees an `await`.
This pattern is not particular to SpiceDB's client; it is the general answer to "call an
async-only library from synchronous code," and a project migrating from a synchronous
OpenFGA client onto this pack's async-only Python target (above) will reach for it.

**The obvious way to add lazy, cached construction to that bridge deadlocks, and the failure
is silent-total, not partial.** A dispatcher that lazily builds and caches the SpiceDB client
(or any other shared async resource) *from inside a coroutine already running on that same
background loop* -- by calling the same `run_coroutine_threadsafe(...).result()` helper a
second time, from within code the first call already scheduled onto the loop -- hangs
outright: `.result()` blocks the calling thread, but the calling thread in this nested case
*is* the loop's own thread, so nothing can ever run the newly scheduled coroutine to produce
the result being waited on. Every call through the dispatcher then times out at the
configured deadline with no exception pointing at the cause, because from the outside this
looks identical to a slow or unreachable SpiceDB, not a self-deadlock in the calling code.

**Verified directly**, with a minimal reproduction isolating the hazard from any SpiceDB
client specifics (the deadlock is in the bridging pattern itself, not in `spicedb-python`):
a coroutine already executing on a persistent background loop (having itself been dispatched
there via `run_coroutine_threadsafe`) that lazily resolves a cached resource by calling
`run_coroutine_threadsafe(build(), loop).result()` again, from inside itself, blocks until a
2-second timeout and never completes. Moving the identical lazy-construction call to run on
the *calling* thread -- resolving and caching the shared resource before any coroutine is
scheduled onto the loop, never from code already running on it -- completes in under 20ms
against the same setup. The fix is structural, not tuning: **resolve any shared resource a
loop-dispatched coroutine will need before scheduling that coroutine, on the thread doing the
scheduling; never call the loop-dispatch helper again from inside code the helper already
dispatched.**

This is the same *category* of hazard as a naive per-call `asyncio.run()` on a synchronous call
site -- "the obvious-looking fix is empirically broken" -- but one level deeper: this trap is in
the bridge's own lazy-initialization path, not in the call site using it, and it was found
independently, in the wild, by a project that had already built the background-loop bridge for
its own converted call sites and then hit this exact deadlock building a second dispatcher (a
differential-harness dual-run dispatcher, per `/spicedb-dev:migrate-verify`) on top of the same
bridge. Any dispatcher built on this pattern -- including a harness dispatcher this pack's own
`/spicedb-dev:migrate-verify` guides an implementer to build, off the critical path, on "its own
goroutine/thread/task/queue" -- inherits this hazard if it adds caching or lazy construction to
the bridge without resolving it on the calling thread first, before any coroutine needing it is
scheduled onto the loop.

## Consistency

OpenFGA's `ConsistencyPreference` enum (`apiModel.d.ts:441-445`) has exactly three values.
**For a check that does not depend on a write the same request path just made**, the mapping
is a clean rename:

| OpenFGA | SpiceDB |
|---|---|
| `HIGHER_CONSISTENCY` | `full()` |
| `MINIMIZE_LATENCY` | `minLatency()` |
| `UNSPECIFIED` (the default) | `minLatency()` |

The italicized condition is load-bearing, not framing: an earlier version of this table carried
no such condition, and a project converted under it failed on every request of its ordinary
create-then-check pattern. "Read-after-write checks," below, is the rule this table does not
cover.

Verified live, same check, all three OpenFGA values, then the SpiceDB equivalents -- with no
write immediately ahead of the check, so this confirms the helper names and that all three
answer correctly at rest, nothing about read-after-write freshness:

```
HIGHER_CONSISTENCY: bob can view document:1 = true
MINIMIZE_LATENCY: bob can view document:1 = true
UNSPECIFIED (default): bob can view document:1 = true
```
```ts
const fullResult = await client.checkPermission(full(), req);
const minLatResult = await client.checkPermission(minLatency(), req);
```
```
full(): bob can view document:1 = true
minLatency(): bob can view document:1 = true
```

**But OpenFGA's `CheckRequest` (`apiModel.d.ts:270-306`) has no revision/token field
anywhere -- `tuple_key`, `contextual_tuples`, `authorization_model_id`, `trace`, `context`,
`consistency`, and nothing else.** There is no zookie: the only freshness lever OpenFGA's API
gives a caller is the blunt two-state choice above (bypass the cache entirely, or accept
whatever the cache has). Read-your-writes -- "I just wrote this, guarantee my next check
sees it, without paying full cache-bypass cost on every other check too" -- is a capability
**the source codebase never had access to**, not a feature this conversion downgrades. That a
source never had a capability does not make its absence safe to reproduce, though -- see below.

SpiceDB's revision-threading consistency helpers make read-your-writes cheap once a client has
a real token to thread, because every write already returns one:

```ts
const rev = await client.write(txn);  // a real ZedToken from the write itself
const readYourWrite = await client.checkPermission(atLeastOrFull(rev), req);
```
```
atLeastOrFull(rev) immediately after the write that granted it = true
```

### Read-after-write checks: the literal mapping is the wrong default, verified live

**A check whose answer depends on a write the same request (or the request just before it)
made is a different case than the table above, and the literal mapping is wrong for it.** This
was found by building a real Go service on an earlier version of this guidance, not by
reasoning about it: create a resource, grant ownership on it, and the very next HTTP request
checks a permission derived from that grant. Every request in that ordinary pattern came back
denied.

**Verified directly** (`spicedb serve-testing` v1.56.0, 150 trials, write immediately followed
by a check on the relationship it just wrote, no delay inserted): a `minLatency()` check fired
right after the `Write` that granted the permission it checks returned the stale (pre-write,
denied) answer in 140 of 150 trials. The identical sequence, with the check instead threading
the ZedToken the `Write` call returned, returned the correct (granted) answer in all 150
trials -- 0 stale. `differential-harness.md`'s own independent measurement of the same effect
(143/150, 95.3%, at a ~360µs write-to-check gap, decaying to 0% by a 5ms gap) matches this --
two separate measurements of the same underlying behavior, not one harness's fluke. Two
consecutive HTTP requests -- the ordinary shape of "create, then check what was just
created" -- sit well inside that window.

**The rule, in order:**

1. **Thread the ZedToken.** Capture the revision the write returned and pass it as the check's
   consistency (`AtLeast(rev)` / `at_least(rev)` / `atLeast(rev)`, or the `atLeastOrFull(rev)`
   shown above when the revision may legitimately be empty -- see "Which `atLeastOr*` helper,"
   below). This is the correct answer and the first thing to reach for wherever a call site can
   carry a revision from its write to its dependent check: a function return, an HTTP response
   header round-tripped by the client, a session, a stored column -- `consistency-deep-dive.md`'s
   ZedToken-routing pattern covers the mechanics.
2. **If a ZedToken cannot be obtained or threaded at this call site at all, use `full()`
   (`fully_consistent`/`fullyConsistent`), and leave a `TODO(spicedbmigration):` marker there**
   naming the switch to a threaded ZedToken as the follow-up -- two lines maximum, per
   `findings-report.md`'s "Inline markers" convention (cited there, not restated here).
   `full()` bypasses SpiceDB's cache and is materially more expensive per call than
   `minLatency()` -- it is the safe choice for this call site, not the free one, which is exactly
   why the marker exists: so a human revisits it once threading becomes possible, rather than it
   staying on `full()`'s cost forever unexamined.

   **How often this branch fires depends on which client commit the project vendors -- establish
   that before assuming it is the common case.** At the pinned commit, none of the seven
   prototype clients exposes a check response's revision token through its idiomatic wrapper at
   all (`migrate-verify.md`'s step 3, "Confirming the client 'exposes' a check call by method
   name is not sufficient" -- verified against all seven client source trees at that commit), so
   a call site written against that surface, with no revision already in hand from a nearby
   write, has no token to thread rather than merely an inconvenient one, and this branch is then
   the common case. Upstream `main` has since exposed `checked_at` on the check surface (and
   `looked_up_at` on lookups) in all seven, which makes rule 1 reachable from the check response
   itself and this branch rare. Defaulting to `full()` against a client that does expose the
   token gives up the cache on every call and buys nothing.
3. **Never `minLatency()` on a read-after-write path.** OpenFGA never offered read-your-writes
   either, but the literal `MINIMIZE_LATENCY`/`UNSPECIFIED` mapping preserves that *absence of a
   capability*, not "an occasionally stale answer" -- a dependent check under it answers wrong on
   the overwhelming majority of the requests that matter (measured above). Keep the literal
   mapping (table, above) only for checks that do **not** depend on a preceding write, where it
   genuinely is faithful and cheap.

### Which `atLeastOr*` helper

When step 1's revision might legitimately be empty (the first check of a session, a call site
with no nearby write to thread from), reach for **`atLeastOrFull(rev)`, not
`atLeastOrMinLatency(rev)`** -- correcting this pack's own earlier guidance, which recommended
the latter for every OpenFGA-sourced conversion. The two differ only in what they do when the
revision string is empty: `atLeastOrFull` falls back to `full()`, mechanically producing step
2's safe fallback from a single call; `atLeastOrMinLatency` falls back to `minLatency()` -- step
3's now-forbidden choice, reintroduced through the back door of "no revision this time."
`core-concepts.md`'s consistency table's general preference for `atLeastOrMinLatency` ("prefer
this over `AtLeastOrFull` unless you specifically need the strict fallback") does not override
step 3 above: a read-after-write check with no revision in hand is exactly the case that needs
the strict fallback. Both helpers are real in every language (`atLeastOrMinLatency` /
`at_least_or_min_latency` / `AtLeastOrMinLatency`, and their `...OrFull` counterparts); where a
revision is actually in hand the two are identical, so this choice is only about the fallback
path -- and it only matters for a call site that cannot guarantee a revision is always present
(a call site that can never obtain one at all is step 2's hardcoded-`full()`-plus-marker case,
not this one).

### The same rule governs lookups, and the failure there is worse

Everything in this section is written about `check`, but **`LookupResources` and
`LookupSubjects` take the identical three-step rule**, and a stale answer costs more, not
less. A stale check denies one object; a stale lookup returns a **short or empty list**, and
the usual consumer turns that into a membership test -- `slices.Contains(results, x)`,
`if not results: deny` -- so an empty list is a blanket denial of *every* object of that type,
not a wrong answer about one. Verified live: a `LookupResources` under `minLatency()`
immediately after writing the granting relationship returned an empty list, and the same call
under `full()` returned the resource.

Write-then-list is a common shape, not an exotic one -- any "grant access, then show me what
I can see" path has it, and a reconciliation loop that diffs a lookup's answer against local
state has it by construction. Treat `lookupResources`/`lookupSubjects` exactly as a dependent
check: thread the ZedToken from the write, else `full()` with a marker, and never
`minLatency()` on a path whose input is something this request just wrote.

### A per-call-site classification, not a project-wide toggle

**Whether a check may use the literal mapping is decided per call site -- dependent on a
preceding write, or not -- never as one project-wide choice between "literal mapping" and
"thread revisions."** A call site with no preceding write in its own request path (an ordinary
permission check with no grant just ahead of it) takes the literal, faithful `minLatency()`
mapping; a call site whose answer depends on a write earlier in the same request, or the
request immediately before it, takes the three-step rule above. Recording "consistency
strategy: literal mapping" or "consistency strategy: thread revisions" as a single blanket
decision for the whole conversion misclassifies every call site on the wrong side of that
split; threading revisions everywhere is nearly free once a call site already has one in hand
from a nearby write, but it is not the axis the decision actually turns on, and treating it as
the axis is how the wrong default shipped in the first place.

**When one call site serves both kinds of caller, classify by caller, not by site.** The
common shape is a single generic helper -- middleware, an authorizer interface, a
`CheckPermission(ctx, resource, subject, permission)` used by every handler in the codebase --
where "is there a preceding write in this request path?" has no answer *at the site*, because
the site serves requests that do and requests that do not. A real production authorization driver observed during this pack's development is exactly this. Do
not resolve it by picking whichever answer the site's own body suggests; the site's body does
not know. Instead, in order of preference:

1. **Parameterize the helper.** Add a consistency argument (or an `atLeastAsFresh(token)`
   variant alongside the existing method) and let each caller pass what its own path needs.
   This restores the per-call-site decision at the level where the information actually
   exists, and is the only option that keeps `minLatency()` for the traffic that can have it.
2. **If the helper cannot be parameterized in this change, it takes the strictest requirement
   of any of its callers.** A shared helper is as consistent as its most demanding caller
   needs, because the alternative answers some requests wrongly. In practice that means the
   three-step rule above, and where no token can be threaded, `full()` -- with a
   `TODO(spicedbmigration):` marker naming option 1 as the follow-up, because the cost is a
   cache bypass on *every* request through that helper, not just the dependent ones.

Say which of the two was taken, and for option 2, say plainly in the report that a
project-wide `full()` was the consequence of a shared helper rather than a considered default
-- it is the kind of cost a reader should see attributed, not discover in a profile.

## The relation-split obligation

**Every converted call site must target the generated *relation* on the write path and the
*permission* on the check path, and `migration-map.json`'s `relation_splits` is the only
place that records which name is which.** This is the exact structural analogue of "The
identifier obligation" below: a `migration-map.json`-driven rewrite phase 1 already decided,
invisible to a compiler and to `zed validate`, that fails at **runtime** -- loudly on some
call shapes and silently on others. Neither obligation is optional and neither is caught by
step 7's build check.

**What phase 1 did.** A source `define` fusing a `[...]` type list with an operator
(`define viewer: [user, group#member] or writer`) cannot become a single SpiceDB name: a
SpiceDB relation holds stored data and a permission is computed, and one name cannot be
both. Phase 1 splits it into a generated **relation** (default suffix `__direct`) plus a
**permission** that keeps the source's original name (`schema-mapping.md`, "The relation/
permission split"), and records the pairing:

```json
"relation_splits": {
  "document": { "viewer": { "relation": "viewer__direct", "permission": "viewer" } }
}
```

Both fields are named explicitly rather than implied by a suffix precisely so that no
consumer has to guess which side of a relationship it is on
(`migrating-to-spicedb/references/findings-report.md`'s `relation_splits` section).
**Read the name out of the map; never construct it by appending `__direct`** -- the suffix
is a gate decision (`/spicedb-dev:migrate` step 5, row 4, offers a project-specific one),
and a hardcoded suffix silently diverges from the schema phase 1 actually emitted.

**"Read it out of the map" is a rule about provenance, not about reading JSON at runtime.**
In Python, Ruby, or TypeScript a call site can load `migration-map.json` directly. A compiled
language cannot: Go, Java, C#, and Rust have no way to consult a JSON file at compile time,
and making every call site parse it at startup adds a failure mode -- and a file dependency
in production -- that the source code never had. For those languages, **generate a constants
module from `migration-map.json` and have call sites reference its symbols**: one exported
constant per check target and per write target, emitted alongside the ID codec, with a
header naming `migration-map.json` as its source and stating it is generated. That satisfies
the rule -- the names still come from the map, and regenerating after a map change is a
mechanical step whose diff is reviewable -- while a call site typing `"viewer__direct"`
inline does not, however correct that string happens to be today. Record the generated
module's path in `phase_status["4"].artifact` the same way the codec's path is recorded.

**The rule, per call surface.** For a source relation `R` on source type `T`:

| Call surface | Target name |
|---|---|
| Relationship **write** -- `Transaction`/`Txn` `.create()` / `.touch()` / `.delete()` | `relation_splits[T][R].relation` |
| Relationship **read** and **delete** filters -- `ReadRelationships`/`DeleteRelationships` `Filter`, and a `Watch`/`Updates` filter | `relation_splits[T][R].relation` |
| **Check** and **bulk check** -- `CheckOne`/`Check`, `check_permission`/`check_permissions`, `checkPermission`/`checkPermissions` | `relation_splits[T][R].permission` |
| **`LookupResources`** and **`LookupSubjects`** -- the permission argument | `relation_splits[T][R].permission` |
| **`ExpandPermissionTree`** | `relation_splits[T][R].permission` |
| **Subject side** of any of the above -- a userset subject `T#R` (`group:eng#member`), on a write as much as on a check | `permissions[T][R]` -- **never** `relation_splits[T][R].relation` |
| Any `R` **absent** from `relation_splits[T]` | unchanged: `permissions[T][R]`, one name for both surfaces |

The subject-side row is not an exception to be memorized separately -- it is the same rule
the data phase already applies to a stored tuple's subject side (`data-mapping.md`, "A
userset subject reference is the one place inside a write that names the *permission*, not
the relation"), and it follows from the schema: the type restriction phase 1 emitted names
the permission, so a userset naming the split relation is not an allowed subject type at all.

**The last row is the common case, and it is why every call site goes through the lookup
rather than a conditional.** `data-mapping.md`'s mechanical count over this pack's 39-store
corpus -- 88 split relations across 28 stores, 9.6% of all mapped relations, median 3.7% per
store -- is the same distribution phase 4 faces, because both phases read the same
`migration-map.json`. A rewrite that only consults `relation_splits` when a relation "looks
split" is a rewrite that has to guess; looking every relation up and falling through to
`permissions[T][R]` gets the un-split 90% right by the same code path that gets the other
10% right.

**How it fails, live.** `spicedb serve-testing` v1.56.0, `zed` v0.31.1, against the schema
phase 1 emits for `define viewer: [user] or owner from parent` -- `relation viewer__direct:
user`, `permission viewer = viewer__direct + parent->owner`, with `folder:1#owner@user:bob`
and `document:1#parent@folder:1` seeded:

```
$ zed relationship create document:1 viewer user:anne
InvalidArgument: cannot write a relationship to permission `viewer` under definition `document`

$ zed relationship read document:1 viewer
FailedPrecondition: relation `viewer` does not have type information

$ zed relationship delete document:1 viewer user:anne
InvalidArgument: cannot write a relationship to permission `viewer` under definition `document`

$ zed relationship create document:1 viewer__direct user:anne
Gh8KEzE3ODY3NjM4NjA0MDk1NzcwMDASCDhhZGEwZDY2

$ zed relationship read document:1 viewer__direct
document:1 viewer__direct user:anne

$ zed permission check document:1 viewer user:anne
true
```

**Two of the failure modes are loud; two are silent, and the silent pair is why a build
check and a smoke test can both pass on a broken conversion.**

- **Loud**, every time, on the first call: a write (or a `Transaction.delete()`, which is a
  write) naming the permission, and a `ReadRelationships` filter naming it. Nothing lands
  and nothing is read.
- **Silent, wrong answer**: a check or lookup left naming the *split relation* instead of
  the permission compiles, runs, returns no error -- and returns only the directly-granted
  subjects, dropping every path the permission's operator side contributes. Same store as
  above, where `user:bob` is a viewer solely through `parent->owner`:

  ```
  $ zed permission check document:1 viewer user:bob
  true
  $ zed permission check document:1 viewer__direct user:bob
  false

  $ zed permission lookup-resources document viewer user:bob
  1
  $ zed permission lookup-resources document viewer__direct user:bob
  (no results)
  ```

  This is the same silent narrowing `schema-mapping.md` names as the split's
  highest-consequence misapplication: SpiceDB does not reject checking a bare relation, so
  the wrong name is a valid question with a narrower answer.
- **Silent, no-op**: a `DeleteRelationships` filter naming the permission -- unlike the
  write path, it does not error **on the bulk/filter path shown here**. It reports success
  and deletes nothing. **This does not generalize to a transactional delete**: the same name
  in a `Txn.Delete`/`DeleteRelationships` update errors loudly and refuses the whole
  transaction (`cannot write a relationship to permission '<name>'`, verified live), the same
  as a write. So the hazard differs by path -- a filter delete is silently wrong, a
  transactional delete is noisily wrong -- and only the first is a silent no-op:

  **A third case exists and neither path handles it: a delete the converted schema cannot
  express at all.** If the source deletes a tuple whose subject is not in the target
  relation's type list -- a legacy wildcard cleanup like `server:main#viewer@user:*` where
  the conversion put `user:*` on a different relation, or none -- then *both* forms fail: the
  permission name errors as above, and the `__direct` name errors too, because `user:*` is not
  an allowed subject of it. There is no rewrite. Treat it as a Class A finding and put it to
  the user with the other batched ones: the real question is whether that cleanup is still
  needed after migration, which is a question about their data, not their code. Do not delete
  the call silently because no target exists, and do not leave it converted-but-erroring --
  and note that whether it *appears* to work depends on something incidental, namely whether
  the caller checks the returned error, so a discarded return (`_ =`) hides it completely.


  ```
  $ zed relationship bulk-delete document:1 viewer --force
  Gh8KEzE3ODY3NjM4NjA4NjcwODMwMDASCDhhZGEwZDY2
  $ zed relationship read document:1 viewer__direct
  document:1 viewer__direct user:anne          # still there
  ```

**Worked before/after**, TypeScript, `OpenFgaClient` source shape, against the
`relation_splits` entry above. On the OpenFGA side one name, `viewer`, serves both surfaces:

```ts
// BEFORE
await fga.write({ writes: [{ user: "user:anne", relation: "viewer", object: "document:1" }] });
const { allowed } = await fga.check({ user: "user:bob", relation: "viewer", object: "document:1" });
const { tuples }  = await fga.read({ object: "document:1", relation: "viewer" });
const { objects } = await fga.listObjects({ type: "document", relation: "viewer", user: "user:bob" });
```

```ts
// AFTER -- split = migrationMap.relation_splits.document.viewer
// BEFORE's write() left on_duplicate unspecified, which OpenFGA treats as
// "error" (not "ignore") -- see the `on_duplicate` table row above -- so the
// faithful verb here is .create(), not .touch()
await client.write(new Transaction().create({
  resourceType: "document", resourceId: "1",
  resourceRelation: split.relation,            // "viewer__direct" -- write path
  subjectType: "user", subjectId: encode("user", "anne"),
}));
const allowed = await client.checkPermission(minLatency(), {
  resourceType: "document", resourceId: "1",
  permission: split.permission,                // "viewer" -- check path
  subjectType: "user", subjectId: encode("user", "bob"),
});
for await (const r of client.readRelationships(
  { resourceType: "document", resourceId: "1",
    resourceRelation: split.relation },        // "viewer__direct" -- read filter
  minLatency(),
)) { /* ... */ }
for await (const id of client.lookupResources(
  { resourceType: "document",
    permission: split.permission,              // "viewer" -- lookup path
    subjectType: "user", subjectId: encode("user", "bob") },
  minLatency(),
)) { /* ... */ }
```

Two lines that were the same string in the source are now two different strings, and which
one each call site needs is decided by the call's *surface*, not by anything visible at the
call site itself. That is why this is a map lookup at every site rather than a rename.

**Recording it.** Every split relation the converted code touches is a **Class B** finding,
recorded exactly the way the identifier obligation below is: one checklist row per
`type.relation` pair in `relation_splits`, with the `file:line`s rewritten on each side, in
`migration-plan.md`'s `## Deferred / manual` section. The plan's own **Relation splits**
table (phase 1's output) is the checklist's source list -- a reviewer should be able to walk
that table and find every one of its rows accounted for on both surfaces.

## The identifier obligation

**Converted call sites must encode identifiers through the exact same codec the data
migration wrote relationships under -- there is no negotiation once phase 3 has run.**
`data-mapping.md`'s "The ID codec" section states the contract (`encode`/`decode`, driven by
`migration-map.json`'s `id_encoding.types`) and demonstrates the failure mode live: a
relationship written under `base64url(email)` is checkable only under that same encoded
form; the *raw* email fails SpiceDB's own object-id grammar before any graph walk happens
(`data-mapping.md`, "Verified, both directions"). That evidence is not repeated here -- this
section states the code-side half of the same obligation.

**The source codebase never needed this codec, because OpenFGA never enforced the constraint
that makes it necessary.** Verified live: OpenFGA accepts a raw email as a subject id with no
encoding of any kind, both to write and to check:

```
$ fga tuple write user:alice@corp.com viewer document:5
{"successful":[{"object":"document:5","relation":"viewer","user":"user:alice@corp.com"}]}
$ fga query check user:alice@corp.com viewer document:5
{"allowed":true,"resolution":""}
```

SpiceDB's object-id grammar (`^[a-zA-Z0-9/_|\-=+]{1,1024}$`) rejects `@` outright, which is
exactly why a real customer identifier space (emails, UUIDs with dashes are fine, external
IDs with other punctuation) can force `id_encoding.mode: "base64url"` in the first place.

**Every converted call site that builds a resource or subject id from application data --
not just the ones a migrating agent happens to test -- must run that id through the same
`encode`/`decode` module phase 3 emitted into the project, at the API boundary.** Concretely:
a `checkPermission`/`writeRelationship`/`lookupResources` call that constructs
`{subjectType: "user", subjectId: someEmailVariable}` needs `someEmailVariable` passed
through `encode("user", someEmailVariable)` first, if `migration-map.json`'s
`id_encoding.types` lists `user`; a value read back from a SpiceDB response (a
`LookupResources` result, a `ReadRelationships` result) that will be shown to a user or
matched against application data needs `decode(...)` before it leaves the boundary. Missing
this is **not** a compile error and not even, in the common case, a silent wrong answer --
`data-mapping.md`'s demonstration shows the raw form is rejected by SpiceDB's own object-id
grammar with a hard error on every single check against a migrated relation, for every
subject of the encoded type, until the call site catches up. That is a fail-closed outage at
runtime, discovered in production traffic, not a half-migration a type checker or a `zed
validate` run would ever catch at build or deploy time -- record every type in
`id_encoding.types` as a required rewrite site in `migration-plan.md`'s `## Deferred /
manual` section (`findings-report.md`'s format) so the code-side sweep has a checklist, not
just a warning.

## Operations with no SpiceDB target -- halt, don't guess

**Six, not three.** The spec's original draft listed three (store CRUD, AuthZEN,
Permissions Index); a systematic ground-truth extraction against the real client and
`.proto` sources found two more (`tools/migration-harness/fixtures/client-api-surface.json`'s
`no_counterpart` block), derived by walking every `.proto` file under `authzed/api/`
recursively rather than asserting a service count -- the walk found **7** services total
(`PermissionsService`, `SchemaService`, `WatchService`, `ExperimentalService`, and three
under `materialize/v0/` that no idiomatic client wraps at all -- `grep -rli materialize
<client-dir>` returns 0 hits across all 7 languages' hand-written source). A sixth,
found independently while inventorying what the OpenFGA SDK exposes rather than while
reasoning about the check path (item 6 below, `readAssertions`/`writeAssertions`), rounds the
list out to six. None of the 7 services model a construct for any of the six items below.

1. **OpenFGA store CRUD** (`CreateStore`/`GetStore`/`ListStores`/`DeleteStore`). SpiceDB is
   a single logical backend, one schema, one relationship graph -- no multi-tenant store
   concept anywhere in the proto surface.
2. **AuthZEN.** No AuthZEN-related service, message, or proto file anywhere in the client
   source.
3. **OpenFGA Permissions Index.** No Index-related service or message anywhere.
4. **`contextual_tuples`** (ephemeral, request-scoped relationship tuples supplied for one
   check call only, never persisted). `CheckPermissionRequest`
   (`permission_service.pb.go:1048-1066`) has `Consistency`, `Resource`, `Permission`,
   `Subject`, `Context` (caveat-evaluation context -- named values for a caveat expression,
   *not* extra tuples), `WithTracing` -- no field anywhere for a non-persisted relationship.
   A "what-if" check built on `contextual_tuples` has no direct target: it must actually
   `Write` the relationship first, a durable, transactional operation, materially different
   from a request-scoped hypothetical.
5. **`authorization_model_id` pinning** (checking against one specific, immutable historical
   model while a newer one is already live). `WriteSchema` replaces the schema outright;
   `CheckPermissionRequest` has no schema/model-id field, and `ReflectSchema`/`DiffSchema`
   read or diff the **current** schema only -- there is no server-side registry of past
   schemas addressable by id for a live check. ZedTokens pin relationship-data revisions,
   an orthogonal concept to schema versioning. **Two SDK read methods share this exact no-
   target reasoning and are recorded under this same item, not as separate entries**: OpenFGA's
   `readAuthorizationModel(id)` (fetch one specific historical model **by id**) and
   `readAuthorizationModels()` (list every stored model version, paginated) both ask a
   question SpiceDB's schema surface cannot answer -- there is no by-id historical read and no
   version-history enumeration, only "what is live right now." **Do not confuse either of
   these with `readLatestAuthorizationModel`**, the call-mapping table's own new row above:
   that method takes no id and asks only "what is live right now," which `ReadSchema` answers
   directly and is a real, mapped construct -- the "no target" classification here is specific
   to the **by-id** and **list-all-versions** shapes, not to every read-the-model call.
6. **`readAssertions`/`writeAssertions`** (OpenFGA's server-side assertion store: a named set
   of `{tuple_key, expectation}` pairs -- `Assertion.tuple_key`/`.expectation`
   (`apiModel.d.ts:50-75`) -- pushed to and read back from the store, scoped to one
   `authorization_model_id`). `OpenFgaClient.readAssertions`/`.writeAssertions`
   (`dist/client.d.ts:466-485`) are real, live methods, confirmed against the installed
   `@openfga/sdk`. This one does not surface when reasoning about converting authorization
   *checks* -- it governs test-assertion pushes, not live authorization decisions -- it
   surfaces only when inventorying what the SDK exposes end to end. SpiceDB has no
   server-side assertion-storage API anywhere: no service, message, or RPC in the proto
   surface mentions an assertion at all -- `grep -rli assertion` across every vendored
   client's hand-written source and every `.proto` file under `authzed/api/` turns up nothing
   but `assert(...)`/`Assert*` test-framework calls in the generated clients' own test suites,
   the same kind of unrelated testing noise the `materialize` check above already had to
   filter past. A call site pushing or reading assertions has no live-server RPC to rename to.
   SpiceDB's equivalent *coverage* is a static artifact, not a server-stored one: the
   validation YAML `/spicedb-dev:migrate-tests` (phase 5) already produces from a source
   `.fga.yaml` file's own `check:` block (`test-mapping.md`), checked offline with
   `zed validate` rather than pushed to and read back from a live store. Converting a
   `writeAssertions` call site means moving its assertions into that validation-YAML file (by
   hand, or automatically if they started life in a `.fga.yaml` fixture phase 5 already
   converts) rather than finding an RPC to call. Lower blast radius than the other five --
   nothing here gates a live authorization decision -- but membership in this list is about
   having no target, not about severity, and an agent that hits a CI or deploy script calling
   `writeAssertions(...)` today gets no instruction to halt at all without this entry.

**`ExperimentalService`'s relationship counters do not change this list -- checked directly
against the proto, not assumed.** The open question worth settling explicitly: do
`ExperimentalRegisterRelationshipCounter` / `ExperimentalCountRelationships` /
`ExperimentalUnregisterRelationshipCounter` (`experimental_service.proto:104-136`, none
marked `deprecated`, unlike every other RPC in the same service -- all seven of
`BulkImportRelationships`, `BulkExportRelationships`, `BulkCheckPermission`,
`ExperimentalReflectSchema`, `ExperimentalComputablePermissions`,
`ExperimentalDependentRelations`, and `ExperimentalDiffSchema` carry `option deprecated =
true`) provide either tuple injection or schema-version pinning. Reading the three
request/response messages directly:
`ExperimentalRegisterRelationshipCounterRequest` takes a `name` and a `RelationshipFilter` --
a filter over **already-persisted** relationships, the same filter shape `ReadRelationships`
uses, not a place to attach ephemeral data. `ExperimentalCountRelationshipsResponse` returns
either `counter_still_calculating: bool` or a `ReadCounterValue{relationship_count: uint64,
read_at: ZedToken}` -- a point-in-time **count** of relationships matching a pre-registered
filter, bound to a revision, nothing resembling a schema version or model id anywhere on
either message. **Verdict: no change to the six-item list.** The counters are a read/count
operation over persisted state, not a write path and not a schema-versioning mechanism --
neither of the two properties this file's no-counterpart items actually turn on.

**Halt, don't guess, exactly as the schema-conversion pack does for an unhandled construct**
(`SKILL.md`'s "Red Flags": "Inventing a translation... is worse than a halt, because nothing
downstream will catch it"). A call site using any of these six constructs is recorded in
`migration-plan.md`'s `## Deferred / manual` section, never approximated with something that
compiles. **Which class it is recorded as is decided by whether it has a blocker-catalog
entry, not by which of the six it is** -- items 4 and 5 (contextual tuples, model-ID pinning)
are Class A, resolved at the gate from `blockers.md`'s own option lists; items 1, 2, 3, and 6
(store CRUD, AuthZEN, Permissions Index, `readAssertions`/`writeAssertions`) have no catalog
entry and are Class C. See "Recording code-side findings in `migration-plan.md`" below for the
rule and its source.

## Recording code-side findings in `migration-plan.md`

Every finding this file's rules produce fits `findings-report.md`'s existing taxonomy, not a
code-specific fourth class:

**The six no-target operations do not all land in one class, and the rule that sorts them is
not severity.** `findings-report.md` states where Class A comes from: "A pack's blocker
catalog (`pack-contract.md` item 4) supplies the actual detection rules and options for its
source; that catalog is where Class A findings come from." `blockers.md` carries four items
-- transitive wildcard, contextual tuples, multi-store tenancy, model-ID pinning -- and
exactly **two of them are among the six**: contextual tuples and `authorization_model_id`
pinning. Those two have a detection rule, an option list, and a gate that resolves them, so a
call site using either is Class A and a resolution must already be on file. The other four
(store CRUD, AuthZEN, Permissions Index, `readAssertions`/`writeAssertions`) have no catalog
entry, no option list, and nothing a gate re-run would add -- SpiceDB has no construct for
any of them, full stop -- so they are Class C: recorded under **Deferred / manual** with
their `file:line`, answered by phase 4 directly, never a halt. This is the same split
`/spicedb-dev:migrate-code`'s step 6 already routes on, and the same four
`/spicedb-dev:migrate` records as Class C advisories that are human work.

- **Class A** (hard blocker, needs a user decision before conversion proceeds): the **two**
  no-target operations with a blocker-catalog entry -- contextual tuples and
  `authorization_model_id` pinning (`blockers.md` items 2 and 4) -- plus the
  non-transactional-writes fork (partial-success semantics vs. one atomic call), which has no
  catalog entry because it is invisible until a call site is read, but is a Class A finding by
  `findings-report.md`'s own definition: no mechanical fix, and conversion of that call site
  cannot proceed until the user decides. Phase 4 puts that one to the user itself, in a batch,
  rather than sending it back to the gate.
- **Class B** (mechanical, but changes stored/checked data and must be seen and owned): the
  relation-split rewrite at every call site touching a relation `migration-map.json` lists
  under `relation_splits` -- one checklist row per `type.relation`, both surfaces -- and the
  identifier-codec rewrite at every call site building or reading an encoded type's id --
  each mechanical once `migration-map.json` is known, but both real behavior-affecting
  rewrites, recorded the same way `data-mapping.md`'s ID codec and `write_relation` sections
  record them for the data phase.
- **Class C** (advisory, never halts): the **four** no-target operations with no
  blocker-catalog entry -- store CRUD, AuthZEN, Permissions Index,
  `readAssertions`/`writeAssertions` -- each recorded with its `file:line` and the answer
  phase 4 took (leave as-is · remove · replace with hand-written logic · point at the
  concrete equivalent where this file names one, as it does for assertions). Also: the
  `batchCheck`-ordering rewrite (correlation-id pairing → positional), any use of
  `correlation_id` beyond pairing that has nothing to carry it on the SpiceDB side, the
  `listRelations` error-handling policy choice (and its language-dependent default once
  converted -- Python raises, TypeScript swallows), the `expand`-tree-walker rewrite, and the
  `readChanges`→`watch` transport-model change.

Record each against the call site (`file:line`) it was found at, in `findings-report.md`'s
"Inline markers" required-reference shape -- site `file:line`(s), the governing rule by file
and section, and a candidate mapping's verified/inferred tag when one exists -- the same
shape phase 0's codebase sweep already uses for the three code-side Class A sweeps
(`## Scan scope`'s "contextual tuples, model-ID pinning, store IDs").

## Deliberately not written yet

Known gaps, held open on purpose, matching this pack's existing convention
(`schema-mapping.md`'s and `test-mapping.md`'s own closing sections, spec decision D11).

- **No corpus of real application code exercises any rule in this file.**
  `SKILL.md`'s validation-corpus section states plainly that Tier 2 (`theopenlane/core`,
  `openfga/flask-demo`, `embesozzi/keycloak-openfga-workshop`) has zero repositories
  converted -- this file is exactly the reference that gap was waiting on. Every rule above
  is verified against the real, installed OpenFGA SDKs and the real vendored SpiceDB client,
  live, but none has yet been exercised against a real, messy, pre-existing codebase's call
  sites, which is a different and harder test than a clean worked example.
- **C#, Java, and Ruby's OpenFGA-side SDKs were not independently re-verified for the three
  source shapes or the non-rename mappings.** This file's OpenFGA-side evidence is drawn
  from the TypeScript/JS SDK (`@openfga/sdk`, whose class names are the literal source of the
  spec's `OpenFgaClient`/`OpenFgaApi` naming) and, for the `OpenFgaClient` shape only, the
  Python SDK. The SpiceDB-side per-language divergences (check signature, streaming, bulk-check
  shared-permission limitation, `proto_client` escape hatch) are verified per language
  directly against the vendored client; the OpenFGA-side half of each mapping is not
  re-derived per OpenFGA SDK language, only per shape.
- **The `@auth0/fga` environment table's exact values between 0.8.0 and 0.10.0** (where the
  `us`/`eu1`/`au1` enum and the `FgaEnvironment` type were introduced) were not walked
  version-by-version -- only 0.4.1, 0.8.0, and 0.10.0 were checked directly. A codebase on an
  intermediate version's exact accepted set should be confirmed against that version's own
  `configuration.js`, not interpolated from the three points here.
- **No live measurement of what happens when a `listRelations` conversion's N-`CheckOne`
  fallback (Go, Rust, C#, Java) is run against a long permission list at scale** -- the
  round-trip cost this file states as the consequence of the shared-permission limitation is
  a structural fact (one call becomes N), not a benchmarked latency number.
