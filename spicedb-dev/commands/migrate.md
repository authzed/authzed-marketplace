---
name: migrate
description: Migrate an application from another authorization system to SpiceDB - analyze, hold the pre-flight gate, and route through the conversion phases
argument-hint: "[project-dir] [output-dir]"
allowed-tools:
  - TaskCreate
  - TaskUpdate
  - Read
  - Write
  - Glob
  - Grep
  - Task
  - AskUserQuestion
---

# Migrate to SpiceDB

**Phase 0** of the migration pipeline, and the migration's front door: analyze the source
system, hold the **single pre-flight gate**, write the plan every later phase reads, and
route into phase 1.

This command decides nothing on its own and converts nothing. It launches the
`migration-analyzer` agent, presents everything that agent found, resolves it **with the
user in one batch**, and records the result. Batching is the point: the decisions interact
-- tenancy constrains the identifier strategy, which constrains the data rewrite, which
constrains every call site -- and a user asked one question at a time cannot see those
interactions.

Outputs, written to `[output-dir]` (default: `[project-dir]` -- the project being migrated, **not** the shell's current working directory, which would scatter this pipeline's state into an unrelated place and leave every later phase unable to find it):

- `migration-map.json` -- the machine-readable record of every decision, the identifier map,
  and phase status, in the shape `migrating-to-spicedb/references/findings-report.md`
  specifies. Every later phase reads and writes state here, and nowhere else.
- `migration-plan.md` -- a human-readable rendering of that same file, for review. No phase
  parses it back in.

The plan is a pure rendering of the map, regenerated every time a phase touches it -- see
`migrating-to-spicedb/references/findings-report.md`'s "Two groups of sections, one rule
each" for exactly which sections that covers.

**Exactly one gate runs per migration.** This command is it. `/spicedb-dev:migrate-schema`
holds a *reduced* inline gate only when it is run standalone with no map present; once
this command has written `migration-map.json`, that inline gate is skipped by its own step 1
and phase 1 reads the recorded state instead of re-asking.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each
task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Check for an existing plan

Check `[output-dir]` (default: `[project-dir]` -- the project being migrated, **not** the shell's current working directory, which would scatter this pipeline's state into an unrelated place and leave every later phase unable to find it) for `migration-map.json` -- the record
this whole pipeline reads and writes state to; `migration-plan.md`'s presence is not the
signal to check, since it is only ever a rendering of that file (`findings-report.md`'s
`## migration-plan.md` section).

**If `migration-map.json` already exists, check who wrote it before doing anything else.**
"Exactly one gate runs per migration" is a rule about **authorship**, not about the file's
existence, and a map that exists is not proof this command's gate ever ran. Read
`phase_status["0"].status` from it -- **not** `migration-plan.md`'s rendered `## Phase
status` table; no phase reads that table, per `findings-report.md`'s `## migration-plan.md`
section:

- **`complete (full gate)`** -- this command's own gate produced it. Do not re-run the gate.
  Summarize the recorded decisions and phase status, both read from `migration-map.json`'s
  `decisions` and `phase_status` keys, then ask what the user wants:
  - **Continue** -- go to step 8 and route into the next incomplete phase.
  - **Re-analyze** -- run step 2 for a fresh findings report, present it read-only, and
    change the plan only where the user asks. Never silently overwrite a recorded decision:
    once phase 1 has run, the schema and the identifier map were generated from it, and once
    phase 3 has run, stored data was written under it. Changing it after either is a
    deliberate edit with a data consequence, not a refresh.
- **`inline (reduced -- no codebase analysis)`** -- `/spicedb-dev:migrate-schema`'s reduced
  step-3b gate wrote it, run standalone with no plan present at the time. This plan was
  never authored by the full gate: nobody has swept the codebase, and the three Class A
  blockers invisible in the model (contextual tuples, model-ID pinning, multi-store tenancy)
  have never been checked against real call sites -- only against a targeted grep run
  without a project directory to scope it. Say this plainly, then run the full gate anyway:
  go to step 2, launch the analyzer, and proceed through step 4 (present) and step 5 (hold
  the gate) as if no plan existed. Carry forward every decision the reduced gate already
  recorded (tenancy, identifier strategy, relation-split naming, per-blocker resolutions,
  consistency strategy) as the answer offered back to the user rather than a blank question,
  unless the full sweep surfaces evidence that contradicts one -- a second store ID the
  model-only scan could not have seen, for example. Do not treat the file's mere presence as
  a reason to skip this; that is exactly the gap this check exists to close. Check
  `migration-map.json`'s `phase_status["1"]` too: the reduced gate's own step 3b runs straight
  into schema conversion in the same invocation, so `schema.zed` may already exist, generated
  from the reduced gate's decisions. If the full sweep changes any of those decisions --
  most likely a code-side Class A resolution the model-only scan never asked about -- say
  explicitly that the existing `schema.zed` was generated from the superseded decision and
  needs regenerating; do not leave the user assuming it is still current.

  **`schema.zed` is not the only thing this re-gate discards -- say so before writing
  anything.** Step 6 below rewrites `migration-map.json` to this gate's own output shape,
  unconditionally, and step 7 regenerates `migration-plan.md`'s rendering of it to match:
  every phase from 1 onward goes back to `pending` in `phase_status`,
  and the `relation_splits` and `arrow_aliases` keys -- **two keys, not the whole map** -- are
  dropped from the JSON entirely. `types`/`permissions`/`identifier_notes` are rebuilt fresh
  from this run's model inventory, not emptied, so **Identifier map** renders again with
  content the next time `migration-plan.md` is regenerated; **Relation splits** and **Arrow
  aliases** render as empty tables instead, because the two JSON keys behind them are now
  absent -- those two tables have no content of their own to overwrite; they show whatever the
  JSON currently holds. `decisions` and `phase_status` are rewritten fresh too, from this
  run's step 5 answers, not merged with whatever the superseded plan recorded -- a re-gate is
  a new gate, not a patch. Both dropped keys (`relation_splits`, `arrow_aliases`) are phase 1's
  findings, not this gate's, so this gate cannot regenerate them -- only
  `/spicedb-dev:migrate-schema` can. State plainly, before writing:
  - That **Relation splits** and **Arrow aliases** currently render with content (if they do)
    and are about to go empty -- **Identifier map** is not going empty, only regenerated.
  - That **phase 1 must be re-run** (`/spicedb-dev:migrate-schema`) before phase 3, 4, or 5
    can run at all: each of them consumes `relation_splits` (phase 3 for every tuple's
    resource side, phase 4 for every write and relationship filter, phase 5 for every
    rendered tuple), and a map without the key reads as "no relation split anywhere", which is
    silently wrong rather than an error.
  - **If phase 3 already ran**, that this is not self-healing. Relationship data is already
    loaded under the names the *old* map recorded. Re-running phase 1 after this re-gate
    regenerates the map, and if any decision changed -- relation-split suffix, identifier
    strategy, a name normalization -- the regenerated names will not match what is stored, and
    the store needs re-loading, not just re-mapping. Say which phases have already run, read
    from `migration-map.json`'s `phase_status`, before the user agrees to the re-gate.

  If the user would rather not lose that state, the alternative is to leave this plan alone
  and run the phases forward from where they are -- at the cost of a plan whose codebase was
  never swept, which is the risk this whole branch exists to surface. That is the user's call
  to make with the consequences in front of them, not a default to pick silently either way.
- **Anything else** (missing, malformed, or an older plan with no phase-0 status recorded)
  -- treat it the same as `inline (reduced -- no codebase analysis)`: the full gate has not
  demonstrably run, so run it.

**If any Class A finding in the existing plan has no recorded resolution, that is also a
case where you resume into the gate.** Present those blockers with their option lists and
resolve them, then continue. A **bulk** resolution does not count as a recorded resolution
here: step 5 row 6 (per-blocker resolutions) requires contextual tuples to be resolved **per
call site**, so a plan recording one resolution for "contextual tuples" as a class, rather
than one per `file:line`, still has every call site unresolved except the one actually
reasoned about. **The sites live in `migration-map.json`'s `decisions.per_blocker_resolutions`,
one array entry per site** (`findings-report.md`) -- read that array, never the rendered
`## Decisions → ### Per-blocker resolutions` table (no command parses `migration-plan.md`).
Check that every entry has a non-null `resolution`, not that the array is merely non-empty. A
map written before that key existed carries no `per_blocker_resolutions` at all -- treat a
missing key the same as every entry being unresolved, and write the array (from whatever the
plan's rendered table shows, if this is an old plan with no JSON copy of it yet) the next time
you touch this file.

### Step 2: Launch the analyzer

```
Task(
    subagent_type="spicedb-dev:migration-analyzer",
    description="Analyze migration source",
    prompt="Run phase 0 of the migration to SpiceDB against the project at [project-dir].
            Detect the source system and confirm a conversion pack exists, read the
            complete authorization model, run the pack's scoping questionnaire, and sweep
            the whole codebase for every Class A blocker in the pack's catalog -- the
            contextual-tuple sweep especially. Before recording any code-side zero, follow
            your own step 4 instructions to establish whether [project-dir] actually
            contains application source in the detected SDK's language (dependency
            manifest, source files, a matched SDK import), and classify each code-side
            sweep as 'swept, none found', 'swept, but vacuous', or 'not swept' -- a bare
            zero with no application code behind it is not acceptable. Return the
            structured findings report, including that classification in both the
            'Swept and not found' section and 'Confidence and gaps'."
)
```

The analyzer reads the model and greps the entire repository. **Do not repeat its work**:
do not read the model yourself and do not re-run its sweeps. Its report is your input, and
keeping that scan out of this context is the reason it is an agent.

Do use `Read` and `Grep` for narrow, targeted follow-up on a specific finding the user
questions -- a single `file:line` the user wants to see before deciding is exactly the
right use of it.

**If the agent cannot be launched** (the runtime returns an error such as "Agent type
'migration-analyzer' not found", under either the prefixed or unprefixed form) -- say so
plainly, then fall back **in this order**, rather than treating it as a hard stop:

1. **Re-dispatch as a generic agent carrying this agent's own instructions**:
   `Task(subagent_type="general-purpose", ...)` with a prompt that tells it to read
   `agents/migration-analyzer.md` in this plugin and follow it exactly, plus the same
   `[project-dir]` and prompt body prepared above. **Prefer this.** **Confirm the file is
   actually readable before relying on it** -- an installed copy old enough to lack the
   agent's registration usually lacks its definition file too, and the two failures look
   nothing alike from here: the registry error names a missing agent, while a missing file
   just makes this rung silently useless. If it is not readable at the plugin path you are
   running from, say so and go to rung 2. The registry lookup is
   what failed, not the instructions -- they are a file on disk, and a generic agent can read
   them. This keeps the scan's output out of this context, which is the entire reason step 2
   dispatches an agent at all.
2. **Only if that also fails**, run the analysis inline, in this same context. Say plainly
   that you are doing so and that it consumes context the pipeline would rather spend on
   later phases. Expect this to be expensive in a way option 1 is not: the sweeps read whole
   model files, and an embedded-in-source model can be a single multi-kilobyte line that
   lands in context in full.

This is a real, encountered failure mode, not a hypothetical one: an installed copy of this plugin can predate this agent shipping, so the
runtime's agent registry simply does not have it wired in even though the agent's own
definition file (`agents/migration-analyzer.md`) is present in the plugin's source. This
step is the first substantive action of the very first command in the pipeline, so a hard
stop here blocks all five phases before a single question is asked or a single file is
written -- more than should be lost to what is underneath a missing-registration problem,
not a missing-capability one.

**The fallback, and its cost, stated plainly before running it:**

1. Tell the user the `migration-analyzer` agent could not be launched (name the exact
   error), so phase 0's analysis is running inline instead of as an agent. **State the
   context cost, not just the workaround**: this means the model's full text, every sweep's
   raw matches, and the codebase inventory all enter this orchestrator's own context -- the
   agent exists specifically to keep that out of here, not because the analysis is
   otherwise impossible. On a large repository this is a real, material cost to this
   session's context budget, not a formality to disclose and move past.
2. Read `agents/migration-analyzer.md` **in full** and follow it exactly as written, step by
   step, in this context -- it is the complete, authoritative definition of what phase 0's
   analysis does (source detection, model extraction across every form the pack lists, the
   scoping questionnaire, every Class A/B/C sweep). Do not improvise a shorter version of
   it and do not skip a sweep because it seems unlikely to fire -- the fallback's job is to
   reproduce the agent's documented behavior faithfully, not to approximate it under time
   pressure.
3. Produce the exact same structured findings report that file's step 7 specifies, and
   proceed into step 3 below with it, exactly as if the agent had returned it.
4. Record, in `migration-map.json`'s `decisions.additional` once step 6 writes it, that
   phase 0's analysis ran via a substitute path rather than the `migration-analyzer` agent,
   **naming which rung was used** -- a later reader should be able to tell this plan's phase-0
   analysis did not come from the agent, and which substitute produced it, the same way the
   reduced-gate authorship marker already lets a reader tell a full gate from a reduced one.
   **This applies to both rungs, not only the inline one**: a `general-purpose` agent carrying
   the analyzer's instructions is equally a substitute path, and equally worth a reader
   knowing about.

Prefer the real `Task` call whenever it succeeds -- this fallback exists to keep the
pipeline usable when the agent is genuinely unavailable, not because inline analysis is an
equally good default.

### Step 3: Confirm a pack exists -- or stop

If the analyzer reports no pack for the detected source, **stop here**. Tell the user what
was detected, what evidence pointed at it, and which packs exist (`migrating-to-spicedb`'s
source registry). Do not improvise a conversion, do not fall back to another source's rules,
and do not write any file. An unsupported source needs a new pack.

If the analyzer could not tell which of two supported sources is in use, ask that first,
before anything else in step 5 -- every subsequent rule comes from the pack.

### Step 4: Present the findings -- all of them, grouped, before asking anything

Write the full picture into the conversation first. The user has to see the whole surface
before answering the first question, or the batch is a batch in name only.

Lead with the pack's **scoping numbers** -- they are what predict the cost -- then:

- **Class A -- hard blockers.** One block per finding: what it is, where it was detected
  (`file:line`), its fidelity rating **and the evidence for that rating**, and the pack
  catalog's **complete** option list with its costs. Say plainly that nothing is written
  until every one of these is resolved.
- **Class B -- normalization decisions.** Mechanical, but they change stored data. Show the
  identifier collisions as *pairs* (never a pre-picked winner), the names that violate
  SpiceDB's 3-64 character rule, the **types** whose object IDs fall outside SpiceDB's
  charset with representative values, the relation-split count, and the noun-shaped
  permission names (post-split), split into the ones with a defensible verb and the ones
  without.
- **Class C -- advisory.** Recorded, never halts, never asked about. Say so explicitly so
  the user does not read the list as more work to resolve now. **Include the
  `LookupResources` product regressions here whenever the analyzer found `listObjects` /
  `streamedListObjects` call sites**, with their count: no total count (so a "Showing 1-20 of
  150" pager cannot be built on it), duplicate resource IDs that must be deduplicated
  client-side whether or not the caller paginates, and a hard 1,000-per-call cap. These are
  product decisions, not conversion defects -- a user who first sees them at phase 4, with
  the schema and data already migrated, has lost the chance to design around them.
- **Swept and not found.** Show these. "Zero contextual tuples, swept with this command" is
  a load-bearing statement about the migration's shape, and it is the difference between
  "none" and "nobody looked".
- **Confidence and gaps.** Show this section in full, every time -- it is not agent color
  and it is not optional reading. In particular, check whether the analyzer used it to flag
  its own sweep as **vacuous** -- the directory it walked contained no application code at
  all. If so, say that plainly, before the scoping numbers are allowed to stand as findings:
  a code-side zero (contextual tuples, model-ID pinning, store IDs) produced by a vacuous
  sweep is not "swept, none found" -- it is "nobody looked at the real application" -- and
  step 5 resolves which one it is before anything else is asked.

Read the pack's blocker catalog yourself before presenting an option list
(`openfga-to-spicedb/references/blockers.md` for OpenFGA). The analyzer relays the options;
the catalog carries the cost of each one, and the cost is what the user is actually
choosing between.

### Step 5: Hold the gate -- resolve everything in one batch

Use AskUserQuestion. Ask in **as few calls as the tool allows**, and in this order, because
each answer constrains the ones after it:

| # | Decision | Ask only when | Options |
|---|---|---|---|
| 1 | **Scan scope** | The analyzer's **Confidence and gaps** section flags its own sweep as vacuous -- the directory it walked contains no application code at all | Give the correct application directory and re-run step 2's sweep there, then re-present step 4 before asking anything else · confirm the swept directory is correct as-is and record every code-side Class A zero as swept-but-vacuous, not confirmed-absent. Ask this **first**: every code-side blocker below (contextual tuples, model-ID pinning, multi-store tenancy) is unconfirmed until the sweep is known to have covered real application code. |
| 2 | **Tenancy** | The analyzer found more than one store ID, or store CRUD calls | **does not apply -- single store** (the right answer when detection fired only on test scaffolding, fixture bootstraps, or a CI job spawning a throwaway server; record the sites and why each is scaffolding) · N separate SpiceDB deployments (true isolation) · one instance with a `tenant` resource type (idiomatic) · definition prefixes per tenant (only when models genuinely differ per tenant). **When to ask:** more than one store ID, or store CRUD sites you have read and judged to be real tenant provisioning. **When not to ask:** exactly one store and every store-CRUD hit is scaffolding or a single-store bootstrap -- record "single store, no tenancy decision required" with the per-site evidence, which is the `does not apply` option resolved without a question rather than a skipped question. Store CRUD alone never forces the question; what forces it is store CRUD that survives reading. |
| 3 | **Identifier strategy** (offer all of `naming-normalization.md`'s options, not only the two `id_encoding.mode` can store -- options 2 and 3 are recorded as `none` plus a Deferred/manual item, per that file; presenting only the storable two silently removes the answer that is often right) | Any object ID falls outside `^[a-zA-Z0-9/_\|\-=+]{1,1024}$` -- an `@` in an email subject ID is the common case | `none` (already legal -- the default; do not ask if nothing is out of range) · `base64url`, applied **per type**, since it rewrites every stored ID for the types named. Ask which types. |
| 4 | **Relation-split naming** | The model has at least one `define` fusing a `[...]` type list with an operator | `__direct` (the pack default) · a project-specific suffix. The permission keeps the original name either way. |
| 5 | **Permission naming style** | The analyzer's Class B findings report at least one noun-shaped permission name (post-split) | **Preserve source names (default)** -- nothing renames; call sites, stored data, dashboards, and any external consumer keep working unchanged, at the cost of a migrated schema that does not follow SpiceDB's own noun/verb convention (`spicedb-schema-design/references/anti-patterns.md`, "Confusing Relations with Permissions") · **Rename nouns to verbs** -- apply the pack's fixed table (`owner`→`own`, `viewer`→`view`, `editor`→`edit`, `reader`→`read`, `writer`→`write`; `schema-mapping.md`'s "Permission naming style") to every matching name; for every other noun-shaped name the analyzer reported with no defensible verb, ask individually whether to supply a custom verb or leave it as a documented exception -- never invent one to force the rule. The cost of renaming: it changes a name an application may check by string, and the migration only rewrites the call sites and data it converts -- a dashboard, an audit log, or another service outside its reach breaks silently. |
| 6 | **Per-blocker resolutions** | Each Class A finding the analyzer reported | That blocker's own option list from the pack catalog, **verbatim and complete**. Contextual tuples are resolved **per call site**, not in bulk -- one call site may be `effort` and the next `blocked`. |
| 7 | **Consistency strategy** | The analyzer found consistency preferences in use at call sites -- and this question covers only the **independent**-check default; it never covers a **dependent** (read-after-write) call site, see Options | Independent checks (no preceding write feeds the answer): literal mapping (`HIGHER_CONSISTENCY` → `full()`, `MINIMIZE_LATENCY`/unspecified → `minLatency()`) · thread revisions through the app anyway, for freshness beyond what the source ever had. If nothing was found, record "independent checks: `minLatency()` when code is rewritten" and do not ask. **Every dependent (read-after-write) check site follows `code-mapping.md`'s "Consistency" section's three-step rule regardless of this answer** -- thread the ZedToken, else `full()` plus a `TODO(spicedbmigration):` marker, never `minLatency()` -- so `/spicedb-dev:migrate-code` applies it unconditionally, not as an outcome of this question. |

**Rules for the option lists:**

- **Verbatim and complete.** Do not summarize a catalog's options, do not drop the one it
  marks as the leading candidate, and do not add `abort` to a blocker whose catalog does not
  offer it (in the OpenFGA catalog, only the transitive wildcard does).
- **When a catalog has more options than the picker will carry**, list every option with its
  cost in the message body first, put the catalog's leading candidates in the picker, and
  say explicitly that the remaining ones can be chosen by typing them.
- **Ask questions you know the answer to only when the answer is a decision.** A question
  whose answer is already determined by the model is not a decision; record it.
- **Record the questions you did not ask**, as explicitly not-applicable with the reason. A
  later phase reading `migration-map.json`'s `decisions` cannot otherwise tell "single
  tenant, decided" from "nobody looked".

**A blocker the user declines to resolve is still a halt.** Step 6 records that site's
`decisions.per_blocker_resolutions` entry with `resolution: null` (or leaves it absent) --
**not** a Markdown `UNRESOLVED` label; `resolution: null`/absent is the one and only
representation of "unresolved" (`findings-report.md`'s `decisions.per_blocker_resolutions`
section). Do **not** route into phase 1 (step 8), and say which decision is outstanding.
`/spicedb-dev:migrate-schema`'s step 1 halts on exactly that field, so the JSON -- not a
label in the rendered plan -- is what keeps the halt in force across sessions.

### Step 6: Write `migration-map.json`

Write this **before** `migration-plan.md` (step 7) -- the plan is a rendering of this file
(`findings-report.md`'s `## migration-plan.md` section), so the JSON has to exist first. This
is the file `migration_harness.idmap.IdMap.load()` parses, and the file
`/spicedb-dev:migrate-data` (phase 3) and `/spicedb-dev:migrate-tests` (phase 5) apply to data
and tests -- and, as of this pack's revision, the single record of every decision this gate
just made, per `findings-report.md`'s `## migration-map.json` section:

```json
{
  "types": { "<source type>": "<spicedb definition>" },
  "permissions": {
    "<source type>": { "<source relation or permission>": "<spicedb name>" }
  },
  "identifier_notes": {
    "types": {}, "permissions": {}
  },
  "id_encoding": { "mode": "none", "types": [], "status": "unknown", "violations": [] },
  "phase_status": {
    "0": { "status": "complete (full gate)", "artifact": "" },
    "1": { "status": "pending", "artifact": "" },
    "2": { "status": "pending", "artifact": "" },
    "3": { "status": "pending", "artifact": "" },
    "4": { "status": "pending", "artifact": "" },
    "5": { "status": "pending", "artifact": "" }
  },
  "decisions": {
    "tenancy": { "decision": "...", "asked": true, "evidence": "...", "tenant_reachability_findings": [] },
    "identifier_strategy": { "asked": true, "evidence": "..." },
    "relation_split_naming": { "suffix": "__direct", "asked": false, "evidence": "..." },
    "permission_naming_style": { "decision": "preserve", "asked": false, "evidence": "..." },
    "consistency_strategy": { "default": "literal-mapping", "asked": true, "evidence": "..." },
    "per_blocker_resolutions": [],
    "additional": []
  }
}
```

**On a re-gate, this step drops the existing `relation_splits` and `arrow_aliases` keys** --
it rewrites `types`/`permissions`/`identifier_notes`/`id_encoding` fresh, from this run's
model inventory, and resets `phase_status["1"]` through `["5"]` to `pending`. Both keys are
phase 1's own findings; only `/spicedb-dev:migrate-schema` can regenerate them, so phases 3,
4, and 5 are all blocked until it re-runs -- step 1's `inline (reduced ...)` branch has what
to tell the user before that happens.

Leave out `relation_splits` and `arrow_aliases` here -- do not write either, even as `{}`.
Which `define`s split, and which arrows need an alias, is discovered only by walking the
model to emit `.zed`, and this gate does not do that; `IdMap.load` treats `relation_splits`'
absence as `{}` (and every reader in this pipeline treats `arrow_aliases`'s absence the same
way), so leaving them out is not a gap, it is this phase correctly reporting what it does not
yet know. `/spicedb-dev:migrate-schema` (phase 1) is what adds them, once the walk finds the
splits and aliases -- see that command's own step 4.

Build `types`/`permissions`/`identifier_notes`/`id_encoding` from the analyzer's **Model
inventory** and the pack's naming normalization:

1. **Type names against one global registry** -- SpiceDB definition names share a single
   namespace. Deduplicate the source list first, preserving first occurrence.
2. **Relation and permission names against a per-type registry, never a global one.** Two
   definitions may each have a `viewer`; that is not a collision, and disambiguating it
   corrupts both.
3. **Apply `decisions.permission_naming_style`'s decision, after normalization, to every
   name the analyzer's noun-shaped-permission-names report flagged.** `preserve`: do nothing
   further. `rename`: replace the normalized value with its recorded verb -- the pack's fixed
   table for a name with a defensible verb (`owner`→`own`, `viewer`→`view`, `editor`→`edit`,
   `reader`→`read`, `writer`→`write`; `schema-mapping.md`'s "Permission naming style") or the
   user's own supplied verb for a name from the no-defensible-verb list they chose to name
   individually; a name they chose to leave keeps its noun. **Relations are never touched by
   this** -- the analyzer's report already excludes a `define` with only a type list (it
   stays a plain relation, correctly a noun already); this rule only ever rewrites an entry
   that will become a SpiceDB permission. Feed a renamed value through the same per-type
   registry as rule 2, so a rename that collides with another name on the same type is still
   caught by rule 7 below. `decisions.permission_naming_style` itself carries only the
   `preserve`/`rename` choice and why -- the renamed pairs and the no-defensible-verb list are
   not duplicated there; they live in `permissions[type]` and `identifier_notes.permissions
   [type]` (rule 6 below), the same two keys any other permission rename already uses.
   Unlike a relation split, which names are eligible needs no model walk, only the analyzer's
   own noun-shaped-permission-names report, so there is nothing `decisions.
   permission_naming_style` would gain by duplicating what those two keys already carry
   (`findings-report.md`'s `decisions.permission_naming_style` section states why).
4. **Include an entry for every source relation and permission, including the ones whose
   name did not change.** A missing entry silently passes the source name through, and
   `/spicedb-dev:migrate-data` (phase 3) and `/spicedb-dev:migrate-tests` (phase 5) both
   consume this map by lookup, with no fallback for an absent entry. Include a type's empty
   map (`"user": {}`) rather than omitting the type.
5. **`id_encoding.mode`** is `"none"` or `"base64url"`, taken from step 5's identifier
   decision -- not chosen here. Any other value is rejected at load. **`id_encoding.status` is
   required and is not derivable from `mode`**: record `clean` only on positive evidence that
   every ID reaching SpiceDB is in-charset (say which construction sites you read, in `note`),
   `encoded` when `base64url` resolves the violations, and `unresolved` when violations exist
   but the chosen strategy is one this pipeline does not implement -- options 2 and 3 of
   `naming-normalization.md`'s identifier gate both land here, as does "change the application
   so it stops producing the illegal ID." An empty sweep is `unknown`, never `clean`.
   `id_encoding.types`
   lists the source types whose object IDs are encoded; a type not in that list passes its
   IDs through unchanged even when the mode is `"base64url"`.
6. **Record every collision, rename, and reserved-word substitution** rules 1-3 produced as an
   `identifier_notes` entry (`findings-report.md`'s shape) -- this is what gives the rendered
   `## Identifier map`'s `note` column something to show; an entry with nothing to say about it
   gets no key here.
7. **Verify the finished map is injective and halt if it is not** -- within `types`
   globally, and within each source type's `permissions` entry. `IdMap.load` rejects a
   non-injective map, and for good reason: two source names sharing one SpiceDB name merges
   them, every check and tuple naming either one rewrites to the same target, and the
   migration looks clean while one of the source's permissions has quietly ceased to exist.
   Report both source names; never pick a winner.

Populate `phase_status` and `decisions` from step 5's gate:

- **`phase_status`** -- phase 0 `complete (full gate)`, phases 1-5 all `pending`. Do not write
  `not implemented` anywhere in a plan this gate writes; that value is a legacy marker from
  before `/spicedb-dev:migrate-code` existed (`findings-report.md`'s `## Phase status`
  section), not a status any phase writes now.
- **`decisions.tenancy`** / **`.identifier_strategy`** / **`.relation_split_naming`** /
  **`.permission_naming_style`** / **`.consistency_strategy`** -- one entry each, per
  `findings-report.md`'s shape: the decision (or `asked: false` with a reason, for a question
  step 5 determined did not apply). `decisions.tenancy.tenant_reachability_findings` carries
  every Class C tenant-reachability finding the analyzer flagged, immediately alongside the
  tenancy decision -- Class C never halts the gate, but it is still a required field here, not
  an optional one. Per flagged type, state plainly: it is isolated by write-path discipline
  only, not by the schema, in **both** the source and the target; adding a tenant edge is
  optional hardening outside a parity-preserving migration, and if the team wants it, record
  that separately under **Deferred / manual** with the schema location.
- **`decisions.per_blocker_resolutions`** -- one array entry per detected Class A site, not
  one per blocker: `{blocker, site, rating, resolution}`, `site` as `file:line` (or the
  literal string `"model"` for a model-only blocker like the transitive wildcard). This array
  is the map's only durable record of *where* each Class A finding was found -- the
  analyzer's report is not written to disk -- so `/spicedb-dev:migrate-schema` and
  `/spicedb-dev:migrate-code` both read it to decide whether a blocker is genuinely resolved
  everywhere it fired. A blocker the pack requires resolved **per call site** (contextual
  tuples) with one entry covering five sites is four unresolved sites, recorded as if it were
  none. Name the `blockers.md` item resolved by number in `resolution` -- "materialized as a
  relationship" alone does not let a reader find the rule that offered it
  (`findings-report.md`'s "Inline markers" required-reference rule).

`relation_splits` and `arrow_aliases` are absent, per the note above; phase 1 re-reads this
file, adds the generated names it produces (`<name>__direct` split relations, `<name>__perm`
arrow aliases) to the same per-type registries, and writes both keys back in. **Every entry
written here must survive that unchanged** -- a name this gate recorded is a decision, not a
draft.

### Step 7: Write `migration-plan.md`

Write it to `[output-dir]`, **after** step 6's `migration-map.json`, since most of this file
is a rendering of it. Layout, from `migrating-to-spicedb/references/findings-report.md` --
every heading below is required, including the ones that are empty or minimal at this point:

```markdown
# Migration Plan: <source> → SpiceDB

> Rendered from `migration-map.json`. Editing this file changes nothing a command reads --
> see `findings-report.md`'s `## migration-plan.md` section.

## At a glance
- **Pipeline:** phases <numbers> of 5 complete -- next: <phase name, or "nothing left to automate">
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
*(rendered from migration-map.json's `decisions` key)*
### Tenancy
### Identifier strategy
### Relation-split naming
### Permission naming style
### Consistency strategy
### Per-blocker resolutions
| blocker | site (file:line, or "model") | rating | resolution |
|---|---|---|---|

## Source
system, version, model location(s) and form, store count, SDK(s) and versions detected,
client shape(s) in use

## Scan scope
directory(ies) the analyzer swept, whether they contain application code, and the resolved
status of every code-side Class A sweep

## Target
SpiceDB version floor (v1.52.0+), endpoint, client language(s)

## Identifier map
*(rendered from `types`/`permissions`/`identifier_notes` -- populated already: this is
phase 0's own algorithm, not phase 1's)*
| source name | spicedb name | kind | note |
|---|---|---|---|

## Relation splits
*(rendered from `relation_splits` -- empty at this point; the key is absent until phase 1
walks the model)*
| definition | source relation | spicedb relation | spicedb permission |
|---|---|---|---|

## Arrow aliases
*(rendered from `arrow_aliases` -- empty at this point; the key is absent until phase 1
walks the model)*
| definition | relation | alias permission | arrow site(s) |
|---|---|---|---|

## Sync obligations
| obligation | source | write path | backfill | reconciliation |
|---|---|---|---|---|

## Deferred / manual
items requiring human work, and items recorded for the reader's awareness only. One entry per
finding, in `findings-report.md`'s "Inline markers" required-reference shape (site
file:line(s), governing rule, candidate mapping with its verified/inferred tag, pack gap,
marker back-reference), under `### Needs action` or `### For the record` per that file's
mechanical marker-type rule.
### Needs action
### For the record

## Phase status
*(rendered from migration-map.json's `phase_status` key)*
| phase | status | artifact |
|---|---|---|
```

Filling rules:

- **`## At a glance`, `## Needs your attention`, `## Decisions`, `## Identifier map`,
  `## Relation splits`, `## Arrow aliases`, and `## Phase status`** are generated directly
  from the `migration-map.json` step 6 just wrote -- there is nothing to author here beyond
  running the render. **`## Identifier map` renders non-empty**: `types` and `permissions`
  are populated by step 6's own algorithm, so this table carries rows from the first write
  onward. **`## Relation splits` and `## Arrow aliases` render empty** (heading, header row,
  separator, nothing else) -- not a special case to remember, simply a consequence of
  `relation_splits` and `arrow_aliases` being absent from the JSON step 6 wrote: which
  `define`s split, and which arrow targets need a generated alias, are found only by walking
  the model to emit `.zed`, and this gate does not do that. A header row with no separator is
  a malformed table, not an empty one. **On a re-gate over a plan phase 1 already filled,
  step 6 already dropped `relation_splits`/`arrow_aliases` from the JSON, so those two
  renderings go back to empty as a direct consequence -- `## Identifier map` does not, since
  `types`/`permissions` were rebuilt, not dropped** -- step 1's `inline (reduced ...)` branch
  states exactly what to tell the user before step 6 writes that drop.
- **`## At a glance` and `## Needs your attention` are synthesized, not separately
  composed.** Count the rows this step is about to write into `## Needs your attention`,
  `## Deferred / manual`, and `## Sync obligations`, and use those counts to fill in
  `## At a glance`'s bullets. `Needs your attention -> Unresolved blockers` is the subset of
  `decisions.per_blocker_resolutions` (step 6) whose `resolution` is null, rendered with each
  site's option list; `Needs action` is the `## Deferred / manual -> ### Needs action`
  subsection's own rows, repeated here so a reviewer sees every kind of open work without
  scrolling past the resolved-history tables first. Omit a `## Needs your attention`
  subsection entirely when its count is zero, rather than writing an empty table; if both
  subsections would be empty, keep the `## Needs your attention` heading with a single line:
  `Nothing needs your attention -- N resolved blockers, 0 open items.` -- never omit the
  heading itself.
- **Scan scope** records the directory the analyzer actually swept and the answer to step
  5's Scan-scope question: whether that directory contained application code. State each
  code-side Class A sweep (contextual tuples, model-ID pinning, store IDs) as exactly one
  of three things -- **swept, none found** (the directory holds real application code and
  the sweep genuinely found nothing), **swept, but vacuous** (the directory held no
  application code, so the zero is unconfirmed -- record the corrected directory and the
  re-swept result here if step 5 resolved it), or **not swept** (should not occur once phase
  0 has run to completion; if it does, say why). A later phase reading the plan cannot tell
  "no contextual tuples" from "nobody looked at the real application" without this section.
  This section has no `migration-map.json` counterpart -- write it directly, as narrative
  Markdown, the same as every earlier revision of this command.
- **Sync obligations** lists every resolution that creates permanent write-path work rather
  than one-time migration work -- materializing a contextual tuple as a real relationship,
  re-modelling one as caveat context, or enumerating subjects in place of a wildcard. Each
  needs a write path on every mutation, a backfill, a reconciliation job, and it opens a
  fail-closed window between the source-of-truth write and the SpiceDB write. **State the
  count**: it is what separates a migration from an ongoing synchronization project. Write
  `None.` if there are none -- that is also a finding. This gate **owns** the section and is
  the only phase that creates it; phase 3 (`/spicedb-dev:migrate-data`) later revises the
  same section in place once every Class A resolution is recorded, and its derivation wins
  where the two disagree (`findings-report.md`'s **`## Sync obligations`** section states the
  rule). Write the count here as the gate's estimate, not as the final number. This section
  has no `migration-map.json` counterpart either -- narrative Markdown, written directly.
- **`## Deferred / manual`** carries the Class C advisories that are human work, split
  mechanically between `### Needs action` and `### For the record` by what the entry asks of
  the reader (`findings-report.md`'s "Needs action vs. for the record") -- at phase 0 no
  `TODO`/`NOTE` marker exists yet in any code, since nothing has been converted, so the split
  is a judgment call about the finding itself, applied consistently:
  - **`### For the record`** -- call sites with no conversion target -- **four** constructs,
    not three: store CRUD, AuthZEN, Okta's Permissions Index, and
    `readAssertions`/`writeAssertions` (the OpenFGA pack's `code-mapping.md` lists six
    no-target operations in total; the other two, contextual tuples and model-ID pinning,
    have blocker-catalog entries and are Class A, resolved above under `## Decisions ->
    Per-blocker resolutions` instead) -- plus `list_objects` **and `list_users`** assertions -- count them together, since naming only `list_objects` undercounts this blind spot by its larger half; a `validation:` block expresses a resolution path, not an expected subject set, so it carries neither -- with no
    validation-YAML equivalent. These are purely descriptive facts about the source; nothing
    further is asked of the reader.
  - **`### Needs action`** -- source-model patterns the source system's own roadmap will
    reject, any model-ID-pinning rollout plan, and the **`LookupResources` product
    regressions** (below). Each of these asks the reader to make a design decision later, not
    only to know a fact.
  Each entry, in either subsection, follows `findings-report.md`'s "Inline markers"
  required-reference shape: `file:line`, the governing pack rule by file and section, and a
  candidate mapping's verified/inferred tag when one exists.
- **`## Deferred / manual -> ### Needs action`** also carries the **`LookupResources` product
  regressions**, whenever the analyzer found any `listObjects` / `streamedListObjects` call
  site. These are not code defects and phase 4 cannot fix them; they are product-level
  differences the user has to see at the gate, while the migration is still being scoped,
  rather than at phase 4 when the rewrite is already underway
  (`spicedb-client-integration/references/core-concepts.md`, "Product-level limits of
  `LookupResources`"). State all three, with the call sites they affect: **no total count**
  exists, so a "Showing 1-20 of 150" pager is not implementable as written; **duplicate
  resource IDs** must be deduplicated client-side whenever a resource is reachable through
  more than one relation feeding the permission -- **mostly across cursor pages** (240 of 241
  measured), but not exclusively, so dedup is required whether or not the caller paginates.
  Note that a quick single-call probe will very likely *not* reproduce a duplicate and is not
  evidence that dedup can be skipped -- `core-concepts.md` carries the measurement; and a **per-call server cap**
  (`MaxLookupResourcesLimit`, rejected server-side above it). **Check the target client
  before reporting that third one as a regression** -- it is a limit on one RPC, not on the
  operation, and a client that requests a page and follows the cursor hides it completely.
  **No client is vendored yet at this phase, so do not assert which way it falls here**:
  record the cap as *client-dependent, unconfirmed*, and let `/spicedb-dev:migrate-code` settle
  it once the client exists in the project -- a client that requests a bounded page and follows
  the result cursor hides the cap completely, and reporting it as a regression then sends the
  user to change code that is already correct. The first two hold regardless of client. Say
  which of the three apply for *this* project's language, and on what evidence. If there are no such call
  sites, record `None.` under `### For the record` instead, with the swept evidence -- the
  same "swept, none found" discipline the Class A sweeps use; a confirmed absence is a fact,
  not an open action.

### Step 8: Route into the next phase

**Check `migration-map.json`'s `phase_status` first**, since this step is reached both from a
fresh run (step 7) and from step 1's **Continue** branch on a resumed plan, and what to tell
the user depends on what has already run. Read the JSON, never the rendered `## Phase status`
table -- `findings-report.md`'s `## migration-plan.md` section states why.

Read each entry's `status` against `findings-report.md`'s **`## Phase status`** closed
vocabulary (`pending` / `complete` / `failed` / `not implemented`), and normalize before
branching, so the switch below is total rather than three special cases:

- **`complete`** -- and, for phase 0 only, `complete (full gate)` -- counts as done.
- **`failed`** counts as **not** done, and is reported explicitly: name the phase, say it
  failed rather than never ran, and route the user back into that phase's own command to
  re-run it. Do not silently offer the next phase past a failure. **Phase 2 is the one phase
  with no command of its own** (`migrating-to-spicedb/SKILL.md`: "Phase 2 deliberately has no
  command... schema conversion launches the existing `schema-validator` agent on completion,
  and `/spicedb-dev:validate-schema` remains available to re-run validation on demand"), so
  "that phase's own command" does not exist for it. Route a `failed` phase 2 to
  **`/spicedb-dev:validate-schema`** to see the current errors against `schema.zed`, and to
  `/spicedb-dev:migrate-schema` to regenerate the schema once the cause is understood --
  re-running validation alone will not change a schema that is genuinely wrong. Phase 2's
  routing table below assumes this; do not send the user to a `/spicedb-dev:migrate-validate`
  or similar, which does not exist.
- **`pending`**, a missing entry, an empty `status`, or **any value the vocabulary does not
  define** (e.g. a phase-2 `status` reading `clean -- 0 errors`) all count as not done. Say
  plainly which entry could not be read and that it is being treated as incomplete -- never
  resolve an unrecognized value to `complete`.
- **`not implemented`** is a legacy value from before `/spicedb-dev:migrate-code` existed.
  Read it exactly like `pending` for phase 4 -- not done -- on a plan old enough to carry it;
  no phase writes this value today, phase 4 included.

Then branch on phases 1, 3, 4, and 5 (phase 2 rides with phase 1):

- **Phase 1 not done** (the fresh-run case, or a resumed plan that never got past the gate)
  -- phase 1 is next. Use item 3 below.
- **Phase 1 done, but phase 3, 4, and/or 5 are not** -- skip item 3; open directly with
  item 4, offering whichever of phase 3 / phase 4 / phase 5 `phase_status` shows not done. If
  phase 2 is `failed`, say so first: the schema did not validate, and phases 3, 4, and 5 all
  consume it. Name **`/spicedb-dev:validate-schema`** as the command that shows the current
  errors -- phase 2 has no command of its own -- and `/spicedb-dev:migrate-schema` as the one
  that regenerates the schema. Phases 3 and 5 have no ordering dependency on each other or on
  phase 4. Phase 4
  additionally needs phase 3's emitted ID codec whenever `migration-map.json`'s
  `id_encoding.mode` is `base64url` for any type (`/spicedb-dev:migrate-code`'s own step 3 and Error Handling table
  halt on this, not this command) -- when that applies, mention that phase 3 should run
  first; when `id_encoding.mode` is `none` and `id_encoding.status` is `clean` for every type, phase 4 does not need phase 3 to
  have run first. **If `status` is `unresolved` or `unknown`, phase 4 must not point converted code at SpiceDB at all**, whatever `mode` says -- see `findings-report.md`'s `id_encoding`.
- **Phase 1, 3, 4, and 5 all done** -- there is nothing left to route into automatically
  *within the phase pipeline*, but the pipeline finishing is not the end of the road: say so
  plainly rather than dead-ending. Skip item 3 and item 4's "run them" framing entirely -- do
  not tell the user to run a command they have already run. Instead say plainly that the
  automated conversion pipeline is finished (phases 0 through 5 have all run) and name what
  comes next, in order: deploying the schema if that hasn't happened, working through every
  recorded **Deferred / manual** item and **Sync obligation**, and then cutover
  (`migrating-to-spicedb/references/cutover-strategies.md`'s seven-step playbook). **Name
  `/spicedb-dev:migrate-verify` explicitly here** -- it implements that playbook's step 4
  ("Dual-write, shadow-read"): it emits a differential harness, in the project's own
  language, that dual-runs SpiceDB beside the still-authoritative source system, diffs
  disagreements safely, and turns confirmed agreements into a regression suite. Point the user
  at it once phase 3 has completed with a passed verification -- running it earlier only
  produces false disagreements against a target that isn't loaded yet
  (`migrate-verify.md`'s own Error Handling table). Cutover steps 5 through 7 (the
  reconciliation job, the flag cutover, and removing the source system) remain the customer's
  own, by design -- `cutover-strategies.md`'s "What the plugin does not automate" section
  states why, and this command should not imply a tool exists for them. Then stop.

Tell the user, plainly:

1. Where `migration-plan.md` and `migration-map.json` were written, and that every later
   phase reads `migration-map.json` rather than re-asking -- that is what makes the
   migration resumable and each sub-command independently runnable.
2. The resolved decisions, one line each, and the sync-obligation count.
3. **Next: phase 1 -- `/spicedb-dev:migrate-schema`.** Offer to continue into it
   immediately, following that command's own process, or hand back so the user can run it.
   It will find the plan in `[output-dir]`, skip its own inline gate, convert the model,
   and launch the `schema-validator` agent (phase 2) on completion. Point it at the same
   `[output-dir]` this command wrote to, or it will not find the plan and will hold its
   reduced gate instead.
4. **Phase 3 (`/spicedb-dev:migrate-data`), phase 4 (`/spicedb-dev:migrate-code`), and phase
   5 (`/spicedb-dev:migrate-tests`) are all implemented commands.** Each is a pure consumer
   of `migration-map.json`, holds no gate of its own, and halts back to this command if the
   map is missing or its `phase_status["0"].status` is not `complete (full gate)`. Run any
   of them pointed at the same `[output-dir]` once phase 1 has produced
   `schema.zed` and `migration-map.json`. Phases 3 and 5 have no ordering dependency on each
   other. Phase 4 additionally imports phase 3's emitted ID codec whenever
   `migration-map.json`'s `id_encoding.mode` is `base64url` for any type -- run phase 3 first in that case; when
   `id_encoding.mode` is `none` and `id_encoding.status` is `clean` everywhere, phase 4 does not need phase 3 to have run first. **If `status` is `unresolved` or `unknown`, phase 4 must not point converted code at SpiceDB at all**, whatever `mode` says -- see `findings-report.md`'s `id_encoding`.
5. **Data before code.** Phase 3 must complete and pass verification before any client
   code -- converted or not -- is pointed at this store's data. A check against a SpiceDB
   instance still missing relationships silently denies everything.
6. **After phase 5: verification and cutover, not the end of the pipeline.**
   `/spicedb-dev:migrate-verify` emits a differential harness
   (`migrating-to-spicedb/references/differential-harness.md`) that dual-runs SpiceDB beside
   the still-authoritative source system -- the tool
   `migrating-to-spicedb/references/cutover-strategies.md` step 4 names. Point the user at it
   once phase 3 has passed verification; it is the bridge between "the pipeline converted
   everything" and the customer's own cutover.

## Error Handling

| Situation | Do this |
|---|---|
| No pack for the detected source | Halt at step 3. Name what was detected and which packs exist. Write nothing. |
| Two supported sources both plausible | Ask which, before any other question. Every rule comes from the pack. |
| No model found anywhere | Ask for the location. If store IDs exist but no on-disk model, say the model may live only on a running store and give the pack's fetch command. Never convert a partial model. |
| Source model uses a schema version the pack rejects | Halt with the pack's own remediation pointer (for OpenFGA `schema 1.0`, its 1.0 → 1.1 upgrade path). Convert afterward. |
| Class A finding left unresolved | Write that site's `decisions.per_blocker_resolutions` entry in `migration-map.json` with `resolution: null` (or leave it absent), do not route into phase 1, and say which decision is outstanding. |
| Identifier collision the map cannot resolve | Halt. Report both source names. Silently merging two relations is a correctness bug, not a naming nit. |
| `migration-map.json` already exists, `phase_status["0"].status` is `complete (full gate)` | Do not re-run the gate (step 1). Summarize, and continue or re-analyze at the user's direction. |
| `migration-map.json` already exists, `phase_status["0"].status` is `inline (reduced -- no codebase analysis)` (or missing) | The full gate has not run. Say so, then run it (step 1): launch the analyzer and hold the gate, carrying forward the reduced gate's recorded decisions as defaults rather than blank questions. |
| The analyzer returns file dumps instead of findings | Ask it for the structured report. Do not paste them onward. |
| The `migration-analyzer` agent cannot be launched (not registered in this runtime) | Not a halt. Follow step 2's fallback ladder as written there -- re-dispatch via `general-purpose` carrying `agents/migration-analyzer.md`, and only if that also fails, run it inline while saying so and naming the context cost. Do not restate the ladder here; step 2 is the single statement of it. |

## Notes

- The version floor is SpiceDB **v1.52.0**; the OpenFGA pack's conversion rules were
  verified against v1.56.0 and zed v0.31.1.
- **Ask once.** Everything this gate resolves is recorded in `migration-map.json` and
  rendered to `migration-plan.md` for review. Later phases read the JSON; they do not
  re-ask. If a decision needs to change, change it there -- and remember that stored data
  may already have been migrated against the old one.
- **Nothing is written until the gate is resolved.** No plan, no map, no schema. That
  ordering is what makes an unresolved Class A finding an actual halt rather than a warning
  attached to output that already exists.
- `/spicedb-dev:migrate-schema` remains independently runnable for schema-only work. When
  it runs with no plan, it holds a reduced inline gate covering phase 1's own inputs and
  says so. The two gates never both run **for the same purpose**: the reduced gate never
  runs once a plan exists, and this command's full gate always runs once, even if a reduced
  gate already wrote a map -- step 1 checks `migration-map.json`'s phase-0 authorship
  (`phase_status["0"].status`) for exactly this reason, because a reduced-gate map means the
  codebase itself was never swept.
