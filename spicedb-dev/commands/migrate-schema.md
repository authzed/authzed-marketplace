---
name: migrate-schema
description: Convert a source authorization model to a SpiceDB schema (.zed)
argument-hint: "[model-file] [output-dir]"
allowed-tools:
  - TaskCreate
  - TaskUpdate
  - Read
  - Write
  - Glob
  - Grep
  - Task
  - AskUserQuestion
---

# Migrate Schema

Phase 1 of the migration pipeline: convert an existing authorization model into a SpiceDB
schema, and emit the identifier map that data and test conversion later apply. After
generation, launch the `schema-validator` agent, the same way `/spicedb-dev:generate-schema`
does.

This command's job is to **convert**, and to decide as little as possible while doing it.
Every decision is meant to be made once, at phase 0's gate, and recorded in
`migration-map.json` -- the single machine-readable record every phase reads and writes all
machine state to (`migrating-to-spicedb/references/findings-report.md`) -- with
`migration-plan.md` rendered from it for a human to review. This command reads
`migration-map.json` for that state; it never parses `migration-plan.md` to decide
anything.

**Phase 0 is `/spicedb-dev:migrate`.** It runs the `migration-analyzer` agent over the
model *and* the codebase, holds the full pre-flight gate, and writes `migration-map.json`
and, rendered from it, `migration-plan.md`. When phase 0 has already run, this command
reads its `migration-map.json` and asks nothing.

This command also stays **independently runnable** for schema-only work. Run standalone
with no plan, it holds a **reduced** inline gate (step 3b) covering only this phase's own
inputs, writes `migration-map.json` and regenerates `migration-plan.md` from it, and
proceeds. **Exactly one gate runs per migration**: step 3b is skipped whenever a plan
exists, and phase 0 always writes one before phase 1 starts, so the two never both run.

Outputs, written to `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory):

- `schema.zed`
- `migration-map.json` -- the machine-readable record; rewritten in place when phase 0
  already emitted one (step 4), or written new by step 3b when run standalone
- `migration-plan.md` -- a rendering of `migration-map.json`, created if it did not already
  exist (step 3b) and regenerated in full whenever this command touches it (step 8)

## Progress Tracking

Before starting, use TaskCreate to create a task for each step. Use TaskUpdate to mark each
task `in_progress` when starting and `completed` when done.

## Process

### Step 1: Read the migration plan, if there is one

Read `migration-plan.md` from `[output-dir]` (default: the directory phase 0 wrote `migration-map.json` to, i.e. the project being migrated -- **not** the shell's current working directory) -- the same
place the Outputs section above writes it -- or the path the user gave. **`migration-map.json`
is what signals a plan exists**, not `migration-plan.md`: the Markdown is only ever a
rendering of the JSON and is safe to delete, so treating its absence as "no plan" halts this
phase on a state the pipeline considers perfectly normal (`migrate.md` step 1 states this,
and this command must not disagree with it). If `migration-map.json` is present and the
Markdown is not, re-render the Markdown from it and continue. Read `migration-map.json` for
every piece of state below except **Source**, which is Markdown-only narrative with no JSON
counterpart -- if that section is what is missing, say so and carry on rather than halting.

**If it exists**, take the decisions from it and go to step 2:

- **Source** -- read from `migration-plan.md`: which system, which pack applies, model
  location(s).
- **Decisions** -- read from `migration-map.json`'s `decisions` key: tenancy, identifier
  strategy (including object-ID encoding mode and which types it applies to, from
  `id_encoding`), relation-split naming, permission naming style, and every per-blocker
  resolution.
- **Identifier map**, **Relation splits**, and **Arrow aliases** -- read from
  `migration-map.json`'s `types`/`permissions`/`identifier_notes`, `relation_splits`, and
  `arrow_aliases` keys respectively, if a previous run already populated them.

If a plan was written by the full gate (`/spicedb-dev:migrate`), its `migration-map.json`
already carries the **Permission naming style** decision applied -- step 4 below treats
those `permissions[type]` entries as fixed, the same as any other name the gate recorded.
Nothing further to apply for that case; the note only matters for step 3b's own standalone
gate, which has to apply it itself.

**Halt on an unresolved Class A finding.** Check `migration-map.json`'s
`decisions.per_blocker_resolutions` array: if any entry has a null or absent `resolution`,
stop and list them. Never convert past one. A single bulk resolution recorded for
"contextual tuples" as a class does not resolve each call site -- `/spicedb-dev:migrate`
step 5 requires them resolved individually, per `file:line` -- so confirm every site's own
entry in the array carries a `resolution` (`findings-report.md`'s `## migration-map.json`
section, `decisions.per_blocker_resolutions`). This is a JSON read, never a parse of the
plan's rendered `## Decisions` → `### Per-blocker resolutions` table -- no phase reads that
Markdown for state, and a check written against it would find nothing and pass vacuously,
which is the opposite of what this halt is for.

**If it does not exist**, do not halt. Say once, plainly, that `/spicedb-dev:migrate` is
the full gate -- it analyzes the whole codebase, not just the model, and three of the four
Class A blockers are invisible in the model -- and offer to hand off to it. If the user
would rather convert the schema alone, continue to steps 2 and 3 (load the pack, read the
complete model), and **step 3b** will gather this phase's own decisions and write
`migration-map.json` and the plan rendered from it. Either way this command has somewhere
to go; never leave the user at a dead end.

### Step 2: Load the conversion pack

Look up the source system in the `migrating-to-spicedb` skill's source registry and load
the matching pack skill. For OpenFGA, Okta FGA, or Auth0 FGA that is
`openfga-to-spicedb`; read its `references/schema-mapping.md` and
`references/naming-normalization.md` before writing any output.

If the detected source has no pack, stop and say so. An unsupported source needs a new
pack, not an ad hoc conversion.

### Step 3: Locate and read the model

Use the `[model-file]` argument if given. Otherwise use the model location from the plan's
**Source** section, if there is a plan. Otherwise glob for **every** form the pack's model
extraction rule (`references/pack-contract.md` item 2) lists -- for OpenFGA, that is not
just one glob:

- `**/*.fga` -- a standalone DSL file, conventionally `model.fga`. **The extension is a
  convention, not a guarantee**: if this glob is empty, also try `**/*.openfga` before
  concluding there is no standalone DSL file -- a real production Go project ships its
  model in a file named `*_model.openfga`, confirmed by its `model` / `schema 1.1` header,
  and a plain `*.fga` glob misses it entirely.
- `**/*.fga.yaml` (and, more broadly, any `**/*.yaml` in a suspected OpenFGA project) --
  open each match and check for a top-level `model:` key. **12 of `openfga/sample-stores`'s
  39 stores use this form** (against 26 standalone `.fga` and one `fga.mod`), and it is the
  one most easily missed, because it produces no file named anything like "model": the
  model lives inline, as a block scalar, inside what
  looks like a test-fixture file. A match with a `model_file:` key instead of `model:` is
  not this form -- it is pointing at the standalone form above, and is not itself a second
  candidate.
- `**/fga.mod` -- a modular manifest. If present, it is **the** entry point (see below), not
  one candidate among several.
- `**/*.json` -- the authorization-model wire format. Check for top-level `schema_version`
  and `type_definitions` keys, not just the extension, since an unrelated JSON file will
  also match the glob.
- **Embedded in application source, no standalone file at all.** If every glob above comes
  back empty but a source-code signal (dependency manifest, SDK import, a
  `writeAuthorizationModel`/`WriteAuthorizationModel` call site) says this is an OpenFGA
  project, grep source files themselves for `schema_version` and `type_definitions` together
  -- not just `*.json`. one real project embeds its model's JSON form as a Go string constant
  (`var authModel = `{"schema_version":"1.1",...}`` `) inside a generated file (`// Code
  generated by Makefile; DO NOT EDIT.`); no `.json` file exists anywhere in the repo to glob
  for. When the generator names a true source (a `go:generate` line, a Makefile rule, a
  header comment), read that file instead -- it is the one a human edits. Otherwise, read the
  embedded literal itself as the model. Do not report "no model found" from empty globs
  alone when the code signals say otherwise -- read the code.

A `.fga.yaml`'s own `model_file:` reference and the file it names are one candidate, not
two -- resolve it before counting candidates. If, after that resolution, several distinct
candidate models are found and no plan says which, use AskUserQuestion. If a modular
manifest (`fga.mod`) is present, the manifest is the entry point -- read every file it
lists.

Read the **complete** model before converting anything. Partial conversion of a model whose
later definitions change earlier ones produces a schema that validates and is wrong.

### Step 3b: The reduced inline gate -- standalone use only

**Skip this entirely if step 1 found a plan.** Re-asking a decision the plan already
records is exactly what the plan exists to prevent, and a second gate that disagrees with
the first is worse than no gate at all. This step exists for one case and one case only:
this command was run **standalone**, for schema-only work, with no `migration-plan.md`
present. Whenever the migration came through `/spicedb-dev:migrate`, phase 0 has already
written the plan and this step does not run.

The gate runs **after** the model is read, because every question below is conditional on
what the model contains, and asking an unconditional list of questions about an unread
model produces guesses instead of decisions. Ask, with AskUserQuestion, only the ones the
model or the codebase actually triggers. Say plainly, up front, what this gate is and is
not: it covers phase 1's own inputs, resolved from the model plus targeted greps. It is
**not** the phase-0 analysis, which reads the whole codebase through the
`migration-analyzer` agent and produces the scoping numbers, the per-call-site contextual
tuple classification, the Class C advisories, and the sync-obligation count. Anything below
that this gate records as "not applicable" was decided from a narrower scan than
`/spicedb-dev:migrate` would have run.

| Decision | Ask only when | Options to offer |
|---|---|---|
| **Tenancy shape** | `Grep` finds more than one store ID (`FGA_STORE_ID`, `--store-id`, store CRUD calls) | All four from `blockers.md` "Multi-store tenancy", offered verbatim: **does not apply -- single store** · N separate SpiceDB deployments · one instance with a `tenant` resource type (idiomatic) · definition prefixes per tenant. If exactly one store, do not ask -- record "single tenant, no decision needed". A single-store model with an internal tenant-shaped type (e.g. `organization`) is **not** this trigger and needs no decision here -- see `blockers.md`'s "Not a blocker: type-based (single-store) tenancy" -- but see the Class C check below, which applies precisely to that shape. |
| **Object-ID encoding** | Any object ID in the model's `.fga.yaml` fixtures falls outside SpiceDB's `^[a-zA-Z0-9/_\|\-=+]{1,1024}$` -- an `@` in an email subject ID is the common case | `none` (IDs are already legal -- the default; do not ask if nothing is out of range) · `base64url` (reversible, and applied **only** to the types you name, since it changes every stored ID for those types) -- **a zero from the fixture scan here is not confirmation of `none`** for an application with OIDC/SAML/JWT-derived identity: it builds subject IDs at runtime from IdP claims (an email claim is the common case), and no fixture file ever contains one to match. This reduced gate has no codebase sweep to catch that shape (`migration-analyzer.md`'s runtime-constructed-identifier sweep is what does); if the codebase shows any IdP integration, say explicitly that object-ID encoding is unconfirmed here, not cleared, and point at the full gate (`/spicedb-dev:migrate`) for a real answer. |
| **Relation-split naming** | The model has at least one `define` fusing a `[...]` type list with an operator | `__direct` (the pack default, and what `schema-mapping.md`'s examples use) · a project-specific suffix. The permission always keeps the original name either way. |
| **Permission naming style** | Reading the model per `schema-mapping.md`'s split rule -- a `define` with no type list, or the permission side of a fused/split `define` -- finds at least one permission name that reads as a noun rather than a verb | **Preserve source names (default)** -- nothing renames; call sites, stored data, and anything outside this codebase that names a permission by string keep working unchanged, at the cost of a schema that does not follow SpiceDB's own noun/verb convention (`spicedb-schema-design/references/anti-patterns.md`) · **Rename nouns to verbs** -- apply the pack's fixed table (`owner`→`own`, `viewer`→`view`, `editor`→`edit`, `reader`→`read`, `writer`→`write`; `schema-mapping.md`'s "Permission naming style") to every matching name; for every other noun-shaped name (no defensible verb -- `member`, `admin`, and any compound role name), ask individually whether to supply a custom verb or leave it as a documented exception -- never invent one. Renaming changes a name an application may check by string, and this reduced gate has only greps to confirm what calls it, not the full-gate codebase sweep. |
| **Class A blocker resolutions** | One of `references/blockers.md`'s four fires. Only one is model-only (transitive wildcard); the other three are invisible in the model and need `Grep` across the whole repo -- multi-store tenancy (`storeId`/`store_id`/`FGA_STORE_ID`, and store CRUD calls), contextual tuples (`contextualTuples`/`contextual_tuples`/`ContextualTuples`), and model-ID pinning (`authorizationModelId`/`authorization_model_id`/`FGA_MODEL_ID`) | That blocker's own option list from `blockers.md`, **verbatim and complete**, including `abort` where the catalog offers it -- only the transitive-wildcard catalog does; do not add it to the other three. Do not summarize the list and do not drop the option the catalog names as leading. |

Run the `Grep` sweeps whether or not you expect a hit -- the three code-side blockers are
the ones a model-only review misses, and they are the reason a clean-looking conversion can
still be wrong.

**Class C: tenant-reachability.** Run this independently of whether the tenancy-shape
question above was asked -- it applies to the single-store, type-based-tenancy shape the
row above explicitly does *not* ask about, not to the multi-store trigger. If the model has
a single store and a type-based tenant root (a type every tenant-scoped resource reaches
via a bare relation or a one-hop arrow, per `blockers.md`'s "Not a blocker: type-based
(single-store) tenancy"), run that pack's Class C detection algorithm --
`blockers.md`'s "tenant-root reachability gap in subject-aggregation types" for OpenFGA.
**This never halts the gate and is never asked about with AskUserQuestion** -- Class C is
advisory-only (`findings-report.md`) -- but every flagged type must still be recorded in
`decisions.tenancy.tenant_reachability_findings`, immediately after the tenancy decision
itself in that same `decisions.tenancy` object, per that key's own `Record` instructions in
`findings-report.md` -- rendered, alongside the tenancy decision, under **Decisions →
Tenancy** once `migration-plan.md` is written. A literal reading of this command that skips
Class C entirely silently drops a finding the pack requires; do not skip it because no
question was asked about it.

**A blocker the user leaves unresolved is still a halt.** The gate moving inline does not
soften it: if the user declines to choose, stop before writing `schema.zed`.

Then **write `migration-map.json`** to the output directory first, before converting
anything, with `decisions.*` filled from this step's answers -- including
`decisions.relation_split_naming` for the naming decision, which **is** filled now, in
contrast to `relation_splits` and `arrow_aliases`, which stay absent from the JSON at this
point because steps 4-6 haven't walked the model yet. Record every answer under
`decisions`, and record the questions you did *not* ask as explicitly not-applicable
(`asked: false`, with the reason in `evidence`) -- a later phase reading the map cannot
otherwise tell "single tenant, decided" from "nobody looked". Set `phase_status["0"]` to
`{"status": "inline (reduced -- no codebase analysis)", "artifact": ""}`.

**Then regenerate `migration-plan.md`** from that same `migration-map.json`, in the layout
`migrating-to-spicedb/references/findings-report.md` specifies -- the ordering (JSON
written first, Markdown rendered from it) is a direct consequence of the artifact split,
not a new decision. Because `relation_splits` and `arrow_aliases` are absent from the JSON
at this point, the rendering's top-level **Identifier map**, **Relation splits**, and
**Arrow aliases** tables come out empty here; step 8 fills them in once steps 4-6 have
produced them. This does **not** include `Decisions → Relation-split naming` or
`Decisions → Permission naming style`, neither of which is a table and neither of which
renders empty -- each already holds the naming decision this gate just asked (or recorded
as not-applicable), alongside the rest of the rendered `Decisions` section, because
`decisions` itself is already filled. The **Permission naming style** decision, when it is
`rename`, *is* applied immediately below in step 4 -- unlike a relation split, which names
are eligible needs no model walk, so there is no reason to defer it to a later step the way
splits are.

### Step 4: Build the identifier map

Do this **before** generating the schema -- the schema must use the mapped names, so the
map is an input to generation, not a summary of it.

**You build this map by hand.** This command has no Bash tool, so there is nothing to
call: the rules below are the whole implementation as far as this phase is concerned.
They are stated as they are because a reference implementation of the same rules --
`migration_harness.idmap.IdMap.build()` -- was written and tested while validating this
pack, and it lives in the plugin's source repository rather than in the plugin (see
"Notes"). Follow the rules; do not go looking for a tool to run.

1. **Collect type names in source order, then deduplicate them, preserving first
   occurrence.** The registry is keyed by the raw source name, so the same raw name twice
   in one list overwrites the first occurrence's mapping and loses its clean name
   (`viewer` silently becomes `viewer_d35ca5`). Valid source models never contain a
   duplicate type name -- but a caller that merges modules, re-reads a file, or
   concatenates lists can easily produce one, and this is the first real call site.
2. **Normalize relation and permission names per type, never globally.** SpiceDB shares
   one namespace between relations and permissions *within* a definition, so names must be
   unique inside one type. Two definitions may each have a `viewer`; that is not a
   collision, and disambiguating it as one corrupts both. One registry per type.
3. **Pre-rename reserved words** before normalizing (see the pack's
   `naming-normalization.md`), and feed the renamed value through the normal path so
   collision detection still covers it.
4. **Apply the `Permission naming style` decision** (step 3b, or `migration-map.json`'s
   `decisions`) to every name that will become a SpiceDB permission -- a `define` with no
   type list, or the
   permission side of a fused/split `define`; never a `define` with only a type list, which
   stays a plain relation and is already a noun correctly. `preserve`: nothing further.
   `rename`: replace the normalized value with its recorded verb -- the fixed table for a
   defensible-verb name, the user's own supplied verb for a no-defensible-verb name they
   chose to name, unchanged for one they chose to leave (`schema-mapping.md`'s "Permission
   naming style"). Feed the renamed value through the same per-type registry as rule 2, so a
   collision it creates is still caught by rule 9 below.
5. **Reserve the generated names in the same per-type registry** as everything else --
   split relations (`<name>__direct`) and arrow-target aliases (`<name>__perm`) occupy the
   same namespace as source names and can collide with them.
6. **Report every collision and every rename.** These are Class B findings: mechanical,
   but they change stored data.
7. **Record every split relation in `relation_splits`, keyed by its source type and source
   relation, with both resulting names named explicitly:** `{"relation": "<name>__direct",
   "permission": "<name>"}`. This is the one piece of information nothing else in
   `migration-map.json` carries -- `permissions[type]` records only the *check* target (the
   permission, identity-mapped, unless rule 4 above renamed it), and a relationship **write**
   to a split relation needs the `__direct` name instead, since SpiceDB rejects a write to a
   permission outright. Add an entry only for relations that actually split; an un-split
   relation needs none; do not invent one for symmetry.
8. **Record every arrow-target alias in `arrow_aliases`, keyed by its source type and
   relation, naming the generated alias and every arrow that references it:**
   `{"alias_permission": "<name>__perm", "arrow_sites": ["<definition>.<permission>", ...]}`.
   Discovered the same way a split is (rule 7 above) -- while walking the model, here
   specifically wherever a source arrow's target resolves to a bare relation instead of a
   permission (`schema-mapping.md`'s arrow rules) -- and recorded only for a relation an
   arrow actually targets by its bare name; do not invent an entry for symmetry. Unlike a
   split, an alias renames nothing a source name maps to: no tuple is ever written or
   checked under an alias name, so it adds no counterpart entry to `permissions` the way a
   split's permission side does.
9. **Check the finished map is injective, and halt if it is not.** Within `types`, and
   within each source type's `permissions` entry combined with that same type's
   `relation_splits` **and `arrow_aliases`** entries, no two distinct source or generated
   names may share one SpiceDB name -- `permissions`, `relation_splits`, and `arrow_aliases`
   write into the same per-definition namespace, so a split's generated `relation` name, or
   an alias's generated `alias_permission` name, can collide with an unrelated relation or
   permission on the same type as easily as two `permissions` entries can collide with each
   other. Verify this explicitly against the map you just wrote -- it is the only check that
   catches a collision the rules above missed, and it is cheap. This is also the one check
   nothing else performs on `arrow_aliases`'s behalf: `findings-report.md`'s injectivity note
   is explicit that the validation harness carries no assertion for that key, so this rule is
   the sole place that collision is ever caught. A non-injective map does not fail loudly
   later: every check, tuple, and test assertion naming either source name rewrites to the
   same target, so the two merge and the migration looks clean while one of the source's
   permissions has quietly ceased to exist. Halt and report both source names; do not pick
   one.

**If `migration-map.json` already exists**, phase 0 wrote it: it carries every source type
and every source relation/permission, already normalized and already collision-checked, with
the gate's own **Permission naming style** decision already applied to any renamed
permission, plus the `id_encoding` the gate decided. Load it and treat every entry already in
`types` and `permissions` as **fixed** -- a renamed permission is not a special case here,
it is exactly what "fixed" already means. Also load `phase_status` and `decisions` from the
same file and treat them as fixed too: nothing in this step overwrites phase 0's `decisions`
or its existing `phase_status` entries -- this step only adds `relation_splits` and
`arrow_aliases`, which are new; `phase_status["1"]`/`phase_status["2"]` are step 8's own
later addition, once conversion and validation have actually happened. Phase 0 never writes
`relation_splits` -- whether a
`define`
splits is discovered only while walking the model to emit `.zed` (this step comes before
step 5, but the *result* of step 5's walk is what rule 7 above records), so that key is this
phase's own new content, not something phase 0 could have known. All that is left to do with
the *fixed* entries is what phase 0 could not know either: reserve the generated names
(`<name>__direct` split relations, `<name>__perm` arrow aliases) in the same per-type
registries, so no source name can collide with one. Usually that changes no existing entry
at all -- a split relation's `permissions[type]` entry still maps to its *permission* name
and an alias has no source name to map -- so `types` and `permissions` are usually written
back unchanged; write either back changed only where a reservation forced a source name to
be disambiguated, and report that as a Class B finding. Never re-derive an entry that is
already there. A name the gate recorded is a decision the user owns, and re-deriving it can
silently move a mapping that stored data has already been migrated against. Rule 6 above
then has almost nothing to report for `types`/`permissions`: those collisions and renames
were phase 0's findings, already in the plan, and phase 1 reports only what its own
reservations newly forced -- which is usually nothing. Do not re-list phase 0's findings as
though this phase discovered them. `relation_splits` and `arrow_aliases`, in contrast, are
entirely this phase's own finding every time -- report both in full, every run, split or
standalone: an arrow-target alias is discovered only while walking the model in step 5, the
same as a split, and phase 0 has no more visibility into it than it does into a split.

Write `migration-map.json`:

```json
{
  "types": { "<source type>": "<spicedb definition>" },
  "permissions": {
    "<source type>": { "<source relation or permission>": "<spicedb name>" }
  },
  "relation_splits": {
    "<source type>": {
      "<source relation>": { "relation": "<name>__direct", "permission": "<spicedb name>" }
    }
  },
  "arrow_aliases": {
    "<source type>": {
      "<relation>": {
        "alias_permission": "<relation>__perm",
        "arrow_sites": ["<definition>.<permission>"]
      }
    }
  },
  "id_encoding": { "mode": "none", "types": [], "status": "unknown", "violations": [] }
}
```

`id_encoding.mode` is `"none"` or `"base64url"`, and `id_encoding.status` is one of
`clean`/`encoded`/`unresolved`/`unknown`, both taken from `migration-map.json`'s own
`id_encoding` key as set by whichever gate ran (phase 0 or step 3b) -- not chosen here.
Carry `status` and `violations` through unchanged; this phase never upgrades a `status`,
because nothing it does resolves an identifier violation.
`id_encoding.types` lists the source types whose object IDs
are encoded. Include an entry in `permissions` for **every** source relation and
permission, including ones whose name did not change: `/spicedb-dev:migrate-data` (phase 3)
and `/spicedb-dev:migrate-tests` (phase 5) both look up names in this map, and a missing
entry silently passes the source name through. Neither command has a fallback for an absent
entry, which makes completeness more important, not less.
`relation_splits` and `arrow_aliases` are both exceptions to "every relation gets an entry":
add a `relation_splits` entry only for a relation that actually split, and an `arrow_aliases`
entry only for a relation whose arrow target resolved to a relation rather than a
permission; omit either key or type entirely when this model produces none of that kind at
all (an empty map, not `{"<type>": {}}` padding). Reserve every `alias_permission` name in
the same per-type registry as `permissions` and `relation_splits` (step 4 rules 5 and 8,
above), and verify it against both via step 4 rule 9's injectivity check -- the harness that
validates this format has no assertion for `arrow_aliases`, so this phase is solely
responsible for verifying it does not collide with either (`findings-report.md`'s
`### arrow_aliases`).

**A split relation maps to its permission name in `permissions`, and additionally gets a
`relation_splits` entry naming both resulting names.** For `organization.member` split into
`relation member__direct` + `permission member`, `permissions["organization"]["member"]` is
still `"member"` -- unchanged from before this key existed, since every check surface
(assertions, other permissions, arrow references, subject-relation references) keeps naming
the permission -- and `relation_splits["organization"]["member"]` is now
`{"relation": "member__direct", "permission": "member"}`. Both fields are named explicitly
so a reader cannot mix up which is the write target and which is the check target: naming
only `"member__direct"` cannot say which side of a relationship it governs, and guessing
wrong is silent on the check side (SpiceDB allows checking a bare relation directly with no
error) and loud on the write side (SpiceDB rejects a write to a permission outright) --
`schema-mapping.md`'s "A split name means two different things depending on position" has
the full, corpus-verified failure matrix. An arrow-target alias works the same way in
miniature: `arrow_aliases[type][relation]` names the generated `alias_permission` and lists
every `arrow_sites` reference, but adds no entry to `permissions` at all, since -- unlike a
split -- an alias renames nothing a source name maps to. Keep both in sync with the plan's
**Relation splits** and **Arrow aliases** tables (step 8) -- the tables are this same
information in human-readable form, not a separate decision.

### Step 5: Convert the model

Walk the source model and emit `.zed` per the pack's `schema-mapping.md`. Apply, in the
pack's terms:

- the construct table, using mapped names throughout;
- the relation/permission split wherever a source construct fuses direct assignment with
  computation, keeping the original name on the permission;
- full parenthesization -- one parenthesized group per source node, never relying on
  SpiceDB operator precedence;
- correct arrow operand order, and permission aliases for arrow targets that resolved to
  relations;
- the required `use` flags, emitted before every definition and caveat;
- the codegen rules: one declaration per line, wrap only with a trailing operator, no
  `_`-prefixed identifiers.

**Halt on any construct the pack has no rule for.** Report it as an unhandled construct
with the source line. A guessed translation that compiles cleanly is worse than a halt --
nothing downstream will catch it.

Keep a running list of every split and alias as you go; it is both a report output and what
step 8 writes into `migration-map.json`'s `relation_splits` and `arrow_aliases` keys, from
which `migration-plan.md`'s own rendering of them is regenerated.

### Step 6: Write the outputs

Write `schema.zed` and `migration-map.json` to the output directory.

`schema.zed` contains only `use` flags, caveats, definitions, relations, and permissions.
No relationships, no assertions -- those belong in a validation YAML file. A short header
comment naming the source model and the date is welcome; commentary explaining decisions
belongs in `migration-plan.md`.

### Step 7: Validate

Use the Task tool to launch the `schema-validator` agent:

```
Task(
    subagent_type="spicedb-dev:schema-validator",
    description="Validate converted schema",
    prompt="Validate the schema file at [path] and suggest improvements"
)
```

Report its findings verbatim in step 9. Two things to watch for, because they are
conversion bugs rather than style notes:

- an `arrow-references-relation` lint means an arrow-target alias was missed;
- any error mentioning a caveat name that does not exist, or an unused caveat parameter,
  means the caveat translation is wrong -- not that the validator is being strict.

**Where an actionable validator finding goes.** This command may not write
`## Deferred / manual` -- but the validator can surface a genuine defect that later phases must
act on (for example: a relationship the source deletes that the converted schema cannot express
on either the permission or the `__direct` side). Recording it only in
`phase_status["2"].artifact` buries it: no later phase reads that field looking for work, so in
a fresh session phase 4 never sees it. **Put such findings in `migration-map.json`'s
`decisions.additional` with `"recorded_by": "/spicedb-dev:migrate-schema step 7"` and a
`file:line`, and name them in this phase's report**, so the next phase inherits them through
state it does read. Say explicitly in the report that the item needs a `Deferred / manual` row
that this phase cannot write, so whichever phase next writes that section adds it.

**The validator is a general-purpose SpiceDB reviewer, so some of what it suggests does not
apply to a converted schema. Report those findings, but do not act on them.** It reviews
`schema.zed` on its merits, with no knowledge that this file was mechanically translated
from another system and that the pack's rules -- not taste -- decide what it may contain.
The recurring case is **`use typechecking`**, which it tends to raise as a high-value
addition: `schema-mapping.md`'s "`use expiration` is one of the few `use` flags both tools accept" section states that flag fires only when a permission
carries a type annotation, and a clean conversion from an OpenFGA source emits none, so
adding it changes nothing this pipeline produced. The same goes for any suggestion to
restructure permissions, merge definitions, or rename for readability -- this command's own
Error Handling is explicit that the schema is not hand-edited past the conversion rules,
because the emitted names are what `migration-map.json` promises every later phase.

So, when reporting in step 9, mark each validator finding as **actionable** (a conversion bug
-- the two above) or **not applicable to a converted schema** (a general-purpose
recommendation the pack's rules exclude), and say which rule excludes it. Passing the
validator's list through unsorted hands the user advice this pipeline forbids following, with
nothing on the page saying so.

A clean validation proves the schema **compiles**. It does not prove the conversion is
faithful; the source system's own `.fga.yaml` assertions are the oracle for that, and
converting them is phase 5 -- `/spicedb-dev:migrate-tests`.

### Step 8: Update the migration map, then the plan

**Update `migration-map.json` first, before touching `migration-plan.md` at all** -- the
plan is a rendering of the map, never the other way around
(`findings-report.md`'s "Two groups of sections, one rule each"). Write:

- **`relation_splits`** -- one entry per split relation, keyed by source type and source
  relation, as built in step 4. `/spicedb-dev:migrate-data` (phase 3) and
  `/spicedb-dev:migrate-code` (phase 4) both read this key directly to rewrite tuples and
  call sites -- neither one reads the plan's rendered table.
- **`arrow_aliases`** -- one entry per `__perm` alias emitted in step 5, keyed by source
  type and relation, with its `arrow_sites`. Unlike a relation split, an alias never
  renames anything a client calls or a tuple stores -- it exists purely so an arrow can
  target a permission instead of a relation -- but step 5 requires tracking "every split
  and alias" and this is where the alias half of that goes.
- **`phase_status["1"]`** -- `{"status": "complete", "artifact": ...}`.
- **`phase_status["2"]`** -- `{"status": "complete", "artifact": ...}` if the
  `schema-validator` agent passed, `{"status": "failed", "artifact": ...}` if it did not,
  with the validator's own summary (error/warning counts, or what failed) always in
  `artifact`, never folded into `status`. `status` is the closed vocabulary
  `migrating-to-spicedb/references/findings-report.md`'s **`## Phase status`** section
  defines (cited here, not restated) -- a value like `clean -- 0 errors` matches no branch
  any later phase reads; write `complete` and put `0 errors` in `artifact`.

**Then regenerate `migration-plan.md` from that updated `migration-map.json`.** Re-read
`migration-plan.md` from `[output-dir]` -- the same place step 1 read it from and step 3b
wrote it to -- and write it back to the same location. Per `findings-report.md`'s "Two
groups of sections, one rule each" rule, `## At a glance`, `## Needs your attention`,
`## Decisions`, `## Identifier map`, `## Relation splits`, `## Arrow aliases`, and
`## Phase status` are all regenerated in full from the JSON just written, regardless of
whether this run's own changes touched every one of them. **Relation splits** and
**Arrow aliases** are the two sections this run actually adds rows to (one row per split --
definition, source relation, SpiceDB relation, SpiceDB permission -- and one row per
`__perm` alias -- definition, relation, alias permission, arrow site(s)), and
**Phase status** shows phases 1 and 2 as just written above.

**This command owns none of `## Source`, `## Scan scope`, `## Target`, `## Sync
obligations`, or `## Deferred / manual`.** Leave all five byte-identical -- nothing in this
step reads or writes them.

### Step 9: Report

Tell the user:

1. Where `schema.zed` and `migration-map.json` were written -- and `migration-plan.md`,
   regenerated as a rendering of it (created new, if step 3b created it; rewritten in full
   either way by step 8). Say that `migration-map.json` is the durable record of the
   decisions, that `migration-plan.md` is a rendering of it and nothing more, and that
   re-running this command will read the JSON rather than re-ask.
2. What was generated: N definitions, N relations, N permissions, N caveats.
3. **Relation splits** -- the count and the table, from `migration-map.json`'s
   `relation_splits` key just written in step 8. Say plainly that each one is a data
   rewrite `/spicedb-dev:migrate-data` (phase 3) performs automatically, using this same
   pairing read directly from the JSON.
4. **Arrow aliases** -- the count and the table, from `migration-map.json`'s
   `arrow_aliases` key just written in step 8, if any were emitted in step 5.
5. **Class C findings, if any** -- e.g. the tenant-reachability gap in subject-aggregation
   types (`blockers.md`), per flagged type. State plainly that these are advisory, not
   blockers, and that they are already recorded in `migration-map.json`'s
   `decisions.tenancy.tenant_reachability_findings`, rendered under the plan's
   `Decisions → Tenancy`.
6. **Name changes** -- every source name that is not its own SpiceDB name, and every
   collision that was disambiguated. Call out a **Permission naming style** rename
   (`owner` → `own`, etc.) distinctly from a mechanical one (reserved word, collision
   suffix) -- it is a deliberate style choice the gate made, not a necessity, and the user
   should be able to tell the two apart in the report the same way `migration-map.json`'s
   `decisions` keeps them in separate entries, rendered in the plan's `Decisions` section.
7. **Unhandled constructs**, if any, with source lines. These are the highest-priority
   item in the report.
8. Validation results from the `schema-validator` agent.
9. Next steps:
   - Review the splits and name changes -- they change stored data.
   - Deploy the schema when ready:
     ```bash
     zed schema write schema.zed --endpoint=localhost:50051 --token=<your-token>
     ```
   - `/spicedb-dev:validate-schema` re-runs validation on demand.
   - **Next: phase 3 -- `/spicedb-dev:migrate-data`, phase 4 -- `/spicedb-dev:migrate-code`,
     and phase 5 -- `/spicedb-dev:migrate-tests`.** All three are implemented commands and
     pure consumers of `migration-map.json` -- point any of them at the same `[output-dir]`
     this command wrote to. Phases 3 and 5 have no ordering
     dependency on each other. Phase 4 additionally imports phase 3's emitted ID codec
     whenever `id_encoding.mode` is `base64url` for any type -- run phase 3 first in that
     case; when `id_encoding.mode` is `none` and `id_encoding.status` is `clean` everywhere, phase 4 does not need phase 3 to
     have run first. **If `status` is `unresolved` or `unknown`, phase 4 must not point converted code at SpiceDB at all**, whatever `mode` says -- see `findings-report.md`'s `id_encoding`.
   - **Data before code.** Phase 3 must complete and pass verification before any client
     code -- converted or not -- is pointed at this store's data. A check against a SpiceDB
     instance still missing relationships silently denies everything.

## Error Handling

| Situation | Do this |
|---|---|
| No `migration-plan.md` | **Do not halt.** Offer `/spicedb-dev:migrate` (phase 0, the full gate) once; if the user wants schema-only, run step 3b's reduced gate, which writes `migration-map.json` and renders `migration-plan.md` from it, then continue. |
| Unresolved Class A finding (a null/absent `resolution` in `migration-map.json`'s `decisions.per_blocker_resolutions`, or declined at the inline gate) | Halt. List the unresolved blockers and their options from the pack's blocker catalog. |
| No pack for the detected source | Halt. An unsupported source needs a new pack. |
| Model file missing or unreadable | Ask for the location. Do not fall back to converting a partial model. |
| A construct with no mapping rule | Halt on that construct. Report it with its source line; do not improvise. |
| A name collision the map cannot resolve | Halt. Report both source names -- silently merging two relations is a correctness bug. Step 4 rule 9 is the check that finds it. |
| Validator reports errors | Report them and stop before deployment guidance. Do not hand-edit the schema past the conversion rules to make an error disappear. |

## Notes

- The version floor is SpiceDB **v1.52.0**; the conversion rules were verified against
  v1.56.0 and zed v0.31.1.
- This command is independently runnable. `/spicedb-dev:migrate` (phase 0) is the
  migration's front door and holds the full gate; this command reads the `migration-map.json`
  that gate wrote, and falls back to a reduced inline gate only when run standalone with no
  plan present. Either way `migration-map.json` is written once and read on every later run
  rather than re-asked, which is what makes the migration resumable.
- **Ask once.** AskUserQuestion covers ambiguity in this phase's inputs (which model file,
  which output directory) and, when no plan exists, step 3b's gate. If a plan exists, do
  not re-ask anything `migration-map.json` records -- change `migration-map.json` instead;
  `migration-plan.md` is only ever a rendering of it, so editing the Markdown changes nothing
  a later run reads.
- `tools/migration-harness/` is **not shipped with this plugin**. It is the parity harness
  used to validate this pack against real OpenFGA stores, and it lives in the plugin's
  source repository (`authzed/authzed-marketplace`). Nothing in this command requires it,
  and this command cannot run it -- there is no Bash tool in `allowed-tools`.
