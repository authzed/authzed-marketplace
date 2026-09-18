# spicedb-dev

A SpiceDB development plugin for Claude Code. Helps developers add a first pass at
fine-grained authorization to applications being built or modified -- covering the full
lifecycle from permission model design through implementation, coverage auditing, and testing.

---

## Purpose and Scope

This plugin is focused on getting authorization structurally correct the first time:
right permissions, right places, right operations. It is not a production hardening or
debugging tool. The target user is a developer building or modifying an application who
needs SpiceDB authorization to work correctly from the start.

---

## Ideal Workflow

### Once, early -- before or alongside data model design

**`/spicedb-dev:plan`**
Scopes the work, produces `authorization-plan.md`, and writes an authorization snippet
to `CLAUDE.md` (and `AGENTS.md`, for Codex and other non-Claude tools). This snippet is
the most important output: it makes the AI assistant consider SpiceDB writes and
checks automatically whenever it generates or modifies handlers in every future
session, without the developer needing to invoke any command.

**`/spicedb-dev:design-model`**
Interactive session that scans for existing model files (Prisma, Django, Go structs,
GraphQL, SQL) and extracts entity names as a starting point. Produces `permission-model.md`.

**`/spicedb-dev:generate-schema`**
Converts `permission-model.md` to `schema.zed`. The schema-validator agent runs
automatically and checks for anti-patterns.

**Deploy the schema** (external step):
```
zed schema write schema.zed --endpoint=localhost:50051 --token=<token>
```

---

### Continuously, alongside every feature

Every feature needs both operations. This is the most common implementation mistake:
adding checks without writes causes SpiceDB to return NO_PERMISSION for everything,
silently.

**`/spicedb-dev:implement-spicedb-relationships`**
Run when writing handlers that create resources, grant membership, or delete resources.
Adds WriteRelationships (on create/grant) and DeleteRelationships (on delete/revoke).

**`/spicedb-dev:implement-spicedb-checks`**
Run when writing handlers that read, modify, or delete resources on behalf of a user.
Adds CheckPermission for single-resource access, LookupResources for list endpoints.
Do not use CheckPermission in a loop -- use BulkCheckPermission to filter a known list
or LookupResources to discover accessible resources.

**`/spicedb-dev:implement-spicedb`** (router)
Use when unsure which of the above to run. Communicates the paired requirement and
routes to the right command.

---

### Periodically, as the app accumulates features

**`/spicedb-dev:audit-coverage`**
Produces a coverage matrix: for every permission in the schema, shows whether a
CheckPermission call exists in code. Also reports relationship write coverage and
unfiltered list endpoints. Routes each gap back to the correct implement command.

---

### Once, when the feature set is stable

**`/spicedb-dev:test-permissions`**
Generates test fixtures and integration tests from the schema. Produces positive
(access granted), negative (access denied), and hierarchical inheritance tests for
every permission.

---

## Entry Points by Situation

| Situation | Start here |
|---|---|
| New project, no existing auth | `/spicedb-dev:plan` |
| Have a data model, need permission design | `/spicedb-dev:design-model` |
| Have a permission model, need a schema | `/spicedb-dev:generate-schema` |
| Have a schema, need implementation (Go / TypeScript / Python / C#, using Authzed's published clients) | `/spicedb-dev:implement-spicedb` |
| Inherited codebase, need coverage picture | `/spicedb-dev:audit-coverage` |
| Need to validate an existing schema | `/spicedb-dev:validate-schema` |
| Migrating from OpenFGA / Okta FGA / Oso Cloud | `/spicedb-dev:migrate` |
| Migration schema converted, need to move relationship data | `/spicedb-dev:migrate-data` |
| Migration schema converted, need to rewrite client code | `/spicedb-dev:migrate-code` |
| Migration schema converted, need to convert test fixtures | `/spicedb-dev:migrate-tests` |
| Migration converted, need to verify it against production traffic before cutover (dual-run, diff, replay, snapshot-to-assertions) | `/spicedb-dev:migrate-verify` |
| Adding the **prototype** SpiceDB client -- or any client in Java, Rust, or Ruby | `spicedb-client-integration` skill (auto-loads; start with `references/installation.md`) |

---

## Components

### Commands
- **plan** -- scopes work, produces authorization-plan.md, sets up CLAUDE.md
- **design-model** -- interactive permission model design, scans existing model files
- **generate-schema** -- converts permission-model.md to schema.zed
- **validate-schema** -- validates any .zed file on demand
- **implement-spicedb** -- router: communicates paired requirement, routes to writes or checks
- **implement-spicedb-relationships** -- adds WriteRelationships / DeleteRelationships to code
- **implement-spicedb-checks** -- adds CheckPermission / LookupResources / BulkCheckPermission to code
- **audit-coverage** -- coverage matrix for schema permissions vs code checks
- **test-permissions** -- generates test fixtures and integration tests
- **migrate** -- phase 0 and the migration's front door: runs the migration-analyzer agent over the model and the codebase, holds the single pre-flight gate, writes migration-plan.md and migration-map.json, routes into phase 1
- **migrate-schema** -- converts a source authorization model to schema.zed and an identifier map; phases 1 and 2, reading migration-plan.md when present and holding a reduced gate inline only when run standalone without one
- **migrate-data** -- phase 3: moves a live source store's relationship data into SpiceDB (extract, transform, load, verify) and emits the ID codec module; a pure consumer of migration-plan.md and migration-map.json, with no gate of its own
- **migrate-code** -- phase 4, and not the last thing the plugin automates: phase 5 and the cutover harness both still have commands behind them, neither ordered against this one. Vendors the SpiceDB client into the project and rewrites OpenFGA call sites into SpiceDB client calls construct-by-construct, per code-mapping.md, importing phase 3's ID codec whenever object IDs are encoded; a pure consumer of migration-plan.md and migration-map.json, with no gate of its own
- **migrate-tests** -- phase 5: converts a source system's test fixtures and assertions into SpiceDB validation YAML, validated with zed validate; a pure consumer of migration-plan.md and migration-map.json, with no gate of its own
- **migrate-verify** -- not one of the six pipeline phases; implements the cutover playbook's "dual-write, shadow-read" step once phase 3 has passed verification. Emits a differential harness (dual-run, diff, replay, snapshot-to-assertions) into the customer's own project, in their language, that dual-runs SpiceDB beside the still-authoritative source system and turns confirmed agreements into regression tests; adds no row to the Phase status table

### Skills (auto-load by context)
- **authorization-planner** -- entry point; routes to the right command or skill
- **spicedb-schema-design** -- schema patterns, anti-patterns, design decisions
- **spicedb-best-practices** -- client library usage, consistency models, error handling
- **authorization-testing** -- test fixture patterns, integration testing
- **migrating-to-spicedb** -- source-agnostic migration framework: phase pipeline (with per-phase build status), pre-flight gate, conversion-pack contract, and (once conversion is done) the cutover playbook and differential-harness contract
- **oso-to-spicedb** -- conversion pack for Oso Cloud: Polar policy mapping, blockers, identifier normalization, fact mapping, code mapping, test mapping
- **openfga-to-spicedb** -- conversion pack for OpenFGA / Okta FGA / Auth0 FGA: schema mapping, blockers, naming normalization, data mapping, code mapping, test mapping, and the differential-harness source adapter
- **spicedb-client-integration** -- general-purpose client integration for Go, Python, TypeScript, C#, Java, Rust, and Ruby: obtaining the prototype client and the vocabulary shared across all seven languages

### Agents (run autonomously)
- **schema-validator** -- validates schema, checks for anti-patterns, suggests improvements
- **checkpoint-identifier** -- data flow analysis to identify where checks should be added
- **migration-analyzer** -- phase 0 of a migration: scans the source model and the whole
  codebase, returns the Class A/B/C findings the pre-flight gate resolves

---

## Critical Constraints

- **Writes before checks.** SpiceDB returns NO_PERMISSION until relationships are written.
  A codebase with only checks and no writes silently denies everything.
- **Relation names, not permission names.** WriteRelationships uses the `Relation` field.
- **Fail-safe on errors.** Deny access when SpiceDB is unavailable.
- **List endpoints need LookupResources.** CheckPermission in a loop does not scale
  and returns unfiltered results before the check.
- **Caveats cannot be used with LookupResources or LookupSubjects.** Model list-accessible
  permissions as relations, not caveats.
- **ZedToken for consistency.** After a write, pass `WrittenAt` as `AtLeastAsFresh` on
  subsequent reads to guarantee read-your-writes. Pass the same ZedToken to all pages
  in a paginated sequence.
