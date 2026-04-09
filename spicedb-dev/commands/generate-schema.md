---
name: generate-schema
description: Convert permission model to SpiceDB schema (.zed file)
argument-hint: "[model-file] [output-file]"
allowed-tools:
  - Read
  - Write
  - Glob
  - Grep
  - Task
---

# Generate SpiceDB Schema

Convert a permission model document to a SpiceDB schema (.zed file). After generation, automatically trigger the `schema-validator` agent to check the schema.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Locate Permission Model

If the user provided a model file path, use that. Otherwise:
1. Look for `permission-model.md` in the current directory
2. If not found, ask user for the model file location
3. If no model exists, suggest running `/spicedb-dev:design-model` first

### Step 2: Parse Permission Model

Read the permission model document and extract:
- Resource definitions with their relations
- Permission definitions with their expressions
- Hierarchical relationships
- Any special patterns (caveats, wildcards, etc.)

### Step 3: Select a Reference Example

Before writing the schema from scratch, identify the closest matching example in
`skills/spicedb-schema-design/examples/` and use it as a starting point or cross-reference.

**Pattern matching guide:**

| Permission model contains... | Start from |
|---|---|
| Simple writer/reader or owner/viewer roles | `basic-rebac.yaml` |
| IP allowlists, rate limits, time-based access | `caveats.yaml` |
| Groups with parent/child hierarchies | `docs-style-sharing.yaml` |
| SaaS subscription plans gating features | `entitlements.yaml` |
| Orgs, teams, repos with multiple role levels | `github.yaml` or `github-style.zed` |
| Platform-level superuser or admin bypass | `superuser.yaml` |
| Users defining their own roles at runtime | `user-defined-roles.yaml` |
| Roles with scoped inheritance across resource hierarchy | `google-iam.yaml` |
| Multi-tenant SaaS with tenant isolation | `multi-tenant-saas.zed` |
| Google Docs-style sharing | `google-docs-style.zed` |

Read the matching example file before generating. Use its structure, naming conventions,
and pattern as a foundation -- adapt to the user's model rather than generating from scratch.
If no single example matches, note which two are closest and combine patterns from both.

**Note for `.yaml` examples:** The schema is embedded inside the `schema:` key. Extract only the content of that block -- do not copy `relationships:`, `assertions:`, or other YAML sections into the `.zed` output.

### Step 4: Generate Schema

Create a `.zed` schema file with:

```
// [Application Name] Authorization Schema
// Generated from permission model

// Subject types
definition user {}

[Additional subject types if needed: team, service_account, etc.]

// Resource definitions
definition [resource_name] {
    relation [relation_name]: [subject_type]
    [Additional relations...]

    permission [permission_name] = [relation_expression]
    [Additional permissions...]
}

[Additional resource definitions...]
```

**Schema generation rules**:
- Use singular nouns for definition names (document, project, not documents, projects)
- Relations use subject type references (user, team#member, etc.)
- Permissions use relation expressions (+, &, ->, etc.)
- Add comments for clarity
- Typically use nouns for relation names, and verbs for permission names

### Step 5: Write to File

Write the schema to `schema.zed` (or a path specified by the user).

Do not add example relationships inline in the `.zed` file -- SpiceDB schema files
contain only definitions, relations, and permissions. Example relationships belong in
a `.yaml` assertion file. After the schema is written, suggest:

> To generate a `.yaml` assertion file with example relationships and validation tests,
> run `/spicedb-dev:test-permissions schema.zed`.

### Step 6: Validate Schema

After generating the schema, use the Task tool to launch the `schema-validator` agent:

```
Task(
    subagent_type="schema-validator",
    description="Validate generated schema",
    prompt="Validate the schema file at [path] and suggest improvements"
)
```

The agent will:
- Check syntax
- Validate best practices
- Suggest optimizations
- Identify potential issues

### Step 7: Report Results

Tell the user:
1. Where the schema was written
2. Summary of what was generated (X definitions, Y relations, Z permissions)
3. Validation results from the schema-validator agent
4. Next steps:
   - **Deploy the schema**: Before writing any relationships or running permission checks, apply the schema to your SpiceDB instance:
     ```bash
     zed schema write schema.zed --endpoint=localhost:50051 --token=<your-token>
     ```
     Or use the `WriteSchema` API call if you're bootstrapping programmatically.
   - Fix any validation issues
   - Use `/spicedb-dev:implement-spicedb-checks` for permission checks and lookups
   - Use `/spicedb-dev:implement-spicedb-relationships` for writing and deleting relationships
   - Use `/spicedb-dev:test-permissions` to generate test fixtures

## Schema Translation Guide

### Relations

**Model**:
```
- `owner`: User who owns the document
- `editor`: User who can edit
- `viewer`: User who can view
```

**Schema**:
```
relation owner: user
relation editor: user
relation viewer: user
```

### Permissions

**Model**:
```
- `view`: viewer + editor + owner
- `edit`: editor + owner
- `delete`: owner
```

**Schema**:
```
permission view = viewer + editor + owner
permission edit = editor + owner
permission delete = owner
```

### Hierarchical Relations

**Model**:
```
- `parent`: Organization that contains this project
- Editors in parent org can edit this project
```

**Schema**:
```
relation parent: organization
permission edit = editor + owner + parent->admin
```

### Union Types

**Model**:
```
- `viewer`: Can be a user or a team member
```

**Schema**:
```
relation viewer: user | team#member
```

### Wildcards (Public Access)

**Model**:
```
- `public_access`: Anyone can view if public
```

**Schema**:
```
relation public_access: user:*
permission view = owner + viewer + public_access
```

### Caveats (Time-Limited)

**Model**:
```
- `temp_viewer`: Temporary viewer with expiration
```

**Schema**:
```
caveat not_expired(expiration timestamp) {
    expiration > now()
}

definition document {
    relation temp_viewer: user with not_expired
    permission view = owner + temp_viewer
}
```

## Error Handling

If the model is unclear or missing information:
1. Load the `spicedb-schema-design` skill for guidance
2. Make reasonable assumptions based on common patterns
3. Add TODO comments in the schema for areas needing clarification
4. Ask the user to review and provide missing details

## Example Output

```zed
// Document Management Authorization Schema
// Generated from permission model on 2024-02-09

definition user {}

definition organization {
    relation admin: user
    relation member: user

    permission view = member + admin
    permission manage = admin
}

definition document {
    relation org: organization
    relation owner: user
    relation editor: user
    relation viewer: user

    permission view = viewer + editor + owner + org->member
    permission edit = editor + owner + org->admin
    permission delete = owner + org->admin
}

// Example usage:
//
// 1. Create organization and add members
//    WriteRelationships(organization:acme#admin@user:alice)
//    WriteRelationships(organization:acme#member@user:bob)
//
// 2. Create document
//    WriteRelationships(document:doc-1#org@organization:acme)
//    WriteRelationships(document:doc-1#owner@user:alice)
//
// 3. Check permissions
//    CheckPermission(user:alice, delete, document:doc-1)  // true (owner)
//    CheckPermission(user:bob, view, document:doc-1)      // true (org member)
//    CheckPermission(user:bob, delete, document:doc-1)    // false (not owner/admin)
```

## Tips

- Keep the schema clean and well-commented
- If no local example matches, the canonical AuthZed schemas are at:
  https://github.com/authzed/examples/tree/main/schemas

## Notes

- Load `spicedb-schema-design` skill if you need pattern guidance during generation
- The generated schema is a starting point; users will refine it over time
