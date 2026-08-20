---
name: SpiceDB Client Integration
description: Use when adding a SpiceDB client library to a project in Go, Python,
  TypeScript, C#, Java, Rust, or Ruby - covers obtaining the prototype client and
  the common patterns (relationships, consistency, streaming lookups) shared across
  all seven languages
---

# SpiceDB Client Integration

This skill helps you add a SpiceDB client to any project, in any of the seven languages
SpiceDB ships client libraries for: Go, Python, TypeScript, C#, Java, Rust, and Ruby. It is
general-purpose -- use it any time a codebase needs to talk to SpiceDB, whether that's a new
integration or one step in a larger migration.

## Prototype status

**The seven clients this skill covers are a prototype**, vendored at a pinned commit rather
than installed as published packages. APIs, types, and behaviors may change or break upstream
at any time. See `references/installation.md` for the exact commit, why vendoring is the only
supported path *for these clients*, and what changes (and what doesn't) once they are
published. Every other file in this skill assumes you've already read that one.

**This is not a claim that SpiceDB has no published clients.** Authzed's established client
family is generally available and is what the rest of this plugin uses -- `authzed-go`
(`v1.10.0`), `@authzed/authzed-node` (`1.6.1`), `authzed` on PyPI (`1.25.0`), `Authzed.Net`
(`1.6.0`), all verified live and tabled in `spicedb-best-practices/references/
client-patterns.md`, which is where `/spicedb-dev:implement-spicedb-checks` and its siblings
already point. Use this skill when you specifically want the prototype's API, or one of the
three languages only it covers (Java, Rust, Ruby); use the established client otherwise.
`references/installation.md` has the per-registry evidence for both halves of that split.

## Overview

Getting a SpiceDB client working in a project has two parts:

1. **Obtain the client.** All seven languages are vendored the same way -- the language's
   client directory plus its sibling `proto-clients/` directory, at one pinned commit.
   `references/installation.md` is the *only* file in this skill that covers this; it's
   written so that when the clients are published, that file changes to package installs and
   nothing else here moves.
2. **Use the client correctly.** The seven idiomatic clients differ in naming convention
   (`NewPlaintext` vs `CreatePlaintext`, `snake_case` vs `camelCase`) but share the same
   underlying types and concepts. `references/core-concepts.md` covers that shared vocabulary
   once, so you don't need to relearn it per language.

## Quick Reference

| Need to... | Read this |
|-----------|-----------|
| Obtain the client for any language (vendoring, pinned commit, prototype status) | `references/installation.md` |
| Shared vocabulary: `Relationship`/`Filter`/`Transaction`, consistency helpers, streaming iteration | `references/core-concepts.md` |
| `LookupResources` limits that affect UI/product design (no total count, duplicate resource IDs whether or not you paginate, 1000-per-call cap) | `references/core-concepts.md` |

### Per-language references

This skill routes by language for anything beyond the shared vocabulary. All seven are
written; each lives at `references/<language>.md`:

| Language | Reference |
|----------|-----------|
| Go | `references/go.md` |
| Python | `references/python.md` |
| TypeScript | `references/typescript.md` |
| C# | `references/csharp.md` |
| Java | `references/java.md` |
| Rust | `references/rust.md` |
| Ruby | `references/ruby.md` |

Each one is verified against a live `spicedb serve-testing` run in that language. For anything
those seven don't cover, the vendored client's own `examples/` directory (see
`references/installation.md`) is the next stop -- every idiomatic client ships runnable
examples for construction, writes, checks, and lookups.

## Typed wrappers (`spicedb-gen`)

Four of the seven languages -- Go, Java, Python, TypeScript -- have an additional option:
`spicedb-gen` reads a `.zed` schema and generates a compile-time-checked wrapper (invalid
resource types, permissions, or subject types become compiler errors instead of runtime
`PermissionDenied`s). C#, Ruby, and Rust don't have generator support; use the idiomatic client
directly. `references/installation.md` covers obtaining `spicedb-gen` alongside the client
you're vendoring.

**Read `references/spicedb-gen.md`'s "Known limitations" before adopting it.** A
self-referential resource type -- `relation parent: folder` inside `definition folder`, one of
the most common hierarchy shapes there is -- crashes the generator outright in all four
languages (`exit 2`, `goroutine stack exceeds 1000000000-byte limit`). That file is the only
place this is recorded, a fix is in progress upstream, and until it lands the typed wrapper is
an option to evaluate rather than a default to reach for.

## Red Flags

If you find yourself:
- Reaching for a package manager to install **this** client -- `npm install @spicedb/client`,
  `pip install spicedb`, `cargo add spicedb`, `go get` on the prototype's repo path. None of
  the seven is published, and two of those names resolve to *other people's* libraries (PyPI
  `spicedb` and NuGet `SpiceDb` are both unofficial third-party clients), so the install
  succeeds and gives you the wrong code. Read `references/installation.md` for the vendoring
  path -- and note that `npm install @authzed/authzed-node` (or `pip install authzed`,
  `go get github.com/authzed/authzed-go`) is a perfectly good thing to do; it just gets you
  Authzed's established client, which this skill is not about.
- Copying a consistency level from another call site without thinking about it -- read
  `references/core-concepts.md`'s consistency section; the wrong default causes either stale
  reads or needless latency.
- Building a "Showing 1-20 of 150" pager on top of `LookupResources` -- read
  `references/core-concepts.md` first; that count does not exist.
- Deduplicating `LookupResources` results only when you notice duplicates in testing -- it's
  not an edge case, it's mandatory whenever a resource is reachable through more than one
  relation; see `references/core-concepts.md`.
- Restating what a client's own comments or documentation claim it does, instead of what its
  compiled signatures do -- prose and code have already diverged for at least one of the seven
  clients (see `references/core-concepts.md`'s "Trust the code, not the docs" section). Trust
  the code.

## What This Skill Does NOT Do

- Map OpenFGA concepts onto SpiceDB clients, or otherwise cover migration-specific vocabulary
  -- that lives in a separate migration pack, deliberately, so this skill stays useful to any
  project adding a SpiceDB client, migration or not.
- Design SpiceDB schemas -- use `spicedb-schema-design` for that.
- Cover consistency models, retries, caveats, and performance tuning in depth for
  already-published clients -- `spicedb-best-practices` covers that ground for Go/TypeScript/
  Python assuming a normal package install; this skill exists because the real clients aren't
  installed that way yet, and cover three more languages besides.
- Write authorization tests -- use `authorization-testing` for that.

## Additional Resources

### Reference Files

- **`references/installation.md`** -- obtaining the client (the only file that covers this),
  including which packages *are* published and which are other people's
- **`references/core-concepts.md`** -- vocabulary shared across all seven languages, plus the
  `LookupResources` product-level limits
- **`references/<language>.md`** -- one per language: `go`, `python`, `typescript`, `csharp`,
  `java`, `rust`, `ruby`
- **`references/spicedb-gen.md`** -- the typed-wrapper generator: what it emits per language,
  and its **Known limitations**, including the self-referential-type crash

### External Resources

- [SpiceDB Clients](https://authzed.com/docs/spicedb/getting-started/clients) -- official
  client documentation (for the eventual published packages)
- [Consistency Explained](https://authzed.com/docs/spicedb/concepts/consistency) -- consistency
  model details

---

**Workflow summary:** Read `references/installation.md` and vendor the client for your
language (client directory + sibling `proto-clients/` directory, at the pinned commit) -->
read `references/core-concepts.md` for the shared types, consistency helpers, and
`LookupResources` limits --> find your language's reference for idiomatic patterns, or fall
back to the vendored client's own `examples/` directory if it isn't written yet.
