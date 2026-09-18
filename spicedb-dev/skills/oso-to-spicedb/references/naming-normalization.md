# Naming normalization: Oso Cloud → SpiceDB

Pack contract item 5. How Oso's identifier rules reduce to SpiceDB's stricter ones.

## The asymmetry, stated exactly

**In Oso, role, permission, and relation names are not identifiers -- they are `String`
values.** Oso's own type reference says so: "By convention, Polar represents permission
names, role names, and relation names as `String` values." They live inside facts, and a
fact argument may be **up to 384 bytes**. So the legal set is, for practical purposes,
*any string*: dots, spaces, hyphens, uppercase, unicode, leading digits.

In SpiceDB the same names are **schema identifiers**, and must match:

```
^[a-z][a-z0-9_]{1,62}[a-z0-9]$
```

Lowercase, starts with a letter, ends alphanumeric, 3-64 characters, underscores only.

**This is a wider gap than it looks, and it is not just about dots.** A pack that only
rewrites `repository.create` → `repository_create` will pass on Oso's documentation
examples and fail on a real customer whose roles came from a UI text field.

Type names (`actor`/`resource` block names) are Polar identifiers rather than strings and
are conventionally `PascalCase`, so they need case folding but rarely more:
`Repository` → `repository`.

## The rule

Use the framework's `normalize_name` algorithm --
`migrating-to-spicedb/references/findings-report.md` defines it and
`migration-map.json`'s `types`/`permissions` maps record every result. This pack supplies
only the source-side inputs:

1. **Fold case.** `PascalCase` type names and any uppercase in a role or permission string
   become lowercase.
2. **Replace each illegal character with `_`.** Dots, spaces, hyphens, slashes, colons.
3. **Fix the ends.** Prefix `x` if the result starts with a digit or underscore; append a
   character if it ends with an underscore.
4. **Pad to the 3-character minimum.** A one- or two-character Oso name is legal there and
   illegal here.
5. **Truncate to 64,** counting the collision suffix.
6. **On collision, append `_` plus the first six hex characters of the sha256 of the
   original string.** Two different Oso names must never normalize to one SpiceDB name --
   they are distinct permissions and merging them silently grants access.

Record every rename in `identifier_notes` with the reason, because **call sites use the
original string** and phase 4 has to rewrite each one.

## Collisions are more likely here than for a relational source

Two mechanisms make them likely, and both are invisible if you only look at one resource
block:

- **Case.** `"Admin"` and `"admin"` are different Oso strings and the same SpiceDB
  identifier.
- **Punctuation.** `"repo.read"`, `"repo-read"`, and `"repo read"` all normalize to
  `repo_read`.

Normalize the **whole** vocabulary at once and check for collisions across it, not per
block. A role in one resource block and a permission in another can collide only if they
land in the same definition -- but global roles and the singleton pattern
(`policy-mapping.md`) put names from several blocks into one definition, so check there
specifically.

## Object ids

Oso object ids arrive as the `id` half of a `{type, id}` pair and are, again, strings up to
384 bytes. SpiceDB object ids must match:

```
^([a-zA-Z0-9/_|\-=+]{1,1024})|\*$
```

Note this is **not** the identifier regex: it permits uppercase, digits anywhere, `/`,
`|`, `-`, `=`, `+`, and is far longer. Most Oso ids pass unchanged. The ones that do not
are the usual suspects -- `@` in an email-shaped id, `.`, and anything percent-encoded by
the application on the way in.

Handle these through the framework's `id_encoding` key, and **read
`findings-report.md`'s `id_encoding` section before recording a verdict**: `mode: "none"`
means "no encoder emitted", which is equally true when ids are clean and when they are so
broken every check errors. `status` is what separates those, and a file-only sweep cannot
establish `clean` for ids built at request time.

**Do not compile the object-id pattern into Go as written.** Go's RE2 rejects repeat counts
above 1000, so `{1,1024}` panics at package init while `go build` and `go vet` both pass.
Use an unbounded class match plus an explicit length test.
