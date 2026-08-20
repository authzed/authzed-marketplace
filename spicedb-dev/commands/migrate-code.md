---
name: migrate-code
description: Rewrite a source system's client call sites into SpiceDB client calls, and add the SpiceDB client to the project
argument-hint: "[project-dir] [output-dir]"
allowed-tools:
  - TaskCreate
  - TaskUpdate
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - AskUserQuestion
---

# Migrate Code

Phase 4 of the migration pipeline: add a real SpiceDB client to the project, then rewrite every
OpenFGA call site into the SpiceDB equivalent -- construct by construct, per `code-mapping.md` --
so the conversion ends with working SpiceDB client code, not a converted schema and data set the
application still checks against the old system.

**It is not the last thing the plugin automates.** Phase 5
(`/spicedb-dev:migrate-tests`) has no ordering dependency on this command and may run before or
after it, and `/spicedb-dev:migrate-verify` automates `cutover-strategies.md` step 4's
differential harness once phase 3 has passed verification. Step 7 of this command's own report
(below) is where that routing is stated to the user; keep the two consistent.

**This command does two things the earlier phases didn't need to: it changes the project's
dependencies, and it edits files a human wrote.** `/spicedb-dev:migrate-schema`,
`/spicedb-dev:migrate-data`, and `/spicedb-dev:migrate-tests` all produce new, standalone
artifacts (`schema.zed`, a migration script, validation YAML) that a human reviews before
anything downstream depends on them. This command instead vendors a client into the
project's own dependency manifest and rewrites existing application source files in place.
Five traps are common enough to name up front, because each produces code that compiles
clean and then fails or answers wrong at runtime:

- **A split relation is two names, and the write path and the check path need different
  ones.** Phase 1 turned every source `define` that fused a `[...]` type list with an
  operator into a generated relation (`viewer__direct`) plus a same-named permission
  (`viewer`), and recorded the pair in `migration-map.json`'s `relation_splits`. A rewrite
  that carries the source name through unchanged writes to a permission -- which SpiceDB
  rejects outright, on every write, at runtime -- and reads relationships through a filter
  that errors the same way; a rewrite that carries the *generated* name onto a check gets a
  narrower answer with no error at all. See `code-mapping.md`'s "The relation-split
  obligation," and step 6 rule 3.
- **Check code cannot be ported across languages mechanically.** Python's
  `check_permission` has no `permission` parameter at all -- it reads
  `Relationship.resource_relation` -- and Rust's `check_permission` (singular) returns a
  `CheckResult` wrapper, not a bare `bool`. Converting a Go call site's `CheckOne(ctx, cs,
  "viewer", rel)` shape into Python or Rust by find-and-replace produces code that either
  doesn't compile or discards a wrapper silently. See `code-mapping.md`'s "Per-language
  check-signature divergence."
- **The identifier obligation fails closed, not at build time.** Data written under
  `base64url(email)` by `/spicedb-dev:migrate-data` while a call site still checks
  `user:alice@corp.com` is a silent half-migration: the raw form fails SpiceDB's own
  object-id grammar on every single check, discovered in production traffic, not by a type
  checker or `zed validate`. Every call site that builds or reads an id of an encoded type
  must go through the codec phase 3 emitted -- see `code-mapping.md`'s "The identifier
  obligation."
- **`batchCheck` ordering silently corrupts results, not just names.** OpenFGA's response is
  a map keyed by `correlation_id`, with no order guarantee. The SpiceDB client returns a
  plain, positionally-ordered array. Code that still pairs responses back to requests by
  `correlation_id` after a rename-only conversion pairs the wrong answer to the wrong
  request whenever the two happen to reorder -- see `code-mapping.md`'s "`batchCheck`
  ordering."
- **A sync OpenFGA source converting to Python's async-only client can turn a denial into a
  silent allow.** Python is the one language of the seven where this actually happens:
  `openfga_sdk.sync.OpenFgaClient` is a real, documented sync client, but the prototype
  `spicedb-python` client has no sync form at all -- every method is `async def`, so the
  rewrite necessarily adds an `await` that was not in the source. A call site that misses it
  gets back a bare coroutine object, not a `bool`; a coroutine is truthy in Python's own
  `if`, so a check that would answer `False` takes the allowed branch instead, and the object
  compiles and runs without error. Confirmed live, and confirmed invisible to `pyright` and
  `mypy --strict` alike -- this is not a symptom of an untyped codebase, no Python type
  checker currently flags it. The other six languages do not share this shape: Go/Java/Ruby's
  SpiceDB targets are synchronous (no `await` to omit), and C#/Rust reject a missing
  `await`/`.await` at compile time; TypeScript's `Promise` is truthy the same way a coroutine
  is, but `@openfga/sdk` has no sync client for a rewrite to carry the habit over from, and
  `tsc` itself flags the bare case (`TS2801`) under a real type-check build. Review every
  Python call site converted from a sync OpenFGA client for a missing `await` by inspection
  -- see `code-mapping.md`'s "Async-only target vs. sync source: the un-awaited-coroutine
  fail-open."

**Writes before checks, still.** `/spicedb-dev:migrate-data`'s own opening states the
pipeline's existing rule: converted client code run against a SpiceDB instance still missing
relationships fails closed, silently, for every request touching unmigrated data. This
command rewrites the code; it does not make it safe to run before phase 3's verification has
passed. Step 9 restates this as part of the report, not as a footnote.

This command's job is to **convert**, following `code-mapping.md` exactly, and to decide as
little as possible about *how*. Every rewrite rule below is "look this up in
`code-mapping.md`'s call mapping table (or `migration-map.json`)," not a judgment call --
**`code-mapping.md` is cited throughout this command, never restated.** If this command's
wording and `code-mapping.md`'s ever disagree, the reference file is authoritative and this
command is stale. The exceptions are genuine judgment calls with no mechanical answer: which
target language a given call site's language ambiguity resolves to, how to handle a call
site with no SpiceDB target at all, and which side of the non-transactional-writes fork to
take. Those are human calls, and this command asks with `AskUserQuestion` and records the
answer in `migration-map.json`'s `decisions.additional` (`findings-report.md`), the same
place `/spicedb-dev:migrate-tests` records its own file-selection and collision decisions --
`migration-plan.md`'s **Decisions** section is regenerated from that record afterward, never
written to directly.

**This command has no gate of its own, reduced or otherwise.** Like
`/spicedb-dev:migrate-data` and `/spicedb-dev:migrate-tests`, it is a pure **consumer** of
`migration-map.json` for every naming, encoding, and Class A resolution decision --
`migration-plan.md` is read only as the human-readable rendering of that same state, never
parsed for a decision (`findings-report.md`'s "This file is a rendering, not a record"). If `migration-map.json` is missing, or its `phase_status["0"].status` shows the
plan was authored by the reduced inline gate rather than the full one, this command halts and
routes to `/spicedb-dev:migrate` rather than inventing a gate to fill the gap.

Outputs:

- The SpiceDB client, vendored into `[project-dir]`'s own dependency manifest(s), per
  language, per `spicedb-client-integration/references/installation.md`.
- Rewritten call sites -- edited in place, in `[project-dir]`, listed by `file:line` in the
  report (step 9).
- An ID codec module per language actually rewritten, if none of that language already
  exists from phase 3 -- see step 3.
- `migration-map.json` -- updated first, in place: every new decision this phase recorded
  (`decisions.additional`) and phase 4's own `phase_status["4"]` entry.
- `migration-plan.md` -- regenerated from that same file afterward, plus every
  behavioral-change finding this phase's rewrite produced appended to its narrative
  **Deferred / manual** section. Per `findings-report.md`'s `## migration-plan.md` section,
  this file is a rendering, not a record -- nothing here reads it back.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each
task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Read `migration-map.json`

Read `migration-map.json` from `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory) -- the same
place every earlier phase writes it -- or the path the user gave. This is the single
machine-readable record every phase reads and writes all migration state to
(`findings-report.md`'s `## migration-map.json` section); `migration-plan.md`, alongside it,
is a rendering of that same state for a human to read, and no phase -- this one included --
parses it back for a decision.

**If it does not exist, halt.** Say plainly that `/spicedb-dev:migrate` (phase 0) is this
pipeline's front door and must run first: it produces both `migration-map.json` and the
`migration-plan.md` rendering of it.

**If it exists, check who wrote it before doing anything else** -- the same authorship check
`/spicedb-dev:migrate-data` and `/spicedb-dev:migrate-tests` perform. Read
`phase_status["0"].status`:

- **`complete (full gate)`** -- proceed.
- **`inline (reduced -- no codebase analysis)`**, missing, or any other value -- halt. Say
  plainly that this plan was never authored by the full gate: the reduced gate covers only
  phase 1's own inputs, and the code-side Class A blockers this very phase depends on
  (contextual tuples resolved **per call site**, model-ID pinning) were never checked
  against real call sites -- only against a targeted grep run without a project directory to
  scope it, if that. Direct the user to `/spicedb-dev:migrate`, which detects this exact
  authorship marker itself and re-runs the full gate, carrying the reduced gate's recorded
  decisions forward as defaults. Do not re-run the gate yourself -- this command has no
  `Task` access to the `migration-analyzer` agent, and reimplementing it inline here is
  exactly the second gate the framework's "exactly one gate per migration" rule exists to
  prevent.

**Also halt on an unresolved Class A finding**: `decisions.per_blocker_resolutions` is an
array with one entry per detected site (`findings-report.md`); if any entry's `resolution` is
`null` or absent, list those sites and stop. `null`/absent is the one and only unresolved
marker (`findings-report.md`'s `decisions` section) -- check entries in the array, never a
Markdown rendering of it, and a blocker resolved once "as a class" still leaves every other
site's entry unresolved. A contextual-tuples or model-ID-pinning call site this phase
encounters with no resolution on file is a second, narrower instance of the same check -- see
step 6.

**Then confirm phase 1 is actually done.** Read `phase_status["1"].status`, and independently
check that `[output-dir]` contains both `schema.zed` and `migration-map.json` -- the recorded
status and the files on disk can disagree if a previous run was interrupted. If either check
fails, halt and direct the user to `/spicedb-dev:migrate-schema`. This command has nothing to
rewrite call sites *against* without the finished identifier map and the `relation_splits`
key it carries.

**Note, but do not halt on, phase 3's and phase 5's status.** Read `phase_status["3"]` and
`phase_status["5"]` now and carry them through to step 9 -- phase 5 (tests) has no bearing on
this command at all, and phase 3 (data) matters only for the data-before-code warning, not as
a precondition for rewriting code. The one place phase 3's *artifacts* (not its status) do
gate this command is the ID codec, handled on its own terms in step 3.

### Step 2: Load `code-mapping.md` and confirm the client-integration skill

Read the plan's **Source** section for the detected system and look it up in the
`migrating-to-spicedb` skill's source registry, the same lookup `/spicedb-dev:migrate-data`
step 2 performs for `data-mapping.md`. For OpenFGA, Okta FGA, or Auth0 FGA that is
`openfga-to-spicedb`; read its `references/code-mapping.md` **in full** before rewriting
anything -- it is the algorithm this command applies, and every step below cites it rather
than restating it. If the plan's source has no pack, or the pack has no code-mapping
reference, stop: an unsupported source needs a mapping written first.

Read `spicedb-client-integration/SKILL.md` and `references/core-concepts.md` too, before
touching any call site -- `code-mapping.md` owns "what an OpenFGA call becomes"; that skill
owns "how to use the client once you know the target call" (method signatures, the shared
`Relationship`/`Filter`/`Transaction` vocabulary, consistency-helper names, streaming and
error-handling idiom, per language). Every worked rewrite in the steps below assumes both are
already loaded, and cites the language-specific reference (`references/<language>.md`) for
anything beyond the shared vocabulary rather than re-deriving a method signature here.

**State plainly, once, before converting anything: this file has no corpus behind it.**
`code-mapping.md`'s own scope section says so directly -- every rule in it is verified
against the real, installed OpenFGA SDKs and the real vendored SpiceDB client, live, but none
has been exercised against a real, pre-existing codebase's call sites, which is a different
and harder test than a clean worked example. That does not change what this command does --
halt, don't guess, on anything the mapping doesn't cover -- but it means an unhandled
construct this run surfaces is genuinely new information, not a gap already known and
accepted; report it the same way step 6 reports any other unhandled case.

### Step 3: Confirm the target language(s) and the ID codec

**Language(s).** Read the plan's **Source** section for the detected SDK/client
language(s), and `migration-map.json`'s `decisions.additional` array for an entry with
`key: "call_site_language"` recorded by phase 3 because the language was ambiguous
(`/spicedb-dev:migrate-data` step 3). Do not assume phase 3's resolved language is
this command's only target: that language is whatever the *migration script* was written in,
which is usually but not necessarily the application's own language. This command's actual
targets are every language the application's call sites are written in, which step 5's sweep
determines from real dependency manifests and imports, not from the plan alone. Where the
plan and the sweep agree, there is nothing to ask; where step 5 finds a call-site language
the plan never mentioned, append it to `migration-map.json`'s `decisions.additional`
(`{"key": "call_site_language", "value": <language>, "note": <why>, "recorded_by":
"/spicedb-dev:migrate-code step 3"}`) rather than silently converting it -- the next
regeneration of `migration-plan.md`'s **Decisions** rendering (step 8) picks it up from
there.

**The ID codec.** `code-mapping.md`'s "The identifier obligation" section states the
contract: converted call sites must encode identifiers through **the exact same codec module
phase 3 emitted**, not a second, independently-written implementation of the same rules --
this is `data-mapping.md`'s "One codec, two consumers" guarantee, and it is structural, not a
convention two independently written pieces of code happen to agree on. Locate it:

1. Read `migration-map.json`'s `phase_status["3"].artifact` for the codec's recorded path
   (`migrate-data.md` step 8 records it there), or look under
   `[output-dir]/migration/id_codec.<ext>` if that field is silent on the exact path.
**Before any of the numbered steps below: detect the client shape first.** Step 5's shape
detection and step 6's embedded-server halt both run *after* step 4 adds the SpiceDB client to
the project's dependency manifest -- so a literal run edits `go.mod`/`pyproject.toml`/
`package.json` to add a prototype library explicitly labelled not for production, and only then
discovers there are no call sites to convert and that it must stop. Run step 5's detection
first; if it finds the embedded-server shape, or no client at all, halt per step 6 **without
having touched the manifest**. Vendor only once you know there is something to rewrite. The
numbering below is unchanged so that references to "step 4", "step 5", and "step 6" elsewhere
still resolve.

1. **First check `id_encoding.status`, not `mode`.** If it is `"unresolved"` or `"unknown"`,
   **stop before rewriting any call site**: violating object IDs either exist or have never
   been ruled out, and no encoder is being emitted, so converted code would hard-error on a
   live request path rather than merely answer wrongly. Put the `violations` list to the user
   with `naming-normalization.md`'s identifier options, as a Class A finding. `mode: "none"`
   is not evidence of safety -- it is equally what a clean project and a badly broken one look
   like, and `status` is the only field that separates them.

2. **If `migration-map.json`'s `id_encoding.mode` is `"none"` for every type** *and*
   `status` is `"clean"`, a codec
   module is not load-bearing for correctness (every id passes through unchanged either
   way), but still import phase 3's file if it exists, for the same reason
   `/spicedb-dev:migrate-data` step 4 still emits both functions under `"none"` -- so a later
   change to `id_encoding.mode` does not require a second version of this file to appear
   later. If phase 3 has never run and no codec file exists anywhere, proceed without one;
   there is nothing to import yet and nothing that needs encoding.
3. **If `id_encoding.mode` is `"base64url"` for any type**, the codec is load-bearing, and it
   must exist on disk. If phase 3 has never run, **halt** -- direct the user to
   `/spicedb-dev:migrate-data` first. Writing a fresh codec here, rather than importing
   phase 3's, would be exactly the failure `naming-normalization.md`'s "One codec, two
   consumers" section warns about: two independently-written implementations of the same
   contract silently diverging.
4. **If the codec's own language does not match a call-site language this run needs to
   convert** (phase 3's script was written in a different language than the application,
   which step 3's "Language(s)" paragraph above allows for), importing phase 3's exact file
   is not possible across languages. Emit a second module in the call-site's language,
   following `data-mapping.md`'s "The ID codec" section's contract exactly -- same
   `encode`/`decode` signatures, same per-type mode lookup against `migration-map.json`'s
   `id_encoding`, same wildcard passthrough, same hard-error-on-empty-or-oversized rule, same
   standard base64url alphabet, **and the same padding**. Re-deriving this codec is safe in
   a way that re-deriving naming or splitting decisions is not, because base64url is a
   published standard rather than a bespoke choice -- but "both implementations are correct"
   is **not** on its own enough to guarantee they agree. Most languages ship a padded and an
   unpadded base64url, both correct, producing different strings for the same input; pick
   opposite variants in the two codecs and every encoded id written by one is unfindable by
   the other, with no error at any layer. `data-mapping.md`'s "The ID codec" section names
   the required variant (padded) and the exact per-language API. Match it explicitly and
   state the API in the emitted file's header, rather than reaching for whatever the
   language calls "standard". Record the second codec's path for step 8 to include in `phase_status["4"]
   .artifact`, alongside the vendored client path(s), with a note that it exists because the
   call-site language differs from the migration script's -- not a direct write to
   `migration-plan.md` here, since every plan update this command makes happens at step 8,
   from `migration-map.json`.

### Step 4: Add the SpiceDB client to the project

**This is the "and then use it" half of the request, not a separate manual step the user is
left to do.** For every language step 3 resolved, follow
`spicedb-client-integration/references/installation.md` exactly -- it is the *only* file that
describes how to obtain a client, per that skill's own convention, and it is cited here
rather than restated:

1. Vendor the client at the pinned commit `installation.md` records, for each target
   language -- the language's client directory plus its sibling `proto-clients/<language>-
   proto` directory, preserving the sibling relationship the vendored manifests already
   assume.
2. Wire it into the project's own dependency manifest, following that language's own recipe
   in `installation.md`'s "Per-language wiring" section (`go.mod` `replace` directives,
   `uv add`/`pip install -e`, the `package.json` workspace-protocol fix, the C# `<Compile
   Remove>` exclusion, the Gradle composite build, the Cargo path dependency, or the Ruby
   `Gemfile` `path:` gems -- whichever this project's language needs). Each recipe was
   verified against a real build in that reference; do not improvise a shortcut past a step
   it calls out as necessary (skipping C#'s `<Compile Remove>`, for one concrete example,
   produces real, unrelated build errors from the vendored test project, not a warning).
3. Confirm the vendored client actually builds/imports before rewriting a single call site --
   run the language's own build or import check (`go build ./...`, `uv run python -c
   "import spicedb"`, `npm run build` or the project's own build step, `dotnet build`,
   `gradle build`, `cargo build`, `bundle exec ruby -e "require 'spicedb'"`). A rewrite
   against a client that doesn't build yet just relocates the same failure into application
   code, where it's harder to diagnose.

**Tell the user, before wiring anything into their manifest, that this is a
not-for-production dependency.** The vendored repository's own README carries the warning
`installation.md` quotes verbatim -- **"PROTOTYPE -- not for production use. The SpiceDB
clients are in early development. APIs, types, and behaviors may change or break at any time,
and bugs are expected."** This command is about to put that into a production dependency
manifest and rewrite live authorization call sites against it. That is a real decision with a
real cost (upstream can break the build or change behavior at any commit; there is no
versioned release to pin to, only a commit), and it must be stated at the moment it is taken,
not discovered later. Step 9's report restates it as a required line.

Say plainly, in the same breath, that **an alternative exists for four of the seven
languages**: Authzed's established clients (`authzed-go`, `@authzed/authzed-node`, `authzed`,
`Authzed.Net`) are published, versioned, and generally available, and are what the rest of
this plugin's non-migration commands use (`spicedb-best-practices/references/
client-patterns.md`). They are a *different* API from the prototype this pack's mapping table
targets, so switching to one is not a free substitution -- `code-mapping.md`'s method names,
`Transaction`/`Filter` vocabulary, and consistency helpers are the prototype's -- but a user
who cannot take a prototype dependency needs to know the option exists before the manifest is
edited, not after.

Do not reach for a package manager install of the **prototype** (`npm install
@spicedb/client`, `pip install spicedb`, `cargo add spicedb`, `go get` on its repo path) --
none of the seven is published, and two of those names resolve to *unrelated third-party*
libraries, so the install succeeds and yields the wrong code. `installation.md`'s "Prototype
status" section has the per-registry evidence, why vendoring is the only supported path for
these clients, and what changes (one file, that one) once that's no longer true.

### Step 5: Detect every call site and its source shape

Sweep `[project-dir]` for every call site touching the source system, before rewriting any of
them -- classify first, edit second, the same discipline `/spicedb-dev:migrate-tests` step 5
applies to collecting tuples before rendering.

**Sweep build, CI, and deploy scripts, not only request-handling application code.** At
least one of the operations in `code-mapping.md`'s "Operations with no SpiceDB
target" section is not a check-path call at all -- it shows up in a pipeline that pushes test
assertions at deploy time, never in a handler. A sweep scoped to `src/`-style application
directories alone will not see it; grep CI config, deploy scripts, and any directory the
project uses for migration or fixture tooling too.

**Detect per call site, not per repository.** `code-mapping.md`'s own framing: a codebase can
contain more than one of the three shapes at once (a migration mid-flight off `@auth0/fga`,
or a service that kept the raw `OpenFgaApi` for one endpoint and the flattened
`OpenFgaClient` everywhere else). Grep for construction and import sites of all three:

- **`OpenFgaClient`** -- flattened camelCase inputs, snake_case responses. The idiomatic
  wrapper.
- **`OpenFgaApi`** -- raw wire shapes (`tuple_key`, `writes.tuple_keys`); store ID lives in
  config on older SDK versions, or is an explicit first argument on newer ones --
  `code-mapping.md`'s "Store ID's position is a real, version-dependent tell" paragraph
  gives the exact SDK version boundary and how to check which shape an installed SDK version
  expects.
- **`Auth0FgaApi`** (`@auth0/fga`, deprecated) -- keyed on an `environment` string instead of
  an `apiUrl`. Once detected, it converts exactly as `OpenFgaApi` -- `code-mapping.md`'s own
  note that recognizing it is the only `Auth0FgaApi`-specific step, not a separate mapping
  table.

**`code-mapping.md`'s shape detection is verified live against the TypeScript/JS (`@openfga/
sdk`, `@auth0/fga`) and Python (`openfga-sdk`) SDKs only** -- its own "Deliberately not
written yet" section states plainly that Go, Java, C#, and Ruby's OpenFGA-side SDKs were not
independently re-verified for the three shapes. For a call site in one of those languages,
apply the same conceptual distinction (an idiomatic flattened wrapper vs. a raw generated API
vs. the deprecated Auth0 client) but confirm the actual class/package name against that SDK's
own installed source before assuming `OpenFgaClient`/`OpenFgaApi`/`Auth0FgaApi` are its exact
names -- `openfga-to-spicedb/SKILL.md`'s Detection section gives the per-language
dependency/import signal to start from (`github.com/openfga/go-sdk`, `dev.openfga:openfga-
sdk`, `OpenFga.Sdk`), but not a confirmed class-name table the way the TS/Python pair has.

For every call site found, record: `file:line`, detected shape, the OpenFGA method called,
and which row of `code-mapping.md`'s call mapping table it maps to (or that it maps to none
-- step 6 handles that case). This list is what step 6 works through and what step 9 reports
counts from; do not start rewriting mid-sweep, or a later duplicate detection of the same
call site (e.g. a helper function called from several places) produces a double-counted or
double-edited site.

### Step 6: Rewrite call sites, halting on anything with no target

Work through step 5's list. For each call site:

**1. Look up the OpenFGA method in `code-mapping.md`'s call mapping table** for the
target language's real method name -- Go/Python/TypeScript are named directly in the table;
C#, Java, Rust, and Ruby "follow the same pattern under that language's own casing
convention," per that table's own note -- confirm the exact name in that language's
`spicedb-client-integration/references/<language>.md` file rather than guessing a casing
convention by analogy.

**2. If the row has a subsection in `code-mapping.md`'s "Mappings that are more than a
rename," apply the specific rewrite that subsection demonstrates -- not a name substitution.**
Check that section for the construct rather than relying on the summaries below: it is added
to as new shapes are found, so a count stated here would go stale, and a construct missing
from this list is not evidence that renaming it is safe. The most commonly hit ones:

- **`batchCheck`/`clientBatchCheck`.** Rewrite any consumer pairing results back to requests
  by `correlation_id` into one that trusts array position instead. If the source code also
  *uses* `correlation_id` for something beyond pairing (logging, tracing, deduplication),
  that value has nothing to carry it on the SpiceDB side -- flag the use as a Class C finding
  (step 8) rather than silently dropping it.
- **`listRelations`.** No uniform SpiceDB-side target across languages -- `code-mapping.md`'s
  table row states which languages get a one-call bulk-check path and which need one
  `CheckOne` per relation. Reproduce the source's error-swallowing behavior deliberately, or
  drop it deliberately; do not let either target language's own default (Python raises and
  drops the whole batch, TypeScript swallows one bad item as `false`) silently decide the
  policy. If `migration-map.json`'s `decisions.additional` doesn't already carry an entry
  keyed `listRelations_error_policy` for this project, ask with `AskUserQuestion` once,
  batched across every `listRelations` call site rather than per site, since the policy is a
  project-wide choice -- then append the answer to `decisions.additional`
  (`{"key": "listRelations_error_policy", "value": ..., "note": ..., "recorded_by":
  "/spicedb-dev:migrate-code step 6"}`) rather than writing it into `migration-plan.md`
  directly; step 8 renders it from there.
- **`expand`.** Delete any branch of a tree-walking consumer that looks for
  `leaf.tupleToUserset` and recurses on it -- SpiceDB's `PermissionRelationshipTree` has no
  node kind that corresponds to it; the whole tree already arrives resolved in one call.
  Rewriting this branch to "look different" without removing it leaves dead code that will
  simply never match.
- **`readChanges` -> `watch`.** Rewrite the request/response poll loop into a stream
  consumer, not a substituted call inside the existing loop body -- this is a structural
  change to the calling code. If the source call site resumes by `start_time` (a wall-clock
  timestamp) rather than a saved `continuation_token`, there is no direct equivalent (`Watch`
  has no timestamp field at all): flag it as a Class A finding (step 8) rather than
  approximating a revision from the timestamp.
- **Non-transactional writes.** A call site using `transaction.disable`/`maxPerChunk` has no
  mechanical target -- `WriteRelationships` is always one transaction. This is a genuine
  fork with two options `code-mapping.md` itself states: (a) issue one SpiceDB `Write` per
  relationship, recovering partial-success semantics at the cost of N round trips, or (b)
  keep one batched `Write` and accept that one bad relationship now fails the whole group.
  Check `migration-map.json`'s `decisions.additional` for an entry keyed
  `non_transactional_writes_policy` first; if none exists, this is a **Class A finding
  resolved in a batch -- it does not stop the run.** Collect every such call site during this
  sweep rather than asking per site, keep converting, and put the collected set to the user
  in one `AskUserQuestion` at the end of this step (below), the same batching discipline the
  phase-0 gate uses. **"Halt" elsewhere in this pipeline means the phase stops and records
  `failed`; this one does not** -- the sweep must finish before the question can even be
  asked, and a phase that converted every call site and got its policy answered is
  `complete`, not `failed`. The only thing that stops here is *writing* a call site whose
  policy is still unanswered. Either way, the eventual answer is recorded into `decisions.additional`
  (`{"key": "non_transactional_writes_policy", "value": ..., "note": ..., "recorded_by":
  "/spicedb-dev:migrate-code step 6"}`), not appended to `migration-plan.md`'s Markdown
  directly.
- **Per-language check-signature divergence.** Never port a `check`/`check_permission`/
  `CheckOne` call's surrounding code from one target language to another mechanically.
  Confirm the target language's actual signature and return type in its own
  `spicedb-client-integration/references/<language>.md` file (Python: "Checks -- the
  permission divergence"; Rust: "Checks") before writing the call, not by copying a sibling
  language's already-converted call site. **For a Python target specifically, also confirm
  every converted check is actually `await`ed** -- `spicedb-python` is async-only, and a
  source call site ported from `openfga_sdk.sync.OpenFgaClient` has no `await` to begin with.
  A missing one hands back a bare coroutine, which is truthy in an `if`, so a denied check
  silently takes the allowed branch; neither `pyright` nor `mypy --strict` flags it, so step
  7's build check will not catch it either -- this needs a by-inspection review of every
  converted Python check call, not a tool run. See `code-mapping.md`'s "Async-only target vs.
  sync source: the un-awaited-coroutine fail-open."

**3. Resolve every relation name through `migration-map.json`'s `relation_splits`, per call
surface** (Class B, `code-mapping.md`'s "The relation-split obligation"). Phase 1 split every
source `define` that fused a `[...]` type list with an operator into a generated **relation**
plus a same-named **permission**, and recorded the pair. One source name therefore becomes
**two different strings** in the converted code, chosen by what the call *is*, not by
anything visible at the call site:

- **Write path** -- `Transaction`/`Txn` `.create()`/`.touch()`/`.delete()` -- and the
  **relation filter** of a relationship read, delete, or watch: `relation_splits[T][R]
  .relation`.
- **Check path** -- `CheckOne`/`Check`/`checkPermission`/`checkPermissions` and their
  per-language equivalents -- and the **permission argument** of `LookupResources`,
  `LookupSubjects`, and `ExpandPermissionTree`: `relation_splits[T][R].permission`.
- **Subject side** of any call (a userset subject like `group:eng#member`), on a write as
  much as on a check: `permissions[T][R]` -- never `relation_splits`.
- **Any relation absent from `relation_splits[T]`**: `permissions[T][R]`, unchanged, one
  name for both surfaces. Look every relation up; do not branch on whether one "looks"
  split.

Read the name out of the map. **Never build it by appending `__direct`** -- the suffix is a
gate decision (`/spicedb-dev:migrate` step 5, row 4, allows a project-specific one) and a
hardcoded one silently disagrees with the schema phase 1 emitted. This rule comes before the
codec rule below because both rewrite the *same* request object and both fail the same way:
at runtime, in production traffic, never at build time. Step 7's build check cannot see
either one -- these are string arguments, not types. `code-mapping.md`'s section has the live
transcript of all four failure modes; the two silent ones (a check or lookup left on the
split relation returns a narrower answer with no error; a `DeleteRelationships` filter naming
the permission reports success and deletes nothing) are why a passing smoke test proves
nothing here. Record every split relation touched as a Class B checklist row for
`migration-plan.md`'s **Deferred / manual** section (step 8), one per `type.relation` in
`migration-map.json`'s `relation_splits` (rendered, for a human, as the plan's **Relation
splits** table), with the `file:line`s on each side.

**4. Encode every resource or subject id built from application data through the step-3
codec, at the API boundary** (Class B, `code-mapping.md`'s "The identifier obligation") --
`encode(type, id)` for an id going into a request, `decode(type, id)` for an id read back
from a response that will be shown to a user or matched against application data, for every
type `migration-map.json`'s `id_encoding.types` lists. This applies to every call site
touching an encoded type's id, not only the ones this sweep happens to test -- record every
type rewritten this way as a checklist item for `migration-plan.md`'s **Deferred / manual**
section (step 8), per
`code-mapping.md`'s own instruction, so the code-side sweep is a checklist a reviewer can
tick off, not just a warning to remember.

**5. Classify every check call as dependent or independent of a preceding write, per
`code-mapping.md`'s "Consistency" section, and apply the matching rule -- never one
project-wide strategy applied uniformly to every call site.** This is not a re-decision of
anything the gate recorded; it is applying a per-call-site classification the gate's own
"Consistency strategy" decision (`migration-map.json`'s `decisions.consistency_strategy`)
does not by itself resolve:

- **Independent** -- no write earlier in the same request, or the request immediately
  before it, feeds this check's answer. Apply the plan's recorded literal mapping: the
  `HIGHER_CONSISTENCY`->`full()` / `MINIMIZE_LATENCY`, `UNSPECIFIED`->`minLatency()` mapping.
  This is faithful and cheap here -- `code-mapping.md`'s "Consistency" section.
- **Dependent (read-after-write)** -- the check's answer depends on a write earlier in the
  same request or the request just before it (the common shape: create a resource, grant
  a relationship on it, then check a permission derived from that grant). Apply
  `code-mapping.md`'s three-step rule, in order: (1) thread the ZedToken the write returned
  (`AtLeast(rev)`/`at_least(rev)`/`atLeast(rev)`, or `atLeastOrFull(rev)` -- never
  `atLeastOrMinLatency(rev)` -- when the revision may legitimately be empty); (2) if no
  ZedToken can be obtained or threaded at this call site at all, use
  `full()`/`fully_consistent`/`fullyConsistent` and leave a `TODO(spicedbmigration):` marker
  there, two lines maximum, per `findings-report.md`'s "Inline markers"; (3) never
  `minLatency()` on this path -- live-verified in `code-mapping.md`'s "Consistency" section to
  answer wrong, not merely stale, on the overwhelming majority of these calls.

`code-mapping.md`'s "Which `atLeastOr*` helper" section is why `atLeastOrFull(rev)`, not
`atLeastOrMinLatency(rev)`, is the right choice for a dependent check's no-revision fallback:
the two are identical wherever a revision is actually in hand and differ only in that
fallback, and a dependent check's fallback must be the strict one.

**6. Do not rewrite, and do not guess a target for, any operation `code-mapping.md`'s
"Operations with no SpiceDB target" section lists.** "Halt" here means halting *that call
site's rewrite*, not this command -- what actually happens next is one of two genuinely
different things, a hard command-level halt or a batched question that lets the sweep
continue, decided entirely by the branch immediately below. Read that branch before assuming
either one. The section, not this command, is the list of which operations these are; cite
it rather than counting it out here, so a construct added to it later needs no matching edit
in this file -- read it now for the current, complete set and for which entries, if any,
have a concrete SpiceDB-side pointer worth surfacing in the halt/question text. A subset of
these have a blocker-catalog entry and may already have a gate-recorded resolution:

**Classify every no-target call site into one of `findings-report.md`'s "Inline markers"
three cases before deciding what "leave a marker" means for it.** That decision is not "does
this construct have a row in `code-mapping.md`" -- it is **"does any caller consume this
call's result?"**:

1. **A caller branches on the result** (the common shape below: contextual tuples, model-ID
   pinning, and most "every other no-target construct" cases, since a check or a decision
   feeds application logic) -- **raise**, never return `false`. Returning `false` for "nobody
   implemented this" is indistinguishable from a real denial.
2. **A deliberate, gate-recorded decision to fail closed** -- **return `false`**, marked
   `TODO(spicedbmigration):`. This exception exists in exactly one place below (contextual
   tuples' "leave the call site failing closed" resolution) and nowhere else in this rule.
3. **Nothing consumes the result** -- a discarded return value, a side-effect or warm-up call.
   **Never raise.** Remove the call, or leave it inert with a `NOTE(spicedbmigration):`
   marker. A discarded-return construct has no decision for a raise to protect; raising it
   turns a no-op into a crash the source application never had -- confirmed live, and it is
   why this rule is stated as three cases rather than "always raise" (see the unhandled-
   construct paragraph below, and `findings-report.md`'s worked example for the exact case).

Do not default to case 1 just because a construct has no mechanical target -- check what the
call site's own callers do with the result before choosing.

- **Contextual tuples or model-ID pinning with a matching per-call-site resolution already
  recorded** in `migration-map.json`'s `decisions.per_blocker_resolutions` -- an entry whose
  `blocker` and `site` (`file:line`) match this call site, and whose `resolution` is not
  `null`/absent: apply it. The
  two blockers' catalogs (`blockers.md` items 2 and 4) offer **different** option sets --
  apply the one the recorded resolution actually names, not the other blocker's vocabulary:
  - *Contextual tuples*: "Materialize as a real relationship" means inserting a real
    `Write`/`Touch` call ahead of the check, not passing anything as request-scoped context
    -- SpiceDB has no such channel. "Re-model as caveat context" means the value moves into
    the check's caveat context argument, which requires the caveat to already exist on the
    target relation from phase 1; if it doesn't, that is itself a finding, not something to
    add here. "Restructure so the edge is persistent" means the same as materializing, but
    permanently rather than around one check. **"Leave the call site failing closed with a
    `TODO(spicedbmigration):` marker" is the one sanctioned exception to the raise rule above**
    -- the user was asked and chose, at the gate, to have this call site deny (`blockers.md`'s
    contextual-tuples entry): insert the marker and let the check return `false`, never `true`.
    This is a real, human-made decision recorded in `migration-map.json`'s
    `decisions.per_blocker_resolutions` (and rendered, for a human, under `migration-plan.md`'s
    **Decisions** section), not a stand-in for one -- nothing else in this rule 6 gets to
    return a boolean this way.
  - *Model-ID pinning*: "Drop pinning and accept the change" is mechanical -- omit the
    pinning parameter (`authorizationModelId` and equivalents) from the converted call
    entirely; `WriteSchema` has no versioned-schema concept for it to bind to anyway. This
    is the one model-ID-pinning resolution that converts cleanly with **no halt** at this
    call site, and it produces a real answer, not a raise. "Emulate with a schema-version
    gate" needs real application-side machinery (a relation, or a feature flag, consulted
    before the check) that only the gate's own resolution can describe -- if
    `migration-map.json`'s `decisions.per_blocker_resolutions` entry for this site doesn't
    already spell out that gate's concrete shape, do not invent one here. Unlike contextual
    tuples, `blockers.md` records no "fail closed" option for
    this blocker, so nothing has actually been decided about what the check returns: **raise**
    at the call site (never return `true` or `false`) with a `TODO(spicedbmigration):` marker
    citing the resolution, and record it as a finding needing further design (step 8), the
    same treatment as the no-resolution case below. "Flag for a manual rollout plan" means the
    gate already decided this site needs human rollout design, not automatic conversion --
    same result: **raise**, with a `TODO(spicedbmigration):` marker pointing at the plan's
    **Deferred / manual** entry for it.
- **Every other no-target call site** -- contextual tuples or model-ID pinning with **no**
  matching resolution on file (or a model-ID-pinning resolution that itself says the site
  needs a `TODO(spicedbmigration):` marker rather than a mechanical drop, immediately above),
  and every construct with
  no blocker-catalog entry at all (so none was ever put to the gate) -- collect all of them
  during this sweep rather than stopping at the first one. Once the sweep is complete,
  resolve the two kinds differently:
  - **A contextual-tuples or model-ID-pinning call site with no resolution at all** is an
    unresolved Class A finding the same way step 1's plan-level check is -- **halt**, list
    every such call site with its `file:line`, and direct the user back to
    `/spicedb-dev:migrate` to resolve it at the gate, using `blockers.md`'s real option list.
    Do not offer options for it here; reimplementing that catalog inline is the same "second
    gate" this command's intro already rules out. **"Halt" here means the run does not reach
    a `complete` phase-4 status, and this specific call site is left unconverted with a
    visible marker comment** -- it does not mean discard every other rewrite step 6 already
    completed for call sites that *did* have a clean target or resolution. An unresolved
    no-target finding is local to the call site it was found at, the same way a schema-level
    Class A finding is local to the construct that triggered it; it has no mechanical
    bearing on an unrelated call site elsewhere in the sweep, and abandoning already-correct
    work over one unresolved site would make a single blocker cost far more than it should.
    Step 8 marks phase 4 `failed`, not `complete`, whenever this occurs, and states plainly
    in the **artifact** column that this is an unresolved-finding halt, not a build failure
    -- `findings-report.md`'s closed vocabulary has no dedicated token for "ran, converted
    everything it could, and correctly stopped on an unresolved finding," so `failed` is the
    correct value to write, distinguished by what the artifact column says happened.
  - **Every other no-target construct** has no blocker-catalog options to offer, because
    SpiceDB has no construct for any of them, full stop -- there is nothing a gate re-run
    would add. Ask about these directly with `AskUserQuestion`, in as few batched calls as
    the tool allows, grouped by construct rather than one question per call site: leave the
    call site as-is, still calling the source system (a deliberate choice during a dual-write
    or phased cutover window) · remove it (the feature or call is being dropped) · replace it
    with hand-written application logic, marked with a `TODO(spicedbmigration):` for a human
    to implement. Where
    `code-mapping.md`'s own text for that construct names a concrete existing equivalent
    (like the validation-YAML pointer above), state it as part of the question and the
    recorded finding, so the answer isn't limited to leave/remove/replace when a real fourth
    option -- point at the equivalent artifact -- exists. This does **not** halt the rest of
    the sweep -- these constructs have no ambiguity about whether SpiceDB can express them
    (it can't), only about what the surrounding application (or pipeline script, for a
    construct step 5 found outside application code) should do instead, so record the answer
    and move on to the next call site rather than stopping the whole run. Record every answer
    in `migration-plan.md`'s **Deferred / manual** section with its `file:line`, and leave a
    matching `TODO(spicedbmigration):` marker at the call site itself (`findings-report.md`'s
    "Inline markers") so the finding is visible on the file, not only in the plan.

**A call site whose method does not appear in `code-mapping.md`'s call mapping table at all,
and is not one of the no-target operations `code-mapping.md` lists either, is an unhandled
construct.** Report
it exactly like an unhandled schema construct (`SKILL.md`'s "Red Flags": "Inventing a
translation... is worse than a halt") -- do not approximate it with whatever SpiceDB call
looks closest. No gate decision covers an unhandled construct, so classify it per
`findings-report.md`'s three-case rule ("Inline markers") the same way as any other no-target
site, by whether a caller actually consumes its result -- an unhandled construct's fidelity
rating says nothing about that, and deciding by rating instead of by caller is the scoping bug
this rule exists to prevent:

- **A caller consumes the result** (case 1) -- the converted code **raises**; leave the call
  site with a `TODO(spicedbmigration):` marker pointing at the plan's **Deferred / manual**
  entry for it.
- **Nothing consumes the result -- a discarded return value, a side-effect or warm-up call**
  (case 3) -- **never raise.** Remove the call, or leave it inert with a
  `NOTE(spicedbmigration):` marker pointing at the same entry. `findings-report.md`'s "Inline
  markers" worked example works exactly this shape -- a discarded-return, connection-warm call
  (`read_authorization_models()`) found by re-running this pipeline against a live
  application, whose first-cut literal-raise conversion crashed the first
  authorization-touching request of every process's life, silently breaking new-user setup --
  and it is why this closing rule no longer says "always raise."

Either way, the **Deferred / manual** entry carries the reasoning and any candidate mapping
found while investigating; the marker itself stays to two lines (`findings-report.md`'s
"Inline markers" -- verify it with the grep-based check that section defines, before this
phase reports `complete`). This is the case step 2's "no corpus" caveat exists to make
expected, not alarming: it means this run found something the mapping has not yet seen, not
that the command is broken.

**Verify every marker this step left, mechanically, before step 7.** Run the check
`findings-report.md`'s "Inline markers" defines (`grep -rn -A2
"TODO(spicedbmigration)\|NOTE(spicedbmigration)" <every file this run touched>`) over every
file step 6 edited. A marker whose comment block runs past two lines must be rewritten now --
move the excess into the `migration-plan.md` entry it points at -- not carried forward into
step 8's report as a known issue. Record the total marker count and the longest marker's
length in lines; step 8 and step 9 both need that number.

### Step 7: Compile / build check

Run the target language's own build or type-check step over every file this run touched (the
same commands step 4 used to confirm the vendored client itself builds). **A clean build
proves the rewrite compiles. It does not prove behavioral equivalence** -- the same
distinction `/spicedb-dev:migrate-tests` draws between a `zed validate`-clean assertion file
and one that actually encodes the source's behavior. Running the converted code against a live, fully migrated SpiceDB instance and
comparing its answers to the source system's is a **next step** (step 9), not something this
command performs itself: it depends on phase 3 having completed and passed verification, and
on the application actually running, neither of which this command controls.

A build failure here is almost always a real defect in the rewrite (a signature mismatch
step 6 rule 2's per-language divergence guidance exists specifically to catch, a missed
import from step 4's vendoring, or a type mismatch from a skipped codec call) -- fix it
against the cited reference before treating it as a client-integration or vendoring problem.

**If the project has no existing build or type-check step at all** (a TypeScript project run
only through `ts-node`/`tsx`/a bundler's transpile-only mode, with no `tsc` in its own
scripts; a dynamically-typed language with no linter/type-checker configured), add the
minimal version of one rather than skipping this step -- a script that only ever strips
types and runs was never proving the rewrite compiles in the first place, and step 4 already
established what "the vendored client itself builds" looks like in this project's language,
which is the same tool to point at the rewritten call sites now.

### Step 8: Update `migration-map.json`, then regenerate `migration-plan.md`

Every finding this run produced fits `code-mapping.md`'s own "Recording code-side findings in
`migration-plan.md`" taxonomy -- cited here, not re-derived:

- **Class A** (already resolved and applied, or halted and unresolved, per step 6 rule 6):
  a no-target call site **whose construct has a blocker-catalog entry** -- contextual tuples
  and model-ID pinning, `blockers.md` items 2 and 4 -- and every non-transactional-writes
  fork. These are the ones a gate resolution exists (or should exist) for.
- **Class B** (mechanical, but changes checked data, and must be seen and owned): the
  relation-split rewrite checklist from step 6 rule 3, one row per `type.relation` in
  `relation_splits`, naming the write-side and check-side `file:line`s separately; and the
  identifier-codec rewrite checklist from step 6 rule 4, one row per encoded type, with every
  `file:line` it touched.
- **Class C** (advisory, never halts): the **other four** no-target constructs -- store CRUD,
  AuthZEN, Permissions Index, `readAssertions`/`writeAssertions` -- which have no
  blocker-catalog entry and no gate options, so each is recorded with its `file:line` and the
  answer this run took, not escalated. Also every `batchCheck`-ordering rewrite, any
  `correlation_id` use beyond pairing with nothing to carry it, the `listRelations`
  error-handling policy chosen and which languages' default it matches or diverges from, the
  `expand`-tree-walker deletions, and the `readChanges`->`watch` transport-model rewrites.

Each of these is recorded in `findings-report.md`'s "Inline markers" required-reference
shape -- site `file:line`(s), the governing `code-mapping.md`/`blockers.md` rule by section,
a candidate mapping's verified-by-reading-source/inferred tag when this run found one, and
any pack-documentation gap the search surfaced -- the same shape phase 0's codebase sweep
already uses for its own code-side Class A findings. Where it lands (`migration-map.json`
versus `migration-plan.md`) depends on which of the two updates below it belongs to.

**Update `migration-map.json` first, before touching `migration-plan.md` at all** -- every
phase in this pipeline writes machine state to the JSON before rendering it, and this step is
no exception:

1. **`decisions.additional`** -- append an entry for any decision this run recorded that
   wasn't already appended at the moment it was made: a call-site language step 3 found the
   plan never mentioned, a `listRelations` error-handling policy, or a non-transactional-writes
   fork choice (`{"key": ..., "value": ..., "note": ..., "recorded_by":
   "/spicedb-dev:migrate-code step N"}`, naming whichever step actually decided it). If step 3
   or step 6 already appended these as they were decided, this is a no-op check here, not a
   second append.
2. **`phase_status["4"]`** -- mark it `complete` only if step 7's build check passed for
   every touched language, step 6 rule 6 produced zero halted, unresolved no-target call
   sites, **and step 6's marker-cap check found zero overruns** (or every overrun it found
   was rewritten before this step, not merely noted). Mark it `failed` if any of the three is
   untrue -- a build failure, an unresolved-finding halt, and an unrewritten marker overrun
   are all `failed`, since none reaches `complete`, but they are different problems and
   `artifact` must say which: a build error summary for the first, the list of halted
   `file:line`s with a note that everything else converted for the second, or the marker's
   `file:line` and its line count for the third (`findings-report.md`'s closed-vocabulary
   rule: detail goes in `artifact`, never the `status` field itself). List the vendored
   client path(s) and every rewritten file in `artifact` either way, plus the marker count
   and longest-marker length from step 6's check, and the second codec's path if step 3
   emitted one.

**Then, and only then, touch `migration-plan.md`.** Re-read it and write it back to the same
location, in two passes:

1. **Append to `## Deferred / manual`.** This section is narrative-only, with no JSON
   counterpart (`findings-report.md`'s `## migration-plan.md` section), so appending to it is
   a direct edit to the Markdown, not a render -- the one part of this step that is. Append
   every finding recorded above, and every no-blocker-catalog-entry resolution from step 6
   (rule 6), one row per entry. **Classify each into `### Needs action` or `### For the
   record` by which marker it cites**, per `findings-report.md`'s "Needs action vs. for the
   record" rule: an entry backed by a `TODO(spicedbmigration):` marker, or a resolution that
   still needs further human design, goes under `### Needs action`; an entry backed by a
   `NOTE(spicedbmigration):` marker, or resolved with nothing further to do, goes under
   `### For the record`. Step 6 already made this same case analysis for every marker it
   left -- classifying here is reading that decision back, not re-deciding it. Concretely,
   from the Class A/B/C breakdown above: the relation-split and identifier-codec **Class B**
   checklists (rule 3, rule 4) carry no marker -- mechanical, correct by construction -- so
   they file under `### For the record`, as do the `batchCheck`/`listRelations`/`expand`
   structural rewrites (**Class C**), which are likewise resolved with nothing further to do.
   Every entry actually left with a `TODO(spicedbmigration):` marker -- an unresolved
   no-target halt, a contextual-tuples "leave failing closed" resolution, a model-ID-pinning
   resolution that raises, or an "other four" no-target answer the user chose to implement as
   hand-written logic -- files under `### Needs action`, **regardless of which class (A or C)
   produced it**; every entry left with a `NOTE(spicedbmigration):` marker, or resolved
   mechanically with no marker at all (materialize, re-model as caveat, restructure
   persistent, drop pinning, an answered non-transactional-writes call site, an "other four"
   answer to leave-as-is or remove), files under `### For the record` instead. Class and
   marker are independent axes -- check the marker actually left at each site (rule 6's own
   instruction: an "other four" answer gets whichever of `TODO`/`NOTE` fits the answer
   chosen, never one fixed marker), not the class it came from.
2. **Regenerate the rendered sections in full** -- `## At a glance`, `## Needs your
   attention`, `## Decisions`, `## Identifier map`, `## Relation splits`, `## Arrow aliases`,
   and `## Phase status` -- from the `migration-map.json` just written, per
   `findings-report.md`'s "Two groups of sections, one rule each" rule.

**`## Source`, `## Scan scope`, `## Target`, and `## Sync obligations` are untouched by this
step.** This phase owns none of the facts they record, and this step's only writes to
`migration-plan.md` are the `## Deferred / manual` append and the rendered-section
regeneration above, per that same rule. Leave them byte-identical.

### Step 9: Report

Tell the user:

1. Which language(s) and source shape(s) were detected and converted, and where the SpiceDB
   client was vendored for each (step 4).
   **Required line, stated as its own point, not a parenthetical:** this run added a
   **not-for-production dependency** to `[project-dir]`'s manifest -- the vendored SpiceDB
   client is labeled by its own repository "PROTOTYPE -- not for production use," pinned to a
   commit rather than a released version, and free to break or change behavior upstream at
   any time. Name the manifest file(s) edited so the user knows exactly what to review, and
   name the alternative for the four languages that have one (Authzed's published
   `authzed-go` / `@authzed/authzed-node` / `authzed` / `Authzed.Net`), noting that adopting
   it means a different client API than the one this conversion targeted. Do not soften this
   into "the client is a prototype" -- say that the project has taken the dependency on.
2. **Counts**: call sites found (step 5), rewritten cleanly (step 6 rules 1-2), rewritten
   against a `relation_splits` entry (rule 3) -- stated as two numbers, write-side and
   check-side, since one source relation produces both -- rewritten with
   an identifier-codec encode/decode inserted (rule 4), and every no-target finding (rule 6)
   split into three buckets, not two -- **converted** (a gate resolution applied
   mechanically, e.g. model-ID pinning dropped), **left with a `TODO(spicedbmigration):`
   marker and recorded** (answered
   directly by this command, or a resolution that itself calls for further design, per rule
   6), and **halted, unresolved** (no resolution exists anywhere and the call site is
   unconverted). The third bucket is the one that keeps phase 4 out of `complete` status --
   name it explicitly rather than folding it into the second. State the count of any
   unhandled construct found (step 6's closing paragraph) with its `file:line`; this is the
   highest-priority item in the report. **Marker cap**: the total marker count and the
   longest marker's length in lines, from step 6's mechanical check -- a number, not
   "markers were kept short." A run with zero markers states that too, rather than omitting
   the line.
3. **Every "more than a rename" mapping this run actually touched**, one line each, per
   `code-mapping.md`'s section of that name -- which policy `listRelations` and the
   non-transactional-writes fork resolved to, which branch a wildcard-subject `check` took,
   where `writeAuthorizationModel`'s schema text now comes from, and that `expand`,
   `readChanges`, and `batchCheck` consumers were structurally rewritten rather than renamed.
   Point at `code-mapping.md`'s own worked before/after for anyone who wants to see why each
   needed more than a name change. Report the ones this run hit, not a fixed list.
4. **Build result** (step 7) -- pass or fail, per language, and say plainly that a pass proves
   the rewrite compiles, not that it answers the same questions the source system did.
5. That `migration-plan.md` was regenerated from `migration-map.json` (updated first, per
   step 8), and remains the durable record, per every earlier phase's own framing.
6. **Data before code, restated plainly.** Check `migration-map.json`'s
   `phase_status["3"].status`: if
   it is not `complete` with a passed verification, say explicitly that this converted code
   must **not** be pointed at this store's SpiceDB data yet -- every check against relationship
   data that hasn't landed fails closed, silently, for that resource. If phase 3 already
   passed, say so, and that the converted code is safe to run against that same target.
7. **This is not the last thing the plugin automates -- phase 5 and the cutover harness both
   still have commands behind them.** Say so plainly, and do not dead-end: state which phases are now
   complete (0, 1, 2, and 4 always, by construction of reaching this point; 3 and 5 per their
   own `phase_status["3"]`/`phase_status["5"]` entries) and which remain. If phase 3 or phase 5 is not yet `complete`,
   name the command that runs it (`/spicedb-dev:migrate-data` / `/spicedb-dev:migrate-tests`)
   and that either can run before or after this command, in either order relative to each
   other. Then, regardless of what remains: say plainly that nothing further in the
   phase-pipeline sense is automated -- phases 0 through 5 are the whole pipeline. What's left
   is deploying the schema if `/spicedb-dev:migrate-schema` step 9's deploy step hasn't run
   yet, running the converted application against a live SpiceDB instance loaded with this
   store's migrated data once phase 3 has passed verification, working through every item this
   run and every earlier phase recorded under **Deferred / manual** and **Sync obligations**,
   and then cutover. **Name `/spicedb-dev:migrate-verify` here**: once phase 3 has passed
   verification, it emits a differential harness
   (`migrating-to-spicedb/references/differential-harness.md`) implementing
   `migrating-to-spicedb/references/cutover-strategies.md` step 4 ("Dual-write, shadow-read")
   -- the bridge between a converted system and a safe cutover. Steps 5 through 7 of that
   playbook (the reconciliation job, the flag cutover, and removing the source system) remain
   the customer's own; say that too, rather than implying a tool exists for them.

## Error Handling

| Situation | Do this |
|---|---|
| No `migration-map.json` | Halt. Direct to `/spicedb-dev:migrate`. This command has no gate of its own. |
| `phase_status["0"].status` not `complete (full gate)` | Halt. Direct to `/spicedb-dev:migrate`, which detects this marker itself and re-runs the full gate. |
| Unresolved Class A finding in `decisions.per_blocker_resolutions` | Halt. List the unresolved blockers. |
| `phase_status["1"].status` not `complete`, or `schema.zed`/`migration-map.json` missing | Halt. Direct to `/spicedb-dev:migrate-schema`. |
| No pack, or pack has no code-mapping reference | Halt. An unsupported source needs a mapping written first. |
| `id_encoding.status` is `unresolved` or `unknown` | **Halt.** Violating object IDs exist or were never ruled out, and no encoder is being emitted, so converted code hard-errors on a live request path. Report `id_encoding.violations` and put the identifier options to the user. `mode: "none"` does not clear this -- only `status: "clean"` does. |
| `id_encoding.mode` is `base64url` for some type and phase 3 has never run (no codec file anywhere) | Halt. Direct to `/spicedb-dev:migrate-data` -- importing phase 3's exact codec is structural, not optional, once encoding is load-bearing. |
| Vendored client fails to build/import (step 4) | Fix the wiring against `installation.md`'s per-language recipe before rewriting any call site -- do not rewrite call sites against a client that doesn't build. |
| A call site's method has no row in `code-mapping.md`'s call mapping table and is not one of the no-target operations that section lists | Halt on that construct -- do not approximate it. Classify per `findings-report.md`'s three-case rule before choosing raise vs. marker-only: raise only if a caller consumes the result (case 1); if nothing does (a discarded return, a warm-up call), remove it or mark it `NOTE(spicedbmigration):` and do not raise (case 3). Report it with its source line either way. |
| Step 6's marker-cap check (`grep -rn -A2`) finds a marker whose comment block runs past two lines | Rewrite it before this phase reports `complete`, not after -- move the excess into the `migration-plan.md` entry it points at and shorten the call site to a two-line pointer. An overrun left unrewritten is a `failed` phase 4, with the marker's `file:line` and line count as the artifact. |
| Contextual tuples or model-ID pinning found at a call site with no matching per-call-site resolution in `migration-map.json`'s `decisions.per_blocker_resolutions` | Once the sweep is complete, leave that call site unconverted with a `TODO(spicedbmigration):` marker (`findings-report.md`'s "Inline markers") and mark phase 4 `failed` (not `complete`) -- this does not undo any other call site's already-applied rewrite. List every such call site. Direct back to `/spicedb-dev:migrate` to resolve at the gate; do not offer options here. |
| No-target call site with no blocker-catalog entry (see `code-mapping.md`'s "Operations with no SpiceDB target" section) | Does not halt the sweep. Ask with `AskUserQuestion`, batched by construct: leave as-is · remove · replace with hand-written logic (`TODO(spicedbmigration):`) · point at a concrete existing equivalent when `code-mapping.md` names one for that construct. Record the answer and a matching `TODO(spicedbmigration):`/`NOTE(spicedbmigration):` marker (`findings-report.md`'s "Inline markers"). |
| Non-transactional-writes call site with no recorded policy | Not a halt in the stop-the-phase sense. Collect during the sweep, keep converting, and resolve the whole set in one batched question at the end of step 6, per the two options `code-mapping.md` states. Phase 4 is `complete` once they are answered and applied. |
| `listRelations` call site with no recorded error-handling policy | Ask once, batched across every such call site -- it is a project-wide policy, not a per-site one. |
| Build/compile check (step 7) fails | Fix against the cited per-language reference before treating it as a vendoring problem. Do not mark phase 4 complete. |
| A call site touches a relation `migration-map.json` lists under `relation_splits` | Not an error -- rewrite it per step 6 rule 3: the split's `relation` on writes and on relationship read/delete/watch filters, its `permission` on checks, bulk checks, `LookupResources`/`LookupSubjects`, and `ExpandPermissionTree`; `permissions[T][R]` on any userset subject side. Never append `__direct` by hand. |
| `migration-map.json` has no `relation_splits` key at all | Treat it as `{}` and pass every relation through `permissions[T][R]` -- a map with no splits is the common case and loads exactly the same way (`findings-report.md`'s `relation_splits` section). |
| The codec's language doesn't match a call-site language needing conversion | Emit a second codec module in that language, following `data-mapping.md`'s ID-codec contract exactly (step 3, rule 4) -- do not skip encoding for that language. |

## Notes

- The version floor is SpiceDB **v1.52.0**; `code-mapping.md`'s rules and
  `installation.md`'s vendoring recipes were verified against v1.56.0, zed v0.31.1, and the
  SpiceDB client commit `installation.md` pins.
- **Ask in as few batches as the tool allows.** Step 3 may ask about a language ambiguity
  step 5's sweep surfaces that the plan didn't already resolve. Step 6 asks about
  `listRelations`'s error-handling policy once (project-wide), the non-transactional-writes
  fork once per batch of call sites (not per site), and every no-blocker-catalog-entry
  no-target construct (`code-mapping.md`'s "Operations with no SpiceDB target" section) once
  per batch grouped by construct. Every other rewrite in this command is a lookup against
  `code-mapping.md` or `migration-map.json`, not a decision.
- The plugin's internal test harness is **not shipped with this plugin**, the same rule every
  earlier phase in this pipeline states about it. Nothing in this command requires it,
  references it, or instructs a user to run it. It does now carry a small set of
  code-conversion fixtures -- a hand-converted app per source shape, run against live OpenFGA
  and live SpiceDB and compared answer-for-answer -- which is what keeps this command's own
  worked examples honest; it is **not** a corpus of real, pre-existing application code, so
  `code-mapping.md`'s "no corpus" caveat (step 2) stands unchanged, and there is still nothing
  here for a user to run.
- **This command has no corpus of real, pre-existing application code behind it either**, the
  same gap `code-mapping.md` states about itself. Treat an unhandled construct this run
  surfaces as new, useful information for hardening the mapping -- the same posture
  `openfga-to-spicedb/SKILL.md`'s Red Flags section already asks of every other phase in this
  pipeline -- not as a sign this command is broken.
- The client commit pin, and everything about obtaining it, lives in exactly one file:
  `spicedb-client-integration/references/installation.md`. This command cites it rather than
  repeating the commit hash or the per-language wiring steps, so that file staying the single
  source of truth as the clients move toward publication doesn't require an edit here too.
