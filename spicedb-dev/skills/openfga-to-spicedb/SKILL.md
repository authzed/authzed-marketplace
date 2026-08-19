---
name: OpenFGA to SpiceDB
description: Use when converting an OpenFGA, Okta FGA, or Auth0 FGA authorization
  model and application to SpiceDB - supplies the construct mapping, blocker catalog,
  identifier normalization, and code mapping needed to convert the schema, data, and
  application code
---

# OpenFGA to SpiceDB

The conversion pack for OpenFGA and its hosted variants (Okta FGA, the deprecated Auth0
FGA). It supplies the source-specific half of the migration pipeline defined by the
`migrating-to-spicedb` skill; that skill owns the phases, the gate, and the finding
taxonomy, and this pack owns everything that mentions OpenFGA by name.

The DSL is identical across all three variants. **Target OpenFGA and nothing changes** --
Okta FGA's deltas are entirely in its data plane (store CRUD and the AuthZEN surface are
absent; it adds four SaaS-only endpoints; its token issuer appears in official sources as
both `auth.fga.dev` and `fga.us.auth0.com`, and either is valid). Those deltas matter in
phase 4, not phase 1.

## Reference files

| Need to... | Read this |
|---|---|
| Translate a model construct into `.zed` | `references/schema-mapping.md` |
| Decide what a name becomes, or how object IDs are encoded | `references/naming-normalization.md` |
| Detect and resolve a Class A hard blocker | `references/blockers.md` |
| Extract, transform, and load a live store's relationship data into SpiceDB | `references/data-mapping.md` |
| Convert `.fga.yaml` test/assertion files into SpiceDB validation YAML | `references/test-mapping.md` |
| Implement the differential-harness seam `/spicedb-dev:migrate-verify` emits from -- dual-run, replay, and the OpenFGA-to-five-state `Outcome` mapping | `references/source-adapter.md` |

## Status: what this pack covers today

This pack is hardened by running it against a real corpus, not by authoring reference
material from research (spec decision D11). Rules are written when a real store forces
them, so the absence of a rule is information: it means nothing in the corpus has needed
it yet, not that the construct is unsupported.

| Pack-contract item | State |
|---|---|
| 1. Detection | in this file |
| 2. Model extraction | in this file |
| 3. Schema mapping | `references/schema-mapping.md` -- **clean constructs only** |
| 4. Blocker catalog | `references/blockers.md` -- the Class A blockers (read the file for the list; it is added to as new shapes are found) |
| 5. Naming normalization | `references/naming-normalization.md` |
| 6. Data mapping | `references/data-mapping.md` -- consumed by `/spicedb-dev:migrate-data` (phase 3) |
| 7. Code mapping | `references/code-mapping.md` -- consumed by `/spicedb-dev:migrate-code` (phase 4) |
| 8. Test mapping | `references/test-mapping.md` -- consumed by `/spicedb-dev:migrate-tests` (phase 5) |
| 9. Scoping questionnaire | in this file |
| 10. Validation corpus | in this file |

Every item in this table has both a reference file and a shipped consumer:
`/spicedb-dev:migrate` (phase 0 -- the `migration-analyzer` agent's scan and the
pre-flight gate, which additionally reads item 9), `/spicedb-dev:migrate-schema` (phases 1
and 2), `/spicedb-dev:migrate-data` (phase 3), `/spicedb-dev:migrate-code` (phase 4), and
`/spicedb-dev:migrate-tests` (phase 5). Item 7 has no corpus behind it yet, unlike items 3,
6, and 8 -- see `references/code-mapping.md`'s own scope section and the Validation corpus
section below.

**`references/source-adapter.md` is an eleventh deliverable, outside this ten-item numbering
on purpose.** The pack contract (`migrating-to-spicedb/references/pack-contract.md`) defines
exactly ten items for the conversion pipeline (phases 0-5); `source-adapter.md` implements the
seam `migrating-to-spicedb/references/differential-harness.md` defines for cutover-time
verification instead, consumed by `/spicedb-dev:migrate-verify`, not by any of phases 0-5. It
is shipped and has a shipped consumer the same as the ten items above, just not one of them.

If a model uses a construct no rule covers, **stop and report it as an unhandled
construct** rather than improvising a translation. That report is the input to the next
round of hardening.

### The parity harness is not part of this plugin

Several files in this pack cite `tools/migration-harness/` -- a small Python tool that
replays a store's `.fga.yaml` assertions against the converted SpiceDB validation YAML and
diffs the two. It is **not shipped with the plugin** and there is nothing for a user of
this pack to run. It lives in the plugin's source repository,
[`authzed/authzed-marketplace`](https://github.com/authzed/authzed-marketplace), under
`tools/migration-harness/`, together with `corpus-runs/`, which holds the converted
artifact and the written findings for each of the 39 stores.

Read every harness citation in this pack as **evidence of how a rule was verified**, not
as a step to perform. To reproduce one: clone that repository, run `./fetch-corpus.sh` to
fetch `openfga/sample-stores` (the corpus is gitignored, not vendored), and follow
`tools/migration-harness/corpus-runs/README.md`. Requires `uv`, Python 3.12, `zed`, and
the `fga` CLI.

## Detection

Any one signal is enough to suspect OpenFGA; confirm with a model file.

**Model files** (strongest signal):

- `*.fga` -- the DSL. **The extension is a convention, not a guarantee** -- a real
  production Go project ships its model at
  a file named `*_model.openfga` -- extension `.openfga` -- confirmed by its `model` /
  `schema 1.1` header. If `*.fga` finds nothing, also try `*.openfga` before concluding
  there is no standalone DSL file.
- `*.fga.yaml` -- test/tuple files
- `fga.mod` -- a modular model split across several `.fga` files via `module` /
  `extend type`
- an authorization model as JSON, usually checked in next to the code that calls
  `writeAuthorizationModel` -- but not always checked in as a file at all: it may instead
  be embedded as a string literal inside a source file with no standalone `.json` anywhere
  (see "Model extraction" below).

**Dependencies / imports:**

| Language | Signal |
|---|---|
| TypeScript / JS | `@openfga/sdk`, `@openfga/syntax-transformer`, `@auth0/fga` (deprecated) |
| Python | `openfga-sdk`, `openfga_sdk` |
| Go | `github.com/openfga/go-sdk` |
| Java | `dev.openfga:openfga-sdk` |
| .NET | `OpenFga.Sdk` |

**Client shapes** -- all are live and each rewrites differently in phase 4; one of them (an embedded in-process OpenFGA server) has no client to rewrite at all and makes phase 4 an architectural decision rather than a mechanical one
(`/spicedb-dev:migrate-code`), so record which one is in use:

- `OpenFgaClient` -- flattened camelCase inputs, snake_case responses
- `OpenFgaApi` -- raw wire shapes (`tuple_key`, `writes.tuple_keys`); store ID lives in
  config on older SDK versions and is an explicit first argument on newer ones
- `Auth0FgaApi` (`@auth0/fga`, deprecated) -- keyed on
  `environment: "us" | "staging" | "playground"`

**Config:** `FGA_STORE_ID`, `FGA_API_URL`, `FGA_MODEL_ID`, `api.us1.fga.dev` (Okta FGA).

## Model extraction

The model exists in several forms, all of which must be checked -- the `migration-analyzer` agent's definition
(`spicedb-dev/agents/migration-analyzer.md`), "Step 2: Locate and read the complete model" table, enumerates them with a detection command each, and is the
authority; the list grows as new shapes are found, so it is cited rather than counted here. Get the complete model
before classifying a single finding -- every later decision depends on it.

| Form | How to read it |
|---|---|
| Standalone DSL | Read the file, conventionally `model.fga`, referenced from a sibling store file's `model_file:` key. A modular model's entry point is `fga.mod` -- not any single `.fga` file -- whose `contents:` list names the files that compose it. The extension is a convention, not a guarantee: if `*.fga` finds nothing, also glob `*.openfga` (that project's real, actively-used model uses this extension) before concluding there is no standalone DSL file. |
| **Inline `model: \|`** | A top-level block embedded directly inside a `.fga.yaml` store file, with no `model_file:` key and no `.fga` file anywhere in the directory. |
| Authorization-model JSON | The wire format written by `writeAuthorizationModel`. Same semantics, different shape: `type_definitions[].relations` plus `metadata.relations[].directly_related_user_types`. |
| **Embedded in application source** | No file glob finds this: the JSON form may be a string literal or constant inside a source file, with no standalone `.json` anywhere in the repo. Confirmed in a real production Go project: `var authModel = `{"schema_version":"1.1",...}`` `, embedded in a generated `.go` file (`// Code generated by Makefile; DO NOT EDIT.`), produced from a true `.openfga` DSL source by a build rule. Grep source files (not just `.json`) for `schema_version`/`type_definitions` together, and separately locate every `writeAuthorizationModel`/`WriteAuthorizationModel` call site to trace what value it's passed. When a generator names the true source, read that instead of the generated literal -- it's the file a human edits. |
| Live store | `fga model get --store-id <id>` (DSL) or `fga model get --store-id <id> --format json`. The only form with no on-disk file to locate. |

**The inline form is the one that gets missed, and it is not rare.** A scan that globs only
`*.fga` finds nothing at all in **12 of the 39** `openfga/sample-stores` stores, because those
12 carry their model inline -- against 26 standalone and 1 (`modular`) using an `fga.mod`
manifest; the three sets are disjoint and sum to 39 (`pack-contract.md` item 2 carries the
derivation). Verified from the corpus checkout:

```
$ python3 -c "
import pathlib
root = pathlib.Path('corpus/sample-stores/stores')
none_ = [d.name for d in sorted(root.iterdir())
         if d.is_dir() and not (list(d.glob('*.fga')) + list(d.glob('fga.mod')))]
print(len(none_), none_)
"
12 ['abac-with-rebac', 'advanced-entitlements', 'banking', 'condition-data-types',
    'developer-portal', 'groups-resource-attributes', 'ip-based-access', 'modeling-guide',
    'multitenant-rbac', 'role-assignments', 'superadmin', 'temporal-access']
```

Two rules:

- **Parse with the real grammar, not a hand-rolled one.** Published grammars are
  permissive in ways real parsers are not; a docs-driven converter silently
  mistranslates real models. Prefer `fga model get` / the OpenFGA CLI to normalize the
  model before converting it.
- **`schema 1.0` is a hard error.** It is rejected by the current OpenFGA server and is
  out of scope for this pack. Point the user at OpenFGA's own 1.0 → 1.1 upgrade path
  first; convert afterward.

Tuple *types* are implicit in OpenFGA -- a tuple is legal iff it matches a
`directly_related_user_types` entry -- so phases 3 and 5 need the model too, not just the
tuple stream. That is why phase 1 emits `migration-map.json` alongside `schema.zed`.

## Scoping questionnaire

The cheapest high-value output this pack has. It is roughly a day of work and it produces
the actual estimate, before any conversion runs.

1. **The model itself.** Count `type` declarations and `define` lines. Count how many
   `define`s mix a `[...]` type list with an operator -- each of those is a relation split
   (`references/schema-mapping.md`), and each split is a data rewrite and a code rewrite.
2. **`grep -rn 'contextualTuples\|contextual_tuples\|ContextualTuples'`** across the whole
   codebase. Contextual tuples are invisible in the model and are the single largest
   source of unplanned work. A non-zero count changes the shape of the migration.
3. **Count store IDs.** More than one store in config, code, or environment means a
   tenancy decision (`references/blockers.md`).
4. **`grep -rniI 'authorizationmodelid\|authorization_model_id\|FGA_MODEL_ID'`.** Model-ID
   pinning has no SpiceDB analogue.
5. **Count `.fga.yaml` files and their `tests[]` entries.** These are the migration's
   free oracle: every assertion true in OpenFGA must be true in SpiceDB. Count
   `list_objects` **and `list_users`** assertions separately from `check:` assertions --
   only `check:` converts to a boolean `assertTrue`/`assertFalse` line, so the other two
   are the part of the oracle a converted validation file does not carry. Counting only
   `list_objects` undercounts the blind spot badly: of the four assertions `github`'s
   converted file cannot carry, three are `list_users` and one is `list_objects`. **The two
   are lost equally** -- neither converts. A `list_users` block's expected subject set does
   *not* map to a validation YAML `validation:` block: that block needs a per-subject
   resolution path (`"[user:alice] is <document:doc1#viewer>"`), which `list_users` never
   records and no `zed` flag computes offline (disproven live; see
   `references/test-mapping.md`, "Two corrections to a naive reading of this table").
   Both stay Class C advisory findings, verified after deployment --
   `list_objects` via `LookupResources`, `list_users` via `LookupSubjects`/`Expand`.

Report these five numbers at the gate. They predict most of the cost.

## Validation corpus

Two different facts, kept apart on purpose: what this pack has actually been run against,
and what it is meant to be run against.

### What was actually run

**All 39 stores** in [`openfga/sample-stores`](https://github.com/openfga/sample-stores)
have been converted, deployed to SpiceDB v1.56.0, and checked assertion-by-assertion against
their own `.fga.yaml` oracle. **38 of 39 reach `PARITY OK`** against that canonical file
(mechanical: `grep -c '^\*\*Final harness run' corpus-runs/README.md` → 39, piped to
`grep -c 'PARITY OK'` → 38). The exception is `abac-with-rebac`: its canonical run against
its own `store.fga.yaml` exits **1** with **`PARITY FAILED`** (two `AMBIGUOUS` findings) --
a documented harness limitation (two mutually exclusive document states flattened into one
comparison), not a conversion defect. The same schema and data reach `PARITY OK` against the
two derived per-scenario store files committed alongside it; see `corpus-runs/README.md`'s
`abac-with-rebac` section. The full run -- every store's own findings, every accounting
correction, and the command that reproduces every comparative claim in this section --
lives in the plugin's source repository at `tools/migration-harness/corpus-runs/README.md`,
not shipped with the plugin; the summary below is drawn from that record, not a substitute
for it.

**The honest summary is that the corpus is exhausted and the pack survived it -- not that the
pack has converged.** 21 of the 39 stores required no pack change at all; 18 filed at least
one finding (`corpus-runs/README.md`'s per-store `### Findings` sections, mechanically
`None.` vs. not -- 39 sections, one per store). The longest unbroken run of zero-finding
stores is **8**, spanning iterations 17 and 18 back to back (batches 6 and 7, 4 stores each);
no longer run exists once same-iteration stores are correctly treated as unordered rather
than assumed to fall in a favorable sequence. **No new *mapping* rule has been forced by a
conversion since iteration 11** (`groups-resource-attributes`) -- every finding from
iteration 12 onward was a worked example or a documentation clarification of a rule already
on file, not a new construct. That is evidence of diminishing returns on this specific
39-store sample, not proof there is nothing left: a construct this pack has never seen is
exactly as unhandled on a 40th store as it was on the first.

**One exception, and it matters more than a mapping rule would:** at iteration 17, a
safety-critical rule was added that a conversion did not force -- the four stores converted
in that same batch were all zero-finding. It is the multi-type-tupleset `__perm`-alias gap in
"Point arrows at permissions, not relations" (`references/schema-mapping.md`): a silent
authorization hole that compiles clean under `zed validate --fail-on-warn` and was found by
review, not by a store. `file-storage` (iteration 16) is the only store in the entire corpus
that uses a multi-type tupleset as an arrow's left operand at all, and it is the one that
forced the underlying alias *rule*; the mechanical detection *script* that catches a
violation was hardened twice more afterward by further review, closing parsing blind spots
rather than responding to a new store. Read that section for exactly what the script does and
does not catch -- it is not being oversold here.

Coverage is uneven across construct families, and the unevenness is informative. Restricted
to the 21 zero-finding stores: wildcards (`knowledge-base`), SpiceDB intersection
(`developer-portal`), type-based multi-tenancy, nested-userset arrow chains, and same-name
recursive arrows are each confirmed clean by at least one store. Two families are not:
**every one of the 8 caveat-bearing stores in the corpus filed at least one finding** --
caveats have never once converted clean -- and the multi-type-tupleset construct has exactly
one bearer in the whole corpus (`file-storage`), which filed a finding too. Neither absence
means the underlying rule is wrong; it means "zero-finding" and "exercised" are different
claims, and only the first is evidence that a construct is easy.

**Several written rules carry zero corpus validation, not just thin validation.** No
committed store's schema exercises the `a - b` exclusion operator (`define view: a but not
b`, rated `clean`) at all. Of the Class A blockers in `references/blockers.md`, **the
transitive wildcard and contextual tuples now have corpus confirmation** (`role-assignments`
and `abac-with-rebac` respectively -- each has its own "Corpus confirmation" passage in that
file, which is the authority on how far the confirmation reaches). **Multi-store tenancy and
model-ID pinning do not**: they remain real, written rules with no corpus store to confirm
the rating against, because both are properties of how an application is *deployed* rather
than of any model a store can carry. Every other rule in `references/schema-mapping.md`,
`references/naming-normalization.md`, and `references/blockers.md` traces to one of the 39
runs above; the exclusion-operator gap and those two blockers do not, and should be treated
as unverified rather than as untested-because-unlikely.

**That is survivable because the pack halts instead of guessing.** A construct with no
rule is reported as an unhandled construct and stops the conversion
(`/spicedb-dev:migrate-schema`, step 5). The failure mode of an unfinished pack is
therefore a stop with a source line attached, not a schema that compiles cleanly and
answers a question differently than OpenFGA did. Treat every halt as the next hardening
input.

The oracle is also partial where it did run: the parity check compares `check:` assertions
only, which is **1436 of 1777 source assertions (80.8%)** summed across all 39 stores from
`corpus-runs/README.md`'s own "Harness-visible fraction" derived set (`Checks / (Checks +
ListObjects + ListUsers)`, per store, from `fga model test`), and as low as 33.3% on
`gdrive`. `list_objects` and `list_users` blocks are not compared by the automated harness
(see the scoping questionnaire, item 5) -- every store's gap was closed instead by direct
live-server verification, recorded in that store's own `corpus-runs/README.md` section.

### What has not been run

**Tier 2 -- application code: zero repositories.** `theopenlane/core` (Go, a production
`fga/` subsystem with its own schema codegen), `openfga/flask-demo` (Python, minimal), and
`embesozzi/keycloak-openfga-workshop` (JS, event-driven) are the intended Tier 2 and have
not been touched. `references/code-mapping.md` and its consumer, `/spicedb-dev:migrate-code`
(phase 4), are both written now (see Status) -- what's missing is exercising either against
a real, pre-existing codebase's call sites, a different and harder test than the clean,
live-verified worked examples both currently rest on.

**Prior art, read but not run:** [`openfga/agent-skills`](https://github.com/openfga/agent-skills)
-- OpenFGA's own model-authoring skills. Models authored with it follow its idioms, so this
pack has to handle them.

## Target version floor

SpiceDB **v1.52.0+**. CVE-2026-40091 yanked v1.49.0 through v1.51.0 (v1.51.1 is the
remediation), and v1.52.0 folded partials and imports into `pkg/schemadsl`. Everything in
this pack was re-verified against SpiceDB v1.56.0 and zed v0.31.1.

## What this pack does NOT do

- Own the pipeline, the gate, or the finding taxonomy -- that is `migrating-to-spicedb`.
- Design a SpiceDB schema from scratch -- that is `spicedb-schema-design`. This pack
  converts an existing model; it does not redesign one.
- Teach SpiceDB client usage -- that is `spicedb-client-integration` /
  `spicedb-best-practices`. This pack's `references/code-mapping.md` cites that skill for
  how to use a target call once the mapping names it, rather than re-teaching it.
- Own how the SpiceDB client is obtained or wired into a project -- that is
  `spicedb-client-integration/references/installation.md`, which `/spicedb-dev:migrate-code`
  invokes.

Data, code, and test conversion are all supported today via `/spicedb-dev:migrate-data`
(phase 3), `/spicedb-dev:migrate-code` (phase 4), and `/spicedb-dev:migrate-tests`
(phase 5).

## Red Flags

If you find yourself:

- **Inventing a translation** for a construct with no rule in `references/schema-mapping.md`
  -- stop and report it as an unhandled construct. A guessed rule that validates cleanly is
  worse than a halt, because nothing downstream will catch it.
- **Converting past an unresolved Class A finding** -- that is exactly what the gate
  exists to prevent.
- **Reading the arrow backwards.** `member from parent` becomes `parent->member`. The
  operand order reverses, and this is the most likely translator bug.
- **Trusting a schema because it compiled.** `zed validate` accepting the output is
  necessary, not sufficient: it does not prove the converted model answers the same
  questions the OpenFGA one did. The `.fga.yaml` assertions are the real oracle.

---

**Workflow summary:** Detect OpenFGA → extract the complete model → run the scoping
questionnaire → resolve Class A blockers at the gate (`/spicedb-dev:migrate`, phase 0) →
convert with `references/schema-mapping.md` and `references/naming-normalization.md` via
`/spicedb-dev:migrate-schema` → validate → `/spicedb-dev:migrate-data` (phase 3) migrates
the relationship data using `references/data-mapping.md`, `/spicedb-dev:migrate-code`
(phase 4) adds the SpiceDB client and rewrites call sites using `references/code-mapping.md`,
and `/spicedb-dev:migrate-tests` (phase 5) converts the test fixtures using
`references/test-mapping.md` -- each reading `migration-plan.md` and `migration-map.json`
rather than re-asking, and phase 4 additionally importing phase 3's emitted ID codec when a
type needs id encoding. Once phase 3 has passed verification, `/spicedb-dev:migrate-verify`
emits a differential harness using `references/source-adapter.md` to translate between
OpenFGA's answers and `migrating-to-spicedb/references/differential-harness.md`'s five-state
vocabulary, so the converted system can be dual-run and shadow-read against real traffic
before cutover.
