---
name: Authorization Testing
description: Use when writing tests for SpiceDB authorization, generating test fixtures,
  designing positive/negative test scenarios, or verifying permission correctness - provides
  patterns for fixture generation, integration testing, and negative test coverage
---

# Authorization Testing

This skill provides guidance on testing SpiceDB-based authorization systems, including test data fixture generation, test scenario design, and integration testing patterns.

## Overview

Testing authorization requires:
- **Test fixtures**: Relationship data representing test scenarios
- **Test scenarios**: Expected permission outcomes
- **Integration tests**: End-to-end authorization flows
- **Negative tests**: Verify denials work correctly

Good authorization tests cover both positive cases (access granted) and negative cases (access denied), and test edge cases like hierarchical inheritance and complex permission rules.

## Quick Reference

| Need to... | Read This |
|-----------|-----------|
| Find test scenario patterns | `references/test-patterns.md` |
| Generate fixture data | `references/fixture-generators.md` |
| See a complete Go test suite | `examples/go_test.go` |
| See SpiceDB assertion tests | `examples/assertions.yaml` |
| See load/perf testing | `examples/load_test.py` |

## Test Fixture Generation

Fixtures establish the relationship graph for testing. Organize by scenario, using
your schema's actual definition and relation names (not generic placeholders).

See `references/fixture-generators.md` for fixture structure patterns and helper
functions in Go, Python, and TypeScript.

Use `/spicedb-dev:test-permissions` to generate fixtures directly from a schema.

## Test Scenarios

See `references/test-patterns.md` for the complete scenario library with 14 patterns covering:
direct access, role-based access, hierarchical inheritance, sharing (wildcard and domain),
complex multi-path, revocation, edge cases, caveated permissions, and CI integration patterns.

## Integration Testing

Integration tests verify authorization in the context of your application.

**Pattern:** Setup, execute, verify, cleanup.

See `examples/go_test.go` for a complete Go test suite demonstrating:
- Client setup with `serve-testing` isolation (unique preshared key per test)
- Schema loading, relationship writing, and permission checking helpers
- Table-driven positive and negative test cases
- Revocation testing

See `references/fixture-generators.md` for Go, Python, and TypeScript helper functions.

### Testing with Assertions

Use SpiceDB's built-in assertion testing (see `examples/assertions.yaml` for a
complete example). Run with `zed validate assertions.yaml`.

Supports three assertion types:
- `assertTrue` -- must return HAS_PERMISSION
- `assertFalse` -- must return NO_PERMISSION
- `assertCaveated` -- caveat exists but context not fully provided; result is conditional

## Red Flags

If you find yourself:
- Unsure how to structure a complex test scenario → Read `references/test-patterns.md`
- Writing fixture setup inline instead of reusing helpers → Read `references/fixture-generators.md`
- Only writing positive tests → Remember: every positive needs a corresponding negative
- Guessing about SpiceDB consistency behavior in tests → Use `FullyConsistent` in tests to avoid flakiness

## What This Skill Does NOT Do

- Design the permission schema -- use `spicedb-schema-design` skill for that
- Implement client authorization calls -- use `/spicedb-dev:implement-spicedb-checks` or `/spicedb-dev:implement-spicedb-relationships` for that
- Generate test fixtures interactively -- use `/spicedb-dev:test-permissions` for that

## Negative Testing

For every positive test (access granted), write a corresponding negative test (access
denied). Key denial patterns: no relationship, insufficient role, revoked access,
cross-tenant isolation, hierarchical boundary.

See `references/test-patterns.md` for the full pattern library with 14 scenarios.

## Performance Testing

See `examples/load_test.py` for a load test with latency reporting. Targets:
CheckPermission <10ms (p95), WriteRelationships <50ms (p95), LookupResources <100ms (p95).

## Additional Resources

### Reference Files

For detailed testing patterns:
- **`references/test-patterns.md`** - Comprehensive test pattern library
- **`references/fixture-generators.md`** - Fixture generation utilities

### Example Tests

Working test examples in `examples/`:
- **`examples/go_test.go`** - Complete Go test suite
- **`examples/assertions.yaml`** - SpiceDB assertion tests
- **`examples/load_test.py`** - Performance testing examples

### External Resources

- [SpiceDB Testing Guide](https://authzed.com/docs/spicedb/modeling/testing) - Official testing documentation
- [Zed Validation](https://authzed.com/docs/spicedb/modeling/validation) - Schema and assertion validation

---

**Workflow summary:** Define test scenarios → Create fixtures → Write positive tests → Write negative tests (one per positive) → Add edge cases → Run with assertion file or integration test. Always fail-safe: deny by default, test denials as rigorously as grants.
