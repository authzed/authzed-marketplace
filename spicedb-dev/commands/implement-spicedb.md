---
name: implement-spicedb
description: Add SpiceDB operations to application code (permission checks, relationship management, bulk operations, lookups)
argument-hint: "[checks|relationships]"
allowed-tools:
  - AskUserQuestion
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Task
---

# Implement SpiceDB Operations

For any application feature, **both operations are required**:
1. **Relationship writes** (WriteRelationships, DeleteRelationships) -- builds the graph
2. **Permission checks** (CheckPermission, LookupResources, etc.) -- queries the graph

If SpiceDB has no relationships for a resource type, all permission checks return
NO_PERMISSION regardless of what checks you add. Always implement writes first.

## Which do you need right now?

Use AskUserQuestion to ask:
- **Writes**: Add WriteRelationships / DeleteRelationships to application code
  (do this when adding: resource creation, membership grants, role assignments, deletions)
- **Checks**: Add CheckPermission / LookupResources / BulkCheckPermission to application code
  (do this when adding: access guards on endpoints, list filtering, permission lookups)
- **Both**: Start with writes (run writes command first, then checks)

After the user selects, carry out the corresponding command's instructions **in this
context**: read the file named below and follow it as written, exactly as if the user had
invoked it directly.

- **Writes** selected: `commands/implement-spicedb-relationships.md`
- **Checks** selected: `commands/implement-spicedb-checks.md`
- **Both** selected: follow `implement-spicedb-relationships.md` to completion first, then
  `implement-spicedb-checks.md` -- writes before checks, for the reason given above.

Both files are commands, not agents and not skills, so they cannot be dispatched with
`Task(subagent_type=...)`: that parameter resolves against the agent registry, which contains
only this plugin's three agents (`migration-analyzer`, `schema-validator`,
`checkpoint-identifier`), and the call fails with an unknown-agent error rather than running
anything. Read the file and follow it instead. If reading it fails, say so plainly and tell
the user to run `/spicedb-dev:implement-spicedb-relationships` or
`/spicedb-dev:implement-spicedb-checks` directly -- do not improvise the operation from this
file's summary, which is a router, not an implementation guide.
