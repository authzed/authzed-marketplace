---
name: migrate-tests
description: Convert a source system's test/assertion files into SpiceDB validation YAML
argument-hint: "[test-file] [output-dir]"
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

# Migrate Tests

Phase 5 of the migration pipeline: convert a source system's test fixtures and assertions
into SpiceDB validation YAML, and validate every emitted file with `zed validate`.

This command's job is to **convert**, following the pack's test-mapping reference exactly,
and to decide as little as possible while doing it. Where a genuine judgment call remains --
which candidate test file to convert, how to resolve a same-object/different-relation
collision -- it is a human call, and this command asks with `AskUserQuestion` and records the
answer directly in `migration-plan.md`'s narrative **Deferred / manual** section -- a
file-selection or collision-scenario choice has no `migration-map.json` counterpart, unlike
phase 0's own decisions, which live in the JSON's `decisions` key and are only rendered to the
plan (`findings-report.md`'s "Two groups of sections, one rule each").

**This command has no gate of its own, reduced or otherwise.** Unlike
`/spicedb-dev:migrate-schema`, which stays independently runnable for schema-only work
because it can decide phase 1's own inputs from the model alone, test conversion is a pure
**consumer** of `migration-plan.md` and `migration-map.json` -- every rewrite rule below is
"look this name up in the map", not a decision this command could make standalone. If
`migration-plan.md` is missing, or was authored by the reduced inline gate rather than the
full one, this command halts and routes to `/spicedb-dev:migrate` rather than inventing a
gate to fill the gap.

Outputs, written to `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory):

- `validation.yaml` -- the canonical converted output for the selected source test file.
- `validation-<scenario>.yaml` -- one additional file per losing scenario, written only when
  step 5's mechanism-B collision fires.
- `migration-map.json` -- updated in place with this run's `phase_status["5"]` (step 8.1) --
  the single machine-readable record of that status.
- `migration-plan.md` -- regenerated in place: every advisory finding, collision record, and
  file-selection decision this run produced is appended directly to the narrative
  **Deferred / manual** section (step 8.2), and every other rendered section is rebuilt from
  the just-updated `migration-map.json` (step 8.3).

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each
task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Read the migration plan

Read `migration-plan.md` from `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory) -- the same
place `/spicedb-dev:migrate` and `/spicedb-dev:migrate-schema` write it -- or the path the
user gave.

**If it does not exist, halt.** Say plainly that `/spicedb-dev:migrate` (phase 0) is this
pipeline's front door and must run first: it produces both `migration-plan.md` and the
`migration-map.json` every rewrite rule below depends on. Do not offer a reduced gate the way
`/spicedb-dev:migrate-schema` does -- there is no schema-only-shaped equivalent for test
conversion, since nothing here is decidable without the map phase 1 already produced. A
phase-0-only map is **not** sufficient here: phase 0 deliberately omits `relation_splits`
(`/spicedb-dev:migrate`'s own step 6 forbids writing it, even as `{}`, because which `define`s
split is discovered only by walking the model), and every relationship write in step 5 keys
off exactly that. This is why step 1 below halts unless phase 1 is complete.

**If it exists, check who wrote it before doing anything else** -- the same authorship check
`/spicedb-dev:migrate`'s own step 1 performs, applied here as a read, not a re-run. This check,
and every other check in this step, reads `migration-map.json`, never a `migration-plan.md`
table -- `findings-report.md`'s "No phase may parse `migration-plan.md` to decide anything":
`migration-map.json` is the single machine-readable record of phase status and decisions
alike, and the Markdown is only ever a rendering of it. Read `migration-map.json`'s
`phase_status["0"]`:

- **`status` is `complete (full gate)`** -- proceed.
- **`status` is `inline (reduced -- no codebase analysis)`**, the key is missing, or any other
  value -- halt. Say plainly that this plan was never authored by the full gate: the reduced
  gate covers only phase 1's own inputs (`/spicedb-dev:migrate-schema`'s step 3b), and the
  three code-side Class A blockers a full sweep alone can confirm (multi-store tenancy,
  contextual tuples, model-ID pinning) were never checked against real call sites. Direct the
  user to `/spicedb-dev:migrate`, which detects this exact authorship marker itself (its own
  step 1) and re-runs the full gate, carrying the reduced gate's recorded decisions forward as
  defaults rather than blank questions. Do not re-run the gate yourself -- this command has
  no `Task` access to the `migration-analyzer` agent, and reimplementing the gate inline here
  is exactly the second gate the framework's "exactly one gate per migration" rule exists to
  prevent.

**Also halt on an unresolved Class A finding**, the same check `/spicedb-dev:migrate-schema`
step 1 performs: read `migration-map.json`'s `decisions.per_blocker_resolutions` array -- if
any entry's `resolution` is `null` or absent, list them and stop. A blocker the gate left open
is still open here.

**Then confirm phase 1 is actually done.** Test conversion has no meaning without a schema
and an identifier map. Read `migration-map.json`'s `phase_status["1"]`, and independently
check that `[output-dir]` contains both `schema.zed` and `migration-map.json` -- the recorded
status and the files on disk can disagree if a previous run was interrupted, and the files are
what this command actually reads. If either check fails, halt and direct the user to
`/spicedb-dev:migrate-schema`.

### Step 2: Load the conversion pack's test-mapping reference

Read the plan's **Source** section for the detected system (already resolved by phase 0; do
not re-detect it) and look it up in the `migrating-to-spicedb` skill's source registry. For
OpenFGA, Okta FGA, or Auth0 FGA that is `openfga-to-spicedb`; read its
`references/test-mapping.md` **in full** before converting anything -- it is the algorithm
this command applies, and every step below cites it rather than restating it. If the plan's
source has no pack, or the pack has no test-mapping reference, stop: an unsupported source
needs a mapping written first (`pack-contract.md` item 8), not an ad hoc conversion.

### Step 3: Load `migration-map.json`

Read `migration-map.json` from `[output-dir]`. Every rewrite this command performs is an
application of that map: `types`/`permissions` for every check surface and every subject-side
userset reference, `relation_splits` for a relationship write's resource side (falling back to
`permissions` when the relation did not split -- the `write_relation` rule
`test-mapping.md`'s "Tuples are writes; assertions are checks" states), and `id_encoding` for
every object ID. Load it once, here, and treat every name and encoding decision in it as
**fixed** for the rest of this run -- do not re-derive a name from
`naming-normalization.md`'s algorithm. A name this map assigned is a decision phase 1 already
made and owns; re-deriving it can silently disagree with data already migrated against it,
the same "never re-derive" rule `/spicedb-dev:migrate-schema` step 4 applies to its own
reservations.

### Step 4: Locate the source test file

Apply `test-mapping.md`'s "Selecting the source test file" rule exactly, searched from the
plan's **Source** section model location (the directory the model file(s) were found in --
test fixtures conventionally live beside the model) or the `[test-file]` argument if the user
gave one:

1. A file literally named `store.fga.yaml` -- use it.
2. No `store.fga.yaml`, exactly one other `*.fga.yaml` -- use it.
3. No `store.fga.yaml`, **more than one** other `*.fga.yaml` -- this is `modeling-guide`'s
   shape, not a glob's job. Use `AskUserQuestion`: list every `*.fga.yaml` candidate, and
   mention `test-mapping.md`'s own guidance to prefer whichever file is most feature-complete
   when the set is a staged/numbered series (e.g. `step-N-*.fga.yaml`). Record the choice and
   the reason directly in `migration-plan.md`'s **Deferred / manual** section once answered --
   there is no `migration-map.json` counterpart for a file-selection choice, so this is a
   narrative append, not a JSON write. File it under **`### For the record`**: the split is
   mechanical, not a per-entry judgment call (`findings-report.md`'s "Needs action vs. for
   the record"), and a resolved file-selection choice has nothing further for a human to do.
   Do not silently pick the first glob match, which is filesystem-order-dependent, not
   alphabetical.
4. **Zero `.fga.yaml` files anywhere in the project -- a real, common outcome, not a dead
   end.** Confirmed to occur on every one of this pack's three real-project test runs, not a
   corner case: a project with a valid, actively-used OpenFGA model can carry its test
   coverage entirely outside the `.fga.yaml` fixture format this command converts -- e.g.
   integration tests that drive a live `openfga` server's HTTP/gRPC API directly through
   shell functions or a language's own test framework (a shell-driven integration suite, ~12
   shell functions exercising the API as different users, is exactly this shape). There is
   nothing here for step 4's job -- locating a `tuples:`/`tests:` fixture -- to find, and
   asking `AskUserQuestion` to "list every candidate" against an empty list is not a
   question, it is a dead end. **Do this instead, and do not fabricate a `.fga.yaml` file to
   force the pipeline through** -- authoring one from nothing would not be running this
   command, it would be manufacturing input to make the absence disappear, and it would
   produce a validation file that tests nothing about this project's actual authorization
   behavior:
   1. Confirm the negative with the same glob this step already uses (`find . -name
      '*.fga.yaml'`), scoped from the plan's model location as usual, so the report can cite
      the command that found nothing rather than asserting it.
   2. Skip the rest of this command's conversion pipeline (steps 5-7) -- there is no fixture
      content to collect, resolve, or render. Do not proceed into step 5 with an empty input
      set; that produces an empty `relationships:` block that looks like a successful, if
      thin, conversion instead of what it actually is: nothing was converted.
   3. **Route somewhere useful.** This project's authorization behavior can still get
      SpiceDB test coverage, just not by *converting* fixtures that do not exist:
      - Point at **`/spicedb-dev:test-permissions`**, pointed at `schema.zed`, to *generate*
        fresh SpiceDB test data and validation scenarios directly from the converted schema.
        This is not a conversion of the source system's tests -- it has no source input to
        convert from -- but it is real, useful SpiceDB test coverage where none would
        otherwise exist, and it is this plugin's own tool for exactly that job.
      - If the project has non-fixture test coverage (a live-API-driven suite, as in one real project observed during this pack's development),
        name it explicitly as **Deferred / manual -> Needs action** in `migration-plan.md`:
        porting a live-API-driven suite to check against SpiceDB instead of the source system
        is real work with no mechanical mapping this pack can automate (its shape is
        arbitrary test-framework code, not a fixture format), so it is handed back rather
        than silently dropped.
   4. **Do not leave an ad-hoc verification fixture in the repository.** Writing a
      throwaway `.fga.yaml` to check parity by hand during any phase is fine and often
      useful, but this command's own file-selection rule above ("no `store.fga.yaml`,
      exactly one other `*.fga.yaml` -- use it") will promote whatever it finds to *the*
      source of truth on a later run, with no way to tell a deliberate fixture from a
      scratch one. Keep such files outside the repo, or delete them before finishing.
   5. Record `migration-map.json`'s `phase_status["5"]` as `"pending"`, not `"failed"` --
      nothing was attempted and rejected; there was nothing to convert. Put the detail in
      `artifact`: the glob command that confirmed zero candidates, and, if
      `/spicedb-dev:test-permissions` was run, the path to what it generated. A later
      addition of a `.fga.yaml` fixture (or a source system change) makes this command
      runnable for real; re-running it then picks up from case 1-3 normally.
   6. Report this plainly in step 9, in place of a conversion report: what was searched for
      and confirmed absent, what was generated instead (if `test-permissions` ran), and what
      was handed back as **Needs action** (if a non-fixture suite exists). Do not report this
      as if phase 5 failed -- it did not fail, it had nothing to convert, and that is a
      finding about the source project's test coverage, not a defect in this command.

This rule picks a *file*, independent of whether that file's own model reference is inline
(`model:`), a sibling `model_file:`, or one of the other forms `pack-contract.md` item 2
lists -- the model form does not change which file holds the `tuples:`/`tests:` content this
command reads, and phase 1 already converted the model itself.

### Step 5: Collect, resolve, and render relationship writes

**Collect.** Gather every `tuples:` entry from the selected file, tagged by its source: the
document root, or the specific `tests:` block it is nested in. Order matters and must be
preserved: root-level entries first, then each `tests:` block's own entries in file order
(`test-mapping.md`, "Collect tuples from both the document root and every `tests:` block").
Reading only the root-level key is a real, high-consequence bug, not a hypothetical one
(`test-mapping.md`'s own framing) -- a store with all its writes nested
(`condition-data-types`: 18 of 18) produces a silently empty `relationships:` block if the
nested source is skipped.

**Detect collisions**, over the collected entries, before rendering anything --
`test-mapping.md`'s "Multi-block collisions" section names two independent mechanisms with
opposite visibility, and both are keyed off the raw `(object, relation, subject)` data, not
off anything rendered yet:

- **Mechanism A** -- the same `(object, relation, subject)` triple recurs across two sources
  with a *different* `condition:` (or one has a condition and the other does not). `zed
  validate` rejects the naive union outright, so this cannot be silently kept as-is.
- **Mechanism B** -- two different `tests:` blocks each write a *different* relation onto the
  *same* object (root-level entries never count toward this check -- they are shared baseline
  state present in every scenario, not an isolated fixture). `zed validate` never objects to
  this one; it is a semantic conflict, not a syntactic one.

Apply `test-mapping.md`'s `check_test_collisions.py` logic (run it directly if Python and
PyYAML are available in this environment; otherwise apply the same two groupings by hand from
the entries just collected -- it is a straightforward pass, not a tool dependency). Check its
scopes match the two mechanisms above before trusting a clean result: **mechanism A must be
keyed over the document root *and* every `tests:` block**, since a root-level tuple can
collide with a nested one and root entries are written into every derived file; **mechanism B
is keyed over `tests:` blocks only**. A version keyed off `tests:` alone for both cannot see a
root-vs-nested collision at all, and reports clean rather than reporting nothing found.

**Resolve mechanism A**, per-triple: keep the first-seen write (the same root-then-file-order
precedence used to collect), discard the rest. **Record every discarded triple** directly in
`migration-plan.md`'s **Deferred / manual** section -- no `migration-map.json` counterpart for
a collision record, so this is a narrative append -- naming both bindings, and flag the
discarded scenario for a hand-written `zed permission check --caveat-context` probe against a
deployed instance -- per `test-mapping.md`'s own framing, the discarded scenario's checks are
not merely untested, they can pass by coincidence once the surviving binding is written, so
silently keeping one value with no trace is not an acceptable resolution on its own. File
every such entry under **`### Needs action`**, not `### For the record`. **This is a
deliberate exception to the mechanical marker-driven default** (`findings-report.md`'s "Needs
action vs. for the record"): the marker left at the call site below is a
`NOTE(spicedbmigration):`, which by that rule's own default would file as "for the record,"
but the hand-written follow-up probe against a deployed instance is still outstanding work a
human has not done -- exactly what "Needs action" means -- so the marker's shape and the
entry's subsection diverge here on purpose. This is the one place in this command that
exception applies; do not generalize it to any other entry below. Also carry the same note
into the emitted file as a `# NOTE(spicedbmigration):` YAML comment, matching the shape of
`test-mapping.md`'s worked example -- the finding must be visible on the file itself, not
only in the plan.

**Resolve mechanism B**, per colliding object: there is no single-graph fix
(`test-mapping.md`: "There is no single-graph fix"). Use `AskUserQuestion` to ask which
colliding `tests:` block represents canonical steady-state seed data; that block becomes the
scope for `validation.yaml` (this file's primary output), and every *other* colliding block
gets its own `validation-<scenario>.yaml`, holding only that block's own tuples and checks
plus every root-level tuple (shared baseline state, present in every derived file). A `tests:`
block that is not party to any collision is unaffected and stays in the primary output
regardless. **This shape -- exactly one colliding pair, with every other block, if any,
untouched -- is the only one the corpus has exercised** (`abac-with-rebac`). If more than two
blocks collide over the same object, or a colliding block also carries checks unrelated to
the collision, the split above is a reasonable generalization but not a verified one; if it is
not obviously correct for the file you are converting, say so and confirm the split shape with
`AskUserQuestion` rather than guessing. Record the chosen canonical scenario, the reason, and
exactly which checks the canonical file cannot verify (the colliding keys) directly in
`migration-plan.md`'s **Deferred / manual** section -- no `migration-map.json` counterpart,
same as mechanism A above -- per `test-mapping.md`'s numbered resolution steps. File it under
**`### For the record`**, unlike mechanism A above: no marker is emitted for this decision at
all (mechanism B produces no `# NOTE(spicedbmigration):` comment, only the split files
themselves), and by the time this entry is written the split is already resolved and the
excluded scenario's checks are already captured and `zed validate`-clean in their own
`validation-<scenario>.yaml` (step 7) -- "resolved, with nothing further to do"
(`findings-report.md`'s "Needs action vs. for the record"), the same mechanical default the
file-selection entry above follows, with no exception this time.

Do not read mechanism A's silence from `zed validate` (it accepts the resolved output cleanly
once the collision is resolved) as evidence nothing needs recording, and do not read mechanism
B's total absence of any complaint from `zed validate` as evidence nothing is wrong --
`test-mapping.md`'s own framing: the silent one is the more dangerous of the two precisely
because nothing downstream flags it unless you go looking.

**Render.** For every entry surviving into a given output file, render one relationship-write
line:

1. Resource side (`object`+`relation`, left of `@`): `migration-map.json`'s
   `relation_splits[type][relation].relation` when present, else `permissions[type][relation]`.
   A write **always** targets a relation, never a permission -- this is
   `test-mapping.md`'s single most likely error, and getting it backward produces a file that
   loads and looks correct while testing the wrong surface.
2. Subject side, when the subject is a userset (`"T#rel"`): **always** the permission name,
   `permissions[type][relation]` -- never `relation_splits`, even when that relation split.
   This is the third position `test-mapping.md` calls out explicitly, distinct from the
   resource side one line above.
3. Both resource and subject object IDs, through `id_encoding` (`mode`/`types` from the map).
   The wildcard subject id (`*`) is never encoded.
4. A tuple-level `condition:` block, as a `[name:{json}]` (or bare `[name]` with no context)
   suffix -- canonicalize context with sorted keys and compact separators
   (`json.dumps(ctx, sort_keys=True, separators=(",", ":"))`, or the equivalent by hand for a
   small object). Pass the caveat name through **verbatim** unless `migration-map.json`'s
   `caveat_renames` records a rename for that exact name from phase 1; a name that satisfies the caveat-name regex but
   was never declared under that exact spelling in `schema.zed` fails at `zed validate`, not
   here.

Sort the finished lines for each output file and join with newlines for its
`relationships:` block. (`relationships:` is sorted; `assertTrue`/`assertFalse` in step 6 are
not -- `zed validate` does not care about either file's line order, and the corpus's own
committed output is not consistently sorted for assertions, so preserve fan-out order there
rather than inventing a sort key.)

### Step 6: Render assertions and advisory findings

For every `tests:` block scoped into a given output file (per step 5's mechanism-B
resolution, or every block when no split fired), walk its `check:` entries and implement the
full |users| x |objects| x |assertions| product `test-mapping.md`'s "Fan-out" section
describes -- singular `user`/`object` are the one-element-list shorthand; implement the
general case even though no corpus store exercises the plural form. For every
`(user, object, relation, expected)` combination:

1. Render through `permissions[type][relation]` -- never `relation_splits`; a check always
   names the permission.
2. Append a ` with {json}` suffix, canonicalized the same way as step 5's tuple condition,
   when `check.context` is truthy.
3. Route to `assertTrue` when `expected` is `true`, to `assertFalse` when `false`.

There is no source construct that produces `assertCaveated`; do not emit one from converted
data (`test-mapping.md`, "`assertCaveated`: the target-only third state"). It remains
available for a hand-written supplementary check, e.g. verifying a mechanism-A discarded
scenario per step 5.

An output file whose scope has no `check:` entries at all (relationships only) is valid, not
an error -- `assertTrue`/`assertFalse` are simply empty lists.

**`list_objects` and `list_users` never convert.** Per `test-mapping.md`'s
"`list_objects`/`list_users`: advisory only", record every block found in the *selected
source file as a whole* (not scoped per output file -- these blocks carry no `tuples:` of
their own to be party to a mechanism-B split) as:

1. A `# NOTE(spicedbmigration):` YAML comment at the top of `validation.yaml`, one line per block, in the
   `test "<name>": <key> (<n> entries)` shape that section's worked example shows -- `<n>` here
   is the count of dict entries under that block's `list_objects:`/`list_users:` key itself
   (matching `advisory_notes`' own `len(entries)`), a **different** count -- never larger, and
   equal in most corpus cases -- than the relation-key count the coverage fraction below uses;
   do not conflate the two.
2. An entry directly in `migration-plan.md`'s **Deferred / manual** section -- no
   `migration-map.json` counterpart -- naming how each should be verified once the schema is
   deployed -- `list_objects` via `LookupResources`, `list_users` via
   `LookupSubjects`/`Expand`. File every such entry under **`### Needs action`**: a human
   still has to run that verification against a deployed instance before it is closed.

**Compute this store's own coverage fraction; never quote another store's.**
`test-mapping.md`'s "Coverage cost" section states the convention --
`checks / (checks + list_objects + list_users)`, counting every quantity by relation-key
count (the same convention `fga model test`'s own `ListObjects`/`ListUsers` reporting uses),
over the *whole selected source file*, not any one split output -- and gives the corpus-wide
median (83.3%) and minimum (`gdrive`, 33.3%) only as reference points for what this generally
costs. Compute the actual fraction for the file just converted and state it in step 8; do not
imply full coverage just because `zed validate` reports every relationship and assertion
loaded cleanly -- `list_objects`/`list_users` entries are excluded from that count entirely,
not silently passed.

**Verify every `# NOTE(spicedbmigration):` comment this step (and step 5's mechanism-A
resolution) produced, mechanically, before step 7.** These are shape (b) header manifests, not
call-site markers -- `findings-report.md`'s "Inline markers", "(b) Generated-file header
manifest" -- so **(a)'s two-line cap does not apply to them; a nine-item collision list is
legitimately ten lines.** What (b) requires instead: exactly one context line, then exactly
one line per item, and the context line points at this run's own `migration-plan.md` entry
(step 5's collision record, or step 6 rule 2's advisory entry above) -- never a prose
paragraph, and never one item wrapped across two comment lines. Run

```
grep -rn "TODO(spicedbmigration)\|NOTE(spicedbmigration)" <every emitted output file>
```

over every `validation*.yaml` this run is about to write to locate each marker, then read the
contiguous run of comment lines beneath it (no `-A`-bounded window -- unlike (a), (b) has no
fixed length to bound it at; the block ends where the comment prefix stops). Confirm every
line in that run other than the marker's own first line is a `#   - ` item, not a continuation
of the context sentence -- a line that is neither has grown a second prose line and must be
rewritten (fold it into the one-sentence context, or make it its own item) before step 7.
Record the total marker count and the longest marker's length in lines; step 8 and step 9 both
need that number -- "legitimately long because it has many items" is not an exemption from
reporting the number, only from capping it at two.

### Step 7: Write the file(s) and validate

Write `schemaFile:` pointing at `[output-dir]`'s `schema.zed`, `relationships:` (step 5), and
`assertions:` (step 6) for each output file, with that file's advisory and collision comments
(steps 5 and 6) prepended -- **collision-risk findings first, then list-assertion findings,
then the YAML body**, matching what `test-mapping.md`'s reference implementation emits
(`validation_gen.py` prepends the list-advisory header, then prepends the collision-risk
header on top of it, so the risk block ends up first). No corpus store carries both kinds at
once, so nothing exercises the ordering; follow it anyway, so a store that does carry both
matches the reference rather than diverging on a detail neither is checking. Write every
output file to `[output-dir]` alongside `schema.zed`, so a bare `schemaFile: schema.zed`
reference is correct.

**Invoke `zed validate` with a path relative to the current working directory -- never an
absolute path.** Verified directly (v1.56.0/zed v0.31.1): `zed` resolves a relative
`schemaFile:` against the directory portion of the argument *as given on the command line*,
not against its resolved absolute location, and rejects the combination outright when that
would require an absolute schema path -- `zed validate --fail-on-warn
/abs/path/to/validation.yaml` fails with `schema filepath ... must be local to where the
command was invoked` even when `schema.zed` sits right next to it and the shell's current
directory is that same folder. A bare relative filename, or a relative path with a
subdirectory prefix (`zed validate --fail-on-warn expenses/validation.yaml` from its parent),
both work. The safe pattern is to run from `[output-dir]` itself:

```bash
cd <output-dir>
zed validate --fail-on-warn validation.yaml
zed validate --fail-on-warn validation-<scenario>.yaml   # once per split file, if any
```

**A validation failure is not a signal to hand-edit the output past the conversion rules
above.** Check, in this order, before anything else: the `schemaFile:` path trap just above (an
absolute argument path produces an error that looks like a schema problem but is not one);
a relation-split write that targets the permission instead of `__direct` (step 5.1); a caveat
name that satisfies the regex but was never declared under that exact spelling in `schema.zed`
(`test-mapping.md`'s "second, sharper risk" under "Check-time context..."); a mechanism-A or
mechanism-B collision step 5 missed. Report the failure and the file if none of those explain
it.

**A file that still does not validate leaves phase 5 `failed`, not `complete`.** This run is a
verification gate like any other, and `findings-report.md`'s `## Phase status` vocabulary is
explicit: "A phase whose verification gate did not pass is `failed`, never `complete`." Carry
that outcome into step 8 -- reporting the failure to the user is necessary but not sufficient,
because the report is not what downstream commands read. `migration-map.json`'s
`phase_status["5"]` is. A `complete` status written over a failed validation tells
`/spicedb-dev:migrate` that phases 0-5 are all done and routes the user onward toward cutover
on a plan whose test conversion never validated.

### Step 8: Update the migration plan

Three passes, strictly in this order -- `migration-map.json` first, then the narrative
Markdown appends, then the regenerated rendering -- per `findings-report.md`'s
"`migration-plan.md` is a pure human-readable rendering of that same state" and its "Two
groups of sections, one rule each" rule (cited, not restated):

1. **Update `migration-map.json`.** Write `phase_status["5"]` from step 7's actual result,
   never unconditionally: `{"status": "complete", "artifact": ...}` **only if** `zed validate
   --fail-on-warn` passed on every emitted file **and** step 6's marker-shape check found
   every block correctly shaped (one context line, then one line per item -- or every
   malformed block was rewritten before this step); `{"status": "failed", "artifact": ...}` if
   either is untrue, with the failing file and the validator's own message as `artifact` for a
   validation failure, or the marker's `file:line` and the offending line for a malformed
   block. `findings-report.md`'s closed vocabulary: "A phase whose verification gate did not
   pass is `failed`, never `complete`." Writing `complete` over a failed validation, or an
   unrewritten malformed marker, is what sends `/spicedb-dev:migrate` on to cutover routing
   with a test conversion that never validated. This command writes only `phase_status["5"]`
   -- every other top-level key is left untouched.

2. **Append the narrative findings directly to `migration-plan.md`.** Re-read
   `migration-plan.md` and append (never replacing existing content) every mechanism-A
   collision record, mechanism-B split decision, and file-selection decision from steps 4-5,
   and every `list_objects`/`list_users` advisory note from step 6 plus the store's own
   coverage fraction, all under **Deferred / manual**, each filed into `### Needs action` or
   `### For the record` per steps 4-6's own classification rules above. Every appended entry
   follows `findings-report.md`'s "Inline markers" required-reference shape -- site
   `file:line`(s) (the colliding triple or block's own location in the source `.fga.yaml`),
   the governing `test-mapping.md` section by name, and a back-reference to the matching
   `NOTE(spicedbmigration):`/collision comment in the emitted YAML. This pass has no
   `migration-map.json` counterpart -- **Deferred / manual** is narrative, per
   `findings-report.md`'s "Two groups of sections" rule.

3. **Regenerate the rendered sections from the just-updated JSON.** `## At a glance`, `## Needs
   your attention`, `## Decisions`, `## Identifier map`, `## Relation splits`, `## Arrow
   aliases`, and `## Phase status` are always regenerated in full from the current
   `migration-map.json`, never edited in place, per `findings-report.md`'s "Two groups of
   sections, one rule each" rule -- cited, not restated. This command still does not touch
   `## Source`, `## Scan scope`, `## Target`, or `## Sync obligations`; leave them
   byte-identical, the same discipline `/spicedb-dev:migrate-schema` step 8 applies to its own
   plan update.

### Step 9: Report

Tell the user:

1. Which source file was converted, and which file-selection branch fired (step 4).
2. Where each validation YAML was written, and whether it passed `zed validate --fail-on-warn`.
3. **Coverage**: N assertions converted, N `list_objects`/`list_users` entries left advisory,
   and the fraction (step 6) -- stated as a fact about this store, not the corpus.
4. **Collisions, if any** (step 5): which mechanism, which triples or objects, how each was
   resolved, and which files that produced.
5. **Marker count**: the total `NOTE(spicedbmigration):` marker count and the longest marker's
   length in lines, from step 6's mechanical check -- a number, not "markers were kept short."
   Length here is informational, not a cap violation: these are shape (b) header manifests, not
   call-site markers, so length tracks item count, not an overrun. A run with zero markers
   states that too.
6. That `migration-plan.md` was updated -- regenerated from the `migration-map.json` phase 5
   just wrote -- and that it remains the durable record -- the same framing every other phase
   in this pipeline uses.
7. Next steps:
   - `zed validate` above already ran fully offline, and **cannot be pointed at a server** --
     it answers from the `relationships:` written inside the YAML file itself and reads
     nothing from any endpoint (see this command's Notes). `--endpoint`/`--token` are global
     `zed` flags every subcommand inherits, so `validate` accepts them and ignores them;
     against a port with nothing listening it still prints `Success!` and exits 0. Do not
     offer it as a live check.
   - The live checks below **do** need a server, because they are the ones `zed validate`
     cannot perform. Deploy the schema and actually load the relationships first -- the
     `relationships:` block is in `resource#relation@subject` form, and `zed relationship
     touch` reads whitespace-separated triples from stdin, so it needs converting:
     ```bash
     spicedb serve-testing &
     zed schema write schema.zed --endpoint localhost:50051 --token <your-token> --insecure
     python3 -c "import yaml; print('\n'.join(l.strip().replace('#',' ',1).replace('@',' ',1) \
       for l in yaml.safe_load(open('validation.yaml'))['relationships'].splitlines() if l.strip()))" \
       | zed relationship touch --endpoint localhost:50051 --token <your-token> --insecure
     ```
     (A store with caveated relationships cannot be loaded this way -- `zed relationship`'s
     `--caveat` flag applies one binding to the whole batch. Write those through a client SDK,
     or re-use the load script `/spicedb-dev:migrate-data` emits.)
     `serve-testing` (v1.56.0+) takes no `--grpc-preshared-key` and gives each distinct
     `--token` its own isolated datastore -- pass `--endpoint`/`--token` explicitly on every
     `zed` invocation rather than `zed context use`, which rewrites shared global `zed`
     configuration instead of scoping to this one migration.
   - Verify every advisory finding by hand against that deployed instance: `LookupResources`
     for each `list_objects` block, `LookupSubjects`/`Expand` for each `list_users` block, and
     `zed permission check --caveat-context` for any mechanism-A discarded scenario.
   - **Phases 3 (data) and 4 (code) may still be pending** -- read `migration-map.json`'s
     `phase_status["3"]` and `phase_status["4"]` and say which. Test conversion does not
     require them to be complete; it consumes only `schema.zed` and `migration-map.json`. A
     validation file that passes `zed validate` proves the *schema* is reachable, not that
     production data or client code have actually been migrated.

## Error Handling

| Situation | Do this |
|---|---|
| No `migration-plan.md` | Halt. Direct to `/spicedb-dev:migrate`. This command has no reduced gate of its own. |
| `migration-map.json`'s `phase_status["0"].status` not `complete (full gate)` | Halt. Direct to `/spicedb-dev:migrate`, which detects this marker itself and re-runs the full gate. Do not re-run the gate here. |
| Unresolved Class A finding (`migration-map.json`'s `decisions.per_blocker_resolutions` has a `null`/absent `resolution`) | Halt. List the unresolved blockers. |
| `migration-map.json`'s `phase_status["1"]` not complete, or `schema.zed`/`migration-map.json` missing from `[output-dir]` | Halt. Direct to `/spicedb-dev:migrate-schema`. |
| No pack, or pack has no test-mapping reference | Halt. An unsupported source needs a mapping written first. |
| No `store.fga.yaml`, more than one other `*.fga.yaml` | Ask which file with `AskUserQuestion`; record the choice and reason directly in `migration-plan.md`'s **Deferred / manual -> For the record** subsection (step 4). |
| Zero `.fga.yaml` files anywhere (confirmed with `find . -name '*.fga.yaml'`) | Not a halt and not a failure. Skip steps 5-7. Point at `/spicedb-dev:test-permissions` against `schema.zed` to generate fresh SpiceDB tests instead of converting nonexistent ones; if the project has non-fixture test coverage (a live-API-driven suite), record it under **Deferred / manual -> Needs action**. Record `phase_status["5"]` as `"pending"` (not `"failed"`), with the confirming command and what was generated in `artifact`. Re-running this command later, once a `.fga.yaml` exists, proceeds normally. |
| Mechanism A collision (same triple, different caveat context) | First-seen wins. Record both bindings directly in `migration-plan.md`'s **Deferred / manual -> Needs action** and as a `# NOTE(spicedbmigration):` comment; flag the discarded scenario for a hand-written check -- **Needs action** despite the `NOTE` marker, per step 5's exception. |
| Mechanism B collision (same object, different relation) | Cannot merge. Ask which scenario is canonical with `AskUserQuestion`; emit one additional file per other scenario; record the split and reason directly in `migration-plan.md`'s **Deferred / manual -> For the record** subsection (step 5). |
| `zed validate --fail-on-warn` fails | Report it. Check split-vs-permission targeting, caveat-name/schema linkage, and a missed collision before anything else; do not hand-edit past the conversion rules. **And mark `migration-map.json`'s `phase_status["5"]` `failed`, never `complete`** (step 8) -- reporting it to the user does not stop `/spicedb-dev:migrate` from reading a `complete` status and routing onward to cutover. |
| Step 6's marker-shape check (`grep -rn`, then reading each block) finds a `# NOTE(spicedbmigration):` comment block with a line that is neither the context sentence nor a `#   - ` item | Rewrite it before step 7, not after -- fold the stray line into the one-sentence context or make it its own item; do not shorten the item list to fit a line cap, (b) has none. A malformed block left unrewritten sets `migration-map.json`'s `phase_status["5"]` to `failed`, with the marker's `file:line` as its `artifact`. |

## Notes

- The version floor is SpiceDB **v1.52.0**; `test-mapping.md`'s conversion rules were
  verified against v1.56.0, zed v0.31.1, and `fga` v0.7.20.
- **Ask once.** `AskUserQuestion` covers file-selection ambiguity (step 4) and a mechanism-B
  split decision (step 5). Every other rewrite in this command is a lookup against
  `migration-map.json`, not a decision -- there is nothing else to ask about.
- `tools/migration-harness/` is **not shipped with this plugin**. It is the parity harness
  used to validate the pack against real OpenFGA stores, and it lives in the plugin's source
  repository (`authzed/authzed-marketplace`). Nothing in this command requires it, and this
  command should not run it or reference it to the user -- it is a development-time tool, not
  something a customer repository has installed. `zed validate` (step 7) is this command's
  own, sufficient, offline correctness check.
- `zed validate` needs no running SpiceDB server -- it validates a schema plus its embedded
  relationships and assertions entirely in-process. A live `spicedb serve-testing` instance
  (Notes above, and step 9's next steps) is only for the follow-on live checks this command
  cannot perform itself: the advisory findings (`list_objects`/`list_users`) and any
  mechanism-A hand-written probe.
