---
name: migrate-verify
description: Emit a differential harness that dual-runs SpiceDB beside a live source system, diffs disagreements safely, and turns confirmed agreements into regression tests
argument-hint: "[project-dir] [output-dir]"
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

# Migrate Verify

This command implements `cutover-strategies.md` step 4, "Dual-write, shadow-read": it emits a
working differential harness into the customer's own project, in their language, wired to both
the source system and SpiceDB, so that a real cutover can run both systems side by side and know
whether they agree before a single user's access starts depending on SpiceDB alone.

**The distinction that governs everything below**: `tools/migration-harness/` is **ours** -- it
proves the *converter* is correct against a corpus this project controls, is never shipped, and
has no role here. What this command emits is **the customer's** -- it proves *their migration* is
correct against traffic this project will never see, and it ships into their repository with no
runtime dependency on this one. `differential-harness.md`'s own framing states this exactly:
"What ships is a specification, not a service this project operates... built once, by
`/spicedb-dev:migrate-verify`, and then it is theirs to run, extend, and eventually retire." Do
not reference `tools/migration-harness/`'s paths anywhere in what this command emits or tells the
user, and do not let a reviewer of this command conflate the two -- they solve different problems
for different owners.

**This command's own risk shape is different from every phase before it.** Phases 1, 2, and 5
produce artifacts a human reviews before anything downstream depends on them. Phase 3 writes to a
live system, but only once, behind an explicit confirmation. What this command emits runs
*continuously*, *unattended*, *beside live production traffic*, for as long as the shadow-read
window lasts. Its failure modes must never surface as a decision that affects a real user --
`differential-harness.md`'s "safety property" is not a nice-to-have here, it is the entire reason
this command is safe to ship at all -- and its comparisons must never manufacture a disagreement
that isn't real. Two known ways a harness does that anyway, both demonstrated live in the pack's
own references, are why every step below cites rather than re-derives its rules:

- **Name translation.** `source-adapter.md`'s "What getting it wrong looks like, live" section
  shows one field of one lookup -- reading a split relation's `.relation` instead of its
  `.permission` -- flip a record from `AGREE` to a candidate `DISAGREE` on a migration that
  converted correctly. A harness that reports this as a real regression sends a correctly
  converted resource type back for rework and repeats the false alarm on every future run.
- **Consistency staleness.** `differential-harness.md`'s "Diff" section measured a
  `minimize_latency` check fired immediately after the write that made it true returning the
  stale (pre-write) answer in 95.3% of trials at a ~360µs write-to-check gap, collapsing to 0% by
  5ms on `serve-testing`'s own hardcoded quantization window -- and states plainly that a real
  deployment's window (`--datastore-revision-quantization-interval`, 5 seconds by default) can be
  far larger. A harness that reports every stale-window disagreement as a genuine defect drowns
  real ones in noise from the first hour it runs.

Both are handled structurally below, not merely documented: this harness's check/lookup path
never reads a `.relation` field, and every candidate disagreement is reconciled against a captured
`zedtoken` before it is ever finalized.

**This is not one of the six pipeline phases** (`SKILL.md`'s phase table, phases 0-5). It is the
tool `cutover-strategies.md` names for its own step 4, "Dual-write, shadow-read": that file's own
"Where this sits in the pipeline" table lists step 4 as automated, and its step-4 section points
here for the harness's mechanics rather than restating them. Do not add a **Phase status** row for
it (step 8, below) -- it sits alongside the six phases, not inside them.

Outputs, written under `[project-dir]` (placement resolved in step 3) unless a step below says
otherwise:

- `migration/verify/source_adapter.<ext>` -- the pack's `observe()`/`ask()` entry points, in the
  project's own language.
- `migration/verify/harness.<ext>` -- the `Question`/`Outcome`/`DifferentialRecord` record shape,
  `Diff`, reconciliation, and the safety wrapper.
- `migration/verify/dual_run.<ext>` -- the out-of-band dispatcher and sampling configuration.
- `migration/verify/snapshot_to_assertions.<ext>` -- turns a batch of confirmed agreements into
  validation YAML.
- `migration-map.json` -- updated only if step 3 resolves a call-site language the plan had not
  recorded, appended to `decisions.additional` (step 8); this command never writes a
  `phase_status` entry -- see step 8 for why.
- `migration-plan.md` -- updated in place with a coverage record under **Deferred / manual**, and
  its rendered sections regenerated from `migration-map.json` when (and only when) that file
  changed this run (step 8; this command does not add a **Phase status** row -- see step 8 for
  why).

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each task
`in_progress` when starting and `completed` when done.

## Process

### Step 1: Read the migration plan

Read `migration-plan.md` from `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory) -- the same place
every earlier phase writes it -- or the path the user gave.

**If it does not exist, halt.** Say plainly that `/spicedb-dev:migrate` (phase 0) is this
pipeline's front door and must run first: it produces both `migration-plan.md` and the
`migration-map.json` this command applies to every name and id it translates.

**If it exists, check who wrote it before doing anything else** -- the same authorship check
`/spicedb-dev:migrate-data` step 1 and `/spicedb-dev:migrate-code` step 1 perform. Read
`migration-map.json`'s `phase_status["0"].status` -- **not** `migration-plan.md`'s rendered
**Phase status** table; no phase reads that table, per `findings-report.md`'s `##
migration-plan.md` section:

- **`complete (full gate)`** -- proceed.
- **`inline (reduced -- no codebase analysis)`**, missing, or any other value -- halt. Say
  plainly that this plan was never authored by the full gate, and direct the user to
  `/spicedb-dev:migrate`, which detects this exact authorship marker itself and re-runs the full
  gate, carrying the reduced gate's recorded decisions forward as defaults. Do not re-run the
  gate yourself -- this command has no `Task` access to the `migration-analyzer` agent.

**Also halt on an unresolved Class A finding**: read `migration-map.json`'s
`decisions.per_blocker_resolutions`, and if any entry's `resolution` is `null` or absent, list
them and stop.

**Then confirm phase 1 is actually done.** Read `migration-map.json`'s
`phase_status["1"].status`, and independently check that `[output-dir]` contains both
`schema.zed` and `migration-map.json` -- the JSON field and the files on disk can disagree if a
previous run was interrupted. If either check fails, halt and direct the user to
`/spicedb-dev:migrate-schema`. This command has nothing to translate names or ids against
without them.

**Note, but do not halt on, phase 3's and phase 4's status** -- read `migration-map.json`'s
`phase_status["3"]` and `phase_status["4"]` now and carry both through to step 9, the same
"note, don't halt" treatment
`/spicedb-dev:migrate-code` step 1 gives phase 3 and phase 5. But this command's own version of
that note carries a sharper consequence than migrate-code's, and must be stated as such, not
folded into the same soft warning:

- **If phase 3 is not `complete` with a passed verification**, this command may still *emit* the
  harness -- the code itself is safe to write regardless -- but the harness's target-side
  answers are only meaningful once the data those answers depend on is actually loaded and
  verified. Turning dual-run sampling on above zero against a store phase 3 hasn't finished
  manufactures exactly this command's own worst failure mode from the data side rather than the
  naming side: the source's real `ALLOWED` answers get compared against a target answering
  `DENIED` everywhere, and every one of those is a false `DISAGREE`, not a defect. State this
  plainly here and again in step 9 -- it is a required line, not a footnote.
- **If phase 4 has not run for this project at all**, note it: this command still needs a real
  SpiceDB client to make its own dual-run call with (step 3), and will vendor one itself,
  following the exact procedure phase 4 would have, if none is already present.

**Check for an existing harness before writing over one.** If `migration/verify/` (or wherever
step 3's placement check resolves) already holds files from a previous run, use
`AskUserQuestion` to confirm before overwriting -- a customer may already have wired `observe()`
calls into their own code against the current file layout, and silently replacing it could break
that wiring without warning.

### Step 2: Load the differential-harness contract and the pack's source adapter

Read `migrating-to-spicedb/references/differential-harness.md` **in full** before writing
anything. It is the contract this command emits against: the record shape (the five-state
`Outcome` vocabulary -- `ALLOWED`, `DENIED`, `CAVEATED`, `ERRORED`, `NOT_ANSWERED`), the ordered
`Diff` rules, the safety property, and the sampling guidance. Every step below cites it rather
than restating it -- if this command's wording and that file's ever disagree, the reference file
is authoritative and this command is stale.

Read the plan's **Source** section for the detected system and look it up in the
`migrating-to-spicedb` skill's source registry -- the same lookup
`/spicedb-dev:migrate-data` step 2 and `/spicedb-dev:migrate-code` step 2 perform. For OpenFGA,
Okta FGA, or Auth0 FGA that is `openfga-to-spicedb`, and for Oso Cloud `oso-to-spicedb`; read its `references/source-adapter.md` **in
full** before converting anything -- it is the seam `differential-harness.md` defines and
declines to fill in, and every step below cites it the same way. If the plan's source has no
pack, or the pack has no `source-adapter.md`, stop: an unsupported source needs an adapter
written first, the same halt pattern `/spicedb-dev:migrate-data` and `/spicedb-dev:migrate-code`
apply to a missing data- or code-mapping reference.

State plainly, once, before building anything: `source-adapter.md` defines **two entry points
sharing one pair of lookup tables**, not one function -- `observe(native_request,
native_response_or_error) -> (Question, Outcome)`, the forward direction Dual-run alone uses, and
`ask(question) -> Outcome`, the reverse direction Replay's parity mode alone uses. This command
implements both, in the shape that file specifies, because both are load-bearing for a different
capability this harness must support.

### Step 3: Confirm the target, the project's language(s), and the SpiceDB client

**Target.** Read the plan's **Target** section for the SpiceDB endpoint. Confirm the schema is
actually deployed there -- this command does not deploy it -- the same check
`/spicedb-dev:migrate-data` step 3 performs:

```bash
zed schema read --endpoint <endpoint> --token <token> --insecure   # drop --insecure over TLS
```

If the read errors, comes back empty, or is missing a definition `migration-map.json`'s `types`
table names, halt and say the target needs `schema.zed` deployed first.

**Language(s).** The harness must be written in whatever language(s) the call sites it will wrap
are written in -- not necessarily phase 3's migration-script language, and not necessarily a
single language. Resolve this the same way `/spicedb-dev:migrate-code` step 3 does: start from
the plan's **Source** section (still a Markdown read -- `## Source` has no JSON counterpart) and
`migration-map.json`'s `decisions.additional` for a `call_site_language` entry already recorded,
and where that's silent or ambiguous, sweep `[project-dir]` for a dependency manifest and ask
with `AskUserQuestion`. If step 5's language turns out to differ from what was recorded, append
it to `decisions.additional` (step 8) the same way `/spicedb-dev:migrate-code` does, rather than
silently converting against an unrecorded assumption.

**The SpiceDB client.** Check `migration-map.json`'s `phase_status["4"]` and `[project-dir]`
itself for an already-vendored client (the manifest markers
`spicedb-client-integration/references/installation.md` describes -- a `replace` directive in
`go.mod`, a workspace-protocol entry in `package.json`, and so on, per language).

- **"Vendored" means an actually importable client is on disk, not merely a `phase_status`
  claim.** A phase-4 entry reading `complete` is a starting point to check, not proof by itself
  -- confirm the manifest marker `installation.md` describes *and* that the client actually
  builds/imports (below) before treating it as reusable. An entry that claims a vendored client
  but resolves to no importable code (a placeholder, an interrupted vendor, a directory with
  nothing but a README) is the same case as no client being vendored at all -- treat it that
  way, vendor fresh per the second bullet below, and say so plainly in the report (step 9) as a
  discrepancy between the JSON and what's actually on disk, the same "the field and the files
  on disk can disagree" caution `/spicedb-dev:migrate-data` step 1 states for its own inputs.
- **If phase 4 already vendored one for this language**, reuse it. Confirm it still builds/imports
  (the same check `/spicedb-dev:migrate-code` step 4 performs) rather than vendoring a second,
  independently-pinned copy -- two copies of a client pinned to different commits is a real,
  avoidable source of drift this command has no reason to introduce.
- **If no client is vendored for this language yet**, vendor one now, following
  `spicedb-client-integration/references/installation.md` exactly -- the same procedure
  `/spicedb-dev:migrate-code` step 4 follows, cited rather than restated. Pin to the commit that
  file records, wire the per-language dependency-manifest recipe it gives, and confirm the client
  builds/imports before continuing.
- **Either way, state the same not-for-production line `/spicedb-dev:migrate-code` step 9
  requires as a required line of this command's own report (step 9)** if this command is the one
  taking on the dependency: the vendored client is labeled by its own repository "PROTOTYPE --
  not for production use," pinned to a commit rather than a released version. Name the manifest
  file(s) touched and the four-language published-client alternative (`authzed-go`,
  `@authzed/authzed-node`, `authzed`, `Authzed.Net`), per `installation.md`'s own guidance,
  exactly as `/spicedb-dev:migrate-code` does.

**What this harness needs from the client, and nothing more.** `CheckPermission`
(or the bulk/lookup equivalent), `LookupResources`/`LookupSubjects` (advisory sampling only, step
5), and `ReadRelationships` at a pinned `zedtoken` (snapshot-to-assertions only, step 7) --
**never** `WriteRelationships` or a `Transaction` of any kind.

**Confirming the client "exposes" a check call by method name is not sufficient, and doing only
that is this command's second-highest-consequence mistake, after the `.relation`/`.permission`
one in step 4.** Every idiomatic vendored client at the pinned commit collapses its check RPC's
real, four-value `Permissionship` enum (`UNSPECIFIED` / `NO_PERMISSION` / `HAS_PERMISSION` /
`CONDITIONAL_PERMISSION`) down to a bare boolean before this harness ever sees it, and none of
them exposes the response's `CheckedAt`/`checked_at` token through that wrapper at all.
**Verified directly against the pinned commit, all seven languages, by source and (for Go) by
live call** -- and re-verified language by language rather than generalized from any one of
them, which is how Java came to be checked at all after an earlier revision of this section
asserted "none of the seven" having examined six:

- **Go** (`spicedb-go/client/checks.go`): `results[i] = item.GetPermissionship() ==
  v1.CheckPermissionResponse_PERMISSIONSHIP_HAS_PERMISSION` -- `CONDITIONAL_PERMISSION` and
  `NO_PERMISSION` both collapse to `false`, indistinguishable to any caller of `client.Check`/
  `client.CheckOne`, and the method's `([]bool, error)` return has nowhere to carry `CheckedAt`
  either. **Confirmed live**, a caveated relationship with no bound context checked through the
  idiomatic wrapper vs. the raw generated stub (below) on the same request:
  `PERMISSIONSHIP_CONDITIONAL_PERMISSION` and `PERMISSIONSHIP_NO_PERMISSION` are two distinct,
  inspectable values from the stub; both would read as plain `false` through `client.Check`.
- **Python** (`spicedb/client.py`): `results.append(pair.item.permissionship == HAS)` -- the
  identical collapse, same direction (fails *closed*: a caveated permission with no context
  reads as `false`, same as a genuine denial).
- **TypeScript** (`spicedb-typescript/src/client.ts`), the opposite direction: `resp.permissionship
  === CheckPermissionResponse_Permissionship.HAS_PERMISSION || resp.permissionship ===
  CheckPermissionResponse_Permissionship.CONDITIONAL_PERMISSION` -- the client's own JSDoc states
  this outright: `"@returns \`true\` if the subject has the permission, \`false\` otherwise.
  Caveated (conditional) permissions return \`true\`."` This one **fails open**, not closed.

The remaining four collapse the same way as Go and Python (fails *closed*), each confirmed in its
own source at the pinned commit rather than assumed:

- **Rust** (`spicedb-rust/src/client.rs`): `item.permissionship == Permissionship::HasPermission
  as i32`, pushed into a `Vec<bool>`.
- **C#** (`spicedb-csharp/SpiceDB.Client/SpiceDBClient.cs`): `results[i] =
  pair.Item.Permissionship == ...Permissionship.HasPermission`.
- **Ruby** (`spicedb-ruby/lib/spicedb/client.rb`): `pair.item.permissionship ==
  :PERMISSIONSHIP_HAS_PERMISSION`, surfaced as `{ has_permission: ... }`.
- **Java** (`spicedb-java/lib/src/main/java/com/authzed/spicedb/SpiceDBClient.java`):
  `pair.getItem().getPermissionship() == Permissionship.PERMISSIONSHIP_HAS_PERMISSION`, and the
  method's declared `List<Boolean>` return has nowhere to carry `CheckedAt` at all.

**At the pinned commit, all seven collapse the enum and none of the seven exposes the token.**
Six fail closed and
TypeScript fails open -- seven clients, two different answers, and not one of them able to
express the state. Both halves of that split are counted from the tree rather than asserted, and
the two counts are each other's complement, so neither can be edited on its own: over the seven
check-surface files listed above, `grep -ci conditional` on each returns non-zero for exactly one
(`spicedb-typescript/src/client.ts`, 3) and zero for the other six -- a wrapper that never names
`CONDITIONAL_PERMISSION` cannot be admitting it, so it collapses caveated to `false` and fails
closed. **Six fail closed and one fails open by that count, and every count of "how many languages
do X" in this section must come from re-running it, never from adjusting a digit.**
The `CheckedAt` half is absolute rather than "most": a search for
`checked_at`/`CheckedAt`/`checkedAt` across all seven idiomatic wrappers' own source at the
pinned commit returns **zero** hits.

**This describes the pinned commit, which is what the pack vendors -- it is not a standing
property of the clients.** Upstream `main` has since made the check surface three-valued in all
seven and exposed `checked_at` (and `looked_up_at`). Re-run the counts above against whatever
commit the project actually vendors before relying on either half; if it is not the pinned one,
the harness's revision handling below can use the client's own token instead of the fallback.

**Why this is stated here rather than only cited.** The two underlying facts are already in the
pack: `differential-harness.md`'s "Outcome" section names
`PERMISSIONSHIP_CONDITIONAL_PERMISSION` as one of the check RPC's three non-error values, and its
"Dual-run" section verifies `CheckPermissionResponse.checked_at` comes back populated on every
response (both cited elsewhere in this command already). What is *not* recorded anywhere else is
the finding above -- that the pack's own vendored clients discard both before this harness could
ever see them. Cite those two files for the protocol facts; this section is the authority only
for the client-surface finding.

**Why this matters more than a missing field**: if this command's dual-run dispatcher (step 6)
is built against a wrapper that fails this way, `target.outcome` can never actually become
`CAVEATED` -- `Diff` rule 2 never fires -- and `target.zedtoken` can never be populated --
reconciliation (step 5) can never run. A caveated permission's real target answer silently
becomes `DENIED` (six of seven languages) or `ALLOWED` (TypeScript), and on the fail-closed
languages a source `ALLOWED` becomes a **manufactured `DISAGREE`** that reconciliation then
re-asks through the same broken client and finalizes as real -- the exact false-disagreement
failure this whole command exists to prevent, arriving through the client instead of through
name translation or staleness.

**Before continuing, confirm -- for the actual installed/vendored client, not by trusting the
findings above to generalize without checking -- that its check surface preserves all three
non-error `Permissionship` values as distinct, inspectable values (not collapsed into a boolean
at any layer this harness will call through), and separately returns the check-time revision
token on every response.** If the idiomatic wrapper does both, use it. **If it does not, fall
back to the client's own raw generated stub** -- the pinned commit vendors it as a sibling
package alongside the idiomatic wrapper for every language (`installation.md`'s "What to vendor:
two directories, not one" table names the pair, one row per language; for Go,
`proto-clients/spicedb-go-proto`'s generated
`PermissionsServiceClient`, constructed directly via `proto.NewClient(...)` rather than through
`spicedb-go/client`), and call its `CheckPermission`/`CheckBulkPermissions` RPC directly instead
of the idiomatic wrapper's check methods. This is sanctioned, not a workaround: the generated
message types carry both facts in full because the collapse happens in the idiomatic wrapper's
own code, not in the wire protocol underneath it. **Verified directly, live**, bypassing
`spicedb-go/client` entirely and calling the raw `PermissionsServiceClient.CheckPermission` RPC
on the same caveated relationship used above:

```
subject=alice  permissionship=PERMISSIONSHIP_CONDITIONAL_PERMISSION  checked_at=token:"Gh8K..."
subject=bob    permissionship=PERMISSIONSHIP_NO_PERMISSION           checked_at=token:"Gh8K..."
```

Two distinct `Permissionship` values and a real `CheckedAt` token on both responses, from the
exact construction the idiomatic wrapper collapses to `false, false` with no token at all.
Record which path (idiomatic wrapper or raw stub) this run actually used for the check surface,
in `migration-plan.md` (step 8) and in the report (step 9) -- a client-sufficiency fact a later
reviewer needs to be able to check without re-deriving it. If neither the wrapper nor the raw
stub is reachable (a build/import failure on the raw stub's own package), that's the same
"client doesn't build" halt `/spicedb-dev:migrate-code`'s Error Handling table already states.

**Placement.** Default to `[project-dir]/migration/verify/`. If the project already has an
obvious home for SpiceDB-related code (the same `internal/authz`, `spicedb/`, or similar
directory `/spicedb-dev:migrate-data` step 3 looks for when placing the codec it emits), that is a
reasonable place to put this harness's modules too, since a human will be importing `observe()`
from real call sites shortly. Ask with `AskUserQuestion` if it is not obvious, and record the
chosen path in `migration-plan.md` once written (step 8) -- nothing else in the plan says where
this landed.

### Step 4: Load `migration-map.json` and the ID codec -- name and id translation

Read `migration-map.json` from `[output-dir]`. This is what makes every name and id this harness
resolves a lookup, not a judgment call -- the same file `data-mapping.md` and `code-mapping.md`
already establish as the single source of truth for phases 3 and 4, read the same way here for a
third purpose.

**This lookup happens twice, at two different times, and the emitted code must handle both.**
This command reads the map *now*, at emission time, to decide what to generate. The harness
itself reads it *again*, at every request, once it is deployed and running beside production --
and `[output-dir]` (where the map lives) and `[project-dir]` (where the harness runs) are not
guaranteed to be the same directory or even the same host. Do not hardcode a relative path to
`migration-map.json` (or the ID codec) that only resolves correctly from this command's own
working directory at emission time -- make the map's runtime location an explicit, documented
configuration value of the emitted harness (an environment variable, a constructor argument,
whatever idiom `[project-dir]`'s own configuration already uses), and say in the report (step 9)
what that value defaults to and that the customer must confirm it resolves in their actual
deployment.

**State plainly, because it is this command's single highest-consequence mistake**
(`source-adapter.md`'s own framing, demonstrated live in its "What getting it wrong looks like"
section): every name this harness's check/lookup path resolves goes through **`.permission`**,
never `.relation`.

- **Resource-side permission, and the permission argument to `LookupResources`/`LookupSubjects`**:
  `relation_splits[T][R].permission`, falling back to `permissions[T][R]` when `R` never split.
- **A userset subject's own relation (`T#R`, e.g. `group:eng#member`)** is a *separate* row of
  `code-mapping.md`'s name-resolution table, not the same one: `permissions[T][R]`, **never**
  `relation_splits[T][R]` in either field. That file's own reasoning applies unchanged here -- the
  type restriction phase 1 emitted names the permission, so a userset naming the split relation
  is not an allowed subject type at all -- and `source-adapter.md`'s forward table states the
  identical rule for `observe()`. Step 5 below repeats it for the adapter; the two must agree.
- **The write-target relation name, `relation_splits[T][R].relation`, has no consumer anywhere in
  this harness.** Dual-run and Replay's parity mode each make exactly one call --
  `CheckPermission` (or its bulk/lookup equivalent) -- and never a write; `source-adapter.md`'s
  "Why `relation_splits[T][R].relation`... never appears anywhere in this adapter" section states
  this directly. **Never construct a split name by appending `__direct`** -- the suffix is a gate
  decision (`/spicedb-dev:migrate` step 5, row 4) that a project can configure differently, and
  the field this harness needs is `.permission`, not the write-target name, regardless of what
  that suffix happens to be.
- **Building `ask()`'s reverse index** (source relation name, from a stored `Question`'s SpiceDB
  permission name) is well-defined only because the forward map is guaranteed injective
  (`findings-report.md`'s `migration-map.json` section, cited by `source-adapter.md`) -- build
  `{spicedb_permission_name: source_relation_name}` once, at start-up, from `permissions[T]` and
  `relation_splits[T][*].permission` together, never from `.relation`. Do not reconstruct a source
  name by guessing (stripping a suffix, assuming identity) -- read it from the map, the same
  "never construct it, read it" discipline `code-mapping.md` states for the forward direction.

**The ID codec.** Locate it the same way `/spicedb-dev:migrate-code` step 3 does:

1. Read `migration-map.json`'s `phase_status["3"].artifact` for the codec's recorded path, or
   look under `[output-dir]/migration/id_codec.<ext>` if it is silent.
2. If `id_encoding.mode` is `"none"` for every type, importing phase 3's file is still preferred
   over skipping it, for the same forward-compatibility reason `/spicedb-dev:migrate-data` step 4
   states; if phase 3 has never run and no codec exists anywhere, proceed without one.
3. If `id_encoding.mode` is `"base64url"` for any type, the codec is load-bearing and must exist
   on disk. **If phase 3 has never run, halt** -- direct the user to `/spicedb-dev:migrate-data`
   first, the same halt `/spicedb-dev:migrate-code` step 3 rule 3 applies, for the identical
   reason: writing a fresh codec here instead of importing phase 3's exact file would be the
   "one codec, two consumers" failure `naming-normalization.md` warns about, now with a third
   independently-written implementation to disagree with the other two.
4. If the codec's own language doesn't match this harness's target language, emit a second module
   in that language, following `data-mapping.md`'s ID-codec contract exactly -- the same
   `/spicedb-dev:migrate-code` step 3 rule 4 already applies verbatim here.

This module is now a **third** consumer of that exact codec file -- phase 3's own script, phase
4's rewritten call sites, and now this harness's `dual_run.<ext>` dispatcher. Import it; do not
reimplement `encode`/`decode` a third time.

- `observe()` translates an OpenFGA-native id **into** SpiceDB vocabulary: `encode(source_type,
  source_id)`.
- `ask()` translates a stored `Question`'s SpiceDB id **back** into an OpenFGA-native one:
  `decode(source_type, spicedb_id)`, fed the source type an inverted `types` lookup just
  produced.
- The wildcard subject id (`*`) is never encoded or decoded on either path, regardless of type.

### Step 5: Emit the harness core

Write `migration/verify/harness.<ext>` and `migration/verify/source_adapter.<ext>`.

**`harness.<ext>` -- the record shape, `Diff`, reconciliation, and the safety wrapper.**

Implement `Question`, `Outcome`, and `DifferentialRecord` exactly as `differential-harness.md`'s
"The record shape" section defines them, field for field -- do not add or drop a state from the
five-value `Outcome` vocabulary, and do not let `disposition` start anywhere but `UNTRIAGED`. The
reason those five states exist, and why collapsing any two of them defeats the whole contract, is
explained there, not here. **`question.origin` (`CHECK` / `BATCH_CHECK` / `LIST_SAMPLED`) is a
required field with no default**, and is the one field whose absence would be easy to read as
cosmetic: it is what makes step 7's "never snapshot a list-derived record" a test the code can
run rather than a convention the caller has to remember, for the same structural reason the
`Outcome` vocabulary keeps `ERRORED` out of `DENIED`.

**Reconciliation's *policy* lives here; its *I/O* does not.** `Diff` rule 4's reconciliation step
needs a live SpiceDB call (the re-ask) -- but step 3 places the actual RPC dispatch in
`dual_run.<ext>`, the module that owns the real client and every outbound call this harness makes.
Resolve the split with dependency injection, not by picking one file to own both halves: `harness.<ext>`
owns the record shape, every rule in the ordered list `differential-harness.md`'s "Diff" section
defines (that section is where the rules are enumerated and therefore where they are counted --
do not restate a number here that a later rule addition would silently falsify), the
bounded-retry policy (how many attempts, what
counts as exhausted), and the safety wrapper -- and calls the re-ask through a callable
`dual_run.<ext>` passes in, rather than importing a client or holding a connection itself.
`dual_run.<ext>` owns constructing that callable from the real client and nothing about `Diff`'s
rules. This keeps `harness.<ext>` testable against recorded fixtures with no live SpiceDB
connection required, matches the two-module split the Outputs list above already draws (record
shape/`Diff` vs. dispatcher), and is the answer if an implementation is tempted to fold the whole
reconciliation loop into `dual_run.<ext>` instead: keep the re-ask policy in `harness.<ext>`, feed
it the call.

**This is where step 3's client-surface check actually gets consumed, and where a regression is
easiest to reintroduce by accident.** `target.outcome = CAVEATED` and `target.zedtoken` are only
ever as real as what the client call underneath them returns -- if the code populating this
struct calls the idiomatic wrapper step 3 found insufficient (rather than the raw stub step 3
required as its fallback), `CAVEATED` becomes structurally unreachable here no matter how
faithfully the rest of this section is implemented, because the wrapper already discarded the
distinction before this code ever ran. Populate `target.outcome`/`target.zedtoken` from whichever
call step 3 actually confirmed preserves all three non-error `Permissionship` values and returns
`CheckedAt` -- not from whichever call was easiest to reach for while writing this file.

**Implement `Diff`'s ordered rules from `differential-harness.md`'s "Diff" section itself, read
open beside the code -- do not implement them from a restatement here, including this one.** That
section is the authoritative enumeration: its rules, their order, each rule's `verdict`/`reason`
pair, and -- for rule 4 -- the full set of branches its reconciliation re-ask can land in, each
with the exact `verdict`/`reason` it finalizes as. This command deliberately does **not** repeat
that list or its count. A restatement here that enumerates fewer branches than the reference
defines reads as complete and self-contained, so an agent implementing from it never consults the
reference, and this command's own "if this command's wording and that file's ever disagree, the
reference file is authoritative" safeguard (step 2) cannot fire against a wording that never
visibly disagrees with anything. That is exactly how an earlier revision of this command shipped
a three-branch rule 4 against a four-branch reference -- which finalized `DISAGREE`/`UNTRIAGED`
on a correctly-migrated caveated resource, the precise failure this command exists to prevent.

Two obligations that are this command's own, and are stated here because the reference does not
carry them:

- **Every branch the reference defines must exist in the emitted code**, as its own distinct,
  reachable path. Count them in the reference and check the emitted `Diff` against that count
  before moving on; a branch collapsed into a neighbouring one is a real defect, not a
  simplification.
- **Populate the record's `reconciliation` block** (`differential-harness.md`'s "The record
  shape": `attempted`, `attempts`, `outcome`, `zedtoken`) from the re-ask on **every** one of
  those branches, not only the one that reclassifies to `STALE_READ` -- it is the re-ask's own
  evidence, independent of which way `Diff` finalized the record, and a record where
  reconciliation was never attempted at all leaves it `attempted: false` rather than a fabricated
  zero value.

**Reconcile only candidate disagreements from rule 4, never every record** -- re-asking at a
stronger consistency for every dual-run call reintroduces the cache-bypass cost dual-run's own
`minimize_latency` default exists to avoid, at the scale of all sampled traffic instead of the
much smaller set that actually disagreed.

**The reconciliation re-ask's consistency**: `core-concepts.md`'s consistency-helpers table names
the call -- `AtLeast(rev)` / `at_least(rev)` / `atLeast(rev)`, per language -- fed the target's
own captured `zedtoken` from the original record. **Never `AtLeastOrFull`/`AtLeastOrMinLatency`**
-- both differ from plain `AtLeast` only in their no-revision fallback, and that fallback is not
this call's to make: `AtLeastOrFull` in particular would silently upgrade every reconciliation
call to a full cache-bypass read the moment a revision went missing.

**And a revision can go missing -- do not emit code that assumes otherwise.** If
`target.zedtoken` is null on a candidate disagreement, reconciliation cannot run at all: finalize
`INCONCLUSIVE`/`RECONCILIATION_FAILED` (`differential-harness.md`, rule 4's own ruling for this
case), never a re-ask at some weaker consistency reported as though a revision had been pinned.
The dominant cause is step 3's client-surface finding arriving one layer later: a wrapper with no
`CheckedAt` field produces a null token on **every** record, so this is not an occasional
degradation but a uniform one -- 100% of candidate disagreements finalizing
`RECONCILIATION_FAILED` while the `AGREE` rate over the remaining comparable records reads
perfect. `differential-harness.md`'s "The health gate" is what catches that shape; step 6 below
is where its numbers get set.

**The safety wrapper -- implement every point of `differential-harness.md`'s "The safety
property" section as code, not documentation**:

- Read-only to the source of truth: never a write call to the source anywhere in this harness, in
  any capability. In **dual-run** specifically, never even a fresh *read* call to the source --
  `observe()` only translates an already-computed decision. **Replay's parity mode is the scoped
  exception** (`differential-harness.md`'s safety property, point 1): `ask()` really does call the
  source, by design, and must therefore be offline, operator-initiated, off every request path,
  and rate-limited or pointed at a read replica. Emit that limit as a required configuration
  value of the parity-replay entry point, not as a comment -- an unbounded replay of a corpus
  the shadow window sized is a load test against the system still authoritative for real users.
- Never a decision path: the dual-run result has no code path back into the request that
  triggered it. It is logged and diffed, never returned to any caller and never consulted by any
  authorization gate, in any `DifferentialRecord` state, agreement or not.
- Every internal failure -- a crash, a timeout, a lost connection to SpiceDB, an exception
  anywhere in the comparison pipeline -- surfaces as a missing or `ERRORED`/`NOT_ANSWERED` record.
  **Never `DENIED`, and never a propagated exception that could affect the caller.**
- Persisting a `DifferentialRecord` is itself best-effort and asynchronous: losing one to a
  storage failure is a lost data point, never a failed production request.

**For a Python target specifically**: the same un-awaited-coroutine hazard `code-mapping.md`
documents for a converted call site (neither `pyright` nor `mypy --strict` catches it) applies to
this harness's own dual-run `CheckPermission` call -- review every `await` here by inspection.
Because the safety wrapper above already requires every internal failure to degrade to a missing
or `NOT_ANSWERED` record rather than propagate, a missed `await` here does not fail anything open
the way it does at a converted call site (this result is never on a decision path) -- but it does
silently degrade the harness into permanent, undetected `NOT_ANSWERED` noise at that call site,
which is real evidence the harness is blind there, not a cosmetic bug. Say so if found.

**If the calling code this dispatcher wraps is itself synchronous** (a Flask view, a Django
view, a Celery task defined as plain `def`) and the project already bridges it to the async-only
target via a persistent background event loop plus `run_coroutine_threadsafe` (`code-mapping.md`'s
"The synchronous-caller bridge, and a second-order deadlock in the obvious version of it"), **this
dispatcher's own lazy client construction is exactly where that deadlock reappears.** Resolve the
SpiceDB client (and any other shared resource this dispatcher needs) on the calling thread before
scheduling any coroutine onto that background loop -- never by calling the loop-dispatch helper a
second time from inside a coroutine the helper already scheduled there. `code-mapping.md`'s section
above has the verified reproduction and the structural fix; cite it, don't re-derive it, and confirm
by inspection that this dispatcher's own client resolution follows it before trusting a clean dry
run against a small question set, since a small set may not exercise the lazy-construction path at
all if the client happens to already be warm.

**`source_adapter.<ext>` -- the pack's `observe()`/`ask()`, per `source-adapter.md`.**

Implement, for the plan's detected source:

- **The forward table** (`observe()`, Dual-run only, never calls the source): resource/subject
  type through `migration-map.json`'s `types` map; resource/non-userset-subject id through the
  codec's `encode`; the permission through the check-path rule above; a userset subject's
  relation through `permissions[T][R]`, never `relation_splits`; `context` canonicalized
  (`sort_keys=True`, compact separators).
- **The reverse table** (`ask()`, Replay's parity mode only, the one place this adapter calls the
  source): the inverted lookups step 4 built.
- **What `observe()` does with a name `migration-map.json` has no entry for** -- a Class C source
  relation with no conversion target, or one added to the source *during* the shadow window,
  which this playbook deliberately runs for a full usage cycle while the source system is still
  being developed. Implement `differential-harness.md`'s "Untranslatable questions" rule exactly:
  `observe()` returns an explicit *untranslatable* signal rather than a `(Question, Outcome)`
  pair; the harness increments a tally keyed by `(source type, source relation)` and reports it;
  and the name is **never** guessed by identity or by adding/stripping a split suffix, and
  **never** turned into a `DifferentialRecord` with an invented `question.permission`. Emit this
  as a real, reachable code path with its own counter -- not a `KeyError` propagating out of a
  lookup, and not a silent `return None` an unmapped call surface disappears into.
- **The five-state `Outcome` mapping specific to this source** -- for OpenFGA, per
  `source-adapter.md`'s table: `ALLOWED`/`DENIED` are a real, typed boolean; `ERRORED` is any
  non-2xx response or thrown SDK exception, **with the observation-hook placement rule stated as
  a hard requirement, not a suggestion** -- the hook must sit at the SDK call boundary itself,
  above any call-site `try`/`catch` that might already swallow an error into a false denial
  before this hook ever sees it (`source-adapter.md`'s own live-demonstrated
  `appLevelSwallow` example is exactly this hazard); `NOT_ANSWERED` is never populated from
  anything the source *returned* -- it covers this adapter's own dispatch never firing, and the
  transport cases where nothing came back at all (a client-side deadline expiring with nothing
  on the wire, a connection that never reached the source), per `source-adapter.md`'s
  `ERRORED`/`NOT_ANSWERED` table; `CAVEATED` is structurally unreachable from a strictly-boolean
  source and this adapter must never attempt to manufacture one.
- **The operation-comparability table**, exactly as that file states it -- implement only what is
  actually comparable:
  - `check` / `batchCheck` -- yes, exact, one `Question` per item. For `batchCheck`, map each
    item's `{"allowed": bool}` or `{"error": {...}}` shape directly; **never default a missing
    `allowed` key to `false`.**
  - `listObjects` / `listUsers` -- advisory only, sampled (step 6), never a full-set diff, never
    eligible for snapshot-to-assertions (step 7).
  - `expand` -- not comparable at all. Do not build a comparison for it "by analogy to `check`'s
    tree-of-booleans intuition" -- `source-adapter.md`'s own words for exactly this trap. SpiceDB's
    expand tree has no node kind matching OpenFGA's `tupleToUserset`; the shapes are structurally
    incompatible, not two encodings of the same information.
  - The six no-target operations (store CRUD, AuthZEN, Permissions Index, `contextual_tuples`,
    `authorization_model_id` pinning, `readAssertions`/`writeAssertions`) -- no comparison
    function exists for any of them, and none should be built.

### Step 6: Emit the dual-run dispatcher and configure sampling

Write `migration/verify/dual_run.<ext>`.

Implement `differential-harness.md`'s "Dual-run" section's three safety properties exactly:

- **Off the critical path.** Fire the SpiceDB call after or alongside the request that already
  has its answer (its own goroutine/thread/task/queue), never awaited by that request. A slow or
  hung call, once this harness's own deadline expires, is `NOT_ANSWERED` -- never `ERRORED`,
  which is reserved for a response that actually came back reporting failure.
- **One call per sampled question, never a write.** `CheckPermission` (or the bulk/lookup
  equivalent, subject to step 5's comparability table).
- **`minimize_latency` by default, never `fully_consistent` for the bulk of traffic** -- the
  language's `MinLatency()`/`min_latency()`/`minLatency()` helper, per `core-concepts.md`'s
  table. `fully_consistent` bypasses SpiceDB's cache and doesn't scale
  (`consistency-deep-dive.md`, "Common Mistakes"); using it for ordinary dual-run traffic
  reintroduces exactly the latency risk this capability exists to avoid.
- **Always capture the target's `checked_at`/`CheckedAt` token on every response**, regardless of
  which consistency was requested -- `differential-harness.md` verified live that this comes back
  populated even under `minimize_latency`, which is what makes reconciliation (step 5) possible
  without any special access to the write path. **This is only true of the call step 3 actually
  confirmed exposes it** -- *no* idiomatic wrapper at the pinned commit has a `CheckedAt` field to
  capture at all, in any of the seven languages (step 3), so "always capture it" means calling the
  raw stub, not adding a `nil`-check around a wrapper that can never populate it. A build that
  skips this does not merely lose a field: `target.zedtoken` is then null on every record, and
  every reconciliation finalizes `RECONCILIATION_FAILED` (step 5).

**State plainly, as a required line of this step's own output, not only the final report: this
dispatcher's only outbound call is to SpiceDB.** It does not call the source system a second time
-- `observe()` (step 5) only translates a decision the production request already made.
`source-adapter.md`'s "One seam, two entry points" table states this directly ("Places a call to
OpenFGA? No -- translates an already-observed call"), and it is what keeps this command's own
constraint -- dual-run must not add a second call to the production request path -- true
structurally, not by convention.

**Retention, minimization, and volume for the record stream.** Implement
`differential-harness.md`'s "What the record stream holds, and how long to hold it" section as
configuration this dispatcher actually reads, not as a comment. This is the one artifact the
whole pipeline emits that durably persists real production request data -- live resource and
subject ids, a `request_id` that correlates back to the production request, error strings that
embed the tuple being checked, and `question.context`, which in any caveat-using deployment
carries exactly the request-time attributes the caveat evaluates over (timestamps, IPs, tenant
ids, email addresses) -- and it does so unattended, for a window measured in billing cycles.
Three values, all required, none defaulted: a **maximum record age** enforced by deletion; a
per-field **minimization** decision, with `question.context` called out explicitly (store key
names plus a hash of the values unless raw values are actually needed for triage, and say so if
they are); and a **projected volume** for the configured sampling rate over the intended window,
computed before sampling is turned up rather than discovered afterward. Record all three in
`migration-plan.md` (step 8) and state them in the report (step 9) -- a customer whose data
retention policy is shorter than the shadow window needs to learn that from this command, not
from an audit. Where the customer's own policy conflicts with the window, the policy wins.

**Sampling**, per `differential-harness.md`'s "Sampling and volume" section: a consistent hash
key (resource or subject id) deciding inclusion, never a request-count cap. Coverage should track
the permission surface, not raw traffic volume, so seed a **higher default rate for
`(resource type, permission)` pairs `migration-map.json` already flags as structurally risky** --
sourced by iterating the JSON directly, never by parsing any Markdown table (no phase, including
this one, may parse `migration-plan.md` for state -- `findings-report.md`'s `##
migration-plan.md` section):

- Every permission at `relation_splits[type][relation].permission`, for every `type` and
  `relation` the map records.
- Every permission named in `arrow_aliases[type][relation].arrow_sites` -- each entry is a
  `<definition>.<permission>` string; extract the permission (the permission that *contains* the
  arrow, e.g. `doc.can_share` for a `folder.owner -> owner__perm` alias -- the alias name itself,
  `owner__perm`, is internal to the aliased type's schema and is never a name this harness
  resolves or constructs).
- Every entry in `identifier_notes.types` and `identifier_notes.permissions` whose note
  describes a collision.
- Every finding in `decisions.tenancy.tenant_reachability_findings`.

**There is no separate `## Class B`/`## Class C` heading, or a dedicated JSON key, to look up by
label** -- `findings-report.md`'s layout does not give Class B/C findings one either; they live
inside the JSON keys just named, the same way Class A's own sites live in
`decisions.per_blocker_resolutions` rather than under a `## Class A` heading or a dedicated key.
A map with nothing in any of those four beyond what `relation_splits`/`arrow_aliases` already
cover has nothing further to weight -- record that as "none found beyond the above," not as a
step silently skipped -- rather than one uniform rate for every permission.

**`## Deferred / manual` has no JSON counterpart, and this command deliberately no longer parses
it for a permission to weight** -- a behavior change from this command's previous version, since
no command may parse `migration-plan.md` for state; instead, step 9's report points the human at
`## Deferred / manual` as an additional place they may want to hand-add sampling weight for a
permission this automatic list didn't catch.

Expose the rate as a per-`(resource type, permission)` configuration the customer can override,
and note that coverage should taper on the same schedule cutover step 6 moves per resource type,
**never to zero while the source system remains authoritative** -- cited, not restated, from that
same section.

**The health gate, and the four numbers this command must not leave unset.**
`differential-harness.md`'s "The health gate" section states why the two-condition "done" bar
alone is satisfiable by a harness that compared nothing: a systematically-failing reconciliation
turns every candidate `DISAGREE` into an `INCONCLUSIVE`/`RECONCILIATION_FAILED` record, which the
`AGREE` rate then excludes from its own denominator, and an `INCONCLUSIVE` still counts as
"sampled." Emit that gate as real code in this dispatcher's reporting path -- all four of that
section's numbered conditions, per `(resource type, permission)` pair: (1) a floor on **distinct
comparable questions**, (2) the paired `INCONCLUSIVE` and `RECONCILIATION_FAILED` ceilings, (3)
the zero-record coverage list, and (4) the breadth condition -- reading its thresholds, along
with the target `AGREE` rate that the reference's separate "Sampling and volume" condition (2)
is measured against, from configuration.

**The floor counts questions, not records, and the breadth condition is not optional.** Key a
comparable record by `(resource, permission, subject, context)` with `context` canonicalized the
identical way step 4 already canonicalizes it (`sort_keys=True`, compact separators), count
*distinct* keys against the floor, and then run the same floor twice more: once with every record
whose `question.resource` is that pair's most frequent resource removed, once with every record
whose `question.subject` is that pair's most frequent subject removed. All three must clear. Both
extra passes are a `group by` over records this dispatcher already holds -- no new call, no new
configuration number. A floor on raw record count is defeated two ways this code must not be
vulnerable to: one hot question re-asked thousands of times, and one resource with a wildcard
grant answering `true` for every subject, which produces thousands of *genuinely distinct*
questions and so defeats a distinct-question floor as well. Only the leave-one-out passes catch
the second. Report a pair that fails any of the three as **undetermined**, never as a pass, and
never silently. **Ask for all four with a single
`AskUserQuestion`** (this is the one question this step adds; it is not a decision this command
may make on the customer's behalf, and it is not a decision it may skip): the reference states
plainly that these are numbers it deliberately does not pick and equally deliberately does not
let go unset, since "unset" is the state in which the gate passes everything. Ship no built-in
default, reject a zero floor and a 100% ceiling, and record the four chosen values in
`migration-plan.md` (step 8) so a later reviewer can see which bar was actually cleared.

### Step 7: Emit the snapshot-to-assertions path

Write `migration/verify/snapshot_to_assertions.<ext>`. Implement
`differential-harness.md`'s "Snapshot-to-assertions" section exactly:

**Eligibility, narrow on purpose, and it is two field tests, not one: `verdict: AGREE` AND
`question.origin` in `{CHECK, BATCH_CHECK}`.** Both tests, together, are the whole admission rule
-- an implementation that writes only the `verdict` half here has already shipped the defect
`question.origin` exists to prevent, because a sampled list-derived record re-asked as a check is
byte-identical to a real one in every remaining field and passes a verdict-only filter. The
`origin` half is spelled out in full at the end of this step; do not implement this paragraph
before reading it. On the `verdict` half: `DISAGREE` and
`INCONCLUSIVE` are excluded outright -- freezing either into a regression suite would assert the
very thing not yet established. A `CAVEATED` target outcome can never appear in an eligible
record by construction, since `Diff` rule 2 always routes it to `INCONCLUSIVE` before an `AGREE`
verdict could ever be reached. A record carrying no `origin` at all is rejected rather than
defaulted to `CHECK`.

**Output shape, matched field-for-field with `/spicedb-dev:migrate-tests`'s own output**:
`ALLOWED` -> `assertTrue`, `DENIED` -> `assertFalse`, in the identical
`"<resource>#<permission>@<subject>"` string form that command renders for a check -- **never**
the write-target relation name. A non-empty `question.context` renders as the identical
` with {json}` suffix, canonicalized the same way (`sort_keys=True`, compact separators) -- this
is deliberate: a file this command writes and a file `/spicedb-dev:migrate-tests` writes share
one grammar and each validates under the same `zed validate --fail-on-warn` command without
either needing to know the other exists. **Do not tell the user they can be concatenated** --
each is a complete YAML document with its own top-level `schemaFile:`/`relationships:`/
`assertions:` keys, and `cat`-ing two together fails with `mapping key "schemaFile" already
defined` (exit 1). Combining two files means merging their blocks under one set of top-level
keys.

**The `relationships:` block.** Read **at the target's own captured `zedtoken`** from the moment
the check was answered -- never whatever is live when the snapshot step runs -- and follow
`differential-harness.md`'s "Snapshot-to-assertions" section exactly, not the narrower
resource+subject reading that section itself corrects: for an `ALLOWED` record, resource+subject
relationships alone are **not** sufficient whenever the permission resolves through an arrow, which
is the common case, not the exception (that section's own live-verified example: a `parent->viewer`
permission's real grant lives on the parent object, which appears nowhere in the `Question`'s
resource or subject fields). Call `ExpandPermissionTree` on the `Question`'s own resource and
permission at the captured `zedtoken` and walk the returned tree by **both** of that section's two
collection rules, unioned across every branch -- cited, not re-derived, from that section:

1. every node's own `expandedObject`, and
2. **every leaf subject carrying an `optionalRelation`** (a userset subject), each of which must
   then be `ExpandPermissionTree`d in turn and walked the same way, recursively, memoized on
   `(objectType, objectId, relation)` so a cyclic membership graph terminates.

**Rule 2 is where an implementation of this step goes wrong, and it fails loudly rather than
subtly** -- `ExpandPermissionTree` never returns a userset as an `expandedObject` node, only as a
leaf subject, so a walk collecting `expandedObject` pairs alone omits the grant-bearing hop and the
emitted file fails `zed validate --fail-on-warn` outright. That section has the live transcript
(both the failing and the passing run) and the reason arrow- and wildcard-resolved records still
validate without rule 2, which is what makes its absence easy to miss. A `DENIED`
record needs no supporting relationships at all. Respect the shelf life a `zedtoken`-scoped read
has: `differential-harness.md` verified
directly that `serve-testing` hardcodes a one-hour GC window and a real deployment defaults to 24
hours (operator-tunable) -- run this export promptly after a batch of `AGREE` records is
confirmed, well inside whichever window the deployment actually has. **If an export hits an
expired `zedtoken` anyway, never silently substitute current live state for the captured one** --
either drop that record from the batch, or re-export against live state with an explicit
`# NOTE(spicedbmigration):` comment stating plainly that its relationships reflect current
state, not the state the answer was originally checked against.

**File placement.** `validation-shadow-<batch-id>.yaml`, written to `[output-dir]`, **never
overwriting** phase 5's `validation.yaml` -- a shadow-traffic regression suite accumulates over
the life of a cutover; the phase-5 file is a one-time artifact. Sort `relationships:` lines
(matching phase 5's rule); preserve capture order for `assertTrue`/`assertFalse` -- the same
"don't invent a sort key" choice `/spicedb-dev:migrate-tests` makes for its own assertion output.

**Validate before trusting.** Run `zed validate --fail-on-warn` against every emitted file before
treating it as part of the regression suite, exactly as phase 5 does, from `[output-dir]` with a
relative path -- the same `schemaFile:` resolution trap `/spicedb-dev:migrate-tests` step 7
documents (an absolute argument path produces a misleading schema error) applies identically
here.

**Verify every `# NOTE(spicedbmigration):` comment this step emitted, mechanically, before step
8.** `findings-report.md`'s "Inline markers" two-line cap applies here exactly as it does to
phases 4 and 5. Run `grep -rn -A2 "TODO(spicedbmigration)\|NOTE(spicedbmigration)"
<every validation-shadow-*.yaml this run wrote>`, and rewrite any comment block that runs past
two lines -- move the excess into the `migration-plan.md` entry it points at. Record the total
marker count and the longest marker's length in lines for step 9's report.

**Never feed a list-derived (`listObjects`/`listUsers` advisory) record into this path, and
enforce that on the record's own `question.origin` field rather than on the caller.** Eligibility
here is **two** tests: `verdict: AGREE` **and** `question.origin` in `{CHECK, BATCH_CHECK}`
(`differential-harness.md`'s "Question" and "Snapshot-to-assertions"). A sampled,
lower-confidence list-derived record was never an ordinary `check`-shaped `AGREE`, and freezing
one into a regression suite would misrepresent it as one -- but once re-asked as a check it is
byte-identical to a real one in every other field, so a `snapshot_to_assertions` filtering only
on `verdict` cannot tell them apart and complies by luck. Reject `LIST_SAMPLED` explicitly, and
reject a record carrying no `origin` at all rather than defaulting it to `CHECK`. Step 5's
`observe()` is where the field gets stamped, at the point the sample is drawn
(`source-adapter.md`'s `listObjects`/`listUsers` section); a stamp applied any later is a guess.

### Step 8: Update `migration-map.json`, then record coverage in the migration plan

**Update `migration-map.json` first, before touching the plan at all.** If step 3 resolved a
call-site language the plan had not recorded, append it to `decisions.additional`: `{"key":
"call_site_language", "value": <language>, "note": <why the question was asked>, "recorded_by":
"/spicedb-dev:migrate-verify"}` -- the same place and the same reason `/spicedb-dev:migrate-code`
step 8 records its own call-site-language finding, which is why step 3 above says to record it
there rather than under **Deferred / manual**. This is the only key this command ever writes to
`migration-map.json`, and it writes nothing when the plan's recorded language already matched.

**This command never writes a `phase_status` entry.** That field is a closed vocabulary of
exactly phases 0-5 (`findings-report.md`: "One row per phase (0, 1, 2, 3, 4, 5)... a consuming
command that cannot parse a value must say so and halt or route conservatively, never assume
`complete`" -- the reference states this applies identically to `phase_status["N"].status` in the
JSON as to the rendered table cell). `/spicedb-dev:migrate-verify` implements
`cutover-strategies.md` step 4, a cutover step with no phase-pipeline analog -- the same territory
that file's own "What the plugin does not automate" section describes for steps 5 through 7,
except this one step does have a command behind it now. Writing a sixth or seventh `phase_status`
entry would break the exact closed-vocabulary discipline that field exists to enforce, and so
would a row appearing in the regenerated `## Phase status` table that renders it. State this in
the report (step 9) too, so it reads as a deliberate choice, not an omission.

**Then, and only then, append the coverage record to `migration-plan.md`'s `## Deferred /
manual` section.** This record is narrative-only content with no JSON counterpart -- re-read
`migration-plan.md` and write it back to the same location, **appending** (never replacing), the
same heading `/spicedb-dev:migrate-tests` step 8 already uses for an analogous fact (its own
`list_objects`/`list_users` coverage fraction).

**Append it as a labeled prose item, not as a new `###` subsection of its own.**
`findings-report.md`'s layout gives `## Deferred / manual` exactly two subsections, `### Needs
action` and `### For the record` -- this entry lands inside whichever of those two applies (next
paragraph), as one bolded-lead-in item, not a third heading alongside them. Use a bolded lead-in
on the first line -- `**Differential harness (/spicedb-dev:migrate-verify)**` -- followed by the
bullets below, so the record reads as one item in that subsection rather than introducing a new
heading level.

**Which of the two subsections it lands under is a judgment call, but the rule is explicit, not
silent: this coverage record defaults to `### For the record`** -- the harness is now emitted and
running, and there is nothing further for a human to do about the fact of its own existence --
**unless the health gate's numbers (step 6) indicate open work for this run**, in which case file
it under `### Needs action` instead: any `(resource type, permission)` pair still below the
distinct-comparable-question floor, over either ceiling, or on the zero-record coverage list is
open work a reviewer triaging `### Needs action` needs to see, not a settled fact for `### For
the record`.

Record:

- Which operations are actually wired for comparison: `check`/`batchCheck`, exact, per-item.
- Which are advisory-only: `listObjects`/`listUsers`, sampled check-shaped probes drawn from a
  list result, never a full-set diff, never eligible for snapshot-to-assertions.
- Which are **not comparable at all**, and why -- cite `source-adapter.md`'s operation table and
  `differential-harness.md`'s "What is not comparable at all" section rather than re-deriving the
  list here, so a construct added to either later needs no matching edit in this command: `expand`
  (structural tree mismatch, no comparison offered); the six no-target operations (store CRUD,
  AuthZEN, Permissions Index, `contextual_tuples`, `authorization_model_id` pinning,
  `readAssertions`/`writeAssertions`); and full-set `listObjects`/`listUsers` diffs specifically
  (as distinct from the sampled check-shaped mode this harness does support for them).
- The `(resource type, permission)` pairs sampling was weighted toward (step 6) -- one row per
  `relation_splits[type][relation]` entry actually in the harness's scope, confirming it resolves
  through `.permission`, not `.relation`; and one row per `arrow_aliases[type][relation]` entry's
  own arrow-site permission, confirming it resolves as an ordinary (non-split) `permissions[T][R]`
  lookup with no `.relation`/`.permission` choice at all -- these are two different mechanisms and
  the checklist should not imply the same field-choice risk applies to both. The same
  Class-B-checklist shape `/spicedb-dev:migrate-code` step 8 uses for its own rewrite checklist,
  applied here to this harness's name resolution instead of a call-site rewrite.
- Where the harness's modules were placed (step 3), and which language(s).
- **The health gate's four configured values** (step 6): the distinct-comparable-question floor
  per `(resource type, permission)`, the `INCONCLUSIVE` ceiling, the `RECONCILIATION_FAILED`
  ceiling, and the target `AGREE` rate -- recorded as the numbers the customer actually chose, so
  a later reviewer can see which bar a "done" claim cleared rather than having to reconstruct it.
  Note alongside them that the floor and the breadth condition are applied to distinct questions
  and to leave-one-out passes over the busiest resource and busiest subject, so a reviewer does
  not read "floor" as a raw record count.
- **What the gate does not establish, as the customer's own remaining work.** Per
  `differential-harness.md`'s "What this gate still does not measure": all four conditions range
  over the questions production happened to ask, never over the schema, so a pair can pass with
  every record having resolved through one branch while an arrow, wildcard, or caveated branch
  was never compared. Record that enumerating each pair's resolution paths from the schema and
  confirming one comparable record per path is the customer's, not this command's -- and that a
  rare branch may need a directed, hand-written comparison rather than a longer wait.
- **The zero-record coverage list**, as an explicit enumeration rather than a summary: every
  `(resource type, permission)` pair derivable from `migration-map.json` (`permissions[T]` and
  `relation_splits[T][*].permission` together, the same pair of tables step 4's reverse index is
  built from) that has accumulated **zero** records, listed by name. This list is written the
  first time this command runs -- when it is necessarily every pair -- and is the row a
  reconciliation job updates as records arrive. `differential-harness.md`'s health gate requires
  it because conditions (1) and (2) can only speak about records that exist: a pair that never
  appeared at all is invisible to both, and is surfaced only by enumerating the map.
- **The record stream's retention, minimization, and volume decisions** (step 6): the maximum
  record age and how deletion is enforced, the per-field minimization choice with
  `question.context` named explicitly, and the projected volume over the intended window. This
  is the pipeline's only durable store of real production request data; the plan is where a
  reviewer -- or the customer's own privacy review -- can see what was decided.
- **The untranslatable tally** (step 5): each `(source type, source relation)` `observe()` could
  not translate, with its count and its disposition -- mapped (re-run the conversion phases for
  it) or explicitly accepted with a reason (a Class C relation with no conversion target being
  the legitimate case). Record it even when empty, as "none observed," so a later run can tell an
  empty tally apart from a step that was skipped.

**Regenerate the rendered sections -- but only if `migration-map.json` actually changed this
run.** `## At a glance`, `## Needs your attention`, `## Decisions`, `## Identifier map`, `##
Relation splits`, `## Arrow aliases`, and `## Phase status` are pure renderings with no state of
their own (`findings-report.md`'s "Two groups of sections, one rule each"); the only way this
command changes what any of them would render is the `decisions.additional` append above, and
that fires in exactly one case (step 3 resolved a call-site language the plan had not recorded).
**If that case did not fire this run, the JSON did not change -- say so explicitly in the report
(step 9) and skip the regeneration, rather than regenerating unconditionally.** If it did fire,
regenerate all seven of those sections in full from the now-current JSON, the same "always
regenerated in full... never edited in place, never left stale" discipline every earlier phase in
this pipeline applies to them.

**Do not touch `## Sync obligations`.** That section belongs to phase 0 and phase 3 only
(`findings-report.md`: "Phase 0 owns this section; phase 3 revises it in place and never appends
a second one") -- this command is neither.

**This command still does not touch `## Source`, `## Scan scope`, or `## Target` either.** Those
three are edited in place only by the phase that owns the fact recorded there -- usually phase 0
(`findings-report.md`'s "Two groups of sections, one rule each") -- and this command owns none of
them.

Leave every other section byte-identical, the same discipline every earlier phase in this
pipeline applies to its own plan update.

### Step 9: Report

Tell the user:

1. **What was emitted, and where** -- language(s), the resolved placement (and why, if it was
   ambiguous), and which SpiceDB client this harness uses. If this command was the one vendoring
   it, restate the not-for-production prototype-client warning as a required line, naming the
   manifest file(s) touched and the four-language published-client alternative (step 3). If
   phase 4 had already vendored it, say plainly this harness is reusing that same dependency, not
   taking on a second one.
2. **Where this belongs in the cutover, stated plainly.** This is `cutover-strategies.md` step 4
   ("Dual-write, shadow-read") -- the source system stays authoritative, every check is still
   answered by it, and this harness's SpiceDB answer is logged and diffed, never returned to any
   caller. Cite the file for the step's full description rather than restating it. State the
   window this belongs in: after the pipeline has produced real code and data to compare against
   (phase 3 complete with a passed verification; a real SpiceDB client and this harness's own
   name/id translation resolved against `migration-map.json`) and before cutover step 7 removes
   the source system. **If `migration-map.json`'s `phase_status["3"].status` is not `complete`
   with a passed verification, restate step 1's warning here as a required line, not a
   footnote**: turning
   dual-run sampling on above zero before that reports the source's real `ALLOWED` answers
   against a target answering `DENIED` everywhere, and every one of those is a false `DISAGREE`,
   not a defect.
3. **The two named failure modes, and that both are handled structurally, not just documented.**
   Name-translation error (cite `source-adapter.md`'s live-demonstrated `AGREE`->`DISAGREE` flip
   from reading `.relation` instead of `.permission`) is prevented by this harness's check/lookup
   path never reading `.relation` anywhere. Consistency staleness (cite the measured
   95%-at-360µs-collapsing-to-0%-by-5ms figures, and that a real deployment's window can be far
   larger) is handled by reconciling every candidate disagreement at `AtLeast(zedtoken)` before
   it is ever finalized as `DISAGREE`.
4. **Coverage**, restating what step 8 recorded, in three explicit buckets -- comparable
   (`check`/`batchCheck`), advisory-only (sampled `listObjects`/`listUsers` probes), and not
   comparable at all (`expand`, the six no-target operations, full-set list diffs) -- the same
   three-bucket discipline `/spicedb-dev:migrate-code` step 9 uses for its own no-target
   findings, applied here to comparison coverage instead of conversion coverage. **Marker
   cap**: the total `NOTE(spicedbmigration):` marker count and the longest marker's length in
   lines, from step 7's mechanical check. **Sampling weight**: state that the
   `(resource type, permission)` pairs step 6 seeded a higher rate for were read directly from
   `migration-map.json`'s `relation_splits`, `arrow_aliases`, `identifier_notes`, and
   `decisions.tenancy.tenant_reachability_findings` -- and that, unlike this command's previous
   version, `## Deferred / manual` is no longer parsed for this list, only pointed at as an
   additional place the human may want to hand-add weight (step 6).
5. That `migration-map.json` was updated first -- `decisions.additional`, if and only if step 3
   resolved a call-site language the plan had not recorded -- and that `migration-plan.md` was
   then updated under **Deferred / manual**, with its rendered sections (`At a glance`, `Needs
   your attention`, `Decisions`, `Identifier map`, `Relation splits`, `Arrow aliases`, `Phase
   status`) regenerated from the JSON only when that append happened, and left untouched (and say
   so) when it did not. State plainly that `migration-map.json` remains the durable record and
   `migration-plan.md` a rendering of it, and that no `phase_status` entry was added, with the
   reason from step 8.
   **Also state, as a required line, what this harness will durably persist and for how long**
   (step 6): the `DifferentialRecord` stream holds live resource and subject ids, a `request_id`
   correlating back to the production request, and -- in any caveat-using deployment --
   `question.context`, which is by construction the request-time attribute payload the caveat
   evaluates over. Name the configured maximum record age, the per-field minimization decision,
   and the projected volume, and say plainly that the customer's own data-retention policy
   governs if it is shorter than the shadow window.
6. **What "done" means, stated honestly.** Not zero disagreements on one run of this harness.
   `cutover-strategies.md` step 7 sets the bar for removing the source system entirely: quiet
   reconciliation "for a full cycle of how the product is actually used... a quiet week is not
   the same evidence as a quiet cycle." `differential-harness.md`'s own "Sampling and volume"
   section states the identical bar from the harness's own side, for when to stop dual-running
   any one `(resource type, permission)` pair. Cite both; do not paraphrase either into a third
   wording that could drift from what either file actually says. **And state the third
   condition explicitly, because it is the one a reader assumes is implied by the other two and
   it is not**: `differential-harness.md`'s "The health gate" must also pass for that pair --
   a floor on distinct comparable questions, bounded `INCONCLUSIVE`/`RECONCILIATION_FAILED`
   rates, a zero-record coverage list enumerated from `migration-map.json`, and the breadth
   condition that re-applies the floor with the pair's busiest resource and busiest subject
   dropped. Name the four values the customer chose in step 6. Without that gate, a harness whose
   reconciliation fails on every record reports a 100% agree rate with zero untriaged
   disagreements and satisfies the other two conditions completely.

   **Then say what a passing gate still does not prove, in the same breath rather than as a
   footnote.** It measures volume and spread over the questions production asked; volume is not
   coverage. Cite `differential-harness.md`'s "What this gate still does not measure": no
   condition ranges over the schema, so every pair can pass while a permission's arrow, wildcard,
   or caveated branch went unexercised. Tell the customer plainly which part stays theirs --
   enumerating each pair's resolution paths and confirming a comparable record for each, deciding
   what to do about a path traffic never takes, and judging whether the cycle was long enough for
   their product. A gate presented as stronger than it is, is worse than a weaker gate honestly
   described.
7. **Route onward -- do not dead-end.** This command does not automate cutover steps 5
   (reconciliation job), 6 (flag cutover), or 7 (source-system removal) --
   `cutover-strategies.md`'s own "What the plugin does not automate" section states why: the
   reconciliation job's schedule, the flag infrastructure, and the removal decision are the
   customer's, not something a tool can certify. Say what comes next: wire `observe()` into the
   call sites that are still authoritative, turn sampling on at a low rate, let a reconciliation
   job (the customer's own) consume the `DifferentialRecord` stream, and run
   `snapshot_to_assertions` periodically to grow the regression suite.
8. **The one real judgment call this command cannot resolve, stated plainly rather than stepped
   around**: `cutover-strategies.md` step 4 requires the source system to remain authoritative for
   the duration of shadow-read. If `/spicedb-dev:migrate-code` (phase 4) has already rewritten a
   given call site to call SpiceDB alone, that call site has nothing left for `observe()` to
   translate -- there is no source-side decision happening there anymore to observe. Wiring this
   harness at such a call site requires the customer's own step-6 flag, or an equivalent branch,
   that keeps a source-system answer live there for the length of the shadow window;
   `cutover-strategies.md` itself leaves that flag's design as the customer's own deployment
   decision, and this command does not invent one on their behalf.
9. **This command emits a module; it does not edit the customer's own call sites the way
   `/spicedb-dev:migrate-code` does.** Doing so would require the same sweep-and-classify work
   that command already performs, and duplicating it here risks a second, drifting
   classification of the same call sites. Wiring `observe()` in -- including, for a language
   whose call sites already wrap the source call in their own error handling, confirming the
   hook sits above any such swallowing (`source-adapter.md`'s observation-hook requirement,
   step 5) -- is applied by the customer, at each call site, per the placement guidance above.

## Error Handling

| Situation | Do this |
|---|---|
| No `migration-plan.md` | Halt. Direct to `/spicedb-dev:migrate`. This command has no gate of its own. |
| Plan's phase 0 not `complete (full gate)` | Halt. Direct to `/spicedb-dev:migrate`, which detects this marker itself and re-runs the full gate. |
| Unresolved Class A finding in the plan | Halt. List the unresolved blockers. |
| Phase 1 not complete, or `schema.zed`/`migration-map.json` missing | Halt. Direct to `/spicedb-dev:migrate-schema`. |
| No pack, or pack has no `source-adapter.md` | Halt. An unsupported source needs an adapter written first. |
| `zed schema read` against the target errors, is empty, or is missing a mapped definition | Halt. The schema must be deployed to the target first; this command does not deploy it. |
| Phase 3 not `complete` with a passed verification | Does not halt emission. Warn loudly (step 1, step 9). Do not enable dual-run sampling above zero against this store until it passes -- every check will otherwise answer `DENIED` on the target, producing a wall of false `DISAGREE` records. |
| `id_encoding.status` is `unresolved` or `unknown` | **Halt.** Violating object IDs exist or were never ruled out, and no encoder is being emitted, so converted code hard-errors on a live request path. Report `id_encoding.violations` and put the identifier options to the user. `mode: "none"` does not clear this -- only `status: "clean"` does. |
| `id_encoding.mode` is `base64url` for some type and phase 3 has never run (no codec file anywhere) | Halt. Direct to `/spicedb-dev:migrate-data` -- importing phase 3's exact codec is structural once encoding is load-bearing. |
| Vendored SpiceDB client fails to build/import | Fix the wiring against `installation.md`'s per-language recipe before continuing. |
| More than one plausible call-site language, and the plan is silent | Ask with `AskUserQuestion`; record the resolution in `migration-map.json`'s `decisions.additional` (step 8). |
| A source operation not in `source-adapter.md`'s comparability table, and not one of the no-target operations it names | Do not approximate a comparison for it. Report it as new information -- the same treatment `/spicedb-dev:migrate-code` gives an unhandled construct -- rather than inventing a mapping. |
| `observe()` sees a source relation with no entry in `migration-map.json` | Return the untranslatable signal, tally it by `(source type, source relation)`, and report it (step 5, step 8). Never guess the SpiceDB name, never emit a `DifferentialRecord` with an invented `question.permission`, and never let it disappear into a swallowed lookup error. |
| A candidate `DISAGREE` whose `target.zedtoken` is null | Finalize `INCONCLUSIVE`/`RECONCILIATION_FAILED`. Never re-ask at a weaker consistency and report it as reconciled. If this happens at all it is happening on every record -- re-check step 3's client-surface finding before reading anything else this harness reports. |
| A `(resource type, permission)` pair is below the health gate's distinct-comparable-question floor, fails either leave-one-out breadth pass, or is over either ceiling | Report it as **undetermined**, or as a **harness fault** for a ceiling breach -- never as a pass, and never as agreement. A pair that cannot be compared, or was only ever compared through one resource or one subject, has not been verified (`differential-harness.md`, "The health gate"). A genuinely narrow pair is accepted in writing with its reason, never auto-passed. |
| `zed validate --fail-on-warn` fails on an emitted snapshot file, and the error's explanation tree ends at a `<type>:<id> <relation>` hop on an object that is **not** the `Question`'s own resource | The `relationships:` walk (step 7) is missing rule 2 -- it collected only `expandedObject` nodes and never followed the leaf subjects carrying `optionalRelation`. Fix the walk; do not add the missing tuple by hand. |
| `zed validate --fail-on-warn` fails on an emitted snapshot file, any other shape | Report it. Check the `schemaFile:` relative-path trap, a relation-split write, and a caveat-name/schema mismatch first, in that order, before treating it as a harness defect. |
| A `zedtoken`-scoped `relationships:` export hits an expired snapshot | Do not silently substitute current live state. Drop the record from this batch, or re-export against live state with an explicit `# NOTE(spicedbmigration):` comment. |
| A candidate `DISAGREE`'s reconciliation re-ask itself errors or times out | Finalize `INCONCLUSIVE`/`RECONCILIATION_FAILED` -- never `DISAGREE` and never `AGREE`. Bounded retries only, never an unbounded loop. |
| A candidate `DISAGREE`'s reconciliation re-ask comes back `CAVEATED` | Finalize `INCONCLUSIVE`/`CAVEAT_GAP` -- never `STALE_READ` and never `DISAGREE`. This is `Diff` rule 4's own branch for it (`differential-harness.md`, "Diff", rule 4, live-verified there); a re-ask is exactly as capable of landing on a caveat gap as a first ask, and folding it into "still disagrees" manufactures a `DISAGREE` on a correctly-migrated caveated resource. |
| Any internal harness failure (crash, timeout, lost connection, an exception in the comparison pipeline) | Must surface as a missing or `ERRORED`/`NOT_ANSWERED` record. Never `DENIED`. Never propagate to the caller. |
| A call site phase 4 already rewrote to call SpiceDB alone (no source-system call left to observe) | Not a defect in this command. Flag it in the report -- the customer needs their own flag or branch to keep a source-system answer live there for the shadow window; this is `cutover-strategies.md` step 6's own decision, not one this command makes for them. |

## Notes

- The version floor is SpiceDB **v1.52.0**; every rule this command cites from
  `differential-harness.md` and `source-adapter.md` was verified against v1.56.0, `zed` v0.31.1,
  and `fga` v0.7.20 -- this command does not re-verify any of those claims itself, and neither
  should an agent executing it; quote the cited file, don't re-derive the number.
- **`spicedb serve-testing` (v1.56.0+) takes no `--grpc-preshared-key` and isolates datastores per
  token.** Pass `--endpoint`/`--token` explicitly to every `zed` invocation and to every client
  this harness constructs; never `zed context use`, which rewrites shared global configuration
  instead of scoping to this one migration.
- **`tools/migration-harness/` is not shipped with this plugin, and is not what this command
  emits.** It is the corpus-validation harness used to check this pack's own conversion rules
  against a corpus this project controls, and it lives in the plugin's source repository, not in
  a customer's project. Nothing this command writes imports it, cites its paths, or depends on it
  being present -- the harness this command emits is a fresh implementation of
  `differential-harness.md`'s contract, written in the project's own language, using that
  project's own SpiceDB client and its own source-system client.
- **Ask as few times as the tool allows.** Step 3 may ask about a language ambiguity and, if not
  obvious, where to place the harness's modules. Step 1 asks before overwriting an existing
  harness. Step 6 asks once for the health gate's four thresholds, which
  `differential-harness.md` states plainly are the customer's to set and must not be defaulted.
  Every other name, id, and comparability decision in this command is a lookup against
  `migration-map.json`, `differential-harness.md`, or `source-adapter.md` -- not a decision.
- This command's own automation boundary matches `cutover-strategies.md`'s explicit line: it
  ships step 4's tooling only. Steps 5 through 7 remain the customer's, by design stated in that
  file's own "What the plugin does not automate" section, not a gap this command should imply it
  closes.
