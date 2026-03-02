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
to `CLAUDE.md`. The CLAUDE.md snippet is the most important output: it makes Claude
consider SpiceDB writes and checks automatically whenever it generates or modifies
handlers in every future session, without the developer needing to invoke any command.

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
| Have a schema, need implementation | `/spicedb-dev:implement-spicedb` |
| Inherited codebase, need coverage picture | `/spicedb-dev:audit-coverage` |
| Need to validate an existing schema | `/spicedb-dev:validate-schema` |

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

### Skills (auto-load by context)
- **authorization-planner** -- entry point; routes to the right command or skill
- **spicedb-schema-design** -- schema patterns, anti-patterns, design decisions
- **spicedb-best-practices** -- client library usage, consistency models, error handling
- **authorization-testing** -- test fixture patterns, integration testing

### Agents (run autonomously)
- **schema-validator** -- validates schema, checks for anti-patterns, suggests improvements
- **checkpoint-identifier** -- data flow analysis to identify where checks should be added

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
