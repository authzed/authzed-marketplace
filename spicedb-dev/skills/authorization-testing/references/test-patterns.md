# Authorization Test Patterns

Comprehensive reference for designing test scenarios for SpiceDB authorization systems.

## Quick Pattern Index

| Scenario Type | When to Use |
|--------------|-------------|
| Direct access | Single relation to resource |
| Role-based | Multiple roles with different permissions |
| Hierarchical | Parent/child resource inheritance |
| Sharing (wildcard/domain) | Group or public access |
| Complex multi-path | Multiple routes to access |
| Permission revocation | Delete relationship, re-check |
| Edge cases | Empty graph, circular refs |
| Caveated | Context-dependent permissions |

---

## Basic Access Scenarios

### 1. Direct Access

```
Setup:
  - user:alice#owner@document:doc-1

Tests:
  - user:alice can view document:doc-1
  - user:alice can edit document:doc-1
  - user:alice can delete document:doc-1
  - user:bob cannot view document:doc-1 (negative)
```

### 2. Role-Based Access

```
Setup:
  - user:alice#owner@document:doc-1
  - user:bob#editor@document:doc-1
  - user:charlie#viewer@document:doc-1

Tests:
  - All can view
  - Only alice and bob can edit
  - Only alice can delete
  - dave (no relationship) cannot view (negative)
```

---

## Hierarchical Scenarios

### 3. Organizational Hierarchy

```
Setup:
  - user:alice#admin@organization:acme
  - user:bob#member@organization:acme
  - team:eng#org@organization:acme
  - user:charlie#member@team:eng
  - project:api#parent@team:eng

Tests:
  - alice (org admin) can manage project:api (inherited)
  - bob (org member) can view project:api (inherited)
  - charlie (team member) can view project:api (inherited)
  - dave (not in org) cannot view project:api (negative)
```

### 4. Nested Hierarchies (3 levels)

```
Setup:
  - folder:a#owner@user:alice
  - folder:b#parent@folder:a
  - folder:c#parent@folder:b
  - document:doc-1#folder@folder:c

Tests:
  - alice can view document:doc-1 (inherited through 3 levels)
  - alice can edit document:doc-1 (inherited)
  - bob (no relationship) cannot view document:doc-1 (negative)
```

---

## Sharing Scenarios

### 5. Link Sharing (Wildcard)

```
Setup:
  - document:doc-1#owner@user:alice
  - document:doc-1#link_viewer@user:*

Tests:
  - alice can edit document:doc-1
  - any user can view document:doc-1 (wildcard)
  - any user cannot edit document:doc-1 (negative)
```

### 6. Domain Sharing

```
Setup:
  - domain:acme.com#member@user:alice
  - domain:acme.com#member@user:bob
  - document:doc-1#owner@user:alice
  - document:doc-1#domain@domain:acme.com

Tests:
  - alice can edit (owner)
  - bob can view (domain member)
  - bob cannot edit (negative)
  - charlie (not in domain) cannot view (negative)
```

---

## Complex Scenarios

### 7. Multiple Permission Paths

```
Setup:
  - organization:acme#admin@user:alice
  - team:eng#org@organization:acme
  - team:eng#member@user:bob
  - project:api#parent@team:eng
  - project:api#owner@user:charlie

Tests:
  - alice can manage (org admin path)
  - bob can view (team member path)
  - charlie can manage (owner path)
  - Verify all paths work independently
  - dave (no relationship anywhere) cannot view (negative)
```

### 8. Permission Revocation

```
Setup:
  - document:doc-1#editor@user:bob

Tests:
  - bob can edit document:doc-1
  - DELETE relationship: document:doc-1#editor@user:bob
  - bob cannot edit document:doc-1 (negative after revocation)
```

**Note on revocation timing:** After deleting a relationship, use `FullyConsistent`
consistency in the follow-up check to avoid reading from a stale replica.
Additionally, test race conditions: a delete issued at the same time as a check
should not grant access.

### 9. Bulk Operation Denial

```
Setup:
  - document:doc-1#viewer@user:bob

Tests:
  - bob can view document:doc-1 (positive)
  - bob cannot delete document:doc-1 (negative)
  - bob cannot share document:doc-1 (negative)
  - bob cannot manage permissions on document:doc-1 (negative)
```

---

## Edge Cases

### 10. Empty Relationships (Deny by Default)

```
Tests:
  - user:alice cannot access document:doc-1 (no relationships at all)
  - Verify system denies by default
```

### 11. Circular References (Must Not Infinite Loop)

```
Setup:
  - folder:a#parent@folder:b
  - folder:b#parent@folder:a (circular)

Tests:
  - Permission checks complete without hanging
  - Schema validation catches circular definitions at write time
```

### 12. Cross-Tenant Isolation

```
Setup:
  - user:alice#member@tenant:acme
  - resource:res-1#tenant@tenant:acme
  - user:eve#member@tenant:other

Test: eve tries to access resource:res-1
Expected: Permission denied (different tenant)
```

### 13. Hierarchical Boundary

```
Setup:
  - user:alice#member@team:engineering
  - project:api#team@team:engineering
  - user:bob#member@team:sales

Test: bob tries to access project:api
Expected: Permission denied (different team)
```

### 14. Race Condition After Revocation

```
Setup:
  - user:bob#editor@document:doc-1

Scenario:
  - Start concurrent: revoke relationship AND check permission
  - After revocation completes, re-check with FullyConsistent
  - bob must not retain access after revocation is committed
```

---

## Caveated Permission Scenarios

For permissions that depend on runtime context, use `assertCaveated` in YAML assertions.

```yaml
assertions:
  assertTrue:
    - "document:doc-1#view@user:alice"

  assertFalse:
    - "document:doc-1#view@user:dave"

  assertCaveated:
    # bob has view via a caveat relationship; result depends on context
    - "document:doc-1#view@user:bob"
```

`assertCaveated` is appropriate when:
- A caveat exists on the relationship but no context was provided in the check
- The answer is "maybe" (caveat could evaluate either way)

---

## CI Integration Patterns

### spicedb serve-testing

SpiceDB provides a `serve-testing` mode where each unique client-supplied token gets an
isolated, empty datastore -- no preshared key is configured on the server. This lets
parallel test suites share one SpiceDB instance:

```bash
# Start SpiceDB in testing mode
spicedb serve-testing
```

Then connect each test suite with its own token (e.g. `--token test-key-1` and
`--token test-key-2`). Each token gets its own namespace -- tests using `test-key-1`
cannot see data written by tests using `test-key-2`.

### GitHub Actions

```yaml
# .github/workflows/test.yml
- uses: authzed/action-spicedb@v1
  with:
    version: "latest"
- uses: authzed/action-spicedb-validate@v1
  with:
    validations-file: assertions.yaml
```

- `authzed/action-spicedb`: Starts a SpiceDB integration test server
- `authzed/action-spicedb-validate`: Validates schema files in CI pipeline

### Testcontainers

**Python:**
```bash
pip install testcontainers-spicedb
```

```python
from testcontainers.spicedb import SpiceDBContainer

with SpiceDBContainer() as spicedb:
    client = connect(spicedb.get_grpc_endpoint())
    # run tests...
```

**Go:**
```go
import "github.com/testcontainers/testcontainers-go/modules/spicedb"

container, err := spicedb.RunContainer(ctx)
```

Testcontainers handles container lifecycle automatically (start on entry, stop on exit).

---

## Debugging Failing Checks

Enable tracing on a `CheckPermissionRequest` to see the traversal tree:

```go
resp, err := client.CheckPermission(ctx, &v1.CheckPermissionRequest{
    Resource:    resource,
    Permission:  "view",
    Subject:     subject,
    WithTracing: true,
})
// resp.DebugTrace contains the full traversal tree
```

Use this when a check returns an unexpected result -- the trace shows exactly
which relations were evaluated and why the check resolved as it did.
