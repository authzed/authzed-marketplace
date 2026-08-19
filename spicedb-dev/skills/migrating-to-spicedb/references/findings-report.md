# Findings Report Format

The three artifacts the pre-flight gate (phase 0) produces, and every later phase reads
instead of re-asking: the finding taxonomy that decides what halts, `migration-map.json`
(below), and `migration-plan.md` (below).

**Each of the latter two artifacts has exactly one audience, and the boundary between them
is drawn on that basis, not on file format:**

- **`migration-map.json` is the single machine-readable record.** Every phase reads and
  writes *all* machine state here -- identifiers, decisions, per-blocker resolutions, and
  phase status -- and nothing else. This is what makes each sub-command independently
  runnable and the whole migration resumable.
- **`migration-plan.md` is a pure human-readable rendering of that same state**, regenerated
  from `migration-map.json` every time a phase touches it, plus a small number of sections
  (`## Scan scope`, `## Sync obligations`, `## Deferred / manual`) that exist only in the
  plan because they are narrative findings with no machine consumer, never counterpart
  fields in the JSON.

**No phase may parse `migration-plan.md` to decide anything.** A human may freely edit the
Markdown; nothing reads it back, so nothing a human edits there can silently break
resumption -- see this file's closing note under `## migration-plan.md` for what editing it
does and does not do.

Everything here is framework-owned and source-agnostic. The concrete blockers, naming
rules, and test-mapping gaps that *populate* a finding are supplied by the source's pack
(see `pack-contract.md`, items 4, 5, and 8) -- this file defines the shape they get poured
into, not their content.

---

## Finding classes

Every finding phase 0 produces is exactly one of three classes. The class decides what
happens next; it is orthogonal to a construct's fidelity rating (`clean`/`effort`/`heavy`/
`blocked`, defined in `SKILL.md`) -- a rating describes the *construct*, a class describes
what the *user must do about it*.

### Class A -- hard blockers

No mechanical fix. Conversion cannot proceed until the user makes a decision. Class A
covers both `heavy` and `blocked` constructs; the finding must state which fidelity rating
applies and why, plus the concrete options being offered. **Never convert past an
unresolved Class A finding.**

A pack's blocker catalog (`pack-contract.md` item 4) supplies the actual detection rules
and options for its source; that catalog is where Class A findings come from.

### Class B -- normalization decisions

Mechanical -- there is a deterministic algorithm and it will run either way -- but the
result changes stored data, so the user must see and own it before it happens, not
discover it after. Typical Class B findings, all arising from SpiceDB's own naming and ID
rules (`pack-contract.md` item 5) rather than from any one source:

- Identifier collisions after normalization (two distinct source names reduce to the same
  SpiceDB name).
- Names shorter than SpiceDB's 3-character minimum or longer than its 64-character maximum
  before mangling.
- Object IDs containing characters outside SpiceDB's object-ID charset.
- The relation-split naming convention (default `<name>__direct`) wherever a pack's schema
  mapping (`pack-contract.md` item 3) splits a fused direct/computed construct into a
  SpiceDB `relation` + `permission` pair -- this choice drives both the data rewrite
  (phase 3) and the code rewrite (phase 4), so it is recorded here even though it is
  mechanical.
- Permission names that read as nouns rather than verbs, against SpiceDB's own convention
  (`spicedb-schema-design/references/anti-patterns.md`, "Confusing Relations with
  Permissions"). Preserving is the default; renaming, where a pack-defined verb exists, is
  the alternative -- either way the choice is recorded once, at the gate, and applied
  mechanically wherever the name resolves after that (`openfga-to-spicedb/references/
  schema-mapping.md`'s "Permission naming style").

### Class C -- advisory

Recorded in `migration-plan.md`, never halts the gate. Typical categories:

- Source-model patterns whose behavior the source system itself is in the process of
  changing or deprecating -- flagged distinctly, because a faithful conversion of
  *current* source behavior may be porting a bug the source is actively removing.
- Loss-of-information findings: something the source silently permits that the target
  rejects or ignores, especially where the target's rejection happens at deploy time
  rather than at local compile -- these must be caught here or they surface later, at the
  worst possible point.
- Test-assertion styles in the source format with no SpiceDB validation-YAML equivalent.
- Call sites or endpoints with no conversion target at all (administrative/management APIs
  the target has no analog for).

A pack's blocker catalog and test-mapping reference (`pack-contract.md` items 4 and 8)
supply the concrete instances for its source.

---

## `migration-plan.md`

**This file is a rendering, not a record.** Every phase writes the decision it just made to
`migration-map.json` (below) first, then regenerates `migration-plan.md` from that same
file so a human always has something current to review -- the plan is never the place a
decision is made or stored, only the place it is displayed. It follows the plugin's
existing `authorization-plan.md` / `permission-model.md` convention of one durable,
human-readable plan file per project phase; "durable" here describes the file's presence
across sessions, not its authority -- `migration-map.json` is what is durable in the sense
that matters for correctness.

**Layout is optimized for a ten-second triage, not narrative completeness.** A reviewer
opening this file needs to know, before reading anything else: is there anything I must
decide or do right now, and can the pipeline proceed. `## At a glance` and `## Needs your
attention` exist to answer that before the reader reaches a single table of resolved
history. Every other section is either a rendering of `migration-map.json` (marked as such
below) or a narrative finding with no machine consumer (`## Scan scope`, `## Sync
obligations`, `## Deferred / manual`) -- those three are the only sections this file is the
sole record of; everything else can be regenerated from the JSON at any time with no loss.

Required sections, in order:

```markdown
# Migration Plan: <source> → SpiceDB

> Rendered from `migration-map.json`. Editing this file changes nothing a command reads --
> see "This file is a rendering, not a record" in `findings-report.md`. To change a
> decision, re-run the phase that owns it, or hand-edit `migration-map.json` directly.

## At a glance
- **Pipeline:** phases <numbers> of 5 complete -- next: <phase name, or "nothing left to automate">
  <!-- Name which phases are complete, do not count them: phases 3 and 5 have no ordering
  dependency on each other or on 4, so completion is routinely out of order and a bare count
  cannot express it. "phases 1, 2, 4 of 5 complete -- next: data" is right; "phase 3 of 5
  complete -- next: data" is the same state rendered as a count and reads as though phase 3
  were done. Phase 0 is the gate, not one of the five: a run that has only gated renders
  "no phases of 5 complete -- next: schema". "next" is the lowest-numbered phase whose
  phase_status is not "complete"; a phase that is "pending" is not complete and can be
  "next" even when higher-numbered phases are done. -->
- **Unresolved blockers:** N
- **Deferred / manual -- needs action:** N
- **Deferred / manual -- for the record:** N
- **Sync obligations:** N
- **Ready to proceed?** <one line: yes, or what is blocking>

## Needs your attention
*(omit a subsection entirely when its count is zero, rather than writing an empty table)*
### Unresolved blockers
| blocker | site | rating | options |
|---|---|---|---|
### Needs action
| item | site(s) | class | rule |
|---|---|---|---|

## Decisions
*(rendering of `migration-map.json`'s `decisions` key)*
### Tenancy
### Identifier strategy
### Relation-split naming
### Permission naming style
### Consistency strategy
### Per-blocker resolutions
| blocker | site (file:line, or "model") | rating | resolution |
|---|---|---|---|

## Source
system, version, model location(s), store count, SDK(s) and versions detected

## Scan scope
directory(ies) the analyzer swept, whether they contain application code, and the resolved
status of every code-side Class A sweep

## Target
SpiceDB version floor, endpoint, client language(s)

## Identifier map
*(rendering of `types` + `permissions` + `identifier_notes`; `kind` is "type" for a `types`
entry, "permission (split)" for a `permissions` entry also present in `relation_splits`, and
otherwise "relation" or "permission" as declared in `schema.zed` -- `migration-map.json`
does not itself distinguish an unsplit relation from a permission, since that distinction
is schema.zed's to make, not the identifier map's. **Before phase 1 has run, neither source
that sentence names exists** -- `relation_splits` is not written until phase 1, and
`schema.zed` does not exist yet -- so a phase-0 rendering derives `kind` from the source
model instead: a source `define` that fuses direct assignment with computed terms will split,
so render it `permission (split)`; one that does not, render by what it is in the source. Say
in the section note that the column is provisional until phase 1 writes `relation_splits`,
and re-render it from `relation_splits` once that exists. Do not leave the column blank and
do not omit the table -- the map is the reason the report is reviewable at the gate)*
| source name | spicedb name | kind | note |
|---|---|---|---|

## Relation splits
*(rendering of `relation_splits`)*
| definition | source relation | spicedb relation | spicedb permission |
|---|---|---|---|

## Arrow aliases
*(rendering of `arrow_aliases`)*
| definition | relation | alias permission | arrow site(s) |
|---|---|---|---|

## Sync obligations
| obligation | source | write path | backfill | reconciliation |
|---|---|---|---|---|

## Deferred / manual
items requiring human work, and items recorded for the reader's awareness only -- see
"Needs action vs. for the record" below for how an entry lands in one subsection or the
other. Each entry is one row in its subsection's table (one line, per "Inline markers"
below), with the full required-reference shape (site file:line(s), governing rule,
candidate mapping with its verified/inferred tag, pack gap, marker back-reference) in a
`<details>` block beneath the table when a row needs more than its cells can hold.
### Needs action
### For the record

## Phase status
*(rendering of `migration-map.json`'s `phase_status` key)*
| phase | status | artifact |
|---|---|---|
```

**`## At a glance` and `## Needs your attention` are synthesized when the file is
regenerated, not separately maintained.** `At a glance`'s counts are computed by counting
the rows the render step is about to write into `Needs your attention`, `Deferred / manual`,
and `Sync obligations` -- there is nothing here for a phase to read back, only something for
every regeneration to recompute fresh. `Needs your attention -> Unresolved blockers` is the
subset of **Decisions -> Per-blocker resolutions** rows whose `resolution` is null; `Needs
action` is the **Deferred / manual -> Needs action** subsection's own rows, repeated at the
top so a reviewer sees both kinds of open work in one place without scrolling to two
sections. **Because the two carry identical row text, an edit aimed at "the first match" lands
in the wrong one and is silently discarded on the next render.** `## Deferred / manual` is the
record; `## Needs your attention` is derived from it and regenerated wholesale. Add, change,
or remove rows only in `## Deferred / manual`, then re-render -- and if a row you added does
not appear in both sections afterwards, you edited the derived copy, which is the failure this
paragraph exists to prevent (it produces no error and leaves the count unchanged). If both subsections would be empty, keep `## Needs your attention` with a single
line: `Nothing needs your attention -- N resolved blockers, 0 open items.`; do not omit the
heading itself, since its absence is easy to misread as "not rendered yet" rather than
"clean."

**Needs action vs. for the record**, under **`## Deferred / manual`**: the split is
mechanical, driven by the marker the entry cites, not a judgment call made per entry --
**an entry backed by a `TODO(spicedbmigration):` marker (or, from phase 1 onward, with no
marker at all yet, because the work behind it has not started) goes under `### Needs action`; an entry backed
by a `NOTE(spicedbmigration):` marker, or resolved with nothing further to do, goes under
`### For the record`.** A phase writing this section always knows which marker it left (or
whether it left one), so this requires no re-classification -- it is the same case analysis
`## Inline markers` below already requires the phase to make before it writes anything.
**Phase 0 is the one exception.** No code has been touched at the gate, so *nothing* carries a
marker and the mechanical rule would sweep every phase-0 entry into `Needs action`, including
purely informational ones. At phase 0 the split is a judgment about the finding itself -- does
a human have to do something about it, or is it context a reviewer should have --
and `/spicedb-dev:migrate` step 7 owns that judgment. The marker rule governs from phase 1 on. Both
subsections are tables: one row per entry (`item | site(s) | class | rule`), with a
`<details>` block immediately below the table for any entry whose full required-reference
shape does not fit its row -- omit the `<details>` block entirely for an entry whose row
already says everything (a one-line file-selection note, for instance). A marker's own line
2 still names `Deferred / manual` without needing to say which subsection; a reader (or a
future regeneration) finds the entry by scanning both tables, and the split can move an
entry from one to the other on a later run (a `TODO` resolved into a `NOTE`) without the
marker's pointer text ever needing to change.

**`## Phase status`** is the table every phase reads to decide whether it may run and what
to route into next, so its `status` column is a **closed vocabulary**, not free prose. It is
a rendering of `migration-map.json`'s `phase_status` key (below) -- **no phase reads this
Markdown table**; every read described in this section is a description of what the
rendered table shows a human, and applies equally to the JSON field it renders. One row per
phase (0, 1, 2, 3, 4, 5), and `status` is exactly one of:

| status | meaning |
|---|---|
| `pending` | Not run. The initial value for every phase but 0. |
| `complete` | Ran and succeeded. Phase 0 additionally qualifies it as `complete (full gate)`; phase 0 written by phase 1's reduced standalone gate is `inline (reduced -- no codebase analysis)` instead. |
| `failed` | Ran and did not succeed. The `artifact` column says what failed. A phase whose verification gate did not pass is `failed`, never `complete`. |
| `not implemented` | Legacy value. Before `/spicedb-dev:migrate-code` existed, phase 4 (client code) had no command and stayed `not implemented` permanently. That command now exists, and phase 4 writes `pending`/`complete`/`failed` like every other phase; a plan written before that command shipped may still carry `not implemented` in its phase-4 row, and a reader must treat that exactly like `pending` -- not run -- rather than as a value any phase writes today. |

**Rules that make the vocabulary usable:**

- **The `status` field holds the status token and nothing else.** Detail -- a validator's
  error count, a verification result, a file path -- goes in the `artifact` field. A value
  reading `clean -- 0 errors` matches no branch in any consuming command and is a defect,
  not a richer status.
- **Phase 2 is a status like any other.** It records whether the `schema-validator` agent
  passed: `complete` when the schema validated (put the validator's own summary in
  `artifact`), `failed` when it did not.
- **Readers match on the token, and treat anything unrecognized as not-complete.** A
  consuming command that cannot parse a value must say so and halt or route conservatively,
  never assume `complete`. Silently reading an unknown value as done is how a phase gets
  skipped. This applies to `phase_status["N"].status` in `migration-map.json` -- the JSON
  value is exactly as much a closed vocabulary as the rendered table cell was, and an
  unrecognized string is exactly as much a halt-and-say-so case.

**`## Scan scope`** is written by the full phase-0 gate from the `migration-analyzer`
agent's own **Confidence and gaps** section, and it exists so a code-side zero can never be
read as more confident than it is. Record the directory the analyzer actually walked, and
for every code-side Class A sweep (contextual tuples, model-ID pinning, store IDs) exactly
one of three states: **swept, none found** -- the directory holds real application code and
the sweep genuinely found nothing; **swept, but vacuous** -- the directory held no
application code at all, so a zero there is unconfirmed, not a finding (`migration-analyzer.md`
step 4 requires the agent to check for a dependency manifest, source files in the SDK's
language, and a matched SDK import *before* recording any code-side zero, and its step 7
report template carries the resulting classification as a required field in both the
"Swept and not found" section and "Confidence and gaps" -- not prose the agent volunteers
when it happens to notice; the gate must not silently promote it to "swept, none found"); or
**not swept** -- the sweep did not run. "Swept, none found" and "not swept" are the "none
versus nobody looked" distinction the whole finding taxonomy exists to preserve; "swept, but
vacuous" is the case in between, where the command ran but had nothing to examine, and it is
just as unconfirmed as "not swept" even though a command and an exit code exist for it. A
plan written by phase 1's reduced standalone gate may omit this section; step 3b runs
targeted greps, not the `migration-analyzer` agent's directory sweep, so it has no
Confidence-and-gaps section to draw this from.

**`## Sync obligations`** is written by the full phase-0 gate and carries every resolution
that creates permanent write-path work rather than one-time migration work: a construct
whose value SpiceDB cannot hold becomes a replicated edge the team owns forever, needing a
write path on every mutation that could change it, a backfill, a reconciliation job for
drift, and a fail-closed window between the source-of-truth write and the SpiceDB write.
**State the count** -- the count is what separates a migration from an ongoing
synchronization project, and it is mispriced whenever it surfaces late. `None.` is a valid
and useful value. A plan written by phase 1's reduced standalone gate may omit this section;
that omission is part of what "reduced" means.

**Phase 0 owns this section; phase 3 revises it in place and never appends a second one.**
Two phases derive an obligation count from different inputs -- phase 0 from the gate's
recorded resolutions, phase 3 from `migration-map.json`'s `decisions.per_blocker_resolutions`
plus, for a plan predating that key, a re-derivation from the converted `schema.zed` (the
source pack's data-mapping reference has the algorithm). They can disagree, and two tables under one
heading means two counts get read aloud to the user as if both were the answer. The rule:

- **Exactly one `## Sync obligations` section exists in the plan at all times.** Phase 3
  rewrites its rows; it does not add a heading, and it does not append to a table it did not
  read first.
- **Phase 3's derivation wins where the two disagree**, because it runs after every Class A
  resolution is recorded and is the phase that actually owns the write path. Phase 0's count
  is the gate's estimate; phase 3's is the finding.
- **A revision must be visible, not silent.** When phase 3's count differs from what it
  found in the section, keep the phase-0 rows it supersedes as a short note under the table
  (`phase 0 recorded N; phase 3 derived M because ...`) and say so in phase 3's report. A
  count that changes with no trace is indistinguishable from a count that was wrong twice.
- **`None.` is a value, not an empty section.** Phase 3 replacing `None.` with rows, or rows
  with `None.`, is a revision like any other and gets the same note.

This is the one section where phase 3's "leave every other section byte-identical" rule does
not apply, and it is scoped to exactly this heading.

The **Decisions** section is a rendering of `migration-map.json`'s `decisions` key (below)
-- **no phase reads `## Decisions` from the Markdown**; every rule stated here describes the
JSON field the section renders, and applies to that field, not to the table a human sees.
`decisions` is where every Class A finding's resolution is recorded, along with the tenancy,
identifier-strategy, and consistency-strategy choices that constrain every phase after it.

**`decisions.per_blocker_resolutions` is the plan's durable record of the Class A *sites*,
not only of the answers**, and it is an array of one entry per detected site for that reason,
each carrying `site` (`file:line`, or `"model"`), `rating` (the fidelity rating that site
earned), and `resolution` (the resolution chosen *for that site*, or `null` if the site is
still unresolved -- **`null`/absent is the one and only unresolved marker; a phase checking
"is this site resolved" tests exactly this field, never a Markdown cell's presence or
wording**). There is no separate `## Class A` heading or JSON key, and a later phase looking
for "the sites the plan lists" is looking at this array. That matters most for a blocker a
pack requires to be resolved **per call site** rather than in bulk (OpenFGA's contextual
tuples: one site may be `effort` and the next `blocked`) -- a single entry reading
"contextual tuples: re-model as caveat context" resolves exactly one site's worth of the
finding and leaves every other site's `resolution` at `null`, which is what
`/spicedb-dev:migrate-schema` and `/spicedb-dev:migrate-code` are both checking for when
they refuse to convert past an unresolved Class A finding. A model-only blocker (OpenFGA's
transitive wildcard) has no `file:line`; its `site` is the literal string `"model"`, with
the offending construct named in `resolution`. Blockers that were swept and found absent do
not get an entry here -- they belong in `## Scan scope`, with their sweep classification.
`resolution` names the `blockers.md` item it resolves by number, per "Inline markers"'
required-reference rule below -- "materialized" or "re-modeled" alone is not enough to find
the rule that offered it. **`decisions.tenancy.tenant_reachability_findings` carries every
Class C tenant-reachability finding** (a pack's blocker catalog may define one -- OpenFGA's
is `blockers.md`'s "tenant-root reachability gap in subject-aggregation types"), alongside
the tenancy decision itself, per that finding's own `Record` instructions: Class C never
halts the gate, but it is still a required part of the plan, not an optional note. The
rendered Markdown shows both under `### Tenancy`, in the same order.

**`decisions.permission_naming_style`** is the gate's `preserve`/`rename` choice for whether
a permission's name should follow SpiceDB's own noun/verb convention
(`spicedb-schema-design/references/anti-patterns.md`, "Confusing Relations with Permissions"
-- cited, not restated; `openfga-to-spicedb/references/schema-mapping.md`'s "Permission
naming style" is the pack's own detection and options for this decision). It carries
`decision` (`"preserve"` or `"rename"`), `asked`, and `evidence`, the same shape as
`relation_split_naming` -- **but, unlike every other field in `decisions`, it does not by
itself carry the per-name outcome.** A chosen rename is applied directly to the existing
`permissions[type]` entry (the renamed verb in place of the identity-mapped noun) and
annotated in `identifier_notes.permissions[type]` (below) -- the same two keys any other
permission rename already uses, mechanical or not. This is deliberate: unlike a relation
split, which needs the model walked before the generated names are even known, a
permission's own current name is already in `permissions[type]` by the time this decision is
made, so there is nothing this key would gain by duplicating it. `decisions.
permission_naming_style` records *why* a name looks the way it does; `permissions[type]` and
`identifier_notes` record *what* it looks like.

One field pair in `decisions` (and the two headings that render it) previously shared the
same name and was easy to conflate; they stay distinct, and neither should be read onto the
other:

- **`decisions.relation_split_naming`** holds the *naming* decision from the pack's gate --
  which suffix a fused direct/computed construct's split relation gets (`__direct` by
  default). It is filled at the gate, before any splitting happens, and renders under
  `### Relation-split naming` in `## Decisions`.
- **`relation_splits`** (a top-level `migration-map.json` key, described in its own section
  below) is the *record* of splits actually produced -- populated only once the model has
  been walked and the splits exist, and renders as the `## Relation splits` table.

The **Identifier map**, **Relation splits**, and **Arrow aliases** sections are pure
renderings of `migration-map.json`'s `types`/`permissions`/`identifier_notes`,
`relation_splits`, and `arrow_aliases` keys respectively (all four described below) --
**there is nothing to keep "in sync" any more: regenerating the Markdown from the JSON *is*
the sync**, and a phase that writes one of these JSON keys and then does not regenerate the
Markdown has left a stale rendering, not two disagreeing sources of truth.

**Two groups of sections, one rule each, for any phase updating this file.** `## At a
glance`, `## Needs your attention`, `## Decisions`, `## Identifier map`, `## Relation
splits`, `## Arrow aliases`, and `## Phase status` are **always regenerated in full** from
the current `migration-map.json` whenever a phase touches this file at all -- never edited
in place, never left stale even if this phase's own change did not touch every one of them,
because `## At a glance`'s counts depend on more than one JSON key at once. `## Source`,
`## Scan scope`, and `## Target` are edited in place only by the phase that owns the fact
recorded there (usually phase 0), and are otherwise left byte-identical. `## Sync
obligations` and `## Deferred / manual` are narrative sections with no JSON counterpart,
appended to or revised in place per their own rules above, and otherwise left
byte-identical. A phase's own instructions state which of the second group it touches this
run; everything else in that group stays untouched, and everything in the first group is
regenerated regardless.

**What editing `migration-plan.md` by hand does, and does not, do.** A reviewer can edit any
rendered table or heading in this file freely -- no phase parses it, so nothing here can
desynchronize resumption. What that edit does *not* do is change the state the pipeline
acts on: the next phase to touch this plan reads `migration-map.json`, not the edit, and the
next regeneration overwrites the edited text with a fresh rendering of the JSON, silently
discarding it. A reviewer who wants to change a decision has exactly two paths -- re-run the
phase that owns it (the gate, for anything under `## Decisions`), or hand-edit
`migration-map.json` directly, understanding that *is* the state, unlike the Markdown. The
three narrative-only sections (`## Scan scope`, `## Sync obligations`, `## Deferred /
manual`) are the exception this warning does not apply to: a phase that revises them (phase
3 for **Sync obligations**, per that section's own rule; any phase appending a **Deferred /
manual** entry) reads the *existing Markdown* first, because that text is the only copy of
what a prior phase recorded there -- an edit to one of those three sections is preserved (or
consciously superseded, per **Sync obligations**' own revision rule), not silently
discarded, because those three sections are the plan's own record, not a rendering of
something else.

---

## Inline markers

The in-code counterpart of a plan entry. Every phase that leaves a `spicedbmigration` marker
-- schema, data, code, tests, or a generated file's own header -- uses **exactly one of two
markers, defined here once**; every other file cites this section rather than restating it.

- **`TODO(spicedbmigration):`** -- a human must do something. The call site is unconverted,
  failing closed, approximated, or otherwise not finished.
- **`NOTE(spicedbmigration):`** -- informational; no action required. Something changed
  behavior but is correct, or is a deliberate choice worth knowing about.

A marker takes one of two shapes, depending on what it sits in. Both use the two markers above
and the target language's ordinary comment syntax, so one `grep -rn "spicedbmigration)"` still
finds every marker either shape ever leaves -- but the two shapes are not interchangeable, and
holding a shape (b) manifest to shape (a)'s two-line cap (or vice versa) is its own bug, not
rigor: a two-line cap applied to a nine-item manifest does not shorten it, it deletes eight
items' worth of record with nowhere else for them to go.

- **(a) Call-site marker** -- sits inside code someone is reading, interrupting it. Two lines
  maximum, pointing at the `migration-plan.md` entry that carries the reasoning. This is the
  shape "Format, no exceptions" below documents, and the shape `migrate-code.md` and
  `migrate-verify.md` leave at a call site or in a single-item data-quality note.
- **(b) Generated-file header manifest** -- sits at the top of a file the migration
  *produced* (a converted `validation.yaml`), enumerating items that had no conversion. It is
  a list, not an annotation: one line of context, then one line per item. See "(b)
  Generated-file header manifest," after (a)'s rules below.

### (a) Call-site marker: format, no exceptions

- **Two lines maximum.** Line 1: what, in one sentence. Line 2: where the detail lives --
  the `migration-plan.md` section this finding was recorded under (`Deferred / manual`,
  `Decisions -> Per-blocker resolutions`, etc.).
- **The plan carries the reasoning; the call site carries a pointer to it.** Alternatives
  considered, citations, candidate mappings found while investigating -- all of that goes in
  the plan entry the marker points at, never at the call site itself. A marker that restates
  the reasoning it's pointing at has defeated the point of pointing.
- **Wanting to write more than two lines is the signal, not an obstacle to write around.** If
  you find yourself justifying a judgment call, explaining why a rule does or does not apply to
  this case, or citing more than one source, that is the tell that the content belongs in the
  `migration-plan.md` entry, not the call site -- a subtle judgment worth defending is worth
  defending in the plan, where a reader expects reasoning, not in a comment, where a reader
  expects a pointer. Write the plan entry first, with the defense in it; the marker becomes a
  two-line pointer to an entry that already exists, not a growing justification in place of one.
- **Use the target language's ordinary comment syntax** (`#`, `//`, `/* */`, ...). The prefix
  text after it is identical in every language, so one `grep -rn "spicedbmigration)"` finds
  every marker a migration ever left, regardless of source language.
- **This rule protects authorization decision points, not every unhandled construct.** A
  construct's fidelity rating (`clean`/`effort`/`heavy`/`blocked`) says nothing about whether
  any caller ever consumes its result, and treating "unhandled" as synonymous with "must
  raise" is a real scoping bug, not a hypothetical one: applied literally to a discarded-return
  connection-warm call in a real application, it turned the *first authorization-touching
  request of every process's life* into an uncaught crash -- worse than the source app, which
  never threw there (the worked example below is that exact, live-verified case). **The
  scoping test: does any caller consume the result?** If nothing branches on it, raising
  converts a no-op into a crash and protects nothing. Classify every unconverted or unhandled
  construct into exactly one of three cases -- never decide by fidelity rating or by "this
  construct has no target" alone:

  1. **An unconverted authorization decision -- a caller branches on the result.** Returning
     `false` for "nobody implemented this" is indistinguishable from a real denial: the
     application keeps running, the marker sits unread in a file, and the gap resurfaces later
     as a mysterious permission denial someone debugs as a data problem instead of a missing
     conversion. **Raise. Never return a boolean.** This is not a new opinion, it is the same
     distinction already enforced elsewhere in this pipeline: `differential-harness.md`'s
     "Outcome" section (under "The record shape") keeps `ERRORED` separate from `DENIED` for
     exactly this reason -- collapsing them hid an entire defect class; `code-mapping.md`'s
     "`listRelations` error convention" section already refuses to let converted code inherit
     an OpenFGA SDK habit of swallowing errors as `allowed: false`; and `code-mapping.md`'s
     "Async-only target vs. sync source: the un-awaited-coroutine fail-open" section documents
     the same collapse from the other direction (a dropped `await` yielding a truthy
     coroutine).
  2. **A deliberate, gate-recorded fail-closed decision -- the user was asked, at the gate, and
     chose to have a specific call site deny.** **Return `false`, with a
     `TODO(spicedbmigration):` marker.** In this pack that option exists in exactly one place,
     `blockers.md`'s contextual-tuples entry ("Leave the call site failing closed with a
     `TODO(spicedbmigration):` marker") -- a real answer a human gave, recorded in
     `migration-plan.md`'s `Decisions -> Per-blocker resolutions`, not a stand-in for one.
  3. **A non-decision construct -- the call's return value is discarded, or it is a
     side-effect/warm-up call with nothing downstream that consumes its answer.** Nothing
     branches on it, so there is no decision for a raise to protect, and raising only
     introduces a crash the source application never had. **Remove it, or leave it inert with a
     `NOTE(spicedbmigration):` marker. Never raise.** Raising here is a regression against the
     source application's own behavior, not a safety measure.

  Case 1 and case 3 can look identical at the fidelity-rating level -- both are commonly
  `heavy` or genuinely unhandled constructs, absent from `code-mapping.md`'s call mapping
  table -- and differ only in whether a caller consumes the result, so check the call site's
  own callers before choosing, never the rating. Nothing else qualifies for case 2: an
  unhandled or unconverted construct that *is* a decision, with no gate decision behind it,
  always falls to case 1 and raises.

**Verify the two-line cap mechanically, before the phase that left the markers reports
`complete` -- a cap nothing checks is a suggestion, not a rule.** Every command that emits
markers runs this over every file it edited or wrote, and reports the result rather than
assuming it (this needs no plugin-internal tooling; it is exactly what a customer's own agent
can run over its own output):

```
grep -rn -A2 "TODO(spicedbmigration)\|NOTE(spicedbmigration)" <edited files>
```

For each match, the marker line plus the next line is the whole budget -- **if the second line
of context `-A2` prints is itself still a continuation of that comment** (same comment prefix,
no intervening blank or code line), the block has already run past two lines and **must be
rewritten**: move the excess into the `migration-plan.md` entry the marker points at (the
"wanting to write more than two lines" rule above), and shorten the call-site comment to a
one-sentence *what* plus a one-line pointer to *where*. **State the result in the phase's own
report: the total marker count, and the longest marker's length in lines.** A number in a
report is what makes a silent overrun visible; "markers were kept short" is a claim, not a
verification.

**The plan entry a marker's line 2 points at must carry real references, not a paraphrase of
them.** "See the call mapping table" is not a reference; `code-mapping.md`'s call mapping
table is -- a citation naming the wrong section, or a line number that has since
moved, costs a reader their trust in every other citation in the plan. Every entry a marker
can point at -- a `Deferred / manual` item, a `Per-blocker resolutions` row, any Class A/B/C
finding -- carries, when applicable:

- **Site(s):** `file:line` in the customer's own codebase, one per site. List every site a
  construct was found at; "several call sites" is not a reference, it is the absence of one.
- **Rule:** the governing pack reference that produced this finding, by file *and section* --
  `code-mapping.md`'s call mapping table, or the specific `blockers.md` item number,
  not the file name alone.
- **Candidate mapping**, when the run found one: `file:line` in the vendored client's own
  source, plus an explicit **verified by reading source** or **inferred, unconfirmed** tag --
  never left for the reader to guess. An untagged mapping is treated as unconfirmed.
- **Pack gap**, when the run surfaces one: a real client capability the pack's own reference
  files should document and don't is a finding about the pack, not only about the customer's
  code -- name the reference file(s) that should have carried it.
- **Marker:** confirmation that a `TODO(spicedbmigration):`/`NOTE(spicedbmigration):` marker
  was left at the site, so the two are navigable in both directions -- the marker names the
  plan section, the plan entry names the `file:line` it marked.

**Worked example -- case 3, a non-decision construct.** A call site whose method has no row in
`code-mapping.md`'s call mapping table and is not one of the "Operations with no SpiceDB
target" either -- an unhandled construct by the same test `migrate-code.md` step 6 applies --
but whose **return value the source code discards**: `fga_client.read_authorization_models()`,
called with its result never assigned or checked, sitting inside the client's lazy-init
function that every authorization wrapper in the file calls exactly once per process. This is
a real, live-verified case, not an invented one: a first-cut conversion that raised here (case
1, applied without checking whether anything consumed the result) made the **first
authorization-touching request of every process's life** throw an uncaught exception --
silently swallowed by a broad `except Exception` in the caller, which meant a brand-new user's
default-ownership relationship and default file were never created, permanently. The source
application never threw there, and nothing in it ever consumed this call's return value --
that is exactly the case-3 test: no caller branches on the result, so there is no decision for
a raise to protect, and raising is a regression, not a safeguard.

Before -- twelve lines, no consistent prefix, not `grep`-able, and the plan's own reasoning
duplicated at the call site instead of linked to it:

```python
    # UNHANDLED CONSTRUCT (phase 4 /spicedb-dev:migrate-code): the source called
    # `fga_client.read_authorization_models()` here (discarded return value, apparently a
    # connection-warm/verify call). That OpenFGA method has no row in code-mapping.md's call
    # mapping table (Sec 11.2) and is not one of the six "operations with no SpiceDB target"
    # that section explicitly lists either -- so per migrate-code.md step 6's closing rule
    # this is an *unhandled construct*, and must not be approximated with a guessed
    # equivalent. Left unconverted -- see migration-plan.md's Deferred/manual section and
    # this run's report for a verified candidate mapping found by reading the vendored
    # client's own source directly (not guessed): `async def read_schema(self) -> str`
    # exists at spicedb-python's client.py:351, undocumented in code-mapping.md and in
    # spicedb-client-integration/references/python.md alike.
```

After -- the two-line marker and the `Deferred / manual` entry it points at, read together as
one mechanism: neither half is useful alone, the marker without the entry is a dead end, and
the entry without the marker is a finding nobody at the call site would ever discover. **Case
3: the call is removed, not raised** -- a `NOTE(spicedbmigration):`, not a `TODO`, because
dropping a call whose result the source never used is the faithful, zero-behavior-change
translation:

```python
    # NOTE(spicedbmigration): read_authorization_models() dropped (discarded return, no target).
    # See migration-plan.md > Deferred / manual: read_authorization_models().
```

```markdown
## Deferred / manual

- **`read_authorization_models()`** -- `src/authz/client.py:88` (only call site found), inside
  the client's lazy-init function. No SpiceDB target: absent from `code-mapping.md`'s call
  mapping table and not one of the six entries in its "Operations with no SpiceDB target"
  section either -- an unhandled construct, per `migrate-code.md` step 6's closing rule.
  **Case 3, not case 1:** the source discarded this call's return value and no caller branches
  on it, so the call is removed rather than raised -- raising here was tried and reverted after
  live testing showed it crashed the first authorization-touching request of every process
  (swallowed by a broad `except Exception` upstream, silently breaking new-user setup).
  - **Rule:** `code-mapping.md`, call mapping table; "Operations with no SpiceDB
    target"; this file's "Inline markers" three-case rule, case 3.
  - **Candidate mapping:** `spicedb-python`'s `client.py:351`,
    `async def read_schema(self) -> str` -- **verified by reading the vendored client's own
    source directly**, not guessed. Semantics not confirmed equivalent to
    `read_authorization_models()`; review before adopting it.
  - **Pack gap:** `read_schema()` is undocumented in both `code-mapping.md` and
    `spicedb-client-integration/references/python.md` -- each should list it and doesn't.
    Flag against the pack, not only against this customer's code.
  - **Marker:** `NOTE(spicedbmigration):` at `src/authz/client.py:88`, call site removed.
```

Everything the twelve-line version put at the call site -- which rule classified it, the
candidate mapping found by reading the vendored client's own source, the `client.py:351`
citation, the doc gap -- moves into the `Deferred / manual` entry itself, where it is written
once and read by anyone who needs it, instead of copied at every site that reproduces the
same finding.

### (b) Generated-file header manifest: format

Not every marker interrupts code. Some sit at the top of a file a migration *produced* --
`migrate-tests.md`'s converted `validation.yaml`, or this pack's own internal harness
generating a corpus artifact -- enumerating items that could not be converted at all
(`list_objects`/`list_users` blocks, a discarded tuple collision). There is no single call
site to point away from: the manifest itself is the finding, not a pointer to one recorded
elsewhere. (a)'s two-line cap does not apply to it -- holding a nine-item manifest to two
lines would not shorten the comment, it would delete eight items' worth of record.

**Format, no exceptions:**

- **One line of context, then one line per item. No prose paragraphs.** The context line
  states what could not be converted, in one sentence. Every item after it is one line, one
  instance -- a name, a count, a triple -- never wrapped across multiple `#`/comment lines and
  never expanded into an explanation; if an item needs more than a line to state, that is a
  sign it belongs in the plan (below), not that the manifest may grow prose.
- **The context line points at where the detail lives, exactly as (a)'s line 2 does.** Where
  the run has a `migration-plan.md`, it points there -- the **Deferred / manual** entry (or
  **Decisions**, for a mechanism-A collision) naming how each item should be verified.
  **Where it does not** -- this pack's own internal corpus/doc generation runs `generate_validation`
  over a bare `.fga.yaml` store with no `migration-plan.md` in scope, and always will -- the
  context line points at the source artifact instead. `validation_gen.py`'s own two headers do
  exactly this: the `list_objects`/`list_users` advisory header (worked example below) points a
  reader at "the source `.fga.yaml` store directly," since no plan exists to point at instead;
  its tuple-collision header points at `schema-mapping.md`'s worked explanation of the risk
  being flagged (`test-mapping.md`'s `condition-data-types` example has that one in full).
- **Same two markers, same comment-prefix rule, same `grep`-reachability as (a).** Shape is the
  only thing that differs between (a) and (b) -- not vocabulary, not the marker names, not
  `grep -rn "spicedbmigration)"`'s ability to find it.

**Worked example** -- `validation_gen.py`'s `generate_validation`, run against
`openfga/sample-stores/stores/gdrive` (`test-mapping.md`'s "`list_objects` / `list_users`: advisory only" section has the fuller worked example, including the full file; this is the
header alone, byte-for-byte real output, no `migration-plan.md` in this context so the context
line points at the source store):

```
# NOTE(spicedbmigration): list_objects/list_users block(s) below have no validation-YAML equivalent and were not converted -- review the source .fga.yaml store directly:
#   - test "Test which documents can Anne read": list_objects (1 entries)
#   - test "Test who can access doc:2021-roadmap": list_users (1 entries)
#   - test "Check if the right users have access to the right documents": list_users (4 entries)
```

One context line, three item lines -- four lines total, for a three-item finding, and it would
be five for a four-item one. That the total exceeds two lines is not an overrun of (a)'s cap;
(a)'s cap was never (b)'s rule to begin with. What *would* be an overrun here: a context line
spanning two comment lines, or an item line that explains rather than names.

---

## `migration-map.json`

The machine-readable half of the plan. **Phase 0 emits it** from the model inventory and
the gate's identifier decision, and phase 1 rewrites it in place -- taking every entry
already there as fixed, and reserving only the generated names (split relations, arrow
aliases) it alone knows about. When phase 1 runs standalone, with no plan and no map, it
emits the file itself. Phases 3 (data, `/spicedb-dev:migrate-data`) and 5 (tests,
`/spicedb-dev:migrate-tests`) load it to rewrite source-side identifiers and names into
their SpiceDB-side form -- both commands are shipped and consume this file automatically.

**This is also the single file every phase reads and writes *all* machine state to** --
identifiers, decisions, per-blocker resolutions, and phase status alike -- per this file's
opening note. `types`, `permissions`, `relation_splits`, and `id_encoding` (all four
described below) are the identifier half, unchanged in shape by that framing; `identifier_notes`,
`caveat_renames`, `arrow_aliases`, `phase_status`, and `decisions` (also below) are the rest of it, carrying
what used to live only in `migration-plan.md`'s prose and tables.

The `types`/`permissions`/`relation_splits`/`id_encoding` shape below is also what
`migration_harness.idmap.IdMap.load()` parses. That module is a validation tool in the
plugin's source repository
([`authzed/authzed-marketplace`](https://github.com/authzed/authzed-marketplace), under
`tools/migration-harness/`) and is **not shipped with the plugin**; it is cited here
because it is the executable statement of this format, and its tests are what keep the
format honest. Nothing here requires running it. **`IdMap.load()` reads only those four
keys, by name, and ignores every other top-level key** (Python's `dict.get()` on a name it
never asks for) -- `identifier_notes`, `caveat_renames`, `arrow_aliases`, `phase_status`, and
`decisions` pass through it unnoticed, so adding them to this format required no change to the harness and
none of its tests reference them. They are real to every phase in this pipeline; they are
invisible to the harness by construction, not by omission.

One rule the harness enforces that a hand-written map easily violates: the mapping must be
**injective** within each namespace -- globally for `types`, and within one source type for
`permissions` **and** `relation_splits` together (they share one per-definition SpiceDB
namespace -- see `relation_splits` below). Two source names sharing one SpiceDB name
silently merges them. `arrow_aliases`' generated `alias_permission` names share that same
per-type namespace too (`migrate-schema.md` reserves them there for exactly this reason),
but the harness does not check it -- there is no assertion in `idmap.py` for a key it never
reads -- so the phase that writes `arrow_aliases` is solely responsible for verifying it
does not collide with `permissions` or `relation_splits` on the same type.

```json
{
  "types": {
    "<source type>": "<spicedb definition name>"
  },
  "permissions": {
    "<source type>": {
      "<source relation or permission name>": "<spicedb relation or permission name>"
    }
  },
  "relation_splits": {
    "<source type>": {
      "<source relation name>": {
        "relation": "<spicedb relation name -- the write target>",
        "permission": "<spicedb permission name -- the check target>"
      }
    }
  },
  "id_encoding": {
    "mode": "none",
    "types": ["<source type>"],
    "status": "clean | encoded | unresolved | unknown",
    "violations": [
      { "type": "<source type>", "example": "<an illegal id, or the construction that builds one>",
        "illegal_chars": "<the offending character(s)>" }
    ],
    "note": "<required when status is 'clean': which id-construction sites were read to establish it>"
  },
  "identifier_notes": {
    "types": { "<source type>": "<why this name is what it is -- collision, rename, reserved word>" },
    "permissions": {
      "<source type>": { "<source relation or permission name>": "<same, for one relation/permission>" }
    }
  },
  "caveat_renames": {
    "<source condition/caveat name>": "<spicedb caveat name>"
  },
  "arrow_aliases": {
    "<source type>": {
      "<relation>": {
        "alias_permission": "<generated name, e.g. '<relation>__perm'>",
        "arrow_sites": ["<definition>.<permission> -- the arrow expression(s) that reference this alias>"]
      }
    }
  },
  "phase_status": {
    "0": { "status": "complete (full gate)", "artifact": "" },
    "1": { "status": "pending", "artifact": "" },
    "2": { "status": "pending", "artifact": "" },
    "3": { "status": "pending", "artifact": "" },
    "4": { "status": "pending", "artifact": "" },
    "5": { "status": "pending", "artifact": "" }
  },
  "decisions": {
    "tenancy": {
      "decision": "<the option chosen, or 'single store, no tenancy decision required'>",
      "asked": true,
      "evidence": "<what the analyzer found>",
      "tenant_reachability_findings": [
        { "type": "<flagged source type>", "note": "<the Class C finding, in full>" }
      ]
    },
    "identifier_strategy": {
      "asked": true,
      "evidence": "<what triggered, or ruled out, the question -- id_encoding above carries the answer itself>"
    },
    "relation_split_naming": {
      "suffix": "__direct",
      "asked": false,
      "evidence": "<why -- e.g. 'pack default; no project-specific suffix requested'>"
    },
    "permission_naming_style": {
      "decision": "preserve",
      "asked": false,
      "evidence": "<what the analyzer's noun-shaped-permission-names report found, or why nothing was asked -- the renamed pairs themselves live in permissions[type] and identifier_notes, not here>"
    },
    "consistency_strategy": {
      "default": "literal-mapping",
      "asked": true,
      "evidence": "<what the analyzer found at independent-check call sites>"
    },
    "per_blocker_resolutions": [
      {
        "blocker": "<pack blocker-catalog name>",
        "site": "<file:line, or 'model'>",
        "rating": "clean | effort | heavy | blocked",
        "resolution": "<resolution text, naming the blockers.md item number -- null if unresolved>"
      }
    ],
    "additional": [
      { "key": "<short label, e.g. 'call_site_language'>", "value": "<the recorded answer>", "note": "<why it was asked>", "recorded_by": "<phase/command that recorded it>" }
    ]
  }
}
```

All top-level keys are optional; a missing or `null` key is treated as empty. `types`,
`permissions`, `relation_splits`, `identifier_notes`, `caveat_renames`, `arrow_aliases`,
`phase_status`, and `decisions` default to `{}`; `id_encoding` defaults to `{"mode": "none", "types": [], "status": "unknown", "violations": []}` -- note the default `status` is **`unknown`**, not `clean`, so a map that never had the question settled cannot be mistaken for one that settled it in the affirmative;
`decisions.per_blocker_resolutions` and `decisions.additional` default to `[]`. Every
`migration-map.json` written before `relation_splits` existed is missing that key outright,
and loads exactly as it always has -- the same is true of every key this revision adds, for
every map written before this revision.

### `types`

A flat map from source type name to SpiceDB definition name, produced by the pack's naming
normalization (`pack-contract.md` item 5). One global namespace -- SpiceDB definition names
share a single namespace, so every source type name must resolve to a distinct SpiceDB
name.

### `permissions`

A map keyed by **source type name**, whose value is a per-type map from source relation/
permission name to SpiceDB relation/permission name. This single per-type table is used to
translate names on *both* sides of a relationship: the resource-side permission or
relation being checked, and a subject-side relation reference (e.g. `group#member`,
where `group` is the type and `member` is the relation looked up in that type's entry).
That mirrors SpiceDB itself, which only requires relation/permission names to be unique
*within* one definition -- the same source name recurring on two different types is not a
collision and gets two independent entries.

For a relation the pack's schema mapping split (`schema-mapping.md`'s "The relation/permission split"), `permissions[type]` carries the split source name mapped to its
**permission** -- the unsuffixed name, since that is the name every check surface
(assertions, other permissions, arrow references) keeps using. It does not carry the
generated `__direct` relation; `relation_splits` (below) is where that lives.

**That unsuffixed name is the source's own identity by default, but is not always.** Where
`decisions.permission_naming_style` is `rename` (`schema-mapping.md`'s "Permission naming
style"), the value here is the recorded verb instead (`"owner": "own"` in place of the
identity `"owner": "owner"`) -- applied identically whether the source relation split or not.
Every read surface -- checks, arrows, subject-relation references -- resolves through this
same map either way, so a rename needs no separate handling anywhere that already looks up a
permission's name here.

### `relation_splits`

A map keyed by **source type name**, whose value is a per-type map from a *split* source
relation to both of the SpiceDB names it produced:

```json
{ "<source relation name>": { "relation": "<write target>", "permission": "<check target>" } }
```

A `define` that fuses a `[...]` type list with an operator splits into two SpiceDB names
(`schema-mapping.md`'s "The relation/permission split"): a generated `relation` (default
suffix `__direct`) that a relationship **write** must target, since SpiceDB rejects a write
to a permission outright, and a `permission` that keeps the source's own name (or its
renamed verb, per `decisions.permission_naming_style` -- see `permissions` above) and is
what every **check** -- an assertion, another permission, an arrow, a subject-relation
reference -- still uses. `permissions[type]` already carries the second of those two (the
check target); `relation_splits` exists because nothing else records the
first, and a data migration (phase 3) cannot rewrite a stored relationship's resource side
without it.

Naming both fields explicitly, rather than a bare string or a fixed-order pair, is
deliberate: `"member": "member__direct"` alone cannot say which side of a relationship it
governs, and guessing wrong is exactly the failure `schema-mapping.md` calls out --
misapplying the write-side name to a check silently narrows the answer with no error at all
(SpiceDB does not reject checking a bare relation directly), where misapplying the
check-side name to a write fails loudly instead. `{"relation": ..., "permission": ...}` makes
the two positions unambiguous at every call site, including a human reading the file.

Absent for a source type or relation that never split -- the overwhelmingly common case --
and absent entirely on any map with no splits at all, old or new. `IdMap.load` treats a
missing key the same way it treats a missing `permissions` or `types`: as `{}`, no error.

`IdMap.apply`, which rewrites **assertions**, never reads `relation_splits` -- it has no
reason to, since a check always wants the permission name `permissions[type]` already
supplies, and changing that would invalidate every assertion `apply` has ever produced,
including the parity evidence already recorded for the corpus. `IdMap.write_relation(source_type,
source_relation)` is the separate accessor for the write side: it returns the split's
`relation` when one is recorded, and otherwise falls back to the ordinary `permissions`
mapping, so an un-split relation still writes under the same name it is checked under.

### `id_encoding`

Controls object-ID rewriting, independent of the `types`/`permissions` name mangling above.

- **`mode`** -- `"none"` (object IDs pass through unchanged) or `"base64url"` (object IDs
  are base64url-encoded, landing entirely inside SpiceDB's object-ID character set with no
  further mangling needed and remaining reversible). Any other value is invalid and is
  rejected when the file is loaded.
- **`types`** -- the list of source type names whose object IDs should be encoded. A type
  not in this list passes its IDs through unchanged even when `mode` is `"base64url"`. The
  wildcard subject ID (`*`) is never encoded, regardless of type.
- **`status`** -- **required**, and the field that makes `mode` safe to read. One of:
  - `"clean"` -- every object ID that reaches SpiceDB already satisfies the charset, and this
    was established by **positive evidence**, not by an empty sweep. Say what the evidence was
    in `note`.
  - `"encoded"` -- violating IDs exist and `mode: "base64url"` resolves them. The emitted codec
    is the fix and no further human work is required.
  - `"unresolved"` -- **violating IDs exist and this pipeline is not fixing them.** The chosen
    strategy is one it cannot represent (encode only the violating values; a project-supplied
    mapping function; changing the application so it stops producing the illegal ID), or the
    decision has not been made yet.
  - `"unknown"` -- the question has not been settled. **This is the value to assume whenever
    `status` is absent**, including in a map written before this field existed. Never read a
    missing `status` as `"clean"`.
- **`note`** -- required when `status` is `"clean"`: which object-ID construction sites were
  read to establish it. A `clean` verdict with no `note` is not a verdict, because the one thing
  that makes `clean` trustworthy is having looked somewhere a file sweep cannot reach.
- **`violations`** -- required unless `status` is `"clean"`: a list of what was found, each with
  the source type, an example illegal ID (or the construction that produces it), and the
  offending character(s). This is what a later phase and a human reviewer act on.

**`mode: "none"` does not mean "the identifiers are fine."** It means "this pipeline will emit
no encoder," which is equally true when IDs are perfectly legal and when they are so illegal
that every check will fail. Those two states are opposite in consequence and identical in
`mode`, so **`status` is what separates them and every consumer must read it**:

- `mode: "none"` + `status: "clean"` -- nothing to do.
- `mode: "none"` + `status: "unresolved"` -- **the migration is not safe to complete.** SpiceDB
  rejects an out-of-charset object ID outright: a write fails client-side and a check fails
  server-side on the object-ID pattern, so the failure is a hard error on a live request path,
  not a silent denial. Phase 3 must not load data and phase 4 must not point converted code at
  SpiceDB until a human has supplied the encoder. Treat it as an unresolved Class A blocker
  for gating purposes, and render it under `### Needs action` with the `violations` list.

**A "clean" verdict needs evidence a file sweep cannot give you.** Scanning fixtures and
literals finds only IDs someone wrote down. An application that builds IDs at request time --
from an OIDC claim, a joined path, a `*` selector, or its own escaping helper -- puts nothing
in any file for a sweep to match, and those are exactly the IDs that reach SpiceDB. An empty
sweep is `"unknown"`, never `"clean"`; to record `"clean"`, read the code that constructs
object IDs and say in `note` which construction sites you read.

Encoding is not lossless-by-truncation: an empty ID, or (under `"base64url"`) an ID whose
encoded form would exceed SpiceDB's 1024-character object-ID limit, is a hard error rather
than a silent truncation -- truncation would break reversibility.

### `identifier_notes`

Free-text annotations on an entry already present in `types` or `permissions`, for the
`## Identifier map` rendering's `note` column -- **not a second source of names**, only a
reason attached to a name that already exists in one of those two maps. Two independent
sub-keys, `types` and `permissions`, mirroring the shape of the maps they annotate: `types`
is keyed by source type name, `permissions` by source type then source relation/permission
name. A note is typically one of: a collision this name was disambiguated to avoid ("`can-
edit` and `can_edit` both normalize to `can_edit`; this entry is the disambiguated form"), a
reserved-word rename, a length correction (SpiceDB's 3-64 character rule), or a
`decisions.permission_naming_style` rename ("noun → verb, gate decision: `owner` → `own`" --
or, for a no-defensible-verb name the user chose to leave rather than rename, "noun-shaped,
no defensible verb; left as-is per user choice," even though `permissions[type]` did not
change for that entry, because the choice itself is worth a reader seeing). An entry with
nothing to say about it has no key here at all -- the overwhelming majority of names -- and
the rendered `note` column is blank for it; `identifier_notes` is never padded with an entry
per name for symmetry.

### `caveat_renames`

A flat map from source condition/caveat name to the SpiceDB caveat name it was renamed to
(`{"<source name>": "<spicedb name>"}`), for the one naming surface `types`/`permissions`
does not cover: caveat names live in their own namespace, declared in `schema.zed` rather
than as a relation or permission. Phase 1 writes an entry here only when a source condition
name needed changing to satisfy SpiceDB's caveat-name grammar (`^[a-z][a-z0-9_]{1,62}[a-z0-9]$`,
stricter than a schema declaration's own lexing, per `data-mapping.md`'s ""Condition → caveat context" section");
absent entirely when every condition name in the model already conforms, which is the common
case. `/spicedb-dev:migrate-data` and `/spicedb-dev:migrate-tests` both consult this key when
rendering a `condition:` block's caveat suffix -- **never** the Markdown -- and halt, rather
than silently normalize, on a caveat name that fails the grammar with no matching entry here.

### `arrow_aliases`

A map keyed by **source type name**, whose value is a per-type map from a relation to the
generated permission alias that lets an arrow target it, plus where that alias is used:

```json
{ "<relation>": { "alias_permission": "<name>__perm", "arrow_sites": ["<definition>.<permission>", "..."] } }
```

SpiceDB rejects an arrow whose right-hand side names a bare relation rather than a
permission (the `arrow-references-relation` lint); wherever a source arrow's target
resolved to a relation instead of a permission, `/spicedb-dev:migrate-schema` generates a
same-target alias permission (default suffix `__perm`) purely so the arrow has a permission
to point at. `alias_permission` is the generated name, reserved in the same per-type
registry as `permissions` and `relation_splits` (see the injectivity note above).
`arrow_sites` lists every `<definition>.<permission>` whose arrow expression references this
alias -- one alias can be referenced by more than one arrow, so this is a list, not a single
site. **Unlike `relation_splits`, an alias renames nothing a source name maps to** -- it is
purely a schema-generation artifact with no data-side or code-side consumer: no tuple is
ever written or checked under an alias name, and neither `/spicedb-dev:migrate-data` nor
`/spicedb-dev:migrate-code` ever looks one up. Its only consumers are the `## Arrow aliases`
rendering and `/spicedb-dev:migrate-verify`'s sampling-weight heuristic (`differential-
harness.md` cites this key for that). Absent for a source type with no aliases, and absent
entirely on a map with none at all -- the common case.

### `phase_status`

A map keyed by phase number as a string (`"0"` through `"5"`), whose value is `{"status":
..., "artifact": ...}` -- the same `status`/`artifact` split, and the same closed
`status` vocabulary, `## Phase status` above defines for the Markdown rendering of this key.
Every phase's read of "is phase N complete" is a read of `phase_status["N"].status` against
that vocabulary, never of any Markdown table. A key missing for a given phase number reads
as `pending` (or, for phase 0, as not yet gated -- no phase before `/spicedb-dev:migrate`
has run), the same default the closed vocabulary already assigns to an absent or
unrecognized value.

### `decisions`

Every choice `/spicedb-dev:migrate`'s gate makes (or `/spicedb-dev:migrate-schema`'s reduced
gate, standalone), and every later phase's own recorded choice, in one object:

- **`tenancy`**, **`identifier_strategy`**, **`relation_split_naming`**,
  **`permission_naming_style`**, and **`consistency_strategy`** each carry `evidence` (why
  the question was or wasn't asked) and either `decision`/`suffix`/`default` (the answer) or
  `asked: false` with a reason in `evidence` when the question did not apply.
  `identifier_strategy` does not duplicate `id_encoding`'s `mode`/`types` -- those fields
  remain the single source for the actual encoding configuration; `decisions.
  identifier_strategy` carries only the narrative of why that configuration was (or wasn't)
  chosen. Likewise, `permission_naming_style` does not duplicate the renamed names themselves
  -- those live in `permissions[type]` and `identifier_notes.permissions[type]` (below), the
  same two keys any other permission rename already uses; this field carries only the
  `preserve`/`rename` choice and why. `tenancy.tenant_reachability_findings` is an
  array, `[]` when empty, of every Class C tenant-reachability finding (see `## Decisions`
  above); `None found` is recorded as an empty array plus a note in `evidence`, not as the
  key's absence, so a reader can tell "checked, found none" from "never checked."
- **`per_blocker_resolutions`** is an array, one entry per detected Class A site: `blocker`,
  `site` (`file:line`, or the literal string `"model"`), `rating`, and `resolution`.
  **`resolution: null` (or the key's absence) is the one and only representation of
  "unresolved"** -- every phase's unresolved-Class-A-finding halt is exactly the check
  "does any entry have a null/missing `resolution`," and no other field or convention marks
  it. A blocker requiring per-call-site resolution (OpenFGA's contextual tuples) gets one
  entry per site; a single resolved entry does not resolve a sibling site's own entry.
- **`additional`** is an array for a decision no dedicated key above anticipates -- a
  call-site language `/spicedb-dev:migrate-code` or `/spicedb-dev:migrate-verify` resolved
  because the plan was silent or ambiguous, a `listRelations` error-handling policy, a
  non-transactional-writes fork choice -- each `{"key": ..., "value": ..., "note": ...,
  "recorded_by": ...}`. This is the one place in `decisions` an entry is *appended* rather
  than filled into a fixed field, because the set of decisions a later phase might need to
  record is open-ended in a way the gate's own dedicated keys are not. `[]` when nothing has
  been appended.

### Two ways to build one

`IdMap` supports two constructors:

- **`load(path)`** -- reads a `migration-map.json` file as shown above and trusts it as
  already collision-free. This is what phases 3 and 5 use to consume a plan phase 1 already
  wrote.
- **`build(types, relations, ...)`** -- an alternate, registry-based constructor that takes
  raw source type names and a `{type: [relation names]}` map, and runs the same
  normalization and collision-resolution phase 1 uses to *produce* `migration-map.json` in
  the first place: type names are disambiguated against one global registry (matching the
  single SpiceDB definition namespace), and each type's relation/permission names are
  disambiguated against a registry scoped to that type alone. Both constructors produce the
  same `types`/`permissions`/`id_encoding` shape, so downstream code (data rewriting, test
  rewriting) works identically regardless of which one produced the `IdMap`.

`build()` does not populate `relation_splits` -- it disambiguates raw names, and whether a
`define` splits is a schema-conversion decision (which operators and type lists a source
`define` mixes) that this constructor has no visibility into. A caller with splits to record
adds the `relation_splits` key to the `migration-map.json` document itself (the normal path,
and the only one available to `/spicedb-dev:migrate-schema`, which has no Bash tool and so
never calls `build()` at all); `load()` is what then picks it up.

A pack or command generating `migration-map.json` by hand should still guarantee the same
collision-free property `build()` guarantees: never let two distinct source names collide
on the same SpiceDB name within a namespace -- now including a split's generated `relation`
name, or an alias's generated `alias_permission` name, colliding with another entry in that
same type's `permissions`, `relation_splits`, or `arrow_aliases`.
