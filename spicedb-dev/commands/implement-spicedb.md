---
name: implement-spicedb
description: Add SpiceDB operations to application code (permission checks, relationship management, bulk operations, lookups)
argument-hint: "[checks|relationships]"
allowed-tools:
  - AskUserQuestion
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

After the user selects, use the Task tool to invoke the appropriate command:
- Writes selected: `Task(subagent_type="implement-spicedb-relationships", ...)`
- Checks selected: `Task(subagent_type="implement-spicedb-checks", ...)`
- Both selected: explain to run writes command first, then checks command
