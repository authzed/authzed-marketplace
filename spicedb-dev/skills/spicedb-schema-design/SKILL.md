---
name: SpiceDB Schema Design
description: Use when designing SpiceDB schemas, modeling permission hierarchies, or
  choosing between RBAC/ReBAC/multi-tenant patterns - guides resource/relation/permission
  design with pattern examples and anti-pattern checks
---

# SpiceDB Schema Design

This skill guides the design of effective SpiceDB schemas for authorization systems. Use this when modeling permissions, relationships, and access control for applications.

## Overview

SpiceDB uses a schema language to define:
- **Resources** (objects to protect, like documents or organizations)
- **Relations** (connections between resources and subjects, like ownership or membership)
- **Permissions** (computed access rights based on relations, like view or edit)

Good schema design requires understanding the application's domain, identifying resources and their relationships, and modeling permissions that reflect business requirements.

## Quick Reference

| Need to... | Read This |
|-----------|-----------|
| Find a pattern for your use case | `references/patterns.md` |
| Check if a design is an anti-pattern | `references/anti-patterns.md` |
| See a working GitHub-style schema | `examples/github-style.zed` or `examples/github.yaml` |
| See a Google Docs-style schema | `examples/google-docs-style.zed` or `examples/docs-style-sharing.yaml` |
| See a multi-tenant SaaS schema | `examples/multi-tenant-saas.zed` |
| See a minimal two-role ReBAC schema | `examples/basic-rebac.yaml` |
| See caveats / ABAC (IP allowlist, rate limits) | `examples/caveats.yaml` |
| See SaaS feature entitlements / plan gating | `examples/entitlements.yaml` |
| See platform-level superuser / admin bypass | `examples/superuser.yaml` |
| See user-defined roles (like JIRA project roles) | `examples/user-defined-roles.yaml` |
| See Google IAM-style role hierarchy | `examples/google-iam.yaml` |
| See AI agents delegated access on behalf of a user | `examples/ai-agents.yaml` |
| See a schema split across multiple validation files | `examples/multiple-validation-files/` |
| Evolve an existing schema safely | `references/schema-evolution.md` |

## When to Use This Skill

Use this skill when:
- Starting a new SpiceDB implementation
- Modeling permissions for a feature or module
- Refactoring an existing authorization system
- User asks about relation vs permission design choices
- Clarifying how to represent hierarchies or role-based access

## Starting a Design Session

To run an interactive permission model design session, use `/spicedb-dev:design-model`.
It scans your codebase for existing models, asks about resources and access patterns, and
produces `permission-model.md`. This skill provides pattern guidance and anti-pattern checks
during that session -- no separate invocation needed.

## Enable Type Checking (Recommended)

Add `use typechecking` at the top of your schema to enable compile-time type checking of
permissions. Annotate permissions with their return subject type:

```
use typechecking

definition document {
    relation viewer: user
    relation admin: serviceaccount

    permission view: user = viewer      // Annotated return type
    // permission edit: user = admin   // This FAILS type-check: admin is serviceaccount, not user
}
```

Without `use typechecking`, `permission edit = admin & viewer` silently compiles but always
returns false when `admin` and `viewer` are different subject types. Type checking surfaces
this at schema write time.

## Common Mistakes to Avoid

See `references/anti-patterns.md` for the full anti-pattern reference with ❌/✅ examples.
Key anti-patterns: permission explosion, confusing relations with permissions, excessive nesting,
wildcard overuse, caveat misuse, schema bloat, missing hierarchy, circular dependencies.

## Red Flags

If you find yourself:
- Unsure which pattern fits the domain → Read `references/patterns.md`
- Debating whether a design is an anti-pattern → Read `references/anti-patterns.md`
- Speculating about SpiceDB syntax → Check the official SpiceDB schema language docs
- Designing something more complex than the examples → Start simpler and add only what's needed

## What This Skill Does NOT Do

- Generate `.zed` files -- use `/spicedb-dev:generate-schema` for that
- Implement client code -- use `/spicedb-dev:implement-spicedb-checks` or `/spicedb-dev:implement-spicedb-relationships` for that
- Write or run tests -- use `/spicedb-dev:test-permissions` for that
- Teach SpiceDB client library usage -- use the `spicedb-best-practices` skill for that

## Iterative Design Workflow

Schema design is iterative. Follow this workflow:

1. **Start simple**: Model core resources and basic permissions
2. **Validate with scenarios**: Write example checks (can user X access resource Y?)
3. **Identify gaps**: Find missing permissions or incorrect inheritance
4. **Refine**: Add relations, adjust permissions, introduce hierarchies
5. **Test**: Use SpiceDB's consistency checker and validation tools
6. **Evolve**: Schema can be versioned and migrated as requirements change

## Additional Resources

### Reference Files

For detailed patterns and advanced techniques, consult:
- **`references/patterns.md`** - Comprehensive pattern library with detailed examples
- **`references/anti-patterns.md`** - Common mistakes and how to fix them

### Example Schemas

Working schema examples in `examples/`. Files ending in `.yaml` use the `zed validate`
format and include relationships, assertions, and validation alongside the schema --
they can be run with `zed validate <file.yaml>`. Files ending in `.zed` are schema-only.

**Plugin-authored examples:**
- **`examples/github-style.zed`** - Repository permissions model
- **`examples/google-docs-style.zed`** - Document sharing model
- **`examples/multi-tenant-saas.zed`** - SaaS with tenant isolation

**AuthZed community examples** (source: https://github.com/authzed/examples/tree/main/schemas):
- **`examples/basic-rebac.yaml`** - Minimal writer/reader pattern
- **`examples/caveats.yaml`** - ABAC with IP allowlists and rate limiting
- **`examples/docs-style-sharing.yaml`** - Group hierarchies (parent-of and child-of patterns)
- **`examples/entitlements.yaml`** - SaaS plan/feature gating
- **`examples/github.yaml`** - GitHub-style orgs, teams, repos (canonical AuthZed version)
- **`examples/superuser.yaml`** - Platform admin bypass pattern
- **`examples/user-defined-roles.yaml`** - Runtime-created roles (JIRA-style)
- **`examples/google-iam.yaml`** - Google IAM-style role hierarchy (Spanner IAM, full schema)
- **`examples/ai-agents.yaml`** - AI agents inherit delegated view access from a user, but never gain write access
- **`examples/multiple-validation-files/`** - Not a modeling pattern -- demonstrates the `zed validate` technique of splitting one schema (`schema.zed`) across multiple independent validation files (`validations/*.yaml`) run together with `zed validate validations/*`. Directory-based, unlike every other example here; requires zed v0.25.0+.

### External Resources

- [SpiceDB Schema Language](https://authzed.com/docs/reference/schema-lang) - Official language reference
- [SpiceDB Playground](https://play.authzed.com/) - Interactive schema testing
- [Modeling Patterns](https://authzed.com/docs/guides/schema) - Official modeling guides

## Testing Your Schema

After designing, validate the schema:

1. **Syntax validation**: Use `zed validate schema.zed`
2. **Consistency check**: Run SpiceDB's consistency checker
3. **Scenario testing**: Write test assertions for expected access patterns
4. **Performance review**: Check for expensive operations (deeply nested arrows)

Use the `schema-validator` agent to automatically check schemas for issues and best practices.

---

**Workflow summary:** Identify resources → Map relations → Define permissions → Consider caveats → Validate with scenarios → Refine iteratively. Start simple; add complexity only when a concrete scenario requires it.
