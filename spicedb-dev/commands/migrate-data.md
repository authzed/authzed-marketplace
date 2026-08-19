---
name: migrate-data
description: Migrate a live source system's relationship data into SpiceDB
argument-hint: "[store-id] [output-dir]"
allowed-tools:
  - TaskCreate
  - TaskUpdate
  - Read
  - Write
  - Glob
  - Grep
  - Bash
  - AskUserQuestion
---

# Migrate Data

Phase 3 of the migration pipeline: move a live OpenFGA store's relationship data into a
live SpiceDB instance -- extract, transform, load, and verify -- and emit the ID codec
module that this phase and phase 4 (client code) must encode identically through. **Phase 4
is `/spicedb-dev:migrate-code`**, which imports this exact codec module rather than
inlining its own encode/decode logic -- say so, and name it, rather than treating the
rewrite as manual.

**This is the most consequential command in the pipeline.** `/spicedb-dev:migrate-schema`
and `/spicedb-dev:migrate-tests` produce files a human reviews before anything happens to a
live system. This command writes to a live SpiceDB instance. A mistake here is expensive
and hard to undo, and two mistakes are common enough to name up front:

- **A relation split's write target is the `__direct` relation, never the permission.**
  SpiceDB rejects a write to a permission outright, so getting this backward halts loudly on
  the first write -- but only if every write actually goes through this rewrite. A store
  with even one split relation depends on it (see `data-mapping.md`, "Tuples are writes").
- **Running converted client code before this phase completes denies everything.** This is
  the plugin's existing "data before code" rule (`migrating-to-spicedb/SKILL.md`): a check
  against a SpiceDB instance still missing relationships fails closed, silently, for every
  request touching unmigrated data. Do not treat this command as optional groundwork phase 4
  can route around.

This command's job is to **convert**, following the pack's data-mapping reference exactly,
and to decide as little as possible about *how* -- every rewrite rule is "look this up in
`migration-map.json`," not a judgment call. What it does *not* minimize is caution around
the write itself: dry run before load, `TOUCH` not `CREATE`, detect a partial prior attempt
before choosing a load strategy, and a verification pass that actually re-reads the target
and can fail.

**This command has no gate of its own, reduced or otherwise.** Like
`/spicedb-dev:migrate-tests`, it is a pure **consumer** of `migration-map.json` and
`migration-plan.md` -- every naming and encoding decision below is a lookup against the JSON,
not something this command could decide standalone; `migration-plan.md` supplies only the
narrative sections (Source, Target) that have no JSON counterpart. If `migration-map.json` is
missing, or its `phase_status["0"]` shows it was authored by the reduced inline gate rather
than the full one, this command halts and routes to `/spicedb-dev:migrate` rather than
inventing a gate to fill the gap.

Outputs, written to `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory) unless step 3 places the
code elsewhere in the project:

- `migration/id_codec.<ext>` -- the ID codec module, in the project's language.
- `migration/migrate_data.<ext>` -- the extract/transform/load script, in the project's
  language, importing the codec rather than inlining encoding logic.
- `migration/relationships.jsonl` -- the transformed relationship-write set, one JSON object
  per line, written by the script itself and read back by its own `--verify` pass.
- `migration/checkpoint.json` -- extraction and load progress, written and read by the
  script for resumability.
- `migration-map.json` -- updated in place: `phase_status["3"]`, and an entry appended to
  `decisions.additional` if the project language was ambiguous enough to ask about (step 3).
- `migration-plan.md` -- regenerated in place: the rendered sections from the updated
  `migration-map.json`, plus the narrative **Sync obligations** section revised per its own
  rule (step 8).

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each
task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Read the migration state

Read `migration-map.json` from `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory) -- the same
place every earlier phase writes it -- or the path the user gave. Confirm `migration-plan.md`
sits alongside it; later steps read a couple of narrative sections from that Markdown
(Source, Target), but every check in this step reads the JSON -- `migration-map.json` is the
single machine-readable record and nothing here parses the plan to decide anything
(`findings-report.md`).

**If either file does not exist, halt.** Say plainly that `/spicedb-dev:migrate` (phase 0) is
this pipeline's front door and must run first: it produces both `migration-plan.md` and the
`migration-map.json` this command applies to every tuple. Do not offer a reduced gate --
there is nothing this phase could decide on its own that phase 0 or phase 1 has not already
decided.

**If it exists, check who wrote it before doing anything else** -- the same authorship check
`/spicedb-dev:migrate-tests` step 1 performs. Read `phase_status["0"].status`:

- **`complete (full gate)`** -- proceed.
- **`inline (reduced -- no codebase analysis)`**, missing, or any other value -- halt. Say
  plainly that this plan was never authored by the full gate, and direct the user to
  `/spicedb-dev:migrate`, which detects this exact authorship marker itself and re-runs the
  full gate, carrying the reduced gate's recorded decisions forward as defaults. Do not
  re-run the gate yourself -- this command has no `Task` access to the `migration-analyzer`
  agent.

**Also halt on an unresolved Class A finding**: if any entry in
`decisions.per_blocker_resolutions` has a null or absent `resolution`, list them (blocker,
site, rating) and stop.

**Then confirm phase 1 is actually done.** Read `phase_status["1"]`, and independently check
that `[output-dir]` contains both `schema.zed` and `migration-map.json` -- the recorded
status and the files on disk can disagree if a previous run was interrupted. If either check
fails, halt and direct the user to `/spicedb-dev:migrate-schema`.

**Then check whether this phase itself is resuming.** Look for `migration/checkpoint.json`
in `[output-dir]` (or wherever `phase_status["3"].artifact` records the script was written,
if that field is already populated from a prior run of this command). If it exists, this is
a resumed or repeat run, not a fresh start -- read it now; step 6 uses it rather than
starting extraction or the bulk/`TOUCH` decision over.

### Step 2: Load the conversion pack's data-mapping reference

Read the plan's **Source** section for the detected system and look it up in the
`migrating-to-spicedb` skill's source registry. For OpenFGA, Okta FGA, or Auth0 FGA that is
`openfga-to-spicedb`; read its `references/data-mapping.md` **in full** before writing
either output -- it is the algorithm this command applies, and every step below cites it
rather than restating it. If the plan's source has no pack, or the pack has no data-mapping
reference, stop: an unsupported source needs a mapping written first
(`pack-contract.md` item 6).

Every rule that follows is drawn from that file. Where a step below states a rule plainly,
it is quoting the reference's conclusion, not adding a new one -- if this command's wording
and `data-mapping.md`'s ever disagree, the reference file is authoritative and this command
is stale.

### Step 3: Confirm the target and the project's language

**Target.** Read the plan's **Target** section for the SpiceDB endpoint. Confirm the schema
is actually deployed there before doing anything else -- this command does not deploy it
(that is `/spicedb-dev:migrate-schema`'s step 9 next-step, run by a human on their own
schedule) and a load against an undeployed or stale schema fails in confusing ways rather
than a clean halt. Run `zed schema read` against the plan's endpoint and token, passed
**explicitly** on the command line:

```bash
zed schema read --endpoint <endpoint> --token <token> --insecure   # drop --insecure over TLS
```

Never `zed context use` -- it rewrites shared global `zed` configuration instead of scoping
to this one migration, the same rule every other phase in this pipeline follows. If the read
errors, comes back empty, or is missing a definition `migration-map.json`'s `types` table
names, halt and say the target needs `schema.zed` deployed first; do not deploy it yourself.

**Evaluate "No live source store exists yet" (below) before applying that halt.** This check
exists to protect the *load*, and the no-source-store branch does not load anything -- it
emits the ID codec and stops. A project at adoption stage typically has neither an OpenFGA
store with data nor a deployed SpiceDB target, so halting here on a missing target would
block the one branch written for exactly that situation, before it can be reached. When the
no-source-store conditions hold, skip this target check entirely, record `pending`, and say
in the artifact that no target was required because nothing was loaded. Reach this halt only
when there *is* data to load.

**Store.** Resolve the OpenFGA store to extract from: the `[store-id]` argument if given,
else the plan's **Source** section if it names exactly one store. If `migration-map.json`'s
`decisions.tenancy` records more than one store (multi-store tenancy), and no `[store-id]`
was given, use `AskUserQuestion` to ask which store this run targets -- one run of this
command migrates one store's data.

**No live source store exists yet -- a normal precondition, not an edge case.** A migration
planned before or during initial SpiceDB adoption commonly has a real model and real
application code, converted or in progress, with **no OpenFGA store carrying production
data at all** -- there is nothing yet for this phase to extract. Detect this before assuming
a store must be reachable:

- No `[store-id]` was given, the plan's **Source** section names zero distinct store IDs
  (phase 0's own scoping numbers already record this count), and `fga store list` (or the
  client SDK's equivalent) against the configured API URL returns no stores, fails to connect,
  **or hangs**. Wrap it: `timeout 30 fga store list`. With no API URL configured the CLI does
  not error and does not return -- it blocks indefinitely (measured: no output and no exit at
  25s or at 120s), so a run following "returns no stores or fails to connect" literally stalls
  here forever. A timeout expiry counts as "no reachable store" for this branch; record the
  command and that it timed out.
  **"Zero distinct store IDs" counts IDs found in files, which is not the same as "no store
  exists."** An application that reads its store ID from runtime configuration (an
  `authorization.openfga.store.id` key, an env var) has exactly one store and zero IDs in the
  tree, so this condition fires on a deployment that genuinely has data. The reachability
  half above is what settles it, not the count: if a store is configured but unreachable from
  here, this phase is `pending` because it could not connect -- **not** because there is
  nothing to migrate, and the artifact note must say which of the two it was. Never conclude
  "no store exists" from the file-scan count alone.
- The user, asked directly (`AskUserQuestion`, if not already stated in the plan), confirms
  there is no live store to migrate from.

**When this is the case, do not halt and do not treat it as a failure.** This phase has two
genuinely separable jobs -- emit the ID codec (step 4), which depends only on
`migration-map.json`'s `id_encoding`/`types` and needs no live data at all, and extract/
transform/load/verify (steps 5-7), which does. Do the first now; skip the second:

1. Run step 2 and step 4 as written -- read `data-mapping.md`, resolve the language (below),
   and emit `migration/id_codec.<ext>`. This is real, usable output: `/spicedb-dev:migrate-
   code` (phase 4) can import it immediately, and nothing about it changes once real data
   shows up later.
2. Skip step 5's extraction and load, and steps 6-7, entirely -- there is no source to
   extract from and nothing to verify against. Do not emit `migration/migrate_data.<ext>`
   against a store that does not exist, and do not fabricate placeholder data to exercise the
   script; that would not be testing this phase, it would be manufacturing input to make the
   halt disappear (the same discipline `/spicedb-dev:migrate-tests` applies to its own
   zero-candidate case).
3. Write `migration-map.json`'s `phase_status["3"]` as `status: "pending"` -- per
   `findings-report.md`'s closed vocabulary, this phase has not run to completion, and
   `"failed"` would misstate what happened (nothing was attempted and rejected; there was
   nothing to attempt). Put the detail in `artifact`, where it belongs: name the codec's
   path, and say plainly that extraction/load/verification are deferred until a live source
   store exists, so a later run of this exact command resumes from here rather than starting
   over.
4. Say so explicitly in the report (step 9), in place of the load/verification report: what
   was produced now (the codec, ready for phase 4), what was not attempted and why, and that
   re-running `/spicedb-dev:migrate-data` once a live OpenFGA store with real data exists will
   complete the deferred half with no wasted work. Restate the **"data before code"** rule
   for this case specifically: phase 4's *converted* code must still not be pointed at
   SpiceDB for this store's resource types until this phase's extraction/load/verification
   actually runs and passes -- an empty store answers every check `false`, the same fail-
   closed hazard as a partially loaded one, and "no data exists yet" does not relax that
   rule, it is exactly the condition it protects against.

This is different from every other halt in this command's **Error Handling** table: those
all mean something is wrong and needs fixing before the command can proceed at all. A
missing live store is not wrong -- it is where a migration started early in adoption
legitimately is -- and the correct response is to do the part of this phase's job that is
possible now and say plainly what is deferred, not to stop the pipeline.

**Language.** Determine the project's language for the codec and the script: the plan's
**Source** section records the detected SDK/client language(s). If that is a single
language, use it. If it lists more than one, or the plan is silent, check the project for a
dependency manifest (`go.mod`, `package.json`, `pyproject.toml`/`requirements.txt`,
`pom.xml`/`build.gradle`) and ask with `AskUserQuestion` if more than one plausible target
remains. If it was ambiguous enough to ask about, record it now for step 8 to write:
`{"key": "resolved_language", "value": <language>, "note": <why it was ambiguous>,
"recorded_by": "/spicedb-dev:migrate-data step 3"}`, appended to `migration-map.json`'s
`decisions.additional` -- phase 4 needs the same answer and should not have to re-derive it.

**Placement.** Default to writing the codec and the script under `[output-dir]/migration/`
-- **but check that name is free first.** "Migration" is a heavily overloaded word in
application repositories: database schema migrations, data backfills, and, in one real project's case, an
`internal/migration` package for live instance migration, all commonly own it already. In a
language where a directory name is also a module or package name (Go, Java, Rust, Python), a
collision is a compile error at best and a silently shadowed import at worst. If the name is
taken, qualify it rather than merging into the existing package -- `spicedbmigration/`,
`authzmigration/`, or the project's own convention -- and never add files to a directory whose
existing contents are about something else.
If the project has an obvious existing home for SpiceDB-related code (an `internal/authz`,
`spicedb/`, or similar directory already importing a SpiceDB client), that is a reasonable
place to put the codec instead, since phase 4 will import it from application call sites --
ask with `AskUserQuestion` if it is not obvious, and record the chosen path in
`migration-map.json`'s `phase_status["3"].artifact` once written (step 8). Getting this
recorded matters: nothing else in the plan says where the codec landed, and phase 4 has no
other way to find it.

### Step 4: Emit the ID codec module

Follow `data-mapping.md`'s "The ID codec" section exactly. One module, written once, in the
project's own language -- not this plugin's Python. Its contract, reproducing
`encode_id`/`IdMap` from that reference:

- `encode(source_type, source_id) -> str` and `decode(source_type, spicedb_id) -> str` (the
  inverse), covering **every** source type in `migration-map.json`'s `types` table, not just
  ones this store's tuples happen to use.
- Mode is per source type, driven by `migration-map.json`'s `id_encoding.mode` and
  `id_encoding.types`: a type not in `id_encoding.types` passes through unchanged in both
  directions, regardless of `mode`.
- The wildcard subject id (`*`) is never encoded or decoded, regardless of type -- it is a
  distinct grammar token, not an ordinary object id.
- `base64url` mode uses **padded** base64url (`A-Za-z0-9-_=`, already inside SpiceDB's
  object-id charset, no further mangling). Padded specifically: most languages ship an
  unpadded variant too, and the two disagree. `data-mapping.md`'s "The ID codec" section
  carries the per-language API table -- use it rather than reaching for whatever the language
  calls "standard", and record the API used in the emitted codec's header.
- An empty id, or an id whose encoded form would exceed SpiceDB's 1024-character object-id
  limit, is a hard error at encode time -- **never** a silent truncation, which would break
  the decode direction irrecoverably.

Emit this **before** the migration script (step 5), and have the script import it rather
than inlining the encode/decode calls -- this is what makes "phase 3 and phase 4 encode
identically" a structural guarantee instead of a convention two independently written pieces
of code have to happen to agree on (`naming-normalization.md`, "One codec, two consumers").
**Do not load data when `id_encoding.status` is `"unresolved"` or `"unknown"`.** `mode:
"none"` under either of those does not mean the IDs are legal -- it means no encoder is being
emitted while violations are known to exist or have never been ruled out. Loading then writes
relationships whose object IDs SpiceDB will reject outright (a write fails client-side, a check
fails server-side on the object-ID pattern), so the result is a hard error on a live request
path rather than a wrong answer. Halt, say which of the two states it is, and render
`id_encoding.violations` under `### Needs action`. Emit the codec as usual -- that part is
safe and useful -- and record `phase_status["3"]` as `pending`, not `failed`.

Even when this project's `id_encoding.mode` is `"none"`, still emit both functions with the
full contract above -- phase 4 imports this exact file and must not need a second version
later if a type's encoding mode ever changes.

### Step 5: Emit the migration script

One script, in the project's language, implementing extract -> transform -> load exactly as
`data-mapping.md` specifies, importing the step-4 codec. State each rule below to the user as
it is applied, citing the reference section, rather than silently encoding it -- this is the
part of the command a reviewer most needs to be able to check against the source of truth.

**Scope configuration validation to what the invocation actually does.** If the script exposes
separate modes (a load-only re-run against an already-extracted `migration/relationships.jsonl`,
a re-verify pass, a dry run), do not require OpenFGA connection settings (store id, API URL)
unconditionally at startup for a mode with no OpenFGA interaction at all -- a load-only or
verify-only invocation has nothing to validate them against, and a script that demands them
anyway forces a caller to supply throwaway values just to invoke a mode that never uses them.
Validate each setting only when the code path that actually needs it runs.

**Extraction.** `fga tuple read --output-format simple-json --max-pages 0`, paginating
exhaustively (`data-mapping.md`, "Extraction"). **Never `fga store export` for a store of
unknown size** -- it silently truncates at 100 tuples with no warning, and `--max-tuples 0`
means zero tuples, not unlimited. `fga store export` is acceptable only for a store already
confirmed small by another extraction pass.

For a store large enough that a single `--max-pages 0` call is impractical to restart from
scratch if interrupted, follow `data-mapping.md`'s "Resumability" section point 3 **exactly
as written, not from memory** -- do not re-derive or paraphrase it here. The short version,
so the trap is not missed: **the `fga` CLI cannot resume a paginated extraction across
separate invocations at all.** Its own reported `continuation_token` is always empty, even
mid-pagination, and it has no flag to accept one back in; only the raw `Read` API (HTTP
gateway or gRPC, never the CLI) exposes and honors the field, verified live in that section.
An agent that checkpoints the CLI's own (always-empty) token and reads it as "extraction
complete" **silently truncates the migration with no error anywhere** -- this is exactly why
step 7 below requires an independent source-side completeness check before trusting anything
this step extracted. A store small enough for one `--max-pages 0` call never needs any of
this; mark extraction complete in `migration/checkpoint.json` either way once it finishes, so
a later resume of the *load* phase does not re-extract.

**Transform.** The transform needs `migration-map.json`, not just the tuple shape --
"nothing in the tuple itself says whether `relation` split... or whether the resource type
encodes its object ids" (`data-mapping.md`, "The transform needs the model, not just the
tuple stream"). Per tuple `{user, relation, object, condition?}`:

1. **Resource side (the relation being written):** look up `relation` in
   `migration-map.json`'s `relation_splits[resource_type]`; if present, write to that entry's
   `relation` (the generated `__direct` name); if absent, fall back to
   `permissions[resource_type][relation]`, the same name a check would use. This fallback is
   not an edge case to special-case away -- it is how an un-split relation ends up writing
   under the same name it is checked under, and it runs on every tuple, split store or not.
2. **Subject side, when the subject is a userset** (`"T#rel"`): **always**
   `permissions[T][rel]` -- **never** `relation_splits`, even when that exact relation split
   on type `T`. Getting steps 1 and 2 backward is `data-mapping.md`'s single highest-
   consequence mistake, and it fails in opposite ways: the resource side fails loudly
   (SpiceDB rejects a write to a permission), the subject side fails silently (SpiceDB
   accepts checking a bare relation with no error).
3. **Both object ids**, through the step-4 codec's `encode(type, id)` -- the wildcard subject
   id `*` is passed through unencoded, never through `encode`.
4. **A `condition:` block**, when present, becomes a caveat suffix `[name:{json}]` (or bare
   `[name]` with no context). Canonicalize the context (sorted keys, compact separators) so
   this matches phase 5's assertion-side rendering byte-for-byte if `/spicedb-dev:migrate-
   tests` also runs against this store. **Validate the caveat name against SpiceDB's strict
   relationship-string grammar, `^[a-z][a-z0-9_]{1,62}[a-z0-9]$`, and raise rather than
   silently normalize it.** A caveat name can satisfy a schema declaration's looser lexing
   rule and still fail this stricter one -- `data-mapping.md`'s ""Condition → caveat context" section," verified
   live against v1.56.0. Normalizing here would produce a write referencing a caveat name the
   deployed schema never declared under that spelling. If `migration-map.json` records a
   rename for this exact condition name (phase 1's own naming normalization), apply that
   rename; otherwise halt and report the mismatch.

**Do not deduplicate.** A live OpenFGA store holds exactly one row per `(object, relation,
user)` triple already -- that invariant is enforced by the source system itself. The dedup
`/spicedb-dev:migrate-tests` performs exists only because `.fga.yaml` fixtures nest the same
baseline tuples inside multiple independent `tests:` blocks; a live store has no such
nesting. Deduplicating real extracted tuples would silently drop legitimate rows with no
error anywhere. This script transforms every extracted tuple, once, in extraction order, and
sorts the result (below) -- it does not group, count, or collapse anything by key first.

**Sort** the finished relationship-write set by `(resource type, resource id, relation,
subject type, subject id, subject relation)` before loading (`data-mapping.md`, "Sort
order") -- no correctness effect, but it makes two runs against the same source diffable.
Write the sorted set to `migration/relationships.jsonl`, one JSON object per line, before
issuing a single write call -- this file is both the dry run's count source and the
verification pass's oracle (step 7).

**Load.** Two paths, `data-mapping.md`'s "Two write paths":

| Path | Call | Batch limit | Semantics |
|---|---|---|---|
| Bulk | `ImportBulkRelationships` (`PermissionsService` -- not the deprecated `ExperimentalService.BulkImportRelationships`) | none published; whole stream is one transaction | Fails **wholesale** on any single collision |
| Incremental | `WriteRelationships` | 1000 updates/call | Use `TOUCH`, never `CREATE` -- idempotent |

Check whether the installed client SDK exposes `ImportBulkRelationships` as a streaming
call before writing code against it -- do not assume based on language alone.
`spicedb-best-practices/references/bootstrapping.md` carries this rule and, for the one SDK
it names, records that the gap is **closed**: `authzed` Python 1.25.0's
`PermissionsServiceStub.ImportBulkRelationships` **is** `channel.stream_unary`, genuine
client-streaming, correcting an earlier note that called Python batched-only. Do not read
that file as a list of languages to avoid the bulk path in. SDK generation moves in both
directions, so confirm against the actual installed version's generated stub rather than
trusting any note -- including that one -- as current. If the call is unavailable, skip
straight to
the `WriteRelationships`/`TOUCH` path below for the first attempt, not just the fallback.

**Watch for the deprecated-service name collision on *both* the import and the export
side, not just import.** `data-mapping.md`'s "Two write paths" names the trap for
`ImportBulkRelationships`: the deprecated `ExperimentalService.BulkImportRelationships` is a
different RPC with a transposed name, not an alias. **The same collision exists on the
generated-code import surface in at least the Python SDK, on both the load and the
verification side**: `authzed.api.v1.BulkImportRelationshipsRequest` and
`BulkExportRelationshipsRequest`, both importable at the package's top level, are the
deprecated `ExperimentalService` messages -- the current `PermissionsService` types
(`ImportBulkRelationshipsRequest`, `ExportBulkRelationshipsRequest`, transposed word order)
are not exported at the top level at all and must be imported from the
`permission_service_pb2` submodule directly. Verified live against the installed `authzed`
1.25.0 Python package: `BulkExportRelationshipsRequest.DESCRIPTOR.file.name` resolves to
`experimental_service.proto`; `permission_service_pb2.ExportBulkRelationshipsRequest` exists
and resolves to `permission_service.proto`, and `'ExportBulkRelationshipsRequest' in
dir(authzed.api.v1)` is `False`. Do not trust an import name that merely looks right --
confirm which `.proto` file (or gRPC service) a generated type or stub method actually binds
to, in whatever language this script is written, the same way this step already requires
confirming `ImportBulkRelationships`'s streaming support against the installed stub rather
than a note.

**Detect a partial prior run before choosing a strategy -- do not simply retry.**
`ImportBulkRelationships` gives no signal about how much of an interrupted attempt actually
landed, and retrying it against a partially populated target fails wholesale again with the
same error. The recipe, verified live in `data-mapping.md`'s "Resumability" section:

1. **First attempt (fresh target only): `ImportBulkRelationships`**, chunked as needed, over
   the whole `relationships.jsonl` set.
2. **On `AlreadyExists` (or if `migration/checkpoint.json` already records a prior bulk
   failure), stop retrying the bulk path and switch to `WriteRelationships` + `TOUCH`,
   replaying the *entire* transformed set** -- not an estimated remainder. This is safe and
   correct regardless of whether the interrupted attempt landed 0, some, or all of the
   relationships, because `TOUCH` is idempotent and the replay set is deterministic. Record
   the chosen strategy (`"bulk"` or `"touch"`) in the checkpoint immediately, so a second
   interruption does not re-attempt the doomed bulk path.
3. Respect the caveat-context limits while chunking: 25,000 bytes per relationship's stored
   caveat context. A relationship over that limit is a hard error to report, not a value to
   truncate.
4. **The `zed relationship` CLI's batch mode cannot express per-line caveat context** -- its
   `--caveat` flag applies one binding to an entire batch. Any store with more than one
   distinct caveat binding (check `relationships.jsonl` for more than one context value)
   cannot be bulk-loaded through that CLI at all; the script must call
   `WriteRelationships`/`ImportBulkRelationships` directly through the client SDK, exactly as
   the table above assumes -- do not fall back to shelling out to `zed relationship` for a
   caveated store.

**`--dry-run`**: run extraction and the full transform, including every local grammar check
(object-id charset, caveat-name grammar) above -- these raise before any network call is
made for a malformed value -- and report the total relationship count, the count per
resource type, the count carrying a caveat, and the count writing to a `__direct` split
relation. **Zero calls** to `WriteRelationships` or `ImportBulkRelationships`. Every raised
error during this pass is a finding to fix before the real load, not a row to skip past.

### Step 6: Dry run, confirm, and load

Run the script with `--dry-run` first, always -- even on a resume. Report its counts to the
user in full (per resource type, caveated count, split-write count).

**Then confirm before the live load.** This is a write to a production authorization system;
use `AskUserQuestion` to show the dry-run counts and the target endpoint, and confirm the
user wants to proceed. This is not a modeling decision the way the pipeline's other gates
are -- it is a last check before an operation that is expensive to undo -- but it is
required, not optional, for exactly the reason this command's opening section states.

Once confirmed, run the script without `--dry-run`. It writes `migration/checkpoint.json` as
it goes (step 5's resumability recipe) so an interruption here is recoverable by re-running
this same command.

### Step 7: Verify

Run the script's `--verify` pass. Per `data-mapping.md`'s "Verification pass": verify against
the deployed target, not the source in isolation -- a green extraction proves the *source*
was read correctly, only a post-load read proves the *target* now matches it. **Three
levels, run in this exact order** -- the first is not optional and must run before the other
two are trusted:

1. **Extraction completeness, against the source, first.** The other two levels only prove
   the target matches `migration/relationships.jsonl` -- neither can detect an extraction
   that itself under-read the source, which is exactly what a broken resumed extraction
   (step 5) produces with no error anywhere else in the pipeline. Independently re-count the
   source: a **fresh** `fga tuple read --output-format simple-json --max-pages 0` run
   (counted, not re-transformed -- this single-invocation path is independently established
   as exhaustive in `data-mapping.md`'s own Extraction section) against the same store, and
   confirm its count equals `relationships.jsonl`'s line count. **A mismatch here is an
   immediate fail -- halt before running levels 2 and 3 at all.** They would otherwise report
   a clean pass against a target that only agrees with a truncated oracle, exactly the
   failure `data-mapping.md`'s "Verification pass" section demonstrates live (a 10-of-25
   truncated extraction whose target and oracle agreed perfectly at 10, and whose truncation
   was caught only by this independent recount).

   **On a store still taking writes, a mismatch here is unmigrated data, not noise.** Every
   tuple in that delta exists in OpenFGA and does not exist in SpiceDB -- the same fact this
   level exists to catch, arriving by a different route. Do not size the delta against the
   store's expected write volume and wave it through as drift; a one-shot copy is stale the
   moment extraction ends, and "small and explainable" is still a list of relationships
   missing from the target. Apply `data-mapping.md`'s **"Concurrency: a one-shot copy is
   stale the moment extraction ends"** section rather than deciding here: quiesce the source
   for the extract-to-load window if at all possible, or else converge -- re-extract and
   replay the entire transformed set through `TOUCH` (idempotent) until a fresh source count
   and the target count agree, catching up the incremental writes through OpenFGA's
   `ReadChanges` API (`fga tuple changes --continuation-token`, which unlike `fga tuple read`
   does surface and accept a token) and applying each change by its own operation. Note that
   section's own caveat: `ReadChanges` reports deletes too, a `TOUCH` replay never removes
   anything, and a delete plus a write in the same window cancel out in the count -- so a
   matching count is necessary but not sufficient on a live store.
2. **Count and read-back.** Read the target back (`ExportBulkRelationships`, paginated up to
   `MaxBulkExportRelationshipsLimit` = 10,000 per response, or `zed relationship read` for a
   small store) and confirm the total matches `relationships.jsonl`'s line count, with **no
   line missing and none duplicated**.
3. **Sample checks, both directions.** For a sample of the transformed relationships
   (default: 25, or every one for a store smaller than that), confirm each specific
   relationship is present on the target, and run `zed permission check` for the permission
   each one's `write_relation` call was made in service of (i.e. the split's `permission`, or
   the relation itself when un-split), expecting `true`.

   **Skip the check probe for any sampled relationship whose subject is the wildcard, and use
   an existence read instead.** SpiceDB rejects `*` as a *check* subject outright -- it is a
   relationship subject, never something you can ask a question about -- so a sample that
   happens to include a public grant aborts this level with an error rather than reporting a
   mismatch. Verified: the same probe returns `true` for a concrete subject and raises for
   `user:*`. For those rows, confirm presence with a `ReadRelationships` filter against the
   **relation** side and count that as the level-3 evidence, exactly as `code-mapping.md`'s
   "`check` with a wildcard subject" branch 1 prescribes for application call sites. Say in
   the artifact how many sampled rows took the read path rather than the check path -- a store
   whose sample is mostly wildcards has weaker level-3 evidence than the count alone suggests.

   **Pair every one of those with a source-known-*false* check**, per `data-mapping.md`'s
   "Sample checks" ("against source-known-true and source-known-false facts"). A true-only
   sample cannot fail in the direction that matters most here: a transform bug that emits
   every relationship *uncaveated* -- dropping a `condition:` block the source carried --
   makes a conditional grant unconditional, and every expecting-`true` probe still returns
   `true`. Levels 1 and 2 are counts and cannot see it either. Draw the false facts from the
   source: a `(subject, permission, resource)` combination `fga query check` answers `false`
   on, and any caveated relationship checked with context the caveat should *reject*.

   **`zed validate` cannot cross-check the target, and must not be offered as if it could.**
   It is entirely in-process: it loads the `relationships:` written inside the YAML file
   itself and answers from those, reading nothing from any server. `--endpoint`/`--token` are
   global `zed` flags inherited by every subcommand, so `validate` *accepts* them and then
   ignores them -- verified against a port with nothing listening on it:

   ```bash
   $ zed validate --fail-on-warn validation.yaml --endpoint localhost:59999 --token nosuchtoken --insecure
   Success! - 9 relationships loaded, 6 assertions run, 0 expected relations validated
   $ echo $?
   0
   ```

   A green run there says nothing whatsoever about the data this phase just loaded. The
   `zed permission check` probes above are this phase's real target-side cross-check.

**A verification failure halts.** Report exactly what mismatched -- an extraction-vs-source
count mismatch, a missing relationship, a target-vs-oracle count mismatch, or a failing
sample check -- and do not mark phase 3 complete in the plan (step 8). Do not re-run the load
automatically; a verification failure after a load claims to have succeeded is a finding to
investigate, not a transient error to paper over by trying again. **A level-1 failure means
the whole migration is suspect, not just the count**: `relationships.jsonl` was built from a
truncated read, so the load itself wrote fewer relationships than the source actually has --
re-run extraction correctly (per step 5, using the raw `Read` API for a store that needs
cross-invocation resumability) and re-run the full pipeline from extraction, not just from
load.

### Step 8: Record sync obligations and update the migration state

**Determine the sync-obligation count**, per `data-mapping.md`'s "Sync obligations" section:

1. Read `migration-map.json`'s `decisions.per_blocker_resolutions` first. Every
   materialized-marker or contextual-tuple resolution already recorded there is a sync
   obligation by construction -- count each flagged construct/call site once.
2. If `migration-map.json` predates that key (no such entries recorded), re-derive from
   `schema.zed` using `data-mapping.md`'s two pre-filters -- a same-type self-relation
   unioned with no caveat, and a sibling `schema-materialized-marker.zed`-style file -- and
   read every candidate individually; the pre-filter over-matches (most self-relations are
   ordinary recursive hierarchies, not sync obligations) and is a list to read, not a count to
   report directly.
3. Caveat-context resolutions are usually **zero** new obligations (the application already
   reads the value it is now passing as check context), except when a Class A contextual-
   tuple blocker's resolution re-models an ephemeral, per-request value as a caveat --
   `blockers.md` states both of that blocker's `effort`-rated resolutions create obligations;
   do not assume every caveat resolution is free without checking which shape it is.

**Phase 0 owns `## Sync obligations`; this phase revises it in place.** Read what is already
under that heading in `migration-plan.md` *before* writing anything -- phase 0 wrote its own
count there from the gate's recorded resolutions, and this phase has just derived one from a
different input. `## Sync obligations` has no JSON counterpart -- it is one of the plan's
narrative-only sections, read from and written back to the Markdown directly, per
`findings-report.md`'s `## Sync obligations` section, which states the reconciliation rule
in full and which this command follows rather than restating. In short: exactly one such
section exists in the plan, this phase rewrites its rows rather than appending a second
table or a second heading, **this phase's derivation wins** where the two disagree, and a
revision must be visible -- when the count changes, keep a short note under the table
(`phase 0 recorded N; phase 3 derived M because ...`) and say so in the report (step 9).
`None.` is a value, not an empty section, and replacing it in either direction is a revision
like any other.

Write one row per obligation (`obligation | source | write path | backfill |
reconciliation`), and **state the count explicitly in the report** (step 9) even when it is
zero -- the count is what tells the user whether this was a one-time migration or the start
of an ongoing synchronization project they are signing up to run.

Write `migration-map.json` back to the same location first, updating:

- **`phase_status["3"]`** -- `status: "complete"` (only if step 7's verification passed,
  else `"failed"`), with the codec path, script path, and `relationships.jsonl` path as its
  `artifact`.
- **`decisions.additional`** -- the resolved project language (step 3), appended only if it
  was ambiguous enough to ask about and is not already recorded there from an earlier run.

Then regenerate `migration-plan.md`. `## At a glance`, `## Needs your attention`,
`## Decisions`, `## Identifier map`, `## Relation splits`, `## Arrow aliases`, and
`## Phase status` are rewritten in full from the just-updated `migration-map.json`, per
`findings-report.md`'s "Two groups of sections" rule -- this command does not track which of
those changed this run; it regenerates all of them regardless. Separately, revise the
**Sync obligations** section in the Markdown in place, per the ownership rule above.

Leave `## Source`, `## Scan scope`, `## Target`, and `## Deferred / manual` byte-identical,
the same discipline every earlier phase in this pipeline applies to its own plan update.
**Sync obligations** is the one narrative section this phase revises; every other heading
in that second group is untouched, and every rendered section is regenerated regardless of
whether this run touched the JSON keys behind it.

### Step 9: Report

Tell the user:

1. Which store was migrated, against which SpiceDB endpoint, and where the codec and script
   were written.
2. **Counts**: the dry-run counts (step 6) and the counts actually loaded, per resource type,
   with the caveated and split-write counts called out -- state the number of relations this
   store actually split (from `migration-map.json`'s `relation_splits`) and that every one of
   those writes went to its `__direct` relation, not its permission.
3. **Verification result** -- pass or fail, and exactly what was checked: the source-side
   recount (level 1), the count/read-back (level 2), and the sample size **with its
   true/false split** (level 3). A pass here means the *target* was independently re-read and
   matched the source, not merely that the load call returned without error. If the source
   was live rather than quiesced during the load window, say so, and say whether the counts
   were converged or a `ReadChanges` catch-up was applied -- a matching count on a live store
   is necessary, not sufficient.
4. **Sync obligations** -- the count and the table, stated as a fact about this store, not a
   generic estimate. If nonzero, say plainly that each row is now permanent write-path work,
   not one-time migration work. **If this phase's count differs from what phase 0 recorded,
   say both numbers and why yours supersedes it** (step 8) -- one count, one owner, and the
   change stated out loud rather than left for the reader to notice.
5. That `migration-map.json` was updated -- the durable record -- and `migration-plan.md`
   regenerated from it, so there is a current human-readable rendering to review.
6. **Data before code.** Say explicitly: phase 4, `/spicedb-dev:migrate-code`, rewrites
   client call sites to check against SpiceDB instead of the source system, and imports this
   phase's ID codec to do it. Running that converted code -- or any code that already calls
   SpiceDB for this store's resource types -- **before this phase's verification passes**
   will deny requests that were previously allowed, because the relationships those checks
   depend on are not there yet, or are only partially there. This phase must complete, and
   pass verification, first; `/spicedb-dev:migrate-code` can be run beforehand to produce the
   rewritten code, but that code must not be pointed at this store's data until this phase
   passes.
7. If verification failed, **do not** offer next-phase guidance -- say what mismatched and
   that phase 3 is not marked complete until it is resolved and re-verified.

## Error Handling

| Situation | Do this |
|---|---|
| No `migration-map.json` or `migration-plan.md` | Halt. Direct to `/spicedb-dev:migrate`. This command has no gate of its own. |
| `phase_status["0"].status` not `complete (full gate)` | Halt. Direct to `/spicedb-dev:migrate`, which detects this marker itself and re-runs the full gate. |
| Unresolved Class A finding in `migration-map.json`'s `decisions.per_blocker_resolutions` | Halt. List the unresolved blockers. |
| `phase_status["1"]` not complete, or `schema.zed`/`migration-map.json` missing | Halt. Direct to `/spicedb-dev:migrate-schema`. |
| No pack, or pack has no data-mapping reference | Halt. An unsupported source needs a mapping written first. |
| `zed schema read` against the target errors, is empty, or is missing a mapped definition | Halt. The schema must be deployed to the target before data can load; do not deploy it from this command. |
| More than one store and no `[store-id]` given | Ask which store with `AskUserQuestion`. |
| No live source store exists yet (zero store IDs recorded, no `[store-id]` given, and `fga store list` returns nothing or cannot connect) | Not a halt. Emit the ID codec (step 4) only; skip extraction/load/verification. Record `phase_status["3"]` as `"pending"` with the deferral noted in `artifact`. Report what was produced and that re-running this command later, once a store exists, completes the rest. |
| Object id fails SpiceDB's object-id grammar | Halt (raised by the transform before any network call). Report the offending value and tuple. |
| Caveat name fails the strict relationship-string grammar | Halt. Report the mismatch. Apply a phase-1-recorded rename if `migration-map.json` has one; never silently normalize. |
| `ImportBulkRelationships` returns `AlreadyExists` | Stop retrying the bulk path. Switch to `WriteRelationships` + `TOUCH`, replaying the entire transformed set. Record the switch in the checkpoint. |
| `WriteRelationships` call exceeds 1000 updates | Chunk to 1000 per call -- this is a scripting bug in the emitted script, not a data problem; fix the script. |
| A relationship's caveat context exceeds 25,000 bytes | Halt. Report the offending relationship; do not truncate. |
| Store has more than one distinct caveat binding | The `zed relationship` CLI cannot bulk-load it (one `--caveat` per batch). The script must use the client SDK directly. **Where the client comes from:** adding one to the project is `/spicedb-dev:migrate-code` step 4's job, and this phase runs first, so do not assume one is present. Vendor it here for the migration script's own use, announce the not-for-production status the same way phase 4 is required to, and record the path in `phase_status["3"].artifact` so phase 4 reuses it rather than vendoring a second copy. |
| Level-1 verification (extraction-vs-source count) mismatch | Halt immediately, before running levels 2 and 3. Never accept the delta as drift: on a live store it is relationships that exist in OpenFGA and not in SpiceDB. If the extraction was truncated, re-run it with the raw `Read` API (step 5), not just the load, and re-run the full pipeline. If the source took writes during the window, converge per `data-mapping.md`'s "Concurrency" section -- quiesce, or re-extract and TOUCH-replay until the counts agree, applying `ReadChanges` by operation. |
| Level-2/3 verification (target-vs-oracle count, or a failing sample check) mismatch | Halt. Report exactly what mismatched. Do not mark phase 3 complete. Do not auto-retry the load. |
| User declines to confirm the live load (step 6) | Stop. The dry run and the emitted script remain in place for a later run. |
| Generated code imports a bulk-import/export request type that resolves to the deprecated `ExperimentalService` (Python: `authzed.api.v1.Bulk{Import,Export}RelationshipsRequest`) | Fix the import to the `PermissionsService` type from the language's `permission_service` generated module -- do not assume the top-level name is correct in any language without checking which `.proto`/service it binds to. |

## Notes

- The version floor is SpiceDB **v1.52.0**; `data-mapping.md`'s rules were verified against
  v1.56.0, zed v0.31.1, and `fga` v0.7.20.
- **`spicedb serve-testing` (v1.56.0+) takes no `--grpc-preshared-key` and isolates
  datastores per token.** Pass `--endpoint`/`--token` explicitly to every `zed` invocation
  and to every client the emitted script constructs; never `zed context use`, which rewrites
  shared global configuration instead of scoping to this one migration.
- **Ask twice, deliberately.** Step 3 may ask which store and which language, once each, if
  either is ambiguous. Step 6 always confirms before the live load -- that confirmation is
  not a modeling decision like the pipeline's other gates, and asking it every run (not just
  when ambiguous) is intentional given what a mistake here costs.
- `tools/migration-harness/` is **not shipped with this plugin**. It is the parity harness
  used to validate `data-mapping.md` against real OpenFGA stores and a live SpiceDB
  instance, and it lives in the plugin's source repository
  (`authzed/authzed-marketplace`), not in a customer's project. `tuple_transform.py` and
  `idmap.py` are cited throughout `data-mapping.md` as the reference implementation this
  command's rules were checked against -- **do not import or run them, and do not tell a
  user to.** The script this command emits is a fresh implementation of the same contract,
  written in the project's own language, using that project's SpiceDB client SDK and the
  `fga` CLI -- nothing in this command depends on the harness being present.
- The ID codec (step 4) is consumed by exactly two things: this command's own script, and
  `/spicedb-dev:migrate-code` (phase 4), which imports this exact file at every client
  call site that builds or reads an encoded type's id, rather than inlining its own
  encode/decode logic -- see `naming-normalization.md`'s "One codec, two consumers" for what
  silently half-encoded data looks like when they don't.
