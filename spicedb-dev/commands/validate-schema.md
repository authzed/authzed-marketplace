---
name: validate-schema
description: Validate a SpiceDB schema (.zed file) using the schema-validator agent
argument-hint: "[schema-file]"
allowed-tools:
  - Read
  - Glob
  - Task
  - AskUserQuestion
---

# Validate SpiceDB Schema

Validate any SpiceDB `.zed` schema file using the `schema-validator` agent. Works on
any `.zed` file -- plugin-generated, manually edited, or migrated from another system.

## Process

### Step 1: Locate Schema

If the user provided a schema file path, use that. Otherwise:
1. Search for `*.zed` files in the current working directory
2. If exactly one is found, use it
3. If multiple are found, ask the user which to validate using AskUserQuestion
4. If none are found, ask the user for the schema file location

### Step 2: Launch schema-validator Agent

Use the Task tool to launch the `schema-validator` agent:

```
Task(
    subagent_type="spicedb-dev:schema-validator",
    description="Validate SpiceDB schema",
    prompt="Validate the schema file at [path] and suggest improvements"
)
```

The agent will:
- Check syntax for `.zed` schema language validity
- Validate best practices (naming conventions, relation/permission structure)
- Suggest optimizations
- Identify potential issues (anti-patterns, missing type annotations, etc.)

### Step 3: Present Results

Present the validation results to the user with:
1. **Summary**: Pass/fail and number of issues found
2. **Issues**: List each issue with its severity (error, warning, suggestion) and location
3. **Next steps**:
   - If issues found: describe what to fix and how
   - If no issues: suggest running `/spicedb-dev:generate-schema` (if no schema yet)
     or `/spicedb-dev:implement-spicedb-checks` to add authorization code

## Tips

- This command works on any `.zed` file, not just plugin-generated schemas
- Run after manually editing a schema to catch regressions
- Run after migrating a schema from another system to verify correctness
- The `generate-schema` command runs this automatically after generation

## Notes

- The `schema-validator` agent performs static analysis only -- it does not connect to a live SpiceDB instance
- For runtime validation with actual relationships, use `spicedb serve-testing` (see `authorization-testing` skill)
