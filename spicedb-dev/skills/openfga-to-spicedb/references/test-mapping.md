# Test Mapping: OpenFGA → SpiceDB

Construct-by-construct translation rules for phase 5 (`.fga.yaml` → SpiceDB validation
YAML), plus the structural mismatches a generator hits immediately (fan-out, per-test tuple
scoping, multi-block collisions).

## Scope of this file

**This file is the algorithm**, in the same sense `naming-normalization.md` is: apply the
rules below directly to a source `.fga.yaml` file -- there is no tool to call, and this pack
ships no code (`SKILL.md`, "The parity harness is not part of this plugin"). A reference
implementation, `generate_validation`, exists in the plugin's source repository at
`tools/migration-harness/src/migration_harness/validation_gen.py`. It is not shipped and not
required, but it is covered by 240 passing tests (246 collected, 6 skipped) spanning all
39 corpus stores, so **the two must not diverge**: every rule below was checked against what
that function actually does, not against the design spec's aspirational description of it --
two places where the two disagree are called out explicitly, in "Two corrections to a naive
reading of this table" below.

This file assumes phase 1 (schema conversion) and its `migration-map.json` already exist.
Test conversion is a pure **consumer** of that artifact -- `IdMap.apply` for check surfaces,
`IdMap.write_relation` for relationship writes -- and invents no new normalization rule of
its own. See `schema-mapping.md` for the relation/permission split and
`naming-normalization.md` for the identifier algorithm; this file only states how those two
already-decided artifacts get applied when rendering a `.fga.yaml` file's data and checks.

Everything here was re-verified against **SpiceDB v1.56.0** and **zed v0.31.1** (floor:
v1.52.0), and every count is derived mechanically from the 39-store corpus at
`tools/migration-harness/corpus/sample-stores/stores/` (source) and
`tools/migration-harness/corpus-runs/<store>/` (converted), with the command stated.

## Construct table

| `.fga.yaml` | SpiceDB validation YAML | Where |
|---|---|---|
| `model:` (inline) / `model_file:` (sibling `.fga`/JSON) | `schema:` (inline) / `schemaFile:` (sibling path, resolved relative to the validation file) | mechanical |
| `tuples:` -- structured, at the document root **and/or** nested inside a `tests:` block | `relationships:` -- one string-form line per tuple | "Collect tuples from both the document root and every `tests:` block" |
| `check.assertions: {relation: true}` | `assertions.assertTrue` | "Fan-out" |
| `check.assertions: {relation: false}` | `assertions.assertFalse` | "Fan-out" |
| `check.context` | ` with {json}` suffix on the assertion string | "Check-time context becomes a ` with {json}` suffix" |
| tuple-level `condition: {name, context}` | `[name:{json}]` (or bare `[name]` if no context) suffix on the relationship-write line | "Check-time context..." (same section) |
| `list_objects` (expected-object-set) | **no equivalent** -- advisory finding | "`list_objects` / `list_users`: advisory only" |
| `list_users.assertions.users` (expected-subject-set) | **no working equivalent in this generator** -- advisory finding, same treatment as `list_objects` | "`list_objects` / `list_users`: advisory only" |
| *(no OpenFGA source construct)* | `assertions.assertCaveated` | "`assertCaveated`: the target-only third state" |

The **highest-consequence rule is not in this table at all**, because it isn't a construct
mapping -- it's a rule about which of two already-listed mappings applies, and getting it
backward is the single most likely error a migrating agent makes. See "Tuples are writes;
assertions are checks" immediately below.

### Two corrections to a naive reading of this table

Both corrections come from the same source: the design spec and `pack-contract.md` item 8 describe
the *intended* target shape; `generate_validation` -- the reference implementation, gated by
the full corpus -- is what actually ships, and per this file's own framing above, the code
wins where the two disagree.

**`list_users` does not actually convert to a `validation:` block.** The design spec's construct
table and `pack-contract.md` item 8 both state that a `list_users` block's expected subject
set "maps to a validation YAML `validation:` expected-relations block." Verified directly
that this claim does not survive contact with `zed validate`: a `validation:` block does not
accept a flat list of expected subject IDs -- SpiceDB's own `validation:` syntax requires a
per-subject **resolution path**, spelled `"[type:id] is <path-through-relations>"`, and `zed`
computes/checks that path against the schema, not against a bare membership claim:

```
$ cat validation.yaml
schemaFile: schema.zed
relationships: |-
  document:doc1#viewer@user:alice
assertions:
  assertTrue:
    - "document:doc1#view@user:alice"
  assertFalse: []
validation:
  document:doc1#view:
    - "[user:alice]"
$ zed validate --fail-on-warn validation.yaml
error: parse error in `[user:alice]`, line 10, column 7: For object and permission/relation
`document:doc1#view`, found different relationships for subject `user:alice`: Specified: ``,
Computed: `<document:doc1#viewer>`
```

Supplying the full path fixes it (`"[user:alice] is <document:doc1#viewer>"` validates
clean), but an OpenFGA `list_users.assertions.users` block never records that path -- it
records only the flat expected-member set, exactly the shape `list_objects.assertions`
records too. There is no `zed` flag that computes the path offline from a relationship set
alone (`zed validate --help` and `zed --help` both confirmed: no `--update` or equivalent);
producing one would mean deploying the schema and relationships to a live server and walking
`Expand`/`LookupSubjects` to reconstruct the path, which is outside what a `.fga.yaml` → YAML
text transform can do. `generate_validation`'s own `advisory_notes` function, read directly,
confirms this is the actual shipped behavior, not an oversight: it loops
`for key in ("list_objects", "list_users")` and treats both identically -- neither is
converted, both become the same `# NOTE(spicedbmigration):` comment. Treat `list_users`
exactly like `list_objects` for conversion purposes: an advisory finding, verified live
(`LookupSubjects`
or `Expand`, not a validation-YAML block). Count both together when stating how much of a
store's oracle survives conversion -- see "`list_objects` / `list_users`: advisory only"
below for the mechanically-derived coverage cost.

**`assertCaveated` has no OpenFGA source construct at all.** It exists in the *target*
vocabulary (see "`assertCaveated`" below) but nothing in a `.fga.yaml` file ever produces it:
`load_fga_assertions` reads every `check.assertions` value through `bool(expected)`, and
OpenFGA's own check API is strictly boolean -- there is no "caveated"/"conditional" third
state on the source side to map from. `spicedb_val.py`'s own docstring states the same fact
from the read side: "assertCaveated is returned uncompared: OpenFGA checks are boolean and
have no conditional third state, so these need human review rather than parity." This
generator will never emit `assertCaveated`; it is
listed in the construct table only because it is a real part of the target format a
migrating agent may need for **hand-written** supplementary checks (e.g. the checks this
file's collision sections below tell you to add by hand).

## Tuples are writes; assertions are checks

**The single most likely error in this whole pipeline.** A split relation (`schema-mapping.md`,
"The relation/permission split") has two SpiceDB names: the relation, `X__direct`, which is
the only legal *write* target, and the permission, `X`, which kept the original name and is
the only thing a *check* evaluates. `.fga.yaml`'s `tuples:` block and its `check:` block name
the *same* source relation, but they render to **different** SpiceDB names:

- A `tuples:` entry writes a relationship. Its `object`+`relation` render through
  `IdMap.write_relation(resource_type, relation)` -- if the relation split, this returns the
  `__direct` name; SpiceDB unconditionally rejects a write to a permission.
- A `check:` entry's `assertions` key becomes an assertion. It renders through
  `IdMap.apply`, which resolves permissions/relations for a check surface and always
  returns the split's *permission* name -- the unsuffixed one.

Swapping the two produces a validation file that loads and looks correct -- `zed validate`
does not distinguish "wrote to the wrong relation" from "wrote to the right one" -- and tests
the wrong surface silently.

**Worked example**, `openfga/sample-stores/stores/github` (corpus-verified, iteration 1).
`repo.reader` splits (`define reader: [user, team#member] or triager or repo_reader from
owner` mixes a type list with `or`), and the same store both writes to it and checks it, so
the contrast is visible in one committed file, `corpus-runs/github/validation.yaml`:

The **write** (inside `relationships:`) targets the split relation, `reader__direct`; the
**check** (inside `assertions:`) targets the permission, `reader`, unsuffixed:

```
relationships: |-
  repo:openfga/openfga#reader__direct@user:anne
assertions:
  assertTrue:
    - "repo:openfga/openfga#reader@user:anne"
```

`corpus-runs/github/migration-map.json` records the pairing an agent must consult to render
each side correctly:

```json
"relation_splits": {
  "repo": {
    "reader": {"relation": "reader__direct", "permission": "reader"}
  }
}
```

A **third** position exists inside `relationships:` itself, and it does *not* use the write
name: a userset **subject reference** (`T#rel` on the right of `@`) always names the
permission, because the allowed-type list on the SpiceDB side names the permission too
(`schema-mapping.md`, "A userset subject may point at a split permission"). Same file,
`organization.member` splits, and both positions appear in `relationships:` at once -- the
**write** (resource side) uses `member__direct`, the **subject reference** two lines later
stays unsuffixed, `member`:

```
relationships: |-
  organization:openfga#member__direct@user:erik
  organization:openfga#repo_admin@organization:openfga#member
```

**The rule for rendering any one tuple line, stated mechanically:**

| What you are rendering | Which name |
|---|---|
| The resource side of a `tuples:` entry (`object`+`relation`, left of `@`) | `IdMap.write_relation(resource_type, relation)` |
| The subject side of a `tuples:` entry, when the subject is a userset (the tuple's `user:` field spells `"T#rel"`) | `IdMap.apply(...).subject_relation` -- unsuffixed, same table `apply` always uses |
| Any `check:`-derived assertion line, `assertTrue`/`assertFalse`/`assertCaveated` | `IdMap.apply(...).permission` -- unsuffixed |

`write_relation` falls back to the ordinary `permissions[type][relation]` mapping whenever
`relation_splits` has no entry for that type/relation (an un-split relation writes under the
same name it is checked under), so applying `write_relation` unconditionally to every
resource-side write is safe -- it is a no-op except on the relations that actually split.

Verified: `corpus-runs/github/validation.yaml` passes `zed validate --fail-on-warn`
unedited -- `Success! - 9 relationships loaded, 6 assertions run, 0 expected relations
validated`.

## Fan-out: one check block becomes |users| × |objects| × |assertions| lines

A `.fga.yaml` `check:` entry can name a `user` or a plural `users` list, an `object` or a
plural `objects` list, and an `assertions` map with one or more relations. `fga_store.py`'s
`load_fga_assertions` expands this as a triple-nested loop -- one output assertion per
`(user, object, relation)` combination -- so |users| × |objects| × |assertions| individual
SpiceDB assertion lines come out of one source block.

**Worked example**, a hotel-management model. One check block:

```yaml
- user: user:priya
  object: hotel:harborview
  assertions:
    can_close_property: false
    can_update_listing: true
    can_book_room: true
    can_schedule_housekeeping: true
    can_order_supplies: true
    can_view_ledger: true
    can_view_dashboard: true
```

`|users|=1 × |objects|=1 × |assertions|=7` → 7 lines, split by the boolean each one carries,
rendered the same way `generate_validation` renders any check block:

```
assertions:
  assertTrue:
    - hotel:harborview#can_book_room@user:priya
    - hotel:harborview#can_order_supplies@user:priya
    - hotel:harborview#can_schedule_housekeeping@user:priya
    - hotel:harborview#can_update_listing@user:priya
    - hotel:harborview#can_view_dashboard@user:priya
    - hotel:harborview#can_view_ledger@user:priya
  assertFalse:
    - hotel:harborview#can_close_property@user:priya
```

The real corpus confirms the same fan-out at larger scale: `openfga/sample-stores/stores/
hospitality`'s own committed `corpus-runs/hospitality/validation.yaml` passes `zed validate
--fail-on-warn` unedited -- `Success! - 19 relationships loaded, 109 assertions run, 0
expected relations validated` -- the bulk of it exactly this multiplication, repeated across
every user/object pair that store's own `tests:` block names.

**Implement the full three-dimensional product, even though the corpus never exercises two
of the three dimensions at once.** Mechanically confirmed across the entire corpus -- every
`check:` entry in all 51 `.fga.yaml` files uses the singular `user`/`object` keys; none uses
the plural `users`/`objects` form:

```
$ python3 -c "
import yaml, pathlib
root = pathlib.Path('corpus/sample-stores/stores')
found = []
for d in sorted(root.iterdir()):
    for f in d.glob('*.fga.yaml'):
        doc = yaml.safe_load(f.read_text()) or {}
        for t in doc.get('tests') or []:
            for c in t.get('check') or []:
                if 'users' in c or 'objects' in c:
                    found.append((d.name, f.name))
print(len(found), found[:5])
"
0 []
```

Every observed multiplicity comes from the `assertions` dict (multiple relations checked
against one fixed user/object pair, as in the `hospitality` example above). The format
permits plural `users:`/`objects:` (`fga_store._effective` handles both), so implement the
full product; do not special-case it down to "assertions only" on the strength of this
corpus, since a real customer repository can use the plural form even though none of these
39 samples do.

**Algorithm**, mirroring `load_fga_assertions` exactly:

```
for test in doc["tests"]:
    for entry in test.get("check", []):
        users   = entry["users"]   if "users"   in entry else [entry["user"]]
        objects = entry["objects"] if "objects" in entry else [entry["object"]]
        context = canonical_json(entry.get("context"))   # "" if absent/empty
        for user in users:
            for obj in objects:
                for relation, expected in entry["assertions"].items():
                    emit(user, obj, relation, bool(expected), context)
```

Entries with both singular and plural forms for the same slot (`user` and `users` both
present) are invalid input, mirroring the `fga` CLI's own validation.

## Check-time context becomes a ` with {json}` suffix

`check.context` (a per-check value map, supplied at check time rather than bound to a
relationship) becomes a ` with {json}` suffix appended to the assertion string, only when
truthy -- `datatype_test:one#is_valid@user:int with {"_int":1}`. The JSON must be
**canonicalized**: `json.dumps(context, sort_keys=True, separators=(",", ":"))`. This is not
cosmetic -- the read side (`spicedb_val.parse_assertion_string`) re-canonicalizes the same
way and compares by value, and `zed`'s own assertion parser is exact-string, so an
inconsistently-formatted suffix breaks round-tripping.

**Emit these assertion lines unquoted, and never wrap one in double quotes.** The canonical
JSON contains `"` characters, so a double-quoted YAML scalar around an assertion carrying
context produces `- "...with {"current_time":"..."}"`, which is not valid YAML -- `zed
validate` fails it with `did not find expected '-' indicator`. Examples elsewhere in this file
show a quoted form; that form is only safe for assertions with **no** context suffix, and the
two interact, so use the unquoted form uniformly rather than deciding per line. A single-quoted
scalar also works if a quote is unavoidable, since the suffix contains no single quotes.

A tuple-level `condition:` block (`{"name": ..., "context": {...}}`) becomes the same kind of
suffix, on the *relationship-write* line instead: `[name:{json}]`, or bare `[name]` if the
condition carries no context. **The caveat name passes through verbatim, unnormalized** --
`idmap.py` has no caveat namespace, so nothing here renames it; it must already satisfy
SpiceDB's caveat-name grammar (`^[a-z][a-z0-9_]{1,62}[a-z0-9]$`, `naming-normalization.md`
"Caveat names are not bound by the name regex"), since the relationship-string grammar
enforces it even though a bare `caveat` declaration would accept something looser. Verified:

```
>>> _check_caveat_name("My-Cond")
InputError: caveat name 'My-Cond' does not satisfy SpiceDB's caveat-name pattern
'^[a-z][a-z0-9_]{1,62}[a-z0-9]$'
```

**A second, sharper risk beyond the syntactic check: the name must match what phase 1 actually
declared, not just be syntactically legal.** `_check_caveat_name` only validates the regex --
it has no access to `schema.zed` and cannot tell whether a caveat by that exact name was
actually declared there. If phase 1's schema conversion renamed a condition (a collision, a
reserved word, or any other reason `naming-normalization.md` lists), the *raw* source
condition name is still what gets emitted here unless the migrating agent explicitly applies
the same rename. A raw name that already satisfies the strict regex passes this check and
then fails later, at `zed validate`, with a schema-linkage error (`could not lookup caveat`)
rather than here. **Not corpus-forced** -- mechanically confirmed zero of the 39 stores needed
a caveat rename (`naming-normalization.md`'s own confirmation on `condition-data-types`
generalizes: every condition name in every store that uses conditions already satisfies the
strict regex) -- but the rule is: whenever phase 1 records a caveat rename in
`migration-plan.md`, apply that same rename here, not the raw source name.

**Worked example**, `openfga/sample-stores/stores/condition-data-types`. Two `tests:` blocks
assert the identical semantic fact ("is_valid holds for a value of each of nine CEL types")
through two different mechanisms -- context bound at write time (block 1) vs. supplied at
check time (block 2):

```yaml
# tests[0], "Test with context in tuples"
tuples:
  - {user: user:int, object: datatype_test:one, relation: is_valid,
     condition: {name: is_valid_uint, context: {_uint: 1}}}
check:
  - {user: user:int, object: datatype_test:one, assertions: {is_valid: true}}

# tests[1], "Test with context in checks"
tuples:
  - {user: user:int, object: datatype_test:one, relation: is_valid,
     condition: {name: is_valid_int}}
check:
  - {user: user:int, object: datatype_test:one, context: {_int: 1},
     assertions: {is_valid: true}}
```

Generated (`generate_validation` run directly against this store; see "Collect tuples..."
below for why both blocks' tuples are even present), and verified against `zed validate
--fail-on-warn`:

```
relationships: |-
  datatype_test:one#is_valid@user:double[is_valid_double:{"_double":1}]
  datatype_test:one#is_valid@user:duration[is_valid_duration:{"_duration":"10s"}]
  datatype_test:one#is_valid@user:int[is_valid_uint:{"_uint":1}]
  ...
assertions:
  assertTrue:
  - datatype_test:one#is_valid@user:int
  - datatype_test:one#is_valid@user:uint
  ...
  - datatype_test:one#is_valid@user:int with {"_int":1}
  - datatype_test:one#is_valid@user:uint with {"_uint":1}
  ...
```

`Success! - 9 relationships loaded, 18 assertions run, 0 expected relations validated`.

**Mechanical count of exposure:** tuple-level `condition:` blocks appear in **8 of the 39**
stores, not just this one -- `advanced-entitlements` (6), `banking` (2),
`condition-data-types` (18), `groups-resource-attributes` (2), `ip-based-access` (1),
`modeling-guide` (1), `superadmin` (1), `temporal-access` (2).

The count must apply this file's own **"Selecting the source test file"** rule, not
hard-code `store.fga.yaml`. An earlier version of this script read `store.fga.yaml` only and
therefore reported 7: it silently skipped `modeling-guide`, the one store with no
`store.fga.yaml` at all, whose selected file (`step-10-fine-grained-api-access.fga.yaml`)
carries a `time_based_grant` condition -- and whose converted output records the consequence
at `corpus-runs/modeling-guide/validation.yaml:16`:

```
system:root#super_admin@user:sam[time_based_grant:{"grant_time":"2024-07-21T00:00:00Z","grant_duration":"1h"}]
```

```
$ python3 -c "
import yaml, pathlib
ROOT = pathlib.Path('corpus/sample-stores/stores')
MANUAL = {'modeling-guide': 'step-10-fine-grained-api-access.fga.yaml'}   # 'Selecting the source test file'
def select(src):
    if (src / 'store.fga.yaml').is_file(): return src / 'store.fga.yaml'
    cands = sorted(src.glob('*.fga.yaml'))
    return cands[0] if len(cands) == 1 else None
total = 0
for d in sorted(ROOT.iterdir()):
    if not d.is_dir(): continue
    f = d / MANUAL[d.name] if d.name in MANUAL else select(d)
    if f is None: print('UNRESOLVED', d.name); continue
    doc = yaml.safe_load(f.read_text()) or {}
    n = sum(1 for tp in (doc.get('tuples') or []) if tp.get('condition'))
    n += sum(1 for t in (doc.get('tests') or []) for tp in (t.get('tuples') or []) if tp.get('condition'))
    if n: print(d.name, n); total += 1
print('stores:', total)
"
advanced-entitlements 6
banking 2
condition-data-types 18
groups-resource-attributes 2
ip-based-access 1
modeling-guide 1
superadmin 1
temporal-access 2
stores: 8
```

## `assertCaveated`: the target-only third state

SpiceDB's validation YAML has a third assertion bucket, `assertCaveated`, for a relationship
that carries a caveat whose context is not fully supplied at check time -- the answer is
neither `true` nor `false`, it is *caveated* (indeterminate). As established in "Two
corrections..." above, this generator never produces one: OpenFGA checks are strictly
boolean, so no source construct maps to it. It is documented here because a migrating agent
may need to **hand-write** one -- for example, as a supplementary regression check alongside
an advisory finding this file's collision sections tell you to add by hand.

Minimal verified example. A relationship written with a caveat but **no bound context**,
checked with **no context supplied either**, is genuinely indeterminate:

```zed
definition user {}

caveat is_valid(expected int) {
	expected > 0
}

definition document {
	relation viewer: user with is_valid
	permission view = viewer
}
```
```yaml
schemaFile: schema-caveat.zed
relationships: |-
  document:doc1#viewer@user:alice[is_valid]
assertions:
  assertTrue: []
  assertFalse: []
  assertCaveated:
    - "document:doc1#view@user:alice"
```

`zed validate --fail-on-warn`: `Success! - 1 relationships loaded, 0 assertions run, 0
expected relations validated`. (Binding context at write time, `[is_valid:{"expected":1}]`,
resolves the caveat fully and moves the assertion to `assertTrue` instead -- confirmed by
constructing that variant and observing `zed validate` reject it under `assertCaveated` with
`Expected relation or permission ... to be caveated`, since it is no longer caveated at all
once fully bound.)

## Selecting the source test file

Before any of the rules above apply, an agent has to find the right `.fga.yaml`. The corpus
exercises three shapes, and one of them is genuinely ambiguous:

1. **`store.fga.yaml` present** -- use it. 38 of the 39 corpus stores ship a file with this
   exact name.
2. **No `store.fga.yaml`, exactly one other `*.fga.yaml`** -- use it. Sound as a general rule,
   but **not exercised anywhere in this corpus**: the one store lacking `store.fga.yaml`
   (`modeling-guide`) has ten candidates, not one, so this branch never actually fires on any
   of the 39. Keep it for a customer repository that names its single test file something
   else.
3. **Two or more candidates and no `store.fga.yaml`** -- halt; choosing between them needs
   human judgment, not a glob.
4. **No `*.fga.yaml` anywhere in the repository** -- **not a halt.** There is no ambiguity to
   resolve and nothing to choose between: the project simply has no fixture-based tests. This
   is the common case outside the corpus -- every one of the 39 corpus stores ships a fixture,
   and real projects frequently ship none -- so do not read case 3's halt as covering it.
   `/spicedb-dev:migrate-tests` step 4 owns what happens next (route to
   `/spicedb-dev:test-permissions`, hand back any non-fixture suite as Needs action, record
   the phase `pending` rather than `failed`); this rule's job ends at establishing that there
   is no source file to select.

```
$ total=0; has_store=0; sole=0
$ for d in corpus/sample-stores/stores/*/; do
    total=$((total+1))
    if [ -f "${d}store.fga.yaml" ]; then
      has_store=$((has_store+1))
      n=$(ls "$d"*.fga.yaml | wc -l | tr -d ' ')
      [ "$n" -eq 1 ] && sole=$((sole+1))
    fi
  done
$ echo "total=$total has_store_fga_yaml=$has_store sole_and_is_store=$sole"
total=39 has_store_fga_yaml=38 sole_and_is_store=37
```

Rule 1 alone resolves 38 of 39. It resolves `modular` too, even though that store's own
`README.md` ("Try It Out") recommends running its three *per-module* test files
(`core.fga.yaml`, `wiki.fga.yaml`, `issue-tracker.fga.yaml`) and never mentions
`store.fga.yaml` at all -- `store.fga.yaml` is still the correct pick: its own baseline
(`fga model test --tests store.fga.yaml`) is green (`Checks 5/5 passing`), and it is what the
committed corpus conversion actually used. Do not let a store's own narrative documentation
override the filename rule.

**`modeling-guide` is the one store rule 3 catches**, and it needs a real decision, not just
a tie-break: it ships ten `step-N-*.fga.yaml` files (one per stage of a tutorial video
series: multi-tenancy, groups, public access, relationship-based ABAC, super-admin,
conditional relationships, custom roles, application access, fine-grained API access), each
cumulative on the last. The corpus's own resolution converts **only**
`step-10-fine-grained-api-access.fga.yaml`, the final, most feature-complete checkpoint --
not an assembly of all ten -- because it is the only file that exercises every prior step's
constructs at once (`corpus-runs/README.md`'s `modeling-guide` section: "Steps 1-9 are not
separately converted; this is a scope decision for this batch, not a claim that they are
structurally identical to step 10."). "Cumulative" is a superset claim, not an identity
claim -- one relation (`organization.application`) is genuinely dropped, not renamed, between
step 9 and step 10, confirmed by reading both `model:` blocks side by side. When a source
repository ships this shape (a numbered/staged series with no unified file), pick the most
feature-complete file as the one to convert and record the choice and the reason in
`migration-plan.md`; do not silently pick an arbitrary one (a naive `glob()`'s first match is
filesystem-order-dependent, not alphabetical, and picking the wrong step file produces a
validation file whose assertion set is a strict subset of the store's real oracle).

## Collect tuples from both the document root and every `tests:` block

`.fga.yaml` allows a `tuples:` key at the document root **and**, independently, nested inside
an individual `tests:` block. `fga model test` treats each `tests:` block as an isolated
dataset, so a block's own `tuples:` seeds state for that scenario alone, layered on top of
(never replacing) any root-level tuples. **Both sources must be collected, in this order:
root-level first, then each `tests:` block's own entries in file order.** The order matters
directly -- it decides which binding wins in the collision case below.

Reading only the root-level key is a real, high-consequence bug, not a hypothetical one: a
store with **zero** root-level tuples and all of its writes nested inside `tests:` blocks
produces a completely empty `relationships:` block if only `doc.get("tuples")` is read.
`condition-data-types` is exactly this shape -- all 18 of its tuple writes live inside its two
`tests:` blocks, none at the root -- and an implementation that reads only the root silently
drops all 18, while every `check:`-derived assertion is generated normally (assertions come
from a different code path, `load_fga_assertions`, which was never affected). The resulting
file loads under a naive set-of-assertions comparison with no error at all: every assertion
target still "exists" as far as string comparison goes, and only `zed validate` -- which
actually needs the relationships to be present to resolve a permission -- catches it, with
`Expected relation or permission ... to exist` for every single assertion.

**Mechanical count of exposure**, across all 39 stores:

```
$ for d in corpus/sample-stores/stores/*/; do n=$(basename "$d"); for f in "$d"*.fga.yaml; do
    [ -f "$f" ] || continue; python3 -c "
import yaml
doc = yaml.safe_load(open('$f')) or {}
top = len(doc.get('tuples') or [])
nested = sum(len(t.get('tuples') or []) for t in (doc.get('tests') or []))
if nested: print(f'$n\t$f\ttop={top}\tnested={nested}')
"; done; done
abac-with-rebac        .../store.fga.yaml   top=5   nested=2
condition-data-types   .../store.fga.yaml   top=0   nested=18
```

Only two of 39 stores have any `tests:`-block-scoped `tuples:` at all. `condition-data-types`
is the nested-only shape this section's bug hits hardest (root=0). `abac-with-rebac` mixes
both (root=5, nested=2) -- and its own nested tuples are the subject of the next section's
second collision mechanism, not this one.

## Multi-block collisions: two distinct mechanisms, opposite visibility

Once tuples from more than one `tests:` block are collected into one SpiceDB relationship
graph, two genuinely different collision shapes can occur. They are easy to conflate because
both stem from the same root cause (`fga model test`'s per-block isolation has no SpiceDB
counterpart), but they have opposite failure signatures and opposite fixes. Verified
first-hand, on a live v1.56.0 server, that the two behave as different as they sound:

```
$ uv run migration-harness --store corpus/sample-stores/stores/condition-data-types/store.fga.yaml \
    --converted corpus-runs/condition-data-types/validation.yaml --map corpus-runs/condition-data-types/migration-map.json
PARITY OK (18 assertions compared)

$ uv run migration-harness --store corpus/sample-stores/stores/abac-with-rebac/store.fga.yaml \
    --converted corpus-runs/abac-with-rebac/validation.yaml --map corpus-runs/abac-with-rebac/migration-map.json
PARITY FAILED (4 assertions compared)
AMBIGUOUS     document:readme#can_view@user:anne same-side conflict: expected=False vs expected=True
AMBIGUOUS     document:readme#can_edit@user:bob same-side conflict: expected=True vs expected=False
```

`condition-data-types`' collision (mechanism A, below) is **silently green** -- the harness
sees nothing wrong, because both colliding blocks assert the *same* boolean, just for
different reasons. `abac-with-rebac`'s collision (mechanism B) is **loudly red**, because the
two blocks assert *different* booleans for the same `(subject, permission, resource)` key.
The silent one is the more dangerous of the two precisely because nothing downstream flags
it unless you go looking.

### A. Same triple, different caveat context -- zed rejects the union; first-seen wins; must be recorded

A `.fga.yaml` file can reuse the identical `(object, relation, subject)` triple across two
`tests:` blocks, scoped apart only by `fga model test`'s own per-block isolation. SpiceDB
allows exactly one relationship per triple, so `zed validate`'s loader rejects the naive
union of both blocks' writes outright. Reproduced directly (both of `condition-data-types`'
bindings for `user:int` merged naively):

```
$ cat cdt-naive.yaml
schemaFile: cdt-schema.zed
relationships: |-
  datatype_test:one#is_valid@user:int[is_valid_uint:{"_uint":1}]
  datatype_test:one#is_valid@user:int[is_valid_int]
assertions:
  assertTrue:
    - "datatype_test:one#is_valid@user:int"
  assertFalse: []
$ zed validate --fail-on-warn cdt-naive.yaml
error: found repeated relationship `datatype_test:one#is_valid@user:int[is_valid_int]`
```

**Resolution: first-seen wins** (the collection order established in the previous section --
root-level tuples first, then each `tests:` block in file order), **and the collision must be
recorded, never silently dropped.** Silently keeping one binding is not enough on its own:
once the discarded scenario's own tuple is gone, its `check:` assertion can still evaluate to
the "right" boolean for the **wrong reason**, because SpiceDB merges request-supplied caveat
context with whatever context the surviving relationship already has bound, and an
already-satisfied bound caveat does not need the check's own context to agree with it.
Verified first-hand on a live v1.56.0 server: with *only* the winning `is_valid_uint` binding
written (the discarded `is_valid_int` binding entirely absent), a check supplying the
**wrong, unrelated** context -- or none at all -- still returns `true`:

```
$ zed relationship create datatype_test:one is_valid user:int --caveat 'is_valid_uint:{"_uint":1}' ...
$ zed permission check datatype_test:one is_valid user:int --caveat-context '{"_totally_unrelated_key":999}' ...
true
$ zed permission check datatype_test:one is_valid user:int ...
true
```

The discarded scenario's check is not merely *untested* -- it is *unverified*, and it can
pass by pure coincidence with no signal anywhere in `zed validate` or a harness run. This is
exactly why silently keeping one value is not an acceptable resolution on its own: **record
every colliding triple in `migration-plan.md`, naming both bindings, and flag the discarded
one for a hand-written check** (a live `zed permission check --caveat-context` probe against
the discarded scenario's own context, verified by hand, not trusted to the converted graph).

The resolved, correctly-recorded output for this store (`generate_validation` run directly,
byte-for-byte -- all nine colliding items appear below, one per colliding subject type, each
on its own line under a single one-sentence context line, per findings-report.md's "Inline
markers", "(b) Generated-file header manifest"):

```
# NOTE(spicedbmigration): merging isolated tests: block(s) below into one shared graph carries risk zed validate can't see -- verify each by hand (see schema-mapping.md, "Multiple isolated test fixtures colliding in one converted graph"):
#   - datatype_test:one#is_valid@user:int: kept `datatype_test:one#is_valid@user:int[is_valid_uint:{"_uint":1}]`, discarded conflicting write `datatype_test:one#is_valid@user:int[is_valid_int]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:uint: kept `datatype_test:one#is_valid@user:uint[is_valid_uint:{"_uint":1}]`, discarded conflicting write `datatype_test:one#is_valid@user:uint[is_valid_uint]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:double: kept `datatype_test:one#is_valid@user:double[is_valid_double:{"_double":1}]`, discarded conflicting write `datatype_test:one#is_valid@user:double[is_valid_double]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:string: kept `datatype_test:one#is_valid@user:string[is_valid_string:{"_string":"1"}]`, discarded conflicting write `datatype_test:one#is_valid@user:string[is_valid_string]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:timestamp: kept `datatype_test:one#is_valid@user:timestamp[is_valid_timestamp:{"_timestamp":"2019-02-01T00:00:00Z"}]`, discarded conflicting write `datatype_test:one#is_valid@user:timestamp[is_valid_timestamp]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:duration: kept `datatype_test:one#is_valid@user:duration[is_valid_duration:{"_duration":"10s"}]`, discarded conflicting write `datatype_test:one#is_valid@user:duration[is_valid_duration]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:mapstring: kept `datatype_test:one#is_valid@user:mapstring[is_valid_map_string:{"_mapstring":{"key":"1"}}]`, discarded conflicting write `datatype_test:one#is_valid@user:mapstring[is_valid_map_string]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:liststring: kept `datatype_test:one#is_valid@user:liststring[is_valid_list_string:{"_liststring":["1"]}]`, discarded conflicting write `datatype_test:one#is_valid@user:liststring[is_valid_list_string]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
#   - datatype_test:one#is_valid@user:ipaddress: kept `datatype_test:one#is_valid@user:ipaddress[is_valid_ipaddress:{"_ipaddress":"192.168.0.1"}]`, discarded conflicting write `datatype_test:one#is_valid@user:ipaddress[is_valid_ipaddress]` -- two tests: blocks wrote different caveat bindings to this same triple; the discarded scenario's checks are unverified by this converted graph, not merely untested (they can pass by coincidence)
schemaFile: schema.zed
relationships: |-
  ...
```

Verified: this file (all 9 advisory entries present, full `relationships`/`assertions`
blocks) passes `zed validate --fail-on-warn` -- `Success! - 9 relationships loaded, 18
assertions run, 0 expected relations validated`.

**Detection script.** For an agent converting a store cold, run this before merging any
`tests:` block's tuples into one graph -- it is the mechanism above, standalone, with no
dependency on the (unshipped) harness:

```python
#!/usr/bin/env python3
# check_test_collisions.py -- for a source .fga.yaml, find every (object,
# relation, subject) triple that recurs across tests: blocks with a
# different condition (a caveat-collision: zed will accept only one, and the
# other's checks become unverified-not-untested), and every object that two
# different tests: blocks write two or more different relations onto (a
# same-object/different-relation collision: cannot be merged into one
# converted graph at all, needs per-scenario file splitting). Exit 1 and
# print every collision found; exit 0 if the store's tests: blocks are
# collision-free.
import sys

import yaml


def check(path):
    doc = yaml.safe_load(open(path)) or {}
    triples = {}  # (obj, rel, subj) -> [(block_name, condition), ...]
    by_object = {}  # obj -> {rel: {block_name, ...}}

    # Mechanism A is keyed over BOTH sources -- a root-level tuple and a nested one can
    # collide on the same triple with different conditions, and the root entries are
    # written into every derived file, so that collision is real. Mechanism B is keyed
    # over tests: blocks ONLY: root entries are shared baseline state present in every
    # scenario, not an isolated fixture, so they never make two blocks "collide".
    sources = [("<document root>", doc.get("tuples") or [], False)]
    sources += [
        (t.get("name", "<unnamed test>"), t.get("tuples") or [], True)
        for t in doc.get("tests") or []
    ]

    for name, entries, counts_for_b in sources:
        for entry in entries:
            key = (entry["object"], entry["relation"], entry["user"])
            triples.setdefault(key, []).append((name, entry.get("condition")))
            if counts_for_b:
                by_object.setdefault(entry["object"], {}).setdefault(
                    entry["relation"], set()
                ).add(name)

    problems = []
    for (obj, rel, subj), writers in sorted(triples.items()):
        conditions = {str(c) for _, c in writers}
        if len(writers) > 1 and len(conditions) > 1:
            detail = "; ".join(f"{b}={c}" for b, c in writers)
            problems.append(
                f"{path}: CAVEAT COLLISION {obj}#{rel}@{subj} -- {detail} "
                "(zed accepts only one write per triple; first-seen wins, "
                "the rest are unverified, not merely untested)"
            )

    for obj, rels in sorted(by_object.items()):
        contributing = {b for blocks in rels.values() for b in blocks}
        if len(rels) > 1 and len(contributing) > 1:
            detail = "; ".join(
                f"{rel} (from {', '.join(sorted(blocks))})"
                for rel, blocks in sorted(rels.items())
            )
            problems.append(
                f"{path}: RELATION COLLISION {obj} written under {len(rels)} "
                f"different relations by {len(contributing)} tests: blocks "
                f"-- {detail} (cannot be merged into one graph; split into "
                "one derived --store file per tests: block instead)"
            )
    return problems


problems = [p for f in sys.argv[1:] for p in check(f)]
for p in problems:
    print(p)
sys.exit(1 if problems else 0)
```

Verified against the complete corpus -- run over all 51 `.fga.yaml` files (`find
corpus/sample-stores/stores -name "*.fga.yaml" | wc -l` → 51), it reports **exactly** nine
`CAVEAT COLLISION` lines for `condition-data-types` and exactly one `RELATION COLLISION` line
for `abac-with-rebac` (see mechanism B immediately below), and **nothing** for the other 37
stores -- zero false positives, matching `generate_validation`'s own shipped detectors
(`_dedupe_tuple_lines`'s collision notes and `_cross_block_relation_conflicts`) line for line
in what they flag. Exit status is 1 when anything is flagged, 0 otherwise.

**Note the two mechanisms read different scopes, and mechanism A's is the wider one.** An
earlier version of this script keyed *both* off `doc["tests"]` alone, which made it
structurally unable to see a root-level tuple colliding with a nested one -- a real mechanism-A
case, since root entries are written into every derived file. The version above keys A over
the document root *and* every `tests:` block, and keys B over `tests:` blocks only (root
entries are shared baseline state, not an isolated fixture, so they can never make two blocks
collide). Widening A changes nothing on this corpus -- the counts above are from the
root-inclusive version -- because no corpus store happens to exercise a root-vs-nested caveat
collision; it closes a gap that was latent, not one that was firing.

### B. Same object, different relation, mutually exclusive scenarios -- cannot merge; split per scenario

A different mechanism, and one `zed validate` never objects to at all. Instead of a
recurring identical triple, two different `tests:` blocks can each write a **different**
relation onto the **same** object, where each block represents one real-world state of that
object (draft vs. published) rather than a duplicate fact. No raw triple repeats, so `zed
validate`'s loader accepts writing both relations at once without complaint -- the problem is
semantic, not syntactic, and shows up only once the schema resolves a permission through
whichever one is present.

**Worked example**, `openfga/sample-stores/stores/abac-with-rebac`. Its two `tests:` blocks
each add exactly one nested tuple to the same `document:readme`:

```yaml
# tests[0], "Test permissions for draft document"
tuples: [{user: document:readme, relation: draft, object: document:readme}]
check:
  - {user: user:anne,   object: document:readme, assertions: {can_edit: false, can_view: false}}
  - {user: user:bob,    object: document:readme, assertions: {can_edit: true,  can_view: true}}
  - {user: user:jeremy, object: document:readme, assertions: {can_edit: false, can_view: false}}

# tests[1], "Test permissions for published document"
tuples: [{user: document:readme, relation: published, object: document:readme}]
check:
  - {user: user:anne,   object: document:readme, assertions: {can_edit: false, can_view: true}}
  - {user: user:bob,    object: document:readme, assertions: {can_edit: false, can_view: true}}
  - {user: user:jeremy, object: document:readme, assertions: {can_edit: false, can_view: false}}
```

`can_edit@bob` and `can_view@anne` genuinely disagree between the two blocks -- that is the
whole point, they encode two different real-world states of the same document -- and merging
both blocks' checks into one flat assertion list (which `load_fga_assertions` does; it never
reads `tuples:` at all) hands the harness's comparator two entries with the same key and
different expected answers. `parity.py`'s `_dedupe` correctly recognizes this as a same-side
conflict and reports `AMBIGUOUS` rather than picking one -- reproduced above in "Multi-block
collisions" -- but **there is no way to resolve it by picking one relationship to persist**,
unlike mechanism A. Verified first-hand, on a live v1.56.0 server: writing only `published`
reproduces every one of that scenario's six expected answers exactly; writing `draft` on top
(so **both** are present at once, the shape a naive whole-store merge produces) flips the two
colliding checks to the *wrong* answer for whichever scenario is not the one currently
intended, while every non-colliding check stays correct:

```
$ # state: draft only (bob owns + verified, anne+jeremy are viewers, anne verified)
$ for u in anne bob jeremy; do for p in can_edit can_view; do
    zed permission check document:readme $p user:$u ...; done; done
can_edit@anne=false  can_view@anne=false
can_edit@bob=true    can_view@bob=true
can_edit@jeremy=false can_view@jeremy=false
# matches the "draft" scenario's own expectations exactly

$ zed relationship create document:readme published document:readme ...
$ # state: BOTH draft and published now present -- the naive whole-store merge
$ for u in anne bob jeremy; do for p in can_edit can_view; do
    zed permission check document:readme $p user:$u ...; done; done
can_edit@anne=false  can_view@anne=true    # published's own expectation, but WRONG for draft
can_edit@bob=true    can_view@bob=true     # draft's own expectation, but WRONG for published
can_edit@jeremy=false can_view@jeremy=false
```

`can_edit@bob` stays `true` (correct only for "draft", not "published" -- bob keeps edit
access on a document that is supposed to already be locked for editing once published) and
`can_view@anne` flips to `true` (correct only for "published", not "draft" -- anne can now
see a document that is supposed to still be unpublished). Both are silently wrong for
whichever scenario is not the one actually in force. This is a real modeling error the
converted data would ship with, not merely a harness limitation: reproduced independently of
the harness, directly against live permission checks.

**Resolution: pick one scenario as canonical, and verify the excluded one(s) separately.**
There is no single-graph fix, because the scenarios are, by construction, mutually exclusive
states of one object.

1. Pick the scenario that best represents steady-state seed data for the shipped
   `validation.yaml` (this store's own committed conversion picks "published"; record the
   choice and the reason in `migration-plan.md`).
2. Derive one `--store` file **per `tests:` block**, holding only that block's own tuples and
   checks (root-level tuples, shared baseline state, go in every derived file). Run the
   parity check against each derived pair independently -- each one reaches `PARITY OK` on
   its own, confirmed against the plugin's source repository (`store-draft.fga.yaml` and
   `store-published.fga.yaml` are themselves derived, verbatim slices of the upstream store's
   two `tests:` blocks, so -- like the rest of the corpus -- they are gitignored there rather
   than committed; `corpus-runs/README.md`'s abac-with-rebac section shows how to regenerate
   them from `corpus/sample-stores/stores/abac-with-rebac/store.fga.yaml`. `validation-draft.
   yaml` and `validation.yaml` are committed):

   ```
   $ uv run migration-harness --store corpus-runs/abac-with-rebac/store-draft.fga.yaml \
       --converted corpus-runs/abac-with-rebac/validation-draft.yaml --map corpus-runs/abac-with-rebac/migration-map.json
   PARITY OK (6 assertions compared)
   $ uv run migration-harness --store corpus-runs/abac-with-rebac/store-published.fga.yaml \
       --converted corpus-runs/abac-with-rebac/validation.yaml --map corpus-runs/abac-with-rebac/migration-map.json
   PARITY OK (6 assertions compared)
   ```

3. Record, in `migration-plan.md`, exactly which checks the canonical whole-store run cannot
   verify (the colliding keys named in its `AMBIGUOUS` output) and how each was verified
   instead -- a second harness invocation against the per-scenario derived file (as above), a
   live-server toggle-and-check (as the worked example above does), or both.

**The canonical (whole-store) invocation of a store shaped this way legitimately reports
`AMBIGUOUS` and exit 1 -- for *any* correct conversion, not just an imperfect one.** This is
not a bug to chase: a green `zed validate` on the shipped `validation.yaml` and zero
`MISSING`/`EXTRA`/`CONTRADICTION` from the harness, alongside the two `AMBIGUOUS` lines, is
the expected steady state for this store shape. Do not iterate on the schema or the
conversion trying to make the canonical run's `AMBIGUOUS` go away; it structurally cannot,
for the reason stated above -- the fix is the derived per-scenario verification, not a
different conversion.

## `list_objects` / `list_users`: advisory only

Neither `list_objects` nor `list_users` converts into anything in the validation-YAML output.
`advisory_notes` (the shipped reference implementation) treats them identically: for every
`list_objects`/`list_users` block found in any `tests:` entry, it records
`test "<name>": <key> (<n> entries)` and emits nothing into `relationships:` or `assertions:`.
Rendered into the file as a `# NOTE(spicedbmigration):` comment, so the finding is visible on
the file itself, not only in a log:

**Worked example**, `openfga/sample-stores/stores/gdrive` (`generate_validation` run
directly):

```
# NOTE(spicedbmigration): list_objects/list_users block(s) below have no validation-YAML equivalent and were not converted -- review the source .fga.yaml store directly:
#   - test "Test which documents can Anne read": list_objects (1 entries)
#   - test "Test who can access doc:2021-roadmap": list_users (1 entries)
#   - test "Check if the right users have access to the right documents": list_users (4 entries)
schemaFile: schema.zed
relationships: |-
  doc:2021-roadmap#parent@folder:product-2021
  doc:2021-roadmap#viewer@user:beth
  doc:public-roadmap#parent@folder:product-2021
  doc:public-roadmap#viewer@user:*
  folder:product-2021#owner@user:anne
  folder:product-2021#viewer__direct@group:fabrikam#member
  group:contoso#member@user:anne
  group:contoso#member@user:beth
  group:fabrikam#member@user:charles
assertions:
  assertTrue:
  - doc:2021-roadmap#can_write@user:anne
  - doc:2021-roadmap#can_read@user:charles
  assertFalse:
  - doc:2021-roadmap#can_change_owner@user:beth
```

Verified: `zed validate --fail-on-warn` on this exact output -- `Success! - 9 relationships
loaded, 3 assertions run, 0 expected relations validated`. Record both kinds of note in
`migration-plan.md` as Class C advisory findings (not dropped silently), and verify them
live once the schema is deployed: a `list_objects` block's expected object set via
`LookupResources`, a `list_users` block's expected subject set via `LookupSubjects`.

**Coverage cost, derived mechanically across all 39 stores.** Define harness-visible fraction
as `checks / (checks + list_objects + list_users)`, counting `checks` the way
`load_fga_assertions` does (the fully fanned-out count -- see "Fan-out" above) and counting
each `list_objects`/`list_users` entry by the number of relation keys inside its own
`assertions` map (the same convention `fga model test`'s own reported `ListObjects`/`ListUsers`
counts use). Cross-checked exactly against `corpus-runs/README.md`'s independently-published
per-store figures for `github` 6/10, `gdrive` 3/9, `advanced-entitlements` 16/19,
`multitenant-rbac` 12/13, and `file-storage` 20/30 -- all five match. **One store, deliberately
excluded from that cross-check:** `custom-roles`' own write-up states **9/15**, expanding each
`list_objects`/`list_users` entry by its *expected-member* count instead (2 members + 4
members) rather than its relation-key count (1 + 1, which is what this convention and this
script actually compute, and what a *different* mention of that same store elsewhere in
`corpus-runs/README.md` states -- `9/11`, contradicting the store's own section). The README's
own text already flags this exact ambiguity ("Per-store sections sometimes expand a list test
into its expected members instead -- e.g. `custom-roles` reports 9/15 that way. Both orderings
agree on the minimum; state which you mean."). Stating which one this script means: relation-key
count, matching `fga model test`'s own reporting, not expected-member count -- `custom-roles`
is excluded from the cross-check list above specifically because the two conventions disagree
for that one store, not because either this script or that store's write-up is wrong. Script
(uses this pack's own reference implementation, `load_fga_assertions`, for the `checks` half,
and the same file-selection rule as "Selecting the source test file" above):

```
$ uv run python3 - <<'EOF'
import pathlib, statistics, sys
sys.path.insert(0, "src")
import yaml
from migration_harness.fga_store import load_fga_assertions

ROOT = pathlib.Path("corpus/sample-stores/stores")
MANUAL = {"modeling-guide": "step-10-fine-grained-api-access.fga.yaml"}

def select(src):
    if (src / "store.fga.yaml").is_file():
        return src / "store.fga.yaml"
    cands = sorted(src.glob("*.fga.yaml"))
    return cands[0] if len(cands) == 1 else None

fracs = []
for d in sorted(ROOT.iterdir()):
    f = d / MANUAL[d.name] if d.name in MANUAL else select(d)
    doc = yaml.safe_load(f.read_text()) or {}
    checks = len(load_fga_assertions(f))
    lo = lu = 0
    for t in doc.get("tests") or []:
        for e in t.get("list_objects") or []: lo += len(e.get("assertions") or {})
        for e in t.get("list_users")   or []: lu += len(e.get("assertions") or {})
    fracs.append((checks / (checks + lo + lu), d.name))
fracs.sort()
print("n =", len(fracs))
print("median =", round(statistics.median(f for f, _ in fracs) * 100, 1))
print("min =", round(fracs[0][0] * 100, 1), fracs[0][1])
EOF
n = 39
median = 83.3
min = 33.3 gdrive
```

Median coverage across the 39-store corpus is **83.3%** -- a majority of stores lose some
fraction of their source oracle to this gap -- and `gdrive` is the thinnest at **33.3%** (3
of its 9 total assertions are `check:`-derived; the other 6 are `list_objects`/`list_users`
entries this generator cannot convert). Neither figure is close to 100%; treat "the harness
compared N/N" as coverage of the *convertible* fraction only, never as coverage of the
store's whole oracle.

## Running the reference implementation

`generate_validation(store_path, idmap, schema_ref) -> str` (the reference implementation
cited throughout this file) takes a source `.fga.yaml` path, a loaded `IdMap`, and the
`schemaFile:` value to embed, and returns validation-YAML text implementing every rule above.
It is not shipped with this plugin (`SKILL.md`, "The parity harness is not part of this
plugin") -- reproducing it means cloning `authzed/authzed-marketplace` and running it from
`tools/migration-harness/`, which requires `uv`, Python 3.12, `zed`, and the `fga` CLI. Every
example above that shows its output was produced by calling it directly against the named
corpus store and validated with `zed validate --fail-on-warn`.

The sibling parity CLI (`uv run migration-harness --store <fga.yaml> --converted
<validation.yaml> --map <migration-map.json>`) is the dev-time verification tool this file's
worked examples run against, not a step a pack user performs. Exit codes: `0` parity OK (at
least one assertion compared, none disagreed), `1` parity failure (including "zero assertions
compared", which proves nothing), `2` `zed validate` failed against `--converted`, `3` a
harness input error (bad file, malformed YAML/JSON, a `migration-map.json` that merges two
names).

## Worked example

`openfga/sample-stores/stores/github` -- shown here from the test-conversion side.
`schema-mapping.md`'s own "Worked example" section demonstrates the identical relation-split
and userset-subject mechanics, using a repository-hosting model of its own, that this real
store's `organization.member` and `repo.{admin,reader,writer}` relations also exercise.

Source `tuples:` (9 entries, all at the document root -- no nested `tests:`-block tuples in
this store) and one representative `check:` entry:

```yaml
tuples:
  - {user: organization:openfga, relation: owner, object: repo:openfga/openfga}
  - {user: "organization:openfga#member", relation: repo_admin, object: organization:openfga}
  - {user: user:erik, relation: member, object: organization:openfga}
  - {user: "team:openfga/core#member", relation: admin, object: repo:openfga/openfga}
  - {user: user:anne, relation: reader, object: repo:openfga/openfga}
  - {user: user:beth, relation: writer, object: repo:openfga/openfga}
  - {user: user:charles, relation: member, object: team:openfga/core}
  - {user: "team:openfga/backend#member", relation: member, object: team:openfga/core}
  - {user: user:diane, relation: member, object: team:openfga/backend}
```

Converted (`corpus-runs/github/validation.yaml`, committed):

```yaml
schemaFile: schema.zed
relationships: |-
  repo:openfga/openfga#owner@organization:openfga
  organization:openfga#repo_admin@organization:openfga#member
  organization:openfga#member__direct@user:erik
  repo:openfga/openfga#admin__direct@team:openfga/core#member
  repo:openfga/openfga#reader__direct@user:anne
  repo:openfga/openfga#writer__direct@user:beth
  team:openfga/core#member@user:charles
  team:openfga/core#member@team:openfga/backend#member
  team:openfga/backend#member@user:diane
assertions:
  assertTrue:
    - "repo:openfga/openfga#reader@user:anne"
    - "repo:openfga/openfga#writer@user:charles"
    - "repo:openfga/openfga#admin@user:diane"
    - "repo:openfga/openfga#reader@user:erik"
  assertFalse:
    - "repo:openfga/openfga#triager@user:anne"
    - "repo:openfga/openfga#admin@user:beth"
```

Every rule in this file is visible in that output:

- `organization.member` and `repo.{admin,reader,writer}` all split (`schema-mapping.md`'s
  worked example shows why) -- every one of their **writes** carries `__direct`
  (`member__direct`, `admin__direct`, `reader__direct`, `writer__direct`); every one of their
  **checks** (`assertTrue`/`assertFalse`) and every **subject-side userset reference**
  (`@organization:openfga#member`) stays unsuffixed.
- `organization.owner`, `repo.owner`, and `team.member` never split (pure type lists, no
  operator) -- their tuples write under their own bare name (`repo:...#owner@organization:...`,
  `team:...#member@user:...`) with nothing to get wrong.
- No `check.context`, no tuple `condition:`, and no nested `tests:`-block tuples -- besides
  the split, this store exercises none of *those* constructs, which is why it is small enough
  to show in full here.
- It **does** carry `list_objects`/`list_users`, so this example is not a demonstration of
  their absence. Derived mechanically -- 3 blocks, 4 entries, 4 relation keys:

  ```
  $ python3 -c "
  import yaml
  doc = yaml.safe_load(open('corpus/sample-stores/stores/github/store.fga.yaml'))
  b = e = k = 0
  for t in doc.get('tests') or []:
      for key in ('list_objects', 'list_users'):
          entries = t.get(key) or []
          if entries: b += 1
          e += len(entries)
          k += sum(len(entry.get('assertions') or {}) for entry in entries)
  print('blocks', b, 'entries', e, 'relation-keys', k)
  "
  blocks 3 entries 4 relation-keys 4
  ```

  Those 4 are precisely the gap in this store's 6/10 coverage fraction (10 − 6 = 4) cited
  under "Coverage cost" above.
- **The committed file quoted above predates the advisory feature and therefore does not
  show it.** Running the current `generate_validation` on this store prepends a 4-line
  header (one context line, then one `#   - ` line per block -- `findings-report.md`'s
  "Inline markers", "(b) Generated-file header manifest") that the committed text does not
  contain, and emits the `relationships:` lines sorted rather than in source order. Both
  are generator changes the frozen corpus has not been regenerated against -- read the quote
  above as the committed artifact, not as this generator's current output:

  ```
  # NOTE(spicedbmigration): list_objects/list_users block(s) below have no validation-YAML equivalent and were not converted -- review the source .fga.yaml store directly:
  #   - test "Test who are readers of the openfga/openfga repo": list_users (1 entries)
  #   - test "Test which repos can Diane read": list_objects (1 entries)
  #   - test "Check if the right users have access to the right repositories": list_users (2 entries)
  ```

Verified: this file passes `zed validate --fail-on-warn` unedited -- `Success! - 9
relationships loaded, 6 assertions run, 0 expected relations validated`.

## Deliberately not written yet

Known gaps, held open on purpose until a corpus store forces the rule, matching this pack's
existing convention (`schema-mapping.md`'s own closing section, spec decision D11).

- **A working, offline `validation:`-block generator.** "Two corrections..." above
  established that `zed`'s `validation:` block needs a per-subject resolution path, which
  `list_users.assertions.users` never records and no `zed` CLI flag computes offline. A
  generator that deploys the schema+relationships to a live/testing server first and walks
  `Expand`/`LookupSubjects` to reconstruct the path per subject is a plausible future
  extension, but it changes this generator's shape (offline text transform → requires a live
  server) and has not been attempted.
- **Real multi-subject/multi-object fan-out.** Mechanically confirmed zero of 51 `.fga.yaml`
  files in the corpus ever populate a check entry's plural `users`/`objects` keys (see
  "Fan-out"). The algorithm above implements the full product regardless, but it has never
  been exercised end to end against a real `|users| > 1` or `|objects| > 1` fixture.
  `id_encoding.mode != "none"` inside a test-conversion context is in the same position: zero
  of 39 committed `migration-map.json` files use anything but `"none"`
  (`for f in corpus-runs/*/migration-map.json; do ...; done` → no non-`"none"` mode found),
  so the interaction between `_check_object_id`'s grammar check and a real base64url-encoded
  ID inside a `.fga.yaml` fixture is untested by this corpus. `naming-normalization.md`'s
  "One codec, two consumers" still applies unchanged: whichever codec phase 3 uses, test
  conversion must use the identical one.
- **A generator-side fix for the same-object/different-relation collision (mechanism B).**
  There is no schema-independent way to resolve it automatically -- deciding whether a
  downstream permission's answer actually depends on which relation is present requires the
  schema, which this generator does not consult. The per-scenario file-split resolution above
  is the answer, and it is a human/agent step, not something a future version of this
  generator can silently do instead.
