---
name: design-model
description: Interactive permission model design session for SpiceDB schemas
argument-hint: "[app-description]"
allowed-tools:
  - AskUserQuestion
  - Read
  - Write
  - Glob
  - Grep
---

# Design Permission Model

Guide the user through designing a comprehensive permission model for their application, then output a structured model document.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each task `in_progress` when starting and `completed` when done.

## What You're Building

Before we start, here's what this command produces. The `examples/github-style.zed` file shows a GitHub-style model: organizations contain repositories, teams have members, and permissions like `read`, `write`, and `admin` are computed from relationships. We'll build something similar for your application.

Before presenting this orientation, read `skills/spicedb-schema-design/examples/github-style.zed` and reference specific resource types and permissions from it in the explanation above.

## Process

### Step 1: Understand the Application

First, understand what the application does and its authorization requirements.

**Before asking questions: scan for existing models.**

Use Glob to search for these patterns in the working directory:
- `**/*.prisma` (Prisma schema)
- `**/models.py`, `**/models/**/*.py` (Django)
- `**/internal/models/**/*.go`, `**/*_model.go` (Go)
- `**/*.entity.ts`, `**/*.model.ts` (TypeScript/NestJS)
- `**/db/schema.rb` (Rails)
- `**/*.graphql`, `**/*.gql` (GraphQL)
- `**/schema.sql`, `**/migrations/**/*.sql` (SQL schemas)

If model files are found, read them and extract entity and table names. Use these as the
starting point for the resource inventory: reference specific entity names in your questions
rather than asking abstractly. For example, instead of "What are your main resources?",
ask "I can see you have `Document`, `Project`, and `Organization` models -- are these the
main things that need permission control, or are there others?"

If no model files are found, proceed with the question-driven approach below.

Ask the user:
1. **Application type**: What kind of application is this? (SaaS, internal tool, API, etc.)
2. **User types**: Who will use the system? (end users, admins, service accounts, etc.)
3. **Resources**: What needs to be protected? (documents, projects, files, etc.)
4. **Access patterns**: How do users typically interact with resources?

Load the `spicedb-schema-design` skill to guide the design process.

### Step 2: Identify Resources

For each resource type the user mentions, determine:
- Resource name (singular noun, e.g., "document", "project")
- What it represents
- Whether it has parent/child relationships
- Whether it's hierarchical

Use AskUserQuestion to present options and gather details:
- Does this resource have a parent? (organizations, folders, etc.)
- Can this resource contain other resources?
- Is there a hierarchy or nesting?

### Step 3: Identify Relationships

For each resource, identify the relationships (who can be connected to it):
- Direct assignments: owner, editor, viewer, etc.
- Group relationships: teams, departments, etc.
- Hierarchical relationships: parent, organization, etc.

Ask:
- Who can be an owner/admin of this resource?
- Are there different levels of access? (read-only, read-write, admin)
- Can groups or teams access this resource?
- Should access inherit from a parent resource?

### Step 4: Model Permissions

For each resource, identify the permissions (actions users can perform):
- Standard actions: view, edit, delete, share
- Resource-specific actions: approve, publish, archive, etc.

Ask:
- What actions can users perform on this resource?
- Which relationships grant which permissions?
- Should some permissions combine multiple relationships? (e.g., view = viewer + editor + owner)

### Step 5: Consider Special Cases

Check for special authorization patterns:
- **Public access**: Can resources be public? (link sharing, user:*)
- **Domain sharing**: Share with all users in a domain?
- **Time-limited access**: Temporary permissions with expiration?
- **Attribute-based**: Permissions based on resource attributes?

**Caveat vs Relation decision** -- ask for each "special case" pattern identified:

> Is the condition evaluated from dynamic request context at check time (current timestamp,
> client IP address, JWT claims, subscription tier fetched at request time)?
> **Use a caveat.** The condition is not a persistent relationship.
>
> Or is it a stable, persistent fact about who has what role or owns what resource?
> **Use a relation.** Write it as a relationship when the state changes.

**Critical constraint to share with the user:** Caveats cannot be used in `LookupResources`
or `LookupSubjects` calls. If the design requires listing all resources a user can access
(e.g., a list endpoint), avoid caveats on that permission's primary access path and use
relations instead.

**Default:** If uncertain, model as a relation. Add a caveat only when dynamic request
context is truly required and list operations are not needed on that permission.

### Step 6: Generate Model Document

Create a structured permission model document with:

```markdown
# Permission Model: [Application Name]

## Overview
[Brief description of the application and its authorization needs]

## Resources

### [Resource Name 1]
**Description**: [What this resource represents]
**Hierarchy**: [Parent resources, if any]

**Relations**:
- `owner`: [Description of owner relationship]
- `editor`: [Description of editor relationship]
- `viewer`: [Description of viewer relationship]
- [Additional relations...]

**Permissions**:
- `view`: [Who can view] = [relation expression]
- `edit`: [Who can edit] = [relation expression]
- `delete`: [Who can delete] = [relation expression]
- [Additional permissions...]

### [Resource Name 2]
[Same structure...]

## Hierarchies

[Describe any hierarchical relationships between resources]

## Special Patterns

- **Public Access**: [How public access works, if applicable]
- **Domain Sharing**: [How domain sharing works, if applicable]
- **Time-Limited Access**: [How temporary access works, if applicable]

## Access Scenarios

### Scenario 1: [Name]
**Setup**: [Describe the relationship setup]
**Expected Access**: [Who can do what]

### Scenario 2: [Name]
[Same structure...]

## Next Steps

1. Review this model with stakeholders
2. Use `/spicedb-dev:generate-schema` to create SpiceDB schema
3. Use `/spicedb-dev:implement-spicedb-checks` for permission checks and lookups
4. Use `/spicedb-dev:implement-spicedb-relationships` for writing and deleting relationships
5. Use `/spicedb-dev:test-permissions` to generate test fixtures
```

Write this document to a file named `permission-model.md` in the current directory (or a location specified by the user).

## Example Usage

```
User: "/spicedb-dev:design-model"

Claude:
"I'll help you design a permission model for your application.

First, let me understand your application:
1. What kind of application is this? (SaaS, internal tool, API, etc.)
2. What are the main resources that need authorization? (documents, projects, etc.)
3. Who are the main user types? (end users, admins, teams, etc.)"

[Interactive design session...]

Claude:
"I've created your permission model in `permission-model.md`.

Next steps:
- Review the model
- Run `/spicedb-dev:generate-schema` to create the SpiceDB schema
"
```

## Notes

- Prioritize the user's domain vocabulary in the output -- use their terms for resources
  (e.g., "workspace", "ticket") rather than generic SpiceDB terms
- Use AskUserQuestion for binary choices; for open-ended questions, infer from context
  and confirm inline rather than stopping the session
