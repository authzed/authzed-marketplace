# SpiceDB Schema Anti-Patterns

Common mistakes in SpiceDB schema design and how to avoid them.

## Anti-Pattern 1: Permission Explosion

### ❌ Problem

Creating too many fine-grained relations instead of using computed permissions.

```
definition document {
    relation owner: user
    relation can_read: user
    relation can_write: user
    relation can_delete: user
    relation can_share: user
    relation can_comment: user
    relation can_export: user
    relation can_print: user
}
```

### Why It's Bad

- Every action requires a separate relationship write
- Increases storage and query overhead
- Makes permission management complex
- Hard to maintain consistency (user with can_write but not can_read?)

### ✅ Solution

Use role-based relations and compute permissions.

```
definition document {
    relation owner: user
    relation editor: user
    relation commenter: user
    relation viewer: user

    permission read = viewer + commenter + editor + owner
    permission write = editor + owner
    permission delete = owner
    permission share = owner
    permission comment = commenter + editor + owner
    permission export = editor + owner
    permission print = viewer + commenter + editor + owner
}
```

### Benefits

- Fewer relationship writes (one write gives multiple permissions)
- Clear role hierarchy
- Easier to reason about
- Better performance

---

## Anti-Pattern 2: Confusing Relations with Permissions

### ❌ Problem

Using "can_" or "has_" prefixes for relations, or using action verbs.

```
definition document {
    relation can_view: user          // Bad: sounds like permission
    relation has_access: user        // Bad: vague
    relation views: user             // Bad: action verb
    relation deletes: user           // Bad: action verb

    permission view = can_view
}
```

### Why It's Bad

- Confuses the intent (is it a relation or permission?)
- Unclear whether to write to relation or check permission
- Naming collision between relations and permissions

### ✅ Solution

Relations are nouns (roles/relationships), permissions are verbs (actions).

```
definition document {
    relation viewer: user            // Good: role noun
    relation editor: user            // Good: role noun
    relation owner: user             // Good: relationship noun

    permission view = viewer + editor + owner    // Good: action verb
    permission edit = editor + owner
    permission delete = owner
}
```

### Benefits

- Clear semantic distinction
- Obvious when to write relations vs check permissions
- Consistent with SpiceDB conventions

---

## Anti-Pattern 3: Excessive Nesting

### ❌ Problem

Deep arrow chains that are hard to understand and slow to evaluate.

```
permission view =
    parent->parent->parent->parent->admin +
    parent->parent->parent->admin +
    parent->parent->admin +
    parent->admin +
    owner
```

### Why It's Bad

- Performance degrades with depth
- Hard to understand permission inheritance
- Difficult to debug access issues
- Increases query latency

### ✅ Solution

Use recursive definitions for hierarchies.

```
definition folder {
    relation parent: folder
    relation owner: user
    relation viewer: user

    // Recursive: automatically handles arbitrary depth
    permission view = viewer + owner + parent->view
    permission edit = owner + parent->edit
}
```

### Benefits

- Handles arbitrary depth cleanly
- Easier to understand
- Better performance (SpiceDB optimizes recursion)
- More maintainable

---

## Anti-Pattern 4: Wildcard Overuse

### ❌ Problem

Using wildcards for permissions that should be explicit.

```
definition sensitive_document {
    relation delete_access: user:*    // Bad: everyone can delete!
    relation admin: user:*            // Bad: everyone is admin!

    permission delete = delete_access
    permission admin_access = admin
}
```

### Why It's Bad

- Grants access to literally everyone
- Security vulnerability
- Hard to audit (no explicit grants)
- Bypasses authorization intent

### ✅ Solution

Use wildcards only for truly public resources.

```
definition document {
    relation owner: user
    relation viewer: user
    relation public_access: user:*    // OK: for public view only

    permission view = owner + viewer + public_access  // Public can view
    permission edit = owner                            // But not edit
    permission delete = owner                          // Or delete
}
```

### When Wildcards Are OK

- Public read-only access
- Unauthenticated viewing
- Default organization-wide permissions (with care)

### When to Avoid Wildcards

- Any write operations
- Sensitive data access
- Administrative functions
- Audit-critical actions

---

## Anti-Pattern 5: Caveat Misuse

### ❌ Problem 1: Using Caveats for Static Logic

```
caveat is_editor(role string) {
    role == "editor"
}

relation user_access: user with is_editor
```

**Why bad:** Static role checks belong in relations, not caveats.

### ❌ Problem 2: Complex Business Logic in Caveats

```
caveat can_access(
    user_age int,
    user_location string,
    user_subscription string,
    resource_tier string,
    time_of_day int
) {
    user_age >= 18 &&
    user_location in ["US", "CA", "UK"] &&
    (user_subscription == "premium" || resource_tier == "free") &&
    time_of_day >= 9 && time_of_day <= 17
}
```

**Why bad:**
- Hard to maintain
- Difficult to test
- Application logic leaking into authorization
- Poor observability

### ✅ Solution

Use caveats for simple, dynamic context checks only.

```
// Good: Simple expiration check
caveat not_expired(expiration timestamp) {
    expiration > now()
}

// Good: Simple IP check (user_ip is ipaddress type, passed as context at check time)
caveat ip_allowed(user_ip ipaddress, cidr string) {
    user_ip.in_cidr(cidr)
}

relation temp_viewer: user with not_expired
relation admin: user with ip_allowed
```

### Caveat Guidelines

**Use caveats for:**
- Time-based conditions (expiration, time windows)
- Request context (IP address, user agent)
- Simple attribute checks (2-3 conditions max)

**Don't use caveats for:**
- Static relationships (use relations)
- Complex business logic (use application code)
- Multi-step workflows (use application state)
- Computations requiring external data

---

## Anti-Pattern 6: Schema Bloat

### ❌ Problem

Creating separate definitions for every minor variation.

```
definition public_document {
    relation owner: user
    permission view = owner
}

definition private_document {
    relation owner: user
    relation viewer: user
    permission view = owner + viewer
}

definition shared_document {
    relation owner: user
    relation viewer: user
    relation link_access: user:*
    permission view = owner + viewer + link_access
}
```

### Why It's Bad

- Code duplication
- Hard to maintain consistency
- Confusing for developers (which type to use?)
- Difficult to change behavior across all types

### ✅ Solution

Use one definition with optional relations.

```
definition document {
    relation owner: user
    relation viewer: user          // Optional: not set for public docs
    relation link_access: user:*   // Optional: not set for private docs

    permission view = owner + viewer + link_access
}
```

### Benefits

- Single definition
- Flexibility through relation presence/absence
- Easier to maintain
- Consistent behavior

---

## Anti-Pattern 7: Missing Hierarchy

### ❌ Problem

Replicating permissions instead of inheriting from parent.

```
definition project {
    relation owner: user
}

definition document {
    relation project_owner: user  // Duplicating project owner
    relation owner: user

    permission edit = owner + project_owner
}
```

### Why It's Bad

- Must manually sync project owners to documents
- Denormalization increases storage
- Permission changes require updates to all children
- Prone to inconsistency

### ✅ Solution

Use hierarchical inheritance.

```
definition project {
    relation owner: user
    relation admin: user

    permission manage = owner + admin
}

definition document {
    relation project: project
    relation owner: user

    permission edit = owner + project->admin
    permission view = owner + project->manage
}
```

### Benefits

- Automatic inheritance
- Single source of truth
- Changes propagate automatically
- Normalized data

---

## Anti-Pattern 8: Circular Dependencies

### ❌ Problem

Definitions that reference each other in circles.

```
definition team {
    relation parent: organization
    relation member: user

    permission view = member + parent->team_member
}

definition organization {
    relation team: team
    relation admin: user

    permission team_member = team->member  // Circular!
}
```

### Why It's Bad

- Can cause infinite loops
- SpiceDB may reject schema
- Hard to reason about
- Unpredictable behavior

### ✅ Solution

Design clear hierarchies without cycles.

```
definition organization {
    relation admin: user
    relation member: user

    permission manage = admin
}

definition team {
    relation organization: organization
    relation member: user

    permission view = member + organization->admin
}
```

### Prevention

- Draw schema diagrams before implementing
- Identify parent-child relationships
- Avoid bidirectional arrows
- Test for cycles with `zed validate`

---

## Anti-Pattern 9: Ignoring Union Types

### ❌ Problem

Creating separate relations for each subject type.

```
definition resource {
    relation user_viewer: user
    relation team_viewer: team
    relation group_viewer: group

    permission view = user_viewer + ??? // How to include teams and groups?
}
```

### Why It's Bad

- Can't combine in permissions easily
- Repetitive definitions
- Hard to extend with new types

### ✅ Solution

Use union types in relations.

```
definition resource {
    relation viewer: user | team#member | group#member

    permission view = viewer
}
```

### Benefits

- Single relation for multiple types
- Cleaner permission definitions
- Easy to extend
- Matches SpiceDB's type system

---

## Anti-Pattern 10: Premature Optimization

### ❌ Problem

Denormalizing or pre-computing permissions prematurely.

```
// Trying to "cache" computed permissions
definition document {
    relation owner: user
    relation editor: user
    relation viewer: user
    relation effective_viewer: user  // Pre-computed viewer + editor + owner

    permission view = effective_viewer
}
```

### Why It's Bad

- Manual maintenance of denormalized data
- Prone to inconsistency
- Adds complexity without measured benefit
- SpiceDB already optimizes queries

### ✅ Solution

Let SpiceDB compute permissions. Optimize only when profiling shows issues.

```
definition document {
    relation owner: user
    relation editor: user
    relation viewer: user

    permission view = viewer + editor + owner  // Let SpiceDB compute
}
```

### When to Optimize

- After profiling shows actual performance issues
- When arrow chains exceed 3-4 levels
- With measured improvement from optimization
- With automated consistency maintenance

---

## Anti-Pattern 11: Ignoring Consistency

### ❌ Problem

Not considering SpiceDB's consistency model in schema design.

```
// Writing relationships without considering consistency
WriteRelationships(user:alice#editor@document:1)
WriteRelationships(document:1#project@project:A)

// Immediately checking:
CheckPermission(user:alice, edit, document:1)  // May fail due to eventual consistency!
```

### Why It's Bad

- Race conditions in permission checks
- Unpredictable behavior
- Hard-to-debug issues in production

### ✅ Solution

Understand and use SpiceDB's consistency guarantees (zookies).

```
// Write and get zookie
resp = WriteRelationships(...)
zookie = resp.written_at

// Check at or after zookie
CheckPermission(
    user:alice,
    edit,
    document:1,
    consistency: AtLeastAsFresh(zookie)
)
```

### Best Practices

- Use `FullyConsistent` for critical operations
- Use `AtLeastAsFresh` when you need to read-your-writes
- Use `MinimizeLatency` for non-critical checks
- Understand the trade-offs (consistency vs latency)

---

## Anti-Pattern 12: Schema as Database

### ❌ Problem

Treating SpiceDB schema like a database schema with all attributes.

```
definition user {
    relation email: string           // Bad: not a relation
    relation created_at: timestamp   // Bad: not a relation
    relation is_active: boolean      // Bad: not a relation
}
```

### Why It's Bad

- SpiceDB is not a database
- Schema is for relationships and permissions only
- Increases complexity without benefit
- Violates single responsibility principle

### ✅ Solution

Store only authorization-relevant relationships in SpiceDB.

```
definition user {}  // Minimal definition

definition document {
    relation owner: user
    relation viewer: user

    permission view = viewer + owner
}
```

Store user attributes (email, created_at, etc.) in your application database.

### What Belongs in SpiceDB

- Relationships (user owns document)
- Group memberships (user in team)
- Hierarchies (project in organization)
- Authorization decisions

### What Doesn't Belong

- User profiles
- Resource metadata
- Application state
- Business logic

---

## Anti-Pattern 13: Arrow Over Relations with Subject Relations

### ❌ Problem

Using an arrow on a relation whose subjects include a `#subjectrelation` suffix:

```
definition group {
    relation member: user
}

definition document {
    relation parent: group#member    // Subject relation specified
    permission view = parent->view   // Arrow over this relation
}
```

### Why It's Bad

When SpiceDB walks `parent->view`, it operates on the **group object** (the `group#member`
binding's object type), ignoring the `#member` qualifier. The arrow asks "what `view`
permission does the group have?" -- not "what `view` permission do members of the group have?"

This is counterintuitive and causes silent correctness bugs:
- You intend: "users who are members of the parent group can view"
- SpiceDB does: "the group object itself has a view permission"

It can also trigger expensive `LookupSubjects` calls internally.

### ✅ Solution

Express intent through a correctly typed intermediate relation:

```
definition document {
    relation parent: group           // The group itself, not group#member
    permission view = parent->member // Walk to the group's members
}
```

Or restructure so direct relations express membership without the `#subjectrelation` suffix.

---

## Summary Checklist

When reviewing schemas, check for these anti-patterns:

- [ ] Too many fine-grained relations (use computed permissions)
- [ ] "can_" or "has_" in relation names (use role nouns)
- [ ] Deep arrow chains (use recursive definitions)
- [ ] Wildcards on sensitive permissions (explicit only)
- [ ] Complex caveat logic (keep simple, move to app)
- [ ] Schema bloat (consolidate similar definitions)
- [ ] Missing hierarchy (use parent relations)
- [ ] Circular dependencies (design clear hierarchy)
- [ ] Not using union types (combine subject types)
- [ ] Premature optimization (profile first)
- [ ] Ignoring consistency (use zookies appropriately)
- [ ] Treating as database (relationships only)
- [ ] Arrow over relation with `#subjectrelation` suffix (restructure to avoid)

Use the `schema-validator` agent to automatically detect many of these anti-patterns.
