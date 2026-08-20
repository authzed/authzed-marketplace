---
name: Migrating to SpiceDB
description: Use when migrating an application from another authorization system
  (OpenFGA, Okta FGA) to SpiceDB - runs the phase pipeline that converts schema,
  data, application code, and tests, with a pre-flight gate that surfaces every
  blocking decision before conversion begins
---

# Migrating to SpiceDB

This skill defines the source-agnostic pipeline for converting an application's
authorization system to SpiceDB: schema, data, client code, and tests. Source-specific
rules live in a separate **conversion pack** skill; this skill defines the pipeline those
packs plug into, and the protocol commands and packs use to talk to each other.

## Overview

A migration converts four things, in a fixed order: the authorization model to a `.zed`
schema, existing relationship data, application client code, and the source system's test
suite. Every decision that could go wrong -- an identifier collision, a construct SpiceDB
can't express, a multi-tenancy choice -- is surfaced **once**, up front, at a single gate,
because these decisions interact (tenancy choice constrains identifier strategy constrains
code rewriting). Nothing is converted past an unresolved hard blocker.

## Source registry

| Source system | Pack skill | Status |
|---|---|---|
| OpenFGA / Okta FGA / Auth0 FGA | `openfga-to-spicedb` | supported |

If the detected source has no pack, stop and say so. Do not improvise a translation --
an unsupported source needs a new pack, not an ad hoc conversion.

## Phase pipeline

This is the **designed** pipeline, and every phase of it is built. The status column stays
authoritative -- read it rather than this sentence, and never route a user to a phase whose
status is anything other than **shipped**.

| # | Phase | Driver | Output | Status |
|---|---|---|---|---|
| 0 | Discover & analyze | `migration-analyzer` agent, via `/spicedb-dev:migrate` | `migration-plan.md`, `migration-map.json` + **GATE** | **shipped** |
| 1 | Schema conversion | `/spicedb-dev:migrate-schema` | `schema.zed`, `migration-map.json`, `migration-plan.md` | **shipped** |
| 2 | Validate | `schema-validator` agent (runs inside phase 1) | validation report | **shipped** |
| 3 | Data migration | `/spicedb-dev:migrate-data` | migration script + ID codec | **shipped** |
| 4 | Client code | `/spicedb-dev:migrate-code` | vendored client + rewritten call sites | **shipped** |
| 5 | Test conversion | `/spicedb-dev:migrate-tests` | SpiceDB validation YAML | **shipped** |

**What works today: phases 0 through 5, in full.** `/spicedb-dev:migrate` is the front door: it
launches the `migration-analyzer` agent over the model *and* the codebase, holds the full
pre-flight gate, writes `migration-plan.md` and `migration-map.json`, then routes into
`/spicedb-dev:migrate-schema`, which converts (phase 1) and validates (phase 2).
`/spicedb-dev:migrate-data` (phase 3), `/spicedb-dev:migrate-code` (phase 4), and
`/spicedb-dev:migrate-tests` (phase 5) are each run separately once a plan exists: all
three are pure consumers of `migration-plan.md` and `migration-map.json`, hold no gate of
their own, and halt back to `/spicedb-dev:migrate` if the plan is missing or was written by
the reduced inline gate rather than the full one. Phase 4 additionally imports phase 3's
emitted ID codec whenever `id_encoding.mode` is `base64url` for any type, so run phase 3
first in that case; phases 3 and 5 have no ordering dependency on each other, and phase 4
does not need phase 3 to have run first when `id_encoding.mode` is `none` and `id_encoding.status` is `clean`. **If `status` is `unresolved` or `unknown`, phase 4 must not point converted code at SpiceDB at all**, whatever `mode` says -- see `findings-report.md`'s `id_encoding`.

`/spicedb-dev:migrate-schema` stays independently runnable for schema-only work. Run
standalone with no `migration-plan.md` present, it holds a **reduced** gate inline --
asking only the decisions schema conversion itself depends on (tenancy shape, ID encoding,
split naming, any Class A resolutions the model or a targeted repo grep triggers) -- and
writes the plan it then reads on every subsequent run. Reduced is the operative word: that
inline gate covers phase 1's inputs, not the whole-codebase analysis phase 0 does.
**Exactly one gate runs per migration**: the inline one is skipped whenever a plan exists,
and phase 0 always writes one first.

Phase 4 is implemented as `/spicedb-dev:migrate-code`, which consumes `migration-map.json`'s
`relation_splits` key automatically instead of a human reading a table -- the same shape
phases 3 and 5 (`/spicedb-dev:migrate-data`, `/spicedb-dev:migrate-tests`) already use.
`migration-plan.md`'s **Relation splits** section is a rendering of that same key for a
human to review, not something any phase reads (`references/findings-report.md`).

**Phase 3 must complete and pass verification before converted code is *run* against this
store's data, whoever runs it** -- checked against an empty or partially loaded SpiceDB
instance, it silently denies everything. This is a runtime ordering rule, not a command
ordering rule: `/spicedb-dev:migrate-code` itself may run before `/spicedb-dev:migrate-data`
(it only needs phase 3's emitted ID codec file, and only when `id_encoding.mode` requires
encoding for some type); it is *pointing the resulting code at live SpiceDB data* that must
wait for phase 3's verification to pass.

Two structural notes, recorded so they are not re-litigated: phase 0's read-heavy scan
(grepping an entire codebase, reading the full source model) lives in the
`migration-analyzer` agent rather than inline, so that output doesn't swamp the
orchestrator's context, and it is source-agnostic -- a pack's detection rules and blocker
catalog are its inputs, not its logic. Phase 2 deliberately has no command of its own:
schema conversion launches the existing `schema-validator` agent on completion, the same
way `/spicedb-dev:generate-schema` already does, and `/spicedb-dev:validate-schema`
remains available to re-run validation on demand.

## The gate

The gate produces every decision at once, before any file is written. Never convert past
an unresolved Class A finding -- a hard blocker with no mechanical fix. Class B and C
findings don't halt, but Class B still needs to be seen and owned because it changes
stored data. See `references/findings-report.md` for the full taxonomy and the
`migration-plan.md` / `migration-map.json` formats the gate produces.

Phase 0 (`/spicedb-dev:migrate`) holds this gate. `/spicedb-dev:migrate-schema` holds a
reduced version of it inline, and only when run standalone with no plan present. What does
**not** change with the reduction: the halt. A Class A finding with no recorded resolution
stops the conversion either way.

## Fidelity ratings

Every construct in a pack's schema mapping carries one of four ratings: `clean`,
`effort`, `heavy`, `blocked`.

`clean` is a mechanical translation with no decision required. `effort` is expressible,
but needs redesign or creates a new write-path obligation. The other two are the ones
worth slowing down for:

- **`heavy`** -- expressible only via generated schema or reification. Possible and
  verified, but structurally costly, and the cost grows with the customer's vocabulary
  (for example, a generated permission union per custom role). This still ships as SpiceDB
  schema -- it just means schema writes enter the product's hot path.
- **`blocked`** -- SpiceDB genuinely cannot answer it. The capability has to move into
  application code or another product entirely.

Do not report `heavy` as `effort` -- that hides a hot-path schema-write obligation from
the estimate. Do not report `heavy` as `blocked` either -- customer-defined roles are
exactly the construct this cuts against: the one most likely to decide a B2B deal, and the
easiest to assume unsupported before it has actually been worked through. The distinction
is easy to get wrong in both directions, and it is the one that decides whether a
migration is priced correctly. A pack must state the evidence behind each rating: `heavy`
requires a verified worked example, `blocked` requires a statement of where the
capability goes instead.

## Working on someone else's repository

This pipeline rewrites application source across a whole repository -- call sites, schema,
generated modules, build rules. Treat the target repo as read-only *upstream* and local-only
*downstream*:

- **Never push, and never open a pull request.** Publishing the conversion is the user's
  decision, made after they have read `migration-plan.md`, not a step in any phase. No phase
  is complete only once its work is pushed; completion is defined by the validation below.
  This holds even when the run's own commits are clean and the tests pass.
- **Never work on the default branch.** A dedicated branch must exist before phase 1 writes
  anything. If the working tree is dirty when the pipeline starts, say so and stop rather
  than mixing the conversion into someone's in-progress work.
- **Commit locally as you go, per phase** -- a local commit is the checkpoint that makes a
  phase resumable and makes the diff reviewable, not a publication step. **Not every phase
  can do this itself:** `/spicedb-dev:migrate` and `/spicedb-dev:migrate-schema` declare no
  `Bash` in their `allowed-tools` and so cannot run `git` at all. Those two state what should
  be committed and leave it to whoever is driving the pipeline; do not report a phase blocked
  because it could not commit, and do not treat an uncommitted phase 0 or 1 as incomplete.
  The phases that do have `Bash` commit their own work.
- **Never rewrite existing history**, and never touch a branch the pipeline did not create.

**Validate instead of publishing.** What replaces "push and see if CI passes" is validating
the changes where they are, and a phase that cannot show this has not finished:

| Phase | What must pass before it is `complete` | Where that is defined |
|---|---|---|
| 1 -- schema | Emits `schema.zed`; it is phase 2 that validates it, so do not claim validation here | `migrate-schema.md` |
| 2 -- validate | The `spicedb-dev:schema-validator` agent runs and its findings are recorded | `validate-schema.md` |
| 3 -- data | The migration script's own `--verify` pass over what it loaded | `migrate-data.md` steps 5-7 |
| 4 -- code | The language's build or type-check step, run over every file the phase touched | `migrate-code.md` step 8 |
| 5 -- tests | `zed validate` clean on every emitted validation YAML | `migrate-tests.md` |

Each command states its own criterion; the table is an index, not a second source. Where they
ever disagree, the command wins. Note what a clean build does and does not buy: it proves the
rewrite compiles, never that it behaves like the source system -- that is
`/spicedb-dev:migrate-verify`'s job, and no amount of local validation substitutes for it.

Record the command and its result in `phase_status[N].artifact`, the same way every other
phase artifact is recorded. A phase whose validation was not run is `pending`, not
`complete` -- the same distinction phases 3 and 5 already draw between "nothing to do" and
"failed". Reporting a phase `complete` on the strength of the code having been written,
without the check above having been run, is the failure this section exists to prevent.

## What This Skill Does NOT Do

- Translate any specific source's constructs -- that's the pack's job. See the source
  registry above and `references/pack-contract.md`.
- Build or run the customer's own reconciliation job, feature-flag infrastructure, or the
  decision of when to remove the source system -- `references/cutover-strategies.md`'s steps
  5 through 7 are the customer's, by design, even though steps 1 through 4 (inventory through
  dual-write/shadow-read) run on top of this plugin's commands, `/spicedb-dev:migrate-verify`
  included.
- Teach SpiceDB client library usage in the target language -- use
  `spicedb-client-integration` for that.
- Design the target SpiceDB schema from scratch (as opposed to converting an existing
  model) -- use `spicedb-schema-design` for greenfield modeling.

## Red Flags

If you find yourself:
- Naming a source-specific construct (an OpenFGA `contextualTuples`) anywhere in this
  skill's files outside the source registry table -- that content belongs in the pack,
  not here.
- About to write `schema.zed` or touch relationship data with an unresolved Class A
  finding still open -- stop. That is exactly what the gate exists to prevent.
- Treating `heavy` as a lesser version of `effort` -- re-read the Fidelity ratings section
  above; the distinction is commercial, not cosmetic.
- Asked to migrate a source with no pack in the registry -- say so and stop. Do not
  improvise.
- About to run `git push`, open a pull request, or commit to the default branch of the
  target repository -- stop. See "Working on someone else's repository" above; publishing
  is the user's call, and no phase needs it to be complete.
- About to mark a phase `complete` without having run that phase's validation command --
  stop. Written is not validated.

## Quick Reference

| Need to... | Read This |
|---|---|
| See the ten things a conversion pack must supply | `references/pack-contract.md` |
| See the Class A/B/C finding taxonomy, `migration-plan.md` layout, and the `migration-map.json` schema | `references/findings-report.md` |
| See the seven-step production cutover playbook that picks up once the pipeline converts everything | `references/cutover-strategies.md` |
| See the dual-run/diff/replay/snapshot-to-assertions contract `/spicedb-dev:migrate-verify` emits a harness against | `references/differential-harness.md` |

---

**Workflow summary, as it runs today:** Run `/spicedb-dev:migrate` → the
`migration-analyzer` agent scans the model and the codebase → the gate resolves every
Class A finding in one batch and writes `migration-plan.md` and `migration-map.json` →
`/spicedb-dev:migrate-schema` reads the plan, converts the schema (phase 1) and validates
it (phase 2) → `/spicedb-dev:migrate-data` (phase 3), `/spicedb-dev:migrate-code` (phase 4),
and `/spicedb-dev:migrate-tests` (phase 5) each read `migration-plan.md` and
`migration-map.json` rather than re-asking, which is what makes the migration resumable --
phase 4 additionally imports phase 3's emitted ID codec whenever a type needs id encoding →
once phase 3 passes verification, the converted application can safely check against the
migrated SpiceDB data → `/spicedb-dev:migrate-verify` emits a differential harness
(`references/differential-harness.md`) implementing `references/cutover-strategies.md` step 4,
so the converted system can be dual-run and shadow-read beside the still-authoritative source
before cutover.
