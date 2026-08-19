# The Pack Contract

A conversion pack skill (`openfga-to-spicedb`, and eventually `oso-to-spicedb`) supplies
exactly these ten things. The generic pipeline commands and agents consume only this
interface -- nothing else about a source system is visible to the framework. This is what
lets a second source (OSO) plug in as one new skill and one registry entry, with no change
to the pipeline itself.

**What consumes this contract today:** `/spicedb-dev:migrate` with the
`migration-analyzer` agent (items 1, 2, 4, 5, 9), `/spicedb-dev:migrate-schema` (items 1,
2, 3, 4, 5), `/spicedb-dev:migrate-data` (item 6), `/spicedb-dev:migrate-code` (item 7),
`/spicedb-dev:migrate-tests` (item 8), and the `schema-validator` agent. All five commands
are shipped (see the phase-pipeline table in `SKILL.md`); every item in this contract has a
consumer today.

Items 9 and 10 were added after validating the contract against a real second-source
analysis (Oso Cloud); they are not afterthoughts, they are load-bearing.

**An eleventh deliverable exists outside this numbering, for cutover rather than conversion.**
`/spicedb-dev:migrate-verify` additionally consumes a pack's `references/source-adapter.md` --
the seam `references/differential-harness.md` defines (given a question in that file's
vocabulary, ask the source system and translate its answer back into the same vocabulary).
It is not one of the ten items above because it has nothing to do with converting the schema,
data, code, or tests; it exists so a *converted* system can be dual-run and shadow-read against
the source system it replaced, per `references/cutover-strategies.md` step 4.
`openfga-to-spicedb/references/source-adapter.md` is the shipped example. A future pack
supplies its own the same way it supplies the ten items above, once its migration is converted
and cutover begins.

Each item below is a heading, a one-paragraph definition of what the pack must supply, and
-- as a worked example of the *shape* the answer takes -- the `openfga-to-spicedb` pack's
answer. The worked examples are illustrations of the contract, not the pack itself; the
actual pack skill and its reference files are a separate deliverable.

## 1. Detection

How to recognize the source system: dependency names, import paths, config file patterns,
model file extensions. This is what `migration-analyzer` runs first, and what decides
whether a pack applies at all. `/spicedb-dev:migrate-schema` reads the same detection rules
itself when it is run standalone.

**OpenFGA's answer:** dependency/import signals across the three live client shapes --
`OpenFgaClient` construction (flattened camelCase SDKs), `OpenFgaApi` construction (raw
wire-shape SDKs, store ID either in config or as an explicit first argument depending on
SDK version), and the deprecated `Auth0FgaApi` (`@auth0/fga`, keyed on
`environment: "us"|"staging"|"playground"`). Model file signals: `.fga` DSL files,
`.fga.yaml` test/tuple files, and `fga.mod` for modular models split across multiple `.fga`
files via `module` / `extend type`.

## 2. Model extraction

Where the authorization model lives, in what formats, and how to read each. Phase 0 needs
this to load the complete model before it can classify a single finding.

**OpenFGA's answer:** the model exists in several forms -- enumerated in full, with the
detection command for each, in the `migration-analyzer` agent's own definition (`spicedb-dev/agents/migration-analyzer.md`),
"Step 2: Locate and read the complete model" table, which is the single authority for that list and is added to as new shapes turn
up. Do not restate a count of them here or anywhere else; a pack must check for **every form
that table lists** -- across `openfga/sample-stores`'s 39 stores, a scan that globs only `*.fga` finds
nothing at all in 12 of them and the wrong entry point in a 13th:

1. **Standalone DSL text**, conventionally `model.fga`, referenced from a sibling
   store file's `model_file:` key (e.g. `accounting/store.fga.yaml`'s `model_file:
   model.fga`). The `.fga` extension is a convention, not a guarantee -- a real production
   project (a real production Go project) ships its model at
   a file named `*_model.openfga` -- extension `.openfga` -- confirmed by its `model` /
   `schema 1.1` header. A glob restricted to `*.fga` alone misses this; check the file's
   content (a `model` / `schema 1.1` header) when a DSL-shaped file with another extension
   is a candidate.
2. **Inline**, as a top-level `model: |` block embedded directly inside a `.fga.yaml` store
   file, with no `model_file:` key and no separate `.fga` file anywhere in the directory.
   **12** of `openfga/sample-stores`'s 39 stores carry their model this way --
   `multitenant-rbac` and `abac-with-rebac` among them -- against **26** using the
   standalone form and **1** (`modular`) using an `fga.mod` manifest; the three sets are
   disjoint and sum to 39. It is not the most common form, it is the most easily *missed*
   one, because it produces no file named anything like "model". Derived from a checkout of
   the corpus, run from `stores/`:
   ```bash
   for s in $(ls -d */ | tr -d '/'); do
     if   [ -f "$s/fga.mod" ]; then k=MODULAR
     elif [ -n "$(find $s -maxdepth 1 -name '*.fga' -print -quit)" ]; then k=STANDALONE
     elif grep -lq '^model: *|' $s/*.fga.yaml 2>/dev/null; then k=INLINE
     else k=OTHER; fi; echo "$k"
   done | sort | uniq -c   # -> 12 INLINE, 1 MODULAR, 26 STANDALONE
   ```
3. **As JSON**, the wire format used by `writeAuthorizationModel`.
4. **Live on a running store**, fetchable by API -- the only one of the four with no
   on-disk file to locate at all.

Modular models are a fifth on-disk shape in practice: multiple `.fga` files joined by an
`fga.mod` manifest (its `contents:` list) and `module` / `extend type` declarations, where
`fga.mod` itself -- not any single `.fga` file -- is the entry point
(`modular/fga.mod` lists `core.fga`, `wiki.fga`, `issue-tracker/projects.fga`,
`issue-tracker/tickets.fga`). The pack reads all of these through the real OpenFGA grammar
(its ANTLR parser and `typesystem.NewAndValidate`), not a hand-rolled parser -- published
grammars are permissive in ways the real parser is not (see item 10).

**A sixth shape has no standalone file at all: the model embedded directly in application
source.** Confirmed in a real production Go project: item 3's JSON form is never written to disk on its own --
instead it is a string constant inside a generated `.go` file (`var authModel =
`{"schema_version":"1.1",...}`` `, headed `// Code generated by Makefile; DO NOT EDIT.`),
produced from the standalone-DSL source (item 1's `.openfga` file) by a build rule. No
`**/*.json` glob finds this, because no `.json` file exists. A pack must grep source files
themselves for `schema_version`/`type_definitions` together, and separately locate every
`writeAuthorizationModel`/`WriteAuthorizationModel` call site to trace what value it passes
-- a literal, a constant, or an imported string. **When the model is only reachable by
reading code**: if the generating build step names a true source file (a `go:generate`
line, a build-tool rule, a header comment naming its input), read that file instead of the
generated literal -- it is the file a human actually edits, and it is far more legible than
an escaped JSON blob; otherwise, read the embedded literal itself and treat its contents as
the model text, exactly as item 3 treats a standalone JSON file. Do not report "no model
found" from empty file globs when source-code signals (a matched SDK import, a
`writeAuthorizationModel` call site) say a model exists -- that combination means the model
is code-embedded, not absent.

## 3. Schema mapping

Construct-by-construct translation rules producing `.zed`, each carrying one of the four
fidelity ratings (`clean` / `effort` / `heavy` / `blocked` -- see `SKILL.md`). Where more
than one valid SpiceDB encoding exists for a construct, the mapping presents the **choice
with its tradeoffs**, never a single silent answer.

**OpenFGA's answer:** a construct-by-construct table -- `type` becomes `definition`,
`define viewer: [user]` becomes `relation viewer: user`, `define view: viewer` becomes
`permission view = viewer`, `or`/`and`/`but not` become `+`/`&`/`-`, `member from parent`
becomes `parent->member` with the operand order reversed, `condition` becomes `caveat`.
Where a relation fuses "directly assignable" and "computed" in one `define`, the pack
splits it into a SpiceDB `relation` (default suffix `__direct`) plus a `permission` --
this is local to that one relation, not viral, because OpenFGA forbids any rewrite on a
relation referenced as a tupleset. For encoding choices, the pack surfaces the measured
tradeoffs rather than picking for the user -- e.g. attribute-as-caveat-context vs.
attribute-as-wildcard-marker vs. attribute-encoded-in-the-relation-name have different
`LookupResources` latency and write-atomicity costs, and the fastest of the three only
works for low-cardinality enumerable values.

## 4. Blocker catalog

Each `heavy` and `blocked` construct: its detection rule, and the concrete options to
offer the user. This is what turns a Class A finding (see `findings-report.md`) into
something actionable instead of a dead end.

**OpenFGA's answer** -- the hard blockers `blockers.md` catalogs (count them there, not here), each with a detection rule and the options
offered at the gate. This table is a **summary**; `openfga-to-spicedb`'s
`references/blockers.md` is the authority, and it carries the evidence, the corpus status,
and the cost of each option. Read it before offering any of these to a user:

| Blocker | Detection | Options offered |
|---|---|---|
| Transitive wildcard *(rating provisional)* | A relation allows `T#rel` where `rel` itself allows `U:*` **and `rel` is a relation, not a permission**. One hop only. Rejected at `zed validate` **and** `WriteSchema`, so it stops the conversion rather than reaching deploy. | **Alias the intermediate relation as a permission** and point the userset at it (leading candidate) · flatten the wildcard onto the outer relation · drop it and enumerate subjects · hand-redesign · abort |
| Contextual tuples | `contextualTuples` / `contextual_tuples` at any call site -- invisible in the model itself. | Materialize as real relationships around the check · re-model as caveat context · restructure so the edge is persistent · leave the call site failing closed with a `TODO(spicedbmigration):` marker (`findings-report.md`'s "Inline markers") |
| Multi-store tenancy | More than one store ID in config/code, or store CRUD calls **that are not test scaffolding**. | **does not apply -- single store** (correct when detection fired only on scaffolding) · N separate SpiceDB deployments (true isolation) · one instance with a `tenant` resource type (idiomatic) · definition prefixes per tenant (only when models genuinely differ per tenant) |
| Model-ID pinning | `authorizationModelId` / `authorization_model_id` passed in config or per request. | Drop pinning and accept the change · emulate with a schema-version gate · flag for a manual rollout plan |

## 5. Naming normalization

The source's identifier rules, and how they reduce to SpiceDB's stricter ones. SpiceDB
type/relation/permission names must match `^[a-z][a-z0-9_]{1,62}[a-z0-9]$` (minimum 3
characters, lowercase, no hyphens, no leading/trailing underscore); object IDs must match
`^[a-zA-Z0-9/_|\-=+]{1,1024}$`. **Caveat names are not bound by that regex** -- they only
need to lex as identifiers -- but a pack should still normalize them by the same rule as a
conservative default, since SpiceDB's relationship-string grammar (unlike `WriteSchema`)
does enforce it; see `naming-normalization.md`'s "Caveat names are not bound by the name
regex" for the verified distinction and why normalizing anyway is still the right call.
Every pack states which of its source's legal identifiers violate these rules and the
deterministic algorithm that reduces them.

**OpenFGA's answer:** identifiers legal in OpenFGA and illegal in SpiceDB, each verified
individually against zed v0.31.1 and SpiceDB v1.56.0 -- uppercase (`type User`), hyphens
(`type My-Doc`), dots and slashes (`type a/b`), 1-2 character names, a leading underscore,
and SpiceDB's own reserved words used as identifiers: `relation`, `permission`,
`definition`, `caveat`, `nil`, `with`. Note which words are **not** on that list:
`model`, `schema`, `type`, `module`, and `extend` are OpenFGA keywords, not SpiceDB ones,
and all five compile and write fine as both definition names and relation names. Renaming
a legal identifier is not free -- it is a Class B finding that rewrites stored data -- so
a pack must verify each word rather than inherit a list. See `openfga-to-spicedb`'s
`references/naming-normalization.md` for the full verified table, including the two
context-dependent cases (`self`, `expiration`). Names reduce via:
lowercase → `-`/`.`/`/` become `_` → strip illegal characters and leading/trailing
underscores → pad names under 3 characters → truncate names over 64 with a short hash
suffix so distinct over-long inputs stay distinct. Object IDs reduce via base64url, which
lands entirely inside SpiceDB's ID character set unchanged and is reversible.

## 6. Data mapping

How to extract existing relationships/facts and transform them into SpiceDB
relationships -- **plus which conversions create ongoing sync obligations**: a source
construct that reads state SpiceDB cannot hold (an attribute, a computed property, any
pure function of current state) becomes a replicated edge the customer owns permanently,
with a write path, a backfill, a reconciliation job, and a fail-closed window between the
source-of-truth write and the SpiceDB write. The obligation *count* is what separates a
migration from an ongoing synchronization project, and it must be surfaced at the gate,
not discovered later.

**OpenFGA's answer:** a generated (not hand-written) extract/transform/load script --
paginated `Read` with continuation tokens or a bulk store export for extraction; relation
splits, name mangling, ID encoding, and condition→caveat context rewriting for transform
(this step needs the model, not just the tuple stream, since OpenFGA tuples are only
implicitly typed); `ImportBulkRelationships` falling back to batched `WriteRelationships`
with `TOUCH` for load; a checkpointed source continuation token for resumability; a dry
run that reports counts without writing; and a post-load sample re-read for verification.
For OpenFGA, the sync-obligation set is usually small, since its facts are already
relationship-shaped, and is driven mainly by contextual tuples.

## 7. Code mapping

Source client API surface → SpiceDB client API, per language, per client generation
(a source system may have had more than one client shape over its history).

**OpenFGA's answer:** a call-by-call table -- `check` → `CheckOne`/`check_permission`,
`batchCheck` → `Check` (ordered `[]bool`), `listObjects` → `LookupResources`, `listUsers`
→ `LookupSubjects`, `expand` → `ExpandPermissionTree`, `write`/`writeTuples` → a
`Txn` of `touch`/`delete` calls, `writeAuthorizationModel` → `WriteSchema`. Several of
these are more than a rename and the pack calls that out explicitly rather than
mechanically porting them: `batchCheck` correlation-ID-keyed results become positionally
ordered; `listRelations`'s client-side error-swallowing (`allowed: false` on failure) must
be reproduced deliberately or dropped, never silently kept, per the plugin's fail-safe
rule; `expand`'s tree shape has no `tupleToUserset` node in SpiceDB; `readChanges`'s paged
poll becomes a revision-keyed server stream; non-transactional/per-tuple-result writes
have no SpiceDB target, since `WriteRelationships` is always transactional.

## 8. Test mapping

Source test format → SpiceDB validation YAML, including the structural mismatches, not
just the field renames.

**OpenFGA's answer:** `.fga.yaml` → SpiceDB validation YAML, mapping `model`/`model_file`
to `schema`/`schemaFile`, structured `tuples` to string-form `relationships`, and boolean
check assertions to `assertTrue`/`assertFalse`. Two structural mismatches the mapping must
handle, not just the field table: one `.fga.yaml` check block fans out to |users| ×
|objects| × |assertions| individual SpiceDB assertion lines, and SpiceDB validation files
have one global relationship set, so each `tests[]` entry carrying its own `tuples` becomes
a separate validation file -- or, where two `tests[]` entries' data genuinely conflicts,
needs per-scenario file splitting rather than a merge. `openfga-to-spicedb`'s
`references/test-mapping.md` is the authority for both mismatches and carries the evidence;
read it before building an equivalent mapping for a new source.

**Neither list-assertion form converts, and they get the identical treatment.** A
`list_users` block's expected subject set does **not** map to a validation YAML
`validation:` block, despite that block existing in the target format: `validation:`
requires a per-subject resolution *path* (`"[user:alice] is <document:doc1#viewer>"`), which
`zed` computes/checks against the schema, not a flat membership claim -- and no `.fga.yaml`
construct ever records that path, nor does any `zed` CLI flag compute one offline (verified
against zed v0.31.1 / SpiceDB v1.56.0; see `test-mapping.md`, "Two corrections to a naive
reading of this table"). `list_objects` already had no validation-YAML form; `list_users`
gets that same treatment, not a special one. Both become a Class C advisory finding instead
of a converted test, verified live once the schema is deployed (`list_objects` via
`LookupResources`, `list_users` via `LookupSubjects`/`Expand`). Count both together when
reporting how much of the source oracle survives conversion: on `openfga/sample-stores`'s
`github`, three of the four uncarried assertions are `list_users` and only one is
`list_objects`, so a pack that counts `list_objects` alone reports a blind spot four times
smaller than the real one.

`assertCaveated` -- the target format's third assertion bucket, for a relationship whose
caveat isn't fully resolved by the supplied context -- has **no OpenFGA source construct at
all**: OpenFGA's check API is strictly boolean, so a conversion never emits one. It stays
part of the target vocabulary only for a migrating agent's own hand-written supplementary
checks (for example, verifying one of the two advisory findings above), never as something
this mapping produces from source data.

## 9. Scoping questionnaire

The small set of artifacts and questions that predict most of the migration cost *before*
any conversion runs. This is a day of work, and it is the pack's cheapest high-value
output -- it produces the actual estimate, not just a go/no-go.

**OpenFGA's answer:** the model, plus a grep for `contextualTuples`/`contextual_tuples`
across the codebase, plus a store count. (For contrast, Oso's equivalent -- the second
planned source -- is `GET /policy` plus one question: "do you call `listLocal` anywhere?"
Different sources predict cost from different signals; each pack picks its own.)

## 10. Validation corpus

The real applications the pack is tested against, named explicitly. Published grammars
and documentation are **not** a semantic spec for this purpose: a permissive published
grammar parses constructs the source's real parser rejects, and real sample applications
routinely use constructs their own docs never demonstrate. A pack built only from
documentation will silently mistranslate real policies it was never tested against.

A pack must state the corpus it **has run**, separately from the corpus it intends to run.
The two are different facts and only the first one is evidence.

**OpenFGA's answer, as run:** **all 39** stores in
[`openfga/sample-stores`](https://github.com/openfga/sample-stores). Each was converted
whole, deployed to SpiceDB v1.56.0, and checked assertion-by-assertion against its own
`.fga.yaml` oracle; **38 of 39 reach `PARITY OK`** against that canonical file (mechanical:
`grep -c '^\*\*Final harness run' corpus-runs/README.md` → 39, piped to `grep -c 'PARITY
OK'` → 38). The exception is `abac-with-rebac`: its canonical run, against its own
`store.fga.yaml`, exits **1** with **`PARITY FAILED`** (two `AMBIGUOUS` findings) --
verified live. This is a documented harness limitation, not a conversion defect (two
mutually exclusive document states get flattened into one comparison); the same schema and
data reach `PARITY OK` against the two derived per-scenario store files committed alongside
it. See `corpus-runs/README.md`'s `abac-with-rebac` section. **Zero Tier-2 application
repositories have been run**; `theopenlane/core` (Go), `openfga/flask-demo` (Python), and
`embesozzi/keycloak-openfga-workshop` (JS) are the intended Tier 2 and remain untouched.
Item 7 (code mapping) and `/spicedb-dev:migrate-code`, its consumer, are both written now,
but neither has been exercised against a real, pre-existing codebase -- Tier 2 is the
corpus that gap is waiting on, not blocked on either being unwritten any longer.
[`openfga/agent-skills`](https://github.com/openfga/agent-skills) is read
as prior art, since models authored with it follow its idioms.

**The honest position is that the corpus is exhausted and the pack survived it -- not that
the pack has converged.** 21 of the 39 stores required no pack change; 18 filed at least
one finding. The longest unbroken run of zero-finding stores is **8**, and no longer run
can be claimed: stores sharing an iteration carry no inferable order, so a streak cannot be
extended across an iteration boundary that contains a finding. Coverage is also uneven in a
way the headline number hides -- **every one of the 8 caveat-bearing stores filed a
finding** (caveats have never once converted clean), the multi-type-tupleset construct has
exactly one bearer in the whole corpus and it filed findings too, and several written rules
carry **zero** corpus validation: the `a - b` exclusion operator, and the multi-store tenancy
and model-ID pinning blockers -- both properties of how an application is *deployed* rather
than of any model a store can carry, so no store could confirm them. The other two Class A
blockers (transitive wildcard, contextual tuples) have since been corpus-confirmed; the pack's
`blockers.md` carries the confirmation passage for each and is the authority on how far each
one reaches.
"Zero-finding" and "exercised" are different claims, and only the first is evidence a
construct is easy.

Both counts above are mechanical over the 39 committed per-store sections in the source
repository's `tools/migration-harness/corpus-runs/README.md` (not shipped with the plugin;
see `openfga-to-spicedb`'s "The parity harness is not part of this plugin"), run from
`corpus-runs/`:

```bash
# 21 zero-finding / 18 filed
awk '/^### Findings/{getline; while($0 ~ /^$/) getline;
     print ($0 ~ /^None\./ ? "NONE" : "FILED")}' README.md | sort | uniq -c
# the 8 caveat-bearing stores, to intersect with the above
grep -c 'caveat' */schema.zed | grep -v ':0'
```

That file is the authority for every per-store claim; derive from it rather than recalling,
and state the command for any comparative or ordinal claim. This is not a stylistic
preference: this section previously shipped a "hardening did not converge" summary built on
11 of the 39 stores and left it standing after the other 28 were converted, and
`corpus-runs/README.md` records seven further false comparative claims shipped across the
same hardening loop ("The canonical store table").

A pack in this state is not finished, and the mitigation is structural rather than
statistical: every consumer of this contract is required to **halt on a construct it has no
rule for** rather than translate it by analogy. The failure mode is then a stop with a
source line attached, not a schema that compiles cleanly and answers a question differently
than the source did. See `openfga-to-spicedb`'s Status and Validation corpus sections.
