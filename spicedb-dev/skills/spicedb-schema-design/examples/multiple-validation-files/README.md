# Multiple Validation Files with a Single Schema

Pattern: split a schema into `schema.zed` plus several independent
`validations/*.yaml` files that each reference the schema via `schemaFile:`,
so a single `zed validate` invocation can test the same schema from multiple
independent validation files.
Source: https://github.com/authzed/examples/tree/main/schemas/multiple-validation-files

Unlike this directory's other examples (single self-contained `.yaml` files
that each demonstrate an authorization *modeling* pattern), this example
demonstrates a `zed` CLI *technique*: organizing a larger schema project
across multiple files instead of duplicating the schema inline in every
validation file. The schema itself -- a Google Cloud Spanner IAM-style
role/permission model -- is illustrative but not the point; the point is the
project layout.

This requires zed version v0.25.0 or later.

## Layout

```
multiple-validation-files/
├── schema.zed                    # the schema, defined once
└── validations/
    ├── admin-role.yaml           # independent test: admin role grants
    └── reader-role.yaml          # independent test: reader role grants
```

Each file under `validations/` is a normal `zed validate` test file
(`relationships`, `assertions`, `validation`), except it omits the inline
`schema:` block and instead points at the shared schema with `schemaFile:`:

```yaml
schemaFile: "../schema.zed"
```

This lets you write multiple independent tests of a single schema -- for
example, one validation file per role, per tenant scenario, or per
regression case -- without copy-pasting the schema into each one and without
the risk of the copies drifting out of sync.

## Running it

Running the following from this directory:

```
zed validate validations/*
```

will validate the schema and run all validations in all files under
`validations/`.
