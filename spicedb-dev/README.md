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

### Skills

- **authorization-planner** - Entry point; routes to the right command or skill based on where you are in the workflow
- **spicedb-schema-design** - Schema patterns, anti-patterns, and design decisions
- **spicedb-best-practices** - Client library usage, consistency models, error handling, performance
- **authorization-testing** - Test fixture generation and integration testing patterns

### Agents

- **schema-validator** - Validates SpiceDB schemas and suggests improvements
- **checkpoint-identifier** - Analyzes code to identify where permission checks should go

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

## Quick Setup: Make Permissions Ambient

After installing, add the following to your project's `CLAUDE.md`. This is the single
most effective step -- it makes Claude consider SpiceDB writes and checks automatically
whenever it generates or modifies handlers, without you needing to invoke any command.

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
spicedb serve-testing --grpc-preshared-key test
```
This starts an in-memory SpiceDB instance on `localhost:50051` with no persistence -- suitable for development and testing. Data is lost on restart.

For a persistent local instance, see instructions in the [SpiceDB docs](https://authzed.com/docs/spicedb/concepts/datastores).

For hosted, self-service SpiceDB use [AuthZed Cloud](https://authzed.com/cloud)

**SpiceDB CLI (`zed`)** -- used by the schema-validator agent to validate `.zed` files. Install via [authzed.com/docs/spicedb/getting-started/installing-zed](https://authzed.com/docs/spicedb/getting-started/installing-zed).

## License

Apache-2.0

## Community

- [Discord](https://authzed.com/discord) - Chat with the SpiceDB community
- [Issues](https://github.com/authzed/authzed-marketplace/issues) - Questions, ideas, and feature requests
