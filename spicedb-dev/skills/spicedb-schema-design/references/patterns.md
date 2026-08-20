# SpiceDB Schema Patterns

Comprehensive collection of proven SpiceDB schema patterns for common authorization scenarios.

## Table of Contents

- [Basic Patterns](#basic-patterns)
- [Hierarchical Patterns](#hierarchical-patterns)
- [Multi-Tenancy Patterns](#multi-tenancy-patterns)
- [Sharing Patterns](#sharing-patterns)
- [Advanced Patterns](#advanced-patterns)

## Basic Patterns

### Simple RBAC

Direct role assignments on resources.

```
definition user {}

definition resource {
    relation owner: user
    relation editor: user
    relation viewer: user

    permission view = viewer + editor + owner
    permission edit = editor + owner
    permission delete = owner
}
```

**Use cases:**
- Simple document management
- Basic resource ownership
- Direct user-to-resource permissions

**Characteristics:**
- No hierarchy
- Direct assignments only
- Easy to understand and implement

### Group Membership

Users belong to groups, groups have permissions.

```
definition user {}

definition group {
    relation member: user
    relation admin: user

    permission view = member + admin
    permission manage = admin
}

definition resource {
    relation owner: user
    relation viewer: user | group#member

    permission view = owner + viewer
    permission edit = owner
}
```

**Use cases:**
- Team-based access
- Department permissions
- Shared resource access

**Key feature:** `user | group#member` allows both individual users and group members as viewers.

### Tag-Based Access

Resources have tags, users have access to tags.

```
definition user {}

definition tag {
    relation viewer: user
}

definition resource {
    relation owner: user
    relation tag: tag

    permission view = owner + tag->viewer
}
```

**Use cases:**
- Category-based access
- Label-driven permissions
- Flexible grouping without explicit groups

## Hierarchical Patterns

### Organization Hierarchy

Organizations contain teams, teams contain projects.

```
definition user {}

definition organization {
    relation admin: user
    relation member: user

    permission view = member + admin
    permission manage = admin
}

definition team {
    relation parent: organization
    relation admin: user
    relation member: user

    permission view = member + admin + parent->member
    permission manage = admin + parent->admin
}

definition project {
    relation parent: team
    relation owner: user

    permission view = owner + parent->member
    permission edit = owner + parent->admin
}
```

**Use cases:**
- Corporate structures
- Multi-level organizations
- Inherited permissions

**Key pattern:** `parent->admin` inherits admin rights from parent.

### Recursive Hierarchy

Unlimited nesting depth (folders in folders).

```
definition user {}

definition folder {
    relation parent: folder
    relation owner: user
    relation editor: user
    relation viewer: user

    permission view = viewer + editor + owner + parent->view
    permission edit = editor + owner + parent->edit
    permission delete = owner + parent->delete
}
```

**Use cases:**
- File system permissions
- Nested folder structures
- Arbitrary depth hierarchies

**Key pattern:** `parent->view` recursively inherits view permission up the hierarchy.

### Matrix Organization

Resources belong to multiple hierarchies (department AND location).

```
definition user {}

definition department {
    relation member: user
    relation admin: user
}

definition location {
    relation member: user
    relation admin: user
}

definition resource {
    relation department: department
    relation location: location
    relation owner: user

    permission view = owner + department->member + location->member
    permission edit = owner + department->admin + location->admin
}
```

**Use cases:**
- Matrix organizations
- Multi-dimensional access control
- Cross-cutting concerns

## Multi-Tenancy Patterns

### Basic Multi-Tenant

Resources belong to tenants, users belong to tenants.

```
definition user {}

definition tenant {
    relation admin: user
    relation member: user

    permission manage = admin
    permission access = member + admin
}

definition resource {
    relation tenant: tenant
    relation owner: user

    permission view = owner + tenant->member
    permission edit = owner + tenant->admin
    permission delete = owner + tenant->admin
}
```

**Use cases:**
- SaaS applications
- Customer isolation
- Workspace-based apps

**Key pattern:** All resource access requires tenant membership.

### Multi-Tenant with Sharing

Resources can be shared across tenants.

```
definition user {}

definition tenant {
    relation member: user
}

definition resource {
    relation tenant: tenant
    relation owner: user
    relation shared_with: user | tenant#member

    permission view = owner + tenant->member + shared_with
    permission edit = owner + tenant->member
}
```

**Use cases:**
- Cross-tenant collaboration
- Partner access
- External sharing

### Service Accounts per Tenant

Service accounts scoped to tenants.

```
definition user {}

definition service_account {
    relation tenant: tenant
}

definition tenant {
    relation admin: user
    relation member: user | service_account
}

definition resource {
    relation tenant: tenant
    relation owner: user

    permission view = owner + tenant->member
    permission api_access = tenant->member
}
```

**Use cases:**
- API access per tenant
- Automated processes
- Service-to-service auth

## Sharing Patterns

### Link Sharing

Public links grant access to anyone with the link.

```
definition user {}

definition document {
    relation owner: user
    relation link_shared: user:*

    permission view = owner + link_shared
}
```

**Use cases:**
- Public sharing links
- Anonymous access
- Temporary public access

**Security note:** Link sharing grants access to `user:*` (all users). Combine with caveats for expiring links.

### Invited Sharing

Explicitly share with specific users or emails.

```
definition user {}

definition document {
    relation owner: user
    relation invited_viewer: user
    relation invited_editor: user

    permission view = owner + invited_viewer + invited_editor
    permission edit = owner + invited_editor
}
```

**Use cases:**
- Email-based invitations
- Explicit grants
- Controlled sharing

### Domain-Based Sharing

Share with all users from a domain.

```
definition user {}

definition domain {
    relation member: user
}

definition document {
    relation owner: user
    relation shared_with_domain: domain

    permission view = owner + shared_with_domain->member
}
```

**Use cases:**
- Company-wide sharing
- Partner domain access
- Educational institutions

### Workspace Sharing

Share through workspaces (channels, folders).

```
definition user {}

definition workspace {
    relation member: user
}

definition document {
    relation owner: user
    relation workspace: workspace

    permission view = owner + workspace->member
}
```

**Use cases:**
- Slack-style channels
- Shared folders
- Collaborative spaces

## Advanced Patterns

### Time-Limited Access

Access expires after a certain time.

**Preferred (SpiceDB v1.40+): Native expiration**

```
use expiration

definition document {
    relation owner: user
    relation temp_viewer: user with expiration

    permission view = owner + temp_viewer
}
```

Write an expiring relationship by setting `OptionalExpiresAt` on the relationship:

```go
// Go: set expiry at write time
OptionalExpiresAt: timestamppb.New(time.Now().Add(24 * time.Hour))
```

**Legacy (still works, not recommended):**

```
caveat expiring(expiration timestamp) {
    expiration > now()
}

definition document {
    relation owner: user
    relation temp_viewer: user with expiring

    permission view = owner + temp_viewer
}
```

**Why prefer native expiration over caveats:**
- Clients don't need to supply `now` at check time
- Expired relationships are garbage-collected automatically
- No accumulation of stale caveated relationships degrading check performance

**Use cases:**
- Temporary access grants
- Time-boxed permissions
- Trial periods

### IP Allowlist (with Caveats)

Access restricted by IP address.

```
caveat ip_allowlist(user_ip ipaddress, allowed_cidrs list<string>) {
    allowed_cidrs.exists(c, user_ip.in_cidr(c))
}

definition resource {
    relation admin: user with ip_allowlist
    relation viewer: user

    permission view = viewer + admin
    permission edit = admin
}
```

**Use cases:**
- Location-based access
- VPN requirements
- Security-critical operations

**Important:** `ipaddress` is a SpiceDB-specific CEL type with an `.in_cidr(string)` method.
The IP must be passed as a caveat parameter at check time -- SpiceDB has no `request` object.
Pass it via `CheckPermissionRequest.Context`: `{"user_ip": "203.0.113.5", "allowed_cidrs": ["203.0.113.0/24"]}`.

### Delegated Administration

Users can grant permissions they have to others.

```
definition document {
    relation owner: user
    relation admin: user
    relation editor: user

    permission view = editor + admin + owner
    permission edit = editor + admin + owner
    permission grant_edit = admin + owner
    permission grant_admin = owner
}
```

**Use cases:**
- Delegated permission management
- Self-service access control
- Distributed administration

### Approval Workflows

Multiple approvers required for action.

```
definition user {}

definition approval_request {
    relation requester: user
    relation approver: user
    relation approved_by: user

    permission request = requester
    permission approve = approver
    permission execute = approved_by & approver
}
```

**Use cases:**
- Change management
- Financial approvals
- Security reviews

**Note:** This is a simplified example. Real approval workflows often need application-level state management.

### Attribute-Based Access Control (ABAC)

Permissions based on resource attributes.

```
caveat is_owner(user_id string, resource_owner string) {
    user_id == resource_owner
}

definition document {
    relation editor: user
    relation attribute_viewer: user with is_owner

    permission view = editor + attribute_viewer
    permission edit = editor
}
```

**Use cases:**
- Own-resource access (users can only edit their own data)
- Dynamic attribute checks
- Context-aware permissions

**Limitation:** SpiceDB caveats have limited expressions. Complex ABAC often requires application-level logic.

### Loop Relationships for Boolean Attributes / Feature Flags

Use a self-referential relationship to model a boolean attribute on a resource without
wildcards (wildcards are not supported by Authzed Materialize):

```
definition document {
    relation edit_enabled: document

    relation editor: user
    permission edit = editor & edit_enabled->editor  // Only if editing is enabled
}
```

Write `document:1#edit_enabled@document:1` to "turn on" the attribute;
delete it to "turn off". This pattern avoids wildcards while keeping the
attribute inside SpiceDB rather than in application code.

**Use cases:**
- Feature flags on resources
- Publish/unpublish state
- Enabled/disabled toggles

### Subject ID Selection

When mapping application identity to SpiceDB subject IDs:

```
// ✅ CORRECT: stable OIDC sub field
user:01HXQZ...   // opaque stable identifier

// ❌ WRONG: email address
user:alice@example.com  // unstable, @ is not allowed in object IDs
```

Rules:
- Use the stable `sub` field from OIDC tokens, not email addresses
- Email addresses are unstable (users change them) and `@` is not a valid character in SpiceDB object IDs
- For multiple auth providers, create a separate definition per provider:

```
definition google_user {}
definition github_user {}

definition document {
    relation owner: google_user | github_user
}
```

### `.all` Intersection Arrow

The `.all` modifier requires a subject to be related via **all** objects on a relation:

```
definition document {
    relation required_groups: group

    // User must be a member of ALL required_groups on this document
    permission view = required_groups.all(member)
}
```

Write multiple `document:doc-1#required_groups@group:X` relationships; a user must be a
member of every group listed. Without `.all`, a user needs membership in only any one group.

**Use cases:**
- Multi-factor access requirements
- Requiring membership in multiple approval groups
- All-or-nothing conditional access

## Combining Patterns

Real applications often combine multiple patterns:

### GitHub-Style Repository Permissions

Combines hierarchy, teams, and sharing.

```
definition user {}

definition organization {
    relation admin: user
    relation member: user
}

definition team {
    relation org: organization
    relation member: user
}

definition repository {
    relation org: organization
    relation admin: user | team#member
    relation writer: user | team#member
    relation reader: user | team#member

    permission read = reader + writer + admin + org->member
    permission write = writer + admin + org->admin
    permission admin_repo = admin + org->admin
}
```

### Google Docs-Style Sharing

Combines ownership, link sharing, and domain sharing.

```
definition user {}

definition domain {
    relation member: user
}

definition document {
    relation owner: user
    relation editor: user
    relation commenter: user
    relation viewer: user
    relation link_viewer: user:*
    relation domain: domain

    permission view = viewer + commenter + editor + owner + link_viewer + domain->member
    permission comment = commenter + editor + owner
    permission edit = editor + owner
    permission share = owner
}
```

## Pattern Selection Guide

| Pattern | Complexity | Use Case | Scalability |
|---------|-----------|----------|-------------|
| Simple RBAC | Low | Small apps, direct ownership | High |
| Group Membership | Low | Team collaboration | High |
| Tag-Based | Medium | Flexible categorization | High |
| Org Hierarchy | Medium | Corporate structures | High |
| Recursive Hierarchy | Medium | File systems, nested folders | Medium |
| Multi-Tenant | Medium | SaaS applications | High |
| Link Sharing | Low | Public sharing | High |
| Domain Sharing | Low | Company-wide access | High |
| Time-Limited (Caveats) | High | Temporary access | Medium |
| ABAC (Caveats) | High | Dynamic attributes | Medium |

**Complexity** refers to implementation and maintenance effort.
**Scalability** refers to performance at large scale (millions of relationships).

## Performance Considerations

### Efficient Patterns

✅ **Good:**
```
permission view = viewer + parent->member  // One level of indirection
```

✅ **Good:**
```
permission admin = owner + org->admin + team->admin  // Multiple paths, but shallow
```

### Expensive Patterns

⚠️ **Expensive:**
```
permission view = parent->parent->parent->viewer  // Deep nesting
```

⚠️ **Expensive:**
```
permission access = (folder->parent->parent->viewer) + (team->org->parent->admin)  // Multiple deep paths
```

**Optimization tips:**
- Limit arrow chains to 2-3 levels
- Use recursive definitions for deep hierarchies
- Denormalize frequently-checked permissions
- Consider materialized views for expensive checks

## External Resources

- [SpiceDB Playground](https://play.authzed.com/) - Test patterns interactively
- [Authzed Examples](https://github.com/authzed/examples) - Production schema examples
- [Schema Best Practices](https://authzed.com/docs/guides/schema) - Official guidance
