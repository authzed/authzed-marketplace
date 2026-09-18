# spicedb-dev

SpiceDB development plugin for Claude Code. Adds fine-grained authorization to applications as they are built -- keeping permissions in sync with features, not as an afterthought.

## Features

### Commands

- `/spicedb-dev:plan` - Plan a full authorization implementation; produces authorization-plan.md and sets up CLAUDE.md for ambient coverage
- `/spicedb-dev:design-model` - Interactive permission model design session
- `/spicedb-dev:generate-schema` - Convert permission model to SpiceDB schema
- `/spicedb-dev:validate-schema` - Validate an existing SpiceDB schema (.zed file)
- `/spicedb-dev:implement-spicedb` - Add SpiceDB to a feature; routes to writes, checks, or both
- `/spicedb-dev:implement-spicedb-checks` - Add permission checks and lookups to code (CheckPermission, BulkCheckPermission, LookupResources, LookupSubjects)
- `/spicedb-dev:implement-spicedb-relationships` - Add relationship writes and deletes to code (WriteRelationships, DeleteRelationships)
- `/spicedb-dev:audit-coverage` - Audit permission coverage; shows which schema permissions have code checks
- `/spicedb-dev:test-permissions` - Generate test data fixtures and scenarios
- `/spicedb-dev:migrate` - Migrating from another authorization system? Start here. See [Migrating to SpiceDB](#migrating-to-spicedb) below
- `/spicedb-dev:migrate-schema` - Convert the source model to a SpiceDB schema
- `/spicedb-dev:migrate-data` - Move a live store's relationship data into SpiceDB
- `/spicedb-dev:migrate-code` - Rewrite client call sites and add the SpiceDB client
- `/spicedb-dev:migrate-tests` - Convert test fixtures and assertions to SpiceDB validation YAML
- `/spicedb-dev:migrate-verify` - Emit a differential harness that dual-runs SpiceDB beside the still-authoritative source

### Skills

- **authorization-planner** - Entry point; routes to the right command or skill based on where you are in the workflow
- **spicedb-schema-design** - Schema patterns, anti-patterns, and design decisions
- **spicedb-best-practices** - Client library usage, consistency models, error handling, performance
- **authorization-testing** - Test fixture generation and integration testing patterns
- **migrating-to-spicedb** - Source-agnostic migration framework: phase pipeline (with per-phase build status), pre-flight gate protocol, the conversion-pack contract, and (once conversion is done) the seven-step cutover playbook and the differential-harness contract `/spicedb-dev:migrate-verify` implements
- **oso-to-spicedb** - Conversion pack for Oso Cloud: Polar policy mapping, blocker catalog, identifier normalization, fact mapping, code mapping, and test mapping
- **openfga-to-spicedb** - Conversion pack for OpenFGA, Okta FGA, and Auth0 FGA: schema mapping, blocker catalog, identifier normalization, data mapping, code mapping, test mapping, and the differential-harness source adapter
- **spicedb-client-integration** - General-purpose SpiceDB client integration for Go, Python, TypeScript, C#, Java, Rust, and Ruby: obtaining the prototype client and the vocabulary shared across all seven languages

### Agents

- **schema-validator** - Validates SpiceDB schemas and suggests improvements
- **checkpoint-identifier** - Analyzes code to identify where permission checks should go
- **migration-analyzer** - Phase 0 of a migration: scans the source authorization model and the whole codebase, returns Class A/B/C findings for the pre-flight gate

## Installation

This plugin is available in the AuthZed marketplace.

**1. Add the AuthZed marketplace to Claude Code:**

```
/plugin marketplace add authzed/authzed-marketplace
```

**2. Install the plugin:**

```
/plugin install spicedb-dev@authzed-marketplace
```

Alternatively, install from a local clone of the marketplace repository:

```
/plugin marketplace add /path/to/authzed-marketplace
/plugin install spicedb-dev@authzed-marketplace
```

### Codex CLI

**1. Add the AuthZed marketplace to Codex:**

```bash
codex plugin marketplace add authzed/authzed-marketplace
```

**2. Install the plugin:**

```bash
codex plugin add spicedb-dev@authzed-marketplace
```

See [`.codex/INSTALL.md`](.codex/INSTALL.md) for the manual installation path
(no marketplace registration) and troubleshooting.

## Migrating to SpiceDB

If you already run OpenFGA, Okta FGA, Auth0 FGA, or Oso Cloud, the plugin converts the model, the
data, the application code, and the tests -- and then helps you cut over without guessing.

```
/spicedb-dev:migrate /path/to/your/project
```

That single command analyzes the project and holds one **pre-flight gate**: every decision
that cannot be made mechanically is put to you *before* anything is written. Tenancy shape,
identifier encoding, permission naming, what to do about constructs SpiceDB models
differently -- all asked once, up front, and recorded. From there it routes through the
phases.

### The phases

| Phase | Command | What it does |
|---|---|---|
| 0 | `migrate` | Analyze, hold the gate, write `migration-plan.md` + `migration-map.json` |
| 1 | `migrate-schema` | Convert the model to `schema.zed` |
| 2 | *(automatic)* | Validate the schema and report findings |
| 3 | `migrate-data` | Extract, transform, load, and verify relationship data |
| 4 | `migrate-code` | Rewrite call sites; add the SpiceDB client |
| 5 | `migrate-tests` | Convert fixtures and assertions to validation YAML |
| — | `migrate-verify` | Dual-run SpiceDB beside the source and diff the answers |

Phases 3 and 5 have no ordering dependency on each other; phase 4 needs phase 3 only when
object IDs are encoded. Each phase records its own status, so a run can stop and resume.

### Two artifacts, one of them authoritative

- **`migration-map.json`** is the record: every name, every decision, every phase's status.
  Phases read and write this.
- **`migration-plan.md`** is a rendering of it, for humans to review. Deleting it loses
  nothing.

### It stops rather than guessing

Some things have no mechanical conversion, and the pipeline is built to say so instead of
producing something that compiles and is wrong. It halts and asks when it finds contextual
tuples, multi-store tenancy, model-ID pinning, a wildcard that would become transitive, or
an embedded in-process OpenFGA server (which makes the code phase an architectural decision
rather than a rewrite). Anything it cannot convert is handed back explicitly, with
`file:line`, under **Needs action** in the plan.

Converted code carries `TODO(spicedbmigration):` and `NOTE(spicedbmigration):` markers at
every site that needs a human, so `grep` finds all of them.

### What it does not do

It will not push, open a pull request, or work on your default branch -- publishing the
conversion is your decision. It does not run your cutover: the differential harness and the
[cutover playbook](skills/migrating-to-spicedb/references/cutover-strategies.md) give you
dual-write and shadow-read, but deciding when to flip and when to remove the source system
stays with you.

## Quick Setup: Make Permissions Ambient

After installing, add the following to your project's `CLAUDE.md` (Claude Code) or
`AGENTS.md` (Codex and other tools). This is the single most effective step -- it makes
your AI assistant consider SpiceDB writes and checks automatically whenever it generates
or modifies handlers, without you needing to invoke any command. Running `/spicedb-dev:plan`
(Claude Code) or the `plan` command (Codex) writes both files automatically.

```markdown
## Authorization (SpiceDB)

This project uses SpiceDB for fine-grained authorization via the spicedb-dev plugin.

When generating or modifying any handler, route, or service method that creates, reads,
updates, or deletes a resource:

1. **Relationship writes**: Does this handler create a resource, grant access, or delete
   a resource? If yes, add WriteRelationships (on create/grant) or DeleteRelationships
   (on delete/revoke) alongside the database operation.

2. **Permission checks**: Does this handler read, modify, or delete a resource on behalf
   of a user? If yes, add CheckPermission before accessing the resource. For list
   endpoints, use LookupResources -- not CheckPermission in a loop.

3. **Schema match**: If schema.zed exists, verify object types and relation/permission
   names in generated code match the schema exactly before inserting.

If unsure which operation to add: `/spicedb-dev:implement-spicedb`
```

Running `/spicedb-dev:plan` will offer to add this automatically.

## Usage

See [SUMMARY.md](SUMMARY.md) for the full guide: ideal workflow, entry points by
situation, and critical constraints.

### Short version

**Start here for any new project:**
```
/spicedb-dev:plan
```
Produces `authorization-plan.md` and adds an authorization snippet to `CLAUDE.md` so
permissions are considered automatically in every future session.

**Then, alongside every feature you build:**
```
/spicedb-dev:implement-spicedb-relationships  (writes: create/delete handlers)
/spicedb-dev:implement-spicedb-checks         (checks: any handler accessing a resource)
```
Both are required. SpiceDB returns NO_PERMISSION for everything until relationships are written.

**Periodically, to catch gaps:**
```
/spicedb-dev:audit-coverage
```

**Once the feature set is stable:**
```
/spicedb-dev:test-permissions
```

## Requirements

**SpiceDB instance** -- needed before implementation (Step 3 onward).

Fastest local setup:
```bash
spicedb serve-testing
```
This starts an in-memory SpiceDB instance on `localhost:50051` (gRPC) with no persistence -- suitable for development and testing. Data is lost on restart.

`serve-testing` needs no preshared key: it accepts any client-supplied token and gives
each unique token its own fully isolated, empty datastore. Connect with whatever token
you like:
```bash
zed schema write schema.zed --endpoint localhost:50051 --token my-token
```
Reusing the same token reconnects to that token's datastore; a different token starts
from empty -- convenient for running parallel test suites against one instance without
interference.

For a persistent local instance, see instructions in the [SpiceDB docs](https://authzed.com/docs/spicedb/concepts/datastores).

For hosted, self-service SpiceDB use [AuthZed Cloud](https://authzed.com/cloud)

**SpiceDB CLI (`zed`)** -- used by the schema-validator agent to validate `.zed` files. Install via [authzed.com/docs/spicedb/getting-started/installing-zed](https://authzed.com/docs/spicedb/getting-started/installing-zed).

## License

Apache-2.0

## Community

- [Discord](https://authzed.com/discord) - Chat with the SpiceDB community
- [Issues](https://github.com/authzed/authzed-marketplace/issues) - Questions, ideas, and feature requests
