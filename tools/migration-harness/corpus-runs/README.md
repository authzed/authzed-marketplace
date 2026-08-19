# Corpus runs

One directory per `openfga/sample-stores` store, each holding the phase-1 output for that
store (`schema.zed`, `validation.yaml`, `migration-map.json`) and this file recording what
the run found.

The corpus itself is gitignored (`corpus/`); re-fetch it with `./fetch-corpus.sh`.

## The ritual

```bash
# 1. baseline -- the source must be green before converting anything
cd corpus/sample-stores/stores/<store> && fga model test --tests store.fga.yaml

# 2. convert by following the pack, into corpus-runs/<store>/

# 3. parity
cd tools/migration-harness
uv run migration-harness \
  --store corpus/sample-stores/stores/<store>/store.fga.yaml \
  --converted corpus-runs/<store>/validation.yaml \
  --map corpus-runs/<store>/migration-map.json
```

Exit codes: `0` clean · `1` parity failure · `2` `zed validate` failed · `3` harness input
error (a misconfigured run, not a wrong conversion).

### Two traps that cost time

- **`schemaFile:` is resolved relative to the validation file, but `zed` also requires the
  resolved path to be *under the directory the command was invoked from*.** Running the
  harness from `tools/migration-harness/` works; pointing `--converted` at a validation
  file outside that tree fails with `schema filepath ... must be local to where the command
  was invoked` and surfaces as **exit 2**, which reads like a conversion failure and is not
  one. Keep converted artifacts inside `corpus-runs/`.
- **A green `zed validate` is not parity, and a green harness run is not full coverage.**
  Verify a suspiciously clean store with negative controls (flip one expectation → expect
  exit 2; delete one assertion → expect exit 1 `MISSING`) before believing it.
- **A converted schema using `import` across multiple files can pass `zed validate` while
  being permanently undeployable -- but only if the validation YAML is co-located with the
  multi-file source, which this pack's own layout avoids.** `zed validate` resolves bare
  `import`/`partial` client-side; a live `WriteSchema` never accepts `import` in any form.
  Pointing `schemaFile:` at the raw multi-file root from a validation YAML **in the same
  directory** gives a false `Success!`; pointing this pack's actual `validation.yaml`
  (kept in a different directory from the multi-file source) at that same root fails loudly
  instead (`failed to read import in schema file`, harness exit 2) -- so the danger is real
  but narrow, not universal. Note `partial` alone is not part of this trap: a single-file
  schema using only `partial` (`use partial`, no `import`) deploys directly via
  `WriteSchema` with no compile step at all. The only correct target for `schemaFile:` /
  `--converted` in this pack's layout is still the file that comes *out* of
  `zed schema compile`, never the multi-file source that goes in. See `schema-mapping.md`'s
  "Modular models" section ("`partial` and `import` need opposite syntax..." and "Three
  ways to ship a modular schema") for the full verified matrix.

### Known harness gaps

The harness was closed during the 11-store hardening loop -- gaps were recorded here rather
than patched, so a rule change and a tool change could never be confused. It was reopened
once, in the final fix wave, to close the gaps that let a *green* run be wrong; those are
marked **fixed** below. The 11 conversions were re-run afterward with identical outcomes
(10 × exit 0, `abac-with-rebac` × exit 1 on gap 3).

1. `list_objects` / `list_users` blocks are dropped. **Partly fixed.** Nothing surfaces
   them as advisory findings and nothing converts them, so the operator must still read the
   source store to learn how much of its oracle is invisible -- this file's "What the
   harness could not see" sections carry that count per store. What changed: the run no
   longer *hides* the loss. Every run prints the number of assertions actually compared,
   and a run that compares **zero** -- a store whose oracle is entirely `list_*`, or one
   with an empty `tests:` -- now fails with `NOTHING COMPARED` instead of exiting 0 on
   vacuous set arithmetic. Across these 11 stores the harness sees 97 of 117 source
   assertions (82.9%), lowest on `ip-based-access` (50%).
   `load_fga_assertions`'s docstring also claimed these blocks had "no SpiceDB validation
   YAML equivalent"; that was wrong for `list_users`, whose expected subject set maps to a
   validation YAML `validation:` block. The docstring now says what is true.
2. Content-validation errors raised a raw traceback instead of the clean exit-3 path.
   **Fixed.** Every content-validation site (`_effective`, `parse_object_ref`,
   `parse_assertion_string`, `IdMap.load`) now raises `model.InputError`, which `cli.main`
   catches and reports as exit 3. `InputError` subclasses `ValueError` but is caught
   *specifically*: a plain `ValueError` from anywhere else is a harness bug and still
   surfaces as a traceback rather than being relabelled an operator error.
2b. `--converted` was stat'd only after `zed validate` ran, so a typo'd path returned 2
   (conversion is wrong) instead of 3 (operator error). **Fixed** -- all three inputs are
   stat'd before `zed` is invoked.
2c. `IdMap.load` did no collision checking, so a map merging two source names onto one
   SpiceDB name reported `PARITY OK`: both source assertions rewrote to one key and
   `_dedupe` collapsed them as a duplicate. **Fixed** -- `load` now rejects a
   non-injective map (globally for `types`, per source type for `permissions`) as an
   input error. `IdMap.build` already guaranteed this by construction, but `build` has no
   production caller: the `/spicedb-dev:migrate-schema` agent has no Bash tool, so every
   map that reaches a real migration is written by hand and enters through `load`.
3. `load_fga_assertions` never reads `tuples:` at all -- top-level or per-`tests:`-block --
   only `check:` blocks. A `.fga.yaml` file whose `tests:` blocks each attach a *different*
   relation to the *same* object to represent mutually exclusive real-world scenarios (e.g.
   a document that is either `draft` or `published`, never both) therefore flattens into an
   assertion set that disagrees with itself on any check whose answer depends on which
   scenario is active. `parity._dedupe`'s own ambiguity detection (built for a different,
   same-triple collision -- see `condition-data-types` below) correctly fires on this too,
   but there is no conversion that resolves it: the colliding keys are excluded from
   comparison **before** the SpiceDB side is ever consulted, so the whole-store harness run
   reports `AMBIGUOUS` and exits 1 regardless of how correct the converted schema and data
   are. Confirmed on `abac-with-rebac` (see its section below): a demonstrably fully-correct
   conversion still produces exactly two `AMBIGUOUS` lines and zero
   `MISSING`/`EXTRA`/`CONTRADICTION`. Workaround: split the colliding `tests:` blocks into
   separate derived `--store` files and run the harness once per scenario (each reaches
   `PARITY OK` independently) -- see `schema-mapping.md`'s "Multiple isolated test fixtures
   colliding in one converted graph".

---

## The canonical store table

**This table is the single source of truth for every cross-store comparison in this file.**
Seven false comparative claims shipped across this corpus-hardening loop, every one of them
falsifiable by opening a sibling store's committed artifacts. The common cause was
architectural, not careless: each store's conversion agent saw its own store in full and the
other ten only through this file's narrative prose, so "verification" repeatedly meant
re-reading a list against itself, which cannot detect an omission. Two separate fix rounds
reproduced the defect while fixing it — the most recent shipped a false ordinal in the very
sentence correcting one.

**The standing rule this table exists to enforce:**

1. **Derive, never recall.** Every per-store comparative claim below must derive from this
   table (or re-run the command that built the relevant column), never from another
   section's prose and never from a recalled set of predecessors.
2. **Any rank needs a stated sort, not just a membership set.** Membership derivation was
   already mechanized when the last false ordinal shipped; the *ranking* was eyeballed. This
   covers every rank claim, not only chronological ones — "the Nth store to X", "the first
   to X", "the thinnest/deepest/largest X" all qualify. Each must state its metric, the
   command, that command's full output, and the list sorted by the stated key. Satisfy it by
   citing one of the **derived sets** below, which carry exactly those four things; add a new
   derived set rather than inlining a fresh derivation per store.
3. **Label iteration numbers as iteration numbers.** Write `iteration 3`, not `` `modular`(3) ``
   — a bare parenthesized number is ambiguous notation that helped hide the last error.
   Within a single enumeration you may drop to the bare form *after* the first member has
   established it: "`github` (iteration 1), `custom-roles` (4), `temporal-access` (6)" is
   fine. A bare number as the **first or only** reference to a store is not.
4. **Implicit membership is a comparative claim.** "unlike X", "joins X and Y", "along with",
   "as in", "already", "also", "every store since X" have the identical failure mode as an
   explicit ordinal and must be checked against this table the same way.
5. **Know what this table cannot check: predicates about other sections' prose.** Every
   column here is a property of a committed *artifact*. A claim whose predicate is instead a
   property of another store's **write-up** — "no prior store's section *reports* this kind
   of gap", "every prior section left its gap open", "this is the first section to
   *describe* X" — has no column here and cannot be mechanized from committed files. This is
   not hypothetical: the iteration 11 consolidation pass fixed a false claim of exactly this
   shape (`abac-with-rebac`, below) and its *replacement* was false again for the same
   reason, on a correctly-enumerated four-store set — the membership was right, the property
   attributed to each member was a claim about their prose that nobody had read. Either
   avoid such claims, or verify them by reading every section named, one at a time, and say
   in the text that that is what was done. Do not assume a green table covers them.

### The table

| Store | Iteration | Split | Arrows | `__perm` aliases | Permissions | `use` flags | Check-only source | Class B gate |
|---|---|---|---|---|---|---|---|---|
| `github` | 1 | split | 3 | 3 | 9 | none | no | — |
| `condition-data-types` | 2 | no split | 0 | 0 | 0 | none | **yes** | — |
| `modular` | 3 | split | 2 | 0 | 5 | none in `schema.zed`; `use partial` in `schema-use-partial.zed` | **yes** | — |
| `custom-roles` | 4 | split | 7 | 0 | 17 | none | no | — |
| `abac-with-rebac` | 5 | no split | 4 | 1 | 5 | none | **yes** | — |
| `temporal-access` | 6 | no split | 0 | 0 | 0 | none in `schema.zed`; `use expiration` in `schema-native-expiration.zed` | no | **gate 1** |
| `multitenant-rbac` | 7 | split | 2 | 0 | 13 | none | no | — |
| `ip-based-access` | 8 | no split | 1 | 1 | 2 | none | no | **gate 2** |
| `advanced-entitlements` | 9 | no split | 0 | 0 | 0 | none | no | **gate 3** |
| `superadmin` | 10 | split | 5 | 2 | 8 | none in `schema.zed`; `use expiration` in `schema-native-expiration.zed` | no | applies gate 1 unchanged |
| `groups-resource-attributes` | 11 | no split | 1 | 1 | 2 | none | **yes** | **gate 4** |
| `accounting` | 12 | split | 18 | 2 | 28 | none | no | — |
| `ads` | 12 | split | 16 | 3 | 27 | none | no | — |
| `applicant-tracking-system` | 12 | split | 25 | 5 | 36 | none | no | — |
| `banking` | 12 | no split | 1 | 1 | 2 | none | **yes** | applies gate 3 unchanged |
| `calendar` | 13 | split | 14 | 3 | 28 | none | no | — |
| `call-center` | 13 | split | 11 | 3 | 21 | none | no | — |
| `chat` | 13 | split | 5 | 1 | 16 | none | no | — |
| `crm` | 13 | split | 15 | 2 | 29 | none | no | — |
| `entitlements` | 14 | no split | 2 | 1 | 3 | none | no | — |
| `gdrive` | 14 | split | 4 | 1 | 7 | none | no | — |
| `iot` | 14 | no split | 0 | 0 | 3 | none | no | — |
| `slack` | 14 | split | 0 | 0 | 3 | none | no | — |
| `expenses` | 15 | no split | 2 | 0 | 2 | none | no | — |
| `healthcare` | 15 | split | 13 | 4 | 28 | none | no | — |
| `modeling-guide` | 15 | split | 6 | 2 | 11 | none | **yes** | applies gate 1 unchanged |
| `role-assignments` | 15 | no split | 6 | 3 | 7 | none | **yes** | — |
| `file-storage` | 16 | split | 10 | 3 | 25 | none | no | — |
| `issue-tracking` | 16 | split | 17 | 4 | 31 | none | no | — |
| `kms` | 16 | split | 8 | 3 | 21 | none | no | — |
| `payment` | 16 | split | 14 | 3 | 19 | none | no | — |
| `developer-portal` | 17 | split | 5 | 3 | 20 | none | no | — |
| `ecommerce` | 17 | split | 17 | 3 | 31 | none | no | — |
| `hospitality` | 17 | split | 18 | 5 | 31 | none | no | — |
| `human-resources` | 17 | split | 22 | 5 | 39 | none | no | — |
| `knowledge-base` | 18 | split | 9 | 1 | 19 | none | no | — |
| `lms` | 18 | split | 16 | 5 | 32 | none | no | — |
| `manufacturing` | 18 | split | 21 | 5 | 35 | none | no | — |
| `real-estate` | 18 | split | 17 | 4 | 28 | none | no | — |

### Column definitions and the commands that produce them

Every column is derived from committed artifacts only — never from this file's own prose, and
never from a source checkout. All commands are run from `tools/migration-harness/corpus-runs/`.

- **Iteration** — the ordinal position of the commit that first added that store's
  `schema.zed`, from `git log --reverse`. This is the sort key for every chronological
  ordinal in this file. Starting with batch 1, a single commit can add more than one store's
  `schema.zed` at once (the corpus-hardening loop switched from one-store-per-iteration to
  batches of independent stores processed and committed together) — `accounting`, `ads`,
  `applicant-tracking-system`, and `banking` all share **iteration 12** for exactly this
  reason, mechanically true to this column's own definition (one commit, one ordinal
  position, `git log --reverse` cannot distinguish an order among files added in the same
  commit). Do not infer a chronological order among same-iteration stores; where this file
  states one, it is processing/listing order only, stated as such.
- **Split** — `split` iff `schema.zed` contains at least one `__direct` relation.
  Command: `grep -L '__direct' */schema.zed` lists the no-split stores.
  Variant `schema-*.zed` files do not affect membership; only `schema.zed` counts.
- **Arrows** — count of `->` in `schema.zed`, comment lines excluded.
- **`__perm` aliases** — count of `permission <name>__perm` *declarations* in `schema.zed`,
  comment lines excluded. Note this counts declared aliases, not raw token occurrences: an
  alias appears twice (once declared, once used in the arrow), so `github`'s 3 declared
  aliases show up as 6 raw `__perm` tokens. Use the declared count; state which you mean.
- **Permissions** — count of `permission ` lines in `schema.zed`, comment lines excluded.
- **`use` flags** — `grep -n '^\s*use ' */*.zed`. Every store's canonical `schema.zed` uses
  zero `use` flags; the only three in the corpus are in non-canonical variant artifacts.
- **Check-only source** — the *source* store carries no `list_objects`/`list_users` test, so
  the harness's known gap #1 has nothing to hide. Derived from the source store, not from the
  converted `validation.yaml` (which has no such construct at all, so grepping it always
  returns zero and proves nothing):
  `grep -rlE 'list_objects|list_users' ../corpus/sample-stores/stores/<store>/`
- **Class B gate** — whether this store's iteration produced a Class B encoding gate decision
  in `schema-mapping.md`, and its number in the sequence.

Reproducing the four numeric columns in one pass:

```bash
$ for f in */schema.zed; do d=${f%/schema.zed}; nc=$(grep -v '^[[:space:]]*//' "$f");
    sp=$(printf '%s' "$nc" | grep -q '__direct' && echo split || echo no-split)
    ar=$(printf '%s' "$nc" | grep -o -- '->' | wc -l | tr -d ' ')
    al=$(printf '%s' "$nc" | grep -cE '^[[:space:]]*permission[[:space:]]+[A-Za-z0-9_]*__perm')
    pm=$(printf '%s' "$nc" | grep -cE '^[[:space:]]*permission[[:space:]]')
    printf '%-27s %-8s %6s %6s %5s\n' "$d" "$sp" "$ar" "$al" "$pm"; done
abac-with-rebac             no-split      4      1     5
accounting                  split        18      2    28
ads                         split        16      3    27
advanced-entitlements       no-split      0      0     0
applicant-tracking-system   split        25      5    36
banking                     no-split      1      1     2
calendar                    split        14      3    28
call-center                 split        11      3    21
chat                        split         5      1    16
condition-data-types        no-split      0      0     0
crm                         split        15      2    29
custom-roles                split         7      0    17
developer-portal            split         5      3    20
ecommerce                   split        17      3    31
entitlements                no-split      2      1     3
expenses                    no-split      2      0     2
file-storage                split        10      3    25
gdrive                      split         4      1     7
github                      split         3      3     9
groups-resource-attributes  no-split      1      1     2
healthcare                  split        13      4    28
hospitality                 split        18      5    31
human-resources             split        22      5    39
iot                         no-split      0      0     3
ip-based-access             no-split      1      1     2
issue-tracking              split        17      4    31
kms                         split         8      3    21
knowledge-base              split         9      1    19
lms                         split        16      5    32
manufacturing               split        21      5    35
modeling-guide              split         6      2    11
modular                     split         2      0     5
multitenant-rbac            split         2      0    13
payment                     split        14      3    19
real-estate                 split        17      4    28
role-assignments            no-split      6      3     7
slack                       split         0      0     3
superadmin                  split         5      2     8
temporal-access             no-split      0      0     0
```

Batch 1 (iteration 12: `accounting`, `ads`, `applicant-tracking-system`, `banking`) added the
four new rows above in the same pass -- command and full output above, not narrated separately
per store. (Corrected: a prior draft of this line said "iterations 12-15", contradicting both
the Iteration column above, which places all four at iteration 12, and that column's own
definition, which states a single commit -- and therefore a single ordinal -- can add more than
one store's `schema.zed` at once. Entered in commit `951dea7`; fixed in the batch-3 pass.)

Batch 2 (iteration 13: `calendar`, `call-center`, `chat`, `crm`) added the four new rows above
in the same pass — command and full output above, not narrated separately per store.

Batch 3 (iteration 14: `entitlements`, `gdrive`, `iot`, `slack`) added the four new rows above
in the same pass — command and full output above, not narrated separately per store. `slack`'s
0 arrows and `iot`'s 0 arrows are not zero-permission stores (3 permissions each) -- both reach
their permissions entirely through same-type unions and foreign-type userset subjects
(`workspace#member`, `device_group#it_admin`), never through a `->` arrow at all, which is why
Arrows and Permissions must be read as independent columns, not one derived from the other (see
`accounting`'s own 18-arrows-but-1-hop-of-depth case in **Arrow-chain hop depth** below for the
same point from the opposite direction).

Batch 4 (iteration 15: `expenses`, `healthcare`, `modeling-guide`, `role-assignments`) added
the four new rows above in the same pass — command and full output above, not narrated
separately per store. `modeling-guide` converts only `step-10-fine-grained-api-access.fga.yaml`
of the ten cumulative step files under `corpus/sample-stores/stores/modeling-guide/` (no
`store.fga.yaml` exists there at all — see that store's own section for why step 10 is the
representative one); its numeric columns describe that one file, not the other nine.

Batch 5 (iteration 16: `file-storage`, `issue-tracking`, `kms`, `payment`) added the four new
rows above in the same pass — command and full output above, not narrated separately per
store. All four ship a separate `model.fga` file (the second of the three source-model shapes
this pack's verified facts describe), none an inline `model:` block or `fga.mod`.

Batch 6 (iteration 17: `developer-portal`, `ecommerce`, `hospitality`, `human-resources`)
added the four new rows above in the same pass — command and full output above, not narrated
separately per store. `developer-portal` ships an inline `model:` block in `store.fga.yaml`
(the first of the three source-model shapes this pack's verified facts describe); `ecommerce`,
`hospitality`, and `human-resources` each ship a separate `model.fga` file (the second shape),
matching all four of batch 5's stores.

Batch 7 (iteration 18: `knowledge-base`, `lms`, `manufacturing`, `real-estate`) added the four
new rows above in the same pass — command and full output above, not narrated separately per
store. This is the final batch: all 39 stores in `openfga/sample-stores` are now converted. All
four ship a separate `model.fga` file (the second of the three source-model shapes this pack's
verified facts describe), matching all four of batch 5's and three of batch 6's stores.

### The derived sets every ordinal in this file ranks against

Each set below states its metric, its command, that command's full output, and the membership
**sorted by iteration number** — the sort every chronological ordinal in this file uses.

**No-split lineage** — metric: `schema.zed` with zero `__direct` relations anywhere.

```
$ grep -L '__direct' */schema.zed
abac-with-rebac/schema.zed
advanced-entitlements/schema.zed
banking/schema.zed
condition-data-types/schema.zed
entitlements/schema.zed
expenses/schema.zed
groups-resource-attributes/schema.zed
iot/schema.zed
ip-based-access/schema.zed
role-assignments/schema.zed
temporal-access/schema.zed
```

Sorted by iteration: `condition-data-types` (iteration 2, rank 1), `abac-with-rebac`
(iteration 5, rank 2), `temporal-access` (iteration 6, rank 3), `ip-based-access`
(iteration 8, rank 4), `advanced-entitlements` (iteration 9, rank 5),
`groups-resource-attributes` (iteration 11, rank 6), `banking` (iteration 12, rank 7),
`entitlements` (iteration 14, rank 8), `iot` (iteration 14, rank 8 -- tied with
`entitlements`, both iteration 14; no ordinal between same-iteration members), `expenses`
(iteration 15, rank 9), `role-assignments` (iteration 15, rank 9 -- tied with `expenses`,
both iteration 15).
**Eleven stores** (updated by batch 4, which added `expenses` and `role-assignments`;
`healthcare` and `modeling-guide` both split and do not join this set). **Unchanged by batch
5** — re-running the same `grep -L` above over all 31 committed stores returns the identical
eleven files; none of `file-storage`, `issue-tracking`, `kms`, or `payment` join (all four
split, per the canonical table above). **Unchanged by batch 6** — re-running the same
`grep -L` above over all 35 committed stores still returns the identical eleven files; none of
`developer-portal`, `ecommerce`, `hospitality`, or `human-resources` join (all four split, per
the canonical table above). **Unchanged by batch 7, the corpus's final batch** — re-running the
same `grep -L` above over all 39 committed stores still returns the identical eleven files; none
of `knowledge-base`, `lms`, `manufacturing`, or `real-estate` join (all four split, per the
canonical table above). This set's final membership, across the complete 39-store corpus, is
therefore the same eleven stores batch 4 last updated.

**No-alias-needed set** — metric: at least one arrow (`->`) *and* zero declared `__perm`
aliases. Read off the Arrows and `__perm` aliases columns above: `modular` (iteration 3,
rank 1), `custom-roles` (iteration 4, rank 2), `multitenant-rbac` (iteration 7, rank 3),
`expenses` (iteration 15, rank 4). **Four stores, updated by batch 4** — `expenses` has 2
arrows (`manager->can_manage`, `submitter->can_manage`) and zero declared `__perm` aliases,
because both arrows already land on a target that resolved to a permission before either
arrow is reached (`can_manage` carries no type list at all, so it is a plain permission from
the split rule's own final bullet; the same-name recursive arrow then targets that permission
directly, needing no alias either); none of
`healthcare`, `modeling-guide`, or `role-assignments` qualify (4, 2, and 3 declared aliases
respectively). `entitlements` and `gdrive` each have arrows but also one declared `__perm`
alias apiece, so neither qualifies; `iot` and `slack` have zero arrows, so the metric's first
conjunct excludes them outright. `github` is the counterexample — 3 arrows, 3 declared
aliases (6 raw `__perm` tokens), one alias per arrow. Sorted instead by arrow count
descending: `custom-roles` (7 arrows), `modular` (2 arrows), `multitenant-rbac` (2 arrows),
`expenses` (2 arrows) — `custom-roles` ranks first on that key and is the only member above 2
arrows; `modular`, `multitenant-rbac`, and `expenses` tie at 2. (Those parenthesised numbers
are arrow counts, not iteration numbers; per rule 3, a bare number means whatever the
enumeration's labelled first member established, so a change of key re-labels.) **Unchanged
by batch 5** — none of `file-storage` (3 declared aliases), `issue-tracking` (4), `kms` (3),
or `payment` (3) has zero declared `__perm` aliases, so none qualify regardless of arrow
count. **Unchanged by batch 6** — none of `developer-portal` (3 declared aliases),
`ecommerce` (3), `hospitality` (5), or `human-resources` (5) has zero declared `__perm`
aliases either, so none qualify regardless of arrow count. **Unchanged by batch 7, the corpus's
final batch** — none of `knowledge-base` (1 declared alias), `lms` (5), `manufacturing` (5), or
`real-estate` (4) has zero declared `__perm` aliases, so none qualify regardless of arrow count.
This set's final membership, across the complete 39-store corpus, remains the four stores named
above.

**Zero-permissions set** — metric: zero `permission` lines in `schema.zed`. Sorted by
iteration: `condition-data-types` (iteration 2, rank 1), `temporal-access` (iteration 6,
rank 2), `advanced-entitlements` (iteration 9, rank 3). **Three stores, unchanged by batch
3 or batch 4** — `entitlements` (3 permissions), `gdrive` (7), `iot` (3), `slack` (3),
`expenses` (2), `healthcare` (28), `modeling-guide` (11), and `role-assignments` (7) all
declare at least one `permission` line. **Unchanged by batch 5** — `file-storage` (25),
`issue-tracking` (31), `kms` (21), and `payment` (19) all declare many. **Unchanged by
batch 6** — `developer-portal` (20), `ecommerce` (31), `hospitality` (31), and
`human-resources` (39) all declare many. **Unchanged by batch 7, the corpus's final batch** —
`knowledge-base` (19), `lms` (32), `manufacturing` (35), and `real-estate` (28) all declare
many. This set's final membership, across the complete 39-store corpus, remains the three
stores named above.

**Check-only sources** — metric: the source store has no `list_objects`/`list_users` test.
Sorted by iteration: `condition-data-types` (iteration 2), `modular` (iteration 3),
`abac-with-rebac` (iteration 5), `groups-resource-attributes` (iteration 11), `banking`
(iteration 12), `modeling-guide` (iteration 15), `role-assignments` (iteration 15). **Seven
stores, updated by batch 4** — `modeling-guide` (converting only
`step-10-fine-grained-api-access.fga.yaml`, which carries no `list_objects`/`list_users`
block) and `role-assignments` (whose single `tests:` entry is a bare `check:` list) both join;
`expenses` and `healthcare` do not (both carry `list_objects` and `list_users` tests in
source, confirmed by `fga model test`). Unchanged by batch 2 or batch 3: none of `calendar`,
`call-center`, `chat`, `crm`, `entitlements`, `gdrive`, `iot`, or `slack` are check-only
(all eight carry both `list_objects` and `list_users` tests in their source, confirmed by
`fga model test`). The complementary twenty — `github`, `custom-roles`, `temporal-access`,
`multitenant-rbac`, `ip-based-access`, `advanced-entitlements`, `superadmin`, `accounting`,
`ads`, `applicant-tracking-system`, `calendar`, `call-center`, `chat`, `crm`, `entitlements`,
`gdrive`, `iot`, `slack`, `expenses`, `healthcare` — each carry a `list_objects`/`list_users`
gap. Of those twenty, `github` (iteration 1) is the only one that left its gap open; the
other nineteen (including all three batch-1 stores, all four batch-2 stores, all four batch-3
stores, and both check-gap-carrying batch-4 stores) closed it by direct live-server
verification. **Unchanged by batch 5 as a membership question, updated as a gap-closure
count** — none of `file-storage`, `issue-tracking`, `kms`, or `payment` is check-only (all
four carry both `list_objects` and `list_users` tests in source, confirmed by `fga model
test`, so none join the seven above), which grows the complementary set to twenty-four; all
four batch-5 stores closed their gap by direct live-server verification, so `github` remains
the only store in the whole 31-store corpus that left its gap open. **Unchanged by batch 6 as
a membership question, updated as a gap-closure count** — none of `developer-portal`,
`ecommerce`, `hospitality`, or `human-resources` is check-only (all four carry both
`list_objects` and `list_users` tests in source, confirmed by `fga model test`, so none join
the seven above), which grows the complementary set to twenty-eight; all four batch-6 stores
closed their gap by direct live-server verification, so `github` remains the only store in the
whole 35-store corpus that left its gap open. **Unchanged by batch 7 as a membership question,
updated as a gap-closure count — this is the corpus's final state** — none of `knowledge-base`,
`lms`, `manufacturing`, or `real-estate` is check-only (all four carry both `list_objects` and
`list_users` tests in source, confirmed by `fga model test`, so none join the seven above),
which grows the complementary set to thirty-two; all four batch-7 stores closed their gap by
direct live-server verification, so across the complete 39-store corpus `github` (iteration 1)
remains the only store that left its `list_objects`/`list_users` gap open.

**Split-context caveat family** — metric: a `caveat` declaring **two or more** parameters,
where the store's own fixtures bind some at write time and supply the rest per check. A
single-parameter caveat cannot split across the two channels at all, which is what excludes
`condition-data-types` (its two `tests:` blocks swap the *same* parameter between channels
rather than splitting *different* parameters across both).

```
$ for f in */schema.zed; do d=${f%/schema.zed};
    n=$(grep '^caveat ' "$f" | awk -F'[()]' '{print $2}' | awk -F',' 'NF>=2' | wc -l | tr -d ' ')
    t=$(grep -c '^caveat ' "$f"); [ "$t" != 0 ] && printf '%-28s multi-param=%s of %s\n' "$d" "$n" "$t"; done
advanced-entitlements        multi-param=3 of 3
banking                      multi-param=1 of 1
condition-data-types         multi-param=0 of 9
groups-resource-attributes   multi-param=1 of 1
ip-based-access              multi-param=1 of 1
modeling-guide               multi-param=1 of 1
superadmin                   multi-param=1 of 1
temporal-access              multi-param=1 of 1
```

Sorted by iteration — **seven stores**: `temporal-access` (iteration 6, rank 1),
`ip-based-access` (8, rank 2), `advanced-entitlements` (9, rank 3), `superadmin` (10,
rank 4), `groups-resource-attributes` (11, rank 5), `banking` (12, rank 6),
`modeling-guide` (15, rank 7). **Ranked instead
by distinct *domain* of the check-supplied half — still four domains**, in order of first
appearance: wall-clock time (`temporal-access`, 6), network locality (`ip-based-access`, 8),
usage counter (`advanced-entitlements`, 9), resource attribute
(`groups-resource-attributes`, 11). `superadmin` (10) re-uses `temporal-access`'s wall-clock
domain and so adds no new one; `banking` (12) re-uses `advanced-entitlements`' usage-counter
domain the same way (a request-supplied `transaction_amount` compared against a write-time-
bound `transaction_limit`, the identical `used`-vs-`quota` shape) and likewise adds no new
domain — which is why `groups-resource-attributes` is the **fifth store but still the fourth
domain**, and `banking` is the **sixth store but still the fourth domain**. `modeling-guide`
(15) re-uses `temporal-access`'s wall-clock domain a second time, the same way `superadmin`
did, and is therefore the **seventh store but still the fourth domain**. Any claim in this
family must say which of the two keys it ranks on. `banking`'s own caveat carries a third
parameter (`new_transaction_limit_approved`, an override also supplied per check) with no
prior-corpus analogue in this family — recorded as a variation in parameter *count* within
the existing domain, not a new domain, since the underlying shape (one write-time-bound
value compared against one or more check-supplied values) is unchanged. **Unchanged by
batch 5** — `grep -c '^caveat ' */schema.zed` returns 0 for all four of `file-storage`,
`issue-tracking`, `kms`, and `payment`; none declares a `caveat` of any kind, so none touch
this family. **Unchanged by batch 6** — the same command returns 0 for all four of
`developer-portal`, `ecommerce`, `hospitality`, and `human-resources`; none declares a
`caveat` of any kind, so none touch this family (none of the four exercises a Class B gate
either, per the canonical table above). **Unchanged by batch 7, the corpus's final batch** —
the same command returns 0 for all four of `knowledge-base`, `lms`, `manufacturing`, and
`real-estate`; none declares a `caveat` of any kind, so none touch this family or any Class B
gate. Across the complete 39-store corpus this set's final membership remains the seven stores
named above, and no fifth gate was ever forced.

Unchanged by batch 3: none of `entitlements`, `gdrive`, `iot`, or `slack` declare a `caveat`
at all (`grep -c '^caveat ' */schema.zed` returns 0 for all four) — despite its name,
`entitlements` (batch 3) does not touch this family or gate 3's usage-counter domain; its
`organization` → `plan` → `feature` shape is ordinary nested-arrow ReBAC with no condition
anywhere, corpus-confirmed distinct from `advanced-entitlements` (iteration 9), which is the
store that actually forced gate 3. Updated by batch 4: `modeling-guide` joins this family (see
above); none of `expenses`, `healthcare`, or `role-assignments` declare a `caveat` at all
(`grep -c '^caveat ' */schema.zed` returns 0 for all three).

**Harness-visible fraction** — metric: `Checks / (Checks + ListObjects + ListUsers)` as
reported by `fga model test --tests store.fga.yaml` in each source store, counting each list
test as one. (Per-store sections sometimes expand a list test into its expected members
instead — e.g. `custom-roles` reports 9/15 that way. Both orderings agree on the minimum;
state which you mean.)

```
$ for s in <all 11>; do (cd $s && fga model test --tests store.fga.yaml); done   # Checks/LO/LU
github  6/6, 1/1, 3/3      condition-data-types 18/18      modular 5/5
custom-roles 9/9, 1/1, 1/1 abac-with-rebac 12/12           temporal-access 4/4, 1/1, 2/2
multitenant-rbac 12/12, 1/1 ip-based-access 2/2, 2/2       advanced-entitlements 16/16, 3/3
superadmin 8/8, 3/3, 2/2   groups-resource-attributes 5/5

$ for s in accounting ads applicant-tracking-system banking; do (cd $s && fga model test --tests store.fga.yaml); done   # Checks/LO/LU, batch 1
accounting 51/51, 12/12, 4/4   ads 84/84, 10/10, 6/6
applicant-tracking-system 43/43, 13/13, 6/6   banking 5/5

$ for s in calendar call-center chat crm; do (cd $s && fga model test --tests store.fga.yaml); done   # Checks/LO/LU, batch 2
calendar 71/71, 9/9, 4/4   call-center 46/46, 6/6, 2/2
chat 42/42, 5/5, 3/3       crm 42/42, 8/8, 4/4

$ for s in entitlements gdrive iot slack; do (cd $s && fga model test --tests store.fga.yaml); done   # Checks/LO/LU, batch 3
entitlements 9/9, 1/1, 1/1   gdrive 3/3, 1/1, 5/5
iot 4/4, 1/1, 1/1            slack 6/6, 1/1, 1/1

$ for s in expenses healthcare modeling-guide role-assignments; do (cd $s && fga model test --tests <fixture>); done   # Checks/LO/LU, batch 4
expenses 3/3, 1/1, 1/1        healthcare 101/101, 6/6, 6/6
modeling-guide 30/30          role-assignments 8/8

$ for s in file-storage issue-tracking kms payment; do (cd $s && fga model test --tests store.fga.yaml); done   # Checks/LO/LU, batch 5
file-storage 20/20, 5/5, 5/5   issue-tracking 34/34, 9/9, 3/3
kms 48/48, 14/14, 3/3          payment 68/68, 7/7, 3/3

$ for s in developer-portal ecommerce hospitality human-resources; do (cd $s && fga model test --tests store.fga.yaml); done   # Checks/LO/LU, batch 6
developer-portal 10/10, 1/1, 1/1   ecommerce 83/83, 7/7, 4/4
hospitality 109/109, 5/5, 3/3      human-resources 54/54, 7/7, 3/3

$ for s in knowledge-base lms manufacturing real-estate; do (cd $s && fga model test --tests store.fga.yaml); done   # Checks/LO/LU, batch 7
knowledge-base 33/33, 12/12, 4/4   lms 84/84, 30/30, 12/12
manufacturing 136/136, 40/40, 15/15   real-estate 112/112, 7/7, 3/3
```

(`modeling-guide`'s fixture is `step-10-fine-grained-api-access.fga.yaml`, not
`store.fga.yaml` — this store has no `store.fga.yaml`, see its own section.)

Sorted ascending: **`gdrive` 3/9 (33.3%, rank 1 — the thinnest, unchanged by batch 4 and
batch 5)**,
`ip-based-access` 2/4 (50%), `temporal-access` 4/7 (57%),
`expenses` 3/5 (60%) = `github` 6/10 (60%, exact fraction tie), `superadmin` 8/13 (62%),
`iot` 4/6 (66.7%) = `file-storage` 20/30 (66.7%) = `lms` 84/126 (66.7%, three-way exact
fraction tie — all reduce to 2/3), `knowledge-base` 33/49 (67.3%, not an exact fraction tie
with the 2/3 trio despite the close rounded display — `33×3=99` vs `49×2=98`),
`applicant-tracking-system` 43/62 (69.4%), `manufacturing` 136/191 (71.2%), `kms` 48/65
(73.8%), `issue-tracking` 34/46
(73.9%, not an exact fraction tie with `kms` despite the rounded display — `48×46=2208` vs
`34×65=2210`), `slack` 6/8 (75%), `accounting` 51/67 (76.1%),
`crm` 42/54 (77.8%), `custom-roles` 9/11 (82%) = `entitlements` 9/11 (82%, exact fraction
tie), `developer-portal` 10/12 (83.3%), `ads` 84/100 (84.0%) = `chat` 42/50 (84.0%),
`advanced-entitlements` 16/19 (84.2%), `human-resources` 54/64 (84.4%, not an exact fraction
tie with `advanced-entitlements` despite the close rounded display — `54×19=1026` vs
`16×64=1024`), `calendar` 71/84 (84.5%), `call-center` 46/54 (85.2%), `payment` 68/78 (87.2%),
`ecommerce` 83/94 (88.3%), `healthcare` 101/113 (89.4%), `real-estate` 112/122 (91.8%, not an
exact fraction tie with `multitenant-rbac` despite the close rounded display — `112×13=1456`
vs `12×122=1464`), `multitenant-rbac` 12/13 (92%),
`hospitality` 109/117 (93.2%), then
the seven check-only stores (`condition-data-types`, `modular`, `abac-with-rebac`,
`groups-resource-attributes`, `banking`, `modeling-guide`, `role-assignments`) at 100%. Batch
2 did not displace `ip-based-access` as the thinnest; batch 3 does — `gdrive`'s 5
`list_users` sub-assertions against only 3 `check:` assertions (a store deliberately weighted
toward `list_users` coverage of a wildcard-bearing relation) push it below
`ip-based-access`'s prior 50% floor. Batch 4 does not displace `gdrive`: `expenses`, its
closest new entry, ties `github` at 60% but stays well above `gdrive`'s 33.3%, and
`healthcare`'s heavy `check:` block (101 of its 113 assertions) keeps it near the dense end
of the distribution rather than the thin one. Batch 5 does not displace `gdrive` either:
`file-storage`, its closest new entry, exactly ties `iot` at 66.7% (both reduce to 2/3, `20/30`
and `4/6`), well above `gdrive`'s 33.3%; `kms` and `issue-tracking` land within 0.1 percentage
point of each other in the low-to-mid 70s, between `applicant-tracking-system` and `slack`;
`payment` — the batch's flat, `check:`-heavy fan-out — lands at 87.2%, between `call-center`
and `healthcare`, near the dense end of the distribution the same way `healthcare` did in batch
4. Batch 6 does not displace `gdrive` either: `developer-portal`, its closest new entry, lands
at 83.3%, between `entitlements`/`custom-roles` and `ads`/`chat`, well above `gdrive`'s 33.3%;
`human-resources` lands at 84.4%, edging just past `advanced-entitlements` into the gap before
`calendar`; `ecommerce` lands at 88.3%, between `payment` and `healthcare`; `hospitality` — the
batch's densest, almost entirely `check:`-driven fixture (109 of its 117 assertions) — lands at
93.2%, between `multitenant-rbac` and the seven check-only stores' 100% tier, the closest any
non-check-only store has come to that tier. Batch 7, the corpus's final batch, does not displace
`gdrive` either: `lms`, its closest new entry, exactly ties `iot` and `file-storage` at 66.7%
(all three reduce to 2/3), well above `gdrive`'s 33.3%; `knowledge-base` — the batch's dedicated
wildcard/recursion store, deliberately weighted toward `list_objects` coverage the same way
`gdrive` was toward `list_users` — lands at 67.3%, just above that 2/3 tier, not below it;
`manufacturing` lands at 71.2%, between `applicant-tracking-system` and `kms`; `real-estate` — a
`check:`-heavy fixture — lands at 91.8%, just under `multitenant-rbac`. **Across the complete
39-store corpus, `gdrive` (iteration 14) remains the thinnest at 33.3%, and `ip-based-access`
(iteration 8) remains the second-thinnest at 50% — no store in any of batches 4 through 7 came
within 16 points of `gdrive`'s floor.**

**SpiceDB intersection (`&`)** — metric: a `&` in a **permission expression**, which must be
distinguished from CEL `&&` inside caveat bodies (`grep -l '&'` alone matches both and is
misleading here).

```
$ grep -l '&' */schema.zed
banking/schema.zed
condition-data-types/schema.zed        <- CEL '&&' in caveat bodies only, not intersection
developer-portal/schema.zed
ip-based-access/schema.zed
modeling-guide/schema.zed
role-assignments/schema.zed

$ grep -nE '^\s*permission\s.*&' */schema*.zed
banking/schema.zed:23:	permission can_make_bank_transfer = ((owner + account_manager + delegate) & bank->transfer_limit_policy__perm)
developer-portal/schema.zed:39:	permission reader = (reader__direct & organization->application__perm)
developer-portal/schema.zed:42:	permission writer = (writer__direct & organization->application__perm)
ip-based-access/schema.zed:18:	permission can_view = (viewer & organization->ip_based_access_policy__perm)
modeling-guide/schema.zed:57:	permission can_view = ((viewer & published->viewer) + can_edit)
role-assignments/schema.zed:15:	permission can_view_project = (assignee & role->can_view_project__perm)
role-assignments/schema.zed:16:	permission can_edit_project = (assignee & role->can_edit_project__perm)
```

**Five stores** (updated by batch 6), sorted by iteration: `ip-based-access` (iteration 8,
rank 1), `banking` (iteration 12, rank 2), `modeling-guide` (iteration 15, rank 3),
`role-assignments` (iteration 15, rank 3 -- tied with `modeling-guide`, both iteration 15),
`developer-portal` (iteration 17, rank 4).
`banking` is the corpus's first to nest a multi-term union
group as one operand of an intersection (`(owner + account_manager + delegate) & ...`,
three unioned relations on the intersection's left); `ip-based-access`'s own intersection has
two atomic operands, no nested union on either side. `role-assignments` has the same
two-atomic-operand shape as `ip-based-access`, on two separate permissions rather than one.
`modeling-guide` nests the opposite direction from `banking`: its `&` group is itself one
operand **inside** a union (`(viewer & published->viewer) + can_edit`), rather than a union
being nested inside the `&` the way `banking`'s left operand is — the corpus's first
intersection-inside-a-union, as opposed to `banking`'s union-inside-an-intersection (derived
directly from the `grep -nE` output above: `modeling-guide`'s line has `&` inside one `+`
operand's parentheses; `banking`'s has `+` inside one `&` operand's parentheses). Unchanged by
batch 3: none of `entitlements`, `gdrive`, `iot`, or `slack` contain a `&` of any kind.
Updated by batch 4: `modeling-guide` and `role-assignments` both join (see above); neither
`expenses` nor `healthcare` contains a `&` of any kind (re-run of `grep -l '&' */schema.zed`
above shows exactly the five files listed, none of which are `expenses` or `healthcare`).
Unchanged by batch 5: a re-run of the same `grep -l '&' */schema.zed` over all 31 committed
stores returns the identical five files; none of `file-storage`, `issue-tracking`, `kms`, or
`payment` contains a `&` of any kind. Updated by batch 6: `developer-portal` joins (see the
`grep -nE` output above); neither `ecommerce`, `hospitality`, nor `human-resources` contains a
`&` of any kind (`grep -l '&' */schema.zed` over all 35 committed stores returns exactly the
six files above, none of which are those three). `developer-portal` is a new shape within this
family: on both its `&`-bearing permissions, one operand is the split's own `__direct` relation
itself (`reader__direct & ...`, `writer__direct & ...`) -- the OpenFGA source fuses the type
list directly with `and` in the same `define` (`define reader: [application] and application
from organization`), rather than referencing an already-declared relation or permission from a
separate `define` the way all four prior intersection stores do. Checked directly: none of
`banking`'s `owner`/`account_manager`/`delegate`, `ip-based-access`'s `viewer`,
`modeling-guide`'s `viewer`, or `role-assignments`' `assignee` is declared with its own type
list fused to `and` in the same `define` as the `&` that uses it (each is a separate, earlier
`define` the intersection later references) -- `developer-portal` is the corpus's first to
combine the split rule and intersection inside one `define`, though both rules already cover
it independently (the split rule's "any operator" wording, corpus-verified in "The
relation/permission split," names `and` as one of the fusing operators without qualification)
and the composition raised no new question, hence no Finding. **Unchanged by batch 7, the
corpus's final batch** — none of `knowledge-base`, `lms`, `manufacturing`, or `real-estate`
contains a `&` of any kind (`grep -l '&' */schema.zed` over all 39 committed stores returns
exactly the same six files as batch 6, of which five are true intersection stores and
`condition-data-types` is the CEL-`&&`-only false positive noted at the top of this entry).
Across the complete 39-store corpus this set's final membership remains the five stores named
above.

**Arrow-chain hop depth** — metric: the longest chain of `->` crossings needed to fully
resolve any permission in `schema.zed` down to a base (non-permission) relation. An
`X->Y` arrow is one hop: it crosses to `X`'s target type and resolves permission `Y`
there, recursively. A bare same-type reference to another permission is zero hops (no
type boundary crossed) but still recurses into that permission's own dependencies; a bare
reference to a relation terminates the recursion at its current depth. This is a property
of the whole dependency graph, not of the raw `->` token count already in the canonical
table's **Arrows** column (`accounting` has 18 arrows but a max chain depth of only 1 --
many parallel single-hop arrows, no chaining -- which is why hop depth needs its own
column and cannot be read off Arrows).

```
$ python3 - <<'EOF'
import re, os
def parse(path):
    text = '\n'.join(l for l in open(path) if not l.strip().startswith('//'))
    defs = {}
    for m in re.finditer(r'definition\s+(\w+)\s*\{', text):
        i = m.end(); depth = 1
        while depth: depth += {'{':1,'}':-1}.get(text[i], 0); i += 1
        body = text[m.end():i-1]
        rel = {r: [t.split('with')[0].strip().replace(':*','').split('#')[0]
                    for t in ty.split('|')]
               for r, ty in re.findall(r'relation\s+(\w+)\s*:\s*([^\n]+)', body)}
        perm = dict(re.findall(r'permission\s+(\w+)\s*=\s*([^\n]+)', body))
        defs[m.group(1)] = (rel, perm)
    return defs
def depth(defs, t, n, memo, seen):
    if (t, n) in memo: return memo[(t, n)]
    if (t, n) in seen or t not in defs: return 0
    rel, perm = defs[t]
    if n not in perm: return 0
    seen.add((t, n)); best = 0
    for tok in re.findall(r'\w+(?:->\w+)?', perm[n]):
        if '->' in tok:
            l, r = tok.split('->')
            for tt in rel.get(l, []):
                best = max(best, 1 + depth(defs, tt.strip(), r, memo, seen))
        elif tok in perm:
            best = max(best, depth(defs, t, tok, memo, seen))
    seen.discard((t, n)); memo[(t, n)] = best; return best
for d in sorted(os.listdir('.')):
    p = f'{d}/schema.zed'
    if not os.path.isfile(p): continue
    defs = parse(p); memo = {}; best = (0, None)
    for t, (rel, perm) in defs.items():
        for n in perm:
            hd = depth(defs, t, n, memo, set())
            if hd > best[0]: best = (hd, f'{t}.{n}')
    print(f'{d:30s} max_hops={best[0]:2d}  ({best[1]})')
EOF
abac-with-rebac                max_hops= 2  (document.can_view)
accounting                     max_hops= 1  (account.can_edit)
ads                            max_hops= 3  (ad0.can_delete)
advanced-entitlements          max_hops= 0  (None)
applicant-tracking-system      max_hops= 5  (scorecard.can_view)
banking                        max_hops= 1  (account.can_make_bank_transfer)
calendar                       max_hops= 3  (recording.can_delete)
call-center                    max_hops= 2  (comment.can_delete)
chat                           max_hops= 2  (message.can_view)
condition-data-types           max_hops= 0  (None)
crm                            max_hops= 2  (contact.can_delete)
custom-roles                   max_hops= 2  (asset.comment)
developer-portal               max_hops= 1  (application.writer)
ecommerce                      max_hops= 3  (review.can_delete)
entitlements                   max_hops= 2  (feature.can_access)
expenses                       max_hops= 2  (report.approver)
file-storage                   max_hops= 3  (file.reader)
gdrive                         max_hops= 2  (doc.can_read)
github                         max_hops= 1  (repo.admin)
groups-resource-attributes     max_hops= 1  (document.can_access)
healthcare                     max_hops= 3  (treatment.can_delete)
hospitality                    max_hops= 2  (room.can_delete)
human-resources                max_hops= 3  (employment.can_edit)
iot                            max_hops= 0  (None)
ip-based-access                max_hops= 1  (document.can_view)
issue-tracking                 max_hops= 3  (comment.can_delete)
kms                            max_hops= 3  (comment.can_delete)
knowledge-base                 max_hops= 3  (attachment.can_view)
lms                            max_hops= 3  (activity.can_delete)
manufacturing                  max_hops= 2  (machine.org_engineer)
modeling-guide                 max_hops= 3  (document.can_edit)
modular                        max_hops= 1  (space.can_view_pages)
multitenant-rbac               max_hops= 1  (document.can_delete)
payment                        max_hops= 1  (link.can_delete)
real-estate                    max_hops= 2  (transaction.can_delete)
role-assignments               max_hops= 2  (project.can_view)
slack                          max_hops= 0  (None)
superadmin                     max_hops= 3  (task.editor)
temporal-access                max_hops= 0  (None)
```

Sorted by hop depth descending, ties broken by iteration ascending: **`applicant-tracking-
system` (iteration 12) is the corpus's deepest at 5 hops** (`scorecard.can_view` ->
`scheduled_interview.organization_admin` -> `application.organization_admin` ->
`job.organization_admin` -> `department.organization_admin` -> `organization.admin__perm`
-> `admin`), ahead of a three-way tie at 3 hops between `ads` (iteration 12), `calendar`
(iteration 13), and `superadmin` (iteration 10). No store exceeds 5. This corrects a false
"four arrow hops" / "four-hop" claim that shipped in this store's own section and was then
cited by `calendar`'s section (see both stores' write-ups); the true deepest single path
through this store is `offer.can_approve`, at 4 hops, but `scorecard.can_view` -- which
arrows *into* `scheduled_interview.organization_admin` before that permission's own 4-hop
walk begins -- was omitted from the original count, undercounting the store's actual
maximum by one hop.

Unchanged by batch 3: `entitlements` (`feature.can_access` -> `plan.subscriber_member` ->
`organization.member__perm`) and `gdrive` (`doc.can_read` -> `folder.viewer` ->
`folder.viewer__direct`) both land at 2 hops; `iot` and `slack` have zero arrows anywhere
and sit at 0.

Unchanged by batch 4: `healthcare` (`treatment.can_delete` -> `encounter.can_delete`, then
via `encounter.org_admin` -- a same-type, zero-hop reference from `can_delete` -- ->
`patient.org_admin` -> `organization.admin__perm`, three arrow crossings total:
`treatment->encounter`, `encounter->patient`, `patient->organization`) and `modeling-guide`
(`document.can_edit` -> `folder.can_edit` -> `organization.can_edit_documents`, then via
`organization.admin` -- again a same-type, zero-hop reference -- -> `system.super_admin__perm`,
three arrow crossings total: `document->folder`, `folder->organization`,
`organization->system`) both land at 3 hops -- inside the existing 3-hop
tier (`ads`, `calendar`, `superadmin`), not past it; `expenses` (`report.approver` ->
`employee.can_manage`, one hop, then one more hop into `employee.can_manage`'s own recursive
`manager->can_manage` arrow before the script's cycle breaker stops the walk) and
`role-assignments`
(`project.can_view` -> `role_assignment.can_view_project` -> `role.can_view_project__perm`)
both land at 2 hops. `applicant-tracking-system` remains the corpus's deepest at 5 across all
27 committed stores. Note `expenses`' own recursive `employee.can_manage` is a case where this
metric understates the construct's real behavior: the script's cycle-breaker treats a
permission's self-referential back-edge as contributing zero further depth once revisited, so
it reports 2 hops for `report.approver`, not the unbounded walk the construct actually
performs -- verified live, the store's own 3-link manager chain (`daniel` -> `matt` -> `sam`
-> `emily`) resolves all three ancestors through recursion (see this store's own section). This
metric answers "longest static chain in the schema text," not "deepest a recursive permission
can resolve against live data," and the two questions have different answers for any
recursive-arrow store.

Updated by batch 5: `file-storage` (`file.reader` -> `folder->reader` -> `folder.reader` ->
`parent->reader`, landing on the `drive` branch of the multi-type tupleset -> `drive.reader` ->
`organization->member`, three arrow crossings total), `issue-tracking` (`comment.can_delete` ->
`ticket->organization_admin` -> `ticket.organization_admin` -> `collection->organization_admin`
-> `collection.organization_admin` -> `organization->admin__perm`, three arrow crossings
total), and `kms` (`comment.can_delete` -> `page->organization_admin` ->
`page.organization_admin` -> `space->organization_admin` -> `space.organization_admin` ->
`organization->admin__perm`, three arrow crossings total) all land at 3 hops -- inside the
existing 3-hop tier (`ads`, `calendar`, `superadmin`, `healthcare`, `modeling-guide`), not past
it; `payment` (`link.can_delete` -> `organization->admin__perm`, one arrow crossing, the same
shape all fourteen of its arrows share) lands at 1 hop, inside the existing 1-hop tier
(`accounting`, `banking`, `github`, `groups-resource-attributes`, `ip-based-access`, `modular`,
`multitenant-rbac`). `applicant-tracking-system` remains the corpus's deepest at 5 across all
31 committed stores. `file-storage` carries the same metric-understatement caveat `expenses`
does: `folder.reader`/`.writer`/`.owner`/`.organization_admin` each fold a `parent->` arrow of
their own name into their own definition (the same-name recursive-arrow shape, see this
store's own section), and the script's cycle-breaker caps how many times that recursive arm
can be walked within one DFS path -- so `file.reader`'s reported 3 hops is the deepest
*acyclic* path the script's own algorithm finds through this schema's text, not a bound on how
many times a live chain of nested `folder` objects can actually recurse. Verified live (see
this store's own section, corrected in batch 6): a subject reaching `folder:deep-folder` (a
fixture-absent third level of folder nesting) who carries no other grant resolves
`organization_admin` — and therefore `can_delete`, which unions it with `owner` — through
**four** arrow crossings (`folder->folder->folder->drive->organization`), one more than this
metric's static count for that permission. Checking `can_delete` directly against `user:alice`
instead resolves via the shorter 3-crossing `owner` branch, since she is also
`drive:shared-drive`'s direct owner (`--explain` shows only that child evaluated, the
`organization_admin` child never appearing) — the two subjects reach the same `true` verdict
through different branches, which is why observing the fourth crossing requires isolating
`organization_admin` rather than checking `can_delete` on a subject who also satisfies `owner`.
A fourth level of nesting would add a fifth crossing to either branch with no bound encoded
anywhere in the schema.

Updated by batch 6: `ecommerce` (`review.can_delete` -> `product->can_delete` ->
`product.can_delete` -> `store->organization_admin` -> `store.organization_admin` ->
`organization->admin__perm`, three arrow crossings total) and `human-resources`
(`employment.can_edit` -> `employee->can_edit` -> `employee.can_edit`, then via
`employee.can_terminate` -- a same-type, zero-hop reference -- ->
`employee.company_can_manage_employees` -> `company->can_manage_employees` ->
`company.can_manage_employees` -> `organization->can_manage_employees`, three arrow crossings
total: `employment->employee`, `employee->company`, `company->organization`) both land at 3
hops -- inside the existing 3-hop tier, not past it; `hospitality` (`room.can_delete` ->
`hotel->can_delete` -> `hotel.can_delete`, then via `hotel.org_admin` -- a same-type, zero-hop
reference -- -> `organization->admin__perm`, two arrow crossings total) lands at 2 hops, inside
the existing 2-hop tier; `developer-portal` (`application.writer` ->
`organization->admin__perm`, one arrow crossing) lands at 1 hop, inside the existing 1-hop
tier. `applicant-tracking-system` remains the corpus's deepest at 5 across all 35 committed
stores. `human-resources`' own single-hop self-referential arrow on `team` (`team.can_view`'s
`parent_team->member__perm`, tupleset type `team` equals the arrow's own definition) does
**not** join the "same-name recursive arrow" lineage (`gdrive`, `expenses`, `modeling-guide`,
`file-storage`, `issue-tracking` — five stores, not six: `abac-with-rebac` is not a member
either, corrected here, since by this same test its own two arrows also target
differently-named permissions -- `can_edit` arrows to `owner_email_verified`, `can_view` to
`viewer_email_verified`, never to a permission sharing the source permission's own name.
`abac-with-rebac` established the *general* self-referential-arrow rule this lineage and
`human-resources`'s own construct both draw on, but it was never itself a same-name instance —
this store's own "Supplementary probe" bullet below cites the identical five-store list): the
arrow's target name (`member__perm`)
differs from the permission being defined (`can_view`), so it is the plainer base case ("A
self-referential arrow... needs no special rule") rather than the extended same-name
subsection -- checked directly against the OpenFGA source (`team.can_view: member or member
from parent_team or can_manage`, no permission ever arrows into its own name here) before
writing this distinction, precisely to avoid conflating the two tracked shapes.

Updated by batch 7, the corpus's final batch: `knowledge-base` (`attachment.can_view` ->
`article->can_view` -> `article.can_view`, then via `article.can_edit`/`.editor` -- same-type,
zero-hop references -- -> `article.editor`'s `parent_container->editor` -> `container.editor`,
then via `container.organization_admin` -- another same-type, zero-hop reference -- ->
`container->organization`, three arrow crossings total: `attachment->article`,
`article->container`, `container->organization`) and `lms` (`activity.can_delete` ->
`class->organization_admin` -> `class.organization_admin` -> `course->organization_admin` ->
`course.organization_admin` -> `organization->admin__perm`, three arrow crossings total) both
land at 3 hops -- inside the existing 3-hop tier, not past it; `manufacturing`
(`machine.org_engineer` -> `production_line->org_engineer` -> `production_line.org_engineer`
-> `organization->engineer__perm`, two arrow crossings total) and `real-estate`
(`transaction.can_delete` -> `listing->can_delete` -> `listing.can_delete`, then via
`listing.org_admin` -- a same-type, zero-hop reference -- -> `organization->admin__perm`, two
arrow crossings total) both land at 2 hops, inside the existing 2-hop tier. **Across the
complete 39-store corpus, `applicant-tracking-system` remains the deepest at 5 hops** -- no
store in batches 5 through 7 came within two hops of it. `knowledge-base`'s own
`container.viewer`/`article.viewer` (the store's wildcard-bearing, recursive permissions --
see this store's own section) are **not** the store's deepest path: their same-name recursive
arm (`parent_container->viewer`) is capped at zero additional depth by this script's own
cycle-breaker, the identical understatement `expenses`, `file-storage`, and every other
same-name-recursive-arrow store already carries in this column (see the caveats recorded
above and below) -- `container.viewer`'s own static hop count is lower than
`attachment.can_view`'s 3, even though a live chain of nested containers resolves through
more crossings than either number shows, verified directly on this store (see its own
section's live probe).

**Class B gate decisions**, in order: `temporal-access` (iteration 6, gate 1),
`ip-based-access` (iteration 8, gate 2), `advanced-entitlements` (iteration 9, gate 3),
`groups-resource-attributes` (iteration 11, gate 4). `superadmin` (iteration 10) applies
gate 1 unchanged through nested arrows rather than declaring a new gate. All four gate
sections live in `schema-mapping.md` and share one structure (`Detection.` / `Options` /
`Record:`); see "The gate decision: detection, options, and where it's recorded" in each.
`banking` (iteration 12, batch 1) is a second re-application, not a fifth gate: its
`transaction_amount <= transaction_limit` caveat is the identical write-time-bound-quota-vs-
check-time-supplied-usage shape gate 3 already covers, so it applies gate 3 unchanged, the
same way `superadmin` applies gate 1 unchanged. No corpus store has yet forced a fifth gate.
Batch 3 does not add a re-application either: despite its name, `entitlements` (iteration
14) carries no `caveat` and no Class B shape at all (see **Split-context caveat family**
above) — none of `entitlements`, `gdrive`, `iot`, or `slack` touch any of the four gates.
Batch 4 adds one more re-application: `modeling-guide` (iteration 15) reuses gate 1
unchanged, the same way `superadmin` does, its `time_based_grant` caveat being the identical
`current_time < grant_time + grant_duration` shape applied three arrow hops from the checked
permission rather than two (see **Arrow-chain hop depth** above and this store's own
section). None of `expenses`, `healthcare`, or `role-assignments` touch any of the four
gates — none declares a `caveat` at all (see **Split-context caveat family** above). Still no
corpus store has forced a fifth gate. Batch 5 adds no re-application either: none of
`file-storage`, `issue-tracking`, `kms`, or `payment` declares a `caveat` at all (see
**Split-context caveat family** above), so none touch any of the four gates. Still no corpus
store has forced a fifth gate. Batches 6 and 7 add no re-application either, and close the
corpus: none of `developer-portal`, `ecommerce`, `hospitality`, `human-resources`,
`knowledge-base`, `lms`, `manufacturing`, or `real-estate` declares a `caveat` at all (see
**Split-context caveat family** above for the command and full per-batch confirmation).
**Across the complete 39-store corpus, the four gate decisions above (plus `banking`'s and
`superadmin`'s unchanged re-applications) remain the only ones on file, and no fifth gate was
ever forced.**

---

## `github`

**Baseline:** green — `Tests 4/4, Checks 6/6, ListObjects 1/1, ListUsers 3/3`.

**Final harness run:** `PARITY OK`, exit **0**. `zed validate`: 9 relationships loaded,
6 assertions run. Also clean under `--fail-on-warn`.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition`, incl. an empty `type user` | all four types | schema-mapping construct table |
| Direct relation with a userset type | `team.member: [user, team#member]` | construct table |
| **Self-referential userset** (`team#member` on `team`) | `team.member` | construct table (no special rule needed) |
| **Relation/permission split** | `organization.member`, and all six `repo.*` except `owner` | "The relation/permission split" |
| Pure-direct relation, no split | `organization.owner`, `repo.owner`, `organization.repo_*` | split rule, final bullet |
| Userset subject pointing at a **split permission** | `organization#member` inside `repo_admin` | "A userset subject may point at a split permission" |
| **Arrow**, operand order reversed | `repo_admin from owner` → `owner->repo_admin__perm` (×3) | "Arrows" |
| `__perm` alias for arrow targets | `repo_admin__perm`, `repo_reader__perm`, `repo_writer__perm` | "Point arrows at permissions, not relations" |
| Chained implication across 5 permissions | `admin ⊆ maintainer ⊆ writer ⊆ triager ⊆ reader` | "Watch the direction of implication" |
| Object IDs containing `/` | `repo:openfga/openfga`, `team:openfga/core` | naming-normalization object-ID charset |

**Not exercised:** wildcards (`:*` appears nowhere in this model), conditions/caveats,
modular models, contextual tuples, multiple stores, model-ID pinning. `id_encoding` is
`none` — every object ID here is already inside SpiceDB's charset, `/` included.

### Findings

**1 — A split name means two different things depending on position.**
*Classification: ambiguous guidance → worked example.*
*Changed: `references/schema-mapping.md`.*

`organization.member` splits into `relation member__direct` + `permission member`, and this
store uses the name in **both** positions: it stores `user:erik member organization:openfga`
(write path) and stores a userset subject `organization:openfga#member` (read path). The
split rule said every reference site "keeps working unchanged", which is true of every
*read* site and false of the write path. Both uniform mappings fail, each with a distinct
verified error:

```
# member -> member (identity), applied to the write path:
cannot write a relationship to permission `member` under definition `organization`

# member -> member__direct, applied to the subject side:
subjects of type `organization#member__direct` are not allowed on relation
`organization#repo_admin`
```

The consequence is that **`migration-map.json` cannot express a split.** Its `permissions`
table is one per-type map that `IdMap.apply` uses for the check surface *and* for subject
relations, both of which need the unsuffixed name — so the split name is recorded there as
identity, and the `__direct` rename lives only in `migration-plan.md`'s Relation splits
table. A phase-3 rewriter must read both. The harness cannot catch a mistake here, because
it only ever maps assertions. **Correction (iteration 2):** this was originally written as
"silent data corruption in phase 3" -- wrong phase. The write-path misapplications above
both fail loudly (verified: SpiceDB unconditionally rejects a write to a permission, and
unconditionally rejects an invalid subject-relation type), so phase 3's data rewrite either
works or stops hard. The genuinely silent failure is on the check/read path in phases 4-5: a
bare split relation is a legal check target, so consulting the wrong name there returns a
narrower answer with no error at all. Fixed in `schema-mapping.md` too.

**2 — The `__perm` arrow-alias rule is verified, and is now mechanically checkable.**
*Classification: ambiguous guidance → worked example.*
*Changed: `references/schema-mapping.md`.*

The rule existed but had no stated check, and the lint is a *warning* — plain
`zed validate` stays green on the un-aliased form, so a translator that skips the alias
looks correct. Counterfactual run: pointing the three arrows at the bare relations yields
exactly three `arrow-references-relation` warnings, and `--fail-on-warn` is now recorded as
the way to catch it.

**3 — The transitive-wildcard blocker is not settled by this store.**
*Classification: unhandled construct → rating left provisional.*
*Changed: `references/blockers.md`.*

This model contains no `:*` at all, so it cannot confirm or refute the alias option. It
does exercise the *mechanism* the option relies on, which narrows the open question:
a userset subject pointing at a permission is confirmed semantically transparent — erik's
`reader` check resolves through `repo_admin → member (permission) → member__direct` and
matches OpenFGA's own `true`. What remains unconfirmed is whether that transparency
survives a **wildcard** at the far end, which is the actual claim. **Rating stays `effort`,
provisional; the finding stays Class A.** A store with a pure-direct, wildcard-bearing
relation referenced as a userset is what settles it.

**4 — Recommended pack amendment. APPLIED in the final fix wave.**
The scoping questionnaire in `SKILL.md` (item 5) said to note `list_objects` assertions
separately as having no SpiceDB equivalent. It should say `list_objects` **and
`list_users`** — of the 4 assertions on this store the harness cannot see, 3 are
`list_users` and 1 is `list_objects`, so the questionnaire as written undercounted the
blind spot roughly fourfold. `SKILL.md` was outside the files that task was scoped to
modify, and the amendment sat unapplied for ten iterations. It is now applied, in
`openfga-to-spicedb/SKILL.md` item 5 and in `pack-contract.md` item 8, both of which also
now record that the two forms are *not* equally lost: `list_users` maps to a validation
YAML `validation:` block, while `list_objects` has no equivalent at all.

### What the harness could not see

The harness compared **6 of this store's 10 assertions (60%)**. All 6 come from the single
`tests:` entry that has a `check:` block; the other three entries use `list_users` (3
assertions) and `list_objects` (1), which the harness drops silently and by design —
SpiceDB validation YAML has no equivalent form.

Specifically unverified against SpiceDB:

- **Exhaustiveness.** Both `list_users` tests assert a *complete* subject set, which also
  asserts that nobody else qualifies. A check-only oracle cannot express that at all, so
  an over-permissive conversion is the failure mode this store is least able to catch.
- `reader` for beth, charles, and diane (the check block asserts `reader` only for anne and
  erik), and `writer` for beth, diane, and erik.
- **Userset subjects as check results** — `list_users` asserts that
  `team:openfga/backend#member` and `team:openfga/core#member` are writers on the repo.
  Nothing in the converted validation file exercises a userset as the *subject of a check*.
- `list_objects` for diane (`repo` objects she can read) — SpiceDB's `LookupResources` path
  is untested here.

Also worth noting about the visible 6: every one targets the same resource object
(`repo:openfga/openfga`), and only 4 of `repo`'s 6 permissions are asserted directly
(`reader`, `triager`, `writer`, `admin`). `maintainer` and `owner` are exercised only
transitively, and no assertion targets a `team` or `organization` resource at all.

---

## `condition-data-types`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 2/2 passing,
Checks 18/18 passing`.

**Final harness run:** `PARITY OK`, exit **0**. `zed validate`: 9 relationships loaded, 18
assertions run. Also clean under `--fail-on-warn` on both the bare schema and the full
validation file. Additionally verified end to end against a live SpiceDB v1.56.0 server
(`WriteSchema`, `WriteRelationships`, `CheckPermission` for all 18 questions) — not required
by the ritual, but done deliberately because this is the corpus's first caveat-bearing
store and `zed validate` alone was not enough to trust the caveat findings below.

This store has one type (`datatype_test`) with one relation (`is_valid`) that is a pure
type list unioning nine `user with <condition>` clauses — no split, no permission, no
arrows. Its entire content is nine `condition` blocks, one per OpenFGA CEL parameter type.
Negative-control-verified per this file's standing method: flipping one expectation to
`assertFalse` makes `zed validate` fail loudly (exit 1 from `zed`, exit 2 from the
harness); deleting one assertion line makes the harness report `MISSING` (exit 1).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition`, incl. an empty `type user` | both types | schema-mapping construct table |
| `define` with only a type list → plain relation, no split | `datatype_test.is_valid` | split rule, final bullet (confirms, no new rule needed) |
| `[user with cond]` unioned across nine conditions via `\|` | `datatype_test.is_valid` | construct table + "Line wrapping has a direction" (trailing `\|`, not leading) |
| `condition c(p: T) { ... }` → `caveat c(p T) { ... }`, all 9 OpenFGA parameter types | all nine `condition` blocks | **new: "Caveat parameter types and expression bodies"** |
| Caveat context embedded at write time vs. supplied at check time | both `tests:` blocks | SpiceDB supports both natively; see collision finding below |

**Not exercised:** wildcards, the relation/permission split, arrows, modular models,
multiple stores, model-ID pinning, contextual tuples. `id_encoding` is `none` — every
object ID here (`one`, `int`, `uint`, `double`, ...) is already inside SpiceDB's charset.

### Findings

**1 — Caveat parameter types and expression bodies, proven for all nine OpenFGA types.**
*Classification: missing conversion rule → `schema-mapping.md`.*
*Changed: `references/schema-mapping.md` (new section "Caveat parameter types and
expression bodies").*

Seven of the nine types (`string`, `int`, `double`, `duration`, `timestamp`, `map<T>`,
`list<T>`) carry their OpenFGA CEL body over unchanged. Two do not, both verified from
SpiceDB source and confirmed on a live server:

- **`uint` is CEL `int` under the hood.** `pkg/caveats/types/basic.go` registers it as
  `RegisterBasicType(ts, "uint", cel.IntType, convertNumericType[uint64])` — the parameter's
  actual CEL type is `int`, not CEL's native `uint`. `_uint != 0u` (the literal OpenFGA
  wrote) fails to compile (``found no matching overload for '_!=_' applied to
  '(int, uint)'``); `_uint != 0` compiles and is correct. Rewrite rule: strip the `u`
  suffix from every integer literal compared against a `uint` parameter, and never call
  `uint(...)` in the body.
- **`ipaddress` has no in-expression literal constructor.** `_ipaddress != ipaddress("...")`
  — legal OpenFGA CEL — fails with ``undeclared reference to 'ipaddress'``: unlike
  `duration`/`timestamp`, which map onto CEL's native types and inherit CEL's
  standard-library constructors for free, SpiceDB's `ipaddress` is a custom opaque type
  with exactly one method (`.in_cidr`) and no constructor. For the shape this store uses —
  equality against one hardcoded address — `.in_cidr("<addr>/32")` (IPv4) or `/128` (IPv6)
  is an exact substitute, verified correct on a live server for both a matching and a
  non-matching address.

Both are rated `effort`, not `clean`: each needs a body-level rewrite, not just the
declaration-shape change (`p: int` → `p int`) the pack already had. Neither is Class A —
both have a full-fidelity mechanical fix once known, so neither goes to `blockers.md`.

**2 — Caveat parameter names have no rename-absorption layer, unlike every other name in
the pack.**
*Classification: ambiguous guidance → worked example.*
*Changed: `references/schema-mapping.md`, `references/naming-normalization.md`.*

This store's nine caveat names already satisfy the strict `^[a-z][a-z0-9_]{1,62}[a-z0-9]$`
regex, so `naming-normalization.md`'s "prefer leaving alone" guidance holds without being
forced to renormalize anything — a confirmation, not a counter-example. Its nine caveat
*parameter* names, though (`_string`, `_uint`, ...), are all leading-underscore, which
raised a real question: does the codegen rule "never emit a `_`-prefixed identifier"
apply here? Verified no — that rule was proven only against relation/definition names, and
`caveat c(_uint uint) { ... }` compiles and deploys unchanged. More importantly, unlike a
relation or permission rename, **a caveat parameter rename has nothing to absorb it**:
`migration-map.json` / `IdMap.apply` rewrites relation and permission names on every
assertion, but caveat context is a raw JSON object compared verbatim by the harness (and,
downstream, by SpiceDB's context binding) — there is no field anywhere for a
parameter-name translation. Renaming one is a coupled schema/data/call-site change with no
single source of truth, unlike every other rename this pack makes. Leave them as-is.

**3 — A source with multiple isolated `tests:` fixtures can collide in one converted
graph, and the collision can silently substitute the wrong caveat.**
*Classification: ambiguous guidance → worked example (no prior guidance existed for
combining more than one `tests:` block).*
*Changed: `references/schema-mapping.md`.*

This is the store's most important finding, and the harness's green exit code does not
surface it. `fga model test` treats each `tests:` block as an isolated dataset; this
store's two blocks ("context in tuples" vs. "context in checks") both assert against
`datatype_test:one#is_valid@user:<X>` for the same nine `X`. SpiceDB has one flat
relationship graph with no such isolation, and only one relationship may exist per
`(object, relation, subject)` triple — `zed validate`'s loader rejects the naive merge
outright:

```
error: found repeated relationship `datatype_test:one#is_valid@user:uint[is_valid_int:{"_int":1}]`
```

Once forced to pick one relationship per triple (this conversion keeps the "context in
tuples" block's binding, since its checks carry no context of their own and have no other
way to pass), the *other* block's corresponding checks still return the right boolean for
the wrong reason: SpiceDB merges request-supplied context with the relationship's own
bound caveat, so a check supplying unrelated or absent context against an
already-satisfied bound caveat returns `true` without touching the caveat the check
nominally names. Verified live: `user:int`'s two blocks name genuinely different caveats
(`is_valid_uint` at write time, `is_valid_int` at check time) — `is_valid_int` ends up
referenced by **zero** relationships in the converted graph, yet

```
$ zed permission check datatype_test:one is_valid user:int --caveat-context '{"_totally_unrelated_key":999}'
true
```

still returns `true`, and both `zed validate` and the parity harness stay green throughout
(see `validation.yaml`'s `assertTrue` block, which does include this literal check). This
is not a harness bug — the harness comparator has nothing to compare against here, since
the converted graph really does answer every assertion correctly by the only measure it
has (booleans). It is a property of collapsing an isolated-per-test source into one shared
graph, and it means one of the two logical scenarios ("check-time-only context
resolution", and for `user:int` specifically, `is_valid_int` itself) has **zero** real
coverage in this converted artifact despite 18/18 assertions passing. The detection rule
and the recommended handling (flag the colliding triples, record them in
`migration-plan.md`, do not trust the harness's boolean answer for them) are now in
`schema-mapping.md`.

**4 — The `schema-mapping.md` "silent data corruption in phase 3" claim was the wrong
phase (task correction, not a new finding).**
*Classification: correction, made before conversion began per this iteration's brief.*
*Changed: `references/schema-mapping.md`, this file's `github` finding 1.*

Both write-path misapplications of the relation/permission split (identity-mapped to a
permission; `__direct`-mapped to a subject-relation) fail **loudly**, independently
verified on this machine before touching this store: SpiceDB unconditionally rejects a
write to a permission and unconditionally rejects an invalid subject-relation type. The
genuinely silent risk is on the check/read path in phases 4-5 — confirmed directly by this
store's own schema, where `zed permission check datatype_test:one is_valid user:X`
succeeds against a bare `relation` with no permission wrapping it at all, so "a bare split
relation is a legal check target" is not hypothetical. Both references now say phase 4-5,
not phase 3.

### What the harness could not see

Every one of this store's 18 assertions has a `check:` block (no `list_objects` /
`list_users` anywhere), so the harness's per-assertion coverage is **18 of 18 (100%)** —
better than `github`'s 60%. But 100% assertion coverage is not 100% *caveat* coverage: per
finding 3, `is_valid_int` is declared, compiles, deploys, and is referenced by the store's
own OpenFGA condition list — and is never once evaluated by anything in the converted
SpiceDB graph, while the harness reports full parity. The only way this surfaced was
deliberately cross-referencing which caveat names actually appear in `relationships:`
against the full set of `caveat` declarations in `schema.zed`, and confirming on a live
server that the "passing" check for `is_valid_int` doesn't even touch it. A purely
boolean, harness-only pass would have missed this completely.

---

## `modular`

**Baseline:** green, and the invocation is **identical** to a single-file store's, which is
itself worth recording since it was not obvious in advance. `fga.mod` requires `schema
1.2`, has no `model`/`schema` header of its own, and lists four `.fga` files under
`contents:` (`core.fga`, `issue-tracker/projects.fga`, `issue-tracker/tickets.fga`,
`wiki.fga`), each opening with `module <name>` instead. Despite that, this store ships a
top-level `store.fga.yaml` whose `model_file: ./fga.mod` the `fga` CLI resolves completely
transparently:

```
$ fga model test --tests store.fga.yaml
# Test Summary #
Tests 1/1 passing
Checks 5/5 passing
```

No `--model` override, no separate resolution step -- `model_file` pointing at a manifest
instead of a `.fga` file is handled identically to the single-file case by `fga model test`
itself. This store also ships three **per-module** test files, one per the store's own
`README.md` (`core.fga.yaml`, `wiki.fga.yaml`, `issue-tracker.fga.yaml`), each independently
green (`Tests 1/1 passing`, `Checks 2/2 passing`, all three).

**Final harness run:** `PARITY OK`, exit **0**, against `store.fga.yaml` (5/5 assertions,
all `check:` blocks). `zed validate`: 3 relationships loaded, 5 assertions run. Clean under
`--fail-on-warn` too. Negative-control-verified per this file's standing method: flipping
`organization:openfga#admin@user:anne` to `assertFalse` fails `zed validate` itself (exit 1
from `zed`, exit 2 from the harness, with `zed`'s own explanation trace showing the
relationship it found); deleting the `project:openfga#viewer@user:anne` assertion makes the
harness report `MISSING` (exit 1). Additionally verified end to end against live SpiceDB
servers at both **v1.54.0** and **v1.56.0** (`WriteSchema`, real relationship writes,
`CheckPermission` for all six of the model's permissions, including `can_create_project`,
which `store.fga.yaml` never asserts) -- done deliberately, because this store is the first
whose conversion path runs through a compiler step (`zed schema compile`) the harness itself
never invokes, and because the first pass at findings 2-3 below turned out to be wrong in a
way that only live-server testing (not `zed validate`, and not reading SpiceDB source)
caught. See findings 2-3.

This store is `modular`'s two types of coverage-thin-but-real: `organization` gets three
permissions checked but only ever through one subject (anne) and never a negative; `group`,
`page`, and `ticket` are declared in the model and never referenced by any assertion in any
of the four `.fga.yaml` files at all.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all seven types | schema-mapping construct table |
| Relation/permission split | `organization.member` (`[user] or admin`) | "The relation/permission split" (confirms, no new rule) |
| Pure-direct relation, no split | `organization.admin`, `group.member`, `space.organization`, `page.space`, `page.owner`, `project.organization`, `ticket.project`, `ticket.owner` | split rule, final bullet |
| Pure-alias permission, no split | `organization.can_create_space`, `organization.can_create_project` | split rule, final bullet |
| Arrow, operand order reversed | `space.can_view_pages` (`member from organization`), `project.viewer` (same) | "Arrows" |
| Arrow into an **already-split permission with the same name** | both arrows above target `organization#member`, needing **no** `__perm` alias | "Point arrows at permissions, not relations" -- confirms the "no alias when the target already split" branch, which `github` never exercised (`github`'s three arrow targets were all pure-direct, needing the alias) |
| `fga.mod` / `module` / `extend type` | the whole store | **new: "Modular models"** |
| `extend type organization` adding a **permission** | `wiki.fga`'s `can_create_space`, `issue-tracker/projects.fga`'s `can_create_project` | "Modular models" |
| A module spanning two files (`issue-tracker`) | `issue-tracker/projects.fga` + `issue-tracker/tickets.fga` | "Modular models" -- confirms a module boundary and a file boundary aren't the same thing, and the second file needs no special handling once the module's `partial`(s) are elsewhere |

**Not exercised:** `extend type` adding a **relation** rather than a permission (verified to
work in isolation during pack development, but this store's own two `extend type` blocks are
both permission-only, so the store itself does not force or confirm this shape);
condition/caveat names across modules (this store has zero caveats, so the task brief's
"condition names are globally unique across modules" fact is not exercised or re-derived
here); wildcards; multiple stores; model-ID pinning; object IDs needing normalization
(`id_encoding` is `none` -- `openfga`, `anne`, etc. are all inside SpiceDB's charset already).

### Findings

**1 — The modular-model mapping rule: `partial` + `import` + one root file, then
`zed schema compile`.**
*Classification: missing conversion rule → `schema-mapping.md`.*
*Changed: `references/schema-mapping.md` (new section "Modular models: `fga.mod`, `module`,
`extend type`"; construct table row updated from "rule not written yet" to `effort`).*

OpenFGA's `extend type T { ... }` has no direct SpiceDB syntax -- SpiceDB cannot reopen a
`definition`. The mechanical translation: each module's own `type` declarations become
ordinary `definition`s as usual; each `extend type T` block becomes a `partial` instead of a
`definition`; and one root file (the SpiceDB analogue of `fga.mod`) `import`s every module
file and assembles the extended type by spreading the base module's contribution alongside
every extending module's partial (`definition organization { ...core_organization
...wiki_organization_ext ...issuetracker_organization_ext }`). Verified end to end on a live
SpiceDB v1.56.0 server: all six of `organization`/`space`/`project`'s permissions resolve
exactly as OpenFGA resolves them, including `can_create_project`, which `store.fga.yaml`
itself never checks. Two supporting facts, both verified and both needed to trust the rule
generally rather than just for this store's specific shape: a `partial` may hold `relation`
lines as well as `permission` lines (so the rule covers a relation-shaped `extend type` too,
even though this store only exercises the permission-shaped case), and a file's own
references resolve against the *whole* compiled program reachable from the root's `import`
graph, not just what that file itself imports -- `wiki.zed`'s `permission can_create_space =
admin` never imports `core.zed`, where `admin` is declared, and still compiles, because
`manifest.zed` imports both.

**2 — `partial` and `import` need opposite syntax forms on zed v0.31.1 versus a live
`WriteSchema` -- `use partial` genuinely deploys directly; `use import` never does.**
*Classification: ambiguous guidance → worked example (this finding went through one wrong
draft, caught by review before merge; see the note at the end of this entry).*
*Changed: `references/schema-mapping.md` ("`use` flags are load-bearing" section, and the new
"Modular models" section, "Three ways to ship a modular schema").*

The decisive test is each tool's own bogus-`use`-flag enumeration:

```
$ zed schema write /tmp/bogus.zed   # live SpiceDB v1.56.0's answer
Unknown use flag: `bogusflag`. Options are: expiration, import, partial, self, typechecking

$ zed validate /tmp/bogus.zed       # zed v0.31.1's own local parser's answer
Unknown use flag: `bogusflag`. Options are: expiration
```

`WriteSchema` knows `partial`, `import`, `self`, and `typechecking` as real `use` flags;
zed's own local parser (`validate` / `schema compile`) knows only `expiration`. Verified
directly on a live server: a single-file schema opening with `use partial`, holding one
`partial` block per extending module spread into `organization`, deploys via
`zed schema write` with **no compile step at all**, and `zed schema read` afterward shows
the partials already merged. `import` is different: `use import` is rejected in the
`WriteSchema` context with a distinct, deliberate error
(`import statements are not allowed in this context`), confirming the pack's original,
pre-this-store text on that point was correct all along. Net result: **`partial` is a real,
working `WriteSchema` feature that only needs the right flag; `import` is not, in any
form.** No single file text satisfies both `zed`'s local tooling (which wants the bare,
unflagged keyword) and `WriteSchema` (which wants `use partial`) for a `partial`-bearing
schema -- that toolchain-pairing skew, not a missing grammar, is why this pack's own
artifacts still compile before deploying (see "Three ways to ship a modular schema" for the
three real options and what each costs). `corpus-runs/modular/schema-use-partial.zed` is
this option, built from the same store and independently deployed + fully re-checked on a
live server as part of this finding, kept alongside `schema.zed` as a working, documented
alternative -- not the artifact this store's harness run uses, since `zed validate` cannot
parse it.

*What went wrong the first time, left in for the next iteration's benefit:* an earlier draft
of this finding tested only the *bare* form of `partial`/`import` against the server (which
correctly fails, lacking the `use` flag it needs) and only the `use`-flagged form against
zed's local parser (which correctly fails, not recognizing that flag) -- and concluded
neither construct reaches `WriteSchema` at all. It never tried `use partial` directly
against a live server, which is the one combination that works. A SpiceDB source checkout
consulted at the time was v1.47.0, predating the v1.52.0 fold `SKILL.md`'s "Target version
floor" describes -- correctly quoted, wrongly read as still describing v1.56.0's behavior.
That line in `SKILL.md` was right; the earlier draft's conclusion from it was not, and is
not repeated here.

**3 — A green `zed validate` can certify an undeployable schema, but only from one specific,
avoidable layout mistake -- not from this pack's own canonical layout.**
*Classification: ambiguous guidance → worked example; recorded as a third "trap" in this
file's ritual section.*
*Changed: `references/schema-mapping.md` ("Modular models"), this file's "Two traps" section
(now three).*

`zed validate` -- both directly on a `.zed` file and via a validation YAML's `schemaFile:`
-- uses the same extended-syntax parser as `zed schema compile`, so it resolves bare
`import`/`partial` transparently. Pointing `schemaFile:` at the raw, uncompiled
`modules/manifest.zed` **from a validation YAML sitting in the same directory as that root**
does give a false green:

```
Success! - 3 relationships loaded, 5 assertions run, 0 expected relations validated
```

identical to what the compiled `schema.zed` gives, on a file that a live `WriteSchema`
cannot accept as-is (it uses `import` across four files, finding 2). But relative `import`
paths resolve against the *validation YAML's* directory, not the schema file's, and this
pack's own canonical layout keeps `validation.yaml` and the multi-file source in different
directories (`corpus-runs/modular/validation.yaml` next to `schema.zed`; the multi-file
source in `modules/`). Pointing that real `validation.yaml` at `modules/manifest.zed`
instead of `schema.zed` does **not** stay quietly green:

```
$ zed validate validation.yaml   # schemaFile: modules/manifest.zed
error: parse error in ``, line N, column 1: failed to read import in schema file
```

exit 1 from `zed`, **exit 2 from the harness** -- the normal, correct "this doesn't
validate" signal, not silence. The false-green case reproduces only when someone co-locates
the validation YAML with the multi-file source itself (authoring or debugging in place
inside `modules/`), which this pack's artifacts do not do. One more distinct form worth
recording: a validation YAML's *inline* `schema:` block uses a different, non-extended
compiler than `schemaFile:` does -- a bare `partial` inside an inline `schema:` block fails
immediately with the same token error `WriteSchema` gives, even though the identical text
in a `schemaFile:`-referenced `.zed` file is accepted. `schema:` and `schemaFile:` are not
interchangeable here. Either way, `corpus-runs/modular/schema.zed` (the `zed schema compile`
output of `modules/manifest.zed`) remains the only file this store's `--converted` /
`schemaFile:` should ever point at.

**4 — Provenance comments survive `zed schema compile`, but only when placed on the specific
line, not on the `partial`'s own declaration -- and a file-header comment block attaches to
the first definition, not to the file.**
*Classification: ambiguous guidance → worked example (no prior guidance existed on
preserving OpenFGA's `# module: X, file: Y` provenance through a SpiceDB compile step).*
*Changed: `references/schema-mapping.md` ("Modular models").*

OpenFGA's `fga model get` renders its combined view with a `# module: X, file: Y` comment on
every type and `# extended by: ...` on every extended relation -- SpiceDB's flattening step
has no built-in equivalent, and the task brief's framing ("provenance survives only in
metadata") suggested it might be lost entirely. Verified otherwise, using this store's own
module files: a `//` comment placed directly above an individual `relation` or `permission`
line inside a `partial` **does** survive into the merged `definition` after `zed schema
compile`, attached to that same line, even though the `partial`'s own declaration-line
comment does not. `corpus-runs/modular/modules/*.zed` carry exactly this kind of
line-level provenance comment (`// core.fga: define admin: [user] -- pure-direct, no
split`, etc.), and `zed schema compile modules/manifest.zed` reproduces every one of them,
correctly attributed, in `corpus-runs/modular/schema.zed` -- which is now literally that
compile's output, committed unedited (see finding 5). This is recorded as an available,
optional technique, not a mapping requirement -- the `modules/` source tree under version
control is the durable provenance record regardless of whether any given line is commented.

One hazard the same evidence surfaces: `core.zed`'s own multi-paragraph header comment,
written to describe the whole file, survives too -- but attached only to `definition user
{}`, the first declaration it precedes, not to the file as a whole or to `organization`
(the definition `core.zed`'s content mostly exists to feed via a `partial`). Nothing marks a
leading comment as file-level; do not rely on one to document a file's role once compiled.

**5 — `corpus-runs/modular/schema.zed` was hand-written, not the compile output it claimed
to be -- fixed by regenerating it.**
*Classification: correction, caught by review before merge, not a store-forced finding in
its own right.*
*Changed: `corpus-runs/modular/schema.zed` (regenerated); `references/schema-mapping.md`
("Always fully parenthesize", new note on compiled-artifact governance).*

An earlier commit's `schema.zed` was semantically correct (parity held, live-server checks
passed) but was authored by hand to match this pack's parenthesization and blank-line
conventions, while every piece of prose describing it -- this file, `schema-mapping.md`,
finding 4 above -- claimed it was `zed schema compile`'s literal output. It was not: the
real compile output carries all 37 comment lines from `modules/*.zed` (the hand-written
version had none, so it demonstrated finding 4 not at all), orders `organization` last
rather than second, and renders `permission member = member__direct + admin` with no
parentheses, where "Always fully parenthesize" calls for
`permission member = (member__direct + admin)`. `schema.zed` is now the literal,
unedited output of `zed schema compile modules/manifest.zed`, re-verified against a live
server after regenerating. The parenthesization gap is real and is now documented directly
in `schema-mapping.md`: that rule governs hand- or translator-authored schemas, not a file
whose entire reason to exist is being regenerable byte-for-byte from its compiled source --
for the modular pipeline's option A specifically, the compiler's own formatting governs, and
the fix is to commit what it emits rather than hand-restyle it.

### What the harness could not see

`store.fga.yaml`'s 5 assertions are **100% covered** (5/5) -- every one is a `check:` block,
no `list_objects`/`list_users` anywhere in this store at all. But that 100% is a thin slice
of what the store's four `.fga.yaml` files collectively assert (11 check-assertions total,
with overlap), and re-running the harness with `--store` pointed at each per-module file in
turn (still against the same converted `validation.yaml`) surfaces exactly one **genuine**
gap along with some expected noise worth explaining so a future iteration doesn't mistake
one for the other:

```
$ uv run migration-harness --store .../issue-tracker.fga.yaml --converted .../validation.yaml --map ...
MISSING       organization:openfga#can_create_project@user:anne expected=True
EXTRA         organization:openfga#admin@user:anne expected=True
EXTRA         organization:openfga#can_create_space@user:anne expected=True
EXTRA         space:openfga#can_view_pages@user:anne expected=True
EXTRA         organization:openfga#member@user:anne expected=True
```

The `MISSING` line is real: `can_create_project` is asserted by `issue-tracker.fga.yaml` (a
per-module test file) but not by `store.fga.yaml` (the file the ritual's `--store` points
at), so the harness's canonical run over this store never checks it at all -- confirmed
correct only by the live-server verification in finding 1, not by anything the harness
itself reports when run the ritual's usual way. The four `EXTRA` lines are **not** real
defects: they are `validation.yaml` assertions that are true and correct but fall outside
this one narrow per-module file's own three-assertion scope, and they recur, differently,
against every one of the other two per-module files for the same reason. The harness's
`compare()` has no concept of "this oracle file is deliberately partial," so pointing
`--store` at a per-module file and reusing the full `validation.yaml` produces one honest
`MISSING` wrapped in mechanical `EXTRA` noise -- useful for finding a real gap, but only if
the noise is anticipated rather than mistaken for a regression.

Beyond that: this store has **zero negative assertions** across all four `.fga.yaml` files --
every `check:` block anywhere in the store asserts `true`, and every one is for the same
subject, `user:anne`. `group`, `page`, and `ticket` are declared in the model and never
referenced by a single assertion anywhere in the source. None of this is a harness gap in
the sense the two documented ones are -- it is a property of the store itself -- but it means
the harness's `PARITY OK` here certifies five true answers for one user and is silent on
every negative case and three of the model's seven types. The live-server checks in this
section's "Baseline" and finding 1 (`user:zoe`, a subject related to nothing, correctly
`false` on both `admin` and `viewer`) are this iteration's substitute for the negative
coverage the source oracle itself never provides.

---

## `custom-roles`

**Baseline:** green -- `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 9/9 passing, ListObjects 1/1 passing, ListUsers 1/1 passing`.

This is the store this iteration was chosen for: OpenFGA's own worked example of a customer
defining roles at runtime, whose sample-store `README.md` frames it plainly ("Orgs can
create their own roles"). `migrating-to-spicedb/SKILL.md`'s Fidelity ratings section singles
this construct out as "the construct most likely to decide a real B2B deal" and warns that a
prior, independent analysis assumed it `blocked` when it was actually `heavy`. Neither
assumption survives this store: it converted clean on the **first attempt**, using only rules
already on file, and needs **zero `use` flags** -- the first corpus store for which that
*combination* holds (zero `use` flags alone is not novel here: `github` and
`condition-data-types` already have none, and `modular`'s own canonical, compiled `schema.zed`
has none either, since `use import`/`use partial` are needed only by its uncompiled
multi-file source -- what is new is reaching a first-attempt `PARITY OK`, with no new rule
required, on a `use`-flag-free schema; `github`, `condition-data-types`, and `modular` each
needed at least one fix-and-rerun cycle, per their own "Final harness run" sections lacking
"first attempt").

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 25
relationships loaded, 9 assertions run. Also clean under `--fail-on-warn` (no
`arrow-references-relation` warnings at all -- see the constructs table). Negative-control-
verified per this file's standing method: flipping `org:contoso#role_creator@user:carlos` to
`assertFalse` fails `zed validate` itself (`zed`'s own explanation trace shows exactly which
tuple it found: `org:contoso role_creator` walks through `owner@user:carlos`), exit 1 from
`zed`, exit 2 from the harness; deleting the
`asset:website-hero-image#view@user:anne` assertion makes the harness report `MISSING`
(exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because this store's whole point is a
commercial fidelity rating and the harness alone cannot certify one:

- `WriteSchema` accepted `schema.zed` unedited, with **no compile step and no `use` flag** --
  `zed schema read` afterward is byte-identical to the source modulo formatting.
- All 25 relationships loaded via `WriteRelationships`.
- All **9** check-block assertions match exactly.
- The `list_objects` test (Beth's viewable assets) and the `list_users` test (who can view
  `asset:homepage`) -- both silently dropped by the harness, see "Known harness gaps" above
  -- were independently run via `LookupResources`/`LookupSubjects` and matched the source
  oracle **exactly**, including the negative half (no extra objects, no extra subjects). This
  store's full oracle is **15/15 confirmed** (9 check + 2 list_objects + 4 list_users),
  not just the 9/15 the harness itself can see.
- The decisive test for the fidelity rating (see Finding 1): a brand-new role the fixture
  never mentions was introduced **after** the schema was deployed and granted a permission,
  with zero schema writes.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all six types | schema-mapping construct table |
| Direct relation with a 3-way userset type list | `role.assignee: [user, team#member, org#member]` | construct table |
| Pure-direct relation, no split | `team.member`, `org.owner`, `asset-category.org`, `asset.category` | split rule, final bullet |
| **Relation/permission split, pervasively** | 10 of `org`'s 11 relations, 4 of `asset-category`'s 5, 3 of `asset`'s 4 | "The relation/permission split" |
| Userset subject pointing at a **split permission** | `role.assignee`'s `org#member`; every `org->asset_*` / `category->*` arrow target | "A userset subject may point at a split permission" |
| Arrow, operand order reversed | `asset_creator from org` → `org->asset_creator` (×4), `commenter`/`editor`/`viewer from category` (×3) | "Arrows" |
| Arrow into an already-split permission — **no `__perm` alias anywhere in the store** | all 7 arrows | "Point arrows at permissions, not relations" — confirms the no-alias branch at greater scale than any prior store forced, not for the first time. See "The canonical store table" → **No-alias-needed set** for the metric (at least one arrow and zero declared `__perm` aliases), the command, and the full sorted membership: `modular` (iteration 3, rank 1), **this store (iteration 4, rank 2)**, `multitenant-rbac` (iteration 7, rank 3). `github` (iteration 1) is the counterexample, needing an alias on each of its 3 arrows. **This store is second chronologically, not third** [correction, iteration 11 consolidation pass: a prior draft ranked it third by eyeballing the ordinal off a correctly-derived membership set — the set was mechanized, the sort was not]. Sorted instead by arrow count, this store ranks first and is the set's only member above 2 arrows (7 arrows, versus 2 each for `modular` and `multitenant-rbac`) |
| **Runtime-defined ("custom") roles** | the whole store: `role` type + `[role#assignee]` unioned into 17 permissions across 3 types | **new: "Runtime-defined ('custom') roles"** |
| Type name normalization, hyphen → underscore | `type asset-category` → `definition asset_category` | naming-normalization algorithm, rule 3 — first live corpus confirmation on an actual `type` declaration (previously only a worked/synthetic example in the reference doc) |
| Object IDs containing hyphens, no encoding needed | `website-content`, `branding-contractor-1`, `media-asset-manager`, etc. | naming-normalization object-ID charset — hyphen is legal in object IDs even though illegal in definition/relation names |

**Not exercised:** wildcards (no `:*` anywhere), conditions/caveats, modular models,
contextual tuples, multiple stores, model-ID pinning, the "arrow discards the subject
relation" hazard (no tupleset relation here has a userset-typed allowed type). `id_encoding`
is `none` — every object ID is already inside SpiceDB's charset.

### Findings

**1 — Runtime-defined ("custom") roles rate `effort`, not `heavy` and not `blocked`, for a
bounded, pre-declared permission surface.**
*Classification: missing conversion rule → `schema-mapping.md` (new section), with a
cross-reference in `blockers.md` ruling the construct out as a blocker.*
*Changed: `references/schema-mapping.md` (new "Runtime-defined ('custom') roles" section,
construct table row, "Deliberately not written yet" boundary bullet); `references/blockers.md`
(new "Not a blocker" section).*

This is the finding this iteration exists to produce. `custom-roles`' model needs no new
SpiceDB construct at all: a `role` type carries one plain relation, `assignee`, and every
permission the product wants to be role-grantable already unions `[role#assignee]` into its
own type list alongside whatever OpenFGA grants by ownership or membership
(`define asset_creator: [role#assignee] or owner`). Because that fuses a type list with an
operator, **the split rule this pack already had applies unmodified** — the permission keeps
its name, a `role#assignee`-typed `__direct` relation appears, and the role's actual grant is
one relationship write onto that relation:
`asset_category:website-content#editor__direct@role:content-manager#assignee`. Assigning a
user, team, or org to a role is the same userset-subject-to-relation pattern OpenFGA already
used for everything else (`role:content-manager#assignee@team:marketing#member`). Nothing
here required inventing a rule; it is a composition of three rules already in
`schema-mapping.md` before this store was touched.

The decisive test, run live against this store's own deployed schema, is whether the schema
*grows* as the customer's role vocabulary grows — `migrating-to-spicedb/SKILL.md`'s own
definition of `heavy`. It does not:

```
$ zed permission check asset:homepage edit user:frank
false
$ zed relationship create role:senior-editor assignee user:frank
$ zed relationship create asset_category:website-content editor__direct role:senior-editor#assignee
$ zed permission check asset:homepage edit user:frank
true
```

A role the fixture never mentions, assigned to a subject who started with no access at all,
reaches a real permission with **two relationship writes and zero schema writes**. That is
the specific claim the SKILL.md's own reference case says was wrongly assumed `blocked`; here
it is confirmed neither `blocked` nor `heavy`. **Rating: `effort`** — not `clean` outright,
because the split still carries its own Class B `__direct`-suffix decision
(`migration-plan.md`), the same one every split relation already carries and nothing
role-specific.

This result has a stated boundary, written into both changed files: it holds only because
every role-grantable permission was already declared in the schema, ahead of time, as
`[role#assignee] or ...`. A role that must grant a permission or resource shape the schema
did not anticipate is a different, harder question this store's evidence does not answer —
flagged as open in `schema-mapping.md`'s "Deliberately not written yet", with
`advanced-entitlements` named as the next corpus candidate to check it against.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **9 of 9 (100%)** — matching,
not beating, every prior store, each of which also covers 100% of its own `check:` block
(`github` 6/6, `condition-data-types` 18/18, `modular` 5/5); `custom-roles` reaches it the
same way `github` does, with one `check:` entry-set and no coverage gaps within it
[correction, iteration 11 consolidation pass: this read "better than any prior store," which
is false on both available metrics — every prior store is also 100% on its `check:` block,
and on *total*-oracle coverage this store's 60% is worse than `condition-data-types`' and
`modular`'s 100%]. But the store's full oracle is 15 facts, not 9: one `list_objects` test (2
expected objects) and one `list_users` test (4 expected subjects) are silently dropped by the
harness per the known gap documented above, so the harness's own view is **9/15 (60%)** of
the store's total assertions — coincidentally the same fraction `github` reported, for the
same structural reason.

This is the first `list_objects`/`list_users` gap in the corpus to be closed by direct
verification rather than left as an acknowledged blind spot. Stated precisely, since "unlike
prior iterations" on its own is too broad to be checkable: of the three prior stores, only
`github` (iteration 1) carries a `list_objects`/`list_users` gap at all — see "The canonical
store table" → **Check-only sources** — and `github` left its gap open;
`condition-data-types` (2) and `modular` (3) are check-only sources with no such gap to
close, though `modular` did confirm its one genuine per-module-file `MISSING` live. Both of
this store's dropped tests were run live via `LookupResources`
(`asset` objects Beth can `view`) and `LookupSubjects` (`user`-typed subjects who can `view`
`asset:homepage`), and both matched the source oracle exactly — including the negative half
that a `list_objects`/`list_users` assertion carries and a `check` cannot (nobody extra
qualifies). This store's converted schema and data are therefore confirmed correct against
**100% of its own oracle**, not just the 60% the harness's `PARITY OK` alone can certify.

---

## `abac-with-rebac`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 2/2 passing,
Checks 12/12 passing`.

This store's own `README.md` frames it precisely: it is a worked demonstration of OpenFGA's
"if you can model your attribute as a relation, you should" guidance, for the two attribute
shapes that *can* be modeled without conditions — a foreign-key-style reference
(`email_verified`, walked via an arrow) and a few-valued category (`draft`/`published`,
modeled as a relation from `document` to itself). **This store contains zero `condition`
blocks** — a third attribute shape its own `README.md` explicitly says *does* need
conditions ("a discrete variable with many possible values... it's not possible to be
modeled with pure ReBAC") is described but deliberately not shown in `store.fga.yaml` itself.
The task brief that selected this store expected conditions interacting with the relation
graph; the store's actual content, verified directly (`grep -n condition
store.fga.yaml` matches nothing), is the opposite case — two ABAC shapes modeled as pure
ReBAC specifically to avoid needing them. None of the caveat-specific facts carried over from
`condition-data-types` (tuple-context-wins-on-conflict, unused-parameter rejection, caveat
name normalization) are exercised or re-derived here as a result; this section does not
force any new caveat rule.

**Final harness run.** The canonical invocation (`--store` pointed at the store's own
`store.fga.yaml`) is **exit 1**, `PARITY FAILED`, and — this is the headline finding — stays
exit 1 for *any* correct conversion, not just this one. `zed validate --fail-on-warn` on
`validation.yaml` alone is clean (`Success! - 6 relationships loaded, 6 assertions run`), and
the harness run itself shows zero `MISSING`/`EXTRA`/`CONTRADICTION`, only:

```
$ uv run migration-harness --store corpus/sample-stores/stores/abac-with-rebac/store.fga.yaml \
    --converted corpus-runs/abac-with-rebac/validation.yaml --map corpus-runs/abac-with-rebac/migration-map.json
PARITY FAILED
AMBIGUOUS     document:readme#can_view@user:anne same-side conflict: expected=False vs expected=True
AMBIGUOUS     document:readme#can_edit@user:bob same-side conflict: expected=True vs expected=False
```

This is a harness limitation (see "Known harness gaps" #3 above), not a conversion defect:
`store.fga.yaml`'s two `tests:` blocks each attach one tuple (`draft` or `published`) to the
same `document:readme` to represent two mutually exclusive real-world states of one
document, and `load_fga_assertions` flattens both blocks' `check:` assertions without ever
reading either block's `tuples:` — so it cannot tell that `can_view@anne` and
`can_edit@bob`'s two different expected answers belong to two different scenarios rather
than contradicting each other. **Proof this is harness-structural, not a property of this
conversion:** the same schema and relationships, split into two derived single-scenario
pairs (`store-draft.fga.yaml` + `validation-draft.yaml`, and `store-published.fga.yaml` +
`validation.yaml`), each independently reach `PARITY OK`, exit **0**. `validation-draft.yaml`
and `validation.yaml` are committed; `store-draft.fga.yaml` and `store-published.fga.yaml` are
each a verbatim slice of the upstream store's own two `tests:` blocks, so — like the rest of
`corpus/sample-stores/` — they are gitignored rather than committed (see
`tools/migration-harness/.gitignore`). Regenerate either locally before running the commands
below: copy `corpus/sample-stores/stores/abac-with-rebac/store.fga.yaml`'s root-level `model:`
and `tuples:` unchanged, and keep only the one `tests:` entry ("Test permissions for draft
document" or "...published document") the filename names — each derived file's own header
comment spells out the same derivation.

```
$ uv run migration-harness --store corpus-runs/abac-with-rebac/store-draft.fga.yaml \
    --converted corpus-runs/abac-with-rebac/validation-draft.yaml --map corpus-runs/abac-with-rebac/migration-map.json
PARITY OK
$ uv run migration-harness --store corpus-runs/abac-with-rebac/store-published.fga.yaml \
    --converted corpus-runs/abac-with-rebac/validation.yaml --map corpus-runs/abac-with-rebac/migration-map.json
PARITY OK
```

Negative-control-verified per this file's standing method, against the published-scenario
pair: flipping `document:readme#can_view@user:anne` to `assertFalse` fails `zed validate`
itself (exit 1 from `zed`, with `zed`'s own explanation trace showing exactly which path it
walked: `can_view` → `viewer_email_verified` via `anne`'s bound `email_verified`), exit 2
from the harness; deleting the `document:readme#can_edit@user:jeremy` assertion makes the
harness report `MISSING` (exit 1). Additionally verified end to end against a live SpiceDB
v1.56.0 server, deliberately, because this is the first corpus store with a *self-referential*
arrow (see Constructs table) and because the harness's structural blindness above means its
own `PARITY OK` can never, by construction, confirm both scenarios in one run:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 12 of the source's checks reproduced exactly by toggling one relationship
  (`document:readme#draft@document:readme` ↔ `#published@document:readme`) and re-running
  all six `can_edit`/`can_view` checks each time — matching `fga model test`'s own
  `Checks 12/12 passing` one for one.
- The exact mechanism behind the two `AMBIGUOUS` lines was reproduced directly: writing
  **both** `#draft` and `#published` on `document:readme` at once (never a real state, but a
  concrete demonstration of why merging the two scenarios into one graph is wrong, not just
  unrepresentable) flips `can_edit@bob` and `can_view@anne` both to `true` — silently
  contradicting whichever one of the two source tests is not the one currently "active":
  ```
  $ zed permission check document:readme can_edit user:bob    # both draft and published present
  true    # test 2 (published only) expects false
  $ zed permission check document:readme can_view user:anne   # both draft and published present
  true    # test 1 (draft only) expects false
  ```

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | both types | schema-mapping construct table |
| Pure-direct relation, no split (type list only, no operator) | `user.email_verified`, `document.draft`, `document.published`, `document.viewer`, `document.owner` | split rule, final bullet (confirms) |
| Pure computed permission, no split (no type list, only `from`/`or`) | `document.viewer_email_verified`, `document.owner_email_verified`, `document.can_view`, `document.can_edit` | split rule, final bullet (confirms) |
| Arrow, operand order reversed | `viewer_email_verified`/`owner_email_verified` (`X from viewer`/`owner`), `can_view`'s `... from published`, `can_edit`'s `... from draft` | "Arrows" |
| `__perm` alias for an arrow target that is a bare (unsplit) relation | `user.email_verified__perm`, needed because `email_verified` has only a type list and never splits | "Point arrows at permissions, not relations" |
| **Self-referential arrow** — tupleset relation's allowed type equals the arrow's own definition | `document.draft`/`document.published` used as tupleset relations *on* `document`, walking `document → document` | **new: "Arrows" confirmed to generalize with no special rule — see the new "A self-referential arrow..." note** |
| Arrow chained through another arrow-derived permission (nested arrow) | `can_view`'s `published->viewer_email_verified`, where `viewer_email_verified` is itself `viewer->email_verified__perm` | "Arrows" (confirms, no new rule) |
| Union of a plain permission and an arrow, one parenthesized group | `can_view = (owner_email_verified + published->viewer_email_verified)` | "Always fully parenthesize" |
| Per-test tuples encoding two mutually exclusive scenarios under the same object ID across two `tests:` blocks (the store's own comments call this "sent as a contextual tuple") | the `draft`/`published` self-loop on `document:readme` | **new: "Multiple isolated test fixtures colliding in one converted graph" → "Same object ID, different relation..."; also the corpus's first confirmed instance of `blockers.md`'s Contextual-tuples `effort` branch** |

**Not exercised:** wildcards (no `:*` anywhere), conditions/caveats (see above — deliberately
avoided by this store's own design), the relation/permission split (no `define` here mixes a
type list with an operator), modular models, userset subjects (every relation's type list is
a bare type, never `T#rel`; every stored tuple's subject is a bare object), multiple stores,
model-ID pinning. `id_encoding` is `none` — `bob`, `anne`, `jeremy`, `readme` are all already
inside SpiceDB's charset.

### Findings

**1 — A source store's per-test tuples can encode two mutually exclusive scenarios under one
object ID, and the harness's whole-store run can never certify both at once — a
conversion-independent limitation, not a defect to fix.**
*Classification: genuine harness limitation (reported, not patched) + ambiguous guidance →
worked example for the pack side.*
*Changed: `corpus-runs/README.md` ("Known harness gaps" #3, and this section);
`references/schema-mapping.md` (new "Multiple isolated test fixtures colliding in one
converted graph" section, restructured from `condition-data-types`' existing subsection into
two sibling sub-cases); `references/blockers.md` (corpus-confirmation note on the
"Contextual tuples" entry's `effort` branch).*

This is the same top-level phenomenon `condition-data-types` found (iteration 2, finding
3) — `fga model test`'s per-`tests:`-block isolation has no SpiceDB equivalent, so combining
more than one block into one converted graph can collide — but a different mechanism with a
different, more severe consequence. `condition-data-types`' collision was a **recurring
identical triple** with different caveat context, caught loudly by `zed validate`'s loader at
merge time (`found repeated relationship`), with a clear resolution (keep one binding, flag
the other check as unverified in `migration-plan.md`). This store's collision is a
**recurring object with two different relations attached across blocks** — no triple
repeats, so `zed validate` never objects to any single choice, or even to writing both at
once. The real problem surfaces one layer up, in the harness's own comparator: `parity.py`'s
`_dedupe` — evidently built to catch exactly `condition-data-types`' case — treats a same-key,
different-expected pair on the OpenFGA side as `AMBIGUOUS` and (correctly, per its own
docstring) drops that key from **both** sides before any comparison happens. Because
`load_fga_assertions` never reads `tuples:` at all (verified by reading it directly, not by
inference — `fga_store.py` only ever calls `test.get("check")`), it cannot tell that the two
conflicting expectations belong to two different, individually-consistent scenarios rather
than a genuinely self-contradictory source. The result: **no `migration-map.json`, no
`validation.yaml` content, and no schema choice changes the outcome** — the two `AMBIGUOUS`
lines are structurally guaranteed by the shape of `store.fga.yaml` itself, verified by
producing a conversion with zero `MISSING`/`EXTRA`/`CONTRADICTION` and exactly those two
lines regardless.

The practical handling, now written into `schema-mapping.md`: pick one scenario as the
canonical `validation.yaml` (documented, arbitrary but recorded — this run picks
"published"), and verify the scenario(s) the harness's canonical invocation cannot see by
running it a second time against a derived, single-`tests:`-block `--store` file — which
does reach `PARITY OK` — or by hand-verifying on a live server. Both are done here (see
"Final harness run" above); `validation-draft.yaml` is committed alongside the canonical
three files, and `store-draft.fga.yaml` is regenerable on demand from the upstream store (see
the note above) — together they keep the second harness invocation this finding depends on
reproducible, not just asserted.

This finding also connects to an existing Class A entry rather than creating a new one:
`blockers.md`'s "Contextual tuples" blocker already tells the detector to sweep `.fga.yaml`
files, on the theory that a test supplying contextual tuples is evidence of a production call
site doing the same. `abac-with-rebac`'s own comments confirm the theory directly ("This
tuple can be written to OpenFGA when the document status changes, or can be sent as a
contextual tuple") and the store is the corpus's first live confirmation that materializing
such a tuple as a real, persisted relationship (`blockers.md`'s `effort` option) is fully
correct at the SpiceDB level — the residual problem is entirely in the harness's verification
tooling, not in SpiceDB's capability to represent the underlying attribute.

**2 — A self-referential arrow (tupleset relation's allowed type equals the arrow's own
definition) needs no special rule, corpus-confirmed for the first time.**
*Classification: ambiguous guidance → worked example (the existing "Arrows" rule already
logically covers this shape; no prior store exercised it to confirm).*
*Changed: `references/schema-mapping.md` ("Arrows" section, new "A self-referential arrow...
needs no special rule" note).*

`document.can_edit = owner_email_verified from draft` walks `document → document`: the
tupleset relation (`draft`) and the arrow's own definition (`document`) are the same type.
None of `github`, `modular`, or `custom-roles` exercised this — every arrow in those three
stores crosses to a genuinely different type (`repo`→`organization`, `space`→`organization`,
`asset_category`→`org`). Nothing in the "Arrows" rule as written excludes a same-type target,
and this store confirms it needs nothing extra: `zed validate` accepts it with no warning
beyond the ordinary `__perm`-alias one (see constructs table), and all twelve of the store's
checks reproduce exactly on a live v1.56.0 server. Recorded as a confirmation of the existing
rule's generality, not a new construct — the same status `github`'s userset-subject-splits and
`modular`'s cross-file references occupy.

### What the harness could not see

Every one of this store's 12 source assertions is a `check:` block — no `list_objects` /
`list_users` anywhere — so on the surface this looks like `custom-roles`' 100% case. It is
not: of the **6 unique `(subject, permission)` questions** the store actually asks about
`document:readme` (3 subjects × 2 permissions — `context` never varies, so `resource` and
`context` are constant across all 12 checks), **2 of the 6 (33%) are structurally excluded
from any single harness invocation**, per finding 1 — not merely uncovered by this store's
own test design (as `github`'s untested `team`/`organization` resources were), but
*excluded by the harness's own comparator logic* regardless of which scenario's tuple the
converted graph carries. Across the corpus the gaps these sections report fall into **three**
kinds, distinguished by which component produces them — verified by reading all four prior
sections one at a time, since "what kind of gap does a section document" is a property of
prose and has no column in the canonical store table (see its rule 5):

1. **The loader drops whole assertion types.** `fga_store.py`'s `load_fga_assertions` never
   reads `list_objects`/`list_users` blocks at all (its own docstring says so) — `github`
   (iteration 1) and `custom-roles` (4).
2. **The source oracle's own reach falls short.** Nothing in the harness is involved; the
   store simply never asserts the thing — `condition-data-types` (iteration 2), whose `is_valid_int`
   caveat is never evaluated despite 18/18 parity, and `modular` (3), whose
   `can_create_project` is asserted only in a per-module file the canonical `--store` run
   never reads.
3. **The comparator excludes keys it did read.** `parity.py`'s `_dedupe` drops a
   same-key/different-expected pair from **both** sides before comparison — **this store,
   and only this store.**

Kind 3 is what is novel here; the gap is then **fully closed** in this run rather than left
as an acknowledged blind spot: both
[correction, iteration 11 consolidation pass, twice. The original text read "Unlike every
prior store's 'what the harness could not see' section, this gap is fully closed" — false as
a quantifier over *closure*: `custom-roles`, the immediately preceding store, closed its own
gap by direct verification and says so in those exact words. The first replacement then
claimed all four prior sections documented a gap in the *source oracle's* reach — false
again, for `custom-roles` and `github`, whose gaps are harness-manufactured too, just by the
loader rather than by `_dedupe`. The dichotomy had three members, not two. Both errors share
a cause worth naming: the predicate being attributed ("what kind of gap does that store's
section document") is a claim about **prose**, which the canonical table cannot check and
which nobody had verified by reading the sections.]
excluded questions (`can_view@anne`, `can_edit@bob`) are independently confirmed correct for
both of their scenario-dependent answers, via the two split harness runs (each `PARITY OK`)
and directly on a live server (see "Final harness run"). The lesson generalizes past this one
store: a green canonical harness run's `PARITY OK` certifies only the assertion keys that
survive `_dedupe` unambiguously, and a store whose `tests:` blocks encode scenario-dependent
data can have real coverage the canonical invocation is structurally incapable of reporting
on — checking for this pattern is now written into `schema-mapping.md`'s detection rule for
future stores.

---

## `temporal-access`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 4/4 passing,
Checks 4/4 passing, ListObjects 1/1 passing, ListUsers 2/2 passing`.

This store is one condition (`temporal_access(grant_time: timestamp, grant_duration:
duration, current_time: timestamp) { current_time < grant_time + grant_duration }`) applied
to one relation (`document.viewer: [user, user with temporal_access]`), and it is the store
this iteration was chosen for: SpiceDB can express "time-limited access" two ways — a
mechanical caveat translation, or native relationship expiration (`use expiration` / `with
expiration` / `optional_expires_at`) — and the task was to determine which this pack should
recommend. Every schema-mapping rule the caveat form needs already existed before this store
was touched (`timestamp`/`duration` parameter types from `condition-data-types`; "a bare type
list stays a plain relation" from the split rule, already confirmed against a `with`-clause
union by `condition-data-types`' own `is_valid` relation) — the caveat conversion needed zero
new rules and reached `PARITY OK` on the **first attempt**. The substance of this iteration is
entirely the encoding *choice*, not the syntax.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 3
relationships loaded, 4 assertions run. Also clean under `--fail-on-warn`. Negative-control-
verified per this file's standing method: flipping `document:1#viewer@user:bob` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the relationship it found); deleting that same
assertion makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because this store's whole point is a
recommendation between two encodings and the harness alone cannot settle it:

- `WriteSchema` accepted the caveat `schema.zed` unedited, no compile step, no `use` flag.
- All 3 relationships loaded via `WriteRelationships`, all **4** check-block assertions match
  exactly (`zed permission check ... --caveat-context '{"current_time":"..."}', `, one per
  source check).
- The `list_objects` test (documents anne can view at a fixed instant) and both `list_users`
  tests (who can view `document:1`/`document:2` at that instant) — all three silently dropped
  by the harness, see "Known harness gaps" above — were independently run via
  `LookupResources`/`LookupSubjects` and matched the source oracle **exactly**, including the
  negative half (document:2's list_users excludes bob; list_objects lists no document beyond
  1 and 2). This store's full oracle is **7/7 confirmed** (4 check + 1 list_objects +
  2 list_users), not just the 4/7 the harness itself can see.
- A parallel native-expiration schema (`use expiration`, `with expiration`,
  `corpus-runs/temporal-access/schema-native-expiration.zed`) was deployed to a separate
  keyspace and independently verified: dropping `use expiration` reproduces the documented
  trap exactly (`could not lookup caveat 'expiration' for relation 'viewer': caveat with name
  'expiration' not found`); with the flag, `WriteSchema` and `zed validate` both accept it
  directly, no compile step. This is the decisive evidence for the recommendation below.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | both types | schema-mapping construct table |
| `define viewer: [user, user with cond]` — bare type list mixing an unconditioned and a conditioned subject type | `document.viewer` | split rule, final bullet (confirms — a mixed list is still "only a type list," no split) |
| `condition c(grant_time: timestamp, grant_duration: duration, current_time: timestamp) { ... }` → `caveat c(grant_time timestamp, grant_duration duration, current_time timestamp) { ... }` | `temporal_access` | "Caveat parameter types and expression bodies" (both parameter types already covered — confirms, no new rule) |
| Caveat context split across write time (`grant_time`, `grant_duration`, bound to the relationship) and check time (`current_time`, supplied per check) | every `viewer` check on anne | SpiceDB supports both natively, merged per-check — same mechanism `condition-data-types` documented, applied here without collision (this store has exactly one `tests:` `check:` block, so no cross-block collision risk) |
| **Temporal access: caveat vs. native expiration** | the whole store's reason for being selected | **new: "Temporal access: caveat vs. native expiration"** |

**Not exercised:** wildcards, the relation/permission split (no `define` here mixes a type
list with an operator), arrows, modular models, runtime-defined roles, multiple stores,
model-ID pinning, caveat-parameter leading-underscore normalization (this store's own
parameter names — `grant_time`, `grant_duration`, `current_time` — have no leading
underscore, unlike `condition-data-types`' `_string`/`_uint`/etc., so there was nothing to
confirm or re-derive here). `id_encoding` is `none` — `bob`, `anne`, `1`, `2` are all already
inside SpiceDB's charset.

### Findings

**1 — Temporal access is a Class B gate decision between two encodings, not a silent
default; native expiration is the recommended default and caveat is the alternative for a
call site that genuinely needs "as of a time."**
*Classification: ambiguous guidance → worked example (this was an explicitly flagged open
"encoding choice," not a mapping gap — see `schema-mapping.md`'s "Deliberately not written
yet," pre-this-store text).*
*Changed: `references/schema-mapping.md` (new "Temporal access: caveat vs. native expiration"
section, construct table row, two forward-reference updates), `corpus-runs/temporal-access/`
(`schema.zed` stays the caveat form — it is the only one this pack's harness ritual can
verify; `schema-native-expiration.zed` now documents the recommended default, not a
fallback).*

**Correction (fix round 1, owner-directed, not re-litigated here).** This finding originally
shipped a single unconditional recommendation — caveat over native expiration — approved at
the time and reversed on later owner review. Two things were wrong with the original call,
both fixed below and in `schema-mapping.md`: it violated `pack-contract.md` item 3, which
requires presenting a multi-encoding choice with its tradeoffs rather than picking one
silently; and its stated justification (native expiration has no caller-suppliable "now," so
this harness can't verify it) is a fact about this pack's own verification tooling, not a
runtime tradeoff — it says nothing about which encoding a production system should run.
Native expiration's real production advantages (no per-check CEL evaluation, GC reclaims
expired rows, expiry enforced identically on all four read paths) were already verified live
in this section and are unaffected by the correction; only the recommendation drawn from the
evidence changes.

The two encodings store the identical *value* — `grant_time + grant_duration`, computed once
either way — but diverge on who supplies "now" at check time. A caveat's `current_time` is an
ordinary parameter the caller supplies as check context, exactly as this store's own
`tests:` blocks do; it can be any instant — real, historical, or a future "as of" date.
Native expiration has no caller-suppliable "now" anywhere in the API: verified from SpiceDB
source (`internal/datastore/memdb/memdb.go`'s `SnapshotReader` always filters against
`time.Now()`, the real wall clock at read time, independent of the requested revision/
ZedToken; `pkg/development/assertions.go`'s `RunCheck` — what both `zed validate` and the
developer API use — accepts only a `CaveatContext`, no time parameter) and confirmed live on
this toolchain (`zed permission check --help` has no time-override flag; `--caveat-context`
is caveat-only; `--consistency-at-exactly` pins the data snapshot, not the wall-clock instant
expiration is compared against). Every backing datastore filters expiration with its own
`now()` the same way — this is not a memdb-only property.

The consequence is exactly what the task brief for this store asked to determine: this
store's three time-varying assertions (`viewer` true 10 minutes into a 1-hour grant, false 2
hours into that grant, false 9 seconds into an unrelated 5-second grant) ask "was this valid
at caller-chosen instant X" — a question only the caveat form can answer deterministically.
Native expiration can only answer "is this valid **right now**." Reproducing this store's
exact assertions with native expiration would require either sleeping in real time between
write and check (flaky, and exactly the wall-clock dependency this pack's test-conversion
guidance says to avoid) or asserting only against dates safely in the past/future relative to
whenever the suite happens to run — a different claim than "9 seconds into a 5-second grant."
**Native-expiration semantics are not verifiable by this harness, or by `zed validate`, for
any assertion whose expected answer depends on an offset from a fixed instant rather than
from real time** — confirmed structurally (the validation-YAML grammar's only per-assertion
context channel, `spicedb_val.py`'s `" with {...}"` suffix, feeds `CaveatContext` and nothing
else) and empirically (see "Final harness run" above).

One question the task brief posed directly — what happens to a relationship whose expiry
passes, before GC runs — was answered live: a relationship written with an expiration 6
seconds in the future checked `true` and appeared in `zed relationship read` immediately
after the write; 8 seconds later (well inside SpiceDB's GC interval, so nothing had been
physically reclaimed) it checked `false` and had vanished from `zed relationship read`
entirely. This matches source: `internal/datastore/memdb/readonly.go`'s read-path iterators
drop any relationship past its `OptionalExpiration` unless the caller opts out via
`SkipExpiration`, and `internal/services/v1/relationships.go`'s `ReadRelationships` handler
sets `SkipExpiration: !traits.AllowsExpiration` — filtering is *always on*, on every read
path (`Check`, `ReadRelationships`, `LookupResources`, `LookupSubjects`), for any relation
declared `with expiration`, with no request-level flag to see the not-yet-collected row. GC
reclaims storage only; visibility is already gone the instant the timestamp passes.

**Gate decision, recorded in `schema-mapping.md`:** native expiration is this pack's
**recommended default** for a temporal-access condition carried over from an OpenFGA source —
rated `effort` (not for syntactic difficulty, it is syntactically simpler, but because
adopting it is a conscious, recordable decision to drop the source's "as of an arbitrary
time" capability), with the genuine operational advantages verified live above (no per-check
CEL evaluation; the datastore physically reclaims expired rows via GC; expiry enforced
identically on all four read paths). Caveat remains the documented **alternative** — rated
`clean`, preserves the source's check-time flexibility exactly, and is the only form this
pack's own tooling can verify — for a source model whose call sites genuinely need "as of a
time" rather than "is this valid right now." Detection, the options table, and the
`migration-plan.md` record location (`Decisions` → `Per-blocker resolutions`) are now in
`schema-mapping.md`'s "The gate decision" subsection. One thing that does *not* differ
between the two forms, so it is not part of the decision either way: neither has a lower
bound — OpenFGA's own condition never checks `current_time >= grant_time`, so both encodings
equally allow a check to succeed before the grant's nominal start. That is a property of the
source model, inherited identically either way.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **4 of 4 (100%)**. But the
store's full oracle is 7 facts, not 4: one `list_objects` test (2 expected documents) and two
`list_users` tests (2 and 1 expected subjects) are silently dropped by the harness per the
known gap documented above, so the harness's own view is **4/7 (57%)** of the store's total
assertions.

As with `custom-roles`, this gap was closed by direct verification rather than left as an
acknowledged blind spot (see "Final harness run" above) — all three dropped tests were run
live via `LookupResources`/`LookupSubjects` and matched the source oracle exactly, including
the negative half a `check:` block cannot express (document:2's `list_users` correctly
excludes bob, who is only ever granted on document:1).

Beyond the known list_objects/list_users gap, this store surfaces a **structural** blind spot
specific to native expiration, covered in Finding 1: even where the harness's own comparator
has full coverage of a store's `check:` block (as it does here, 100%), it has **no mechanism
at all** for verifying a native-expiration encoding's time-varying behavior, for any store —
the validation-YAML grammar's one context channel (`" with {...}"`) only ever reaches a
caveat's bound parameters. This is the reason `schema.zed` uses the caveat form: not because
it is the recommended production default (it is not, as of fix round 1 — see Finding 1's
correction), but because it is the only encoding this harness (or `zed validate`) can certify
at all. The recommended default, native expiration, was verified the way any
harness-unreachable recommendation must be — directly against a live server (see "Final
harness run" above) — never by a green harness run, since none is possible for that option.

---

## `multitenant-rbac`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 12/12 passing, ListUsers 1/1 passing`.

This is the store this iteration was chosen for: OpenFGA's own worked example of
multi-tenant RBAC modeled *within one store* via a tenant root type (`organization`),
rather than via store-per-tenant. `blockers.md`'s "3. Multi-store tenancy" entry is a
Class A halt about the number of OpenFGA *stores*; this store has exactly one, with tenancy
expressed as an ordinary type, and the task was to determine whether that trips the halt
anyway (it does not) and whether tenant isolation actually holds in the converted schema
(the harness cannot check this — the source only ever has one tenant, `acme`, and never
asserts a negative against a second one). The conversion needed zero tenancy-specific rules
and reached `PARITY OK` on the **first attempt**; the substance of this iteration is almost
entirely in the post-green cross-tenant probing described below.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 12
relationships loaded, 12 assertions run. Also clean under `--fail-on-warn`. Negative-control
verified per this file's standing method: flipping `document:readme#can_view@user:anne` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing exactly which path it walked:
`can_view -> editor -> organization:acme#document_manager -> ... -> admin -> user:anne`);
deleting the `organization:acme#can_edit_billing@user:francis` assertion makes the harness
report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because tenant isolation is exactly the
property this store's harness run structurally cannot certify:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 12 relationships loaded via `WriteRelationships`, all 12 checks reproduced exactly,
  and `LookupSubjects(document:readme, can_view, user)` returned exactly `{emily, anne,
  ian}` — matching the source's own `list_users` test (which the harness drops silently,
  see "Known harness gaps" above), including the negative half (`francis` correctly
  excluded).
- A second tenant (`beta`: its own admin, group, role, and document, wired the same way
  `acme`'s are) was written alongside the converted `acme` data, and every cross-tenant
  probe denied correctly, in both directions and through both `check` and the
  `LookupSubjects`/`LookupResources` exhaustive-set APIs. See Finding 2 below for the full
  probe list and the one caveat it surfaced.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all five types | schema-mapping construct table |
| Direct relation with a userset type list, incl. self-referential | `group.member: [user, group#member]`, `role.assignee: [user, role#assignee, group#member]` | construct table (confirms; `role#assignee` is declared and self-referential but no tuple/test ever exercises role-to-role nesting, unlike `group#member`, which does — `group:acme-data-engineering#member` is nested into `group:engineering`) |
| Pure-direct relation, 2-way type list, no split (referenced by other permissions but not itself carrying an operator) | `organization.admin: [user, role#assignee]` | split rule, final bullet — confirms a relation referenced from a sibling permission's union does not itself split |
| Relation/permission split | `organization.user_manager`, `.billing_manager`, `.document_manager`, `.document_viewer` (`[role#assignee] or admin`) | "The relation/permission split", composed with "Runtime-defined ('custom') roles" |
| Pure-alias permission, no split | `organization.can_invite_user`, `.can_delete_user`, `.can_edit_billing`, `.can_create_document`; `document.can_edit`, `.can_delete` | split rule, final bullet |
| Arrow, operand order reversed, into an already-split permission (no `__perm` alias) | `document.editor` (`document_manager from organization`), `document.viewer` (`document_viewer from organization`) | "Arrows"; "Point arrows at permissions, not relations" — no-alias branch |
| Runtime-defined ("custom") roles | the whole `organization`/`role` design — every org permission a role can grant already unions `[role#assignee]` | "Runtime-defined ('custom') roles" (confirms, no new rule) |
| **Type-based tenancy (tenant-as-resource-type)** | the whole store's reason for being selected — `organization` as tenant root, `document.organization` as the tenant edge | **new: "Type-based tenancy (tenant-as-resource-type)"** |

**Not exercised:** wildcards (no `:*` anywhere), conditions/caveats, modular models,
contextual tuples, **multiple OpenFGA stores** (the store models tenancy but is itself
single-store — the actual "3. Multi-store tenancy" blocker needs a different corpus
candidate, one that genuinely provisions one store per tenant, to be exercised at all),
model-ID pinning, the transitive-wildcard blocker, role-to-role nesting via `role#assignee`
(declared, never exercised by data). `id_encoding` is `none` — every object ID (`acme`,
`acme-finance`, `readme`, ...) is already inside SpiceDB's charset, hyphens included.

### Findings

**1 — Type-based (single-store) tenancy is `clean` and is not the multi-store tenancy
blocker; the distinction was implicit and this store makes it explicit.**
*Classification: ambiguous guidance → worked example.*
*Changed: `references/blockers.md` (new scope paragraph in "3. Multi-store tenancy", new
"Not a blocker: type-based (single-store) tenancy" section); `references/schema-mapping.md`
(new "Type-based tenancy (tenant-as-resource-type)" section, construct table row).*

`blockers.md`'s multi-store tenancy entry has always been about store *count* — its
detection rule greps for distinct store IDs and store CRUD calls — but its options table
(N deployments / a `tenant` resource type / definition prefixes) describes target shapes a
model can be *converted into*, which invited a misreading: a model that already looks like
option 2 (a tenant root type every resource routes through) could be mistaken for having
already triggered the blocker, rather than for being the case the blocker's detection
correctly does not fire on. `multitenant-rbac` settles it by example: one OpenFGA store, no
`storeId`/`FGA_STORE_ID` anywhere (there is no application code at all, only a model and
tests), `organization` translating by the ordinary `type` → `definition` rule with no
tenancy-specific syntax anywhere in the converted schema, and a first-attempt `PARITY OK`.
Of the spec's three tenancy shapes (definition prefixes, tenant-as-resource-type, separate
deployments), this store is unambiguously the second, and needs neither of the other two:
prefixing every definition per tenant would be pure regression (schema bloat that grows
with the customer list, the exact cost `blockers.md`'s own options table warns against for
a model that is already tenant-agnostic in its schema), and separate deployments would
discard isolation the schema already provides. `blockers.md` now says this directly: run
the Class A gate only when the detection rule fires (evidence of multiple stores), never
merely because a model has a type that plays the tenant role.

**2 — Cross-tenant isolation holds structurally only for the type that carries an explicit
tenant edge; a subject-aggregation type with none is isolated by write-path discipline
alone, identically in OpenFGA and SpiceDB.**
*Classification: Class C advisory finding (`findings-report.md`'s taxonomy) — a caveat
surfaced by post-green probing, not a mapping gap or a conversion defect, but one that must
be mechanically detectable and recorded, not merely known.*
*Changed: `references/blockers.md` ("Not a blocker: type-based (single-store) tenancy" now
carries only the Class A scope statement and the positive probe results; the caveat moved
into its own new, non-nested "Class C: tenant-root reachability gap in subject-aggregation
types" section with a `Detection` algorithm and a `Record:` line); `references/schema-mapping.md`
("Type-based tenancy", "The caveat isolation-probing surfaced" section, updated to point at
the Class C section instead of stating the caveat as prose alone).*

**Correction (fix round 1, review-directed).** The caveat below was originally documented
only as prose at the bottom of a "Not a blocker" section — correct in substance, but exactly
the framing that lets a reader stop at "isolation holds" without reaching it, and it lacked
the `Detection`/`Record:` structure every other formal finding in `blockers.md` carries, so
nothing would mechanically force a future phase-0 pass on a *different* model to check for
this shape. Fixed by splitting it into its own top-level, plainly-named section with a
7-step detection algorithm (walk the type-reference graph from the tenant root; flag any
subject-aggregation type with no path back; confirm live) and a `Record:` line pointing at
`migration-plan.md`'s `Decisions` → `Tenancy` subsection, immediately after the tenancy
choice itself. The underlying finding is unchanged.

This is the highest-value result of this iteration, and the harness's `PARITY OK` says
nothing about it either way — the source store has only one tenant. Probing a fabricated
second tenant (`beta`) on a live server (see "Final harness run" above) confirmed isolation
holds for `document`, the one type with an explicit `organization` edge: every cross-tenant
`check` (both directions, plus a group/role userset presented directly as a check subject)
returned `false`, and `LookupResources`/`LookupSubjects` agreed with no extra members in
either direction. But `role` and `group` carry **no relation back to `organization` at
all** — in the source OpenFGA model, not only in the converted one. Verified live: writing
one relationship that reuses an `acme` role inside `beta`'s `admin` union
(`organization:beta#admin@role:acme-admins#assignee`) instantly grants every `acme`
IT-admin `beta` admin, billing, and document access, with zero schema change — because
nothing in either type system ties a `role`/`group` object to one tenant; only the
organization-side relationship graph does. This is not a SpiceDB regression (OpenFGA has
the identical property) and it is not the multi-store blocker (it is about which types
*within* one store carry the tenant edge). The rule that falls out, now recorded in both
changed files: **isolation is a schema property only for types with an explicit tenant
relation; for a subject-aggregation type with none, it is a write-discipline invariant the
migration faithfully preserves rather than improves on** — worth a `migration-plan.md` note
wherever this pattern recurs, not a gate, since adding a tenant edge to harden it would be a
genuine design change the source model never made.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **12 of 12 (100%)**. But the
store's full oracle is 13 facts, not 12: one `list_users` test (3 expected subjects) is
silently dropped by the harness per the known gap documented above, so the harness's own
view is **12/13 (92%)** of the store's total assertions.

This gap was closed by direct verification, not left as an acknowledged blind spot (see
"Final harness run" above): the dropped `list_users` test was run live via
`LookupSubjects` and matched the source oracle exactly, including the negative half a
`check:` block cannot express (`francis` correctly excluded from `document:readme`'s viewer
set). Beyond the known gap, this store's own test design never asserts a negative against a
second tenant at all — the store has exactly one, `acme` — so the cross-tenant denial
results in Finding 2 are not something *any* rerun of the harness against this store's own
`store.fga.yaml` could ever produce, no matter how the converted schema is probed; they
required fabricating a second tenant's data by hand and are the actual point of this
iteration.

---

## `ip-based-access`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 2/2 passing,
Checks 2/2 passing, ListObjects 2/2 passing`.

This is the store `pack-contract.md`'s item 3 names as the open corpus candidate for the
pack's "encoding choice" material, and the store `schema-mapping.md`'s "Deliberately not
written yet" section had flagged since before `temporal-access` was converted. It is also
the store this iteration exists to get right after `temporal-access`'s own gate decision was
shipped once as a silent default and had to be reversed on owner review (see that section's
"Correction" note) — a `pack-contract.md` item 3 violation this iteration does not repeat.
The model is one caveat (`in_company_network(user_ip: ipaddress, cidr: string) {
user_ip.in_cidr(cidr) }`) applied to a userset-typed subject reference
(`organization#member with in_company_network`), combined into `document.can_view` via `and`
with an arrow. The caveat conversion needed exactly one new construct-table row and reached
`PARITY OK` on the **first attempt** — the substance of this iteration, like
`temporal-access`'s, is almost entirely the encoding *choice*, not the syntax.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 4
relationships loaded, 2 assertions run. Also clean under `--fail-on-warn`. Negative-control-
verified per this file's standing method: flipping `document:1#can_view@user:anne with
{"user_ip":"192.168.0.1"}` to `assertFalse` (and its sibling assertion to `assertTrue`) fails
`zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own
explanation trace showing the full walk — the failed `in_cidr` evaluation at both the
top-level `can_view` node and the nested `ip_based_access_policy__perm` arrow target, next
to the correctly-passing `viewer` and `member` hops); deleting the `assertFalse` assertion
makes the harness report `MISSING` (exit 1, `expected=False`).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because this store's whole point is an encoding
recommendation and the harness alone cannot settle it:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 4 relationships loaded via `WriteRelationships`, both check-block assertions match
  exactly (`zed permission check document:1 can_view user:anne --caveat-context
  '{"user_ip":"..."}'`, matching for both the in-range and out-of-range address), and a check
  with **no** context at all correctly returns `caveated` rather than a boolean, per standard
  SpiceDB caveat behavior.
- Both `list_objects` tests — silently dropped by the harness, see "Known harness gaps" above
  — were independently run via `LookupResources(document, can_view, user:anne)` and matched
  the source oracle **exactly**: `{document:1}` for the in-range address, `{}` (correctly
  empty, not merely unasserted) for the out-of-range one. This store's full oracle is **4/4
  confirmed** (2 check + 2 list_objects), not just the 2/4 the harness itself can see.
- A parallel schema fragment adding a materialized `network_verified_member` relation and a
  `can_view_verified` permission (`viewer & organization->network_verified_member__perm`, no
  caveat at all) was deployed to a separate keyspace and independently verified: `check`
  returns `true`/`false` with **no `--caveat-context` flag supplied at all** (unlike
  `can_view`, which returns `caveated` without one), and `LookupResources` against it returns
  the correct resource set with zero per-candidate CEL evaluation. This is the decisive
  evidence for the gate decision below — see Finding 2.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all three types | schema-mapping construct table |
| Pure-direct relation, no split | `organization.member`, `document.organization`, `document.viewer` | split rule, final bullet |
| **Caveat on a userset-typed subject reference** (`T#rel with cond`, not just `user with cond`) | `organization.ip_based_access_policy: [organization#member with in_company_network]` | **new construct-table row** + "IP-based access" section |
| `__perm` arrow-alias for a bare relation target | `organization.ip_based_access_policy__perm` | "Point arrows at permissions, not relations" (confirms) |
| **SpiceDB intersection (`&`), the corpus's first live confirmation of it at all** | `document.can_view` (`viewer and ip_based_access_policy from organization`) | "Always fully parenthesize" (confirms, no new rule). Metric, command and membership: "The canonical store table" → **SpiceDB intersection (`&`)** — one store at the time this row was written, joined by `banking` (iteration 12, batch 1). Note the derivation must exclude CEL `&&` in caveat bodies, which is why a bare `grep -l '&'` also matches `condition-data-types` and is not the right check |
| Intersection combined with an arrow, caveat propagating through the arrow target and an extra userset-membership hop | `document.can_view` | new confirmation, see Finding 1 |
| `ipaddress` caveat parameter compared via `.in_cidr(cidr)` against a **runtime-supplied** parameter, not a hardcoded literal | `in_company_network` | "Caveat parameter types and expression bodies" (confirms — the hardcoded-literal `.in_cidr("<addr>/32")` rewrite rule from `condition-data-types` does not even apply here, since `cidr` is already a parameter) |
| **Encoding choice: caveat vs. materialized marker for a per-request-supplied attribute** | the whole store's reason for being selected | **new: "IP-based access: caveat vs. materialized marker"** |

**Not exercised:** wildcards (no `:*` anywhere), the relation/permission split (no `define`
here mixes a type list with an operator — `ip_based_access_policy` has a list and no
operator, `can_view` has an operator and no list, so neither splits), modular models,
runtime-defined roles, multi-store tenancy, model-ID pinning, contextual tuples, the
multi-`tests:`-block collision (`abac-with-rebac`/`condition-data-types`'s finding) — this
store's two `tests:` blocks vary only `check.context`, never `tuples`, so nothing collides.
`id_encoding` is `none` — `acme`, `anne`, `1` are all already inside SpiceDB's charset.

### Findings

**1 — Two new constructs, both corpus-confirmed to need no new rule: a caveat on a
userset-typed subject reference, and SpiceDB intersection itself.**
*Classification: missing conversion rule (the userset-with-condition row) + ambiguous
guidance → worked example (the intersection confirmation).*
*Changed: `references/schema-mapping.md` (new construct-table row; new note in "Always fully
parenthesize").*

Every prior corpus caveat attached `with cond` to a bare `user` type
(`condition-data-types`, `temporal-access`) or avoided caveats entirely by design
(`abac-with-rebac`). This store's `organization#member with in_company_network` is the
corpus's first caveat on a **userset**-typed subject, and resolving a check through it
requires two independent things to hold at once: ordinary, uncaveated membership in the
userset (`user:anne` ∈ `organization:acme#member`) *and* the caveat bound to the edge that
names that userset as its subject. Verified live, both layers resolved correctly with no
special handling — for the matching address, the non-matching address, a check with no
context at all (`caveated`, not an error), and via `LookupResources`. Separately, this store
is the corpus's **first store to use SpiceDB intersection (`&`) at all**
[updated, batch 1: this section originally also claimed "still only" — true when written, and
falsified by `banking` (iteration 12), the family's second member. See "The canonical store
table" → **SpiceDB intersection (`&`)** for the current two-store membership; this section's
own claim is now scoped to "first," which batch 1 does not change.]
(metric, command and full output in "The canonical store table" → **SpiceDB intersection
(`&`)**; the derivation turns on separating a `&` in a permission expression from CEL `&&`
inside a caveat body, which a bare `grep -l '&'` conflates) in a real converted
schema — every precedence and parenthesization claim in "Always fully parenthesize" had only
ever been verified against hand-built discriminating fixtures until this store forced one
for real. `document.can_view: viewer and ip_based_access_policy from organization` — one
source `and` node — became one parenthesized `&` group with no new rule needed, and matched
OpenFGA's own `Checks 2/2 passing` exactly. Neither finding required inventing anything; both
are the existing split/arrow/parenthesization rules meeting a shape no prior store had
exercised.

**2 — IP-based access is a Class B gate decision between caveat (recommended default) and a
materialized marker (the alternative), and it resolves in the *opposite* direction from
`temporal-access`'s gate — for a principled reason, not an inconsistency.**
*Classification: ambiguous guidance → worked example (an explicitly flagged open "encoding
choice," the same category `temporal-access` resolved, per `schema-mapping.md`'s
"Deliberately not written yet," pre-this-store text).*
*Changed: `references/schema-mapping.md` (new "IP-based access: caveat vs. materialized
marker" section, construct table row, "Deliberately not written yet" update);
`corpus-runs/ip-based-access/` (`schema.zed` is the caveat form — both the recommendation and
the only form the harness ritual can verify, so unlike `temporal-access` there is no split
artifact for this store).*

The task brief supplied an independent, large-scale benchmark of three encodings for one
attribute-gated `LookupResources` path — caveat context, a wildcard marker checked via
intersection, and the attribute encoded directly in the relation name — with a measured
ordering (relation-name fastest, caveat context second, wildcard-marker-plus-intersection
slowest, by a wide margin) cited here for its **ordering only**, never reproduced or requoted
as a number this store's own two-document data set could ever measure. Two of the three do
not apply to this store's actual construct **in their literal form**, and that is itself part
of the finding:

- **Relation-name encoding needs a small, schema-known, enumerable vocabulary.** This store's
  `cidr` is an arbitrary string an organization admin configures at write time, not a fixed
  company-wide set of named zones a schema author could enumerate without a schema change
  every time a customer's network topology changes. Ruled out for this store specifically,
  not for the construct in general — a product whose network policy genuinely is a small
  fixed set of named zones should still evaluate this option against the same cited ordering.
- **The literal `user:*` wildcard-marker form has no syntactic target here at all.** A
  wildcard subject means "any subject of this type"; there is no `ipaddress:*` or equivalent,
  because a source IP is a per-request **value** supplied as caveat context, never a
  **subject type**.

What *does* generalize is the wildcard-marker row's underlying **architecture** — pre-
materialize the gated fact as a plain, uncaveated relationship, checked via intersection
instead of per-call CEL — even though its literal syntax does not apply. Built and verified
live alongside the caveat form (see "Final harness run" above): a `network_verified_member`
relation the application would write once it has verified a member's current network by
whatever means (login-time IP check, VPN certificate, device posture), checked via
`can_view_verified = (viewer & organization->network_verified_member__perm)`. This needs
**zero caveat context and zero per-check CEL evaluation**, confirmed live, and structurally
matches every non-ABAC permission's cost in this pack's corpus.

**This is exactly the trap the task brief named, and it applies with full force to the
alternative just built.** `can_view_verified` is architecturally the same shape the cited
benchmark's middle row measures — and that row measured **slower**, not faster, than plain
caveat context for `LookupResources`. AuthZed's own documented recommendation to prefer a
wildcard/marker pattern over caveats is, per the cited ordering, about **static-data hygiene**
(not letting stale caveat context on infrequently-changing data drift out of sync), **not**
about latency — reaching for the marker here on a latency argument reaches for the wrong
justification. Two real costs attach to it regardless: it answers "was this member's network
verified as of the last write to `network_verified_member`," not "is the **current** request
in range" — a materially weaker guarantee for a construct whose entire reason to exist is
checking the current request, unlike `temporal-access`'s native-expiration narrowing ("as of
an arbitrary time" → "right now"), which cost a query capability but not a live security
property; and it invents a reverification/revocation obligation (cadence, logout, IP-change
handling) with no analog anywhere in the source model, exactly the kind of sync obligation
`pack-contract.md` item 6 requires surfacing at the gate.

One more open question surfaced, not settled: `can_view`, the caveat form, is definitionally
ineligible for SpiceDB Materialize per the given fact that a materialized permission path
supports neither caveats nor wildcards. `can_view_verified` removes the caveat, but nothing in
this store's evidence establishes that an *intersection*-shaped permission is itself
Materialize-eligible — no Materialize environment was available to test directly. Recorded as
an open question, not a settled second recommendation: a customer needing both real-time
per-request IP enforcement and Materialize-scale `LookupResources` on one permission faces a
real, currently-unresolved architectural tension.

**Gate decision, recorded in `schema-mapping.md`:** caveat context is this pack's
**recommended default** for a per-request-supplied ABAC attribute carried over from an
OpenFGA source — rated `clean`, first-attempt `PARITY OK`, the only form this pack's own
tooling verifies deterministically (including `LookupResources`). A materialized marker
remains the documented **alternative**, rated `effort`, for a call site that explicitly
accepts a periodically-refreshed check in place of the literal current request — never chosen
on a latency argument alone, per the benchmark-ordering correction above. Detection, the
options table, and the `migration-plan.md` record location (`Decisions` →
`Per-blocker resolutions`) are in `schema-mapping.md`'s "The gate decision" subsection.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **2 of 2 (100%)**. But the
store's full oracle is 4 facts, not 2: both `tests:` blocks also carry a `list_objects` entry
(1 expected document, and correctly 0), silently dropped by the harness per the known gap
documented above, so the harness's own view is **2/4 (50%)** of the store's total assertions
— on a store with only four assertions total to begin with. **Superseded (batch 3):** this
was the thinnest visible fraction in the corpus through batch 2; `gdrive` (batch 3) now ranks
thinnest at 3/9 (33.3%), see "The canonical store table" → **Harness-visible fraction** for
the full, current sorted list.

As with `custom-roles`, `temporal-access`, and `multitenant-rbac`, this gap was closed by
direct verification rather than left as an acknowledged blind spot (see "Final harness run"
above): both dropped `list_objects` tests were run live via `LookupResources` and matched the
source oracle exactly, including the negative case a `check:` block cannot on its own
establish as *exhaustive* (the out-of-range address's resource set is correctly empty, not
merely unassessed).

---

## `advanced-entitlements`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 2/2 passing,
Checks 16/16 passing, ListObjects 3/3 passing`.

This store's own `README.md` cites Notion's pricing tiers, and it is the store this
iteration was chosen for on a specific, stated hard limit: a SpiceDB caveat compares stored
context against request context — it cannot read another relationship, and it cannot
aggregate. A prior, independent analysis of a comparable system found the entitlements
pattern (`used < quota`, both values independently stored) genuinely `blocked`. The task was
to determine which case this store actually is, not assume — and its own model and tuples
settle it directly: `store.fga.yaml`'s `tuples:` block binds only the *quota* half of each
of its three conditions (`collaborator_limit`, `row_sync_limit`, `page_history_days_limit`);
the *usage* half (`collaborator_count`, `row_sync_count`, `page_history_days_count`) never
appears in `tuples:` anywhere in the file, only in `tests:`' own per-check `context:` blocks.
OpenFGA's own model never persists "used" either — the caller already supplies it fresh per
check, before any SpiceDB migration is in scope. This is the **request-supplied** case, not
the both-stored one, and the conversion needed **zero new mapping rules**, reaching
`PARITY OK` on the **first attempt**. The substance of this iteration is almost entirely the
capability determination and the resulting encoding-choice gate decision, not the syntax —
the same shape `temporal-access` and `ip-based-access` were chosen for.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 13
relationships loaded, 16 assertions run. Also clean under `--fail-on-warn` — but only after
one fix: the store's own `has_feature` relation name (verbatim from OpenFGA) triggers a
`relation-name-references-parent` lint under `--fail-on-warn`, resolved with a
`// spicedb-ignore-warning` comment rather than a rename (see Finding 3). Negative-control-
verified per this file's standing method: flipping `plan:pro#subscriber@user:beth` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the walk: `plan:pro subscriber -> organization:okta
member -> user:beth`); deleting the `feature:enterprise-support#has_feature@user:beth`
assertion makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because this store's whole point is a
capability determination the harness's boolean pass/fail cannot make on its own:

- `WriteSchema` accepted `schema.zed` unedited (including the ignore-warning comment), no
  compile step, no `use` flag.
- All 13 relationships loaded via `WriteRelationships`, all **16** check-block assertions
  match exactly (`zed permission check ... --caveat-context '{...}'`, one per source check).
- All **3** `list_objects` tests — silently dropped by the harness, see "Known harness gaps"
  above — were independently run via `LookupResources(feature, has_feature, ...)` and matched
  the source oracle **exactly**, including the negative half (anne's second list at 1000×
  every limit correctly returns only `basic-page-analytics`, not merely an unasserted
  subset). This store's full oracle is **19/19 confirmed** (16 check + 3 list_objects), not
  just the 16/19 the harness itself can see.
- **The capability probe (Finding 1's decisive evidence):** with `organization:okta` holding
  one member (`beth`), `feature:can-invite-collaborator#has_feature@user:beth` with
  `{"collaborator_count":20}` returns `true`. Writing two more real
  `organization:okta#member` relationships (`carol`, `dave` — the most literal opportunity
  this store's own graph offers for an implicit aggregation) and re-running the identical
  check returns the identical `true` — SpiceDB never noticed or counted them, because nothing
  in the schema ties `collaborator_count` to `organization#member`'s actual cardinality at
  all. Source-confirmed: `pkg/caveats/eval.go`'s `EvaluateCaveatWithConfig`, the only entry
  point that evaluates a caveat body, takes nothing but a flat `map[string]any` — no
  datastore reader, no aggregate primitive.
- **The materialized-marker alternative, verified live in a separate keyspace**
  (`corpus-runs/advanced-entitlements/schema-materialized-marker.zed`): a plain
  `has_feature_verified` relation, written by the application once it has recomputed usage
  against quota, returns `true`/`false` with **no caveat context supplied at all** — the same
  "plain graph walk" cost every non-ABAC permission in this pack's corpus gets, at the price
  of a new recompute-and-rewrite sync obligation the source model never had.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all four types | schema-mapping construct table |
| Pure-direct relation, no split, three levels of userset nesting | `organization.member`, `plan.subscriber`, `feature.has_feature` | split rule, final bullet — confirms through a 3-level chain (`organization#member` → `plan#subscriber` → `feature#has_feature`); the **third** store with zero permissions anywhere in its schema, ranked by iteration in "The canonical store table" → **Zero-permissions set**: `condition-data-types` (iteration 2, rank 1), `temporal-access` (6, rank 2), this store (9, rank 3) — the first two being single-relation caveat stores this one now joins |
| `condition c(p: int, q: int) { p <= q }` → `caveat c(p int, q int) { p <= q }`, three instances | all three caveats | "Caveat parameter types and expression bodies" (confirms, `int` already covered) |
| **The bare uncaveated form combined with *more than one* distinct caveat on the same userset subject, on one relation** | `feature.has_feature` (bare `plan#subscriber` + 3 distinct caveat names, all on the identical `plan#subscriber` reference) | narrower confirmation of the existing split-rule/caveat-union rules — see "Usage-quota entitlements: caveat vs. materialized marker" |
| Caveat context split across write time (quota, bound) and check time (usage, supplied) | every `has_feature` check | SpiceDB merges both natively — same mechanism `temporal-access`/`ip-based-access` established, confirmed for a third, non-temporal, non-network domain — ranked in "The canonical store table" → **Split-context caveat family**, on the *domain* key: wall-clock time (`temporal-access`, iteration 6), network locality (`ip-based-access`, 8), usage counter (this store, 9). This store is rank 3 on both available keys, store order and domain order |
| **Usage-quota entitlements: caveat vs. materialized marker** | the whole store's reason for being selected | **new: "Usage-quota entitlements: caveat vs. materialized marker"** |
| `relation-name-references-parent` lint + `spicedb-ignore-warning` suppression | `feature.has_feature` | **new: Codegen rules addendum** |

**Not exercised:** wildcards (no `:*` anywhere), the relation/permission split (no `define`
in this store mixes a type list with an operator — the **fifth** corpus store with no split
anywhere at all. Metric, command, full output and sorted membership are in "The canonical
store table" → **No-split lineage**; ranked by iteration, that set is
`condition-data-types` (iteration 2, rank 1), `abac-with-rebac` (iteration 5, rank 2),
`temporal-access` (iteration 6, rank 3), `ip-based-access` (iteration 8, rank 4),
**this store (iteration 9, rank 5)**, `groups-resource-attributes` (iteration 11, rank 6) —
so this store is the fifth, not the fourth
[correction, iteration 11 fix round 2: two earlier drafts of this note each omitted one
predecessor found by checking only the stores already listed rather than grepping all ten.
Iteration 11 consolidation pass: the bare parenthesized numbers this note used to carry —
`` `condition-data-types`(2) `` — are unlabeled iteration indices, the ambiguous notation
that helped hide the ordinal error corrected in the `custom-roles` section]),
arrows, modular
models, runtime-defined roles, multi-store tenancy, model-ID pinning, contextual tuples,
`list_users` (this store's baseline has none). `plan`'s userset-subject targets
(`organization#member`, `plan#subscriber`) are both plain relations, never permissions, so
"a userset subject may point at a permission" is not exercised here either. `id_encoding` is
`none` — every object ID (`acme`, `okta`, `free`, `pro`, `can-view-page-history`, ...) is
already inside SpiceDB's charset, hyphens included.

### Findings

**1 — Usage-quota entitlements are the request-supplied case, not the both-stored `blocked`
case; a third Class B gate decision (ranked in "The canonical store table" → **Class B gate
decisions**: `temporal-access` gate 1, `ip-based-access` gate 2, this store gate 3),
resolved the same direction as `ip-based-access` and for
the same stated principle.**
*Classification: missing conversion rule (the capability determination and the N-way
mixed-alternative row) + ambiguous guidance → worked example (the gate decision).*
*Changed: `references/schema-mapping.md` (new "Usage-quota entitlements: caveat vs.
materialized marker" section, construct table row); `references/blockers.md` (new "Not a
blocker: usage-quota entitlements with a request-supplied usage figure" section).*

This is the finding this iteration exists to produce, and it is a **negative** result in the
best sense: the store this iteration was chosen specifically to stress-test the "caveats
can't aggregate" limit against turns out not to hit it, and working out precisely *why* is
what makes the boundary trustworthy rather than merely assumed. `store.fga.yaml`'s own
`tuples:`/`tests:` split (quota bound at write time, usage supplied fresh on every check) is
the same shape `temporal-access` (`grant_time`/`grant_duration` bound, `current_time`
supplied) and `ip-based-access` (`cidr` bound, `user_ip` supplied) already established — this
store is the third instance of it on both available keys — store order and domain order alike
(see "The canonical store table" → **Split-context caveat family**) — and the first that is
neither temporal nor network. The mechanical caveat
translation needed no new rule beyond the existing split rule and caveat-parameter-type
table.

The gate decision follows the identical principle those two stores already established and
resolves the same direction as `ip-based-access`, for the identical stated reason: SpiceDB
has a first-class structural feature for wall-clock comparison (`with expiration`) and none
for arbitrary per-request context. A usage count has no more of a native SpiceDB counting
primitive than a source IP address does, so caveat context stays the recommended default
(rated `clean`) and a materialized "verified" marker relation is the documented alternative
(rated `effort`) — built and verified live (`has_feature_verified`, see "Final harness run"
above), at the cost of checking a **staler** quota state than the source checks on every call,
plus a new recompute-and-rewrite sync obligation with no analog in the source model.

The decisive evidence for *why* this store is not the blocked case, run live specifically to
test the limit rather than assume it: writing real `organization:okta#member` relationships
that would, if SpiceDB could aggregate at all, be the single most natural source for
`collaborator_count` to come from — and confirming the caveat evaluation is completely
unaffected by them (see "Final harness run" above). Source-confirmed the same way:
`pkg/caveats/eval.go`'s `EvaluateCaveatWithConfig` takes only a flat `map[string]any`, with no
datastore reader anywhere in its signature or the `Environment` that compiles a caveat's
parameters. `blockers.md`'s new section states precisely where the capability would have to go
if a future store's usage figure *is* itself a persisted SpiceDB fact rather than a
caller-supplied value: out of the caveat system entirely, into application code that reads
both stored facts itself and supplies the comparison's result (or the raw count) as check-time
context — the same partial workaround the task brief describes, confirmed as the only shape
the capability boundary allows, not merely as a fallback for a missing rule. No corpus store
has that shape yet, so it is recorded as a derived boundary, not a sixth catalog blocker (spec
decision D11).

**2 — The bare uncaveated form combined with more than one distinct caveat, on the same
userset subject, verified to isolate cleanly with zero crosstalk.**
*Classification: ambiguous guidance → worked example, narrower than first drafted (see Fix
round 1 correction in the task report — the original text overclaimed novelty this store
does not have).*
*Changed: `references/schema-mapping.md` ("Usage-quota entitlements" section, "The one
genuinely new construct" subsection).*

The task's other fact to verify rather than assume: that SpiceDB allows a relation's type list
to mix caveated and uncaveated forms of one subject type, with the uncaveated alternative
making the caveat *optional* rather than mandatory. This was already exercised, at larger
scale than this store reaches on either axis alone: `temporal-access` unions a bare form with
1 caveat; `condition-data-types` unions 9 distinct caveat names (with no bare form at all).
`feature.has_feature` is the first store to combine **both** — a bare form *and* more than one
distinct caveat name (3) on the identical subject reference (`plan#subscriber`) in one union.
Three live probes (see "Final harness run" — cross-caveat isolation, the
bare-form-ignores-any-context case, and a second cross-caveat check on the pro tier) confirm
each alternative evaluates strictly against its own declared parameters, with no accidental
satisfaction from a different alternative's context key and no effect at all on a feature with
no caveated alternative. No new rule was needed; this is a real but narrow increment on an
axis (bare-plus-multiple-caveats) neither predecessor store combined, not a new isolation
property in general — `condition-data-types` already exercised 9-way caveat coexistence
correctly resolving, just never alongside a bare alternative.

**3 — The `relation-name-references-parent` lint has a purpose-built suppression comment, and
it is the correct fix here, not a rename.**
*Classification: missing conversion rule (a new codegen constraint) → `schema-mapping.md`.*
*Changed: `references/schema-mapping.md` ("Codegen rules", new bullet).*

`feature.has_feature` — the OpenFGA source's own name, unchanged — trips
`relation-name-references-parent` under `--fail-on-warn` (`"has_feature" references parent
type "feature"... recommended to drop the suffix`). Verified from source
(`pkg/development/warningdefs.go`): the lint is a plain suffix-string comparison with no
semantic content, so unlike `arrow-references-relation` (fixed via the non-destructive
`__perm` alias) there was no basis to suspect a hidden resolution difference — but there was
also no non-destructive fix available, since the write path and the check path are the one
same name. Renaming to comply (`has_feature` → `has`) would trade fidelity to the source's own
name for a cosmetic nudge. `// spicedb-ignore-warning: relation-name-references-parent`,
placed directly above the flagged line, is a real, verified `WriteSchema`-surviving
suppression (confirmed via `zed schema read` after deploy). Re-verified in the iteration 11
consolidation pass by running `zed validate --fail-on-warn` over
`corpus-runs/*/schema*.zed` — **16 files across all 11 stores**, of which 14 files across the
10 stores other than this one: every one exits 0 with no warning of any kind, except
`modular/schema-use-partial.zed`, which exits 1 for the unrelated, documented reason that zed
v0.31.1's local parser cannot read `use partial` at all (see `modular`'s finding 2). That
confirms this is genuinely the first corpus store to force this lint, not merely the first to
mention it. Also re-confirmed that the suppression is what keeps this store green: stripping
the `// spicedb-ignore-warning` line from a scratch copy of `schema.zed` reproduces the
warning and exit 1 immediately (`Relation "has_feature" references parent type "feature" in
its name`).
[correction, iteration 11 consolidation pass: this read "every other corpus store's committed
`schema*.zed` (12 files across 9 stores)" — internally inconsistent, since the parenthetical
counted all 9 stores then current, including this one, while the sentence said "every
other" (which was 10 files across 8 stores). Both figures were also stale at 11 stores.]
[correction, batch 5: "still the only" and "matches only this store's two artifacts" were
already false by batch 3 and went uncorrected through batch 4 — `iot` (iteration 14)
independently forces the identical lint on `device.can_rename_device` and fixes it the same
way (`grep -rln 'spicedb-ignore-warning' corpus-runs/` now matches `advanced-entitlements`'s
two artifacts, `iot/schema.zed`, and `issue-tracking/schema.zed`, this batch, on
`collection.parent_collection`; `iot`'s own section correctly cited this store as the origin
and never repeated the "only" claim itself, which is why the error survived undetected — it
lived in the wrong section). This store remains the *first* to force the lint and the first to
establish the suppress-in-place fix; "only" is retracted, not the fidelity rating or the fix
itself.]

**Correction (not counted toward this iteration's new-findings total, per Fix round 1):
`advanced-entitlements` does not settle the "unbounded permission surface" runtime-roles
question a prior iteration named it for.**
*Classification: documentation correction, caught while reading this store's actual content
against the two stale forward-references pointing at it.*
*Changed: `references/schema-mapping.md` ("Runtime-defined roles" boundary subsection,
"Deliberately not written yet" section — both corrected, not repeated here).*

`custom-roles`' own writeup and the "Deliberately not written yet" section both named
`advanced-entitlements` as "the next corpus candidate to check" for a role that must grant a
permission shape the schema did not anticipate. This store has no `role` type, no
customer-defined permission grant, and no runtime-configurable authorization surface of any
kind — it is a fixed, two-tier subscription-quota model, a different construct despite the
similar-sounding "entitlements" name in both. Both stale references are corrected in place
rather than repeated; the unbounded-permission-surface question remains open with no corpus
candidate currently earmarked.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **16 of 16 (100%)**. But the
store's full oracle is 19 facts, not 16: three `list_objects` tests (5, 4, and 1 expected
features respectively) are silently dropped by the harness per the known gap documented
above, so the harness's own view is **16/19 (84%)** of the store's total assertions.

As with `custom-roles`, `temporal-access`, `multitenant-rbac`, and `ip-based-access`, this gap
was closed by direct verification rather than left as an acknowledged blind spot (see "Final
harness run" above): all three dropped `list_objects` tests were run live via
`LookupResources` and matched the source oracle exactly, including the negative half a
`check:` block cannot on its own establish as *exhaustive* — anne's second list, at 1000×
every plan limit, correctly returns only the one uncaveated feature (`basic-page-analytics`),
not merely an unasserted subset of the free tier's four quota-adjacent features.

---

## `superadmin`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 8/8 passing, ListObjects 3/3 passing, ListUsers 2/2 passing`.

This is the store this iteration was chosen for: OpenFGA's own worked example of a
platform-level admin (`system`) that sits *above* the tenant root (`organization`) this
pack's tenancy material was already built around, paired with a time-boxed helpdesk grant.
It is the deliberate inverse of `multitenant-rbac`'s tenant isolation — a type whose whole
purpose is to reach *across* every tenant by design — and the task was twofold: run
`blockers.md`'s Class C tenant-root reachability algorithm against it and report what it
does (see Finding 1 — it produces zero flags, but not for the reason a first read might
assume), and probe the converted schema for exactly the failure mode a check-only harness
cannot see: over-permissive cross-tenant reach. The conversion needed zero new mapping
rules — every construct (the split, arrows with and without a `__perm` alias, a bare type
list with one `with`-clause) was already established — and reached `PARITY OK` on the
**first attempt**. The substance of this iteration is almost entirely in the Class C
algorithm run and the post-green cross-tenant probing, not the syntax.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 7
relationships loaded, 8 assertions run. Also clean under `--fail-on-warn` (no
`arrow-references-relation` warning despite two arrow targets that would otherwise trigger
it — counterfactual run, same method `github`'s finding 2 established: pointing
`system->admin` and `organization->helpdesk_member` at the bare relations instead of their
`__perm` aliases reproduces exactly two `arrow-references-relation` warnings; the committed
`schema.zed` aliases both, so neither fires). Negative-control-verified per this file's
standing method: flipping `task:create-example#viewer@
user:peter` to `assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the
harness, with `zed`'s own explanation trace showing the full walk: `viewer -> editor ->
project:openfga editor -> organization:acme admin -> admin__direct -> user:peter`); deleting
the `task:create-example#viewer@employee:john with {...}` assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because cross-tenant reach is exactly the
property this store's harness run cannot certify (the source has only one tenant, `acme`,
and the harness drops `list_objects`/`list_users` — 5 of this store's 13 total assertions —
silently regardless):

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 7 relationships loaded via `WriteRelationships`, all **8** check-block assertions match
  exactly (including `employee:john`'s caveat-context check, `--caveat-context
  '{"current_time":"2024-01-01T00:10:00Z"}'`), matching `fga model test`'s own
  `Checks 8/8 passing` one for one.
- All **3** `list_objects` assertions (john's, peter's, and anne's viewable `task` objects)
  and both **`list_users`** assertions (viewers of `task:create-example`, filtered by
  subject type `user` and by `employee` respectively) — all silently dropped by the harness,
  see "Known harness gaps" above — were independently run via
  `LookupResources`/`LookupSubjects` and matched the source oracle **exactly**, including the
  negative half (the `user`-filtered `list_users` call returns only `user:peter`, correctly
  excluding `application:system-management-app` even though it is a full system admin, since
  it is not of type `user`). This store's full oracle is **13/13 confirmed** (8 check +
  3 list_objects + 2 list_users), not just the 8/13 (62%) the harness itself can see.
- **Cross-tenant probing** (see Finding 1 for the algorithm result this evidence feeds): a
  second tenant (`organization:beta`, its own `project`/`task`, declaring
  `organization:beta#system@system:global` — the *same* system root `acme` uses) and a third
  tenant (`organization:gamma`, declaring a **different** root,
  `organization:gamma#system@system:other-vendor`, with its own admin `employee:zara`) were
  written alongside the converted `acme` data. Three probe categories, all matching the
  source's evident design with zero over-permissiveness found:
  1. **A superadmin reaching a tenant they should reach:** `employee:anne` and
     `application:system-management-app` (both `system:global` admins) check `true` on
     `task:beta-task`'s `viewer`/`editor` — `beta` shares `acme`'s system root, so this is
     correct, designed reach, not a leak.
  2. **A non-superadmin attempting the same path:** `user:peter` (`acme`'s own org admin, not
     a system admin) checks `false` on `task:beta-task`; `employee:john` (`acme`'s time-boxed
     helpdesk grant, org-scoped, mid-grant) checks `false` on `task:beta-task#viewer` even
     though the identical check on `acme`'s own `task:create-example` is `true` at the same
     instant — the helpdesk pattern stays tenant-contained even though the superadmin pattern
     doesn't, exactly as the source models them differently; `employee:zoe` (a subject with
     zero relationships anywhere) checks `false` on both tenants.
  3. **A superadmin reaching something outside the intended scope:** `employee:anne`
     (`system:global` admin) checks `false` on `task:gamma-task` — `gamma` declares a
     *different* system root, so her reach correctly stops there — while `employee:zara`
     (`system:other-vendor`'s own admin) checks `true` on `gamma` and `false` on both `acme`
     and `beta`. `LookupResources(task, viewer, employee:anne)` returns exactly `{beta-task,
     create-example}` — `gamma-task` correctly absent — and `LookupSubjects(task:gamma-task,
     viewer, employee)` returns exactly `{zara}`, confirming the same result through the
     exhaustive-set APIs, not just `check`.
- **The gate decision** (temporal access: caveat vs. native expiration) applies unchanged
  from `temporal-access` — see Finding 2. `corpus-runs/superadmin/schema-native-expiration.zed`
  (the recommended default) was deployed to a separate keyspace and independently verified:
  dropping `use expiration` reproduces the documented trap exactly (`could not lookup caveat
  'expiration' for relation 'helpdesk_member': caveat with name 'expiration' not found`);
  with the flag, `WriteSchema` and `zed validate` both accept it directly; a relationship
  written with a 6-second future expiration checked `true` and was visible in
  `zed relationship read` immediately after write, and ~8 seconds later (inside SpiceDB's GC
  interval) checked `false` and had vanished from `zed relationship read`,
  `LookupResources`, and `LookupSubjects` alike — through the same two-hop arrow chain
  `task.viewer` walks to reach it (see Finding 2).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition`, incl. three empty types | `user`, `employee`, `application`, and all four non-empty types | schema-mapping construct table |
| Pure-direct relation, no split (bare type list, no operator) | `system.admin`, `organization.system`, `organization.member`, `organization.helpdesk_member`, `project.organization`, `project.owner`, `task.project`, `task.owner` | split rule, final bullet |
| Relation/permission split | `organization.admin`, `project.editor`, `project.viewer`, `task.editor`, `task.viewer` | "The relation/permission split" |
| Pure-alias permission, no split | `organization.can_create_project` | split rule, final bullet |
| Arrow, operand order reversed, into an **already-split** permission (no `__perm` alias) | `project.editor`'s `admin from organization` (targets `organization.admin`, already split by the row above) | "Arrows"; "Point arrows at permissions, not relations" — no-alias branch |
| Arrow into a **bare (unsplit) relation**, needing a `__perm` alias | `organization.admin`'s `admin from system` (`system.admin__perm`); `project.viewer`'s `helpdesk_member from organization` (`organization.helpdesk_member__perm`) | "Point arrows at permissions, not relations" — alias branch (same shape `abac-with-rebac`'s `email_verified__perm` confirmed) |
| Arrow chained through another arrow-derived permission (nested arrow) | `task.editor`'s `editor from project` (`project.editor`, itself an arrow-derived permission) | "Arrows" (confirms, no new rule) |
| `condition`/`caveat` with `timestamp`/`duration` parameters | `non_expired_time_grant` | "Caveat parameter types and expression bodies" (confirms, no new rule) |
| Bare type list with a single `with`-clause, no bare alternative, no split | `organization.helpdesk_member: employee with non_expired_time_grant` | split rule, final bullet (confirms — a type list needs no *union* to stay a plain relation, one `with`-clause entry is enough) |
| **Temporal access: caveat vs. native expiration, applied through nested arrows** | the whole store's helpdesk-grant reason for being selected | "Temporal access…" gate applied unchanged + **new confirmation: gate composes through 2 arrow hops** |
| Type-based tenancy (tenant-as-resource-type) | `organization` as tenant root, `project.organization` / `task.project→organization.project` as the tenant edge | "Type-based tenancy" (confirms, no new rule) |
| **Class C tenant-root reachability algorithm run against a designed, governed super-root** | the whole store's `system`/`organization` relationship | **new: worked derivation + step-5 scope boundary, `blockers.md`'s Class C section** |

**Not exercised:** wildcards (no `:*` anywhere), userset subjects in any type list (**zero**
`Type#relation` entries anywhere in this store — every subject reference is a bare type or an
arrow target, which is itself load-bearing for Finding 1), runtime-defined roles, modular
models, contextual tuples, multiple OpenFGA stores (single store, tenancy expressed as an
ordinary type, same shape `multitenant-rbac` already ruled out as the multi-store blocker),
model-ID pinning, the transitive-wildcard blocker. `id_encoding` is `none` — every object ID
(`acme`, `global`, `system-management-app`, `create-example`, ...) is already inside
SpiceDB's charset, hyphens included.

### Findings

**1 — The Class C tenant-root reachability algorithm produces zero flags on this store, but
because `system`'s cross-tenant reach never becomes a step-5 candidate, not because the
algorithm evaluated and correctly exempted it — a real, now-documented scope boundary, not a
false positive and not (quite) a clean negative control either.**
*Classification: ambiguous guidance → worked example.*
*Changed: `references/blockers.md` (Class C section: new worked derivation for `superadmin`,
new scope-boundary paragraph, recorded exemption a future step-5 broadening would need).*

This is the finding this iteration exists to produce, and the honest answer is more precise
than either of the two outcomes the task brief posed. Hand-deriving the algorithm exactly as
`blockers.md` specifies it (same steps, same format as the `multitenant-rbac`/`custom-roles`
derivations already on file): `T = organization`; step 3's belongs-to edges give a
tenant-scoped set of `{organization, project, task}` (`system` is not in it — its own edges,
`system -> employee` / `system -> application`, are dead ends that never reach
`organization`); step 5's candidate set — types outside the tenant-scoped set that appear as
a **userset subject** (`Type#relation`) on some tenant-scoped type's relation — is **empty**,
because this store contains zero `Type#relation` entries anywhere. `system` reaches every
tenant through an arrow (`admin from system`, itself fed by the bare, singular
`organization.system: [system]` edge every organization tuple must declare), not through a
type-list userset entry, and step 5's candidate filter was built to catch only the latter
shape. Step 6 never runs on `system` at all; there is nothing to check it against.

**Result: `{}` flagged — correctly, verified live, but for a narrower reason than "the
algorithm recognized a legitimate super-root pattern."** The live probe (see "Final harness
run" above) confirms the silence happens to be right: writing a third tenant
(`organization:gamma`) that declares a **different** system root shows `system:global`'s
admin (`anne`) is correctly *unreachable* on `gamma` — the edge genuinely governs cross-tenant
reach, unlike `multitenant-rbac`'s `role`/`group`, which had no governing edge in either
direction. But the algorithm's own steps never established that governance; they never looked
at `system` at all, because step 5's net is scoped to userset-subject-in-type-list references
only. Confirmed directly: naively broadening step 5 to also treat arrow targets as candidates
would hand `system` to step 6, which would flag it (its own edges still never reach
`organization`) — a **false positive** on exactly the legitimate pattern this store
demonstrates is safe. The exemption such a broadening would need, now recorded in
`blockers.md` as an open sharpening item rather than applied speculatively: skip flagging an
arrow-target candidate `S` when the tenant root `T`'s own belongs-to edges already include a
bare edge to `S` — the shape that distinguishes a designed super-root (`organization -> 
system`, explicit and singular) from an ungoverned leak (no edge at all, either direction).
No corpus store yet has an arrow-fed cross-tenant path that is *also* ungoverned, so the
broadened algorithm is documented, not built — this store settles what the exemption would
need to say, not that step 5 must change today.

**2 — A caveat's or an expiration's temporal gate composes unchanged through nested arrows —
corpus-confirmed through two hops, the deepest indirection any caveat-bearing store has
exercised so far.**
*Classification: ambiguous guidance → worked example.*
*Changed: `references/schema-mapping.md` ("Temporal access…" section, new subsection).*

Every prior caveat-bearing corpus store put its condition at most one arrow hop from the
permission actually being checked, verified directly against each store's own committed
schema before writing this claim: `condition-data-types`, `temporal-access`, and
`advanced-entitlements` all bind the caveat directly on the checked relation (zero hops);
`ip-based-access`'s `document.can_view` arrows exactly one hop to
`organization.ip_based_access_policy__perm`, itself the caveated relation. `superadmin` is
the first to put the condition **two** hops away: `task.viewer` arrows to
`project.viewer`, which itself arrows to `organization.helpdesk_member__perm`, the relation
actually carrying the caveat (or, in the native-expiration alternative, the expiration
timestamp). Both encodings were verified live through the full two-hop chain — the caveat
form via `zed validate` and a live server (matching `fga model test`'s `Checks 8/8 passing`
exactly, including `employee:john`'s time-varying check), and native expiration via a
separately-deployed keyspace (a near-future-expiring `helpdesk_member` relationship checked
`true` through the two-hop `task.viewer` walk immediately after write, then `false` and
absent from every read path ~8 seconds later, before GC could have run). No new rule was
needed for either form: an arrow is a permission-graph walk, and a caveat or expiration
timestamp is evaluated once, at the relationship that actually carries it, regardless of how
many hops a check takes to reach it. Recorded as a confirmation extending the existing
"Temporal access" gate to a hop-count no prior store tested, not a new construct.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **8 of 8 (100%)**. But the
store's full oracle is 13 facts, not 8: three `list_objects` assertions and two `list_users`
assertions are silently dropped by the harness per the known gap documented above, so the
harness's own view is **8/13 (62%)** of the store's total assertions.

As with every prior gap-carrying store except `github` — that is, `custom-roles`
(iteration 4), `temporal-access` (6), `multitenant-rbac` (7), `ip-based-access` (8), and
`advanced-entitlements` (9), per "The canonical store table" → **Check-only sources** and its
complementary seven — this gap was closed by direct verification rather than left as an
acknowledged blind spot (see "Final harness run" above)
[correction, iteration 11 consolidation pass: this read "every caveat/gate-decision store
since `custom-roles`", which names a set that does not exist — `custom-roles` and
`multitenant-rbac` are neither caveat-bearing nor gate stores yet both closed their gaps, and
`abac-with-rebac` (iteration 5) falls in that span with no gap to close at all]:
all five dropped assertions were run live via `LookupResources`/`LookupSubjects` and matched
the source oracle exactly, including the negative half a `check:` block cannot express on its
own (the `user`-filtered `list_users` call correctly excludes the system-management-app
subject, which is a full admin but not of type `user`).

Beyond the known gap, this store's own test design never asserts a negative against a second
tenant, a differently-scoped system root, or an unrelated subject at all — the source has
exactly one tenant (`acme`) and one system (`global`). Every cross-tenant probe result in
Findings 1-2 and "Final harness run" — the entire substance of why this store was chosen —
required fabricating a second and third tenant by hand; no rerun of the harness against this
store's own `store.fga.yaml`, however the converted schema is probed, could ever produce
them.

---

## `groups-resource-attributes`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 1/1 passing,
Checks 5/5 passing` (one of the five checks is a byte-for-byte duplicate of another —
`user:bob`/`status: published` appears twice with the identical expectation — so the store
carries only 4 distinct `(subject, permission, context)` questions).

This store's own `README.md` frames its use case precisely: "members of specific groups can
access content depending on resource attributes" (marketing sees only `published` documents,
content sees `draft` and `published`). It is the store this iteration was chosen for on two
stated grounds: it is the corpus's first to use a container caveat parameter **structurally**
— indexing a `map<string>` and membership-testing a `list<string>`, composed across two
parameters rather than exercised in isolation the way `condition-data-types` exercised each —
and it is the corpus's first caveat-bearing store whose per-check-supplied parameter
(`document_attributes`, specifically its `"status"` key) is an ordinary **resource attribute**
of the kind that lives in a customer's own database as a column. Checked against all five
prior caveat-bearing stores (`grep -c '^caveat ' */schema.zed`), not a recalled subset: none
supplies that shape — wall-clock time in `temporal-access` (iteration 6) and `superadmin`
(10), network locality in `ip-based-access` (8), a usage counter in `advanced-entitlements`
(9), and synthetic per-type probe values in `condition-data-types` (2), which exercises the
parameter *types* rather than any real-world attribute domain. The task also named it as the corpus's plausible candidate for the one
branch `ip-based-access`'s own gate decision left open (`schema-mapping.md`'s "Deliberately
not written yet": "a genuinely enumerable, low-cardinality attribute where all three
[benchmark] forms apply in their literal shape and the relation-name encoding is a live
contender") — see Finding 2.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 5 relationships
loaded, 4 assertions run. Also clean under `--fail-on-warn`. Negative-control-verified per
this file's standing method: flipping `document:1#can_access@user:bob with
{"document_attributes":{"status":"published"}}` from `assertTrue` to `assertFalse` fails
`zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing exactly which hop fails: `group:content member` — ✕,
`group:marketing member` — ✓ for `user:bob`, correctly walking to `true` regardless of the
edited expectation); deleting the `document:1#can_access@user:anne with
{"document_attributes":{"status":"draft"}}` assertion makes the harness report `MISSING`
(exit 1, `expected=True`).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because this store's whole point is (a) a
previously-unverified container-caveat runtime behavior and (b) an encoding-choice
recommendation neither the harness nor `zed validate` can settle on its own:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag. All 5
  relationships loaded via `WriteRelationships`, and all **4** distinct check-block
  assertions match exactly (`zed permission check ... --caveat-context
  '{"document_attributes":{"status":"..."}}'`), matching `fga model test`'s own
  `Checks 5/5 passing` (the 5th being the exact duplicate noted above).
- `LookupResources`/`LookupSubjects` were run for completeness even though this store's own
  source has no `list_objects`/`list_users` test to compare against (see "What the harness
  could not see") — `LookupSubjects(document:1, can_access, user)` returns exactly `{anne}`
  for `status: "draft"` context and exactly `{anne, bob}` for `status: "published"`, matching
  the check-level results.
- **The container-caveat edge case (Finding 1's decisive evidence):** a check with no context
  at all returns `caveated`, matching the given fact. A check supplying
  `document_attributes` as a **present but empty map** (`{}`), or as a map missing the
  `"status"` key, does **not** return `caveated` or `false` — it returns a hard RPC error,
  `InvalidArgument: evaluation error for caveat doc_viewer_condition: no such key: status`.
  Verified this is inherited from the source, not introduced by the conversion: an ad hoc
  `.fga.yaml` fixture reproducing the identical empty-map input against `fga model test`
  fails identically (`Checks 0/1 passing`, ``failed to evaluate condition expression: no such
  key: status``). A guarded rewrite (`"status" in document_attributes && ...`) was built and
  verified to return a clean `false` for the same input with the happy path unchanged.
- **The gate decision (Finding 2's decisive evidence):** a full relation-name/materialized
  alternative (`corpus-runs/groups-resource-attributes/schema-materialized-marker.zed`,
  reusing `abac-with-rebac`'s own self-loop marker pattern for `document.draft`/
  `document.published`) was deployed to a separate keyspace and verified to reproduce all
  four of the source's checks with **zero** `--caveat-context` supplied at all. The same
  deployment also demonstrated, live, the fail-closed window inherent to that alternative:
  deleting the `draft` marker and, before writing the `published` marker, checking
  `document:1 can_access user:anne` (a subject with permanent, unconditional access to both
  states via her group) returns `false` mid-transition — see Finding 2.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all four types | schema-mapping construct table |
| Pure-direct relation, no split (bare type list, no operator) | `organization.member`, `group.organization`, `group.member`, `document.organization` | split rule, final bullet |
| Caveat on a **userset**-typed subject reference, single entry, no split | `organization.can_access_docs: [group#member with doc_viewer_condition]` | "A userset subject may point at a split permission" row + construct table row 33 (`ip-based-access`) — confirms, no new rule |
| Arrow, operand order reversed, into a bare (unsplit) relation, needing a `__perm` alias | `document.can_access` (`can_access_docs from organization` → `organization->can_access_docs__perm`) | "Point arrows at permissions, not relations" — alias branch |
| `condition c(p: map<string>, q: list<string>) { ... }` → `caveat c(p map<string>, q list<string>) { ... }` | `doc_viewer_condition` | "Caveat parameter types and expression bodies" (confirms, both types already covered) |
| **A `map<T>` value indexed by a literal key, tested via `in` against a *different* `list<T>` parameter** — container types composed across parameters, not exercised in isolation | `document_attributes["status"] in allowed_statuses` | **new: "A missing map key is a hard evaluation error..."** |
| Caveat context split across write time (`allowed_statuses`, bound per group) and check time (`document_attributes`, supplied per check) | every `can_access` check | SpiceDB merges both natively — same mechanism established by `temporal-access`/`ip-based-access`/`advanced-entitlements`, confirmed for a 4th, plain-resource-attribute domain. Ranked in "The canonical store table" → **Split-context caveat family**: this store is the **fifth store** in that family but only the **fourth domain**, because `superadmin` (iteration 10) re-uses `temporal-access`'s wall-clock domain rather than adding one. The domain key is the one this claim ranks on |
| **Resource attributes: caveat vs. relation-name encoding, with relation-name finally buildable** | the whole store's reason for being selected | **new: "Resource attributes: caveat vs. relation-name encoding"** |

**Not exercised:** wildcards (no `:*` anywhere), the relation/permission split (no `define`
here mixes a type list with an operator — the **sixth** corpus store with no split anywhere at
all. Metric, command, full output and sorted membership are in "The canonical store table" →
**No-split lineage**; ranked by iteration, that set is `condition-data-types` (iteration 2,
rank 1), `abac-with-rebac` (iteration 5, rank 2), `temporal-access` (iteration 6, rank 3),
`ip-based-access` (iteration 8, rank 4), `advanced-entitlements` (iteration 9, rank 5),
**this store (iteration 11, rank 6)** — six total, so this store is sixth, not fifth
[correction, iteration 11 fix round 2: `abac-with-rebac` was omitted from
this list in two prior drafts, both times because the check re-verified only the
already-named predecessors instead of grepping all ten stores fresh. Iteration 11
consolidation pass: the bare parenthesized numbers this note used to carry are unlabeled
iteration indices — now written out as `iteration N`, and the derivation now lives in one
place instead of being restated per store]), runtime-
defined roles, modular models, multi-store tenancy, model-ID pinning, contextual tuples,
`list_objects`/`list_users` (this store's baseline has neither). `group.organization` is
declared in the schema and carries no relationship in the source's own `tuples:` block — worth
noting, it is also never referenced by any permission expression anywhere in the model, so it
is inert by design, not merely untested (the model's tenant edge runs `document.organization`,
never through `group`). `id_encoding` is `none` — `acme`, `anne`, `bob`, `content`,
`marketing`, `1` are all already inside SpiceDB's charset.

### Findings

**1 — A missing map key surfaces as a hard evaluation error, not `caveated` or `false`, and
this is inherited from OpenFGA, not introduced by the conversion.**
*Classification: missing conversion rule → `schema-mapping.md`.*
*Changed: `references/schema-mapping.md` (new "A missing map key is a hard evaluation error..."
subsection under "Caveat parameter types and expression bodies").*

`document_attributes["status"] in allowed_statuses` composes two container-parameter
behaviors `condition-data-types` had only verified individually (map indexing, list
membership), each against its own variable. The composition itself needed no new rule — it
carries over unchanged, confirmed live for all four checks. What was previously unverified is
what happens when the indexed key is absent: the given fact "checking with no context yields a
caveated result, not an error" turns out to hold only when the **entire parameter** is
missing from context. Once `document_attributes` is present in any form — even `{}` — indexing
it by `"status"` when that key is absent is a hard RPC error
(`InvalidArgument: ... no such key: status`), not `caveated` and not `false`. Verified this is
not a SpiceDB-specific fragility: an ad hoc `.fga.yaml` fixture supplying the identical
`{"document_attributes": {}}` input to `fga model test` fails identically (`Checks 0/1
passing`, ``failed to evaluate condition expression: no such key: status``) — the unguarded
index is OpenFGA's own condition body, carried over unchanged, and both systems fail the same
way on the same incomplete input. A production caller whose resource record lacks the indexed
key gets an RPC error out of `Check`, not a boolean, in both systems alike. A guarded rewrite
(`"status" in document_attributes && document_attributes["status"] in allowed_statuses`, the
same defensive idiom `condition-data-types`' own `is_valid_map_string` already uses) was built
and verified to return a clean `false` for the missing-key case with the happy path unchanged
— recorded as a recommended hardening, not a required rewrite, since `schema.zed` keeps the
literal unguarded form to match the source exactly.

**2 — Resource attributes are a fourth Class B gate decision (ranked in "The canonical store
table" → **Class B gate decisions**: `temporal-access` gate 1, `ip-based-access` gate 2,
`advanced-entitlements` gate 3, this store gate 4), resolved the same direction as
`ip-based-access`/`advanced-entitlements` for the same stated principle — this store builds
the rejected alternative live and commits it (`ip-based-access` also built and live-verified
its own materialized-marker alternative but committed no artifact for it; what it ruled out
*on paper* was the separate relation-name encoding, for lack of an enumerable vocabulary.
`advanced-entitlements` set the precedent of committing a materialized-marker artifact, which
this store matches, not exceeds), and it is
the first to build and exercise a concrete sync obligation end to end, including a live
fail-closed-window transition.**
[correction, iteration 11 consolidation pass: this read "unlike `ip-based-access`, which
ruled its own alternative out on paper and committed nothing" — false, and contradicted by
`ip-based-access`'s own section in this file, whose "Final harness run" records the
`network_verified_member` / `can_view_verified` marker built and verified on a live server.
The two alternatives that store considered were conflated: the marker was built but not
committed; the relation-name encoding was ruled out on paper.]
*Classification: ambiguous guidance → worked example (an explicitly flagged open "encoding
choice," resolving the branch `ip-based-access`'s own gate decision left open).*
*Changed: `references/schema-mapping.md` (new "Resource attributes: caveat vs. relation-name
encoding" section, construct table row, "Deliberately not written yet" resolution).*

`ip-based-access` ruled out relation-name encoding because its own attribute (`cidr`) had no
small, enumerable vocabulary. This store's attribute does — every value any
`allowed_statuses` list or any check's `document_attributes.status` ever takes is one of
exactly two strings, `"draft"`/`"published"`, the identical two-valued category
`abac-with-rebac` already models as a plain relation, per its own `README.md`'s stated
guidance ("if you can model your attribute as a relation, you should"). Built and verified
live (`corpus-runs/groups-resource-attributes/schema-materialized-marker.zed`, reusing
`abac-with-rebac`'s self-loop marker pattern): all four checks reproduce exactly, with **zero**
caveat context needed at all. Enumerability alone does not flip the recommendation, though:
this store's own test design proves the attribute is genuinely per-request, not persisted —
its `tests:` block checks the **same object**, `document:1`, at **two different status
values** within one assertion set, which the caveat form answers for free (context swap, zero
writes) and the materialized form cannot (the marker is either `draft` or `published`, never
both, so reproducing the source's own two checks against the same document requires an actual
write between them). That write is where the sync obligation becomes concrete rather than
hypothetical — verified live, deleting the `draft` marker and checking *before* writing the
`published` marker returns `false` for a subject (`anne`) with unconditional access to both
states via her group membership, a real fail-closed window, not a theoretical one. This is
exactly **one** sync obligation by `pack-contract.md` item 6's count (one source-of-truth
attribute, document status, needing permanent replication if materialized) — the corpus's
first sync obligation built and exercised end to end (write path, backfill, reconciliation,
fail-closed window all named against a live demonstration) rather than described only in the
abstract. The gate still resolves toward caveat as the recommended default, for the identical
stated principle `ip-based-access`/`advanced-entitlements` already established (no first-class
SpiceDB structural feature for a value read fresh per request) — reinforced here, not
overridden, by the fact that the source model's own author already left `document_attributes`
unbound in every tuple while binding `allowed_statuses` at write time, the same caveat/relation
split this pack's gate decision independently arrives at.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **4 of 4 (100%)** — this store's
`tests:` entry is entirely `check:` blocks, so unlike the **seven** stores that do carry a
`list_objects`/`list_users` gap — `github` (iteration 1), `custom-roles` (4),
`temporal-access` (6), `multitenant-rbac` (7), `ip-based-access` (8),
`advanced-entitlements` (9), `superadmin` (10), per "The canonical store table" →
**Check-only sources** — there is no silently-dropped assertion type to close here. This
store is the **fourth** check-only source, joining `condition-data-types` (iteration 2),
`modular` (3 — check-only across all four of its `.fga.yaml` files, not just the canonical
`store.fga.yaml` run), and `abac-with-rebac` (5), with nothing for the known harness gap to
hide
[correction, iteration 11 consolidation pass: this read "unlike every store since
`custom-roles`" followed by a five-store list. The quantifier is false and self-contradicting
— `abac-with-rebac` (iteration 5) falls in that span and is check-only, as the very next
clause of the same sentence already said — and the list also silently dropped `github` and
`custom-roles`, both gap-carrying. Replaced with the full seven-store set from the canonical
table.]. The store's own oracle is genuinely
thin along a different axis instead: only 4 distinct questions total (2 subjects × 2 status
values, with `resource` and `context`'s only free variable being `status`), no negative
control against a third group or a status value outside either group's `allowed_statuses`
(`"archived"`, tested only in this section's own live-server probing, not in the source), and
`group.organization` (see "Not exercised") is never touched by any assertion or, more
notably, by any permission expression in the model at all.

---

# Batch 1

Four stores converted in one pass rather than one per iteration, per the batch-hardening
brief: the ten hardest stores (`github` through `groups-resource-attributes`) were already
converted and the mapping rules had stabilized, so the expectation going in was that most
remaining stores are domain variants needing no new rules. That expectation mostly held —
three of the four below are genuine zeros, and the fourth (`ads`) forced only a routine
application of an existing, already-documented rule (the object-name 3-character minimum)
that no prior corpus type name happened to trigger. Each store still gets its own full
section below, in the same format every prior iteration uses, and the canonical table above
already carries all four stores' rows and derived-set memberships — this section's prose
cites that table rather than re-deriving membership counts inline.

---

## `accounting`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 15/15 passing,
Checks 51/51 passing, ListObjects 12/12 passing, ListUsers 4/4 passing`.

This store is a straightforward multi-level RBAC hierarchy (`admin ⊇ accountant ⊇ auditor ⊇
member`, each level a `[user]` type list unioned with the level below) applied across nine
resource types, all reached from one `organization` tenant root via a bare `organization`
relation and a `from`-arrow. It is the batch's least novel store by design — every construct
it uses (the split, the `__perm` arrow alias, chained implication) was already established by
`github`'s worked example and confirmed since. It converted clean on the **first attempt**,
using only rules already on file.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 18
relationships loaded, 51 assertions run. Also clean under `--fail-on-warn`, on both
`schema.zed` alone and the full `validation.yaml`. Negative-control-verified per this file's
standing method: flipping `account:revenue#can_view@user:alice` to `assertFalse` fails `zed
validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own explanation
trace showing the full walk: `can_view -> auditor -> accountant -> admin -> user:alice`);
deleting the `journal_entry:je-001#can_post@user:alice` assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server, because the harness's
own coverage of this store's oracle is well short of complete (see "What the harness could
not see"):

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 18 relationships loaded via `WriteRelationships`, all 51 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 51/51 passing` one for one.
- All **12** `list_objects` assertions and all **4** `list_users` assertions — silently
  dropped by the harness, see "Known harness gaps" above — were independently run via
  `LookupResources`/`LookupSubjects` and matched the source oracle **exactly**, including the
  negative half a `check:` block cannot express on its own (e.g. `invoice:inv-001`'s
  `can_view`/`can_edit` subject sets via `LookupSubjects` contain exactly the three/two
  expected users, no more). This store's full oracle is **67/67 confirmed** (51 check + 12
  list_objects + 4 list_users), not just the 51/67 (76.1%) the harness itself can see —
  ranked in "The canonical store table" → **Harness-visible fraction**.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all ten types | schema-mapping construct table |
| Pure-direct relation, no split (bare type list, no operator) | `organization.admin`, and every resource type's `organization`/`creator`/`submitter`/`requester`/`approver`/`owner` relation | split rule, final bullet |
| **Relation/permission split, three-deep chained implication** | `organization.accountant`, `.auditor`, `.member` (`[user] or <weaker-role>` at each level) | "The relation/permission split", "Watch the direction of implication" — the same chained-role shape `github`'s worked example established (`admin ⊆ maintainer ⊆ writer ⊆ triager ⊆ reader`), here three levels instead of five and on an `organization` rather than a `repo` |
| Arrow, operand order reversed, into a **split permission** (no `__perm` alias) | `account.can_edit` (`accountant from organization`), and every other `<resource>.can_edit`/`can_approve`/`can_view` that arrows to `accountant`/`auditor` | "Arrows"; "Point arrows at permissions, not relations" — no-alias branch, since `accountant`/`auditor` are already permissions via the split |
| Arrow into a **bare (unsplit) relation**, needing a `__perm` alias | `invoice.can_void`/`journal_entry.can_post`/`purchase_order.can_approve`/`financial_statement.can_edit`, all `admin from organization` (`organization.admin` has no operator, stays a bare relation) | "Point arrows at permissions, not relations" — alias branch |
| Arrow onto a bare relation on a **third** type via a stored relation reference | `invoice.can_view`'s `owner from contact` (`invoice.contact: [contact]`, `contact.owner: [user]` bare) | "Point arrows at permissions, not relations" — alias branch, confirms the rule applies identically when the tupleset relation's target type is neither the checked type nor the tenant root |

**Not exercised:** wildcards, caveats/conditions, modular models, runtime-defined roles,
multi-store tenancy, model-ID pinning, contextual tuples, intersection. `id_encoding` is
`none` — every object ID (`acme`, `revenue`, `vendor-abc`, `inv-001`, `balance-sheet-q1`, ...)
is already inside SpiceDB's charset, hyphens included. `invoice.contact` is declared and
referenced by `can_view`'s arrow, but the source's own `tuples:` never populates it and no
test exercises that specific path — inert by construction, the same shape
`groups-resource-attributes`' `group.organization` carries, noted there as "never touched by
any assertion."

### Findings

None. Every construct this store exercises was already covered by an existing rule before
this store was touched, the schema converted `PARITY OK` on the first attempt with no
fix-and-rerun cycle, and both negative controls and the full live-server list_objects/
list_users sweep behaved exactly as the pack predicts. This is a genuine zero, not an
unexamined one — see "What the harness could not see" for the extent of the post-green
inspection this conclusion rests on.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **51 of 51 (100%)**. But the
store's full oracle is 67 facts, not 51: three `list_objects` test entries (2 assertions
each, 12 total expected objects) and two `list_users` test entries (2 assertions each, 4
total expected-subject-set assertions) are silently dropped by the harness per the known gap
documented above, so the harness's own view is **51/67 (76.1%)** of the store's total
assertions.

This gap was closed by direct verification, not left as an acknowledged blind spot (see
"Final harness run" above): every dropped `list_objects`/`list_users` assertion was run live
via `LookupResources`/`LookupSubjects` and matched the source oracle exactly, including the
negative half a `check:` block cannot express (e.g. diana, a plain `member` with no
accountant/auditor role, correctly returns an empty `LookupResources` set for every
`can_edit`-gated type she was never individually granted access to).

Beyond the known gap: this store's own test design never asserts a negative against a second
organization — the source has exactly one, `acme` — so, as with `multitenant-rbac` and
`superadmin`, nothing in a rerun of the harness against this store's own `store.fga.yaml`
could ever probe cross-tenant reach. This store was not chosen to stress that property (see
"The canonical store table" → **Class B gate** column, which carries no entry for it — the
store's own reason for being in this batch was breadth of the split/arrow/chained-implication
rules already on file, not a new encoding question), so no second-tenant probe was performed
here; it would reproduce `multitenant-rbac`'s and `superadmin`'s already-established result on
a schema with no tenancy-specific construct of its own (`organization` is referenced by every
resource type through a plain, single-valued `organization: [organization]` relation, the
same shape those two stores already settled).

---

## `ads`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 14/14 passing,
Checks 84/84 passing, ListObjects 10/10 passing, ListUsers 6/6 passing`.

This store is an advertising-platform RBAC model with a deep arrow chain: `ad.can_delete`/
`can_view` reach `organization.admin`/`analyst` through **three** arrow hops each (`ad ->
ad_group -> campaign -> organization`), every intermediate hop landing on a pure-alias
permission (no type list, no operator) rather than a relation. (Corrected: an earlier draft
called this "the corpus's deepest plain (non-caveat) arrow chain" — false even within this
same iteration: `applicant-tracking-system`, converted in the same batch, reaches 5 hops with
no caveat either; see "The canonical store table" → **Arrow-chain hop depth** for the
authoritative ranking, mechanically derived across all 39 stores.) This is a greater-depth
confirmation of `abac-with-rebac`'s "arrow chained through another arrow-derived permission"
rule, not a new construct.

**Baseline hazard caught before conversion, not by the harness.** `type ad` is a genuine
2-character OpenFGA type name (`ad:banner-001` is the store's own object naming), and
translating it verbatim (`definition ad {}`) fails `zed validate` outright:
``invalid NamespaceDefinition.Name: value does not match regex pattern`` — SpiceDB's name
regex requires a 3-character minimum. `naming-normalization.md`'s algorithm already has a rule
for this (step 8, "while shorter than 3 characters, append `0`"), demonstrated in that file's
own worked-outputs table only synthetically (`u` → `u00`) until now. Applying it mechanically
(`ad` → `ad0`) resolves the failure with no further change, confirmed end to end via `zed
validate --fail-on-warn` and a first-attempt `PARITY OK` on the renamed schema. Mechanically
confirmed as the only such case in the corpus (checked against every other store's own model
source, not just the ten already converted — see `naming-normalization.md`'s new note for the
command and full output): no other corpus store has a type name under 3 characters.

**Final harness run:** `PARITY OK`, exit **0**, first attempt (after the rename above, which
happened before the first harness invocation, not as a fix-and-rerun cycle). `zed validate`:
14 relationships loaded, 75 assertions run. Also clean under `--fail-on-warn`. The store's
own `Checks 84/84` collapses to **75 distinct** `(subject, permission, resource)` keys once
deduplicated — three of its fourteen `tests:` entries (`Ad approval workflow`, `Campaign
publishing`, `Creative ownership`) each re-assert three checks byte-for-byte identical to
assertions already made in earlier entries, the same kind of exact duplicate
`groups-resource-attributes` noted for a single check. Negative-control-verified per this
file's standing method: flipping `ad_group:us-targeting#can_view@user:erin` to `assertFalse`
fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own
explanation trace showing the walk: `can_view -> can_edit -> owner -> user:erin`); deleting
the `ad0:banner-001#can_approve@user:alice` assertion makes the harness report `MISSING`
(exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` (post-rename) unedited, no compile step, no `use` flag.
- All 14 relationships loaded via `WriteRelationships`, all 75 distinct check-block assertions
  match exactly.
- All **10** `list_objects` assertions and all **6** `list_users` assertions — silently
  dropped by the harness — were independently run via `LookupResources`/`LookupSubjects` and
  matched the source oracle **exactly**, including the negative half (e.g. `charlie`'s
  `campaign`/`creative` `LookupResources` sets contain only `summer-sale`/`video-spot`, never
  the ad-group- or ad-level objects he can only view, never edit). This store's full oracle is
  **91/91 confirmed** (75 distinct check + 10 list_objects + 6 list_users).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all six non-`ad` types, plus `ad` → `ad0` | schema-mapping construct table |
| **3-character type-name minimum, first live corpus instance** | `type ad` → `definition ad0` | naming-normalization algorithm rule 8 (mechanical application of an existing rule; see `naming-normalization.md`'s new note) |
| Relation/permission split, 4-way union at one level | `organization.member: [user] or admin or campaign_manager or analyst` | "The relation/permission split"; "Always fully parenthesize" ("a chain of one operator is one group", confirmed at 4 terms) |
| Pure-direct relation, no split | `organization.admin`/`campaign_manager`/`analyst`, every resource type's `organization`/`owner`/`creator` relation | split rule, final bullet |
| Arrow into a bare relation, needing a `__perm` alias | `campaign.organization_admin`/`organization_campaign_manager`/`organization_analyst` (`X from organization`), `creative`/`report`'s equivalents | "Point arrows at permissions, not relations" — alias branch |
| **Arrow chained through another arrow-derived permission, three hops deep** | `ad.can_delete`/`.can_view` → `ad_group.organization_admin`/`.organization_analyst` → `campaign.organization_admin`/`.organization_analyst` → `organization.admin__perm`/`.analyst__perm` | "Arrows"; "A self-referential arrow… needs no special rule" generalizes — confirms `abac-with-rebac`'s nested-arrow rule at greater depth than any prior plain (non-caveat) chain, no new rule |
| Arrow into an already-permission target (pure-alias, no list), no alias needed | `ad_group.can_edit`'s `campaign->can_edit`; `ad.can_edit`'s `ad_group->can_edit` | "Point arrows at permissions, not relations" — no-alias branch, the "no type list at all" variant rather than the split variant |

**Not exercised:** wildcards, caveats/conditions, modular models, runtime-defined roles,
multi-store tenancy, model-ID pinning, contextual tuples, intersection. `id_encoding` is
`none` — every object ID (`adtech`, `summer-sale`, `us-targeting`, `video-spot`,
`q1-performance`) is already inside SpiceDB's charset; `ad0:banner-001`'s *type* needed the
rename above, but its object ID (`banner-001`) did not.

### Findings

**1 — The object-name 3-character minimum, corpus-confirmed for the first time on a real
type name.**
*Classification: ambiguous guidance → worked example (the rule already existed; no prior
corpus store had exercised it against a real, non-synthetic identifier).*
*Changed: `references/naming-normalization.md` (new note after the worked-outputs table).*

`naming-normalization.md`'s algorithm has always specified the 3-character floor (rule 8,
"while shorter than 3 characters, append `0`"), but every row in that file's own worked-
outputs table was produced by running the implementation against a synthetic or hand-built
input (`u` → `u00`), never a real OpenFGA type name from a corpus store. `ads`' own `type ad`
is a genuine 2-character type name matching the store's own `ad:banner-001` object naming,
and translating it verbatim fails `zed validate` outright. Applying the existing rule
mechanically (`ad` → `ad0`) resolves it with no further change, verified end to end (`zed
validate --fail-on-warn`, then a first-attempt `PARITY OK`). No new rule was needed — the
existing algorithm already covered this shape — but the corpus had never confirmed it against
a live model before, and mechanically checking every other store's source model (not just the
ten already converted) confirms `ads` is the *only* corpus store this floor fires on.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **75 of 75 distinct assertions
(100%)** — the harness silently dedupes the three exact-repeat `tests:` entries noted above,
the same behavior `groups-resource-attributes`' single duplicate exercised at smaller scale.
But the store's full oracle is 91 distinct facts, not 75: ten `list_objects` and six
`list_users` assertions are silently dropped by the harness per the known gap documented
above, so the harness's own view is **75/91 (82.4%)** of the store's distinct total
assertions — or, counting `fga model test`'s own raw `Checks/ListObjects/ListUsers` totals
without dedup (`84/100`), **84.0%**, the figure "The canonical store table" → **Harness-
visible fraction** carries, for consistency with how that column is computed for every other
store.

This gap was closed by direct verification (see "Final harness run" above): every dropped
`list_objects`/`list_users` assertion was run live and matched the source oracle exactly.
Beyond the known gap: this store's own test design never asserts a negative against a second
organization, so cross-tenant reach — the property `multitenant-rbac`/`superadmin` were
chosen to stress — is untested here and not part of what this store's evidence was gathered
to establish.

---

## `applicant-tracking-system`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 19/19 passing,
Checks 43/43 passing, ListObjects 13/13 passing, ListUsers 6/6 passing`.

This store is a ten-type hiring-workflow model with the corpus's deepest arrow chain of any
kind: `offer.can_approve` and `scheduled_interview.organization_admin` each reach
`organization.admin__perm` through **four** arrow hops (`offer -> application -> job ->
department -> organization`, or `scheduled_interview -> application -> job -> department ->
organization`), each intermediate hop again landing on a pure-alias permission -- but the
store's actual deepest chain is one hop deeper still: `scorecard.can_view` arrows *into*
`scheduled_interview.organization_admin` before that permission's own four-hop walk even
begins, reaching `organization.admin__perm` through **five** hops total (`scorecard ->
scheduled_interview -> application -> job -> department -> organization`). Mechanically
derived (method, command, and full output for every corpus store: "The canonical store
table" → **Arrow-chain hop depth**) -- this is the corpus's deepest chain of any kind, at 5
hops, not 4. It also exercises a shape no prior store's arrows combined: one permission
(`application.can_view`) unioning **two different arrows from two different tupleset
relations** (`can_view from job or can_view from candidate`), both targeting a permission
named `can_view` on their respective (different) types.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 24
relationships loaded, 43 assertions run. Also clean under `--fail-on-warn`. Negative-control-
verified per this file's standing method: flipping `offer:diana-offer#can_approve@user:alice`
to `assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the full four-hop walk: `can_approve ->
organization_admin -> organization_admin (application) -> organization_admin (job) ->
organization_admin (department) -> admin__perm -> admin -> user:alice`); deleting the
`scorecard:charlie-feedback#can_edit@user:charlie` assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 24 relationships loaded via `WriteRelationships`, all 43 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 43/43 passing` one for one.
- All **13** `list_objects` assertions and all **6** `list_users` assertions — silently
  dropped by the harness — were independently run via `LookupResources`/`LookupSubjects` and
  matched the source oracle **exactly**, including the negative half (e.g. `alice`'s
  `candidate` `LookupResources` set via the admin path returns exactly `{diana, eve}`, no
  fewer and no more). This store's full oracle is **62/62 confirmed** (43 check + 13
  list_objects + 6 list_users).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all ten types (`job`, at exactly 3 characters, needed no rename — see "Not exercised") | schema-mapping construct table |
| Relation/permission split, 4-way union | `organization.member: [user] or admin or recruiter or hiring_manager` | "The relation/permission split" (confirms, same shape as `ads`' `organization.member`) |
| Pure-direct relation, no split | every type's `organization`/`head`/`hiring_manager`/`recruiter`/`subject`/`organizer`/`interviewer`/`creator` relation, and the tenant-edge relations (`department.organization`, `job.department`, `application.job`/`candidate`, `office.organization`, `scorecard.interview`, etc.) | split rule, final bullet |
| Arrow into a bare relation, needing a `__perm` alias | `department.organization_admin`/`.organization_recruiter` (`X from organization`), `department.head__perm`, `candidate.can_edit`'s `organization->recruiter__perm`, `application.can_edit`/`.can_change_stage`'s `job->recruiter__perm`/`job->hiring_manager__perm`, `office.can_manage`'s `organization->admin__perm` (same shape as `department.organization_admin`) | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target (pure-alias, no list), no alias | `job.organization_admin`'s `department->organization_admin`; `application.organization_admin`/`.organization_recruiter`/`.department_head`'s arrows onto `job`'s and `department`'s equivalents; `scheduled_interview`/`offer`'s arrows onto `application`'s equivalents; `scorecard.can_view`'s arrows onto `scheduled_interview`'s `department_head`/`organization_admin` | "Point arrows at permissions, not relations" — no-alias branch |
| Arrow chain, four hops deep | `offer.can_approve` → `application.organization_admin` → `job.organization_admin` → `department.organization_admin` → `organization.admin__perm` | "Arrows" — confirms the nested-arrow rule at depth 4 |
| **Arrow chain, five hops deep — the corpus's deepest** | `scorecard.can_view` → `scheduled_interview.organization_admin` → `application.organization_admin` → `job.organization_admin` → `department.organization_admin` → `organization.admin__perm` | "Arrows" — confirms the nested-arrow rule at the corpus's deepest hop count; see "The canonical store table" → **Arrow-chain hop depth** for the metric, command, full output over every committed store, and the sorted list this ranking cites |
| **Union of two different arrows targeting identically-named permissions on two different types** | `application.can_view = (job->can_view + candidate->can_view)` | "Always fully parenthesize" (one source `or` node, one group — confirms the existing rule composes with two arrow operands instead of one, no new rule) |

**Not exercised:** wildcards, caveats/conditions, modular models, runtime-defined roles,
multi-store tenancy, model-ID pinning, contextual tuples, intersection. `id_encoding` is
`none` — every object ID (`acme`, `engineering`, `san-francisco`, `swe-senior`,
`diana-onsite`, `charlie-feedback`, `diana-offer`, ...) is already inside SpiceDB's charset,
hyphens included. `job`, at exactly 3 characters, is a negative control confirming the
3-character floor's boundary: it validates unchanged, unlike `ads`' 2-character `ad`.

### Findings

None. Every construct — including the two genuinely deep shapes this store's own design
forced (the five-hop arrow chain, the two-arrow union onto identically-named target
permissions) — resolved using rules already on file, with no ambiguity and no fix-and-rerun
cycle. The schema converted `PARITY OK` on the first attempt, both negative controls behaved
as the pack predicts, and the full live-server list_objects/list_users sweep confirmed the
harness's green run against 100% of the store's own oracle, not just the fraction the harness
itself can see.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **43 of 43 (100%)**. But the
store's full oracle is 62 facts, not 43: thirteen `list_objects` assertions and six
`list_users` assertions are silently dropped by the harness per the known gap documented
above, so the harness's own view is **43/62 (69.4%)** of the store's total assertions — the
thinnest fraction of any store in this batch, though still well above `ip-based-access`'s
50% minimum through batch 2 (superseded by `gdrive`'s 33.3% in batch 3; see "The canonical
store table" → **Harness-visible fraction**).

This gap was closed by direct verification (see "Final harness run" above), including the
negative half list_objects/list_users assertions carry and a `check:` block cannot: `eve`
(the sole `candidate` subject with a live account) is correctly excluded from `bob`'s and
`alice`'s own `LookupResources`/`LookupSubjects` sets wherever the source oracle excludes her.
Beyond the known gap: this store's own test design never asserts a negative against a second
organization, so — as with `accounting` and `ads` above — cross-tenant reach is untested here
and was not part of what this store was chosen to establish.

---

## `banking`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 2/2 passing,
Checks 5/5 passing`. This store carries its model as an inline `model:` block in
`store.fga.yaml` (the third form this pack's stores use, alongside a separate `model.fga` file
and, for `modular`, an `fga.mod` manifest) rather than a separate `.fga` file.

This store is a two-type banking-transfer model (`bank`, `account`) whose single permission,
`can_make_bank_transfer`, combines a 3-way union with an intersection against a caveated
userset — `(owner or account_manager or delegate) and transfer_limit_policy from bank` — and
whose one caveat, `transfer_limit_policy`, is attached to **two different userset-typed
subject references on the same relation**
(`bank#customer with transfer_limit_policy | bank#account_manager with transfer_limit_policy`),
each independently bound with its own limit at write time. It is the batch's only
caveat-bearing store and, per "The canonical store table" → **Split-context caveat family**,
re-uses `advanced-entitlements`' usage-counter domain (a request-supplied
`transaction_amount` checked against a write-time-bound `transaction_limit`) rather than
forcing a new one — the same "apply an existing gate" resolution `superadmin` already
established for the wall-clock domain.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 9 relationships
loaded, 5 assertions run. Also clean under `--fail-on-warn`. Negative-control-verified per
this file's standing method: flipping `account:123#can_make_bank_transfer@employee:bob with
{"transaction_amount":1000,"new_transaction_limit_approved":0}` from `assertTrue` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the full intersection walk — the caveat evaluating
`true` against bob's own `$1000` `account_manager`-path binding, correctly distinguished from
anne's separate `$100` `customer`-path binding); deleting the
`account:123#can_make_bank_transfer@customer:peter with {...}` assertion makes the harness
report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server, deliberately more
thoroughly than a green harness run requires, because this store's whole point (two userset
types sharing one caveat name, each independently bound) is exactly the kind of crosstalk risk
a boolean-only harness pass could miss:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 9 relationships loaded via `WriteRelationships`, all 5 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 5/5 passing` one for one.
- **Cross-binding isolation, probed directly.** Anne (bound at `$100` via `bank#customer`) and
  bob (bound at `$1000` via `bank#account_manager`) each resolve against their *own* binding
  only — verified live: anne's `$1000`-transaction check (`new_transaction_limit_approved:0`)
  correctly returns `false` even though bob's identical-amount check on the same account
  returns `true`, confirming the two userset-typed, identically-named-caveat entries never
  cross-satisfy each other.
- **A delegate with no bank-level binding at all correctly resolves `false`, not
  `caveated`, even with zero context supplied.** A third customer (`carol`), added as an
  `account:123` delegate but never granted `bank:acme#customer` or `#account_manager`, checks
  `false` on `can_make_bank_transfer` both with and without any `--caveat-context` — the
  intersection's right-hand arrow finds no userset membership for her at all, so no caveat is
  ever reached to evaluate, unlike a bound subject with merely *missing* context (which
  correctly returns `caveated`). This is standard SpiceDB caveat/intersection behavior, not a
  new rule — recorded because it is exactly the kind of over-permissiveness question a
  check-only oracle with only three subjects could not have exposed on its own.
- `LookupResources(account, can_make_bank_transfer, customer:anne, ...)` with matching context
  returns exactly `{123}`, confirming the caveated-intersection permission resolves correctly
  through `LookupResources` as well as `check` (no Materialize-eligibility claim is made here
  — per `ip-based-access`'s and `advanced-entitlements`' gate sections, a caveated permission
  is definitionally Materialize-ineligible regardless).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition`, incl. two empty types | `employee`, `customer` | schema-mapping construct table |
| Inline `model:` block in `store.fga.yaml` (no separate `.fga` file) | the whole store | model-discovery fact, not a mapping rule — third form this pack's stores use, alongside a separate `model.fga` file and `modular`'s `fga.mod` manifest |
| Pure-direct relation, no split | `bank.customer`/`.account_manager`, `account.bank`/`.owner`/`.account_manager`/`.delegate` | split rule, final bullet |
| **Caveat on two different userset-typed subject references on one relation, same caveat name, independently bound** | `bank.transfer_limit_policy: bank#customer with transfer_limit_policy \| bank#account_manager with transfer_limit_policy` | "A userset subject may point at a split permission" row + construct table row 33 (`ip-based-access`) — confirms the userset-with-condition shape generalizes to more than one userset type sharing one caveat name, no new rule; live cross-binding isolation probed above |
| Arrow into a bare relation, needing a `__perm` alias | `account.can_make_bank_transfer`'s `transfer_limit_policy from bank` | "Point arrows at permissions, not relations" — alias branch |
| **Intersection nesting a 3-way union as one operand** | `account.can_make_bank_transfer = ((owner + account_manager + delegate) & bank->transfer_limit_policy__perm)` | "Always fully parenthesize" (confirms — one parenthesized group per source node, applied recursively; this is the corpus's second use of intersection and the first to nest a union group inside it — see "The canonical store table" → **SpiceDB intersection (`&`)**) |
| `caveat`/`condition` with `double` parameters, `\|\|` in the body | `transfer_limit_policy(transaction_amount double, transaction_limit double, new_transaction_limit_approved double)` | "Caveat parameter types and expression bodies" (`double` already covered; `\|\|` carries over unchanged, matching "everything else in the expression bodies carried over unchanged") |
| Caveat context split across write time (`transaction_limit`, bound per userset) and check time (`transaction_amount`, `new_transaction_limit_approved`, both supplied per check) | every `can_make_bank_transfer` check | SpiceDB merges both natively — same mechanism established by `temporal-access`/`ip-based-access`/`advanced-entitlements`/`superadmin`; re-uses the usage-counter domain (gate 3), not a new domain — see "The canonical store table" → **Split-context caveat family** |
| **A caveat's own name reused as its carrying relation's name, in the same schema** | `relation transfer_limit_policy: ... with transfer_limit_policy`, `caveat transfer_limit_policy(...)` | **new: `naming-normalization.md`, "Two namespaces, not one -- and a caveat name is in neither of them"** |

**Not exercised:** wildcards, the relation/permission split (no `define` in this store mixes a
type list with an operator — `can_make_bank_transfer` has no type list at all), arrows onto an
already-split or pure-alias permission (its one arrow target, `transfer_limit_policy`, is a
bare relation), modular models, runtime-defined roles, multi-store tenancy, model-ID pinning,
contextual tuples, `list_objects`/`list_users` (this store's baseline has neither — the
**fifth** check-only source; see "The canonical store table" → **Check-only sources**).
`id_encoding` is `none` — `acme`, `anne`, `peter`, `bob`, `123` are all already inside
SpiceDB's charset.

### Findings

**1 — A caveat's own name and a relation's own name occupy separate SpiceDB namespaces, and a
schema may legally reuse one identifier for both — corpus-confirmed for the first time.**
*Classification: ambiguous guidance → worked example (`naming-normalization.md`'s "Two
namespaces, not one" framing was silent on where a caveat name's own collision domain sits,
which reads as though it might share one of the two stated namespaces).*
*Changed: `references/naming-normalization.md` ("What normalization does not do" → "1. It is
not collision-resistant", the "Two namespaces, not one" bullet).*

`banking`'s own OpenFGA source already does this: `define transfer_limit_policy: [... with
transfer_limit_policy]` and `condition transfer_limit_policy(...)` share one identifier for a
relation and its own governing condition. The mechanical translation carries this over
unchanged (`relation transfer_limit_policy: ...`, `caveat transfer_limit_policy(...)`),
verified to compile, deploy via `WriteSchema`, and resolve correctly with no collision error
of any kind. `naming-normalization.md`'s existing "Two namespaces, not one" bullet describes a
global type-name registry and a per-type relation/permission registry, but says nothing about
where a caveat name's own collision domain sits — reasonable to read as implying it shares one
of the two, since caveats, like definitions, are declared at the schema's top level. Verified
otherwise: SpiceDB keeps caveat names in their **own** global registry, distinct from both.
Mechanically confirmed as unprecedented in this corpus (`grep`-verified against every other
committed `schema.zed`, not assumed): no other store reuses a caveat name as a relation or
permission name. The practical consequence for a future translator: running a caveat name
through the same per-type disambiguation registry as that type's relations and permissions
would be a mistake — it is a separate batch collision check against the caveat registry alone.

### What the harness could not see

This store's `tests:` entries are entirely `check:` blocks (no `list_objects`/`list_users`
anywhere), so the harness's own coverage is **5 of 5 (100%)** — this is the batch's only
check-only source, and the corpus's **fifth** overall (joining `condition-data-types`,
`modular`, `abac-with-rebac`, `groups-resource-attributes`; see "The canonical store table" →
**Check-only sources**), so there is no silently-dropped assertion *type* to close here.

What a check-only, three-subject oracle cannot on its own rule out — and what this section's
live probing exists to close — is exactly the crosstalk and over-permissiveness risk this
store's own two-userset-one-caveat-name shape invites: whether anne's and bob's independently-
bound limits could bleed into each other, and whether a delegate with no bank-level binding at
all is silently treated as unconditionally denied, unconditionally permitted, or `caveated`.
All three were probed directly against a live server (see "Final harness run" above) and none
showed any sign of crosstalk. Beyond that: the store's own fixtures never test a subject who
is simultaneously a `bank#customer` *and* a `bank#account_manager` at once (which, per ordinary
arrow-union semantics, would resolve `true` if *either* binding's caveat passes, granting the
more generous of the two limits) — this was not probed live, since it is not a shape the
source model's own data exercises and inventing it would test a scenario the store was never
designed to represent, not verify a gap the conversion introduced.

---

## `calendar`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 12/12 passing,
Checks 71/71 passing, ListObjects 9/9 passing, ListUsers 4/4 passing`. Model carried in a
separate `model.fga` file.

This store is a seven-type calendar-platform RBAC model: `organization`
(`member: [user] or admin or scheduler or viewer`, a 4-way split matching the exact shape
`ads` and `applicant-tracking-system` already established) reached via a bare `organization`
relation and `from`-arrows by `calendar`, `event` (reached from `calendar`), `link`,
`recording` (reached from `event`), and `webinar`. No caveats/conditions anywhere in this
store.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 14
relationships loaded, 64 assertions run. Also clean under `--fail-on-warn`, on both
`schema.zed` alone and the full `validation.yaml`. The store's own `Checks 71/71` collapses to
**64 distinct** `(subject, permission, resource)` keys once deduplicated — the "Event
organizer can manage their events" and "Webinar publishing requires organizer or admin"
`tests:` entries each re-assert checks byte-for-byte identical to assertions already made
earlier in the file, the same exact-duplicate pattern `ads` and `groups-resource-attributes`
already established. Negative-control-verified per this file's standing method: flipping
`calendar:team-cal#can_view@user:alice` to `assertFalse` fails `zed validate` itself (exit 1
from `zed`, exit 2 from the harness, with `zed`'s own explanation trace showing the full walk:
`can_view -> can_edit -> can_share -> can_delete -> organization_admin -> organization:acme
admin__perm -> admin -> user:alice`, correctly failing on the `organization_viewer` and
`organization_scheduler` branches alice does not hold); deleting the same assertion makes the
harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 14 relationships loaded via `WriteRelationships`, all 64 distinct check-block assertions
  match exactly, matching `zed validate`'s own `64 assertions run` and the harness's own `64
  assertions compared` one for one.
- All **9** `list_objects` assertions and all **4** `list_users` assertions — silently dropped
  by the harness — were independently run via `LookupResources`/`LookupSubjects` and matched
  the source oracle **exactly**, including the negative half a `check:` block cannot express:
  `charlie` (a plain `viewer`) is correctly excluded from every `can_edit`-gated
  `LookupResources` set, and `link:booking-page`'s `LookupSubjects` for `can_view` returns only
  `{alice, bob}` — confirming `link` carries **no** organization-viewer path at all, unlike
  `calendar`/`event`/`webinar`, matching the source `README.md`'s own design note ("Viewers
  cannot access scheduling links") exactly. This store's full oracle is **84/84 confirmed** (71
  raw check + 9 list_objects + 4 list_users; 64 distinct check-block keys within that 71).
- No second-tenant probe: the source fixture has exactly one organization (`acme`), the same
  limitation `accounting`/`ads`/`applicant-tracking-system` already documented.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all seven types | schema-mapping construct table |
| Pure-direct relation, no split (bare type list) | `organization.admin`/`.scheduler`/`.viewer`, and every resource type's `organization`/`owner`/`organizer`/`attendee` relation | split rule, final bullet |
| **Relation/permission split, 4-way union** | `organization.member: [user] or admin or scheduler or viewer` | "The relation/permission split" — a corpus confirmation of this exact 4-way shape, joining `ads` and `applicant-tracking-system` (both iteration 12). No ordinal is claimed: `calendar` shares iteration 13 with `call-center` and `crm`, which independently carry the same shape (see their own sections), and the Iteration column's own definition disallows inferring a chronological order among same-iteration stores |
| Arrow into a bare relation, needing a `__perm` alias | `calendar.organization_admin`/`.organization_scheduler`/`.organization_viewer` (`X from organization`), and `link`/`webinar`'s direct arrows onto the same three aliases | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `event.organization_admin`/`.organization_scheduler`/`.organization_viewer` (`X from calendar`, landing on `calendar`'s own alias permissions); `recording.can_delete`/`.can_view`'s arrows onto `event`'s equivalents | "Point arrows at permissions, not relations" — no-alias branch |
| **3-hop arrow chain** | `recording.can_delete` → `event.organization_admin` → `calendar.organization_admin` → `organization.admin__perm` | "Arrows" — ties `ads`' and `superadmin`'s 3-hop depth, short of `applicant-tracking-system`'s corpus-deepest 5 hops; see "The canonical store table" → **Arrow-chain hop depth** |
| Bare permission-to-permission alias (no operator, no arrow) | `calendar.can_delete = organization_admin`, `.can_create_event = can_edit`; `event.can_edit = can_invite`; `link.can_view = can_edit` | construct table row `define view: viewer` → `permission view = viewer`; precedented by `accounting`'s `can_view = can_edit` |
| Fully parenthesized 2-/3-way unions | `calendar.can_share`/`.can_edit`/`.can_view`; `event.can_delete`/`.can_invite`/`.can_view`; `link.can_delete`/`.can_edit`; `webinar.can_publish`/`.can_edit`/`.can_view`; `recording.can_view` | "Always fully parenthesize" (`webinar.can_delete = organization->admin__perm` is a single-term arrow, not a union, and does not belong in this row — `schema.zed:63`; `recording.can_view = (event->organization_viewer + event->organization_scheduler + can_delete)`, `schema.zed:56`, is a genuine 3-way union and was omitted from an earlier draft of this row) |

**Not exercised:** wildcards, caveats/conditions, modular models, runtime-defined roles,
multi-store tenancy, model-ID pinning, contextual tuples, intersection. `id_encoding` is
`none` — every object ID (`acme`, `team-cal`, `standup`, `booking-page`, `standup-rec`,
`product-launch`) is already inside SpiceDB's charset. No type/relation/permission name is
under 3 characters (shortest are `user`/`link`/`event` at 4-5 chars). Cross-tenant isolation
untested — only one organization (`acme`) in the source fixture.

### Findings

None. Every construct this store exercises — the 4-way split, six `__perm` alias
declarations, arrows into bare relations vs. already-permission targets, a 3-hop arrow chain,
and repeated bare permission-to-permission aliases — was already covered by a rule on file
before this store was touched (confirmed by `grep`-checking every construct against every
prior corpus store's `schema.zed`, not assumed). The schema converted `PARITY OK` on the first
attempt, both negative controls behaved exactly as the pack predicts, and the full live-server
list_objects/list_users sweep — including the `link`-has-no-viewer-path negative check — found
no over-permissiveness. This is a genuine zero, in the same shape as `accounting`'s and
`applicant-tracking-system`'s.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **64 of 64 distinct assertions
(100%)** — the harness silently dedupes the seven exact-repeat checks noted above, the same
behavior `ads` and `groups-resource-attributes` already established. But the store's full
oracle by `fga model test`'s own raw counts is 84 assertions, not 71: 9 `list_objects` and 4
`list_users` assertions are silently dropped by the harness per the known gap documented
above, so the harness's own view is **71/84 (84.5%)** of the store's raw total assertions —
see "The canonical store table" → **Harness-visible fraction**.

This gap was closed by direct verification (see "Final harness run" above): every dropped
`list_objects`/`list_users` assertion was run live and matched the source oracle exactly,
including the `link` no-viewer-path negative and `recording`'s non-inheritance of
`event.attendee` access (diana attends `event:standup` but her `recording:standup-rec#can_view`
is `false`, confirmed both by `check` and by `LookupSubjects` excluding her).

---

## `call-center`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 12/12 passing,
Checks 46/46 passing, ListObjects 6/6 passing, ListUsers 2/2 passing`. Model carried in a
separate `model.fga` file.

This store is a six-type call-center RBAC model: `organization`
(`member: [user] or admin or supervisor or agent`, the same 4-way split shape as `ads`,
`applicant-tracking-system`, and this batch's own `calendar`) reached via a bare
`organization` relation and `from`-arrows by `call`, `contact`, `comment` (reached from
`call`), and `recording` (reached from `call`). No caveats/conditions anywhere in this store.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 11
relationships loaded, 40 assertions run. Also clean under `--fail-on-warn`. The store's own
`Checks 46/46` collapses to **40 distinct** keys once deduplicated — six exact-repeat checks
across `tests:` entries, the same pattern `calendar` (above) and `ads` already established.
Negative-control-verified per this file's standing method: flipping
`call:call-001#can_view@user:alice` to `assertFalse` fails `zed validate` itself (exit 1 from
`zed`, exit 2 from the harness); deleting the same assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 11 relationships loaded via `WriteRelationships`, all 40 distinct check-block assertions
  match exactly.
- All **6** `list_objects` assertions and both `list_users` assertions — silently dropped by
  the harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**. This store's full oracle is **54/54 confirmed** (46 raw check + 6
  list_objects + 2 list_users; 40 distinct check-block keys within that 46).
- No second-tenant probe: the source fixture has exactly one organization (`callcenter`).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all six types | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.supervisor`/`.agent`, and every resource type's `organization`/`owner`/`participant`/`author`/`call` relation | split rule, final bullet |
| **Relation/permission split, 4-way union** | `organization.member: [user] or admin or supervisor or agent` | "The relation/permission split" — a corpus confirmation of the 4-way shape, membership `ads`, `applicant-tracking-system` (iteration 12), `calendar`, `call-center` (iteration 13). No ordinal: `call-center` shares iteration 13 with `calendar` and `crm`, and the Iteration column's own definition disallows ordering same-iteration stores |
| Arrow into a bare relation, needing a `__perm` alias | `call.organization_admin`/`.organization_supervisor`/`.organization_agent` (`X from organization`), and `contact.can_delete`'s two arrows onto the same aliases | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `comment.can_delete`/`.can_edit`/`.can_view` and `recording.can_delete`/`.can_edit` (`X from call`, landing on `call`'s own alias permissions) | "Point arrows at permissions, not relations" — no-alias branch |
| Bare permission-to-permission alias (no operator, no arrow) | `call.can_delete = organization_admin`, `.can_create_recording = can_edit`, and **`.can_create_comment = can_view`** (references a permission declared later in the source file) | construct table row `define view: viewer`; precedented by `accounting`'s `can_view = can_edit`; the forward reference itself is licensed by "Emission order", `schema-mapping.md:89-91` ("a permission written above the relations it uses" and "a definition referencing another defined later in the file" compile and deploy fine — only `use` flags must precede every definition) |
| Union of two arrows into different `__perm` aliases on the same source type | `contact.can_delete = (organization->supervisor__perm + organization->admin__perm)` | "Always fully parenthesize" — same shape as `accounting`'s multi-arrow union permissions |

**Not exercised:** wildcards, caveats/conditions, modular models, runtime-defined roles,
multi-store tenancy, model-ID pinning, contextual tuples, intersection. `id_encoding` is
`none` — every object ID (`callcenter`, `call-001`, `john-doe`, `note-001`, `rec-001`) is
already inside SpiceDB's charset, hyphens included. No type/relation/permission name is under
3 characters. Cross-tenant isolation untested — only one organization (`callcenter`).

### Findings

None. Every construct — including the forward-referencing bare alias
(`can_create_comment = can_view`, which names a permission declared later in the source file)
— resolved using rules already on file, with no ambiguity and no fix-and-rerun cycle. The
forward reference is licensed by the "Emission order" rule on file (`schema-mapping.md:89-91`:
only `use` flags must precede every definition; a permission referencing one declared later in
the file is convention, not a compiler requirement, and both `fga` and SpiceDB accept it), not
asserted from this store's own clean first pass. The schema converted `PARITY OK` on the first
attempt, both negative controls
behaved as the pack predicts, and the full live-server list_objects/list_users sweep confirmed
100% of the store's own oracle.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **40 of 40 distinct assertions
(100%)** — six exact-repeat checks are silently deduped, the same behavior noted above for
`calendar`. But the store's full oracle by raw count is 54 assertions: 6 `list_objects` and 2
`list_users` assertions are silently dropped by the harness, so the harness's own view is
**46/54 (85.2%)** of the store's raw total assertions.

This gap was closed by direct verification (see "Final harness run" above): every dropped
`list_objects`/`list_users` assertion was run live and matched the source oracle exactly,
including the negative half a `check:` block cannot express (`diana`, a plain `member`, has no
`list_objects` entries of her own in the source and was not independently probed beyond the
check-block coverage, which already excludes her from every gated permission).

---

## `chat`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 16/16 passing,
Checks 42/42 passing, ListObjects 5/5 passing, ListUsers 3/3 passing`. Model carried in a
separate `model.fga` file.

This store is a five-type chat model: `organization` (`member: [user] or admin`, a plainer
2-way split than every other store in this batch, the same shape `github`'s own
`organization.member` established), `group` (reached from `organization` via arrow), and
`conversation` and `message` (`message` reached from `conversation`). Its one distinctive
construct is `conversation.member__direct: user | group#member` — a relation/permission split
whose direct part's type list mixes a plain type (`user`) with a **foreign type's bare-relation
userset** (`group#member`; `group.member` is itself `[user]`, unsplit), combined with `or
owner`. This is, verbatim, the shape of the pack's own canonical worked example at the top of
"The relation/permission split" (`relation viewer__direct: user | group#member`) — the corpus
had previously confirmed it only via `github`'s `repo.admin__direct`/`.maintainer__direct`/
`.reader__direct`/`.triager__direct`/`.writer__direct` (all `user | team#member`); `grep -n
'__direct:.*#' */schema.zed` across the full corpus shows `custom-roles`'/`multitenant-rbac`'s
`__direct: role#assignee` relations are pure-userset-only, a narrower shape with no bare `user`
alternative. No caveats/conditions anywhere in this store.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 17 relationships
loaded, 42 assertions run — no deduplication needed, `fga model test`'s raw `Checks 42/42`
already equals the distinct-key count. Also clean under `--fail-on-warn`. Negative-control-
verified per this file's standing method: flipping `conversation:engineering-general#can_view@
user:bob` to `assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the
harness, with `zed`'s own explanation trace resolving bob's access through the
`group:engineering#member` path — bob is independently both `owner` and a `group#member`, and
the union walk short-circuits on whichever branch it reaches first); deleting the same
assertion makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 17 relationships loaded via `WriteRelationships`, all 42 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 42/42` one for one.
- All **5** `list_objects` assertions and all **3** `list_users` assertions — silently dropped
  by the harness — were independently run via `LookupResources`/`LookupSubjects` and matched
  the source oracle **exactly**. This store's full oracle is **50/50 confirmed** (42 check + 5
  list_objects + 3 list_users).
- **`group#member` → `conversation.member` path, probed directly beyond the source's own
  oracle.** The source's own checks already exercise this path for `charlie` (who reaches
  `conversation:engineering-general` only through `group:engineering#member`, never a direct
  `member__direct` tuple, `owner`, or org admin), and the negative-control trace above
  independently confirms the same resolution live. Supplementing with a fresh subject absent
  from the source fixture (`user:erin`, not in `store.fga.yaml` at all): before joining the
  group, `can_view`/`can_post` on `conversation:engineering-general` are both `false`; after
  `zed relationship create group:engineering member user:erin`, both become `true` while
  `can_delete`/`can_add_member`/`can_edit` (owner/admin-derived permissions) correctly stay
  `false` — erin gains exactly the `member`-derived permissions, nothing more; after
  `zed relationship delete`, access reverts to `false`. No over-permissiveness in the
  userset-in-split shape.
- No second-tenant probe: the source fixture has exactly one organization (`acme`).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all five types | schema-mapping construct table |
| **Relation/permission split, 2-way union** | `organization.member: [user] or admin` | "The relation/permission split" — a plainer 2-way instance of the shape `github`'s own `organization.member` established |
| Pure-direct relation, no split | `group.organization`/`.member`, `conversation.organization`/`.owner`, `message.conversation`/`.sender` | split rule, final bullet |
| **Relation/permission split whose direct part mixes a plain type with a foreign type's bare-relation userset** | `conversation.member__direct: user \| group#member`, `permission member = (member__direct + owner)` | pack's own canonical worked example, "The relation/permission split" (`relation viewer__direct: user \| group#member`) — previously corpus-confirmed only by `github`'s five `X__direct: user \| team#member` relations, verified via `grep` (see prose above) |
| Arrow into a bare relation, needing a `__perm` alias | `group.can_manage`'s `organization->admin__perm`; `conversation.organization_admin`'s same arrow | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `message.can_view`'s `conversation->can_view`; `.can_reply`'s same arrow; `.can_delete`'s `conversation->organization_admin` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare permission-to-permission alias chain | `conversation.can_edit`/`.can_add_member`/`.can_remove_member` all alias `can_delete`; `.can_view` aliases `can_post`; `message.can_edit = sender` | construct table row `define view: viewer` |

**Not exercised:** wildcards, caveats/conditions, modular models, runtime-defined roles,
multi-store tenancy, model-ID pinning, contextual tuples, intersection (source uses only
`or`). `id_encoding` is `none` — every object ID (`acme`, `engineering`,
`engineering-general`, `bob-charlie-dm`, `msg-001`, `dm-001`) is already inside SpiceDB's
charset. No type/relation/permission name is under 3 characters. Cross-tenant isolation
untested — only one organization (`acme`).

### Findings

None. The store's one distinctive construct — a split relation whose direct part carries a
foreign type's bare-relation userset — is, verbatim, the pack's own headline worked example
for the split rule, and was already corpus-confirmed (in the "mixed bare-type-plus-foreign-
userset" shape specifically) by `github` at iteration 1. Every other construct is a plainer
instance of rules confirmed repeatedly across the corpus. The schema converted `PARITY OK` on
the first attempt, both negative controls behaved as predicted, and the live-server sweep —
including a dedicated add/remove probe on the `group#member` path beyond what the source
oracle itself exercises — found no over-permissiveness.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **42 of 42 (100%)** — no
deduplication needed. But the store's full oracle is 50 assertions, not 42: 5 `list_objects`
and 3 `list_users` assertions are silently dropped by the harness, so the harness's own view is
**42/50 (84.0%)** of the store's total assertions.

This gap was closed by direct verification (see "Final harness run" above), plus the dedicated
`group#member` add/remove probe that goes beyond the source's own oracle — the one shape in
this store most likely to hide over-permissiveness, and confirmed clean.

---

## `crm`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 14/14 passing,
Checks 42/42 passing, ListObjects 8/8 passing, ListUsers 4/4 passing`. Model carried in a
separate `model.fga` file.

This store is a nine-type CRM RBAC model — the largest in this batch — `organization`
(`member: [user] or admin or sales_rep or sales_manager`, the same 4-way split shape as `ads`,
`applicant-tracking-system`, `calendar`, and `call-center`) reached via a bare `organization`
relation and `from`-arrows by `account`, `lead`, `engagement`, `note`, and `task` (direct
children of `organization`), plus `contact` and `opportunity` (reached from `account`, two
hops from `organization`). No caveats/conditions anywhere in this store.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 17 relationships
loaded, 42 assertions run — no deduplication needed. Also clean under `--fail-on-warn`.
Negative-control-verified per this file's standing method: flipping
`account:mega-corp#can_view@user:alice` to `assertFalse` fails `zed validate` itself (exit 1
from `zed`, exit 2 from the harness); deleting the same assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 17 relationships loaded via `WriteRelationships`, all 42 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 42/42` one for one.
- All **8** `list_objects` assertions and all **4** `list_users` assertions — silently dropped
  by the harness — were independently run via `LookupResources`/`LookupSubjects` and matched
  the source oracle **exactly**. This store's full oracle is **54/54 confirmed** (42 check + 8
  list_objects + 4 list_users) — the thinnest harness-visible fraction in this batch (see
  below).
- No second-tenant probe: the source fixture has exactly one organization (`acme`).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all nine types | schema-mapping construct table |
| **Relation/permission split, 4-way union** | `organization.member: [user] or admin or sales_rep or sales_manager` | "The relation/permission split" — a corpus confirmation of the 4-way shape, membership `ads`, `applicant-tracking-system` (iteration 12), `calendar`, `call-center`, `crm` (iteration 13). No ordinal: `crm` shares iteration 13 with `calendar` and `call-center`, and the Iteration column's own definition disallows ordering same-iteration stores |
| Pure-direct relation, no split | `organization.admin`/`.sales_manager`/`.sales_rep`, and every resource type's `organization`/`owner`/`account` relation | split rule, final bullet |
| Arrow into a bare relation, needing a `__perm` alias | `account.organization_admin`/`.organization_sales_manager`; `lead.can_delete`'s `organization->admin__perm`; `engagement`/`note`/`task`'s `organization->admin__perm`/`.sales_manager__perm` arrows | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `contact.can_delete`/`opportunity.can_delete`'s `account->organization_admin`; `contact.can_view`/`opportunity.can_view`'s `account->can_view` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare permission-to-permission alias | `account.can_create_contact`/`.can_create_opportunity`/`.can_view` all alias `can_edit`; `lead.can_view = can_edit`; `note.can_edit = can_delete` | construct table row `define view: viewer`; precedented by `accounting` |
| Union of an arrow and a local permission | `contact.can_view = (account->can_view + can_edit)`; `opportunity.can_view = (account->can_view + can_edit)` | "Always fully parenthesize" — same shape as `applicant-tracking-system`'s `department.can_view = (organization->member + can_manage)` |

**Not exercised:** wildcards, caveats/conditions, modular models, runtime-defined roles,
multi-store tenancy, model-ID pinning, contextual tuples, intersection. `id_encoding` is
`none` — every object ID (`acme`, `mega-corp`, `john-doe`, `new-prospect`, `big-deal`,
`meeting-notes`, `follow-up`) is already inside SpiceDB's charset, hyphens included. No
type/relation/permission name is under 3 characters. Cross-tenant isolation untested — only
one organization (`acme`).

### Findings

None. Every construct — including the two-hop `contact`/`opportunity` reach through `account`
and the arrow-plus-local-permission union it produces — resolved using rules already on file,
confirmed identical in shape to `applicant-tracking-system`'s own two-hop `department`/`job`
pattern. The schema converted `PARITY OK` on the first attempt, both negative controls behaved
as predicted, and the full live-server list_objects/list_users sweep confirmed 100% of the
store's own oracle.

### What the harness could not see

The harness's own coverage of this store's `check:` block is **42 of 42 (100%)** — no
deduplication needed. But the store's full oracle is 54 assertions, not 42: 8 `list_objects`
and 4 `list_users` assertions are silently dropped by the harness, so the harness's own view is
**42/54 (77.8%)** of the store's total assertions — the thinnest fraction in this batch, though
still well above `ip-based-access`'s 50% minimum through batch 2 (superseded by `gdrive`'s
33.3% in batch 3; see "The canonical store table" → **Harness-visible fraction**).

This gap was closed by direct verification (see "Final harness run" above): every dropped
`list_objects`/`list_users` assertion was run live and matched the source oracle exactly.


## `entitlements`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 9/9 passing, ListObjects 1/1 passing, ListUsers 1/1 passing`. Model carried in a
separate `model.fga` file.

This store is a three-type SaaS-entitlement model: `organization` (`member: [user]`, no
split — a bare type list, no operator anywhere on this relation), `plan` (`subscriber:
[organization]`, `subscriber_member: member from subscriber`), and `feature`
(`associated_plan: [plan]`, `can_access: subscriber_member from associated_plan`). Despite
the store's name, and despite the batch brief's own expectation that it would touch gate 3's
usage-counter caveat territory, this store carries **zero caveats** (`grep -c '^caveat '
schema.zed` → 0) — its whole design is a two-hop nested arrow chain
(`organization->plan->feature`) gating access purely through subscription membership, not
through any usage count or condition. It is the corpus's first store built around this exact
tenant→plan→feature indirection shape.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 12
relationships loaded, 9 assertions run. Also clean under `--fail-on-warn`. Negative-control-
verified per this file's standing method: flipping `feature:issues#can_access@user:anne` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the walk `can_access -> plan:enterprise
subscriber_member -> organization:cups member__perm ⨉` failing on the `enterprise`
branch and succeeding on `plan:free subscriber_member -> organization:alpha member__perm ->
member -> user:anne`); deleting the same assertion makes the harness report `MISSING`
(exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 12 relationships loaded via `WriteRelationships`, all 9 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 9/9 passing` one for one.
- The **1** `list_objects` and **1** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `feature:issues`'s subject set is exactly `{anne, beth,
  charles}`, and charles's readable-feature set is exactly `{draft_prs, issues, sso}`, no
  fewer and no more. This store's full oracle is **11/11 confirmed** (9 check + 1
  list_objects + 1 list_users).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all four types | schema-mapping construct table |
| Pure-direct relation, no split | `organization.member`, `plan.subscriber`, `feature.associated_plan` | split rule, final bullet |
| Arrow into a bare relation, needing a `__perm` alias | `plan.subscriber_member`'s `subscriber->member__perm` | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `feature.can_access`'s `associated_plan->subscriber_member` | "Point arrows at permissions, not relations" — no-alias branch |
| **Two-hop arrow chain, tenant → plan → feature** | `feature.can_access` → `plan.subscriber_member` → `organization.member__perm` | "Arrows"; see "The canonical store table" → **Arrow-chain hop depth** |

**Not exercised:** wildcards, caveats/conditions (despite the store's name — see prose
above), the relation/permission split (no `define` in this store mixes a type list with an
operator), intersection, modular models, runtime-defined roles, multi-store tenancy,
model-ID pinning, contextual tuples. `id_encoding` is `none` — every object ID (`alpha`,
`brayer`, `cups`, `enterprise`, `team`, `free`, `draft_prs`, `issues`, `sso`) is already
inside SpiceDB's charset, underscores included.

### Findings

None. Every construct — including the two-hop tenant→plan→feature arrow chain — resolved
using rules already on file, with no ambiguity and no fix-and-rerun cycle. The schema
converted `PARITY OK` on the first attempt, the negative control behaved as the pack
predicts, and the full live-server list_objects/list_users sweep confirmed 100% of the
store's own oracle. Worth recording explicitly since the batch brief flagged this store as
"likely to touch gate 3's territory": it does not. Its shape is ordinary nested-arrow ReBAC,
structurally distinct from `advanced-entitlements` (iteration 9), the store that actually
forced gate 3's usage-counter caveat decision — confirmed by `grep`, not assumed.

### What the harness could not see

The harness compared **9 of this store's 11 assertions (81.8%)** — see "The canonical store
table" → **Harness-visible fraction** (this store ties `custom-roles` exactly, both 9/11).
The 1 `list_objects` and 1 `list_users` assertion are silently dropped by the harness per the
known gap; both were closed by direct live-server verification (see "Final harness run"
above), including the exhaustiveness half a `check:` block cannot express on its own
(`feature:issues`'s subject set is exactly three people, not merely inclusive of them).

---

## `gdrive`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 4/4 passing,
Checks 3/3 passing, ListObjects 1/1 passing, ListUsers 5/5 passing`. Model carried in a
separate `model.fga` file.

This store is a four-type Google-Drive-style document model, and the corpus's **first store
of any kind to carry a live wildcard** — `grep -l ':\*' */schema.zed` across every other
committed store (all 19 prior to this batch, and `entitlements`/`iot`/`slack` alongside it)
returns nothing; only `gdrive` does. `folder` is self-referential (`parent: [folder]`, a
directory tree) and its `viewer` relation fuses a wildcard-bearing type list with an operator
(`[user, user:*, group#member] or owner or viewer from parent`), which triggers the
relation/permission split; `doc.viewer` carries the identical type list
(`[user, user:*, group#member]`) but with no operator, so it stays a bare, unsplit relation.
`doc.can_read`/`can_share`/`can_write` all arrow into `folder` (`viewer from parent`, `owner
from parent`), and `folder.viewer` arrows into itself the same way, walking the folder tree.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 9
relationships loaded, 3 assertions run. Also clean under `--fail-on-warn`. Negative-control-
verified per this file's standing method: flipping `doc:2021-roadmap#can_write@user:anne` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the walk `can_write -> owner ⨉ -> folder:product-2021
owner__perm -> owner -> user:anne`); deleting the same assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 9 relationships loaded via `WriteRelationships`, all 3 check-block assertions match
  exactly.
- All **1** `list_objects` and all **5** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**, including the literal wildcard marker: `lookup-subjects
  doc:public-roadmap viewer user` returns the single entry `user:*` (not an enumeration),
  matching the source's own `list_users` expectation of `user:*` verbatim, and
  `folder:product-2021`'s `viewer` set correctly resolves to `{group:fabrikam#member}` under
  a `group#member`-typed filter and to `{anne, charles}` under a `user`-typed filter (charles
  reached only through `group:fabrikam#member`, anne only through direct `owner`). This
  store's full oracle is **9/9 confirmed** (3 check + 1 list_objects + 5 list_users).
- **Supplementary probe beyond the source's own oracle: does a wildcard survive an arrow?**
  The source fixture's only wildcard tuple (`doc:public-roadmap#viewer@user:*`) is checked
  *directly*, never through `parent->viewer` — so the store's own oracle never actually
  exercises a wildcard reached transitively through the folder arrow. Probed directly: with
  `folder:product-2021#viewer__direct@user:*` added live and a subject entirely outside the
  fixture (`user:zzz-not-in-fixture`), `doc:2021-roadmap#can_read` resolves `true` through
  the full `doc -> parent -> folder.viewer -> viewer__direct` chain; after deleting that
  tuple, the same check reverts to `false`. The wildcard survives the arrow.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all four types | schema-mapping construct table |
| Pure-direct relation, no split | `group.member`, `folder.owner`, `folder.parent`, `doc.owner`, `doc.parent` | split rule, final bullet |
| **Wildcard directly on the relation being translated** | `folder.viewer__direct`'s and `doc.viewer`'s `user:*` | schema-mapping construct table row `define viewer: [user:*]`; "Wildcards" note — `clean` |
| Pure-direct relation with a foreign userset AND a wildcard in one type list, no split (no operator) | `doc.viewer: user \| user:* \| group#member` | split rule, final bullet — confirms the no-split branch composes with a wildcard entry, no new rule |
| **Relation/permission split whose direct part carries a wildcard** | `folder.viewer__direct: user \| user:* \| group#member` (mixed with `or owner or viewer from parent`) | "The relation/permission split" + `blockers.md`'s wildcard note — the split turns the arrow target into a permission automatically, corpus-confirmed live for the first time on this store (see Finding 1) |
| Arrow into a bare relation, needing a `__perm` alias | `doc.can_share`/`.can_write`'s `parent->owner__perm` (folder's `owner` never splits) | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `doc.can_read`'s `parent->viewer` (folder's split `viewer` permission) | "Point arrows at permissions, not relations" — no-alias branch |
| **Self-referential arrow, wildcard-bearing target** | `folder.viewer`'s own `parent->viewer` (folder → folder) | "A self-referential arrow… needs no special rule" (`abac-with-rebac`) — confirms the rule composes with a wildcard-carrying target, corpus-first (see Finding 1) |
| Userset subject pointing at a split permission | `channel`-equivalent here is `doc.can_read`'s `parent->viewer`, already counted above; no additional foreign-type userset reference exists in this store beyond `group#member`, which points at `group.member` (unsplit, no wildcard) | "A userset subject may point at a split permission" — not separately exercised; see "Not exercised" |
| Bare permission-to-permission alias | `folder.can_create_file = owner`; `doc.can_change_owner = owner` | construct table row `define view: viewer` |

**Not exercised:** the transitive-wildcard blocker's actual rejected shape (a userset
type-list entry `[T#rel]` pointing at a wildcard-bearing bare relation — this store's only
userset type-list reference, `group#member`, points at a relation with no wildcard; see
Finding 1), caveats/conditions, intersection, multi-tenancy / type-based tenancy (no tenant
root type at all — access is purely folder/doc ownership and sharing), modular models,
runtime-defined roles, multi-store tenancy, model-ID pinning, contextual tuples.
`id_encoding` is `none` — every object ID (`contoso`, `fabrikam`, `product-2021`,
`public-roadmap`, `2021-roadmap`) is already inside SpiceDB's charset.

### Findings

**1 — First live wildcard-bearing corpus store: corpus-confirms the split-produces-
permission escape hatch end to end, and narrows the transitive-wildcard blocker further.**
*Classification: ambiguous guidance → worked example (the rule already existed in
`schema-mapping.md`/`blockers.md`, rated `clean`/`effort`, but both explicitly said "still
needs a wildcard-bearing corpus store before it is promoted... do not apply it silently
until then").*
*Changed: `references/blockers.md` ("Corpus status" section, Detection step 3, the Options
table's leading-candidate row), `references/schema-mapping.md` (Wildcards note).*

`folder.viewer` fuses `user:*` with an operator, so the split rule turns it into a relation
plus a permission automatically, and every arrow reaching it (`doc.can_read`'s and
`folder.viewer`'s own `parent->viewer`) lands on that permission rather than the bare
relation. Verified live with data outside the source fixture (see "Final harness run" above)
that this is not merely a compile-time technicality: the wildcard itself, not just an
ordinary subject, survives the userset→permission indirection and the folder-tree arrow
walk. This does **not** retire the blocker's core provisional rating — `gdrive` never
constructs the actual rejected shape (a userset *type-list* entry `[T#rel]` pointing at a
still-bare, wildcard-bearing relation; this store's split always intervenes first) — but it
does retire the uncertainty around the leading-candidate *option*, previously confirmed only
by analogy to `github`'s non-wildcard case.

**2 — An arrow into a wildcard-bearing relation is exempt from the transitive-wildcard
typesystem check entirely, independent of the split — not previously stated anywhere in the
pack.**
*Classification: missing rule clarification → `blockers.md` (Detection scope + a new
verified-fact block).*
*Changed: `references/blockers.md`.*

`gdrive`'s own real schema never forces this question (its wildcard-carrying relation always
splits before any arrow reaches it), but it is the natural adjacent question once you're
reasoning about a wildcard-bearing arrow chain, and leaving it untested would have left a
plausible-but-wrong belief (that the split is *necessary*, not just sufficient) on the
table. Verified with a synthetic, deliberately unsplit control schema
(`relation viewer: user | user:* | group#member` with no operator, `permission can_read =
viewer + parent->viewer`): `WriteSchema` accepts it **unedited** on v1.56.0, and
`zed permission check --explain` resolves `true` for an arbitrary subject straight through
`parent->viewer` into the bare wildcard relation — no rejection, no warning. This is
narrower than the Detection rule's scope might suggest to a careful reader: the restriction
fires only for the literal subject type-list syntax (`[T#rel]`), never for a computed-userset
arrow. `blockers.md`'s Detection step 3 now says so explicitly.

### What the harness could not see

The harness compared **3 of this store's 9 assertions (33.3%)** — the corpus's **new
thinnest harness-visible fraction**, below `ip-based-access`'s prior 50% floor (see "The
canonical store table" → **Harness-visible fraction** for the full sorted list and the
command). This store's fixture was deliberately weighted toward `list_users` (5 of its 9
assertions) to exercise the wildcard-subject and group-userset resolution paths, which is
exactly the shape a `check:`-only oracle is worst at expressing. All 6 dropped assertions (1
`list_objects`, 5 `list_users`) were closed by direct live-server verification (see "Final
harness run" above), including the literal-wildcard-marker case (`user:*` returned verbatim
by `LookupSubjects`, not expanded) and the supplementary wildcard-through-arrow probe beyond
the source's own oracle (Finding 1/2 above).

---

## `iot`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 4/4 passing, ListObjects 1/1 passing, ListUsers 1/1 passing`. Model carried in a
separate `model.fga` file.

This store is a three-type IoT access-control model: `device_group` (`it_admin: [user]`,
`security_guard: [user]`, both pure direct, no split) and `device` (`it_admin: [user,
device_group#it_admin]`, `security_guard: [user, device_group#security_guard]`, each a pure
type list mixing a bare type with a foreign userset — no operator, so neither splits).
`device`'s three permissions are bare unions/aliases of those two relations. This store has
**zero arrows** (`grep -c -- '->' schema.zed` → 0): every foreign-type reference is a direct
userset entry in a relation's own type list, never a tupleset-based arrow — the same flat
shape `custom-roles`/`multitenant-rbac` established, exercised here with no arrow indirection
at all rather than as one operand among several.

**Final harness run:** `PARITY OK`, exit **0**. `zed validate`: 10 relationships loaded, 4
assertions run. First pass under `--fail-on-warn` surfaced a lint not caused by anything
specific to this store's shape: `Permission "can_rename_device" references parent type
"device" in its name; it is recommended to drop the suffix (relation-name-references-
parent)` — this is `advanced-entitlements`'s already-documented lint
(`schema-mapping.md`'s Codegen rules addendum), corpus-confirmed a second time on a name the
OpenFGA source chose independently. Resolved with the pack's own purpose-built suppression
comment (`// spicedb-ignore-warning: relation-name-references-parent`, placed directly above
the flagged `permission` line), after which `--fail-on-warn` is clean. Negative-control-
verified per this file's standing method: flipping `device:2#can_rename_device@user:diane` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing `can_rename_device -> it_admin -> device_group:group1
it_admin -> user:diane`); deleting the same assertion makes the harness report `MISSING`
(exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` (with the suppression comment) unedited, no compile
  step, no `use` flag.
- All 10 relationships loaded via `WriteRelationships`, all 4 check-block assertions match
  exactly.
- The **1** `list_objects` and **1** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `device:1`'s `can_view_live_video` subject set is exactly
  `{diane, charles, anne, beth}`, and beth's viewable-device set is exactly `{device:1}`.
  This store's full oracle is **6/6 confirmed** (4 check + 1 list_objects + 1 list_users).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all three types | schema-mapping construct table |
| Pure-direct relation, no split | `device_group.it_admin`, `device_group.security_guard` | split rule, final bullet |
| Direct relation with a userset type, no split, no arrow | `device.it_admin: user \| device_group#it_admin`, `device.security_guard: user \| device_group#security_guard` | construct table row `define viewer: [user, group#member]` — confirms the shape composes with zero arrows anywhere in the store |
| Bare permission-to-relation alias | `device.can_rename_device = it_admin` | construct table row `define view: viewer` |
| Bare two-relation union permission | `device.can_view_live_video = (it_admin + security_guard)`, `device.can_view_recorded_video = (it_admin + security_guard)` | construct table row `define view: a or b`, `schema-mapping.md:35` |
| **`relation-name-references-parent` lint + `spicedb-ignore-warning` suppression** | `device.can_rename_device` (references its own parent type `device`) | already on file — `schema-mapping.md`'s Codegen rules addendum, corpus-confirmed on `advanced-entitlements`'s `feature.has_feature`; applied here verbatim, no new rule needed |

**Not exercised:** arrows of any kind (zero `->` in this schema), the relation/permission
split, wildcards, caveats/conditions, intersection, multi-tenancy / type-based tenancy (no
tenant-root type — `device_group` is a peer grouping construct, not a tenant), modular
models, runtime-defined roles, multi-store tenancy, model-ID pinning, contextual tuples.
`id_encoding` is `none` — every object ID (`1`, `2`, `3`, `group1`) is already inside
SpiceDB's charset; single-digit object IDs write and resolve with no special handling
(object IDs carry no minimum-length floor, unlike type/relation/permission names' 3-character
minimum).

### Findings

None. Every construct — including the `relation-name-references-parent` lint, corpus-
confirmed here for the second time on an independently-chosen source name — resolved using
rules already on file, with no ambiguity and no fix-and-rerun cycle beyond applying the
already-documented suppression comment. The schema converted `PARITY OK` on the first
attempt (after the one-line suppression comment), the negative control behaved as the pack
predicts, and the full live-server list_objects/list_users sweep confirmed 100% of the
store's own oracle.

### What the harness could not see

The harness compared **4 of this store's 6 assertions (66.7%)** — see "The canonical store
table" → **Harness-visible fraction**. The 1 `list_objects` and 1 `list_users` assertion are
silently dropped by the harness per the known gap; both were closed by direct live-server
verification (see "Final harness run" above).

---

## `slack`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 6/6 passing, ListObjects 1/1 passing, ListUsers 1/1 passing`. Model carried in a
separate `model.fga` file.

This store is a three-type Slack-style workspace model: `workspace`
(`channels_admin: [user] or legacy_admin`, `guest: [user]`, `legacy_admin: [user]`,
`member: [user] or legacy_admin or channels_admin` — **two** independent relation/permission
splits on one type, each referencing the other's sibling name) and `channel`
(`commenter: [user, workspace#member] or writer`, `parent_workspace: [workspace]`,
`writer: [user, workspace#member]` — a third split alongside a pure-direct type list carrying
the same foreign userset). This store has **zero arrows**, the same shape `iot` (above)
establishes: every cross-type reference is a userset subject embedded directly in a type
list (`workspace#member`), never a tupleset-based arrow.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 13
relationships loaded, 6 assertions run. Also clean under `--fail-on-warn`. Negative-control-
verified per this file's standing method: flipping
`channel:proj_marketing_campaign#writer@user:david` to `assertFalse` fails `zed validate`
itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own explanation trace
resolving david directly against the bare `channel:proj_marketing_campaign writer` relation
— `writer` is unsplit here, so the trace is the direct tuple itself, no indirection);
deleting the same assertion makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 13 relationships loaded via `WriteRelationships`, all 6 check-block assertions match
  exactly.
- The **1** `list_objects` and **1** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: david's writable-channel set is exactly
  `{proj_marketing_campaign}`, and `proj_marketing_campaign`'s writer subject set is exactly
  `{david, emily, catherine, amy, bob}` — the last four reached only through
  `workspace:sandcastle#member` (itself split three ways: `catherine`/`emily` direct,
  `amy` via `legacy_admin`, `bob` via `channels_admin`), confirming the two-level split
  resolves correctly through a foreign-type userset reference with no over- or
  under-inclusion. This store's full oracle is **8/8 confirmed** (6 check + 1 list_objects +
  1 list_users).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all three types | schema-mapping construct table |
| **Relation/permission split, 2-way union, referencing a sibling split's permission** | `workspace.channels_admin: [user] or legacy_admin` | "The relation/permission split" |
| **Relation/permission split, 3-way union, one operand itself a split permission** | `workspace.member: [user] or legacy_admin or channels_admin` | "The relation/permission split" — confirms a split permission can union another split permission from the same type with no special handling |
| Pure-direct relation, no split | `workspace.guest`, `workspace.legacy_admin`, `channel.parent_workspace` | split rule, final bullet |
| Direct relation with a userset type, no split, no arrow | `channel.writer: user \| workspace#member` | construct table row `define viewer: [user, group#member]` — `member` here is a **split permission**, not a bare relation |
| **Relation/permission split whose direct part is a userset pointing at a split permission** | `channel.commenter__direct: user \| workspace#member` (mixed with `or writer`) | "The relation/permission split" + "A userset subject may point at a split permission" — same combined shape `chat`'s `conversation.member__direct: user \| group#member` established, here the userset target is itself split (`workspace.member`) rather than unsplit (`chat`'s `group.member`) |
| Two-operand union permission (the split's own permission side) | `channel.commenter = (commenter__direct + writer)` | "The relation/permission split" (the permission side unions `__direct` with the remaining operand) + construct table row `define view: a or b`, `schema-mapping.md:35` — not a bare alias (`define view: viewer`, :34), which has only one operand |

**Not exercised:** arrows of any kind (zero `->` in this schema — every cross-type reference
is a direct userset type-list entry), wildcards, caveats/conditions, intersection,
multi-tenancy / type-based tenancy (single workspace in the fixture, no tenant-scoped
resource hierarchy beyond one level), modular models, runtime-defined roles, multi-store
tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` — every object ID
(`sandcastle`, `general`, `marketing_internal`, `proj_marketing_campaign`) is already inside
SpiceDB's charset, underscores included.

### Findings

None. Every construct — including the userset-into-split-permission shape when the target
split's *own* permission unions a second split permission from the same type
(`workspace.member` unioning `workspace.channels_admin`) — resolved using rules already on
file, with no ambiguity and no fix-and-rerun cycle. The schema converted `PARITY OK` on the
first attempt, the negative control behaved as the pack predicts, and the full live-server
list_objects/list_users sweep confirmed 100% of the store's own oracle, including the
five-person `writer` set reached entirely through nested split resolution.

### What the harness could not see

The harness compared **6 of this store's 8 assertions (75%)** — see "The canonical store
table" → **Harness-visible fraction**. The 1 `list_objects` and 1 `list_users` assertion are
silently dropped by the harness per the known gap; both were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express (the five-person `writer` set on `proj_marketing_campaign` is exactly
those five, not merely inclusive of them).

---

# Batch 4

## `expenses`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 3/3 passing, ListObjects 1/1 passing, ListUsers 1/1 passing`. Model carried in a
separate `model.fga` file.

This store is a two-type org-chart model — `employee` and `report`. Its `schema.zed` declares
no `definition user`, one of only two in the corpus that don't (`grep -L 'definition user'
*/schema.zed`: this store and `banking`, whose own subjects are `employee`/`customer`
instead) — every subject anywhere in this store's own data is itself an `employee`.
`employee.can_manage: manager or can_manage from manager` is OpenFGA's canonical "all my
managers, transitively" idiom — a relation arrowing into the very permission being defined, on
the same type, with no depth bound. `report.approver: can_manage from submitter` then arrows
once more to reach it. This is the shape the batch brief flagged as untested by any
zero-finding store so far — the batch brief was written from this store's own README, not from
a corpus-wide `schema.zed` scan, and that scan (run before writing this section, not assumed)
turns up a prior instance: `gdrive` (iteration 14) already carries the byte-for-byte identical
same-name-recursive-arrow *shape* (`folder.viewer`'s own `parent->viewer`), just never
exercised with data that actually recurses more than zero levels — see Finding 1 for the full
correction and what is genuinely new here.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 5 relationships
loaded, 3 assertions run. Also clean under `--fail-on-warn`. Negative-control-verified per
this file's standing method: flipping `employee:daniel#can_manage@employee:matt` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the one-level walk `can_manage -> employee:daniel
manager -> employee:matt`); deleting the same assertion makes the harness report `MISSING`
(exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 5 relationships loaded via `WriteRelationships`, all 3 check-block assertions match
  exactly.
- The **1** `list_objects` and **1** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `employee:emily`'s approvable-report set is exactly
  `{daniel-chair1, sam-chair1}`, and `report:daniel-chair1`'s approver set is exactly
  `{matt, sam, emily}` — **all three ancestors of a three-link manager chain**
  (`daniel`'s manager is `matt`, `matt`'s manager is `sam`, `sam`'s manager is `emily`),
  reached entirely through the recursive `manager->can_manage` arrow with no depth limit
  encoded anywhere in the schema. This store's full oracle is **5/5 confirmed** (3 check + 1
  list_objects + 1 list_users).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | both types (`employee`, `report`) | schema-mapping construct table |
| Pure-direct relation, no split | `employee.manager`, `report.submitter` | split rule, final bullet |
| **Same-name recursive arrow (a permission's own arrow target names the permission being defined)** | `employee.can_manage = (manager + manager->can_manage)` | "A self-referential arrow… needs no special rule" (`abac-with-rebac`), extended by a new subsection this store prompted — see Finding 1. The schema *shape* already existed in the corpus (`gdrive`'s `folder.viewer`, iteration 14) |
| Arrow into an already-permission target, no alias | `report.approver`'s `submitter->can_manage` | "Point arrows at permissions, not relations" — no-alias branch |
| Two-operand union, one group | `employee.can_manage`'s `(manager + manager->can_manage)` | "Always fully parenthesize" |

**Not exercised:** the relation/permission split (no `define` in this store mixes a type list
with an operator — `manager` and `submitter` are pure type lists, `can_manage` and `approver`
carry no type list at all), `__perm` aliases, wildcards, caveats/conditions, intersection,
multi-tenancy / type-based tenancy (no tenant root — plain org-chart), modular models,
runtime-defined roles, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding`
is `none` — every object ID (`daniel`, `matt`, `sam`, `emily`, `daniel-chair1`, `sam-chair1`)
is already inside SpiceDB's charset, hyphens included.

### Findings

**1 — Not a new schema shape: `gdrive` (iteration 14) already carries the identical
same-name recursive arrow. What is new is the first live, multi-level confirmation that it
actually resolves an unbounded chain, not just that it compiles.**
*Classification: ambiguous guidance → worked example (the existing self-referential-arrow
rule's own text — "nothing in this file's arrow rule excludes this shape" — already covers a
same-*name* target as a special case of a same-*type* one; this store corrects the corpus
record of who exercised it and adds the multi-level live evidence no prior store's fixture
provided).*
*Changed: `references/schema-mapping.md` ("A self-referential arrow…" subsection, retitled
with a new same-name-recursion paragraph correcting which stores actually exercised it).*

Before writing this finding as "first recursive arrow in the corpus," a check against every
committed `schema.zed` (not just this store's own view) turned up `gdrive`'s `folder.viewer =
(viewer__direct + owner + parent->viewer)`, on `folder.parent: [folder]` — byte-for-byte the
same pattern, just never called "recursive" in `gdrive`'s own write-up, which framed it under
the (correct, but incomplete) heading "self-referential arrow." That framing is not wrong —
the existing rule really does cover it, with no gap — but it obscures a separate, real
question: does `gdrive`'s own fixture ever prove the recursion resolves more than one level?
It does not. `gdrive`'s committed `validation.yaml` contains exactly one `folder` object and
no `folder#parent@folder` relationship at all, so `folder.viewer`'s recursive arm is present
in the schema but was never actually walked by that store's own oracle — confirmed by reading
the committed relationships, not assumed. `modeling-guide`'s later reuse of the same pattern
(`folder.can_edit`, this batch) is dormant the same way. `expenses` is the first (and, as of
this batch, only) store whose own fixture data forces a genuine multi-level walk: a real
three-link manager chain (`daniel` → `matt` → `sam` → `emily`), confirmed live via
`lookup-subjects report:daniel-chair1 approver employee` returning all three ancestors,
matching the source's own `list_users` oracle exactly. No rule needed to change — the existing
one already covers this shape correctly — but `schema-mapping.md` now says explicitly, with
the corrected per-store record, rather than leaving a future reader to either re-derive it or
mistake `gdrive`'s schema-level confirmation for a live multi-level one. See "The canonical
store table" → **Arrow-chain hop depth** for a related caveat: that derived set's own script
undercounts this store's live recursion depth (it reports 2 hops for `report.approver`,
bounded by its own cycle-breaker), which is now noted there so the two metrics are not
conflated.

### What the harness could not see

The harness compared **3 of this store's 5 assertions (60%)** — see "The canonical store
table" → **Harness-visible fraction** (this store ties `github` exactly, both at 60%). The 1
`list_objects` and 1 `list_users` assertion are silently dropped by the harness per the known
gap; both were closed by direct live-server verification (see "Final harness run" above),
including the exhaustiveness half a `check:` block cannot express on its own (`report:daniel-
chair1`'s approver set is exactly those three ancestors, not merely inclusive of them).

---

## `healthcare`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 17/17 passing,
Checks 101/101 passing, ListObjects 6/6 passing, ListUsers 6/6 passing`. Model carried in a
separate `model.fga` file.

This store is an eight-type healthcare-provider model built around a single tenant root,
`organization` (`admin`/`provider`/`nurse`/`medical_records_staff` all pure-direct, `member`
a 5-way union of the bare form plus those four named relations). Three resource types hang
directly off the tenant root by a one-hop `organization` edge (`facility`, `patient`,
`medication`); two more hang a level deeper, off `patient` (`encounter`, `diagnosis`); and one
more hangs off `encounter` (`treatment`) — a two-level resource hierarchy underneath the
tenant root, one level deeper than `multitenant-rbac`'s single-level `organization -> document`
edge. This is the batch's dedicated multi-tenancy exemplar: unlike `role-assignments` and
`modeling-guide` (both batch 4, both intersection-bearing), this store contains no `caveat`
and no `&` of any kind (`grep -cE '^caveat |&' schema.zed` → 0 for both) — access is
determined purely by tenant-scoped role membership and per-resource assignment (`primary_
provider`, `care_team`, `attending_provider`, `diagnosing_provider`, `ordering_provider`),
walked back up to the tenant root through plain arrows.

**Final harness run:** `PARITY OK`, exit **0**, first attempt, **101 of 101 assertions**
compared. `zed validate`: 20 relationships loaded,
101 assertions run. Also clean under `--fail-on-warn`. Negative-control-verified per this
file's standing method: flipping `patient:doe#can_delete@user:alice` to `assertFalse` fails
`zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own
explanation trace showing the walk `can_delete -> org_admin -> organization:mercy-hospital
admin__perm -> admin -> user:alice`); deleting the same assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 20 relationships loaded via `WriteRelationships`, all 101 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 101/101 passing` one for one.
- All **6** `list_objects` and all **6** `list_users` assertions — silently dropped by the
  harness, and counted per-assertion-key the way `fga model test` itself counts them (the
  "List users with patient doe permissions" entry alone carries 3 keys —
  `can_view`/`can_edit`/`can_view_sensitive` — accounting for 3 of the 6) — were independently
  run via `LookupResources`/`LookupSubjects` and matched the source oracle **exactly**:
  `user:dr-smith`'s primary-provider patient set is exactly `{doe}`; `patient:doe`'s `can_view`
  subject set is exactly `{alice, clerk-brown, dr-smith, nurse-williams}`, correctly excluding
  the unrelated provider `dr-jones` and the billing-only `billing-davis`; and all four other
  list assertions matched with no over- or under-inclusion. This store's full oracle is
  **113/113 confirmed** (101 check + 6 list_objects + 6 list_users).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all eight types (`user`, `organization`, `facility`, `patient`, `encounter`, `diagnosis`, `treatment`, `medication`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.provider`/`.nurse`/`.medical_records_staff`, `facility.organization`/`.director`, `patient.organization`/`.primary_provider`/`.care_team`, `encounter.patient`/`.attending_provider`, `diagnosis.patient`/`.diagnosing_provider`, `treatment.encounter`/`.ordering_provider`, `medication.organization` | split rule, final bullet |
| Relation/permission split, 5-way union | `organization.member: [user] or admin or provider or nurse or medical_records_staff` | "The relation/permission split"; construct table row `define view: a or b` |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin__perm`/`.provider__perm`/`.nurse__perm`/`.medical_records_staff__perm`, reached by `facility.can_delete`, `patient.org_admin`, `patient.org_medical_records`, `medication.can_edit`, `medication.can_view` | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `facility.can_view`'s `organization->member`; `encounter.org_admin`'s `patient->org_admin`; `encounter.can_view`'s `patient->can_view`; `diagnosis.can_edit`'s `patient->org_admin`; `diagnosis.can_view`'s `patient->can_view`; `treatment.can_delete`'s `encounter->can_delete`; `treatment.can_view`'s `encounter->can_view` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare permission-to-permission alias | `patient.can_delete = org_admin`, `.can_create_encounter = can_view_sensitive`, `.can_create_diagnosis = can_edit`; `encounter.can_delete = org_admin` | construct table row `define view: viewer` |
| Bare union of a relation with one or more same-type permission references (no arrow) | `facility.can_edit = (director + can_delete)`; `patient.can_edit = (primary_provider + org_medical_records + can_delete)`; `patient.can_view_sensitive = (primary_provider + care_team + org_medical_records + can_delete)`; `patient.can_view = (can_view_sensitive + can_edit)`; `encounter.can_create_treatment = (attending_provider + can_delete)`; `encounter.can_edit = (attending_provider + can_delete)`; `treatment.can_edit = (ordering_provider + can_delete)` | construct table row `define view: a or b` |
| Type-based tenancy, two-level resource hierarchy under the tenant root | `organization` reaches `facility`/`patient`/`medication` directly and `encounter`/`diagnosis`/`treatment` through `patient`/`encounter` | "Type-based tenancy" — no new construct |
| Three-hop arrow chain | `treatment.can_delete` → `encounter.can_delete`/`.org_admin` → `patient.org_admin` → `organization.admin__perm` | "Arrows"; see "The canonical store table" → **Arrow-chain hop depth** |

**Not exercised:** wildcards, caveats/conditions, intersection, the same-name-recursive-arrow
shape (`gdrive`, `expenses`, and `modeling-guide`, this batch — this store's arrows all cross
to progressively different types, none self-referential), modular models, runtime-defined
roles, multi-store tenancy, model-ID
pinning, contextual tuples. `id_encoding` is `none` — every object ID (`mercy-hospital`,
`main-campus`, `doe`, `roe`, `enc-001`, `diag-001`, `treat-001`, `aspirin`, `alice`,
`dr-smith`, `dr-jones`, `nurse-williams`, `clerk-brown`, `billing-davis`) is already inside
SpiceDB's charset, hyphens included.

### Findings

None. Every construct — including the two-level resource hierarchy underneath the tenant
root and the 5-way `member` union — resolved using rules already on file, with no ambiguity
and no fix-and-rerun cycle. The schema converted `PARITY OK` on the first attempt, the
negative control behaved as the pack predicts, and the full live-server
list_objects/list_users sweep confirmed 100% of the store's own oracle. This is the batch's
multi-tenancy exemplar, structurally distinct from `iot` and `slack` (batch 3's zero-finding
pair, both zero arrows per the canonical table): both of those reach every permission through
same-type unions and foreign-type userset subjects with no tenant-scoped resource hierarchy at
all (per their own committed "Not exercised" notes), while this store's entire permission
surface is scoped under one `organization` walked back to through plain arrows.

### What the harness could not see

The harness compared **101 of this store's 113 assertions (89.4%)** — see "The canonical
store table" → **Harness-visible fraction** (between `call-center` at 85.2% and
`multitenant-rbac` at 92%). The 6 `list_objects` and 6 `list_users` assertions are silently
dropped by the harness per the known gap; all twelve were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express on its own (`patient:doe`'s `can_view` subject set is exactly those four
people, not merely inclusive of them).

---

## `modeling-guide`

**Which file this store converts, and why.** Unlike every other corpus store,
`corpus/sample-stores/stores/modeling-guide/` has no `store.fga.yaml` at all — it holds ten
`step-N-*.fga.yaml` files, one per stage of OpenFGA's own published modeling-guide video
series, each adding one feature on top of the last (multi-tenancy, groups, public access,
relationship-based ABAC, super-admin, conditional relationships, custom roles, application
access, fine-grained API access). All ten are independently green under `fga model test`, and
their `Checks` counts grow monotonically with each step (4, 8, 12, 14, 18, 18, 20, 24, 28,
then **30** on step 10 — confirmed by running `fga model test --tests <file>` against each of
the ten files directly, not inferred from file size), consistent with each step being
cumulative on the one before it. "Cumulative" is a superset claim, not an identity claim, and
one relation is genuinely dropped, not merely renamed, between step 9 and step 10: step 9's
`organization.application: [application]` feeds `admin` (`admin: [user] or super_admin from
system or application`) and is unioned into `can_edit_documents`/`.can_add_admin`/
`.can_create_document` as a blanket `or application`; step 10 removes the `application`
relation from `organization` entirely, drops the `or application` disjunct from `admin`, and
instead lists `application` directly in each of those three permissions' own type list
(`[role#assignee, application] or admin`) behind the comment `# allow defining permissions per
application` — a deliberate, upstream-authored narrowing from "any application is admin" to
per-permission grants, confirmed by reading both step files' `model:` blocks side by side, not
inferred from the Checks-count growth. This run converts only
**`step-10-fine-grained-api-access.fga.yaml`**, the last and most feature-complete step, as
this store's canonical model for the corpus — it is the only file that exercises every prior
step's constructs at once. Steps 1-9 are not separately converted; this is a scope decision
for this batch, not a claim that they are structurally identical to step 10.

**Baseline:** green — `fga model test --tests step-10-fine-grained-api-access.fga.yaml`:
`Tests 8/8 passing, Checks 30/30 passing`. No `list_objects`/`list_users` blocks anywhere in
this file — a check-only source, joining `role-assignments` (this batch) in that derived set
(see "The canonical store table" → **Check-only sources**). Model carried in an inline
`model:` block inside the `.fga.yaml` file itself (the third of the three source-model shapes
this pack's verified facts describe), not a separate `.fga` file.

Across eight
types (`user`, `application`, `system`, `role`, `organization`, `group`, `folder`, `document`)
it exercises multi-tenancy (`organization` as tenant root, `admin` reachable both directly and
via a `system`-wide super-admin), nested usersets (`group.member: [user, group#member]`, a
genuine two-level group-in-group walk in the fixture — `group:engineering`'s members are
members of `group:everyone` via a userset tuple), a wildcard directly on a relation
(`document.viewer__direct: user | user:*`), SpiceDB intersection
(`document.can_view = ((viewer & published->viewer) + can_edit)`, a "this document is
published, and I can view the thing it's published as" ABAC-via-ReBAC gate), and a caveat
(`time_based_grant`, the identical `current_time < grant_time + grant_duration` shape
`temporal-access` and `superadmin` already established, gate 1 reapplied a second time) — five
of the five construct families this batch was chosen to cover, in one schema. It also reuses
the same-name-recursive-arrow shape `gdrive` (iteration 14) and `expenses` (this batch) both
already carry
(`folder.can_edit = (editor + owner + parent->can_edit + organization->can_edit_documents)`,
`parent->can_edit` arrowing into the very permission being defined) — dormant in this store's
own fixture the same way it is in `gdrive`'s, since `folder:root` is the only `folder` object
either store's data ever populates — and the two custom-roles
compositions already on file (`custom-roles`' union-based grant, here as
`organization.can_edit_documents/.can_add_admin/.can_create_document: [role#assignee,
application] or admin`; and reuses the fact that a non-`user` type — `application` — can sit
in an ordinary type list beside a userset with no special handling).

**Final harness run:** `PARITY OK`, exit **0**, first attempt, 30 of 30 assertions compared.
`zed validate`: 20 relationships loaded, 30 assertions run. Also clean under `--fail-on-warn`.
Negative-control-verified per this file's standing method: flipping
`document:welcome#can_edit@user:anne` to `assertFalse` fails `zed validate` itself (exit 1
from `zed`, exit 2 from the harness, with `zed`'s own explanation trace showing
`can_edit ⨉ editor ⨉ owner` failing directly on `document:welcome` and then succeeding through
`folder:root can_edit -> owner -> user:anne`); deleting the same assertion makes the harness
report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 20 relationships loaded via `WriteRelationships` (including one caveated relationship,
  `system:root#super_admin@user:sam[time_based_grant:{...}]`), all 30 check-block assertions
  match exactly — including both time-varying assertions on `user:sam` (`true` nine seconds
  into the grant, `false` the next day), matching `fga model test`'s own `Checks 30/30
  passing` one for one.
- **Supplementary probe beyond the source's own oracle, matching this file's standing method
  of testing with data outside the source fixture:** does the wildcard-through-intersection
  gate on `document:document-not-published` (which has a direct `user:*` viewer tuple but no
  `published` marker) correctly stay closed for a subject entirely absent from the fixture?
  `zed permission check document:document-not-published can_view user:zzz-outside-fixture`
  returns `false`; after writing a self-referential `published` marker
  (`document:document-not-published#published@document:document-not-published`, mirroring
  `public-roadmap`'s own real structure) the same check flips to `true`; deleting that probe
  tuple reverts it to `false`. This confirms the intersection's `published->viewer` arm
  behaves as an ordinary empty-tupleset-is-false arrow, not a special case, and that the
  wildcard on the other arm survives the arrow-plus-intersection combination for a subject the
  source fixture never mentions.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all eight types (`user`, `application`, `system`, `role`, `organization`, `group`, `folder`, `document`) | schema-mapping construct table |
| Pure-direct relation, no split | `system.super_admin` (carries `with cond`, no operator), `role.assignee`, `group.member`, `organization.system`, `folder.organization`/`.parent`/`.owner`/`.viewer`/`.editor`, `document.parent`/`.owner`/`.editor`/`.published` | split rule, final bullet |
| Caveat on a bare/userset-typed relation, temporal gate reapplied | `system.super_admin: user with time_based_grant` | "Temporal access: caveat vs. native expiration", gate 1 applied unchanged a second time, now through **three** arrow hops (deepest yet) — see Finding 1 |
| Relation/permission split (type list fused with an operator) | `organization.admin: [user] or super_admin from system`; `organization.can_edit_documents`/`.can_add_admin`/`.can_create_document: [role#assignee, application] or admin`; `document.viewer: [user, user:*] or viewer from parent` | "The relation/permission split" |
| Wildcard directly on the relation being translated | `document.viewer__direct: user \| user:*` | construct table row `define viewer: [user:*]` |
| Non-`user` type as an ordinary subject alongside a userset, in one type list | `organization.can_edit_documents__direct`/`.can_add_admin__direct`/`.can_create_document__direct: role#assignee \| application` | construct table row `define viewer: [user, group#member]`, generalized to any subject type — no `user`-specificity in the rule |
| Nested userset (group-in-group) | `group.member: user \| group#member`, exercised by `group:engineering#member@group:everyone#member` in the fixture | construct table row `define viewer: [user, group#member]`, self-referential |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin`'s `system->super_admin__perm`; `document.viewer`'s `parent->viewer__perm` (`folder.viewer` never splits) | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `folder.can_edit`'s `organization->can_edit_documents`; `document.can_edit`'s `parent->can_edit`; `document.can_view`'s `published->viewer` | "Point arrows at permissions, not relations" — no-alias branch |
| Same-name recursive arrow (a permission's own arrow target names the permission being defined) | `folder.can_edit = (editor + owner + parent->can_edit + organization->can_edit_documents)` | "A self-referential arrow…" subsection (`abac-with-rebac`), same-name paragraph added this batch — third schema in the corpus to carry this shape (`gdrive`, `expenses`, this store), dormant here exactly as in `gdrive`'s own fixture |
| Self-referential arrow (same-type, different permission name) | `document.can_view`'s `published->viewer` (`document` → `document`) | "A self-referential arrow… needs no special rule" (`abac-with-rebac`) |
| SpiceDB intersection, nested inside a union | `document.can_view = ((viewer & published->viewer) + can_edit)` | construct table row `define view: a and b`; "Always fully parenthesize" — see "The canonical store table" → **SpiceDB intersection (`&`)** for the nesting-direction note |
| Bare two-operand union of a relation and a same-type permission reference | `folder.can_view = (viewer + can_edit)` | construct table row `define view: a or b` — not a bare alias (`define view: viewer`), which has only one operand |
| Runtime-defined roles, union-based grant | `organization.can_edit_documents`/`.can_add_admin`/`.can_create_document: [role#assignee, application] or admin` | "Runtime-defined ('custom') roles" — the `custom-roles` shape, not the `role-assignments` (this batch) intersection-based one |

**Not exercised:** the transitive-wildcard blocker's actual rejected shape (no userset
type-list entry `[T#rel]` anywhere points at a wildcard-bearing bare relation in this store —
`document.viewer`'s wildcard is reached only directly or through the split, never through a
userset reference), the intersection-based runtime-role shape (`role-assignments`, this
batch), multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` —
every object ID (`root`, `welcome`, `acme`, `engineering`, `everyone`, `public-roadmap`,
`document-not-published`, `acme-organization-manager`, `acme-content-editor`, `app-1`,
`app-2`, `anne`, `bob`, `peter`, `martin`, `john`, `omar`, `edith`, `sam`) is already inside
SpiceDB's charset, hyphens included.

### Findings

**1 — A caveat's temporal gate composes unchanged through three nested arrow hops, one
deeper than any prior caveat-bearing corpus store.**
*Classification: ambiguous guidance → worked example (a corpus confirmation extending an
already-general claim to a hop count no prior store had exercised, the identical treatment
`superadmin`'s own two-hop confirmation received).*
*Changed: `references/schema-mapping.md` ("The gate decision applies unchanged when the
condition sits behind arrows…" subsection, extended with a `modeling-guide` paragraph and a
corrected enumeration of every caveat-bearing store's own hop count).*

Every prior caveat-bearing store puts its condition at most two arrow hops from the
permission actually checked — zero for `condition-data-types`, `temporal-access`, and
`advanced-entitlements`; one for `ip-based-access`, `banking`, and
`groups-resource-attributes`; two for `superadmin`, the previous deepest (verified by reading
each store's own committed `schema.zed`, not recalled — see the updated schema-mapping.md
subsection for the per-store citations). `document.can_edit`'s path to
`system.super_admin` — `document->folder` (`parent->can_edit`), `folder->organization`
(`organization->can_edit_documents`), `organization->system`
(`system->super_admin__perm`), with two same-type permission references adding no further
hops — is **three**. This matches "The canonical store table" → **Arrow-chain hop depth**'s
own mechanical output (`modeling-guide max_hops=3 (document.can_edit)`), independently
confirming the count. Verified live and via the harness exactly as `superadmin`'s two-hop case
was: both time-varying assertions on `user:sam` (see "Final harness run" above) resolved
correctly through the full three-hop chain, matching `fga model test`'s own `Checks 30/30
passing`. No new rule was needed — hop count is not a parameter of the caveat/arrow mechanism,
as the existing subsection already states — so this is recorded as a second confirmation
extending that claim's tested depth, not a new construct.

### What the harness could not see

Nothing. This store's `tests:` block is entirely `check:` — no `list_objects`/`list_users` at
all — so the harness's own comparison (**30 of 30 assertions, 100%**) already covers the
store's full oracle. See "The canonical store table" → **Check-only sources** and
**Harness-visible fraction**.

---

## `role-assignments`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 1/1 passing,
Checks 8/8 passing`. No `list_objects`/`list_users` blocks — a check-only source, joining
`modeling-guide` (this batch) in that derived set. Model carried in an inline `model:` block
inside `store.fga.yaml` itself.

This store is a five-type "reusable custom role" model — OpenFGA's own README frames it as an
alternative to `custom-roles`' shape, and the two really are structurally different, not just
differently named. `role` holds the grantable capabilities as bare, wildcard-flagged relations
(`can_view_project: [user:*]`, `can_edit_project: [user:*]` — written once per role, meaning
"this role grants this permission if reached," not "everyone has it" in isolation).
`role_assignment` mediates between a specific `assignee` and a `role`, and its own permission
is an **intersection**, not a union: `can_view_project: assignee and can_view_project from
role`. `organization`/`project` layer an ordinary admin-override tenancy shape on top. This is
the batch's dedicated intersection exemplar, and, of the two other stores with a literal
`user:*` anywhere (`grep -n 'user:\*' */schema.zed`: `gdrive`, on a resource's own `viewer`
relation, and `modeling-guide`, same shape, this batch), the only one where the wildcard sits
on a role's own grantable-capability relation rather than a resource's viewer relation.

**Final harness run:** `PARITY OK`, exit **0**, first attempt, 8 of 8 assertions compared.
`zed validate`: 8 relationships loaded, 8 assertions run. Also clean under `--fail-on-warn`.
Negative-control-verified per this file's standing method: flipping
`project:openfga#can_view@user:anne` to `assertFalse` fails `zed validate` itself (exit 1 from
`zed`, exit 2 from the harness, with `zed`'s own explanation trace showing both operands of
the intersection resolve true independently — `role_assignment:acme-project-admin-openfga
assignee -> user:anne` and `role:acme-project-admin can_view_project__perm -> can_view_project
-> user:anne` via the wildcard); deleting the same assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 8 relationships loaded via `WriteRelationships`, all 8 check-block assertions match
  exactly, matching `fga model test`'s own `Checks 8/8 passing` one for one.
- **Supplementary probe with a subject entirely outside the source fixture, matching this
  file's standing method:** `user:zzz-not-in-fixture` has no tuple anywhere in the store.
  `zed permission check project:openfga can_view user:zzz-not-in-fixture` returns `false` —
  the public `user:*` grant on `role:acme-project-admin` alone is not enough, confirming the
  intersection actually gates the wildcard rather than the wildcard silently making the whole
  permission public. Granting only
  `role_assignment:acme-project-admin-openfga#assignee@user:zzz-not-in-fixture` (one write,
  the assignment side) flips the same check to `true`, confirming the wildcard on
  `role.can_view_project` is reached correctly through the arrow, the `__perm` alias, and the
  intersection for a subject the source fixture never mentions.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all five types (`user`, `role`, `role_assignment`, `organization`, `project`) | schema-mapping construct table |
| Pure-direct relation, no split | `role_assignment.assignee`/`.role`, `organization.admin`, `project.organization`/`.role_assignment` | split rule, final bullet |
| **Wildcard directly on the relation being translated, reached only through an arrow (never a userset)** | `role.can_view_project`/`.can_edit_project: [user:*]` | construct table row `define viewer: [user:*]`; "Wildcards" note |
| Arrow into a bare relation, needing a `__perm` alias | `role.can_view_project__perm`/`.can_edit_project__perm`, reached by `role_assignment.can_view_project`/`.can_edit_project`; `organization.admin__perm`, reached by `project.can_view`/`.can_edit` | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `project.can_view`'s `role_assignment->can_view_project`; `project.can_edit`'s `role_assignment->can_edit_project` | "Point arrows at permissions, not relations" — no-alias branch |
| **SpiceDB intersection, two atomic operands, one side an arrow into a wildcard-bearing relation** | `role_assignment.can_view_project = (assignee & role->can_view_project__perm)`, `.can_edit_project` likewise | construct table row `define view: a and b`; "Always fully parenthesize" — see Finding 1 |
| **Runtime-defined roles, intersection-based grant (second shape, distinct from `custom-roles`' union-based one)** | the whole `role`/`role_assignment` pair above | "Runtime-defined ('custom') roles" → new subsection added by this store, this batch — see Finding 2 |
| Two-operand union | `project.can_view = (role_assignment->can_view_project + organization->admin__perm)`, `.can_edit` likewise | "Always fully parenthesize" |

**Not exercised:** the transitive-wildcard blocker's actual rejected shape (no userset
type-list entry `[T#rel]` anywhere in this store — `role_assignment.role: [role]` is a bare
type reference, not a userset, so the only path to `role`'s wildcard relations is the arrow),
caveats/conditions, the relation/permission split (no `define` in this store mixes a type
list with an operator), recursive arrows, multi-tenancy at more than one tenant (the fixture
never populates `organization`/`project.organization` at all — both test projects reach
`can_view`/`can_edit` purely through `role_assignment`), modular models, multi-store tenancy,
model-ID pinning, contextual tuples. `id_encoding` is `none` — every object ID
(`acme-project-admin`, `acme-project-admin-openfga`, `acme-project-admin-java-sdk`, `openfga`,
`java-sdk`, `anne`, `bob`) is already inside SpiceDB's charset, hyphens included.

### Findings

**1 — First real-corpus confirmation that an arrow into a pure-direct, wildcard-bearing
relation is exempt from the transitive-wildcard typesystem check — previously verified only
with a synthetic control schema.**
*Classification: ambiguous guidance → worked example (the underlying fact was already fully
settled in `blockers.md`, verified against a hand-built control schema; this store is the
first to exercise it with real corpus data, the same treatment `ip-based-access`'s "first
real-corpus confirmation of intersection" received).*
*Changed: `references/blockers.md` ("Transitive wildcard" section, new paragraph following
the synthetic-control verification).*

`blockers.md` already stated, from a synthetic control schema, that "an arrow (`->`) is exempt
from the transitive-wildcard typesystem check entirely, independent of the split," and flagged
the *pure-direct* case specifically (no operator, so no split rescues it) as "still needs its
own corpus store." `role.can_view_project`/`.can_edit_project` are exactly that shape —
`[user:*]`, no operator — and `role_assignment.can_view_project`'s arrow reaches them directly.
`WriteSchema` accepted the schema unedited, the harness reached `PARITY OK` on the first
attempt, and the supplementary live probe above (a subject with no fixture tuple at all)
confirms the wildcard itself, not just an assigned subject, survives both the arrow and the
intersection layered on top of it. This retires the "still needs its own corpus store" caveat
for the pure-direct case; the mechanism was already correctly predicted, and now has real
corpus data behind it too.

**2 — A second runtime-role encoding shape: capability gated by intersection with the
assignment, rather than unioned into the resource's own permission.**
*Classification: ambiguous guidance → worked example (a new composition of already-written
rules — the split, the arrow-alias rule, and intersection — not a new construct, but one a
translator meeting it cold could plausibly fail to recognize as covered).*
*Changed: `references/schema-mapping.md` ("Runtime-defined ('custom') roles" section, new
subsection.)*

`custom-roles` unions `[role#assignee]` directly into the *resource's own* permission
(`define asset_creator: [role#assignee] or owner`); granting a role a capability and granting
a user that capability directly are the same write, onto the same relation. This store
instead holds grantable capabilities on the `role` type itself as wildcard-flagged relations,
and gates them through an intersection on a third, mediating type (`role_assignment`). Every
piece is a rule already on file, but the composition is different enough from the one
`schema-mapping.md` actually describes that it is recorded explicitly rather than left to be
rediscovered. No fidelity change: still `effort`, for the identical reason `custom-roles`
is — the split's `__direct` suffix is the only non-mechanical piece, and this store's split
count is zero (see the canonical table), so even that qualifier does not apply here.

### What the harness could not see

Nothing. This store's `tests:` block is entirely `check:` — no `list_objects`/`list_users` at
all — so the harness's own comparison (**8 of 8 assertions, 100%**) already covers the store's
full oracle. See "The canonical store table" → **Check-only sources** and
**Harness-visible fraction**.

---

## `file-storage`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 13/13 passing,
Checks 20/20 passing, ListObjects 5/5 passing, ListUsers 5/5 passing`. Model carried in a
separate `model.fga` file.

This is a six-type Google-Drive-shaped model built around one tenant root (`organization`)
and a two-level resource tree (`drive` → `folder` → `folder` → `file`, folders nesting inside
folders to arbitrary depth). Its own `README.md` (not `store.fga.yaml`, a plain prose
description) states the design intent directly: "Folders have a single `parent` relation that
accepts both drives and other folders. Permissions cascade from parent to child" and "Owner
propagation: Folder ownership cascades to child folders and files, so the owner of a top-level
folder owns the entire subtree" — i.e. `folder.parent: [drive, folder]` is deliberately
two-typed, not an oversight, and the resulting recursion is the model's whole point. Batch 5's
diversity brief flagged this shape (chosen for structural diversity, not because its contents
were known ahead of time) and it holds: this is the first corpus store where a *tupleset*
relation (not just a subject type list) allows two distinct plain object types side by side.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 15
relationships loaded, 19 assertions run. Also clean under `--fail-on-warn`. The store's own
`Checks 20/20` collapses to **19 distinct** `(subject, permission, resource)` keys once
deduplicated — "Folder owner can manage folder" and "Drive reader can read nested folders"
both assert `folder:projects#can_view@user:bob` as `true`, the same exact-duplicate pattern
`ads`, `groups-resource-attributes`, and `calendar` already established. Negative-control-
verified per this file's standing method: flipping `drive:shared-drive#can_view@user:alice` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the walk `can_view -> can_edit -> writer -> owner ->
user:alice`); deleting `drive:shared-drive#can_delete@user:alice` makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 15 relationships loaded via `WriteRelationships`, all 19 distinct check-block assertions
  match exactly, matching the harness's own `19 assertions compared` one for one.
- All **5** `list_objects` and all **5** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `user:alice`'s owned-drive set is exactly `{shared-drive}`, her
  viewable-folder set is exactly `{projects, backend}`; `user:charlie`'s editable-folder set is
  exactly `{backend}` and owned-file set is exactly `{api-spec}`; `user:bob`'s owned-drive set
  is exactly `{bobs-drive}`; `drive:shared-drive`'s viewer set is exactly `{alice, bob,
  charlie}` and editor set is exactly `{alice}`; `file:api-spec`'s viewer/editor/deleter sets
  are each exactly `{alice, bob, charlie}`. This store's full oracle is **30/30 confirmed**
  (20 check + 5 list_objects + 5 list_users, `Checks + ListObjects + ListUsers` per `fga model
  test`'s own count).
- **Supplementary probe beyond the source's own oracle** (this file's standing method):
  `folder.organization_admin` is present in the schema but never once exercised by the
  source's own `check:`/`list_*` oracle — no test asserts `can_delete` (or anything that
  reaches it) on any `folder` object, confirmed by reading every assertion in `store.fga.yaml`.
  Writing `folder:deep-folder#parent@folder:backend` — a **third** level of folder nesting the
  source fixture never reaches (the fixture's own deepest chain is two: `backend` → `projects`
  → `drive:shared-drive`) — and checking `user:alice` against `folder:deep-folder#can_delete`
  returns `false` before the write and `true` after. **Correction (batch 6): this is not, as an
  earlier draft claimed, "purely through four arrow crossings."** `alice` is both the
  organization's only `admin` *and* `drive:shared-drive`'s direct `owner`, so the probe is
  confounded — `--explain` on the post-write check shows only the `owner` branch evaluated
  (`deep-folder.owner` → `backend.owner` → `projects.owner` → `drive:shared-drive.owner@alice`,
  three arrow crossings; the `organization_admin` child of the same `can_delete` union never
  appears in the trace at all). Isolating `organization_admin` with a second, unconfounded
  subject (`user:admin-only-probe`, granted only `organization:acme#admin`, no folder or drive
  ownership anywhere) against the same `folder:deep-folder#can_delete` does resolve `true`
  purely through four arrow crossings (`folder->folder->folder->drive->organization`:
  `deep-folder`→`backend`, `backend`→`projects`, `projects`→`drive:shared-drive`,
  `drive`→`organization`), `--explain` confirming its `owner` branch evaluates `false` at every
  level while `organization_admin` alone carries the `true` — with no depth bound encoded
  anywhere in the schema, which is the claim this probe actually supports;
  `user:charlie` (granted `writer` only at `folder:backend` via `group:engineering#member`)
  correctly inherits `can_view`/`can_edit` down to the third level too, and a subject with no
  fixture presence at all (`user:zzz-outside-fixture`) is correctly denied throughout.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all six types (`user`, `organization`, `group`, `drive`, `folder`, `file`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin: [user]`, `group.organization: [organization]`/`.member: [user]`, `drive.organization: [organization]`/`.owner: [user]`, `folder.parent: [drive, folder]`, `file.folder: [folder]` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin`; `drive.writer: [user, group#member] or owner`/`.reader: [user, group#member] or writer or member from organization`; `folder.owner: [user] or owner from parent`/`.writer: [user, group#member] or owner or writer from parent`/`.reader: [user, group#member] or writer or reader from parent`; `file.owner: [user] or owner from folder`/`.writer: [user, group#member] or owner or writer from folder`/`.reader: [user, group#member] or writer or reader from folder` | "The relation/permission split" |
| Userset subject in a type list alongside a bare type | `drive.writer__direct`/`.reader__direct`, `folder.writer__direct`/`.reader__direct`, `file.writer__direct`/`.reader__direct`, all `user \| group#member` | construct table row `define viewer: [user, group#member]` |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin__perm`, reached by `drive.organization_admin`'s `organization->admin__perm` | "Point arrows at permissions, not relations" — alias branch |
| **A multi-type tupleset resolving the same arrow target to a relation on one allowed type and a permission on another, needing the alias on *every* allowed type** | `folder.parent: [drive, folder]` feeding `folder.owner = (owner__direct + parent->owner__perm)`, where `drive.owner__perm` and `folder.owner__perm` are both new aliases (`drive.owner` alone would never need one) | new subsection this store forced in "Point arrows at permissions, not relations" — see Finding 1 |
| Arrow into an already-permission target, no alias | `drive.reader`'s `organization->member`; `folder.organization_admin = parent->organization_admin`; `folder.writer`'s `parent->writer`; `folder.reader`'s `parent->reader`; `file.owner`'s `folder->owner`; `file.writer`'s `folder->writer`; `file.reader`'s `folder->reader`; `file.can_delete`'s `folder->organization_admin` | "Point arrows at permissions, not relations" — no-alias branch |
| Same-name recursive arrow (a permission's own arrow target names the permission being defined), on a multi-type tupleset | `folder.organization_admin = parent->organization_admin`; `folder.owner`/`.writer`/`.reader` each fold a `parent->` arrow of their own name into their union | "A self-referential arrow…" / "A same-name recursive arrow…" subsections (`gdrive`, `expenses`, `modeling-guide`) — fourth corpus store to carry this shape, and the first live-probed past two levels (see supplementary probe above) |
| Type-based tenancy | `organization` reached directly by `group`/`drive` and transitively by `folder`/`file` | "Type-based tenancy" — no new construct |
| Bare union of a relation with same-type permission references | `drive.can_delete = (owner + organization_admin)`/`.can_edit = (writer + can_delete)`/`.can_view = (reader + can_edit)`; `folder.can_delete = (owner + organization_admin)`/`.can_create_file = (writer + can_delete)`/`.can_edit = (writer + can_delete)`/`.can_view = (reader + can_edit)`; `file.can_edit = (writer + can_delete)`/`.can_download = (reader + can_edit)`/`.can_view = (reader + can_edit)` | construct table row `define view: a or b` |

**Not exercised:** caveats/conditions, wildcards, intersection, runtime-defined roles, modular
models, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` —
every object ID (`acme`, `engineering`, `shared-drive`, `bobs-drive`, `projects`, `backend`,
`api-spec`, `alice`, `bob`, `charlie`) is already inside SpiceDB's charset, hyphens included.

### Findings

**1 — A tupleset relation with more than one allowed bare type can resolve the arrow's target
name to a relation on one type and a permission on another, and the `__perm` alias must be
applied to every allowed type, not only the ones that individually need it.**
*Classification: ambiguous guidance → worked example (a new composition of already-written
rules — the split and the alias mechanism — not a new construct, but one where the existing
alias bullet's "only when needed" test has an unstated "for this one target type" that a
multi-type tupleset breaks).*
*Changed: `references/schema-mapping.md` ("Point arrows at permissions, not relations"
section, new subsection.)*

`drive.owner: [user]` has no operator and stays a bare relation by the split rule's final
bullet; `folder.owner: [user] or owner from parent` fuses a type list with an operator and
splits into a permission. Both `drive` and `folder` are allowed types of `folder.parent`, so
the single arrow `parent->owner` must resolve against whichever one the subject is. Verified
live on v1.56.0: writing the arrow as `parent->owner` (with the alias applied only to `drive`,
since a literal reading of the existing alias bullet says that is the only type that needs
one) still produces exactly one `arrow-references-relation` warning, for the `drive` branch —
the arrow's token is still `owner`, and `owner` is still a bare relation there regardless of
what else exists under a different name. `--fail-on-warn` only goes clean once the alias
(`owner__perm`) exists on **both** `drive` and `folder`, and the arrow is rewritten to target
`owner__perm` instead of `owner`. Checked before writing this as new: no other committed
`schema.zed` uses a multi-type relation as an arrow's tupleset at all — `superadmin`'s only
type list of the same shape (`system.admin: employee | application`) is never a tupleset,
since both `employee` and `application` are empty types nothing ever arrows through; every
other multi-type relation in the corpus mixes a bare type with a userset (`user |
group#member`) or a type with its own wildcard/caveated variant, never two distinct plain
object types independently defining the same target name. `schema-mapping.md` now carries this
as a worked example next to the alias rule it extends, not folded silently into it.

**Addendum, recorded during batch 6 / iteration 17 (not by re-deriving this store):** a
second, sharper gap in the same construct — applying the `__perm` alias to only *some* of a
multi-type tupleset's allowed types is silent under `zed validate --fail-on-warn` (zero
warnings, not the one this Finding's own alias-omission produces), and wrong on live data for
whichever type's alias is missing — was found by review after this store's own conversion,
not forced by re-running it. `file-storage`'s `folder.parent: [drive, folder]` is this
construct's sole bearer in the corpus (see "Checked before writing this as new" above), so
this store's own committed `schema.zed` is the only place the gap could have appeared, and it
does not: both `drive.owner__perm` and `folder.owner__perm` are present, confirmed clean by
`schema-mapping.md`'s `check_arrow_targets.py` (exit 0). See `schema-mapping.md`'s "Partial
alias application on a multi-type tupleset is silent" subsection for the rule and detection
method, and this file's iteration 17 note (in `human-resources`'s Findings section, the last
of that iteration's four stores) for why it is recorded there rather than attributed to any
one store.

### What the harness could not see

The harness compared **19 of this store's 30 assertions (63.3%** counting distinct keys after
dedup; **66.7%** by the canonical table's own `Checks / (Checks + ListObjects + ListUsers)`
metric, `20/30` — see "The canonical store table" → **Harness-visible fraction** for which
ordering this file states). The 5 `list_objects` and 5 `list_users` assertions are silently
dropped by the harness per the known gap; all ten were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express on its own (`file:api-spec`'s viewer/editor/deleter sets are each exactly
those three people, not merely inclusive of them) and the supplementary third-level-folder
probe, which exercises `organization_admin` recursion the source's own oracle never touches at
all.

---

## `issue-tracking`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 18/18 passing,
Checks 34/34 passing, ListObjects 9/9 passing, ListUsers 3/3 passing`. Model carried in a
separate `model.fga` file.

This is an eight-type support-ticket model built around one tenant root (`organization`) with
two independent role tracks (`admin`, `agent`) feeding a 3-way `member` union, a `team` layer
whose own `can_manage` permission unions a local `lead` relation with an arrow into
`organization`'s `admin`, one arrow-fanned resource hierarchy (`collection` → `ticket` →
`comment`/`attachment`), and a sibling leaf type (`contact`) reached directly off the tenant
root.
`collection.parent_collection: [collection]` gives `collection.viewer` the same same-name
recursive-arrow shape `gdrive`/`expenses`/`modeling-guide`/`file-storage` (this batch) already
carry, but on a **single**-type tupleset (`[collection]`, not `file-storage`'s two-typed
`[drive, folder]`) — so none of that store's multi-type alias question arises here. Unlike
`file-storage`'s deep, live-probed folder nesting, this store's own fixture populates exactly
one level of the hierarchy (`collection:ui-bugs`'s `parent_collection` points at
`collection:bugs`, which itself has none), so the recursive arm is walked live exactly once —
neither dormant (`gdrive`, `modeling-guide`'s `folder.can_edit`, and `file-storage`'s
`organization_admin` before this store's own supplementary probe) nor multi-level
(`expenses`), a data point the existing rule already covers without needing either qualifier.

**Final harness run:** `PARITY OK`, exit **0**, first attempt, 34 of 34 assertions compared
(no duplicate-collapse this time — every one of the 34 source `check:` assertions names a
distinct `(subject, permission, resource)` key). `zed validate`: 19 relationships loaded, 34
assertions run. Also clean under `--fail-on-warn`. Negative-control-verified per this file's
standing method: flipping `ticket:bug-123#can_view@user:alice` to `assertFalse` fails `zed
validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own explanation
trace showing the walk `ticket:bug-123 can_view -> viewer -> collection:bugs viewer ->
organization_admin -> organization:acme admin__perm -> admin -> user:alice`); deleting
`ticket:bug-123#can_delete@user:alice` makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag (including the
  `// spicedb-ignore-warning: relation-name-references-parent` comment on
  `collection.parent_collection`, which `zed schema read` afterward still shows intact).
- All 19 relationships loaded via `WriteRelationships`, all 34 check-block assertions match
  exactly, matching the harness's own `34 assertions compared` one for one.
- All **9** `list_objects` and all **3** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `user:alice`/`user:bob`/`user:diana` can each `can_view` exactly
  `{ticket:bug-123}`; `user:bob`'s viewable-collection set is exactly `{bugs, ui-bugs}` and
  editable set exactly `{bugs}`; `user:diana`'s viewable/deletable-attachment sets are each
  exactly `{screenshot}`; `user:bob`'s viewable/editable-comment sets are each exactly
  `{c-001}`; `ticket:bug-123`'s viewer set is exactly `{alice, bob, diana, charlie}` and its
  closer set exactly `{alice, bob, charlie}`; `collection:bugs`'s viewer set is exactly
  `{alice, bob}`. This store's full oracle is **46/46 confirmed** (34 check + 9 list_objects +
  3 list_users, `Checks + ListObjects + ListUsers` per `fga model test`'s own count).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all eight types (`user`, `organization`, `team`, `collection`, `ticket`, `comment`, `attachment`, `contact`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.agent`, `team.organization`/`.member`/`.lead`, `collection.organization`/`.owner`, `ticket.collection`/`.assignee`/`.assigned_team`/`.creator`/`.reporter`, `comment.ticket`/`.author`, `attachment.ticket`/`.uploader`, `contact.organization` | split rule, final bullet |
| Self-type relation whose name ends in its own type's name, lint suppressed in place | `collection.parent_collection: [collection]` | codegen rule ("A carried-over source name…") — reuses the suppress-in-place fix `advanced-entitlements` established, not a new rule |
| Relation/permission split | `organization.member: [user] or admin or agent`; `collection.viewer: [user, team#member] or owner or organization_admin or viewer from parent_collection`; `ticket.viewer: [user, team#member] or assignee or creator or reporter or member from assigned_team or viewer from collection`, `.editor: [user] or assignee or member from assigned_team` | "The relation/permission split" |
| Userset subject in a type list alongside a bare type | `collection.viewer__direct`, `ticket.viewer__direct`, both `user \| team#member` | construct table row `define viewer: [user, group#member]` |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin__perm` (reached by `team.can_manage`, `collection.organization_admin`, `contact.can_edit`); `organization.agent__perm` (reached by `collection.organization_agent`, `contact.can_edit`); `team.member__perm` (reached by `ticket.viewer`/`.editor`); `team.lead__perm` (reached by `ticket.team_lead`) | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `collection.organization_admin = organization->admin__perm`; `collection.organization_agent = organization->agent__perm`; `ticket.organization_admin = collection->organization_admin`; `ticket.organization_agent = collection->organization_agent`; `comment.can_delete`'s `ticket->organization_admin`; `comment.can_view`'s `ticket->can_view`; `attachment.can_delete`'s `ticket->team_lead`/`ticket->organization_admin`; `attachment.can_view = ticket->can_view` | "Point arrows at permissions, not relations" — no-alias branch |
| Same-name recursive arrow, single-type tupleset, live one-level walk | `collection.viewer`'s `parent_collection->viewer` | "A self-referential arrow…" / "A same-name recursive arrow…" subsections — fifth corpus store with this shape (`gdrive`, `expenses`, `modeling-guide`, `file-storage` this batch, now this store), and the first with a single-type tupleset walked exactly once by live data |
| Type-based tenancy | `organization` reached directly by `team`/`collection`/`contact` and transitively by `ticket`/`comment`/`attachment` | "Type-based tenancy" — no new construct |
| Bare alias (permission referencing a relation or another permission directly) | `collection.can_delete = organization_admin`; `ticket.can_delete = organization_admin`; `comment.can_edit = author`; `contact.can_view = can_edit` | construct table row `define view: viewer` |
| Multi-operand union | `team.can_manage = (lead + organization->admin__perm)`, `.can_view = (member + can_manage)`; `collection.can_edit = (owner + can_delete)`, `.can_create_ticket = (owner + organization_agent + can_delete)`, `.can_view = (viewer + can_edit)`; `ticket.can_assign = (organization_agent + can_delete)`, `.can_close = (assignee + team_lead + can_delete)`, `.can_edit = (editor + can_assign)`, `.can_view = (viewer + can_close + can_edit)`; `comment.can_delete = (author + ticket->organization_admin)`, `.can_view = (can_edit + ticket->can_view)`; `attachment.can_delete = (uploader + ticket->team_lead + ticket->organization_admin)`; `contact.can_edit = (organization->admin__perm + organization->agent__perm)` | "Always fully parenthesize" |

**Not exercised:** caveats/conditions, wildcards, intersection, a multi-type tupleset (unlike
`file-storage`, this batch — `collection.parent_collection` allows only `collection`),
recursion past one live level, runtime-defined roles, modular models, multi-store tenancy,
model-ID pinning, contextual tuples. `id_encoding` is `none` — every object ID (`acme`,
`support`, `bugs`, `ui-bugs`, `bug-123`, `c-001`, `screenshot`, `customer-xyz`, `alice`, `bob`,
`charlie`, `diana`) is already inside SpiceDB's charset, hyphens included.

### Findings

None. Every construct — the tenant root, the two-track role union, the team layer, the
single-type same-name recursive arrow on `collection`, the four independent `__perm` aliases,
and the `relation-name-references-parent` lint suppression on `collection.parent_collection` —
resolved using rules already on file, each traced to the specific subsection above, with no
ambiguity and no fix-and-rerun cycle. The schema converted `PARITY OK` on the first attempt,
the negative control behaved as the pack predicts, and the full live-server
list_objects/list_users sweep confirmed 100% of the store's own oracle.

### What the harness could not see

The harness compared **34 of this store's 46 assertions (73.9%)** — see "The canonical store
table" → **Harness-visible fraction**. The 9 `list_objects` and 3 `list_users` assertions are
silently dropped by the harness per the known gap; all twelve were closed by direct
live-server verification (see "Final harness run" above), including the exhaustiveness half a
`check:` block cannot express on its own (`ticket:bug-123`'s viewer set is exactly those four
people, not merely inclusive of them).

---

## `kms`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 12/12 passing,
Checks 48/48 passing, ListObjects 14/14 passing, ListUsers 3/3 passing`. Model carried in a
separate `model.fga` file.

This is a five-type wiki/knowledge-base model built around one tenant root (`organization`)
with a **four**-way role split (`admin`/`editor`/`viewer`/bare `member`, the widest flat role
union in this batch) feeding a strict three-level resource chain, `organization` → `space` →
`page` → `comment`, each level re-deriving all three of `organization_admin`/
`organization_editor`/`organization_viewer` by arrowing straight to the level above rather than
through a `can_*` permission — the corpus's cleanest "same three names, re-derived at every
level" shape. Distinctively for this batch, `kms` has **zero** userset subjects anywhere
(`grep -n '#' model.fga` inside a type list matches nothing) and **zero** self-type or
multi-type tuplesets — no relation in this store ever points back at its own type or at more
than one type at all. Every tupleset relation (`organization`, `space`, `page`) is a single
bare foreign type, and every arrow is a plain one-hop-per-level walk up the chain. This is the
batch's plainest multi-tenancy exemplar, chosen alongside `file-storage` and `issue-tracking`
for contrast: same tenant-root shape, none of their self-referential recursion or userset
sharing.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 10 relationships
loaded, 42 assertions run. Also clean under `--fail-on-warn`. The store's own `Checks 48/48`
collapses to **42 distinct** `(subject, permission, resource)` keys once deduplicated — "Page
publishing requires admin" and "Comment author can edit and delete own comments" both re-assert
checks byte-for-byte identical to assertions made earlier in "Admin/Editor/Viewer has
access…", the same exact-duplicate pattern `ads`, `groups-resource-attributes`, `calendar`, and
`file-storage` (this batch) already established. Negative-control-verified per this file's
standing method: flipping `space:engineering#can_view@user:alice` to `assertFalse` fails `zed
validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own explanation
trace showing the walk `space:engineering can_view -> can_edit -> can_manage_members ->
can_delete -> organization_admin -> organization:wiki-co admin__perm -> admin -> user:alice`);
deleting `space:engineering#can_delete@user:alice` makes the harness report `MISSING`
(exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 10 relationships loaded via `WriteRelationships`, all 42 distinct check-block assertions
  match exactly, matching the harness's own `42 assertions compared` one for one.
- All **14** `list_objects` and all **3** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `user:alice`'s viewable/editable/deletable-space sets are each
  exactly `{engineering}`; `user:bob`'s viewable/editable-space set is exactly `{engineering}`;
  `user:charlie`'s viewable-space set is exactly `{engineering}`; the equivalent page and
  comment sets for each user match the same way; `space:engineering`'s viewer set is exactly
  `{alice, bob, charlie}`; `page:architecture-guide`'s editor set is exactly `{alice, bob}`;
  `comment:feedback-001`'s deleter set is exactly `{alice, charlie}`. This store's full oracle
  is **65/65 confirmed** (48 check + 14 list_objects + 3 list_users, `Checks + ListObjects +
  ListUsers` per `fga model test`'s own count).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all five types (`user`, `organization`, `space`, `page`, `comment`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.editor`/`.viewer`, `space.organization`/`.owner`, `page.space`/`.author`, `comment.page`/`.author` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin or editor or viewer` | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin__perm`/`.editor__perm`/`.viewer__perm`, reached by `space.organization_admin`/`.organization_editor`/`.organization_viewer` | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `page.organization_admin = space->organization_admin`, `.organization_editor = space->organization_editor`, `.organization_viewer = space->organization_viewer`; `comment.can_delete`'s `page->organization_admin`; `comment.can_view`'s `page->can_view` | "Point arrows at permissions, not relations" — no-alias branch |
| Type-based tenancy (tenant root reached transitively through `space`/`page`/`comment`) | `organization` → `space` → `page` → `comment` | "Type-based tenancy" — no new construct |
| Bare alias (permission referencing another permission directly) | `space.can_delete = organization_admin`; `page.can_publish = organization_admin`; `comment.can_edit = can_delete` | construct table row `define view: viewer` |
| Multi-operand union | `space.can_manage_members = (owner + can_delete)`, `.can_edit = (organization_editor + can_manage_members)`, `.can_view = (organization_viewer + can_edit)`; `page.can_delete = (author + can_publish)`, `.can_edit = (organization_editor + can_delete)`, `.can_view = (organization_viewer + can_edit)`; `comment.can_delete = (author + page->organization_admin)`, `.can_view = (page->can_view + can_edit)` | "Always fully parenthesize" |

**Not exercised:** caveats/conditions, wildcards, intersection, userset subjects of any kind
(no `T#rel` anywhere in this store, unlike every other store in this batch), self-type or
multi-type tuplesets, recursive arrows, runtime-defined roles, modular models, multi-store
tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` — every object ID
(`wiki-co`, `engineering`, `architecture-guide`, `feedback-001`, `alice`, `bob`, `charlie`,
`diana`) is already inside SpiceDB's charset, hyphens included.

### Findings

None. Every construct — the four-way role split, the three-level re-derived tenancy chain, and
the three `__perm` aliases reached through all eight of this store's arrows (`grep -o -- '->'
schema.zed | wc -l`, matching the canonical table's own **Arrows** column) — resolved using
rules already on file, each traced to the specific subsection above, with no ambiguity and no
fix-and-rerun cycle. The schema converted `PARITY OK` on the first attempt, the negative
control behaved as
the pack predicts, and the full live-server list_objects/list_users sweep confirmed 100% of the
store's own oracle. This is the batch's plainest multi-tenancy exemplar: no userset, no
self-reference, no multi-type tupleset anywhere, structurally distinct from `file-storage` and
`issue-tracking` (both this batch), which both carry a same-name recursive arrow.

### What the harness could not see

The harness compared **42 of this store's 65 assertions (64.6%** counting distinct keys after
dedup; **73.8%** by the canonical table's own `Checks / (Checks + ListObjects + ListUsers)`
metric, `48/65` — see "The canonical store table" → **Harness-visible fraction** for which
ordering this file states). The 14 `list_objects` and 3 `list_users` assertions are silently
dropped by the harness per the known gap; all seventeen were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express on its own (`space:engineering`'s viewer set is exactly those three
people, not merely inclusive of them).

---

## `payment`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 13/13 passing,
Checks 68/68 passing, ListObjects 7/7 passing, ListUsers 3/3 passing`. Model carried in a
separate `model.fga` file.

This is a seven-type payment-platform model built around one tenant root (`organization`) with
a three-way role split (`admin`/`finance_manager`/`viewer`) and **five sibling resource types**
(`link`, `payment`, `payout`, `refund`, `subscription`) hanging directly off `organization` —
no resource-to-resource nesting anywhere, unlike every other store converted this batch. Each
resource type independently arrows straight to `organization`'s role aliases: `can_delete`/
`can_approve` on four of the five resolve to a single-operand `organization->admin__perm`
arrow with nothing unioned alongside it, and `finance_manager__perm`/`viewer__perm` are each
reached by three to four of the five types the same way. This is a flat single-tenant fan-out,
structurally the opposite of `file-storage`'s and `issue-tracking`'s nested hierarchies and
`kms`'s single linear chain — this batch's fourth distinct shape of "several resource types
under one tenant root."

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 12 relationships
loaded, 60 assertions run. Also clean under `--fail-on-warn`. The store's own `Checks 68/68`
collapses to **60 distinct** `(subject, permission, resource)` keys once deduplicated —
"Payout approval restricted to admin", "Refund approval restricted to admin", and "Finance
manager can view edit and refund payments" each re-assert checks byte-for-byte identical to
assertions made earlier in "Admin/Finance manager/Viewer has access…", the same
exact-duplicate pattern `ads`, `groups-resource-attributes`, `calendar`, `file-storage`, and
`kms` (both this batch) already established. Negative-control-verified per this file's
standing method: flipping `link:checkout-page#can_view@user:alice` to `assertFalse` fails `zed
validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own explanation
trace showing the walk `link:checkout-page can_view -> can_edit -> can_delete ->
organization:payments-co admin__perm -> admin -> user:alice`); deleting
`link:checkout-page#can_delete@user:alice` makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 12 relationships loaded via `WriteRelationships`, all 60 distinct check-block assertions
  match exactly, matching the harness's own `60 assertions compared` one for one.
- All **7** `list_objects` and all **3** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `user:alice`'s viewable link/payment/subscription sets are each
  exactly the one object of that type; `user:bob`'s editable-link set is exactly
  `{checkout-page}` and refundable-payment set exactly `{txn-001}`; `user:charlie`'s viewable
  link/payment sets are each exactly the one object of that type;
  `link:checkout-page`'s viewer set is exactly `{alice, bob, charlie}`;
  `payment:txn-001`'s refunder set is exactly `{alice, bob}`; `refund:refund-001`'s approver
  set is exactly `{alice}`. This store's full oracle is **78/78 confirmed** (68 check + 7
  list_objects + 3 list_users, `Checks + ListObjects + ListUsers` per `fga model test`'s own
  count).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all seven types (`user`, `organization`, `link`, `payment`, `payout`, `refund`, `subscription`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.finance_manager`/`.viewer`, `link.organization`/`.creator`, `payment.organization`/`.creator`, `payout.organization`, `refund.organization`, `subscription.organization`/`.creator` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin or finance_manager or viewer` | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias, single-operand (no union) | `organization.admin__perm`, reached independently by `link.can_delete`, `payment.can_delete`, `payout.can_approve`, `refund.can_approve`, `subscription.can_delete` — five independent single-hop arrows into the same alias, none unioned with anything else | "Point arrows at permissions, not relations" — alias branch |
| Bare alias (permission referencing another permission directly) | `payment.can_edit = can_refund`; `subscription.can_edit = can_cancel` | construct table row `define view: viewer` |
| Multi-operand union, including a `__perm`-aliased arrow as one operand | `link.can_edit = (creator + organization->finance_manager__perm + can_delete)`, `.can_view = (organization->viewer__perm + can_edit)`; `payment.can_refund = (organization->finance_manager__perm + can_delete)`, `.can_view = (creator + organization->viewer__perm + can_edit)`; `payout.can_view = (organization->finance_manager__perm + can_approve)`; `refund.can_view = (organization->viewer__perm + organization->finance_manager__perm + can_approve)`; `subscription.can_cancel = (organization->finance_manager__perm + can_delete)`, `.can_view = (creator + organization->viewer__perm + can_edit)` | "Always fully parenthesize" |
| Type-based tenancy, flat single-level fan-out (five sibling resource types, no nesting) | `organization` reached directly by all five resource types, none of which reach each other | "Type-based tenancy" — no new construct |

**Not exercised:** caveats/conditions, wildcards, intersection, userset subjects of any kind
(no `T#rel` anywhere, same as `kms`, this batch), self-type or multi-type tuplesets, recursive
arrows, resource-to-resource nesting of any kind, runtime-defined roles, modular models,
multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` — every
object ID (`payments-co`, `checkout-page`, `txn-001`, `payout-001`, `refund-001`,
`plan-monthly`, `alice`, `bob`, `charlie`, `diana`) is already inside SpiceDB's charset,
hyphens included.

### Findings

None. Every construct — the three-way role split and the five-way flat fan-out, including the
fourteen independent arrows (`grep -o 'organization->' schema.zed | wc -l`) into the same three
organization-level aliases — resolved using
rules already on file, each traced to the specific subsection above, with no ambiguity and no
fix-and-rerun cycle. The schema converted `PARITY OK` on the first attempt, the negative
control behaved as the pack predicts, and the full live-server list_objects/list_users sweep
confirmed 100% of the store's own oracle. This is the batch's flat-fan-out exemplar,
structurally distinct from the other three stores converted this batch (`file-storage` and
`issue-tracking` both nest resources under other resources; `kms` chains three levels deep;
this store nests nothing beyond the tenant root itself).

### What the harness could not see

The harness compared **60 of this store's 78 assertions (76.9%** counting distinct keys after
dedup; **87.2%** by the canonical table's own `Checks / (Checks + ListObjects + ListUsers)`
metric, `68/78` — see "The canonical store table" → **Harness-visible fraction** for which
ordering this file states). The 7 `list_objects` and 3 `list_users` assertions are silently
dropped by the harness per the known gap; all ten were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express on its own (`link:checkout-page`'s viewer set is exactly those three
people, not merely inclusive of them).


---

## `developer-portal`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 3/3 passing,
Checks 10/10 passing, ListObjects 1/1 passing, ListUsers 1/1 passing`. Model carried as an
inline `model:` block in `store.fga.yaml` (the first of the three source-model shapes this
pack's verified facts describe — no separate `.fga` file, unlike every store in batches 5 and
6's other three).

This is a four-type developer-portal model (`organization`, `application`, `component`) built
around one tenant root, distinguished from every other store converted this batch by carrying
**intersection** (`grep -c '&' schema.zed` → 2): `component.reader`/`.writer` each read
`define reader : [application] and application from organization` in the source, fusing a type
list directly with `and` in the same `define` rather than referencing an already-split relation
or permission from elsewhere the way the corpus's four prior intersection stores
(`ip-based-access`, `banking`, `modeling-guide`, `role-assignments`) all do — see "The canonical
store table" → **SpiceDB intersection (`&`)** for the full grep evidence that this is a new
shape and for why it still needed no new rule (the split rule's "any operator" wording already
covers `and`). The four types split into two roles: `application` uses ordinary arrow-derived
`writer`/`reader` (an admin-from-organization arrow, a member-from-organization arrow), while
`component` uses the intersection to require that a subject (here, another `application`
object, not a `user`) be both directly granted `reader`/`writer` *and* a member of the same
organization that owns the component — a same-organization co-membership check expressed as an
intersection between a direct grant and an arrow, rather than as an arrow-only walk.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 9 relationships
loaded, 10 assertions run. Also clean under `--fail-on-warn`. All 10 of the store's own
`Checks 10/10` remain distinct after dedup — no exact-duplicate assertions in this store, unlike
several others in the corpus. Negative-control-verified per this file's standing method:
flipping `application:1#can_edit@user:anne` to `assertFalse` fails `zed validate` itself (exit 1
from `zed`, exit 2 from the harness, with `zed`'s own explanation trace showing the walk
`application:1 can_edit -> writer -> organization:acme admin__perm -> admin -> user:anne`);
deleting `application:1#can_delete@user:anne` makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 9 relationships loaded via `WriteRelationships`, all 10 check-block assertions match
  exactly, matching the harness's own `10 assertions compared` one for one.
- The 1 `list_objects` and 1 `list_users` assertion — silently dropped by the harness — were
  independently run via `LookupResources`/`LookupSubjects` and matched the source oracle
  **exactly**: `user:anne`'s viewable-application set is exactly `{1}`; `application:1`'s
  viewer set is exactly `{anne, marie}`. This store's full oracle is **12/12 confirmed**
  (10 check + 1 list_objects + 1 list_users, `Checks + ListObjects + ListUsers` per `fga model
  test`'s own count).
- The intersection itself was probed directly: `zed permission check component:payment
  can_write application:1` returns `false` (application:1 holds no `writer__direct` grant on
  `component:payment` at all — only `reader__direct`) while `component:payment can_view
  application:1` returns `true` (via the `reader` branch, `--explain` showing both the
  `reader__direct` grant and the `organization->application__perm` arrow evaluated together as
  the intersection requires); `component:payment can_write application:2` returns `true`
  through the same two-operand `&`.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all four types (`user`, `organization`, `application`, `component`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.member`... (see split row below), `application.organization`, `component.organization` | split rule, final bullet |
| Relation/permission split (type list fused with `or`) | `application.writer: [user] or admin from organization` | "The relation/permission split" |
| **Relation/permission split (type list fused with `and`)** | `component.reader: [application] and application from organization`/`.writer: [application] and application from organization` | "The relation/permission split" (general "any operator" wording) — new composition this store forced, see prose above |
| Arrow into a bare relation, needing a `__perm` alias | `organization.member__perm`/`.admin__perm`/`.application__perm`, reached by `application.writer`'s `organization->admin__perm`, `application.reader`'s `organization->member__perm`, `component.reader`/`.writer`'s `organization->application__perm`, `component.can_delete`'s `organization->admin__perm` | "Point arrows at permissions, not relations" — alias branch |
| Bare alias (permission referencing another permission directly) | `organization.can_remove_member = admin`, `.can_invite_member = admin`, `.can_create_application = admin`; `application.can_edit = writer`, `.can_delete = writer`, `.can_create_credentials = writer`, `.can_delete_credentials = writer`, `.can_configure_component = writer`; `component.can_write = writer` | construct table row `define view: viewer` |
| Bare union (relation + arrow-derived permission) | `organization.can_view_member = (admin + member)`; `application.can_view = (reader + writer)`; `component.can_view = (reader + writer)` | construct table row `define view: a or b` |
| SpiceDB intersection (`&`), operand is the split's own `__direct` relation | `component.reader = (reader__direct & organization->application__perm)`, `.writer = (writer__direct & organization->application__perm)` | top construct table, `define view: a and b` row; "The canonical store table" → **SpiceDB intersection (`&`)** |
| Type-based tenancy | `organization` reached directly by `application` and `component` | "Type-based tenancy" — no new construct |

**Not exercised:** caveats/conditions, wildcards, userset subjects of any kind (no `T#rel`
anywhere), self-type or multi-type tuplesets, recursive arrows, runtime-defined roles, modular
models, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` —
every object ID (`acme`, `1`, `2`, `payment`, `purchases`, `anne`, `marie`) is already inside
SpiceDB's charset, numeric-only IDs included.

### Findings

None. Every construct — including the store's distinguishing feature, a type list fused
directly with `and` rather than `or` — resolved using rules already on file (the split rule's
own "any operator" wording, the top construct table's `and` → `&` row, and the ordinary alias
mechanism), with no ambiguity and no fix-and-rerun cycle. The schema converted `PARITY OK` on
the first attempt, both negative controls behaved as the pack predicts, and the full
live-server sweep — including a direct probe of the intersection's own two-operand behavior —
confirmed 100% of the store's own oracle.

### What the harness could not see

The harness compared **10 of this store's 12 assertions (83.3%)** — matching the canonical
table's own `Checks / (Checks + ListObjects + ListUsers)` metric exactly (`10/12`, no dedup
loss in either direction). The 1 `list_objects` and 1 `list_users` assertion are silently
dropped by the harness per the known gap; both were closed by direct live-server verification
(see "Final harness run" above), plus the supplementary intersection probe beyond what any
`check:` block in the source exercises.

---

## `ecommerce`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 23/23 passing,
Checks 83/83 passing, ListObjects 7/7 passing, ListUsers 4/4 passing`. Model carried in a
separate `model.fga` file.

This is a seven-type ecommerce model (`organization`, `store`, `product`, `customer`, `review`,
`order`) built around one tenant root (`organization`) with a three-way role split
(`admin`/`store_manager`/bare `member`) feeding `store`, then branching into **four** resource
types that hang off `store` in different ways: `product` and `customer` reach `store` in one
hop each, `review` reaches `store` only transitively through `product` (`review.product` →
`product.store`), and `order` reaches `store` directly *and* separately carries its own
`customer` relation (`order.customer: [customer]`) that several of its permissions arrow
through instead of (or alongside) the `store` arrow — a genuinely branching resource graph,
not a strict linear chain, unlike `kms`'s single chain or `payment`'s flat single-level
fan-out (both batch 5). No intersection, no recursion, no caveats — pure hierarchical
fan-out with a two-parent leaf (`order`), confirming the batch's hypothesis for this store.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 15 relationships
loaded, 78 assertions run. Also clean under `--fail-on-warn`. The store's own `Checks 83/83`
collapses to **78 distinct** `(subject, permission, resource)` keys once deduplicated —
"Store owner can manage store" and "Store manager can manage products" both re-assert
`store:main-shop#can_create_product@user:bob`, and "Product creator can edit own product" and
"Staff can view and manage inventory but not delete products" both re-assert all four of
`product:sneakers#can_view`/`.can_create_review`/`.can_delete`/`.can_manage_inventory@user:charlie`
byte-for-byte identically — the same exact-duplicate pattern `ads`, `groups-resource-attributes`,
`calendar`, `file-storage`, `kms`, and `payment` already established. Negative-control-verified
per this file's standing method: flipping `store:main-shop#can_view@user:alice` to `assertFalse`
fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own
explanation trace showing the walk `store:main-shop can_view -> can_edit -> can_delete ->
organization_admin -> organization:acme admin__perm -> admin -> user:alice`); deleting
`store:main-shop#can_view@user:alice` makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 15 relationships loaded via `WriteRelationships`, all 83 check-block assertions
  (not just the 78 distinct keys) match exactly against a live check, one for one.
- All **7** `list_objects` and all **4** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `user:charlie`'s staff-store set is exactly `{main-shop}`,
  viewable-product set exactly `{sneakers}`, viewable-customer set exactly `{diana}`;
  `user:bob`'s owned-store set is exactly `{main-shop}` and editable-product set exactly
  `{sneakers}`; `user:diana`'s own-account-owner set is exactly `{diana}` and placed-order set
  exactly `{ord-001}`; `product:sneakers`'s viewer and editor sets are each exactly
  `{alice, bob, charlie}`; `order:ord-001`'s viewer set is exactly `{alice, bob, charlie,
  diana}` and editor set exactly `{alice, bob, charlie}`. This store's full oracle is
  **94/94 confirmed** (83 check + 7 list_objects + 4 list_users, `Checks + ListObjects +
  ListUsers` per `fga model test`'s own count).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all seven types (`user`, `organization`, `store`, `product`, `customer`, `review`, `order`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.store_manager`, `store.organization`/`.owner`, `product.store`/`.creator`/`.viewer`, `customer.store`, `review.product`/`.author`, `order.store`/`.customer`/`.placed_by` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin or store_manager`; `store.manager: [user] or owner or store_manager from organization`/`.staff: [user] or manager` | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin__perm`/`.store_manager__perm`, reached by `store.manager`'s `organization->store_manager__perm` and `store.organization_admin`'s `organization->admin__perm`; `customer.account_owner__perm`, reached by `order.can_cancel`/`.can_view`'s `customer->account_owner__perm` | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias, no other operand | `product.can_delete`'s `store->manager`/`store->organization_admin`; `.can_manage_inventory`'s `store->staff`/`store->organization_admin`; `.can_create_review`'s `store->can_view`; `customer.can_delete`'s `store->organization_admin`; `order.can_delete`'s `store->organization_admin` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare alias (permission referencing another permission or relation directly) | `organization.can_create_store = admin`; `order.can_edit = can_fulfill`; `review.can_edit = author` | construct table row `define view: viewer` |
| Multi-operand union, including arrow-derived operands | `store.can_delete = (owner + organization_admin)`, `.can_create_product = (manager + organization_admin)`, `.can_create_customer = (staff + can_edit)`, `.can_create_order = (staff + can_edit)`, `.can_edit = (manager + can_delete)`, `.can_view = (staff + can_edit)`; `product.can_edit = (creator + can_manage_inventory)`, `.can_view = (viewer + can_edit)`; `customer.can_edit = (account_owner + store->manager + can_delete)`, `.can_view = (store->staff + can_edit)`; `review.can_delete = (author + product->can_delete)`, `.can_view = (product->can_view + can_edit)`; `order.can_refund = (store->manager + can_delete)`, `.can_cancel = (customer->account_owner__perm + placed_by + can_refund)`, `.can_fulfill = (store->staff + can_delete)`, `.can_view = (customer->account_owner__perm + placed_by + can_edit)` | "Always fully parenthesize" |
| Type-based tenancy, branching (not linear) resource graph under one tenant root | `organization` → `store` → {`product`, `customer`, `order`}, `review` reached only transitively through `product`, `order` also carrying its own direct `customer` edge | "Type-based tenancy" — no new construct |

**Not exercised:** caveats/conditions, wildcards, intersection, userset subjects of any kind
(no `T#rel` anywhere), self-type or multi-type tuplesets, recursive arrows, runtime-defined
roles, modular models, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding`
is `none` — every object ID (`acme`, `main-shop`, `sneakers`, `diana`, `rev-001`, `ord-001`,
`alice`, `bob`, `charlie`) is already inside SpiceDB's charset, hyphens included.

### Findings

None. Every construct — the three-way role split, the branching (not linear) resource graph,
and the two independent `__perm` aliases (`store_manager__perm`, `account_owner__perm`),
including `order`'s two-parent reach through both `store` and `customer` — resolved using rules
already on file, each traced to the specific subsection above, with no ambiguity and no
fix-and-rerun cycle. The schema converted `PARITY OK` on the first attempt, the negative
control behaved as the pack predicts, and the full live-server list_objects/list_users sweep
confirmed 100% of the store's own oracle.

### What the harness could not see

The harness compared **78 of this store's 94 assertions (83.0%** counting distinct keys after
dedup; **88.3%** by the canonical table's own `Checks / (Checks + ListObjects + ListUsers)`
metric, `83/94` — see "The canonical store table" → **Harness-visible fraction** for which
ordering this file states). The 7 `list_objects` and 4 `list_users` assertions are silently
dropped by the harness per the known gap; all eleven were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express on its own (`order:ord-001`'s viewer set is exactly those four people, not
merely inclusive of them).

---

## `hospitality`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 14/14 passing,
Checks 109/109 passing, ListObjects 5/5 passing, ListUsers 3/3 passing`. Model carried in a
separate `model.fga` file.

This is a seven-type hotel-management model (`organization`, `hotel`, `room`, `reservation`,
`guest`, `service`) built around one tenant root (`organization`) with a two-way role split
(`admin`/`revenue_manager`) feeding `hotel`, which itself carries a **four-role staff split**
(`general_manager`/`front_desk`/`housekeeping`/`concierge`, the widest flat role fan-out this
batch) and three sibling resource types (`room`, `reservation`, `service`) hanging directly off
it. No intersection, no recursion, no caveats — pure hierarchical fan-out, confirming the
batch's hypothesis for this store; structurally the batch's second such shape after `ecommerce`,
but flatter (`hotel`'s three children never nest further, where `ecommerce`'s `review` nests
under `product`). Three of `hotel`'s four staff relations (`front_desk`, `housekeeping`,
`concierge`) are independently arrowed into from `room`/`reservation`/`service`, each needing
its own `__perm` alias — `general_manager` is the only one of the four never arrowed into from
outside `hotel`, referenced only locally within `hotel`'s own permissions, and correctly needs
no alias.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 19 relationships
loaded, 109 assertions run. Also clean under `--fail-on-warn`. All 109 of the store's own
`Checks 109/109` remain distinct after dedup — no exact-duplicate assertions in this store's
`check:` blocks, despite its size, unlike several other stores in the corpus. Negative-control-
verified per this file's standing method: flipping `hotel:grand-plaza#can_view@user:charlie` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the walk `hotel:grand-plaza can_view -> can_edit ->
general_manager -> user:charlie`); deleting `hotel:grand-plaza#can_view@user:charlie` makes the
harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 19 relationships loaded via `WriteRelationships`, all 109 check-block assertions match
  exactly, matching the harness's own `109 assertions compared` one for one.
- All **5** `list_objects` and all **3** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `user:charlie`'s general-manager-hotel set is exactly
  `{grand-plaza}`; `user:maria`'s front-desk-hotel set is exactly `{grand-plaza}`;
  `user:diana`'s housekeeping-hotel set is exactly `{grand-plaza}`; `guest:guest-doe`'s
  reservation set is exactly `{res-001}`; `organization:grand-hotel-group`'s hotel set is
  exactly `{grand-plaza, seaside-resort}`; `hotel:grand-plaza`'s general-manager set is exactly
  `{charlie}` and front-desk set exactly `{maria}`; `reservation:res-001`'s guest set is exactly
  `{guest-doe}`. This store's full oracle is **117/117 confirmed** (109 check + 5 list_objects
  + 3 list_users, `Checks + ListObjects + ListUsers` per `fga model test`'s own count) — the
  batch's densest, and (per "The canonical store table" → **Harness-visible fraction**) the
  closest any non-check-only store in the complete, now-39-store corpus comes to the seven
  check-only stores' 100% floor — re-confirmed after batch 7: `real-estate`'s 91.8% (iteration
  18) is the closest any later store comes, but still falls short of this store's 93.2%.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all seven types (`user`, `organization`, `hotel`, `room`, `reservation`, `guest`, `service`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.revenue_manager`, `hotel.organization`/`.general_manager`/`.front_desk`/`.housekeeping`/`.concierge`, `room.hotel`, `reservation.hotel`/`.guest`, `guest.organization`, `service.hotel` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin or revenue_manager`; `hotel.staff: [user] or general_manager or front_desk or housekeeping or concierge` | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin__perm`, reached by `hotel.org_admin`'s `organization->admin__perm` and `guest.can_delete`'s `organization->admin__perm`; `organization.revenue_manager__perm`, reached by `hotel.org_revenue_manager`; `hotel.front_desk__perm`/`.housekeeping__perm`/`.concierge__perm`, reached independently by `room.can_edit`, `reservation.can_cancel`, and `service.can_edit` (`general_manager` never arrowed into from outside `hotel`, correctly needing no alias) | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `hotel.can_view`'s `organization->member`; `room.can_delete`'s `hotel->can_delete`, `.can_edit`'s `hotel->can_edit`, `.can_view`'s `hotel->can_view`; `reservation.can_delete`'s `hotel->can_delete`, `.can_cancel`'s `hotel->can_edit`, `.can_view`'s `hotel->can_view`; `guest.can_view`'s `organization->member`; `service.can_delete`'s `hotel->can_delete`, `.can_edit`'s `hotel->can_edit`, `.can_view`'s `hotel->can_view` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare alias (permission referencing another permission directly) | `organization.can_create_hotel = admin`, `.can_create_guest = admin`; `hotel.can_delete = org_admin`, `.can_create_room = can_edit`; `reservation.can_edit = can_cancel`; `guest.can_edit = can_delete` | construct table row `define view: viewer` |
| Multi-operand union, including arrow-derived operands | `hotel.can_edit = (general_manager + can_delete)`, `.can_create_reservation = (front_desk + concierge + can_edit)`, `.can_create_service = (concierge + can_edit)`, `.can_view_revenue = (org_revenue_manager + can_edit)`, `.can_view = (staff + organization->member + can_edit)`; `room.can_edit = (hotel->front_desk__perm + hotel->housekeeping__perm + hotel->can_edit)`, `.can_view = (hotel->can_view + can_edit)`; `reservation.can_cancel = (hotel->front_desk__perm + hotel->can_edit)`, `.can_view = (hotel->can_view + can_edit)`; `guest.can_view = (organization->member + can_edit)`; `service.can_edit = (hotel->concierge__perm + hotel->can_edit)`, `.can_view = (hotel->can_view + can_edit)` | "Always fully parenthesize" |
| Type-based tenancy, flat (single-level) resource fan-out under a secondary role root | `organization` → `hotel` → {`room`, `reservation`, `service`}, `guest` reached directly by `organization` alongside `hotel` rather than nested under it | "Type-based tenancy" — no new construct |

**Not exercised:** caveats/conditions, wildcards, intersection, userset subjects of any kind
(no `T#rel` anywhere), self-type or multi-type tuplesets, recursive arrows, runtime-defined
roles, modular models, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding`
is `none` — every object ID (`grand-hotel-group`, `grand-plaza`, `seaside-resort`, `room-101`,
`res-001`, `res-002`, `guest-doe`, `spa-treatment`, `alice`, `bob`, `charlie`, `diana`, `eve`,
`frank`, `maria`) is already inside SpiceDB's charset, hyphens included.

### Findings

None. Every construct — the four-way staff split (the widest flat role fan-out this batch) and
the three independent `__perm` aliases it forces (`front_desk__perm`, `housekeeping__perm`,
`concierge__perm`, each reached by a different child resource type, alongside the one staff
relation, `general_manager`, that correctly needs none) — resolved using rules already on
file, each traced to the specific subsection above, with no ambiguity and no fix-and-rerun
cycle. The schema converted `PARITY OK` on the first attempt, the negative control behaved as
the pack predicts, and the full live-server list_objects/list_users sweep confirmed 100% of the
store's own oracle.

### What the harness could not see

The harness compared **109 of this store's 117 assertions (93.2%)** — matching the canonical
table's own `Checks / (Checks + ListObjects + ListUsers)` metric exactly (`109/117`, no dedup
loss in either direction) and, per "The canonical store table" → **Harness-visible fraction**,
the closest any non-check-only store in the corpus comes to the seven check-only stores' 100%
floor. The 5 `list_objects` and 3 `list_users` assertions are silently dropped by the harness
per the known gap; all eight were closed by direct live-server verification (see "Final harness
run" above), including the exhaustiveness half a `check:` block cannot express on its own
(`hotel:grand-plaza`'s front-desk set is exactly `{maria}`, not merely inclusive of her).

---

## `human-resources`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 22/22 passing,
Checks 54/54 passing, ListObjects 7/7 passing, ListUsers 3/3 passing`. Model carried in a
separate `model.fga` file. **11 source types / 57 `define`s** (`grep -c '^type ' model.fga` →
11, `grep -c '^\s*define ' model.fga` → 57). This carries over to the converted schema on both
of the canonical table's own metrics: `for f in */schema.zed; do echo "$f $(grep -c
'^definition ' "$f")"; done | sort -k2 -rn` puts `human-resources` first at **11** `definition`s
(ahead of `applicant-tracking-system` and `accounting`, tied at 10; full output confirms no
other store reaches 10), and the same command against `permission ` lines puts it first at
**39** across the full, now-complete 39-store corpus (ahead of `applicant-tracking-system`'s
36, then `manufacturing`'s 35 and `lms`'s 32 — both converted after this store, in batch 7 —
then a three-way tie at 31 between `issue-tracking`, `hospitality`, and this batch's own
`ecommerce`) — the corpus's largest by both the type count and the permission count the
canonical table already tracks.

This store carries the batch's two distinguishing secondary constructs, both confirming the
batch's hypotheses: a **self-referential arrow** on `team` (`team.parent_team: [team]`, a
single-type tupleset pointing back at `team`'s own definition — the "recursion" the batch brief
flagged, verified below to be exactly one hop deep, not unbounded), and **two-tier tenancy**
(`organization` at the top, `company` nested directly under it via `company.organization:
[organization]`, itself acting as a secondary scope for `employee`/`employment`/`benefit`/
`time_off`, each independently re-deriving `organization`'s `can_manage_employees`/
`can_view_sensitive_data` by arrowing through `company` rather than around it). Neither forces
a new rule: the tenancy shape is the same "multi-level resource chain under one tenant root"
pattern `healthcare`, `kms`, `issue-tracking`, and `file-storage` already established (cite
"Type-based tenancy"), and the self-referential arrow is the plain, non-recursive base case
already generalized by `abac-with-rebac` — see the dedicated paragraph distinguishing it from
the corpus's "same-name recursive arrow" lineage below.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 23 relationships
loaded, 54 assertions run. First pass under `--fail-on-warn` surfaced the already-documented
`relation-name-references-parent` lint (`schema-mapping.md`'s Codegen rules addendum,
corpus-confirmed on `advanced-entitlements`'s `feature.has_feature` and `iot`'s
`can_rename_device`) on `team.parent_team` (`"parent_team" references parent type "team" in its
name`); fixed the same way as those two precedents, with a `// spicedb-ignore-warning:
relation-name-references-parent` comment placed directly above the flagged relation rather than
a rename, since `parent_team` is the source's own verbatim relation name. Clean under
`--fail-on-warn` after the suppression comment, no schema-shape change. The store's own
`Checks 54/54` remain **54 distinct** after dedup — no exact-duplicate assertions in this
store's `check:` blocks. Negative-control-verified per this file's standing method: flipping
`employee:diana-record#can_view@user:diana` to `assertFalse` fails `zed validate` itself (exit 1
from `zed`, exit 2 from the harness, with `zed`'s own explanation trace showing the walk
`employee:diana-record can_view -> can_view_sensitive -> assignee -> user:diana`, the `manager`
and `team_manager` branches of the same union both evaluating `false` first); deleting
`employee:diana-record#can_view@user:diana` makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` (with the suppression comment) unedited, no compile step.
- All 23 relationships loaded via `WriteRelationships`, all 54 check-block assertions match
  exactly, matching the harness's own `54 assertions compared` one for one.
- All **7** `list_objects` and all **3** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `user:charlie`'s team-manager set is exactly `{engineering}`;
  `user:diana`'s team-member set is exactly `{engineering}`, employee-assignee set exactly
  `{diana-record}`, group-member set exactly `{developers}`, and time-off-requester set exactly
  `{diana-vacation}`; `user:eve`'s team-member set is exactly `{backend}`;
  `employee:diana-record`'s employment set is exactly `{diana-swe}`;
  `employee:diana-record`'s manager set is exactly `{charlie}`; `time_off:diana-vacation`'s
  approver set is exactly `{charlie}`; `team:engineering`'s manager set is exactly `{charlie}`.
  This store's full oracle is **64/64 confirmed** (54 check + 7 list_objects + 3 list_users,
  `Checks + ListObjects + ListUsers` per `fga model test`'s own count).
- **Supplementary probe beyond the source's own oracle** (this file's standing method), testing
  the depth bound of the self-referential `parent_team` arrow: the source fixture nests `team`
  two levels deep (`team:backend#parent_team@team:engineering`) and its own "Team parent-child
  inheritance" test confirms exactly one level of propagation (`user:diana`, a direct member of
  `engineering`, correctly reaches `can_view` on `backend`). Writing a **third** level
  (`team:sub-backend#parent_team@team:backend`, absent from the fixture) and checking
  `user:diana` — a member of `engineering`, two hops from `sub-backend` — against
  `team:sub-backend#can_view` returns **`false`**, `--explain` showing every branch of the
  union evaluate false (`sub-backend`'s own `member`, `backend`'s `member` reached through
  `parent_team`, and `can_manage`); `user:eve` — a direct member of `backend`, one hop from
  `sub-backend` — correctly returns `true` through the `parent_team->member__perm` arrow. This
  confirms the construct is a genuine **one-hop-only** parent lookup, not transitive
  multi-level recursion: unlike the corpus's "same-name recursive arrow" stores (`gdrive`,
  `expenses`, `modeling-guide`, `file-storage`, `issue-tracking`), where a permission arrows
  into *itself* on the parent and so re-walks at every level, `team.can_view` arrows into
  `member__perm` — a different, non-recursive name — so the walk terminates after exactly one
  crossing regardless of how many `team` levels exist above it. The converted schema
  reproduces this bound exactly, with no depth limit encoded anywhere and none needed.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all eleven types (`user`, `organization`, `company`, `group`, `team`, `location`, `employee`, `employment`, `benefit`, `payroll_run`, `time_off`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.hr_manager`, `company.organization`, `group.organization`/`.manager`/`.member`, `team.organization`/`.manager`/`.member`/`.parent_team`, `location.organization`, `employee.company`/`.assignee`/`.manager`/`.team`, `employment.employee`/`.viewer`, `benefit.employee`/`.viewer`, `payroll_run.organization`, `time_off.employee`/`.requester`/`.approver` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin or hr_manager`; `company.admin: [user] or admin from organization`/`.member: [user] or member from organization`; `employee.hr_admin: [user] or company_can_manage_employees` | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias | `organization.admin__perm`, reached by `company.admin`, `group.can_manage`, `team.can_manage`, `location.can_edit`, and `payroll_run.can_approve`'s independent `organization->admin__perm` arrows (five separate arrows into the same alias); `organization.hr_manager__perm`, reached by `payroll_run.can_view`; `team.manager__perm`, reached by `employee.team_manager`; `employee.manager__perm`, reached by `employment.can_view` and `time_off.can_view` | "Point arrows at permissions, not relations" — alias branch |
| Arrow into an already-permission target, no alias | `company.admin`'s `organization->admin__perm` is the only bare-relation case above; `company.member`'s `organization->member`; `.can_manage_employees`'s `organization->can_manage_employees`; `.can_view_sensitive_data`'s `organization->can_view_sensitive_data`; `employee.company_can_manage_employees`'s `company->can_manage_employees`; `.company_can_view_sensitive_data`'s `company->can_view_sensitive_data`; `employment.can_edit`'s `employee->can_edit`; `.can_view_sensitive`'s `employee->can_view_sensitive`; `benefit.can_edit`/`.can_view_sensitive`, same shape; `time_off.can_cancel`/`.can_approve`'s `employee->company_can_manage_employees` | "Point arrows at permissions, not relations" — no-alias branch |
| **Self-referential arrow (single-type tupleset, tupleset's allowed type equals the arrow's own definition), non-recursive base case** | `team.can_view`'s `parent_team->member__perm`, one hop only — see the dedicated distinction from the "same-name recursive arrow" lineage in the prose above and the supplementary probe confirming the one-hop bound live | "A self-referential arrow… needs no special rule" (`abac-with-rebac`) |
| `relation-name-references-parent` lint + `spicedb-ignore-warning` suppression | `team.parent_team` (references its own parent type `team`) | already on file — `schema-mapping.md`'s Codegen rules addendum, corpus-confirmed on `advanced-entitlements`'s `feature.has_feature` and `iot`'s `can_rename_device`; applied here verbatim, no new rule needed |
| Bare alias (permission referencing another permission directly) | `employee.can_terminate = company_can_manage_employees` | construct table row `define view: viewer` |
| Multi-operand union, including arrow-derived operands | `organization.can_manage_employees = (admin + hr_manager)`, `.can_view_sensitive_data = (admin + hr_manager)`; `company.can_view_sensitive_data = (organization->can_view_sensitive_data + can_manage_employees)`, `.can_edit = (admin + can_manage_employees)`, `.can_view = (member + can_edit)`; `group.can_manage = (manager + organization->admin__perm)`, `.can_view = (member + can_manage)`; `team.can_manage = (manager + organization->admin__perm)`, `.can_view = (member + parent_team->member__perm + can_manage)`; `location.can_view = (organization->member + can_edit)`; `employee.can_edit = (hr_admin + can_terminate)`, `.can_view_sensitive = (assignee + company_can_view_sensitive_data + can_edit)`, `.can_view = (manager + team_manager + can_view_sensitive)`; `employment.can_view = (viewer + employee->manager__perm + can_view_sensitive + can_edit)`; `benefit.can_view = (viewer + can_view_sensitive + can_edit)`; `payroll_run.can_view = (organization->hr_manager__perm + can_approve)`; `time_off.can_cancel = (requester + employee->company_can_manage_employees)`, `.can_approve = (approver + employee->company_can_manage_employees)`, `.can_view = (employee->manager__perm + can_approve + can_cancel)` | "Always fully parenthesize" |
| Type-based tenancy, multi-level resource chain, secondary tenant root nested under the primary | `organization` → `company` → `employee` → {`employment`, `benefit`, `time_off`}; `organization` also reached directly by `group`, `team`, `location`, `payroll_run` alongside `company` | "Type-based tenancy" — no new construct |

**Not exercised:** caveats/conditions, wildcards, intersection, userset subjects of any kind (no
`T#rel` anywhere), multi-type tuplesets, same-name recursive arrows (see the dedicated
distinction above — `team.parent_team` is the simpler base case, not this lineage),
runtime-defined roles, modular models, multi-store tenancy, model-ID pinning, contextual tuples.
`id_encoding` is `none` — every object ID (`acme`, `acme-corp`, `engineering`, `backend`,
`developers`, `hq-office`, `diana-record`, `diana-swe`, `diana-health`, `march-2026`,
`diana-vacation`, `alice`, `bob`, `charlie`, `diana`, `eve`) is already inside SpiceDB's
charset, hyphens included.

### Findings

None. Every construct — the corpus's largest type/`define` count, the two-tier tenancy chain,
the single-hop self-referential arrow on `team` (mechanically distinguished from the corpus's
same-name recursive-arrow lineage and live-probed past the fixture's own two-level nesting to
confirm the one-hop bound), and the `relation-name-references-parent` suppression — resolved
using rules already on file, each traced to the specific subsection above, with no ambiguity
and no fix-and-rerun cycle in the schema-shape sense (the lint fix was applied before the first
harness invocation, the same way `ads`'s rename was in batch 1 — not a fix-and-rerun cycle).
The schema converted `PARITY OK` on the first attempt, the negative control behaved as the pack
predicts, and the full live-server list_objects/list_users sweep confirmed 100% of the store's
own oracle.

**Iteration 17 ledger note — a pack-rule addition not attributable to any single store in this
batch.** During iteration 17 (this batch: `developer-portal`, `ecommerce`, `hospitality`, and
this store), review of the alias mechanism `file-storage` (iteration 16) forced surfaced a
further gap in that same construct: applying the `__perm` alias to only *some* of a
multi-type tupleset's allowed types compiles clean under `zed validate --fail-on-warn` (zero
warnings, not even the one an outright bare-relation miss produces) and is wrong on live data
for the branch missing the alias. This was prompted by review, not by converting any of this
iteration's four stores — none of them contains a multi-type tupleset, so none could have
forced it, and this iteration's own harness/live-server results above are unaffected.
`schema-mapping.md` gained the rule, its worked-example reproduction, and a ~155-line
detection script ("Partial alias application on a multi-type tupleset is silent" subsection).
Recorded here, at iteration 17, so it is not invisible to anyone deriving corpus convergence
by reading every store's `### Findings` section — the construct it concerns is cited from
`file-storage`'s own section (iteration 16), the construct's sole bearer.

### What the harness could not see

The harness compared **54 of this store's 64 assertions (84.4%)** — matching the canonical
table's own `Checks / (Checks + ListObjects + ListUsers)` metric exactly (`54/64`, no dedup
loss in either direction). The 7 `list_objects` and 3 `list_users` assertions are silently
dropped by the harness per the known gap; all ten were closed by direct live-server verification
(see "Final harness run" above), including the exhaustiveness half a `check:` block cannot
express on its own (`employee:diana-record`'s manager set is exactly `{charlie}`, not merely
inclusive of him), plus the supplementary third-level `team` probe the source's own oracle never
reaches at all.

---

## `knowledge-base`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 15/15 passing,
Checks 33/33 passing, ListObjects 12/12 passing, ListUsers 4/4 passing`. Model carried in a
separate `model.fga` file.

This is a six-type document/wiki model built around one tenant root (`organization`) with a
three-way role split (`admin`/`editor`/plain `member`), a `group` type whose `member` relation
is referenced as a nested userset, and a `container` type that is **self-referential**
(`parent_container: [container]`, an arbitrary-depth folder tree) with two of its permissions
(`editor`, `viewer`) fusing a wildcard-bearing type list with a same-name recursive arrow —
`viewer: [user, group#member, user:*] or editor or viewer from parent_container or member from
organization`. `article` repeats the same `editor`/`viewer` shape one level down, arrowing into
`container`'s already-resolved permissions rather than recursing itself; `attachment` arrows
into `article`. This is the corpus's **fourth wildcard-bearing store** (`grep -l ':\*'
*/schema.zed` across all 39 committed stores: `gdrive`, `knowledge-base`, `modeling-guide`,
`role-assignments` — sorted by iteration, `gdrive` (14, rank 1), `modeling-guide` and
`role-assignments` (both 15, rank 2, tied), `knowledge-base` (18, rank 3) — and the store this
batch's brief flagged for the most scrutiny, since a wildcard-fused-with-a-same-name-recursive-
arrow shape already exists on file (`gdrive`'s `folder.viewer`, see `schema-mapping.md`'s "A
same-name recursive arrow" subsection) but had not previously been combined with a real,
populated multi-level hierarchy **and** a nested userset in the same type list at once.

**Final harness run:** `PARITY OK`, exit **0**. `zed validate`: 17 relationships loaded, 33
assertions run. Also clean under `--fail-on-warn` once the `relation-name-references-parent`
lint on `container.parent_container` and `article.parent_container` was suppressed in place
(`// spicedb-ignore-warning: relation-name-references-parent`, the same purpose-built escape
hatch `advanced-entitlements` established, reused since by `iot`, `issue-tracking`, and
`human-resources` — `grep -rl 'spicedb-ignore-warning: relation-name-references-parent'
*/schema.zed` across all 39 committed stores returns exactly these five files — applied here
verbatim, no new rule). One authoring slip surfaced before the first real harness invocation,
not a construct gap: the first draft of `validation.yaml` wrote the `attachment:diagram-png
#article@article:getting-started` relationship with resource and subject swapped
(`article:getting-started#article@attachment:diagram-png`), caught immediately by `zed
validate`'s own parse error (`relation/permission "article" not found under definition
"article"`, harness exit 2) before any assertion was compared — a transcription error in the
tuple-to-relationship direction, not a schema-shape or pack-rule question, corrected before the
first assertion-comparing run. Negative-control-verified per this file's standing method after
that correction: flipping `container:engineering-space#can_view@user:alice` to `assertFalse`
fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own
explanation trace showing the walk `can_view -> viewer -> organization:acme member ->
organization:acme admin -> user:alice`); deleting the same assertion makes the harness report
`MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited (including both ignore-warning comments), no
  compile step, no `use` flag.
- All 17 relationships loaded via `WriteRelationships`, all 33 check-block assertions match
  exactly.
- All **12** `list_objects` and all **4** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `alice`/`bob`/`dave`'s viewable and editable `container` sets are
  each exactly `{engineering-space, api-docs}`; `bob`'s viewable/editable/publishable `article`
  set is exactly `{getting-started, public-faq}`, `charlie`'s viewable set the same pair;
  `bob`'s viewable/deletable `attachment` set is exactly `{diagram-png}`;
  `container:engineering-space`'s viewer set is exactly `{alice, bob, charlie, dave}` and
  editor set exactly `{alice, bob, dave}`; `article:getting-started`'s publisher set is exactly
  `{alice, bob}`; `attachment:diagram-png`'s viewer set is exactly `{alice, bob, charlie,
  dave}`. This store's full oracle is **49/49 confirmed** (33 check + 12 list_objects + 4
  list_users, `Checks + ListObjects + ListUsers` per `fga model test`'s own count).
- **The fixture itself already exercises the real (non-wildcard) recursive arm live, not only
  synthetically**: `bob`'s `can_edit: true` on `container:api-docs` resolves through
  `container:api-docs`'s `parent_container->editor` arm alone (`api-docs` carries no direct
  `owner`/`editor__direct`/`organization_admin` grant for `bob` at all — its editor permission
  is entirely inherited from `container:engineering-space`, where `bob` is the direct `owner`),
  confirmed via `--explain` showing exactly that chain and no other.
- **Supplementary probe beyond the source's own oracle, matching `gdrive`'s own methodology**:
  the fixture's only wildcard tuple (`article:public-faq#viewer__direct@user:*`) is checked
  *directly*, never through recursion or an arrow — the store's own oracle never actually
  exercises a wildcard reached transitively through the container tree, the same gap `gdrive`'s
  own fixture left open for its single-hop arrow case. Probed directly: with
  `container:engineering-space#viewer__direct@user:*` added live and a subject entirely outside
  the fixture (`user:zzz-not-in-fixture`), `article:getting-started#can_view` resolves `true`
  through the full `attachment`-independent chain `article -> parent_container (api-docs) ->
  parent_container (engineering-space) -> viewer__direct`. Re-verified against the live
  `--explain` trace (corrected: an earlier draft of this bullet miscounted the trace): this is
  **two arrow crossings, not three**, and the cross-type arrow comes *first*, not last —
  `article.viewer`'s own `parent_container->viewer` (cross-type, `article` → `container`)
  lands on `container:api-docs`, then `container.viewer`'s own `parent_container->viewer`
  (same-name recursive, `container` → `container`, the chain's only recursion) lands on
  `container:engineering-space`, where the wildcard is read directly off that object's own
  `viewer__direct` — a same-object relation read, not a further arrow crossing, so it does not
  count as a third hop. That is one more crossing than `gdrive`'s own single-hop confirmation
  reached (2 vs. 1), and the first live confirmation in the corpus that a wildcard survives a
  **same-name recursive arrow hop composed with a preceding cross-type arrow** — not "two
  consecutive same-name recursive arrow hops" as an earlier draft claimed. `gdrive`'s own
  fixture never populates a `folder#parent@folder` tuple and `modeling-guide`'s reuse of the
  same recursive shape is dormant for the identical reason (see both stores' own sections), so
  a *second* consecutive recursion has no corpus precedent to compare against either way. This
  is structurally the deepest single-recursion chain this fixture can produce:
  `knowledge-base/validation.yaml` contains exactly one `container#parent_container@container`
  tuple (`container:api-docs#parent_container@container:engineering-space`), so a chain with
  two consecutive recursions does not exist here, and none was added to manufacture one — the
  committed fixture is closed. Routing the same probe through `attachment:diagram-png` instead
  of directly through `article` reaches three crossings (`attachment->article`,
  `article->container`, `container->container`) but still carries exactly **one** recursion,
  confirmed live the same way. After deleting the wildcard tuple, both checks revert to
  `false`. See "Final harness run" above for the full `--explain` trace and the negative
  control.

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all six types (`user`, `organization`, `group`, `container`, `article`, `attachment`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.editor`, `group.organization`/`.member`, `container.organization`/`.parent_container`/`.owner`, `article.parent_container`/`.author`, `attachment.article`/`.uploader` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin or editor`, `container.editor`/`.viewer`, `article.editor`/`.viewer` | "The relation/permission split" |
| **Wildcard fused with an operator (split), same type list also carrying a nested userset** | `container.viewer__direct: user \| group#member \| user:*`, `article.viewer__direct: user \| group#member \| user:*` | schema-mapping construct table row `define viewer: [user:*]`; "Wildcards" note — `clean`; composes with the split rule exactly as `gdrive`'s `folder.viewer__direct` does |
| Arrow into a bare relation, needing a `__perm` alias | `container.organization_admin`'s `organization->admin__perm` (the store's only alias) | "Point arrows at permissions, not relations" — alias branch |
| **Same-name recursive arrow, one carrying a wildcard-bearing type list** | `container.editor`'s and `container.viewer`'s own `parent_container->editor`/`parent_container->viewer` | "A same-name recursive arrow…" subsection — the identical composition `gdrive`'s `folder.viewer` already established (`schema-mapping.md` names it explicitly: "`gdrive`'s own `folder.viewer`… is byte-for-byte the identical pattern"), here additionally live-walked one level deep by the fixture itself, its only recursive edge (see "Final harness run") and, via the supplementary probe, carrying the wildcard through that same recursive hop, composed with a preceding cross-type arrow |
| Cross-type arrow into an already-permission target, no alias | `article.organization_admin`'s `parent_container->organization_admin`; `article.editor`'s/`.viewer`'s `parent_container->editor`/`.viewer`; `container.viewer`'s `organization->member`; `attachment.can_view`'s `article->can_view`; `attachment.can_delete`'s `article->organization_admin` | "Point arrows at permissions, not relations" — no-alias branch |
| Nested userset in a nested type list (not wildcard-bearing) | `group#member` inside `container.editor__direct`/`.viewer__direct` and `article.editor__direct`/`.viewer__direct`; `group.member` itself carries no wildcard, so the transitive-wildcard blocker's actual rejected shape is not constructed here (identical to `gdrive`'s own "not exercised" note) | construct table row `define viewer: [user, group#member]` — `clean` |
| `relation-name-references-parent` lint + `spicedb-ignore-warning` suppression | `container.parent_container`, `article.parent_container` (both reference their own parent type `container`) | already on file — `schema-mapping.md`'s Codegen rules addendum, corpus-confirmed on `advanced-entitlements`'s `feature.has_feature`, reused since by `iot`, `issue-tracking`, and `human-resources`; applied here identically, no new rule |
| Bare permission-to-permission alias | `article.can_archive = can_delete`, `article.can_publish = can_delete` | construct table row `define view: viewer` |
| Multi-operand union | `container.can_delete`/`.can_edit`/`.can_create_article`/`.can_view`, `article.can_delete`/`.can_edit`/`.can_view` | "Always fully parenthesize" |

**Not exercised:** the transitive-wildcard blocker's actual rejected shape (no userset
type-list entry `[T#rel]` anywhere points at a wildcard-bearing bare relation — this store's
only userset type-list reference, `group#member`, points at `group.member`, which carries no
wildcard, the identical "not exercised" shape `gdrive` recorded), caveats/conditions,
intersection, multi-tenancy beyond a single organization (the fixture never populates a second
tenant, so no cross-tenant isolation probe applies), multi-type tuplesets (`parent_container`
and `article` are each single-type), modular models, runtime-defined roles, multi-store
tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` — every object ID
(`acme`, `docs-team`, `engineering-space`, `api-docs`, `getting-started`, `public-faq`,
`diagram-png`, `alice`, `bob`, `charlie`, `dave`) is already inside SpiceDB's charset, hyphens
included.

### Findings

None. Every construct — the wildcard-fused split, the same-name recursive arrow (including its
composition with the wildcard, already on file from `gdrive`), the nested userset with no
wildcard on its own target, and the reused lint suppression — resolved using rules already on
file, each traced to the specific subsection above, with no ambiguity and no fix-and-rerun
cycle in the schema-shape sense (the one authoring slip was a relationship-direction
transcription error caught before the first assertion-comparing harness run, not a construct
gap; the lint suppression was applied before the first `zed validate` invocation, the same way
`human-resources`'s was). The schema converted `PARITY OK`, the negative control behaved as the
pack predicts, and the full live-server list_objects/list_users sweep confirmed 100% of the
store's own oracle. This settles the question the batch brief posed: a wildcard-bearing store
**can** convert with zero findings — the prior two wildcard-bearing stores to produce findings
(`gdrive`, `role-assignments`) both filed *worked-example* findings that corpus-confirmed a
mechanism already believed to work but not yet exercised by real data; here that mechanism (and
its composition with a same-name recursive arrow) was already fully corpus-confirmed by
`gdrive` before this store existed, so nothing remained to newly confirm.

### What the harness could not see

The harness compared **33 of this store's 49 assertions (67.3%)** — matching the canonical
table's own `Checks / (Checks + ListObjects + ListUsers)` metric exactly (`33/49`, no dedup
loss in either direction). The 12 `list_objects` and 4 `list_users` assertions are silently
dropped by the harness per the known gap; all sixteen were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express on its own (`container:engineering-space`'s viewer set is exactly those
four people, not merely inclusive of them), plus the supplementary two-level recursive wildcard
probe the source's own oracle never reaches at all.

---

## `lms`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 14/14 passing,
Checks 84/84 passing, ListObjects 30/30 passing, ListUsers 12/12 passing`. Model carried in a
separate `model.fga` file.

This is a seven-type learning-management-system model built around one tenant root
(`organization`) with a three-way role split (`admin`/`instructor`/`student`), fanning out into
a two-level resource hierarchy: `course` sits directly under `organization` and is itself the
parent of both `class` and `content`; `class` is in turn the parent of `activity`; `collection`
hangs directly off `organization`, a sibling of `course` rather than nested under it. Every
non-tenant type reaches its role checks by arrowing up the chain rather than by any relation of
its own carrying a foreign userset — no wildcards, no caveats, no intersection, and (unlike
`knowledge-base`, this batch) no self-referential type anywhere, matching the batch brief's
framing of this store as hierarchical fan-out with no secondary construct. The store's five
declared `__perm` aliases each sit on a different source relation
(`organization.admin__perm`/`.instructor__perm`/`.student__perm`, `course.enrolled_instructor
__perm`, `class.instructor__perm`), and three of the five are independently reached by **two**
unrelated resource types apiece (`organization.admin__perm` by both `course.organization_admin`
and `collection.can_delete`; `.instructor__perm` by `course.organization_instructor` and
`collection.can_edit`; `.student__perm` by `course.organization_student` and
`collection.can_view`) — the same "one alias, several independent single-hop arrows" shape
`payment` (batch 5) established, here on a smaller scale (two reuses instead of `payment`'s up
to five).

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 18 relationships
loaded, 76 assertions run. Also clean under `--fail-on-warn`. The store's own `Checks 84/84`
collapses to **76 distinct** `(subject, permission, resource)` keys once deduplicated — three of
its nine `tests:` blocks ("Course publishing requires owner or admin", "Only instructors and
admins can grade activities", "Only assigned student can submit activity") re-assert checks
byte-for-byte identical to assertions already made in the four role-specific blocks earlier in
the file, except for one genuinely new triple (`activity:homework-1#can_submit@user:alice`,
`false`) — the same exact-duplicate `tests:`-block pattern `ads`, `groups-resource-attributes`,
`calendar`, `file-storage`, `kms`, and `payment` already established. Negative-control-verified
per this file's standing method: flipping `course:intro-cs#can_view@user:alice` to
`assertFalse` fails `zed validate` itself (exit 1 from `zed`, exit 2 from the harness, with
`zed`'s own explanation trace showing the walk `can_view -> can_edit -> can_publish ->
can_delete -> organization_admin -> organization:university admin__perm -> admin ->
user:alice`); deleting the same assertion makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 18 relationships loaded via `WriteRelationships`, all 76 distinct check-block assertions
  match exactly, matching the harness's own `76 assertions compared` one for one.
- All **30** `list_objects` and all **12** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `bob`'s editable/viewable `course`/`class`/`content`/`collection`/
  `activity` sets are each exactly the one object of that type, and his gradeable `activity` set
  the same; `charlie`'s viewable sets across all five types are each exactly the one object,
  and his submittable `activity` set the same; `alice`'s viewable/editable/deletable sets across
  `course`/`class`/`collection`/`activity` are each exactly the one object; `course:intro-cs`'s
  viewer set is exactly `{alice, bob, charlie}`, editor set exactly `{alice, bob}`; the same
  viewer/editor pattern holds for `activity:homework-1`, `class:section-a`, and
  `collection:cs-fundamentals`; `activity:homework-1`'s grader set is exactly `{alice, bob}` and
  submitter set exactly `{charlie}`. This store's full oracle is **126/126 confirmed** (84
  check + 30 list_objects + 12 list_users, `Checks + ListObjects + ListUsers` per `fga model
  test`'s own count).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all seven types (`user`, `organization`, `course`, `class`, `content`, `collection`, `activity`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.instructor`/`.student`, `course.organization`/`.owner`/`.enrolled_instructor`/`.enrolled_student`, `class.course`/`.instructor`/`.student`, `content.course`/`.author`, `collection.organization`/`.owner`, `activity.class`/`.creator`/`.assignee` | split rule, final bullet |
| Relation/permission split | `organization.member: [user] or admin or instructor or student` (the store's only split) | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias, each reused by two independent resource types | `organization.admin__perm`/`.instructor__perm`/`.student__perm`, each reached by both a `course.organization_*` permission and the matching `collection.can_delete`/`.can_edit`/`.can_view` term; `course.enrolled_instructor__perm`, reached by `class.can_edit`; `class.instructor__perm`, reached by `activity.can_grade` | "Point arrows at permissions, not relations" — alias branch |
| Cross-type arrow into an already-permission target, no alias | `class.organization_admin`'s/`.organization_instructor`'s `course->organization_admin`/`.organization_instructor`; `class.can_view`'s `course->can_view`; `content.can_delete`'s `course->organization_admin`; `content.can_edit`'s `course->organization_instructor`; `content.can_view`'s `course->organization_student`; `activity.can_delete`'s `class->organization_admin`; `activity.can_grade`'s `class->organization_instructor` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare permission-to-permission alias | `course.can_delete = organization_admin`, `class.can_delete = organization_admin`, `activity.can_edit = can_grade`, `activity.can_submit = assignee` | construct table row `define view: viewer` |
| Multi-operand union | `organization.member`, `course.can_publish`/`.can_edit`/`.can_view`, `class.can_enroll`/`.can_edit`/`.can_view`, `content.can_delete`/`.can_edit`/`.can_view`, `collection.can_delete`(single)/`.can_edit`/`.can_view`, `activity.can_grade`/`.can_view` | "Always fully parenthesize" |
| Type-based tenancy, two-level fan-out (one sibling type off the tenant root, two more nested under a mid-level type) | `organization` reached directly by `course` and `collection`; `class`/`content` reached only through `course`; `activity` reached only through `class` | "Type-based tenancy" — no new construct |

**Not exercised:** wildcards, caveats/conditions, intersection, userset subjects of any kind (no
`T#rel` anywhere — every relation's type list is a bare `[user]` or a bare object-type
reference), self-type or multi-type tuplesets, recursive arrows, runtime-defined roles, modular
models, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding` is `none` —
every object ID (`university`, `intro-cs`, `section-a`, `lecture-slides`, `cs-fundamentals`,
`homework-1`, `alice`, `bob`, `charlie`, `diana`) is already inside SpiceDB's charset.

### Findings

None. Every construct — the three-way role split, the two-level fan-out, and the five
independently-reused `__perm` aliases — resolved using rules already on file, each traced to
the specific subsection above, with no ambiguity and no fix-and-rerun cycle. The schema
converted `PARITY OK` on the first attempt, the negative control behaved as the pack predicts,
and the full live-server list_objects/list_users sweep confirmed 100% of the store's own oracle.

### What the harness could not see

The harness compared **76 of this store's 126 assertions (60.3%** counting distinct keys after
dedup; **66.7%** by the canonical table's own `Checks / (Checks + ListObjects + ListUsers)`
metric, `84/126` — an exact fraction tie with `iot` and `file-storage`, all three reducing to
2/3; see "The canonical store table" → **Harness-visible fraction** for which ordering this
file states). The 30 `list_objects` and 12 `list_users` assertions are silently dropped by the
harness per the known gap; all forty-two were closed by direct live-server verification (see
"Final harness run" above), including the exhaustiveness half a `check:` block cannot express
on its own (`course:intro-cs`'s viewer set is exactly those three people, not merely inclusive
of them).

---

## `manufacturing`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 15/15 passing,
Checks 136/136 passing, ListObjects 40/40 passing, ListUsers 15/15 passing`. Model carried in
a separate `model.fga` file.

This is an eight-type manufacturing-shop-floor model built around one tenant root
(`organization`) with a **six-way flat role union** — `admin`, `plant_manager`, `engineer`,
`quality_inspector`, `operator`, `procurement` — fused with `[user]` into `organization.member`.
Read off `grep -n 'permission member = (' */schema.zed` across all 39 committed stores and
counting `+`-separated operands (full output above, in each store's own section as it converted):
sorted descending by term count, `manufacturing` (iteration 18, 7 terms: `member__direct` plus
six roles) ranks first, ahead of `healthcare` (iteration 15, 5 terms) and this batch's own
`real-estate` (iteration 18, 5 terms), which tie for second — **the corpus's widest `member`
split union**, confirming the batch brief's "unusually wide" framing mechanically rather than by
inspection alone. Five of the six roles are independently arrow-targeted from other types and so
need a `__perm` alias (`admin__perm`, `plant_manager__perm`, `engineer__perm`,
`quality_inspector__perm`, `procurement__perm`); `operator` is never reached by an arrow from
outside `organization` (`machine.assigned_operator` is a separate, unrelated relation on a
different type) and so needs none — the same "not every role in a wide union gets aliased"
pattern this file has not previously had a store wide enough to demonstrate this clearly. The
resource side fans out three levels deep in one branch (`organization -> production_line ->
machine`, and separately `-> work_order`) and one level in three others (`quality_report`,
`part`, `supplier` each hang directly off `organization`) — no wildcards, no caveats, no
intersection, no self-referential type anywhere. At 35 `permission` lines this is the corpus's
third-largest store by that column, after `human-resources` (39) and `applicant-tracking-system`
(36).

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 19 relationships
loaded, 136 assertions run — the store's full `Checks 136/136` with **zero** deduplication loss,
unlike `lms` (this same batch) and several prior stores; every one of its nine `tests:` blocks
asserts a distinct `(subject, permission, resource)` triple. Also clean under `--fail-on-warn`.
Negative-control-verified per this file's standing method: flipping
`production_line:line-alpha#can_delete@user:alice` to `assertFalse` fails `zed validate` itself
(exit 1 from `zed`, exit 2 from the harness, with `zed`'s own explanation trace showing the walk
`can_delete -> org_admin -> organization:acme-manufacturing admin__perm -> admin ->
user:alice`); deleting the same assertion makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 19 relationships loaded via `WriteRelationships`, all 136 check-block assertions match
  exactly, matching the harness's own `136 assertions compared` one for one.
- All **40** `list_objects` and all **15** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `bob`'s `production_line` grants match all four asserted
  permissions exactly; `eve`'s viewable `machine` set is exactly `{cnc-001, press-002}` and
  editable set exactly `{cnc-001}`; `diana`'s `quality_report` grants match all three asserted
  permissions; `alice`'s `organization` membership and all three creation permissions resolve
  exactly as asserted, matching `frank`'s and `diana`'s narrower subsets; `alice`'s admin sweep
  across `production_line`/`machine`/`work_order`/`quality_report` matches every asserted
  permission exactly, including `machine`'s two-object set for both `can_delete` and `can_edit`;
  `charlie`'s `machine` grants match `alice`'s identical two-object set;
  `production_line:line-alpha`'s five permission-holder sets, `machine:cnc-001`'s three,
  `work_order:wo-001`'s three, and `organization:acme-manufacturing`'s four (`member` plus the
  three creation permissions) all match the source exactly, including the full seven-member
  `member` set. This store's full oracle is **191/191 confirmed** (136 check + 40 list_objects +
  15 list_users, `Checks + ListObjects + ListUsers` per `fga model test`'s own count).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all eight types (`user`, `organization`, `production_line`, `machine`, `work_order`, `quality_report`, `part`, `supplier`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.plant_manager`/`.engineer`/`.quality_inspector`/`.operator`/`.procurement`, `production_line.organization`/`.supervisor`, `machine.production_line`/`.assigned_operator`, `work_order.production_line`/`.creator`/`.assignee`, `quality_report.organization`/`.inspector`, `part.organization`, `supplier.organization` | split rule, final bullet |
| Relation/permission split, **corpus's widest `member` union (six roles)** | `organization.member: [user] or admin or plant_manager or engineer or quality_inspector or operator or procurement` | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias — **five of the union's six roles, one (`operator`) needing none** | `organization.admin__perm` (reached by `production_line.org_admin`, `quality_report.can_delete`, `part.can_delete`, `supplier.can_delete` — four independent arrows into one alias), `.plant_manager__perm` (`production_line.org_plant_manager`), `.engineer__perm` (`production_line.org_engineer`, `part.can_edit`), `.quality_inspector__perm` (`quality_report.can_approve`), `.procurement__perm` (`part.can_edit`, `supplier.can_edit`) | "Point arrows at permissions, not relations" — alias branch |
| Cross-type arrow into an already-permission target, no alias | `machine.org_engineer`'s `production_line->org_engineer`; `machine.can_delete`'s/`.can_edit`'s/`.can_view`'s `production_line->can_delete`/`.can_edit`/`.can_view`; `work_order.can_delete`'s/`.can_approve`'s/`.can_view`'s `production_line->can_delete`/`.can_edit`/`.can_view`; `production_line.can_view`'s, `quality_report.can_view`'s, `part.can_view`'s, and `supplier.can_view`'s shared `organization->member` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare permission-to-permission alias | `production_line.can_delete = org_admin`, `.can_create_work_order = can_edit`; `work_order.can_approve = production_line->can_edit` (single-term arrow, no union) | construct table row `define view: viewer` |
| Multi-operand union | `organization.can_create_part`/`.can_create_supplier`/`.can_create_quality_report`, `production_line.can_edit`/`.can_create_machine`/`.can_view`, `machine.can_delete`/`.can_edit`/`.can_view`, `work_order.can_edit`/`.can_view`, `quality_report.can_approve`/`.can_edit`/`.can_view`, `part.can_edit`/`.can_view`, `supplier.can_edit`/`.can_view` | "Always fully parenthesize" |
| Type-based tenancy, mixed-depth fan-out (three sibling types one level off the tenant root, two more nested a further one and two levels down one branch) | `organization` reached directly by `production_line`/`quality_report`/`part`/`supplier`; `machine` and `work_order` reached only through `production_line` | "Type-based tenancy" — no new construct |

**Not exercised:** wildcards, caveats/conditions, intersection, userset subjects of any kind (no
`T#rel` anywhere), self-type or multi-type tuplesets, recursive arrows, runtime-defined roles,
modular models, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding` is
`none` — every object ID (`acme-manufacturing`, `line-alpha`, `cnc-001`, `press-002`, `wo-001`,
`qr-001`, `gear-assembly`, `steel-corp`, `alice`, `bob`, `charlie`, `diana`, `eve`, `frank`,
`grace`) is already inside SpiceDB's charset, hyphens included.

### Findings

None. Every construct — the corpus's widest `member` split union, the asymmetric aliasing
(five of six roles aliased, one not, because only five are ever arrow-targeted), and the
mixed-depth fan-out — resolved using rules already on file, each traced to the specific
subsection above, with no ambiguity and no fix-and-rerun cycle. The schema converted
`PARITY OK` on the first attempt, the negative control behaved as the pack predicts, and the
full live-server list_objects/list_users sweep confirmed 100% of the store's own oracle.

### What the harness could not see

The harness compared **136 of this store's 191 assertions (71.2%)** — matching the canonical
table's own `Checks / (Checks + ListObjects + ListUsers)` metric exactly (`136/191`, no dedup
loss in either direction, per "Final harness run" above). The 40 `list_objects` and 15
`list_users` assertions are silently dropped by the harness per the known gap; all fifty-five
were closed by direct live-server verification (see "Final harness run" above), including the
exhaustiveness half a `check:` block cannot express on its own (`organization:acme-manufacturing`'s
`member` set is exactly all seven fixture users, not merely inclusive of them).

---

## `real-estate`

**Baseline:** green — `fga model test --tests store.fga.yaml`: `Tests 13/13 passing,
Checks 112/112 passing, ListObjects 7/7 passing, ListUsers 3/3 passing`. Model carried in a
separate `model.fga` file.

This is a seven-type real-estate-brokerage model built around one tenant root (`organization`)
with a four-way role union (`admin`/`broker`/`agent`/`appraiser`) fused with `[user]` into
`organization.member` — a 5-term union, tied with `healthcare` for the corpus's second-widest
`member` split behind `manufacturing`'s 7-term union (this same batch; see that store's own
section for the full sorted derivation). Three types (`property`, `listing`, `neighborhood`)
hang directly off `organization`; `transaction` nests under `listing` and `inspection` nests
under `property`. `listing` itself carries **two** parent-shaped relations — `organization:
[organization]` (used by three of its own permissions) and `property: [property]` (declared,
populated by the fixture, but never referenced by any permission on `listing` in either the
source model or the converted schema) — an ordinary unused pure-direct relation, not a new
construct, but worth naming so a reader checking `grep -c 'relation '` against the permission
count does not mistake the gap for an omission. Like `lms` (this batch), the store carries no
wildcards, no caveats, no intersection, and no self-referential type — hierarchical fan-out with
no secondary construct, matching the batch brief's framing.

**Final harness run:** `PARITY OK`, exit **0**, first attempt. `zed validate`: 18 relationships
loaded, 108 assertions run. Also clean under `--fail-on-warn`. The store's own `Checks 112/112`
collapses to **108 distinct** `(subject, permission, resource)` keys once deduplicated — its
final `tests:` block, "Agent isolation across listings", re-asserts four checks byte-for-byte
identical to assertions already made in "Listing agent manages own listings and transactions"
and "Buyer agent can edit transaction but not the listing" earlier in the file, the same
exact-duplicate `tests:`-block pattern `ads`, `groups-resource-attributes`, `calendar`,
`file-storage`, `kms`, `payment`, and `lms` (this same batch) already established.
Negative-control-verified per this file's standing method: flipping
`organization:skyline-realty#can_create_property@user:alice` to `assertFalse` fails `zed
validate` itself (exit 1 from `zed`, exit 2 from the harness, with `zed`'s own explanation trace
showing the walk `can_create_property -> admin -> user:alice`); deleting the same assertion
makes the harness report `MISSING` (exit 1).

Additionally verified end to end against a live SpiceDB v1.56.0 server:

- `WriteSchema` accepted `schema.zed` unedited, no compile step, no `use` flag.
- All 18 relationships loaded via `WriteRelationships`, all 108 distinct check-block assertions
  match exactly, matching the harness's own `108 assertions compared` one for one.
- All **7** `list_objects` and all **3** `list_users` assertions — silently dropped by the
  harness — were independently run via `LookupResources`/`LookupSubjects` and matched the
  source oracle **exactly**: `alice`'s viewable `property`/`listing`/`transaction` sets are
  exactly `{prop-123}`, `{listing-001, listing-002}`, and `{txn-001}`; `bob`'s editable
  `property` set is exactly `{prop-123}` and viewable `listing` set exactly both listings;
  `charlie`'s editable `listing` set is exactly `{listing-001}` and `diana`'s editable
  `inspection` set exactly `{insp-001}`; `listing:listing-001`'s editor set is exactly `{alice,
  bob, charlie}`; `transaction:txn-001`'s editor set is exactly `{alice, bob, charlie, eve}`;
  `inspection:insp-001`'s editor set is exactly `{alice, diana}`. This store's full oracle is
  **122/122 confirmed** (112 check + 7 list_objects + 3 list_users, `Checks + ListObjects +
  ListUsers` per `fga model test`'s own count).

### Constructs exercised

| Construct | Where | Pack rule |
|---|---|---|
| `type` → `definition` | all seven types (`user`, `organization`, `property`, `listing`, `transaction`, `inspection`, `neighborhood`) | schema-mapping construct table |
| Pure-direct relation, no split | `organization.admin`/`.broker`/`.agent`/`.appraiser`, `property.organization`, `listing.organization`/`.property`(unused by any permission)/`.listing_agent`, `transaction.listing`/`.buyer_agent`/`.seller_agent`, `inspection.property`/`.inspector`, `neighborhood.organization` | split rule, final bullet |
| Relation/permission split, tied second-widest `member` union in the corpus | `organization.member: [user] or admin or broker or agent or appraiser` | "The relation/permission split" |
| Arrow into a bare relation, needing a `__perm` alias, one reused by four independent call sites | `organization.admin__perm` (reached by `property.can_delete`, `listing.org_admin`, `neighborhood.can_edit`), `.broker__perm` (reached by `property.can_edit`, `.can_create_listing`, `.can_create_inspection`, and `listing.org_broker` — four independent arrows into one alias), `.agent__perm` (`property.can_create_listing`), `.appraiser__perm` (`property.can_create_inspection`) | "Point arrows at permissions, not relations" — alias branch |
| Cross-type arrow into an already-permission target, no alias | `transaction.can_delete`'s/`.can_edit`'s `listing->can_delete`/`.can_edit`; `transaction.can_view`'s `listing->can_view`; `inspection.can_delete`'s/`.can_view`'s `property->can_delete`/`.can_view`; `property.can_view`'s, `listing.can_view`'s, and `neighborhood.can_view`'s shared `organization->member` | "Point arrows at permissions, not relations" — no-alias branch |
| Bare permission-to-permission alias | `listing.can_delete = org_admin`, `.can_edit = can_close`; `transaction.can_view_financial = can_edit`; `neighborhood.can_edit = organization->admin__perm` (single-term arrow, no union) | construct table row `define view: viewer` |
| Multi-operand union | `organization.member`, `property.can_edit`/`.can_create_listing`/`.can_create_inspection`/`.can_view`, `listing.can_create_transaction`/`.can_close`/`.can_view`, `transaction.can_edit`/`.can_view`, `inspection.can_edit`/`.can_view`, `neighborhood.can_view` | "Always fully parenthesize" |
| Type-based tenancy, three siblings off the tenant root, two of which nest one further type each | `organization` reached directly by `property`/`listing`/`neighborhood`; `transaction` reached only through `listing`; `inspection` reached only through `property` | "Type-based tenancy" — no new construct |

**Not exercised:** wildcards, caveats/conditions, intersection, userset subjects of any kind (no
`T#rel` anywhere), self-type or multi-type tuplesets, recursive arrows, runtime-defined roles,
modular models, multi-store tenancy, model-ID pinning, contextual tuples. `id_encoding` is
`none` — every object ID (`skyline-realty`, `prop-123`, `listing-001`, `listing-002`,
`txn-001`, `insp-001`, `downtown`, `alice`, `bob`, `charlie`, `diana`, `eve`, `frank`) is
already inside SpiceDB's charset, hyphens included.

### Findings

None. Every construct — the tied second-widest `member` union, the reused `broker__perm` alias,
and `listing`'s unused `property` relation — resolved using rules already on file, each traced
to the specific subsection above, with no ambiguity and no fix-and-rerun cycle. The schema
converted `PARITY OK` on the first attempt, the negative control behaved as the pack predicts,
and the full live-server list_objects/list_users sweep confirmed 100% of the store's own oracle.
This is the batch's densest store by harness-visible fraction (see below) — a `check:`-heavy
fixture the same way `hospitality` (batch 6) and `healthcare` (batch 4) were.

### What the harness could not see

The harness compared **108 of this store's 122 assertions (88.5%** counting distinct keys
after dedup; **91.8%** by the canonical table's own `Checks / (Checks + ListObjects +
ListUsers)` metric, `112/122` — see "The canonical store table" → **Harness-visible fraction**
for which ordering this file states). The 7 `list_objects` and 3 `list_users` assertions are
silently dropped by the harness per the known gap; all ten were closed by direct live-server
verification (see "Final harness run" above), including the exhaustiveness half a `check:`
block cannot express on its own (`transaction:txn-001`'s editor set is exactly those four
people, not merely inclusive of them).

---

## Batch 7 — corpus completion

This batch converted the final four stores in `openfga/sample-stores` (`knowledge-base`, `lms`,
`manufacturing`, `real-estate`). **All 39 stores in the corpus are now converted**, closing the
loop `SKILL.md`'s "Status" table opened at 11 of 39. **Streak of zero-finding stores: 8**,
computed strictly from the canonical table's Iteration column (see "Column definitions" —
Iteration is the only sanctioned sort key in this file) rather than from any narrative order:
iteration 17 (`developer-portal`, `ecommerce`, `hospitality`, `human-resources`) and iteration
18 (`knowledge-base`, `lms`, `manufacturing`, `real-estate`, this batch) are each wholly
zero-finding, 4 + 4 = 8. This does not reach back into iteration 16 (`file-storage`,
`issue-tracking`, `kms`, `payment`): `file-storage` carries a finding (the multi-type-tupleset
alias rule — see its section), and this file's own column-definition rule states that stores
sharing an iteration carry no inferable chronological order, so whether `file-storage` falls
before, after, or between `issue-tracking`/`kms`/`payment` cannot be determined from any
committed artifact. (Corrected: an earlier draft of this line claimed 11 by placing
`issue-tracking`, `kms`, `payment` immediately before `developer-portal` and `file-storage`
earlier still within iteration 16 — an inferred same-iteration order, in violation of the
exact rule the claim sat next to. The defensible count stops at the iteration-16/17 boundary
because that ordering cannot be known, not because iteration 16's three zero-finding stores
are assumed absent from any streak — they are simply unplaceable relative to `file-storage`
without an artifact this corpus does not have.) All four stores in this batch are zero-finding,
extending the streak the full 8 stores across iterations 17 and 18 (multiple distinct
multi-tenancy fan-out shapes, `developer-portal`'s intersection, `human-resources`'s and
`knowledge-base`'s self-referential arrows, and `knowledge-base`'s wildcard) — per the repo
owner's decision recorded in this batch's brief, the corpus-wide criterion (five consecutive
zero-finding stores spanning distinct construct families) was already unreachable in a strict
sense once a prior review established the remaining corpus held zero caveats, zero exclusion,
and zero multi-type tuplesets; conversion proceeded through all 39 regardless, and convergence
is judged on the full evidence rather than the streak alone. `knowledge-base` — the batch's
dedicated wildcard/recursion store, and (since this batch completes the corpus) the last
wildcard-bearing store converted in the whole corpus — settles the question the batch brief
posed: a wildcard-bearing store *can* convert with zero findings, once
the composition it exercises (wildcard fused with a same-name recursive arrow) is already fully
corpus-confirmed by an earlier store (`gdrive`, batch 3).
