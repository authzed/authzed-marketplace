---
name: plan
description: Plan a SpiceDB authorization implementation -- scopes complexity, identifies the right starting phase, and writes an authorization-plan.md artifact with phased next steps and specific commands to run
argument-hint: "[app-description]"
allowed-tools:
  - AskUserQuestion
  - Read
  - Write
  - Edit
  - Glob
---

# Plan Authorization Implementation

Guide the user from "I need to add authorization" to a concrete, phased implementation
plan. Produces `authorization-plan.md` as a shareable file artifact.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Understand the Application

Ask using AskUserQuestion (infer from context where possible):
- Application context: what does it do? what language? new or existing?
- Authorization scope: what resources need protection? who are the actors? is there a hierarchy?
- Starting point: does a schema already exist? does existing auth code exist? are there tests?

### Step 2: Identify Complexity

Based on the answers, classify the authorization model:

| Complexity | Indicators | Typical Pattern |
|---|---|---|
| Simple | 1-3 resource types, flat access, no groups | Basic RBAC |
| Moderate | 3-6 resource types, teams/groups, some hierarchy | ReBAC with inheritance |
| Complex | Many resources, multi-tenant, custom roles, caveats | Multi-tenant SaaS |

Complexity determines how much design work is needed in Phase 1 before generating artifacts.

### Step 3: Identify Which Phases Are Needed

Not every project starts at Phase 1. Match the user's starting point to the right entry phase:

| Starting Point | Entry Phase | Command |
|---|---|---|
| Just an idea, no schema | Phase 1: Design | `/spicedb-dev:design-model` |
| Has a permission model doc but no schema | Phase 2: Generate | `/spicedb-dev:generate-schema` |
| Has a schema but no implementation | Phase 3: Implement | `/spicedb-dev:implement-spicedb-checks` |
| Has implementation but no bootstrap | Phase 4: Bootstrap | `/spicedb-dev:implement-spicedb-relationships` (bulk patterns) |
| Has implementation but no tests | Phase 5: Test | `/spicedb-dev:test-permissions` |
| Has existing auth (role checks, Casbin, OPA, middleware) | Migration track | Start at Phase 1 with migration context (read existing auth code first) |

### Step 4: Produce the Implementation Plan

Structure your response using the Plan Output Template below. Tailor the content to
what you learned in Steps 1-3: omit phases the user has already completed, add scoping
notes for their domain, and flag decisions they will need to make.

### Step 5: Write to File

Write the completed plan to `authorization-plan.md` in the current directory (or a
path specified by the user). Tell the user where it was saved.

The file serves as a shared artifact -- recommend reviewing it with stakeholders
before beginning Phase 1, since changes are cheapest at the planning stage.

### Step 6: Add Authorization Snippet to CLAUDE.md

This step makes SpiceDB permissions ambient in every future Claude Code session --
Claude will consider writes and checks automatically whenever it generates handlers,
without the developer needing to invoke any command.

1. Use Glob to check whether `CLAUDE.md` exists in the current directory
2. If it exists, Read it and check whether it already contains an `## Authorization`
   section -- if so, skip this step
3. If no authorization section exists, use Edit to append the snippet below
4. If `CLAUDE.md` does not exist, use Write to create it with the snippet below
5. Tell the user: "Added authorization instructions to CLAUDE.md -- Claude will now
   consider SpiceDB writes and checks automatically when generating handlers."

**Snippet to add:**

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

## Plan Output Template

Structure your response as follows:

# Authorization Implementation Plan: [App Name]

## Context

- **Application**: [Brief description]
- **Language**: [Go / TypeScript / Python]
- **Starting point**: [Greenfield / Adding to existing app]
- **Authorization complexity**: [Simple / Moderate / Complex]
- **Pattern**: [RBAC / ReBAC / Multi-tenant / Other]

## Resources to Protect

[List the resources identified, e.g.:]
- `organization` -- top-level tenant boundary
- `project` -- belongs to organization
- `document` -- belongs to project; shared with users or teams

## Key Design Decisions to Make

[Flag decisions the user will face during design, e.g.:]
- [ ] Does `viewer` access on a project inherit down to documents?
- [ ] Can users be members of multiple organizations?
- [ ] Is there a concept of a "public" resource accessible without a relationship?

---

## Phase 1: Design the Permission Model

**Goal**: Define resources, relations, and permissions as a structured document.

**Skill**: `spicedb-schema-design` -- loads automatically when discussing schema design
**Command**: `/spicedb-dev:design-model [app description]`
**Input**: Answers to questions about your resources and access patterns
**Output**: `permission-model.md` -- structured model with resources, relations, permissions, and scenarios

**What to expect**: An interactive session (~15-30 min) that asks about your resources,
who can access them, and what they can do. The output drives all subsequent phases.

**Scoping notes**: [Domain-specific guidance, e.g. "focus on org/project hierarchy first,
add document-level sharing in a second pass"]

---

## Phase 2: Generate the SpiceDB Schema

**Goal**: Translate the permission model into a validated `.zed` schema file.

**Command**: `/spicedb-dev:generate-schema [permission-model.md]`
**Input**: `permission-model.md` from Phase 1
**Output**: `schema.zed` -- SpiceDB schema, auto-validated by the `schema-validator` agent

**What to expect**: The command reads the model, generates a `.zed` file, then automatically
runs the `schema-validator` agent to check syntax, flag anti-patterns, and suggest optimizations.
Review the validator output before moving on. To validate an existing schema at any time, use
`/spicedb-dev:validate-schema`.

After validation passes, deploy the schema with `zed schema write schema.zed` before proceeding to Phase 3. No relationships can be written until the schema is loaded.

---

## Phase 3: Implement Authorization in Application Code

**Goal**: Add SpiceDB permission checks, relationship writes, and lookups to your codebase.

**Skill**: `spicedb-best-practices` -- loads automatically when discussing client code
**Commands**:
- `/spicedb-dev:implement-spicedb-checks` -- for permission checks and lookups (CheckPermission, BulkCheckPermission, LookupResources, LookupSubjects)
- `/spicedb-dev:implement-spicedb-relationships` -- for writing and deleting relationships (WriteRelationships, DeleteRelationships)

**Input**: `schema.zed`, application code
**Output**: Updated application code with SpiceDB operations added

**What to expect**: Each command asks about the operation and target location, then uses the
`checkpoint-identifier` agent to analyze your codebase and suggest where to add the calls.
Code is generated in [language] with proper error handling and consistency management.

**Implementation order for [app type]**:
1. SpiceDB client initialization and connection management
2. Relationship writes -- create relationships when users/resources are created (use `implement-spicedb-relationships`)
3. Permission checks -- guard each resource endpoint (use `implement-spicedb-checks`)
4. Relationship deletes -- clean up on resource deletion or access revocation (use `implement-spicedb-relationships`)
5. [Bulk operations / lookups if needed -- use `implement-spicedb-checks`]

---

## Phase 4: Bootstrap Existing Data

**Goal**: Load all existing database relationships into SpiceDB before enabling enforcement.

**Reference**: `skills/spicedb-best-practices/references/bootstrapping.md`
**Command**: Use `/spicedb-dev:implement-spicedb-relationships` for the write
patterns; apply them in bulk to existing database records.

**Input**: Existing database (users, resources, memberships, ownership records)
**Output**: SpiceDB populated with relationships mirroring current database state

**What to expect**: For each resource type, write a script that reads existing records
from your database and calls `WriteRelationships` (with `OPERATION_TOUCH`) in batches.
Write in hierarchy order: top-level resources first (organizations), then child resources
(projects), then memberships and ownership. See `references/bootstrapping.md` for
ordering guidance and batch size limits.

**Critical**: Do not enable SpiceDB permission enforcement until the bootstrap is
complete and spot-checked. See `references/production-deploy.md` for the pre-production
gate checklist.

---

## Migration Track: Replacing Existing Authorization

If the application already has authorization (hardcoded role checks, RBAC middleware,
Casbin policies, OPA rules), use this approach instead of the standard phases:

**Step 1 -- Audit existing permission surface.** Before designing the SpiceDB model,
read the existing auth code to catalog all resource types, roles, and permission rules
currently enforced. Use `checkpoint-identifier` agent (`implement-spicedb-checks`
launches it automatically) to find all existing checks.

**Step 2 -- Map to SpiceDB model.** Design the SpiceDB schema to reflect the same
semantics as the existing system. Use `/spicedb-dev:design-model`. Key decision:
which existing roles map to SpiceDB relations, and where does hierarchy come from?

**Step 3 -- Run in shadow mode.** Implement SpiceDB writes (relationships) but keep
the existing checks enforcing access. Dual-write: every create/update/delete also
writes to SpiceDB. Bootstrap historical data (Phase 4 above).

**Step 4 -- Verify parity.** For a representative set of users and resources, compare
SpiceDB `CheckPermission` results against the existing system's decisions. They should
agree on every case before cutting over.

**Step 5 -- Cut over.** Replace the existing checks with SpiceDB `CheckPermission` calls.
Remove the old authorization code.

Reference: `skills/spicedb-best-practices/references/production-deploy.md` for cutover
checklist and rollback plan.

---

## Phase 5: Test Authorization

**Goal**: Generate comprehensive test coverage for the permission model.

**Skill**: `authorization-testing` -- loads automatically when discussing auth tests
**Command**: `/spicedb-dev:test-permissions [schema.zed] [output-dir]`
**Input**: `schema.zed`
**Output**: Test fixtures and test files in [language] framework

**What to expect**: The command generates positive tests (access granted), negative tests
(access denied), and hierarchical inheritance tests for every permission in the schema.
Output includes a fixture data file and test code ready to run.

---

## Summary: Commands to Run in Order

```
1. /spicedb-dev:design-model
2. /spicedb-dev:generate-schema
3. /spicedb-dev:validate-schema
4. zed schema write schema.zed   (deploy to SpiceDB instance)
5. /spicedb-dev:implement-spicedb-relationships
6. /spicedb-dev:implement-spicedb-checks
7. Bootstrap existing data into SpiceDB (see Phase 4: run WriteRelationships for all existing records)
8. /spicedb-dev:test-permissions
```

[If starting mid-workflow, note which phases are already done:]
> **Starting at Phase [N]**: Phases 1-[N-1] are already complete.

## Reference: Skills Loaded Automatically

These skills activate based on context -- no slash command needed:

| Situation | Skill Loaded |
|---|---|
| Discussing schema design or patterns | `spicedb-schema-design` |
| Discussing client code or consistency | `spicedb-best-practices` |
| Discussing test fixtures or test cases | `authorization-testing` |
