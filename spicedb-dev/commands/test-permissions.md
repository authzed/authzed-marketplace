---
name: test-permissions
description: Generate test data fixtures and test scenarios for permission model
argument-hint: "[schema-file] [output-dir]"
allowed-tools:
  - Read
  - Write
  - Glob
  - Grep
  - Bash
---

# Generate Permission Tests

Generate test data fixtures and test scenarios for a SpiceDB authorization schema.

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Locate Schema

If user provided schema file, use that. Otherwise:
1. Look for `*.zed` files in current directory
2. If multiple found, ask which to use
3. If none found, ask for schema location

### Step 2: Parse Schema

Read the schema and extract:
- Resource definitions
- Relations for each resource
- Permissions for each resource
- Hierarchical relationships
- Wildcards or caveats

### Step 3: Determine Test Language

Ask the user (or detect from codebase):
- Go (use go test)
- TypeScript/JavaScript (use Jest/Mocha)
- Python (use pytest)
- YAML (use SpiceDB assertions)

### Step 4: Generate Test Fixtures

**Use the resource types, relation names, and permission names parsed in Step 2.**
Do not use placeholder names (`document`, `doc-1`, `alice`) unless those are actually
in the schema. For each definition found, use that definition name as the object type
and its declared relations as the relation names in all generated fixtures.

For each resource type in the schema, generate:

**Basic relationships**:
```
# Direct ownership
document:doc-1#owner@user:alice

# Role-based access
document:doc-1#editor@user:bob
document:doc-1#viewer@user:charlie

# No access
# user:dave has no relationships
```

**Hierarchical relationships**:
```
# Organization hierarchy
organization:acme#admin@user:alice
organization:acme#member@user:bob
team:engineering#org@organization:acme
team:engineering#member@user:charlie
project:api#parent@team:engineering
```

**Complex scenarios**:
```
# Multi-path access
organization:acme#admin@user:alice
project:api#org@organization:acme
project:api#owner@user:bob
# alice can access via org admin, bob via ownership
```

Replace `document`, `organization`, `user` with the actual definition names from
Step 2. Replace `owner`, `editor`, `viewer` with the actual relation names declared
in that definition.

Load the `authorization-testing` skill for fixture patterns and test scenarios.

### Step 5: Generate Test Scenarios

For each permission, generate test cases covering:

**Positive tests** (access granted):
- Direct access (owner can edit)
- Role-based access (editor can edit)
- Hierarchical access (org admin can manage)
- Multiple paths (access via different relationships)

**Negative tests** (access denied):
- No relationship (dave cannot view)
- Insufficient role (viewer cannot edit)
- Cross-boundary (different tenant/org)
- After revocation (editor removed)

### Step 6: Generate Test Code

Before generating test code, read `skills/authorization-testing/references/fixture-generators.md` to get the complete helper implementations. Use the implementations from that file rather than stubs.

Based on the programming language, generate test code:

## Language-Specific Test Generation

### Go Tests

```go
package authz_test

import (
    "context"
    "testing"

    v1 "github.com/authzed/authzed-go/proto/authzed/api/v1"
    "github.com/stretchr/testify/assert"
)

func TestDocumentPermissions(t *testing.T) {
    client := setupTestClient(t)

    // Setup: Write test relationships
    writeRelationships(t, client, []*v1.Relationship{
        parseRelationship("document:doc-1#owner@user:alice"),
        parseRelationship("document:doc-1#editor@user:bob"),
        parseRelationship("document:doc-1#viewer@user:charlie"),
    })

    tests := []struct {
        name       string
        subject    string
        permission string
        resource   string
        want       bool
    }{
        // Positive tests
        {"owner can view", "user:alice", "view", "document:doc-1", true},
        {"owner can edit", "user:alice", "edit", "document:doc-1", true},
        {"owner can delete", "user:alice", "delete", "document:doc-1", true},
        {"editor can view", "user:bob", "view", "document:doc-1", true},
        {"editor can edit", "user:bob", "edit", "document:doc-1", true},
        {"viewer can view", "user:charlie", "view", "document:doc-1", true},

        // Negative tests
        {"editor cannot delete", "user:bob", "delete", "document:doc-1", false},
        {"viewer cannot edit", "user:charlie", "edit", "document:doc-1", false},
        {"viewer cannot delete", "user:charlie", "delete", "document:doc-1", false},
        {"no access user cannot view", "user:dave", "view", "document:doc-1", false},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := checkPermission(t, client, tt.subject, tt.permission, tt.resource)
            assert.Equal(t, tt.want, got)
        })
    }
}

// Helper functions
func writeRelationships(t *testing.T, client *authzed.Client, rels []*v1.Relationship) {
    // Implementation...
}

func checkPermission(t *testing.T, client *authzed.Client, subject, permission, resource string) bool {
    // Implementation...
}

func parseRelationship(s string) *v1.Relationship {
    // Implementation...
}
```

### TypeScript Tests

```typescript
import { v1 } from '@authzed/authzed-node';
import { describe, it, expect, beforeAll } from '@jest/globals';

describe('Document Permissions', () => {
    let client: v1.ZedClient;

    beforeAll(async () => {
        client = setupTestClient();

        // Setup: Write test relationships
        await writeRelationships(client, [
            'document:doc-1#owner@user:alice',
            'document:doc-1#editor@user:bob',
            'document:doc-1#viewer@user:charlie',
        ]);
    });

    describe('View Permission', () => {
        it('owner can view', async () => {
            const result = await checkPermission(client, 'user:alice', 'view', 'document:doc-1');
            expect(result).toBe(true);
        });

        it('editor can view', async () => {
            const result = await checkPermission(client, 'user:bob', 'view', 'document:doc-1');
            expect(result).toBe(true);
        });

        it('viewer can view', async () => {
            const result = await checkPermission(client, 'user:charlie', 'view', 'document:doc-1');
            expect(result).toBe(true);
        });

        it('no access user cannot view', async () => {
            const result = await checkPermission(client, 'user:dave', 'view', 'document:doc-1');
            expect(result).toBe(false);
        });
    });

    describe('Edit Permission', () => {
        it('owner can edit', async () => {
            const result = await checkPermission(client, 'user:alice', 'edit', 'document:doc-1');
            expect(result).toBe(true);
        });

        it('editor can edit', async () => {
            const result = await checkPermission(client, 'user:bob', 'edit', 'document:doc-1');
            expect(result).toBe(true);
        });

        it('viewer cannot edit', async () => {
            const result = await checkPermission(client, 'user:charlie', 'edit', 'document:doc-1');
            expect(result).toBe(false);
        });
    });

    // Additional test groups...
});

// Helper functions
async function writeRelationships(client: v1.ZedClient, rels: string[]) {
    // Implementation...
}

async function checkPermission(client: v1.ZedClient, subject: string, permission: string, resource: string): Promise<boolean> {
    // Implementation...
}
```

### Python Tests

```python
import pytest
from authzed.api.v1 import Client, CheckPermissionRequest, WriteRelationshipsRequest

@pytest.fixture
def client():
    """Create test client."""
    client = Client("localhost:50051", "test-token")

    # Setup: Write test relationships
    write_relationships(client, [
        "document:doc-1#owner@user:alice",
        "document:doc-1#editor@user:bob",
        "document:doc-1#viewer@user:charlie",
    ])

    return client

class TestDocumentPermissions:
    """Test document permission model."""

    def test_owner_can_view(self, client):
        assert check_permission(client, "user:alice", "view", "document:doc-1")

    def test_owner_can_edit(self, client):
        assert check_permission(client, "user:alice", "edit", "document:doc-1")

    def test_owner_can_delete(self, client):
        assert check_permission(client, "user:alice", "delete", "document:doc-1")

    def test_editor_can_view(self, client):
        assert check_permission(client, "user:bob", "view", "document:doc-1")

    def test_editor_can_edit(self, client):
        assert check_permission(client, "user:bob", "edit", "document:doc-1")

    def test_editor_cannot_delete(self, client):
        assert not check_permission(client, "user:bob", "delete", "document:doc-1")

    def test_viewer_can_view(self, client):
        assert check_permission(client, "user:charlie", "view", "document:doc-1")

    def test_viewer_cannot_edit(self, client):
        assert not check_permission(client, "user:charlie", "edit", "document:doc-1")

    def test_viewer_cannot_delete(self, client):
        assert not check_permission(client, "user:charlie", "delete", "document:doc-1")

    def test_no_access_user_cannot_view(self, client):
        assert not check_permission(client, "user:dave", "view", "document:doc-1")

# Helper functions
def write_relationships(client: Client, rels: list[str]):
    """Write test relationships."""
    # Implementation...

def check_permission(client: Client, subject: str, permission: str, resource: str) -> bool:
    """Check permission."""
    # Implementation...
```

### YAML Assertions

```yaml
# test-assertions.yaml
schema: |
  definition user {}

  definition document {
    relation owner: user
    relation editor: user
    relation viewer: user

    permission view = viewer + editor + owner
    permission edit = editor + owner
    permission delete = owner
  }

relationships: |
  document:doc-1#owner@user:alice
  document:doc-1#editor@user:bob
  document:doc-1#viewer@user:charlie

assertions:
  assertTrue:
    - "document:doc-1#view@user:alice"
    - "document:doc-1#edit@user:alice"
    - "document:doc-1#delete@user:alice"
    - "document:doc-1#view@user:bob"
    - "document:doc-1#edit@user:bob"
    - "document:doc-1#view@user:charlie"

  assertFalse:
    - "document:doc-1#delete@user:bob"
    - "document:doc-1#edit@user:charlie"
    - "document:doc-1#delete@user:charlie"
    - "document:doc-1#view@user:dave"
```

## Step 7: Generate Test Fixture Data

Create a separate fixture file with relationship data:

**fixtures.json** (language-agnostic):
```json
{
  "basic_document_access": {
    "relationships": [
      "document:doc-1#owner@user:alice",
      "document:doc-1#editor@user:bob",
      "document:doc-1#viewer@user:charlie"
    ],
    "expected": [
      {"subject": "user:alice", "permission": "view", "resource": "document:doc-1", "allowed": true},
      {"subject": "user:alice", "permission": "edit", "resource": "document:doc-1", "allowed": true},
      {"subject": "user:alice", "permission": "delete", "resource": "document:doc-1", "allowed": true},
      {"subject": "user:bob", "permission": "view", "resource": "document:doc-1", "allowed": true},
      {"subject": "user:bob", "permission": "edit", "resource": "document:doc-1", "allowed": true},
      {"subject": "user:bob", "permission": "delete", "resource": "document:doc-1", "allowed": false},
      {"subject": "user:charlie", "permission": "view", "resource": "document:doc-1", "allowed": true},
      {"subject": "user:charlie", "permission": "edit", "resource": "document:doc-1", "allowed": false},
      {"subject": "user:charlie", "permission": "delete", "resource": "document:doc-1", "allowed": false},
      {"subject": "user:dave", "permission": "view", "resource": "document:doc-1", "allowed": false}
    ]
  },
  "hierarchical_access": {
    "relationships": [
      "organization:acme#admin@user:alice",
      "organization:acme#member@user:bob",
      "project:api#org@organization:acme",
      "project:api#owner@user:charlie"
    ],
    "expected": [
      {"subject": "user:alice", "permission": "manage", "resource": "project:api", "allowed": true},
      {"subject": "user:bob", "permission": "view", "resource": "project:api", "allowed": true},
      {"subject": "user:bob", "permission": "manage", "resource": "project:api", "allowed": false},
      {"subject": "user:charlie", "permission": "manage", "resource": "project:api", "allowed": true}
    ]
  }
}
```

## Step 8: Write Test Files

Write generated tests to output directory:
- Test file in appropriate language/framework (e.g., `authz_test.go`, `authz.test.ts`, `test_authz.py`)
- Fixture data file (JSON or language-specific)
- Helpers file (e.g., `authz_test_helpers_test.go` or `helpers.ts`) with **complete** implementations of `setupTestClient`, `writeRelationships`, `checkPermission`, and `parseRelationship` -- copy from `skills/authorization-testing/references/fixture-generators.md`, not stubs
- README with instructions

### Output Structure

```
tests/
├── authz_test.go (or .ts, .py, etc.)
├── fixtures.json
├── helpers.go (or .ts, .py, etc.)
└── README.md
```

## Step 9: Provide Usage Instructions

Tell the user:
1. Where tests were written
2. How to run the tests
3. How to add more test scenarios
4. How to integrate with CI/CD

### SpiceDB Testing Setup

Include a "SpiceDB Testing Setup" section in the output with these options:

**Local development -- `spicedb serve-testing`:**
```bash
spicedb serve-testing --grpc-preshared-key test-key-1 --grpc-preshared-key test-key-2
```
Each unique preshared key gets an isolated, empty datastore. Parallel test suites can share one SpiceDB instance without interference.

**Integration tests with testcontainers:**

Go:
```go
import "github.com/testcontainers/testcontainers-go/modules/spicedb"

container, err := spicedb.RunContainer(ctx)
```

Python:
```python
from testcontainers.spicedb import SpiceDBContainer

with SpiceDBContainer() as spicedb:
    client = connect(spicedb.get_grpc_endpoint())
    # run tests...
```

**GitHub Actions CI:**
```yaml
# .github/workflows/test.yml
- uses: authzed/action-spicedb@v1
  with:
    version: "latest"
- uses: authzed/action-spicedb-validate@v1
  with:
    validations-file: assertions.yaml
```

## Tips

- Cover all permissions for each resource
- Include both positive and negative tests
- Test hierarchical inheritance if present
- Test cross-boundary isolation (tenants, orgs)
- Generate readable test names
- Use fixtures for complex setup

## Example Usage

```
User: "/spicedb-dev:test-permissions"

Claude: [Finds schema.zed]
Claude: "Found schema.zed. What test framework would you like?"

User: "Go tests"

Claude:
[Generates Go test file]
[Generates fixtures.json]
[Generates test helpers]
[Writes to tests/ directory]

"I've generated permission tests in tests/:
- authz_test.go: 25 test cases covering all permissions
- fixtures.json: Test relationship data for 3 scenarios
- helpers.go: Test utilities (setup, assertions, parsing)

To run:
  go test ./tests/...

Tests cover:
- Basic document access (owner, editor, viewer)
- Hierarchical organization access
- Negative cases (insufficient permissions, no access)
"
```

## Notes

- Follow language-specific test conventions
- Include clear test names and documentation
