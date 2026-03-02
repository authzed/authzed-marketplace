---
name: audit-coverage
description: Audit SpiceDB permission coverage -- reads your schema and scans the codebase to produce a matrix showing which permissions have code checks and which are unprotected
argument-hint: "[schema-file]"
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Task
  - AskUserQuestion
---

# Audit Authorization Coverage

Produce a coverage matrix: for every permission declared in the schema, report whether
an explicit CheckPermission call exists in application code.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Locate Schema

- Use provided path if given
- Otherwise glob for `*.zed` in current directory
- If multiple found, ask with AskUserQuestion

### Step 2: Parse Schema for Permission Inventory

Read the .zed file. Extract every `definition` name and every `permission` name
declared within each definition. Build a list of (definition, permission) pairs.
Example:
  document:     [view, edit, delete]
  organization: [view, manage]
  project:      [view, edit, delete, manage]

### Step 3: Detect Language

Grep for CheckPermission call patterns to detect language:
- Go:         `CheckPermission(ctx`
- TypeScript: `checkPermission(`
- Python:     `CheckPermission(CheckPermissionRequest`

Ask if ambiguous.

### Step 4: Inventory Existing Checks

Grep the codebase for all existing CheckPermission calls. For each, extract the
ObjectType and Permission string values.

Go patterns:
  ObjectType: `ObjectType:\s+"([^"]+)"`
  Permission: `Permission:\s+"([^"]+)"`

TypeScript patterns:
  objectType: `objectType:\s+'([^']+)'`
  permission: `permission:\s+'([^']+)'`

Python patterns:
  object_type: `object_type="([^"]+)"`
  permission:  `permission="([^"]+)"`

Record: (object_type, permission, file, approximate line) for each match.

### Step 5: Inventory Relationship Writes

Grep the codebase for `WriteRelationships` and `DeleteRelationships` calls. For each,
extract the `ObjectType` of the resource being written.

Go pattern:  `ObjectType:\s+"([^"]+)"` near `WriteRelationships`
TypeScript:  `objectType:\s+'([^']+)'` near `writeRelationships`
Python:      `object_type="([^"]+)"` near `WriteRelationships`

For each resource type in the schema, check:
- ✅ Has write: at least one WriteRelationships call uses this object type
- ❌ No write: no WriteRelationships call found for this resource type
  (SpiceDB will have no relationships to evaluate -- all checks will return NO_PERMISSION)
- ✅ Has delete: at least one DeleteRelationships call cleans up this resource type on deletion
- ⚠️ Missing delete: resource type has no DeleteRelationships cleanup
  (orphaned relationships remain after resource deletion)

### Step 6: Check List Endpoint Coverage

Grep for list/index endpoint handlers (patterns: `GET /[a-z]+\b` without `/:id`,
`router\.(get|GET).*"/[a-z]+"`, `@Get("/[a-z]+")`).

For each list endpoint found, check whether it contains a `LookupResources` or
`BulkCheckPermission` call. If neither is found, the endpoint likely returns
unfiltered results.

- ✅ Filtered: LookupResources or BulkCheckPermission call found in handler
- ⚠️ Unfiltered: no filtering call found -- users may see resource IDs they cannot access

### Step 7: Cross-Reference

For each (definition, permission) from the schema:
- ✅ Covered: at least one CheckPermission call uses this exact (object_type, permission) pair
- ❌ Not found: no call found

Before flagging a permission as ❌ Not in code, check whether it appears *only* as a
sub-expression within other permissions' definitions in the schema (e.g., used in a
`+ / & / ->` expression but never as a standalone permission). If so, mark it:
- ℹ️ Compositional: used only within other permissions' expressions; no direct
  CheckPermission call is expected or needed

Also flag:
- ⚠️ Unknown: (object_type, permission) pairs found in code that do not exist in the schema
  (potential typo or the schema evolved without updating the code)

### Step 8: Endpoint Scan (Optional)

Ask the user: "Do you want a scan for completely unprotected endpoints (no permission
check of any kind)? This invokes the checkpoint-identifier agent and takes longer."

If yes, launch the `checkpoint-identifier` agent:
  prompt: "Scan all HTTP route handlers and API endpoints in the codebase. Identify
  which ones have no SpiceDB CheckPermission call of any kind -- not wrong permission,
  but zero permission check. List each unprotected endpoint with its handler location."

### Step 9: Produce Coverage Report

Output structured markdown:

---
# Authorization Coverage Report

## Permission Coverage

| Resource | Permission | Status | Code Location |
|---|---|---|---|
| document | view | ✅ Covered | handlers/document.go:48 |
| document | delete | ❌ Not in code | -- |

**Legend:** ✅ Covered | ❌ Not in code | ⚠️ Unknown (schema drift) | ℹ️ Compositional (sub-expression only)

## Coverage Summary
- X of Y permissions (N%) have explicit checks in code
  (compositional permissions excluded from count)
- Uncovered: [list]

## Potential Schema Drift (checks in code not matching schema)
- "read" at handlers/doc.go:102 -- schema has "view" (possible typo)

## Unprotected Endpoints
[If Step 6 ran: findings from checkpoint-identifier]
[If skipped: "Rerun with endpoint scan enabled to find completely unprotected routes."]

## Relationship Write Coverage

| Resource | Has Writes | Has Deletes | Notes |
|---|---|---|---|
| document | ✅ WriteRelationships found | ⚠️ No DeleteRelationships | Orphaned relationships on deletion |
| organization | ✅ | ✅ | |

## List Endpoint Coverage

| Endpoint | Handler (file:line) | Filtered? |
|---|---|---|
| GET /api/documents | ListDocuments in handlers/doc.go:22 | ⚠️ Unfiltered |

## Recommended Next Steps

For each ❌ uncovered permission (no CheckPermission call):
  /spicedb-dev:implement-spicedb-checks

For each ❌ No write resource type (WriteRelationships missing):
  1. /spicedb-dev:implement-spicedb-relationships  (add writes to application code)
  2. Bootstrap existing data: skills/spicedb-best-practices/references/bootstrapping.md
     (SpiceDB is empty for this resource type -- all checks return NO_PERMISSION)

For each ⚠️ Unfiltered list endpoint:
  /spicedb-dev:implement-spicedb-checks  (implement LookupResources for the list handler)
---

**Important caveat in the output:** Note clearly that this analysis uses pattern
matching against string literals. Permission values stored in variables or computed
at runtime will not be detected.
