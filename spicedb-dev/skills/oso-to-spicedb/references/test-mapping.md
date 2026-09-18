# Test mapping: Polar `test` blocks → SpiceDB validation YAML

Pack contract item 8. Oso's policy tests convert unusually well -- this is the cheapest
high-trust artifact in an Oso migration and it gives the customer a green check on day one.

## The source shape

Polar test blocks live in the policy file itself:

```polar
test "lab staff can download datasets and their runs" {
    setup {
        has_role(User{"ana"}, "staff", Lab{"genomics"});
        has_relation(Dataset{"atlas"}, "lab", Lab{"genomics"});
        has_relation(Run{"Run 7"}, "dataset", Dataset{"atlas"});
    }

    assert allow(User{"ana"}, "download", Dataset{"atlas"});
    assert allow(User{"ana"}, "download", Run{"Run 7"});
    assert_not allow(User{"ana"}, "annotate", Dataset{"atlas"});
}
```

Three parts, and each has a direct counterpart:

| Polar | SpiceDB validation YAML |
|---|---|
| `setup { ... }` facts | `relationships:` block |
| `assert allow(a, p, r)` | assertion under `assertTrue` |
| `assert_not allow(a, p, r)` | assertion under `assertFalse` |
| test name | comment above the group |

## Two shapes the documentation does not show, and real policies use

**`test fixture` blocks.** A policy can declare reusable fixtures and pull them into a
test's `setup` by name, including several at once:

```polar
test fixture baseline {
  has_role(User{"ana"}, "staff", Lab{"genomics"});
}

test fixture atlasDataset {
  has_role(User{"ana"}, "curator", Dataset{"atlas"});
}

test "dataset roles" {
  setup {
    fixture baseline;
    fixture atlasDataset;
    has_relation(Run{"run-12"}, "dataset", Dataset{"atlas"});
  }
  ...
}
```

SpiceDB validation YAML has no fixture mechanism, so **resolve them: inline the union of
every referenced fixture plus any inline facts, into that test's `relationships:` block.**
Resolve recursively if a fixture references another. Two tests that share a fixture become
two independent YAML files with the shared facts duplicated -- that is correct, not
wasteful, because each file is self-contained.

Do not convert a `test fixture` block into anything on its own. A fixture no test
references contributes no relationships; note it rather than emitting it.

**`assert ... iff ... in [...]` -- a parameterized assertion.** This binds the action as a
variable and asserts the permitted set *exactly*:

```polar
assert allow(User{"raj"}, action: String, Dataset{"atlas"}) iff
  action in ["download", "runs.list", "annotate"];
```

`iff` is if-and-only-if, so this is **two** claims, and converting only the first loses the
half that catches over-permissioning:

- every action in the list is allowed -- one `assertTrue` each;
- **every other declared permission of that resource type is denied** -- one `assertFalse`
  each, enumerated from the type's `permissions` list.

Expand both halves. A conversion that emits only the `assertTrue` lines produces a file
that passes while a schema granting *more* than the policy did would also pass, which is
the failure this assertion was written to catch.

## The conversion

`setup` facts convert exactly as `data-mapping.md` prescribes -- **including the split
rule**: a `has_role` fact in `setup` writes to the `__direct` relation, while the
`assert` that follows checks the *permission*. Getting this backwards produces a file
that validates and tests nothing, because the assertion resolves through a permission the
setup never populated.

Worked, from the block above:

```yaml
schema: |
  <the converted schema>

relationships: |
  lab:genomics#staff__direct@user:ana
  dataset:atlas#lab@lab:genomics
  run:run_7#dataset@dataset:atlas

assertions:
  assertTrue:
    # lab staff can download datasets and their runs
    - lab:genomics#browse@user:ana
    - dataset:atlas#download@user:ana
    - run:run_7#download@user:ana
  assertFalse:
    - dataset:atlas#annotate@user:ana
```

Note `Run{"Run 7"}` → `run:run_7`: the object id contained a space, which is legal
in Oso and illegal in SpiceDB. Run ids through the `id_encoding` decision, not through an
ad-hoc replacement, and record it -- an id rewritten only in the test file will not match
the one the application writes.

**Emit assertion lines unquoted.** Where a check carries caveat context the canonical JSON
contains double quotes, and a double-quoted YAML scalar around it is invalid YAML.

## What does not convert

**A test whose assertions exercise a `blocked` construct.** If the policy's
`has_storage_remaining` rule moved into the application (`blockers.md` 5), the assertion that
tested it has no SpiceDB counterpart. Do not drop it silently and do not fabricate a
passing assertion: carry it into the plan under **Needs action** with the test name and the
construct it exercised, so the customer knows which of their existing guarantees is now
untested.

**Context facts in `setup`.** Where the conversion turned a context fact into a caveat, the
assertion needs the context supplied at check time rather than a relationship written in
setup. Where it turned one into a stored relationship, it belongs in `relationships:`.
Which one it is was decided at the gate; read it from `migration-map.json` rather than
re-deciding here.

## Coverage

Report converted-assertion coverage as a fraction, per the framework's test-mapping
discipline: how many source assertions produced a SpiceDB assertion, and name every one
that did not with the reason. A high number is not the goal -- an accurate number is,
because it tells the customer exactly how much of their existing test suite survived.
