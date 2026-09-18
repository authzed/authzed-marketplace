# Schema Mapping: OpenFGA → SpiceDB

Construct-by-construct translation rules for phase 1, plus the structural rules a
generator hits immediately (splitting, parenthesization, `use` flags, codegen).

## Scope of this file

**This file covers the `clean` constructs -- the ones that translate mechanically -- and
nothing else.** Rules are added when a real corpus model forces one, not from research
(spec decision D11). A construct with no rule here has not been exercised yet; the correct
response to meeting one is to **halt and report it as an unhandled construct**, not to
improvise. See "Deliberately not written yet" at the bottom for what is known to be
missing.

Every row carries a fidelity rating (`clean` / `effort` / `heavy` / `blocked`, defined in
`migrating-to-spicedb/SKILL.md`). Constructs rated `heavy` or `blocked` are Class A
findings and live in `blockers.md`, not here.

Everything in this file was re-verified against **SpiceDB v1.56.0** and **zed v0.31.1**, and the version-sensitive `use`-flag findings re-checked on **zed v1.2.0**
(floor: v1.52.0). Where the verification produced something sharper than the design spec,
this file records what the compiler actually did.

## Construct table

| OpenFGA | SpiceDB | Rating |
|---|---|---|
| `model` / `schema 1.1` header | *(omit)* | `clean` |
| `type user` | `definition user {}` | `clean` |
| `define viewer: [user]` | `relation viewer: user` | `clean` |
| `define viewer: [user, group#member]` | `relation viewer: user \| group#member` | `clean` |
| `define viewer: [user:*]` | `relation viewer: user:*` | `clean` |
| `define viewer: [user with cond]` | `relation viewer: user with cond` | `clean` |
| `define viewer: [group#member with cond]` | `relation viewer: group#member with cond` -- a caveat on a **userset**-typed subject, not just a bare type | `clean` |
| `define view: viewer` | `permission view = viewer` | `clean` |
| `define view: a or b` | `permission view = (a + b)` | `clean` |
| `define view: a and b` | `permission view = (a & b)` | `clean` |
| `define view: a but not b` | `permission view = (a - b)` | `clean` |
| `define view: member from parent` | `permission view = parent->member` -- **operand order reverses** | `clean` |
| `condition c(p: int) { ... }` | `caveat c(p int) { ... }` -- declaration shape only, see below | `clean` |
| `module` / `extend type` | `partial` + `import` + one root file, then `zed schema compile` -- see "Modular models" | `effort` |
| A `role` type's userset unioned into many permissions (runtime-defined / "custom" roles) | no new syntax -- composition of the split rule + arrows + a userset subject pointing at a relation -- see "Runtime-defined roles" | `effort` |
| A condition comparing a stored grant time/duration against the current time (temporal access) | **Class B gate decision** -- native `use expiration` **recommended default**, `caveat` the alternative where a call site needs "as of a time" -- see "Temporal access: caveat vs. native expiration" | `effort` (native expiration, recommended) / `clean` (caveat, the alternative) |
| A tenant root type within one store, referenced by every tenant-scoped resource (type-based tenancy) | no new syntax -- ordinary `type` → `definition`, ordinary pure-type-list tenant edge -- see "Type-based tenancy" | `clean` |
| A condition comparing a **per-request-supplied** value (e.g. a client IP address) against relatively static write-time-bound context (per-request ABAC / geo-fencing) | **Class B gate decision** -- `caveat` context **recommended default**, a materialized "verified" marker relation the alternative for a call site that accepts checking a periodically-reverified state instead of the literal current request -- see "IP-based access: caveat vs. materialized marker" | `clean` (caveat, recommended) / `effort` (materialized marker, the alternative) |
| A relation whose type list unions **several differently-conditioned variants of one userset alongside the bare, uncaveated form** (usage-quota entitlements: `[T#rel, T#rel with cond_a, T#rel with cond_b, ...]`) | **Class B gate decision** -- `caveat` context **recommended default**, a materialized "verified" marker relation the alternative for a call site that accepts a periodically-recomputed quota check; the syntax itself is not new (a bare type list, split rule's final bullet, confirmed for a bare form combined with more than one distinct caveat) -- see "Usage-quota entitlements: caveat vs. materialized marker" | `clean` (caveat, recommended) / `effort` (materialized marker, the alternative) |
| A condition indexing a **container-typed** attribute the caller supplies fresh per check (a resource's own attribute map, not network/time/usage context) against a small, write-time-bound enumerable set (resource-attribute access control) | **Class B gate decision** -- `caveat` context **recommended default**, relation-name encoding (a per-value marker relation) the alternative, now fully buildable rather than structurally ruled out -- see "Resource attributes: caveat vs. relation-name encoding" | `clean` (caveat, recommended) / `effort` (relation-name marker, the alternative) |

Notes on three rows:

- **Wildcards.** `[user:*]` is `clean` where the wildcard is directly on the relation being
  translated. It is a Class A blocker only in one narrow shape: a userset reference (a
  subject **type-list** entry, `[T#rel]`) whose target translated to a bare `relation` that
  itself carries the wildcard. If the target translated to a permission -- which the split
  rule below produces automatically whenever the source `define` fuses a wildcard with an
  operator -- SpiceDB accepts it. An **arrow** (`->`) into a wildcard-bearing relation is a
  different mechanism and is never subject to this restriction at all, split or not --
  corpus-verified on `gdrive` (batch 3), the corpus's first wildcard-bearing store. See
  `blockers.md` for the verified shape table, the live wildcard-through-arrow confirmation,
  and the detection rule.
- **Conditions → caveats.** The declaration shape is mechanical: drop the colon in the
  parameter list (`p: int` → `p int`), keep the name, keep the body. Caveat names are
  looser than other identifiers, but normalize them anyway -- the relationship-string
  grammar phases 3 and 5 emit does enforce the strict name regex, so `caveat c(p int)`
  compiles and then cannot be referenced from a relationship string
  (`naming-normalization.md`). The
  parameter *type* vocabulary and the expression body are covered for the nine types
  `condition-data-types` exercises -- see "Caveat parameter types and expression bodies"
  below. Halt on any parameter type not in that table.
  One hard rule applies immediately: **emit no unused caveat parameters.** SpiceDB rejects
  `caveat c(a int, b int) { a > 0 }` with ``parameter `b` for caveat `c` is unused`` --
  verified rejected at both `zed validate` and `WriteSchema` on zed v0.31.1 / SpiceDB
  v1.56.0. (An earlier revision of the design spec called this a `WriteSchema`-only
  rejection; that has since been corrected. On an older toolchain it may still surface
  only at deploy time. Either way: do not emit them.)
- **Modular models.** `fga.mod` / `module` / `extend type` map onto SpiceDB `partial` +
  `import`, resolved with `zed schema compile`. See "Modular models" below.

## Emission order

Emit a `.zed` file in this order:

1. `use` flags -- **enforced.** All `use` statements must precede every definition;
   otherwise the compiler fails with ``use expressions must be declared before any
   definition``.
2. `caveat` declarations.
3. `definition` blocks.

Only rule 1 is a compiler requirement. Steps 2 and 3 are **convention**: a caveat declared
after the definitions that reference it compiles and writes fine, as does a definition
referencing another defined later in the file, and a permission written above the relations
it uses. Do not treat a schema that orders these differently as broken -- there is nothing
to fix.

Within a definition, emit all `relation` lines before all `permission` lines. This is
convention, not a parser requirement, but it matches every schema the plugin ships and
keeps diffs readable.

## The relation/permission split

OpenFGA fuses "directly assignable" and "computed" into one `define`. SpiceDB cannot
express both under one name, so **a `define` that mixes a `[...]` type list with any
operator splits into two names**:

```
# OpenFGA
define viewer: [user, group#member] or editor or editor from parent
```
```zed
# SpiceDB
relation viewer__direct: user | group#member
permission viewer = (viewer__direct + editor + parent->editor)
```

Rules:

- The **permission keeps the original name**, the relation takes the suffix. Every *read*
  site -- other permissions, arrows, subject-type references, application code, test
  assertions -- therefore keeps working unchanged. **The write side does not**: the
  resource side of a stored relationship must be renamed to the suffixed relation. See
  "A split name means two different things depending on position" below -- getting this
  wrong is the most likely way to corrupt a phase-3 data rewrite.
- Default suffix is `__direct`. It is a Class B finding: record it in `migration-plan.md`,
  because it drives the phase-3 data rewrite (every stored `viewer` tuple becomes
  `viewer__direct`) and any phase-4 code that writes that relation.
- The split suffix occupies the same per-definition namespace as every other relation and
  permission, so allocate it through the same per-type registry that normalizes names
  (`naming-normalization.md`). `viewer__direct` colliding with a real source relation
  named `viewer__direct` must be caught, not assumed away.
- A `define` with **only** a type list stays a plain `relation`. A `define` with **no**
  type list becomes a plain `permission`. Neither splits.

### A split name means two different things depending on position

One source name becomes **two** SpiceDB names, and which one is correct depends on where
the name appears. Corpus-verified on the `github` store (v1.56.0), whose
`organization.member` splits *and* is used in both positions in the same store:

| Position | Source | SpiceDB | Why |
|---|---|---|---|
| Resource side of a relationship (the write path) | `organization:o#member@user:erik` | `organization:o#member__direct@user:erik` | you cannot write to a permission |
| Subject side of a relationship (a userset reference) | `...@organization:o#member` | `...@organization:o#member` | the allowed-type list names the permission |
| Check / assertion surface | `check(member, organization:o)` | `organization:o#member` | the permission kept the name |

Both uniform mappings are wrong, and each fails with a distinct, verified error:

```
# mapping member -> member (identity) applied to the write path:
cannot write a relationship to permission `member` under definition `organization`

# mapping member -> member__direct applied to the subject side:
subjects of type `organization#member__direct` are not allowed on relation
`organization#repo_admin`
```

**Consequence for `migration-map.json`.** Its `permissions` table is a *single* per-type
map, and `IdMap.apply` uses it for the check surface and for subject relations alike --
both of which want the **unsuffixed** name. So:

- `migration-map.json` records the split source name as **identity** in `permissions`
  (`"member": "member"`) by default -- or, where the gate's **Permission naming style**
  decision below renames it, the recorded verb form (`"owner": "own"`) in its place, applied
  the same way identity mapping is. Either way, that table carries the read surface only, and
  stays that way -- `IdMap.apply`, which rewrites checks and test assertions, must never be
  made to return a `__direct` name; doing so would silently narrow every check and assertion
  that names a split permission, with no error at all (see below).
- The write-side rename is recorded separately, in `migration-map.json`'s own
  `relation_splits` key -- `{"member": {"relation": "member__direct", "permission":
  "member"}}` -- **not** by overloading `permissions` with it. `relation_splits` is a
  distinct table precisely because `permissions` cannot safely hold both names under one
  entry without an accessor having to guess which one a given caller wants; naming both
  fields explicitly (`relation` for the write target, `permission` for the check target)
  removes the guess. `IdMap.write_relation(source_type, source_relation)` is the accessor
  that reads it; `IdMap.apply` does not and never will. The same pairing is also recorded in
  `migration-plan.md`'s **Relation splits** table, in human-readable form -- keep the two in
  sync, the same as the **Identifier map**.

Merging the write-target name into `permissions` itself, instead of a separate
`relation_splits` entry, would break the read surface. The write-path misapplications above
are not this risk -- both fail **loudly**, verified: SpiceDB unconditionally rejects a write
to a permission, and unconditionally rejects an invalid subject-relation type. This one is
different in kind: `member__direct` is a legal check target in SpiceDB (a bare relation may
always be checked directly), so a check or assertion rewritten against the wrong name
returns a **narrower answer with no error at all**. It does not strike in phase 3 -- the
data rewrite runs fine either way, on either name -- it strikes in phases 4-5, wherever
application code or test assertions consult `migration-map.json` for a check target. The
harness will not catch it either, for the same reason: `IdMap.apply` only ever maps
assertions through `permissions`/`types`, and never reads `relation_splits`, so a harness run
comparing checks stays green regardless of whether `relation_splits` is present, absent, or
wrong. State the split in both artifacts, in their own idiom.

### The split is local, not viral

OpenFGA forbids any rewrite on a tupleset relation -- the relation on the right of `from`.
From `openfga/pkg/typesystem/typesystem.go`:

```go
// Tupleset relations must only be direct relationships, no rewrites are allowed on them.
if reflect.TypeOf(tuplesetRewrite.GetUserset()) != reflect.TypeOf(&openfgav1.Userset_This{}) {
    return fmt.Errorf("the '%s#%s' relation is referenced in at least one tupleset and thus must be a direct relation", ...)
}
```

So anything used as an arrow's left operand is guaranteed pure-direct, maps 1:1 to a
SpiceDB `relation`, and never needs a split. A split therefore never cascades into
another definition, and OpenFGA models automatically satisfy SpiceDB's rule that
*permissions cannot be used on the left hand side of an arrow*.

### A userset subject may point at a split permission

`[organization#member]` where `member` splits becomes `organization#member` referring to a
**permission**. That is legal: SpiceDB accepts a subject-relation reference to a
permission, at local compile, at `WriteSchema`, and at check time. Verified on v1.56.0 --
the `github` sample store needs it on its very first definition. No special handling is
required; the reference just keeps the original name, which the split rule already
guarantees.

### Permission naming style: preserve source names, or rename nouns to verbs

"The permission keeps the original name" (above) keeps a name that is very often a noun.
OpenFGA has no relation/permission distinction -- `define owner` is just a define -- so
models name things as roles, and a fused define's permission side inherits that noun
unchanged: `permission owner`, not `permission own`. `relation owner__direct` is correct
style (a relation is a role noun); the permission is not
(`spicedb-schema-design/references/anti-patterns.md`, "Confusing Relations with Permissions"
-- relations are nouns, permissions are verbs; that rule is not restated here). This pack
does not fix that silently -- whether to fix it at all is a gate decision,
`/spicedb-dev:migrate` step 5's **Permission naming style** row, defaulting to leaving it
alone. It governs the *permission* name only, on every plain permission and every split
permission alike; a split relation's `__direct` name is never touched by it, because the
relation side already has the correct style.

**Preserve source names (default).** Every permission keeps the name the split (or a plain
`define`) assigned it. Nothing renames: call sites, stored data, dashboards, and anything
outside this codebase that names a permission by string keep working unchanged. The migrated
schema will not read as idiomatic SpiceDB, and that is the conscious cost of the default, not
an oversight.

**Rename nouns to verbs.** Only where a defensible verb exists -- this pack fixes exactly
five pairs, and invents no others:

| Noun | Verb |
|---|---|
| `owner` | `own` |
| `viewer` | `view` |
| `editor` | `edit` |
| `reader` | `read` |
| `writer` | `write` |

A noun with no defensible verb (`member`, `admin`, `organization_admin`, and any compound
role name -- corpus examples: `document_manager`, `hr_admin`) is never auto-renamed: list it
for the user to name individually or leave, and never invent an awkward verb to force the
rule (`membership`, `administrate`) -- an invented verb is a worse name than the noun it
replaced. Renaming costs exactly what preserving avoids: it changes a name an application may
check by string, and the migration only rewrites the call sites and stored data *inside* the
codebase it converts -- a dashboard, an audit log, or another service that names this
permission from outside the migration's reach breaks silently.

**Where it's recorded.** The chosen style, the specific noun -> verb pairs actually applied,
and the no-defensible-verb list with the user's per-name choice are recorded under
`migration-plan.md`'s `Decisions` -> `### Permission naming style`. The rename needs no new
machine-readable key: it is written directly into `migration-map.json`'s existing
`permissions[type]` entry (`"owner": "own"` in place of the identity `"owner": "owner"`) and,
for a split relation, into that same source relation's
`relation_splits[type][name].permission` field -- the field "Consequence for
`migration-map.json`" above already carries, now potentially holding the renamed value
instead of the identity one. Every phase that resolves a permission's SpiceDB name already
reads one of those two fields for every check, arrow, and subject-relation reference
(`code-mapping.md`'s call-mapping rule 3; the arrow rules below) -- a chosen rename applies
everywhere with no separate mechanism to keep in sync, the same reason `relation_splits`
itself needed no parallel table for the read side. `migration-plan.md`'s `## Identifier map`
carries the human-readable record (`kind` `permission`), the same table any other renamed
identifier already uses -- this is not a new artifact.

## Arrows

`define view: member from parent` → `permission view = parent->member`.

**The operand order reverses.** OpenFGA reads `<target> from <tupleset>`; SpiceDB reads
`<tupleset>-><target>`. This is the single most likely translator bug -- carry a
regression case for it and check it on every change.

### Point arrows at permissions, not relations

`owner->repo_admin` compiles, but emits a lint:

```
warning: Arrow `owner->repo_admin` under permission "admin" references relation
"repo_admin" on definition "organization"; it is recommended to point to a permission
(arrow-references-relation)
```

A naive translator produces the warned-about form throughout, because OpenFGA's
pure-direct arrow targets all become SpiceDB relations. Fix it on the *target* definition:

```zed
definition organization {
    relation repo_admin: user | organization#member
    permission repo_admin__perm = repo_admin      // alias for arrow targets
}
```

and point the arrow at `owner->repo_admin__perm`.

- Default alias suffix `__perm`, allocated through the same per-type registry as every
  other name.
- Emit an alias **only** when the arrow's right operand resolved to a `relation`. If the
  target `define` split, the arrow already names a permission and nothing extra is needed.
- Unlike `__direct`, this suffix is schema-internal: it changes no stored tuple and no
  call site, so it is not a data or code rewrite.
- **Check it mechanically with `zed validate --fail-on-warn`.** The lint is a warning, so
  a plain `zed validate` stays green on the un-aliased form. Corpus-verified on the
  `github` store: with the three `__perm` aliases `--fail-on-warn` passes; pointing the
  same three arrows at the bare relations produces exactly three
  `arrow-references-relation` warnings.
- **`__perm` aliases are exempt from the noun/verb rule, by design, not by oversight.**
  `permission owner__perm = owner` names a permission with a noun stem -- exactly the shape
  "Permission naming style" above governs on a human-authored permission. This one is
  different in kind, not just in degree: it is a mechanically generated artifact of
  `arrow-references-relation` compliance, and, as the bullet above already establishes, it
  "changes no stored tuple and no call site" -- nothing outside the schema itself ever names
  it. The role it aliases frequently has no defensible verb at all (`admin__perm`,
  `member__perm` -- "is an admin" has no natural action form, the same gap "Permission naming
  style" documents for the split permission itself). Verb-renaming only the aliases that
  happen to have one (`owner__perm` -> `own__perm`) while leaving `admin__perm` and
  `member__perm` as nouns would leave the schema in both styles at once for no reason a
  reader could recover from the name alone -- worse than a stated exemption. **Mechanical
  generated names (`__direct`, `__perm`) are exempt from the noun/verb rule; it governs
  human-authored names, and a generated alias is not one.** This needs no gate decision: it
  is not a choice presented to the user, it is a scope statement about which names the rule
  was ever written to cover.

### A multi-type tupleset can resolve the same arrow target to a relation on one allowed type and a permission on another

The alias rule above assumes a single target type: `owner->repo_admin` targets exactly one
definition, so "did the target `define` split" has one answer. A tupleset relation with
**more than one allowed bare type** (`relation parent: drive | folder`) can have a different
answer per type -- corpus-forced on `file-storage` (batch 5): `drive.owner: [user]` has no
operator and stays a bare `relation` by the split rule's final bullet, while `folder.owner:
[user] or owner from parent` fuses a type list with an operator and splits into a
`permission`. Both types are legal members of `folder.parent`'s allowed-type list, and one
arrow (`parent->owner`) must resolve consistently regardless of which of them the subject
actually is.

Verified live on v1.56.0: writing the arrow as `parent->owner` compiles under plain `zed
validate`, but produces exactly one `arrow-references-relation` warning -- for the `drive`
branch only, since `drive.owner` is a bare relation; the `folder` branch is silent, since
`folder.owner` already resolved to a permission by the split. Applying the `__perm` alias
asymmetrically -- only to `drive`, since a literal reading of the bullet above ("emit an
alias only when the target resolved to a relation") says that is the only type that needs
one -- does not fix it: the arrow still spells the target `owner`, and `owner` is still a
bare relation on the `drive` branch no matter what else exists under a different name.
**The alias must be applied to every one of the tupleset's allowed types, including ones
that already resolved to a permission on their own, and the arrow must reference the aliased
name instead of the original:**

```zed
definition drive {
	relation owner: user
	permission owner__perm = owner        // added even though drive.owner alone would not need it
}

definition folder {
	relation parent: drive | folder
	relation owner__direct: user
	permission owner = (owner__direct + parent->owner__perm)   // arrow targets the alias, not `owner`
	permission owner__perm = owner        // folder's own alias, so the name stays valid on the recursive branch too
}
```

`zed validate --fail-on-warn` is clean once both aliases exist and the arrow targets
`owner__perm` uniformly. This is not a new construct -- the split, the arrow rule, and the
alias mechanism are each already on file -- but it is a genuine gap in the bullet above as
written: "only when the target resolved to a relation" implicitly means "for this one target
type," and a multi-type tupleset breaks that assumption. Checked before writing this as new:
no other committed `schema.zed`'s multi-type relation is ever used as an arrow's tupleset at
all (`grep`-verified across all 27 prior stores) -- `superadmin`'s only other type list of
this shape, `system.admin: employee | application`, is never a tupleset (both `employee` and
`application` are declared with empty bodies, and nothing ever arrows through `admin`); every
other multi-type relation in the corpus mixes a bare type with a userset (`user |
group#member`) or a type with its own wildcard/caveated variant (`user | user:*`, `user |
user with cond`), never two distinct plain object types each independently defining the
arrow's target name.

### Partial alias application on a multi-type tupleset is silent, and `--fail-on-warn` does not catch it

The previous subsection's rule is stated once, in full, right above: apply the `__perm`
alias to *every* one of the tupleset's allowed types, not only the ones that individually
need one. It is nonetheless easy to half-apply, because the two branches look asymmetric on
casual inspection -- `drive.owner` is visibly a bare relation and obviously needs an alias;
`folder.owner` is visibly already a permission (from the split), and it is tempting to reason
"`folder.owner` is already a permission, it doesn't need an alias" and stop there. That
reasoning is wrong, and unlike the bare-relation mistake the previous subsection documents,
**it produces no warning at all** -- not "one warning naming only the branch that needs it,"
zero.

Verified live on v1.56.0. Starting from the corrected schema two subsections up, delete only
`folder`'s own `owner__perm` alias (leave the arrow spelled `parent->owner__perm`, leave
`drive.owner__perm` in place -- the one branch a literal reading of the alias rule's original
wording would call "obviously needed"):

```zed
definition drive {
	relation owner: user
	permission owner__perm = owner
}

definition folder {
	relation parent: drive | folder
	relation owner__direct: user
	permission owner = (owner__direct + parent->owner__perm)
	// no folder.owner__perm -- "folder.owner is already a permission, it doesn't need an alias"
}
```

- `zed validate --fail-on-warn` against this: `Success! - 0 relationships loaded, 0
  assertions run, 0 expected relations validated`. **Zero warnings, exit 0.** Not the
  single `arrow-references-relation` warning the bare-relation mistake produces -- nothing.
  `zed schema write` against a live server accepts it the same way, silently.
- Live data: `folder:f1#owner__direct@user:bob`, `folder:f2#parent@folder:f1` (`f2`'s parent
  is a *`folder`*, the branch missing the alias). `zed permission check folder:f2 owner
  user:bob --explain` returns **`false`**, and the explain tree is not "unsatisfied" -- it is
  *absent*: the only child evaluated is `folder:f2#owner__direct`, which fails; the
  `parent->owner__perm` arm of the union does not appear in the trace at all, because `folder`
  has no `owner__perm` for the dispatcher to resolve to. Re-adding `folder.owner__perm =
  owner` (the fix the previous subsection already prescribes) flips the same check to `true`,
  with the explain tree now showing the arrow resolve through `folder:f1#owner` as expected.
  `bob` owns `f1` directly and should transitively own `f2` through it; the half-aliased
  schema denies him with no error anywhere in the pipeline that produced it.

This is what makes the partial-application mistake strictly worse than the bare-relation
one the previous subsection documents: the bare-relation mistake still compiles to a
schema that is *correct on live data* (a bare relation used as an arrow target still
resolves; the lint is purely stylistic), so an operator who ignores the warning ships a
working, if unrecommended, schema. The partial-alias mistake compiles to a schema that is
**wrong on live data for the branch missing the alias**, and nothing in `zed validate`,
`zed validate --fail-on-warn`, or `WriteSchema` distinguishes it from the fully-correct
form. This file's "Point arrows at permissions, not relations" section's instruction to verify the split/alias mechanism with
`zed validate --fail-on-warn` does not fire for this failure mode -- the check the rest of
this pack relies on is silent here, which is why this gap needs its own, different check.

**The rule:** on a multi-type tupleset, the `__perm` alias must be declared under the exact
name the arrow targets on **every** type the tupleset admits, including a type that already
has its own permission carrying the *concept* the arrow needs (`folder.owner`) but not the
alias's *exact name* (`folder.owner__perm`) -- the arrow dispatches on the literal name, not
on whatever else happens to mean the same thing on that type. Applying the alias to some but
not all of the tupleset's types is indistinguishable from applying it to all of them, under
every check this pack otherwise recommends.

**The check that does catch it:** for each multi-type tupleset relation that is ever used as
an arrow's left operand, confirm the arrow's target name resolves -- as either a `relation`
or a `permission` -- on every type in that tupleset's allowed-type list, not only the type(s)
a hand-written test happens to exercise. This has to be driven from the schema text itself
(`zed` has no flag that performs it); the following script does, expanding `...partial` spreads
so `use partial` schemas are checked the same way as single-block ones:

```python
#!/usr/bin/env python3
# check_arrow_targets.py -- for every multi-type tupleset relation used as an
# arrow's left operand, confirm the arrow's target resolves (as a `relation`
# or a `permission`) on every type the tupleset admits. Exit 1 and name every
# gap if not; exit 0 if every arrow target resolves everywhere it can dispatch.
import re, sys

def strip_comments(t):
    return "\n".join(l for l in t.splitlines() if not l.strip().startswith("//"))

def parse_blocks(text, keyword):
    blocks = {}
    for m in re.finditer(keyword + r"\s+(\w+)\s*\{", text):
        i = m.end(); depth = 1
        while depth: depth += {"{": 1, "}": -1}.get(text[i], 0); i += 1
        blocks[m.group(1)] = text[m.end():i - 1]
    return blocks

def parse_definitions(text):
    partials = parse_blocks(text, "partial")
    defs = parse_blocks(text, "definition")
    for d, body in list(defs.items()):
        for p in re.findall(r"\.\.\.(\w+)", body):
            if p in partials: body += "\n" + partials[p]
        defs[d] = body
    return defs

# Parse (kind, name, content) triples for every `relation` and `permission`
# member of a definition body. `content` runs from just after the `:`/`=` to
# the START of the next relation/permission keyword (or end of body) -- NOT
# to the next newline. zed's own grammar has no line-based statement
# terminator, so a relation's type list or a permission's expression is free
# to wrap across multiple lines and still compile; a line-anchored regex
# (`[^\n]+`) silently truncates at the wrap point instead of failing loudly,
# which is exactly the gap that let a wrapped multi-type tupleset or a
# wrapped arrow expression pass this script with zero problems reported
# while still being broken on a live server. Balanced-block parsing (already
# used for `parse_blocks` above) does not have this failure mode, so member
# parsing is held to the same standard here.
_MEMBER_RE = re.compile(r"\b(relation|permission)\s+(\w+)\s*[:=]")

def parse_members(body):
    starts = list(_MEMBER_RE.finditer(body))
    members = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(body)
        members.append((m.group(1), m.group(2), body[m.end():end]))
    return members

def rel_types(body):
    out = {}
    for kind, name, content in parse_members(body):
        if kind != "relation":
            continue
        types = []
        for t in content.split("|"):
            t = t.strip().split("with")[0].strip().rstrip(";").strip()
            t = t.replace(":*", "").split("#")[0].strip()
            if t: types.append(t)
        out[name] = types
    return out

def declared_names(body):
    return {name for _, name, _ in parse_members(body)}

def check(path):
    text = strip_comments(open(path).read())
    defs = parse_definitions(text)
    declared = {d: declared_names(b) for d, b in defs.items()}
    problems = []
    for dname, body in defs.items():
        rtypes = rel_types(body)
        perms = {name: content for kind, name, content in parse_members(body) if kind == "permission"}
        for pname, expr in perms.items():
            # Strip a same-line trailing comment before scanning for arrows: a
            # `//` comment can sit right after `->`, on the line its right
            # operand wraps away from, and strip_comments() above only drops
            # lines that are wholly a comment, not a trailing one. \s* on both
            # sides of -> tolerates any whitespace between an operand and the
            # arrow -- spaces, a single line break, several blank lines -- so
            # the token is found no matter where zed's DSL (which has no
            # line-based statement terminator) lets it wrap.
            arrow_expr = re.sub(r"//[^\n]*", "", expr)
            for left, right in re.findall(r"(\w+)\s*->\s*(\w+)", arrow_expr):
                for t in rtypes.get(left, []):
                    if t in defs and right not in declared[t]:
                        problems.append(
                            f"{path}: {dname}.{pname} -- '{right}' (target of "
                            f"{left}->{right}) is not declared on type '{t}', "
                            f"one of '{left}''s allowed types "
                            f"({', '.join(rtypes[left])})")
    return problems

problems = [p for f in sys.argv[1:] for p in check(f)]
for p in problems: print(p)
sys.exit(1 if problems else 0)
```

Verified against both schemas above: run on the half-aliased schema, it reports exactly one
problem (`folder.owner -- 'owner__perm' (target of parent->owner__perm) is not declared on
type 'folder', one of 'parent''s allowed types (drive, folder)`), exit 1; run on the fully
aliased schema from the previous subsection, it reports nothing, exit 0.

**Corrected (previously line-anchored, now balanced-block):** an earlier version of this
script matched each relation's type list and each permission's expression with a
line-anchored regex (`[^\n]+`, stopping at the first newline). That version silently missed
two variants of the exact gap it exists to catch, both confirmed live on v1.56.0: (1) a
multi-type tupleset's arrow wrapped across a line break inside the permission expression --
the arrow token never appeared in the truncated match, so the script never looked at it and
exited 0 on a schema where the live `--explain` tree showed the arrow arm absent entirely;
(2) a multi-type tupleset relation whose own type list was wrapped across a line break --
only the first line's type was collected, so the second (and any later) allowed type was
never checked against, again exiting 0 while live behavior was broken. Neither is
hypothetical: this is the formatting the corpus itself already uses in three places
(`advanced-entitlements/schema.zed`, `condition-data-types/schema.zed`,
`advanced-entitlements/schema-materialized-marker.zed`, each wrapping a relation's type
list) -- all three on zero-arrow stores, so the corpus's own clean sweep never exercised the
line-anchored version's blind spot. The version above replaces the line-anchored regex with
`parse_members`, which reads each relation/permission's content up to the *next*
relation/permission keyword rather than up to the next newline, so wrapping either a type
list or an expression across lines no longer changes what gets checked. Verified against two
synthetic reproductions of both wrapped variants (a multi-type tupleset relation split across
lines, and a `parent->owner__perm`-style arrow split across lines, each built on top of the
half-aliased schema above): the line-anchored version exits 0 on both (misses the gap); the
balanced-block version above correctly reports the same `folder.owner -- 'owner__perm'...`
problem and exits 1 on both.

**Corrected again (previously whitespace-sensitive, now tolerant of any gap around `->`):**
a re-review of the balanced-block fix above found a *third* variant of the same gap, live-
confirmed on v1.56.0 and independent of the member-boundary parsing that fix addressed: the
arrow-matching regex itself, `re.findall(r"(\w+)->(\w+)", expr)`, required `->` to sit with
no whitespace on either side. A line break immediately after the arrow --

```
permission owner = owner__direct + parent->
	owner__perm
```

-- is valid zed (`zed validate --fail-on-warn` passes it cleanly), and by the time this text
reaches `expr` the balanced-block member parsing has already captured the whole wrapped
expression as one string, so the content was never the problem; the adjacency requirement in
the arrow regex itself was. Confirmed live: deployed on a `folder` type with `parent: drive |
folder` and `permission owner = owner__direct + parent->\n\towner__perm` but no
`folder.owner__perm` alias, a two-hop `folder->folder->drive` chain whose root `drive` has a
real owner produces `check` returning `false` on the outer folder and an `--explain` tree
with only the `owner__direct` leaf -- the `parent->owner__perm` arm is absent, not merely
false -- the identical silent-failure signature the two prior variants produced, and the
prior (balanced-block-only) version of this script exits 0 against it, reporting nothing.

The fix is `\s*` on both sides of `->` (`re.findall(r"(\w+)\s*->\s*(\w+)", arrow_expr)`),
which is tolerant of any amount of whitespace, so it equally covers a break before the arrow,
several blank lines, or a three-line split with `->` alone on its own line -- except zed's own
parser rejects all three of those (`Expected end of statement or definition, found:
TokenTypeRightArrow`): a statement may not end mid-expression on a bare identifier, so only a
line that ends *with* the arrow (or another trailing operator) continues legally. That leaves
"break after `->`" as the only whitespace-shaped variant reachable in schema that compiles at
all, but the fix does not depend on that being the only case zed happens to allow today, and
it additionally strips a same-line trailing `//` comment before scanning
(`parent-> // note\n\towner__perm` was verified to still evade the plain `\s*` version, since
a comment is not whitespace; stripping it first closes that too).

Run across every `schema.zed` and `schema-*.zed` in `corpus-runs/` (44 files as of batch 7,
the corpus's final batch -- `ls */schema.zed */schema-*.zed | wc -l` -- `file-storage`'s
fully-aliased `folder.parent: [drive, folder]` included), the current version reports nothing
for all of them -- this pack's own committed schemas were already correct on this point, and
the script does not false-positive on `use partial` schemas that spread relations/permissions
in from a `partial` block rather than declaring them directly (`modular/schema-use-partial.zed`'s
`organization->member`, contributed by `partial core_organization`, resolves correctly once
partials are expanded before the name lookup). Run this check alongside `zed validate
--fail-on-warn`, not instead of it -- the two catch different failure modes on the same
construct (bare-relation target vs. partially-aliased target) and neither substitutes for the
other.

**What "verified" means here, stated plainly:** three confirmed evasions have been closed
across two fix rounds (line-anchored member content; whitespace- and comment-insensitive
arrow matching), each reproduced live and closed against a live-confirmed silent-check-failure
signature, and the result holds with zero false positives across the entire 44-file corpus.
That is evidence this script works on every shape the corpus and this review process have
produced, not a proof it is exhaustive. This is a regex/balanced-block heuristic over schema
text, not a real zed parser: an earlier report called it "robust" without qualification, and
that overstated what had actually been checked at the time. Do not extend that word to mean
"immune to a fourth variant." Rerun it (and re-derive its file count) after any future change
to how this pack or a generator formats a multi-type tupleset arrow, and treat a clean exit
from this script as corroborating evidence alongside `zed validate --fail-on-warn` and a live
parity check -- never as a standalone guarantee.

### An arrow discards the subject relation

`relation parent: group#member` with `parent->something` walks to `group`'s `something`
and **ignores the `#member`** (verified). If a tupleset relation's allowed types
include a userset (`T#rel`), flag it rather than translating silently -- whether OpenFGA
resolves that case the same way has not been established by corpus evidence.

### A self-referential arrow (tupleset target type = the arrow's own definition) needs no special rule

OpenFGA's "category with a few values" ABAC-as-ReBAC pattern (a status flag modeled as a
relation from a type to *itself*, e.g. `define draft: [document]` on `type document`, then a
self-loop tuple `document:x#draft@document:x` standing in for "x is a draft") produces an
arrow whose tupleset relation's allowed type is the same definition the arrow lives in --
`permission can_edit = draft->owner_email_verified` inside `definition document`, walking
from `document` back to `document`. Nothing in this file's arrow rule excludes this shape,
and none of `github`/`modular`/`custom-roles` exercised it (their arrows all cross to a
genuinely different type). Corpus-verified clean on `abac-with-rebac`, both via
`zed validate` and end to end on a live v1.56.0 server across all twelve of the store's
assertions: a same-type arrow resolves identically to a cross-type one, with no extra syntax
and no fidelity cost. This is a confirmation of the existing rule's generality, not a new
construct.

### A same-name recursive arrow (the arrow's target permission is the permission being defined) is the same rule, not a stronger one -- but wants a live multi-level confirmation

A sharper-looking special case of the self-referential arrow above: not just a same-type
tupleset, but one whose right operand names **the very permission being defined**, so
resolving it recurses through an unbounded chain rather than walking one hop to a
differently-named permission. OpenFGA's org-chart idiom is the canonical example: `define
can_manage: manager or can_manage from manager` on `type employee`, where "who manages me" is
"my direct manager, plus whoever manages my direct manager, all the way up." This maps with
the same arrow rule already on file, no new syntax:

```zed
definition employee {
	relation manager: employee

	permission can_manage = (manager + manager->can_manage)
}
```

`manager` is a pure type list (`[employee]` in the source), so it stays a plain `relation` by
the split rule's final bullet -- never viral (see "The split is local, not viral"), so nothing
about the recursion forces a split here either. The arrow's right operand, `can_manage`, is
itself the permission under construction, which already resolved to a `permission` before the
arrow is reached in the same `definition` block -- so "Point arrows at permissions, not
relations" 's alias branch does not fire: the target is a permission, not a relation, from the
first pass. Nothing in the self-referential-arrow rule above excludes the target permission's
name matching the source permission's own name -- that rule already reads "nothing... excludes
this shape" for *any* same-type target, and a same-*name* target is only a special case of
"same type."

**This shape already existed in the corpus before `expenses` -- `gdrive`'s own `folder.viewer
= (viewer__direct + owner + parent->viewer)` (`folder.parent: [folder]`) is byte-for-byte the
identical pattern, just never named "recursive" in `gdrive`'s own write-up.** What `expenses`
adds is not a new rule but the first **live, multi-level** exercise of it: `gdrive`'s own
fixture never contains a `folder#parent@folder` tuple at all (only one `folder` object exists
in that store's data), so `folder.viewer`'s recursive arm is present in the schema but never
actually walked more than zero times by that store's own oracle -- confirmed by reading
`gdrive`'s committed `validation.yaml`, which has no `folder->folder` relationship of any
kind. `expenses`' own fixture, by contrast, is a real three-link manager chain (`daniel`'s
manager is `matt`, `matt`'s is `sam`, `sam`'s is `emily`), and `modeling-guide`'s later reuse
of the same pattern (`folder.can_edit`, batch 4) is dormant in exactly the same way `gdrive`'s
is -- its own fixture also has only one `folder` object. Corpus-verified on `expenses`
(v1.56.0 / zed v0.31.1): `WriteSchema` accepts the schema unedited, `PARITY OK` on the first
attempt, and a live-server probe confirms the recursion resolves more than one hop deep, not
merely one level of "and my manager's own direct reports" -- `lookup-subjects
report:daniel-chair1 approver employee` (itself one arrow hop, `submitter->can_manage`, onto
the recursive permission) returns all three ancestors in the chain (`matt`, `sam`, `emily`),
matching the source's own `list_users` oracle exactly. This is a confirmation of the existing
self-referential-arrow rule's own stated generality, not a new construct -- recorded
explicitly here, with the corrected "which stores actually exercised it" record, because
`gdrive`'s own section did not use the word "recursive" or note that its own fixture leaves
the recursive arm dormant, and a future reader comparing stores by prose alone could otherwise
believe `gdrive` already settled the live multi-level case when it only settled that the
schema shape compiles and deploys.

## Runtime-defined ("custom") roles

Corpus-verified on `openfga/sample-stores/stores/custom-roles` (v1.56.0 / zed v0.31.1) --
the store built to test whether a customer defining their own roles at runtime can be
modeled in SpiceDB, and at what cost. `migrating-to-spicedb/SKILL.md`'s Fidelity ratings
section calls this "the construct most likely to decide a real B2B deal" and warns that a
prior, independent analysis wrongly assumed it `blocked` when it was actually `heavy`. This
section settles the specific shape where the answer is better than either: fully
expressible as pure data, at the `effort` rating the constituent rules already carry, with
no dedicated role-schema construct at all.

### There is no schema-level construct for this, and none is needed

SpiceDB resolves relation and permission *names* at schema-write time, so a role whose
permission set is chosen by a customer at runtime can never become a relation name --
there is no variable in the relation position. `custom-roles`' own model does not need one.
Its shape is a plain composition of rules already in this file:

1. A `role` type with one relation, `assignee: [user, team#member, org#member]` -- a pure
   type list, so it stays an ordinary `relation`, no split.
2. Every permission the product is willing to let *any* role grant unions `[role#assignee]`
   into its own directly-assignable list, alongside whatever OpenFGA already grants that
   permission by ownership or membership: `define asset_creator: [role#assignee] or owner`.
   Because this fuses a type list with an operator, **the split rule applies exactly as
   written** -- `relation asset_creator__direct: role#assignee` +
   `permission asset_creator = (asset_creator__direct + owner)` -- with no new rule.
   Ten of `org`'s eleven relations split this way, four of `asset-category`'s five, three
   of `asset`'s four; every arrow in the store (`asset_creator from org`,
   `commenter from category`, etc.) lands on a target that already split, so -- as in
   `modular`'s confirmed branch -- none of them need a `__perm` alias.
3. A role is *granted* a permission by writing one relationship onto that permission's own
   `__direct` relation: `asset_category:website-content#editor__direct@role:content-manager#assignee`.
   A role is *assigned* to a user, team, or org member the same way OpenFGA assigns anything
   else -- `role:content-manager#assignee@team:marketing#member` -- a userset subject
   pointing at a plain relation (`team#member`), nothing role-specific.

### The decisive test: does the schema grow with the customer's role vocabulary

This is what separates `effort` from `heavy` (`migrating-to-spicedb/SKILL.md`: `heavy` is
"generated schema... the cost grows with the customer's vocabulary"). Verified live against
this store's own deployed schema and data on a v1.56.0 server: introducing a brand-new role
the fixture never mentions -- `role:senior-editor`, assigned to a user with no prior access
at all, then granted `editor` on `asset_category:website-content` -- takes exactly two
`WriteRelationships` calls and **zero schema writes**:

```
$ zed permission check asset:homepage edit user:frank
false
$ zed relationship create role:senior-editor assignee user:frank
$ zed relationship create asset_category:website-content editor__direct role:senior-editor#assignee
$ zed permission check asset:homepage edit user:frank
true
```

No new `definition`, `relation`, or `permission`, no `WriteSchema` call -- a customer
inventing a role is pure data, indistinguishable in cost from assigning an existing one.
This is exactly the claim the Fidelity ratings section's reference case says was wrongly
assumed `blocked`; here it is neither `blocked` nor `heavy`.

**Rating: `effort`.** Every piece is one of this file's existing rules (the split, arrows, a
userset subject pointing at a relation); the only non-mechanical part is a modeling decision
the source has already made for you -- which permissions are role-grantable at all. It is
not rated `clean` outright only because the split still carries its own Class B decision
(the `__direct` suffix, recorded in `migration-plan.md`) -- the same one every split
relation already carries, nothing role-specific about it.

### The boundary: this does not cover an unbounded permission surface

The data-only shape above works only because every permission a role might ever grant was
already listed in the schema, ahead of time, as `[role#assignee] or ...`. This store does
not exercise the harder case -- a role that must grant a permission or resource shape not
anticipated when the schema was written (e.g. a customer defining a wholly new
resource-type-and-verb pair with no corresponding SpiceDB `relation`/`permission` at all).
That case would need a schema write per new permission *shape*, not per role, which is a
different question this store's evidence does not settle either way. Do not extend the
`effort` rating above to it. **Correction:** an earlier iteration named
`advanced-entitlements` as the next candidate to check this against; it does not settle it
either -- that store has no `role` type or customer-defined permission grant of any kind (it
is a fixed, two-tier subscription-quota model, see "Usage-quota entitlements: caveat vs.
materialized marker"), a different construct despite the similar-sounding "entitlements"
name. This harder shape remains open, with no corpus candidate currently earmarked for it.

### A second runtime-role shape: capability gated by intersection with the assignment, not unioned into the resource's own permission

Corpus-verified on `openfga/sample-stores/stores/role-assignments` (v1.56.0 / zed v0.31.1,
batch 4) -- OpenFGA's own README describes this store as "an alternative method for defining
custom roles" from `custom-roles`' own, and the SpiceDB translation is a genuinely different
composition of existing rules, not a restatement of the one above. `custom-roles`' shape
unions `[role#assignee]` directly into the *resource's own* permission
(`define asset_creator: [role#assignee] or owner`); granting a role a capability and granting
a user that capability directly are the same write, onto the same relation. This store's shape
instead holds the grantable capabilities on the `role` type itself, as bare, wildcard-flagged
relations (`role.can_view_project: [user:*]`, `role.can_edit_project: [user:*]` -- a `user:*`
tuple is written once per role to mean "this role, if assigned and reached, grants this
permission", not "everyone has this permission" in isolation), and mediates through a third
type, `role_assignment`, whose own permission is an **intersection** of "am I the assignee"
and "does the role I'm assigned to grant this":

```zed
definition role {
	relation can_view_project: user:*

	permission can_view_project__perm = can_view_project
}

definition role_assignment {
	relation assignee: user
	relation role: role

	permission can_view_project = (assignee & role->can_view_project__perm)
}
```

Every piece is a rule already on file, composed in a new arrangement: `role`'s capability
relations are pure type lists (`[user:*]`, no operator), so they stay bare relations, exactly
like every other wildcard-on-the-relation-being-translated case; the arrow into that bare
relation needs the `__perm` alias per "Point arrows at permissions, not relations" (the target
never split, since it carries no operator); and the whole permission is one source `and` node,
one parenthesized `&` group, per "Always fully parenthesize". No new construct, but the
resulting shape reads differently enough from `custom-roles`' union-based one that a
translator meeting it cold could plausibly reach for something new -- recorded here so it
does not need rediscovering. The consuming resource type (`project`) then arrows into
`role_assignment`'s intersection permission exactly as it would into any other permission
(`project.can_view = (role_assignment->can_view_project + organization->admin__perm)`, no
alias needed since the target already resolved to a permission). `PARITY OK` on the first
attempt; see `corpus-runs/README.md`'s `role-assignments` section for the live wildcard/
intersection probe with a subject outside the source fixture.

## Type-based tenancy (tenant-as-resource-type)

Corpus-verified on `openfga/sample-stores/stores/multitenant-rbac` (v1.56.0 / zed v0.31.1)
-- OpenFGA's own worked example of multi-tenant RBAC modeled *within one store* via a
tenant root type (`organization`), rather than via store-per-tenant. `blockers.md`'s
"3. Multi-store tenancy" entry is a Class A halt about the number of OpenFGA *stores*, not
about whether a model has a tenant concept -- this section is the mechanical mapping for
the case that entry's detection rule correctly does not fire on, and the halt should not be
applied to it. See `blockers.md`'s "Not a blocker: type-based (single-store) tenancy" for
the scope statement and the cross-tenant probe results; this section carries the mapping
detail and the full probe list that finding summarizes.

### The mapping needs no new construct

A tenant root type is an ordinary type with no tenancy-specific syntax:

```
# OpenFGA
type realm
  relations
      define maintainer: [user, team#captain]
      define triager: [team#captain] or maintainer

type ticket
  relations
      define realm: [realm]
      define closer: triager from realm
```
```zed
# SpiceDB
definition realm {
	relation maintainer: user | team#captain
	relation triager__direct: team#captain

	permission triager = (triager__direct + maintainer)
}

definition ticket {
	relation realm: realm

	permission closer = realm->triager
}
```

`realm` translates by the same `type` → `definition` rule as every other type. The
tenant edge on `ticket` (`define realm: [realm]`) is a pure type list, so
it stays a plain `relation` by the split rule's final bullet -- the same rule that already
covers `custom-roles`' `asset.category` and `abac-with-rebac`'s `document.organization`.
`triager`'s split (a `[team#captain]` type list fused with `or maintainer`) is the
"Runtime-defined roles" pattern from the previous section, composed with this one, not a
new interaction. Nothing about tenancy changes the split rule, the arrow rule, or the
runtime-role rule -- **the corpus confirmation here is that tenancy adds no rule of its
own**, corroborated end to end: `WriteSchema` accepted the converted schema unedited (no
compile step, no `use` flag), and the harness reached `PARITY OK` on the first attempt.

### Which of the three tenancy shapes this is, and why the other two do not apply

The design spec lists three ways a model can express tenancy once SpiceDB has no store
concept of its own: definition prefixes (`acme/document` -- legal SpiceDB syntax, each
`/`-segment independently satisfying the 3-character minimum), a tenant-as-resource-type,
and separate deployments. `multitenant-rbac` is unambiguously the second: `organization` is
already a first-class type in the *source* model, every tenant-scoped resource already
reaches it by relation, and the source is already one store. There is no store-count
decision to make -- the source was never split across stores to begin with -- so the
conversion carries the existing type over mechanically and neither of the other two shapes
is a candidate: prefixing every definition (`acme/document`, `acme/organization`, ...)
would be *pure regression*, turning a single shared schema that already scales to any
number of tenants via data into one that grows a new set of definitions per customer,
exactly the "schema bloat" cost `blockers.md`'s options table warns against; and separate
deployments would throw away isolation the schema already provides for no operational gain.
This is the one blocker option (`blockers.md`'s "One instance with a `tenant` resource
type") that a source model can arrive at the migration already having chosen -- when it
has, per this section, there is nothing left to decide.

### Cross-tenant isolation, probed directly

The harness's check-only comparison cannot show over-permissive tenancy: `store.fga.yaml`
has exactly one tenant (`acme`) and never asserts a negative against a second one, so a
converted schema that accidentally let every `organization` see every other
`organization`'s data would still pass every assertion the source oracle carries. This was
probed directly against a live v1.56.0 server, deliberately, because it is the highest-value
check available on this store and the one a green harness run cannot stand in for. A second
tenant (`beta`: its own admin, group, role, and document, wired the same way `acme`'s are)
was written alongside the converted `acme` data, and:

- **In-tenant checks reproduce the source oracle exactly**: all 12 `check:` assertions
  (`can_edit`/`can_view` for emily/anne/ian/francis on `document:readme`,
  `can_edit_billing` for francis/ian/anne/emily on `organization:acme`) match, and
  `LookupSubjects(document:readme, can_view, user)` returns exactly `{emily, anne, ian}` --
  matching the source's own `list_users` test, which the harness itself drops (see "What the
  harness could not see" in `corpus-runs/README.md`'s `multitenant-rbac` section).
- **Every cross-tenant `check` denies**: `acme`'s admin (`anne`) against `beta`'s document
  and billing permission; `beta`'s admin (`mallory`) against `acme`'s document and billing
  permission; `acme`'s document-manager (`emily`) against `beta`'s document; an
  `acme`-only group/role userset (`group:acme-finance#member`,
  `role:acme-billing-manager#assignee`) presented directly as the check subject against a
  `beta` permission -- all `false`.
- **`LookupResources` agrees**: `document` objects `user:anne` can `can_view` is exactly
  `{readme}`, never `secret`; `organization` objects `user:anne` can `can_edit_billing` on
  is exactly `{acme}`.

Tenant isolation holds, for every object that carries an explicit tenant edge, under both
the point-check and the exhaustive-set APIs.

### The caveat isolation-probing surfaced: not every type in this store carries a tenant edge

`role` and `group` have **no relation back to `organization`** -- in the *source* OpenFGA
model, not only in the converted one (`type role` and `type group` in `store.fga.yaml` have
no `organization`-typed relation at all). A role or group is scoped to a tenant only by
which organization's own relationships happen to reference it as an assignee source, never
by anything on the role/group object itself. Verified live: writing one relationship that
reuses an `acme` role inside `beta`'s `admin` union
(`organization:beta#admin@role:acme-admins#assignee`) instantly grants every `acme`
IT-admin `beta` admin, billing, and document access, with **no schema change** --

```
$ zed permission check organization:beta admin user:ian          # before
false
$ zed relationship create organization:beta admin role:acme-admins#assignee
$ zed permission check organization:beta admin user:ian          # after
true
$ zed permission check organization:beta can_edit_billing user:ian
true
$ zed permission check document:secret can_view user:ian
true
```

-- and removing that one relationship removes the access again. This is not a SpiceDB
regression: OpenFGA has the identical property, since `role`/`group` carry no tenant field
there either, so a source-side bug or naming collision that wires one tenant's role into
another's union would have leaked exactly the same way before migration. **The rule that
falls out of this: tenant isolation is a structural, schema-enforced property only for the
types that carry an explicit tenant relation (here, `document`); for a subject-aggregation
type with none (here, `role`, `group`), isolation is a write-path discipline invariant --
identical in both systems, and identically unenforced by either type system.**

This is a **Class C advisory finding** (`findings-report.md`'s taxonomy), not a blocker --
the conversion is faithful to the source either way, and *adding* a tenant edge to harden it
is a deliberate design change the source model never made, out of scope for a
parity-preserving migration. It still must be detected and recorded, not merely known: see
`blockers.md`'s "Class C: tenant-root reachability gap in subject-aggregation types" for the
mechanical detection algorithm (for each candidate type, walk its **own** belongs-to edges
forward -- resource toward root, never the reverse -- and flag it if that walk never arrives
back at the tenant root) and where it gets recorded
(`migration-plan.md`'s `Decisions` → `Tenancy`, alongside the tenancy shape decision itself).
That section is deliberately its own heading, not nested under "not a blocker," because a
reader who stops at "isolation holds" must not come away thinking it holds everywhere.

## Caveat parameter types and expression bodies

Corpus-verified on `openfga/sample-stores/stores/condition-data-types` (v1.56.0 / zed
v0.31.1), the store built to exercise every OpenFGA condition parameter type, checked both
via `zed validate` and against a live server (`WriteSchema`, `WriteRelationships`,
`CheckPermission`). Both languages embed CEL, but SpiceDB's caveat type system is its own
registration layer on top of CEL (`pkg/caveats/types`), not a passthrough -- two of the nine
types this store exercises need a body-level rewrite, not just the declaration-shape change
above.

### Type keyword mapping

| OpenFGA | SpiceDB keyword | Body compiles unchanged? |
|---|---|---|
| `string` | `string` | yes |
| `int` | `int` | yes |
| `uint` | `uint` | **no -- see below** |
| `double` | `double` | yes |
| `duration` | `duration` | yes |
| `timestamp` | `timestamp` | yes |
| `map<T>` | `map<T>` | yes (single type arg both sides -- both languages fix map keys to `string`) |
| `list<T>` | `list<T>` | yes |
| `ipaddress` | `ipaddress` | **no -- see below** |

`bool` (OpenFGA has it too, unexercised by this store) is presumed to follow `string`/`int`;
not corpus-verified, do not assume. SpiceDB's `bytes` and `any` types have no OpenFGA
source construct, so no mapping rule exists or is needed.

### `uint` is CEL `int` wearing a different name

Verified from source (`pkg/caveats/types/basic.go`): SpiceDB registers `uint` as
`RegisterBasicType(ts, "uint", cel.IntType, convertNumericType[uint64])` -- the CEL type
backing a `uint`-declared caveat parameter is `cel.IntType`, not CEL's native `uint`. The
converter enforces non-negativity on the *value* at context-bind time, but inside the
expression body the parameter behaves exactly like `int`. Consequence, verified:

```
caveat is_valid_uint(_uint uint) { _uint != 0u && _uint > 0u }
```
```
ERROR: found no matching overload for '_!=_' applied to '(int, uint)'
```

`0u` is a genuine CEL uint literal, and `_uint`'s bound type is `int` -- they don't unify.
The `uint(x)` conversion function fails the same way, for the same reason. **Rewrite rule:**
drop the `u` suffix from every integer literal compared against a `uint`-typed parameter,
and never call `uint(...)` in the body. `_uint != 0 && _uint > 0` compiles, and is verified
correct end to end on a live server.

### `ipaddress` has no in-expression literal constructor

OpenFGA lets a condition body construct a comparison value inline:
`_ipaddress != ipaddress("192.0.0.1")`. Unlike `duration` and `timestamp`, which map onto
CEL's *native* `DurationType`/`TimestampType` and inherit CEL's standard-library
constructors for free, SpiceDB's `ipaddress` is a custom opaque type
(`pkg/caveats/types/ipaddress.go`) with exactly one registered method, `.in_cidr(cidrString)`,
and **no constructor function at all**. Verified:

```
caveat is_valid_ipaddress(_ipaddress ipaddress) { _ipaddress != ipaddress("192.0.0.1") }
```
```
ERROR: undeclared reference to 'ipaddress' (in container '')
```

An `ipaddress`-typed value can be compared only against **another `ipaddress`-typed
variable** (`_a != _b` compiles) or tested for CIDR membership -- never against a bare
string or a constructed literal.

**Rewrite rule for a hardcoded single-address comparison** (the shape this store uses):
`.in_cidr()` against a `/32` (IPv4) or `/128` (IPv6) prefix is an exact address-equality
test, so `_x != ipaddress("<addr>")` becomes `!_x.in_cidr("<addr>/32")`. Verified correct on
a live server for both the matching and a non-matching address. This is `effort`, not
`clean`: the translator must know the address family to pick the right suffix, and the rule
only covers comparison against a literal baked into the condition -- comparing against a
*second runtime-supplied* address needs no trick at all, it's a second `ipaddress`
parameter and a plain `!=`.

### Everything else in the expression bodies carried over unchanged

Verified working with no rewrite: string methods (`.startsWith`, `.endsWith`, `.contains`,
`.matches`), the `in` operator on both `map<string>` and `list<string>`, indexing
(`_m["key"]`, `_l[0]`), string comparison operators (`>` on two CEL strings), and the
`.exists`/`.exists_one`/`.all` macros on a `list<T>`. `!= null` also compiles unchanged
against `duration`, `timestamp`, and `ipaddress` parameters.

### A missing map key is a hard evaluation error, not `caveated` or `false` -- confirmed identical in OpenFGA and SpiceDB

Corpus-verified on `openfga/sample-stores/stores/groups-resource-attributes`
(`allowed_statuses.exists(s, s == document_attributes["status"])`, a `map<string>` value
indexed by a literal key and tested for membership via a *different* `list<string>`
parameter's `exists` macro). The
composition itself needs no new rule -- `condition-data-types` already verified `in` and
indexing individually, each against its own variable in isolation; this store is the first
to compose the two, across two parameters, and it carries over unchanged, confirmed live for
all four of the store's checks.

What is new, and previously unverified: the given fact "checking with no context yields a
`caveated` result, not an error" holds only for a parameter **entirely absent** from the
supplied context. Once a container parameter is present in any form -- even an empty map --
indexing it by a key it does not contain is a hard evaluation error, not a graceful `false`
and not `caveated`:

```
$ zed permission check document:1 can_access user:anne --caveat-context '{"document_attributes":{}}'
{"level":"error", ... "rpc error: code = InvalidArgument desc = evaluation error for
caveat doc_viewer_condition: no such key: status"}
```

versus context that omits `document_attributes` altogether:

```
$ zed permission check document:1 can_access user:anne
{"level":"warn","fields":["document_attributes"], ... "missing fields in caveat context"}
caveated
```

This is not a SpiceDB-specific fragility the conversion introduces: OpenFGA's own condition
body, `document_attributes["status"] in allowed_statuses`, fails the identical way against the
identical input under `fga model test` -- `Checks 0/1 passing`, ``error=rpc error: code =
Code(2000) desc = failed to evaluate relationship condition: 'doc_viewer_condition' - failed
to evaluate condition expression: no such key: status`` -- verified directly against a scratch
`.fga.yaml` fixture, not assumed. The SpiceDB caveat above reaches the identical evaluation
error, verified live above, even though its body restructures the comparison as an `exists`
macro rather than carrying OpenFGA's `in` operator over unchanged. A
production caller whose resource record is missing the indexed attribute (a legacy row, a
partial fetch, an application bug) gets an RPC error out of `Check` in both systems alike,
not a boolean; whether that reads as fail-closed depends entirely on how the calling code
handles a `Check` error, per `spicedb-best-practices`' fail-safe guidance -- an unhandled
exception is not automatically a denial.

**This is a decision, not an automatic rewrite -- it changes semantics both ways, so it needs
its own detection and default, not just a fix sitting unattached.** A container-parameter
index guarded with `in` turns a missing key into a definite, silent `false`; left unguarded,
the identical input is a hard `Check` RPC error the caller must handle explicitly. Applying
the guard everywhere trades an error a fail-safe caller is already required to deny on for a
boolean that looks identical to a legitimate denial and is easier to let slip past review;
never applying it leaves a request-failing hazard live in a system whose own guidance
(`spicedb-best-practices`) is deny-on-error. Neither default is free, so the pack states one
and requires the choice to be recorded, the same as any other Class C advisory this file
raises.

**Detection.** Model-only, no code needed: scan every `caveat` body for a container parameter
(`map<T>`/`list<T>`) indexed by a literal key or position (`p["literal"]`, `p[0]`) with no
preceding `in`/bounds test on that same parameter in the same expression.

**Default: apply the guard, unless the call site is independently verified to already deny on
a `Check` RPC error.** Reasoning: an unguarded index's failure mode (a thrown error) is safe
*only* if every calling code path already treats a `Check` error as a denial -- the
`spicedb-best-practices` default, but not something this pack's own tooling can verify from
the schema alone (it is a call-site property, per `pack-contract.md` item 7). Where that
property is confirmed, leaving the literal, unguarded translation is fine and matches the
source exactly (this store's own `schema.zed` does, since its harness ritual has no call site
to verify against). Where it is not confirmed -- the common case during a migration, before
every call site has been individually audited -- apply the guard
(`"status" in document_attributes && allowed_statuses.exists(s, s ==
document_attributes["status"])`, the same defensive idiom `condition-data-types`' own
`is_valid_map_string` already uses) so a
missing key resolves to an auditable `false` instead of depending on unverified call-site
error handling. Verified live: the guarded form returns a clean `false` for the missing-key
case above, with every other check's result unchanged -- the rewrite is free on the happy
path either way.

**Record:** the chosen behavior (guarded vs. left unguarded) per caveat parameter that indexes
a container by a literal key, under `migration-plan.md`'s `Decisions` → `Per-blocker
resolutions`, alongside which call sites (if any) were verified to already deny on a `Check`
RPC error -- the fact the default above turns on.

### Caveat parameter names are not identifiers anyone renames

OpenFGA idiomatically prefixes condition parameters with `_` (`_string`, `_uint`, ...). The
codegen rule "never emit a `_`-prefixed identifier" earlier in this file was verified
against *relation and definition* names only; it does not extend to caveat parameter names.
`caveat c(_uint uint) { _uint > 0 }` compiles and deploys unchanged, verified -- leave them
as-is.

There is a sharper reason than "it compiles," though: unlike a relation or permission name,
**a caveat parameter name has no rename-absorption layer.** `migration-map.json` /
`IdMap.apply` rewrites relation and permission names on every assertion it touches, so a
rename there is transparent to everything downstream. Caveat context is a raw JSON object
(`[cond:{"_uint":1}]` in a relationship string, `with {"_uint":1}` in a check), and the
harness compares that JSON verbatim between the OpenFGA and SpiceDB sides -- there is no
field in `migration-map.json` for a parameter-name translation, and nothing rewrites context
keys. Renaming a caveat parameter is therefore a coupled schema *and* data *and* call-site
change with no single source of truth to drive it, unlike every other rename in this pack.
Prefer never doing it.

## Temporal access: caveat vs. native expiration

Corpus-verified on `openfga/sample-stores/stores/temporal-access` (v1.56.0 / zed v0.31.1),
both via `zed validate` / the harness and end to end against a live server. This is the
encoding-choice the "Deliberately not written yet" section flagged before this store was
touched: OpenFGA's `condition temporal_access(grant_time: timestamp, grant_duration:
duration, current_time: timestamp) { current_time < grant_time + grant_duration }` has two
faithful-looking SpiceDB targets, and they are not interchangeable.

**This is a Class B gate decision (`findings-report.md`'s taxonomy), not a silent default.**
Both encodings are mechanical once chosen -- pack-contract item 3 requires that where more
than one SpiceDB encoding exists for one source construct, the mapping present the choice
with its tradeoffs rather than pick one unilaterally. An earlier version of this section
recommended the caveat form unconditionally; that was wrong in exactly the way item 3 warns
against, and the mistake was a category error, not a close call: the evidence cited for it
(no caller-suppliable "now" in the native-expiration API) is a fact about what *this
harness* can verify with static, pre-recorded assertions, and says nothing about which
encoding a production system should run. **Corrected recommendation: default to native
expiration; the caveat form is the fully-supported alternative for the one case that
specifically needs it.** See "The gate decision: detection, options, and where it's
recorded" below for the decision itself, and "Why the harness only verifies the caveat
form" for why that fact must not be read as a runtime recommendation.

The construct-table row above and this store's `corpus-runs/temporal-access/schema.zed` use
the caveat form -- **because it is the only one this pack's own tooling can verify**, not
because it is the recommended default; `corpus-runs/temporal-access/schema-native-expiration.zed`
is the recommended default path, kept alongside as a fully-verified alternative that the
harness ritual cannot itself validate end to end (see below for what was verified instead,
and how).

### The mechanical caveat translation needs no new rule at all

```zed
caveat temporal_access(grant_time timestamp, grant_duration duration, current_time timestamp) {
	current_time - grant_time < grant_duration
}

definition document {
	relation viewer: user | user with temporal_access
}
```

Every piece was already covered before this store: `timestamp` and `duration` both carry
their body over unchanged per "Caveat parameter types and expression bodies," and `[user,
user with temporal_access]` is a bare type list (a plain type alongside one `T with cond`
clause) -- "only a type list" per the split rule, so it stays a plain `relation`, exactly as
`condition-data-types`' `is_valid` already confirmed for a pure union of `with`-clauses. This
store reached `PARITY OK` on the **first attempt**, needing zero new mapping rules -- the only
thing this store actually forces is the encoding *choice* below, not the syntax.

### What differs semantically: who supplies "now"

Both forms store the same computed value: the caveat's bound context (`grant_time`,
`grant_duration`) and native expiration's `optional_expires_at` both amount to `grant_time +
grant_duration`, computed once. They diverge on **who supplies "now"** -- this is the real,
product-facing difference the gate decision below turns on, independent of anything about
this pack's own verification tooling:

- **Caveat.** `current_time` is an ordinary parameter. The caller supplies it as check
  context, exactly the way this store's own `tests:` blocks do (`context: {current_time:
  "2023-01-01T00:10:00Z"}`) -- it can be any value: the real clock, a fixed historical
  instant, or a future "as of" date for an audit query. OpenFGA's own model already
  decouples "when granted" (bound at write time) from "as of when evaluated" (supplied at
  check time); the caveat translation preserves that decoupling exactly.
- **Native expiration.** There is no caller-suppliable "now," anywhere in the API. Verified
  from source (SpiceDB v1.56.0): `internal/datastore/memdb/memdb.go`'s `SnapshotReader`
  always filters against `time.Now()` -- the real wall clock at the moment of the read --
  regardless of which revision/ZedToken was requested; `pkg/development/assertions.go`'s
  `RunCheck` (what both `zed validate` and the developer API use to run an assertion) takes
  only a `CaveatContext`, no time parameter at all. Verified live, on this toolchain:
  `zed permission check --help` has no time-override flag (`--caveat-context` is
  caveat-only; `--consistency-at-exactly <zedtoken>` pins the *data* snapshot, not the
  wall-clock instant expiration is compared against -- confirmed by source, not just
  absence of a flag). **This is not a memdb quirk**: every backing datastore (postgres,
  mysql, crdb, spanner) filters expiration with its own `now()` SQL function the same way.

**The genuine product-facing consequence, real regardless of any tooling:** this store's own
three time-varying assertions -- `viewer` true 10 minutes into a 1-hour grant, false 2 hours
into that same grant, false 9 seconds into an unrelated 5-second grant -- ask "was this valid
at caller-chosen instant X," which only the caveat form can answer deterministically. Native
expiration can only answer "is this valid **right now**," where "now" is whatever the real
clock reads when the check runs. A production system that needs to evaluate access **as of**
a past or future instant -- an audit query, a "what could this user see last Tuesday"
report, a scheduled grant that should already be active before its writer's clock catches up
-- genuinely cannot get that from native expiration, in any configuration. This is a real
scope difference between the two encodings and belongs in the gate decision below; it is not
the harness-verifiability point that follows.

### Why the harness only verifies the caveat form

Separately from the semantic difference above -- and **not** a reason to prefer either
encoding in production, only a fact about this pack's own tooling -- native-expiration
semantics are not verifiable by this harness, or by `zed validate`, for any assertion whose
expected answer depends on an offset from a fixed instant rather than from the real clock.
Reproducing this store's exact assertions with native expiration would mean either sleeping
in real time between the write and the check (flaky, and exactly the wall-clock dependency
this pack's own test-conversion guidance says to avoid) or asserting only against dates
safely in the past or future relative to whenever the suite happens to run -- which tests a
different claim than "9 seconds into a 5-second grant." Verified structurally: the harness's
own comparator (`spicedb_val.py`'s `" with {...}"` suffix, which only ever feeds
`CaveatContext`) has no field anywhere in the validation-YAML grammar for a per-assertion "as
of" time. **This is why `corpus-runs/temporal-access/schema.zed` uses the caveat form and
`validation.yaml` stays unchanged by this correction** -- the caveat form is what this
pack's own harness ritual can verify end to end, which is a statement about the harness, not
a claim that the caveat form is the better production choice. The native-expiration
alternative was instead verified directly against a live server (see "What happens to an
expired relationship before GC runs" and "`use expiration`..." below), which is how a
recommendation that the harness cannot itself certify still gets verified rather than
merely asserted.

One thing that does **not** differ between the two forms: neither has a lower bound. OpenFGA's
own condition never checks `current_time >= grant_time`, so a check evaluated *before* the
grant's nominal start already returns `true` in the source model -- both the caveat
translation (which carries the expression over verbatim) and native expiration (which encodes
only a single upper-bound timestamp, by construction) inherit this quirk identically. It is a
property of the source model, not a point of difference between the two SpiceDB encodings.

### What happens to an expired relationship before GC runs

Verified live: wrote `document:3#viewer@user:carol[expiration:<now+6s>]`; immediately after,
`zed permission check` returned `true` and `zed relationship read` listed it; 8 seconds later
(comfortably inside SpiceDB's GC interval, so nothing had been physically reclaimed yet),
`check` returned `false` and `zed relationship read` no longer listed it at all -- **gone from
reads before any GC cycle could have run.** This matches source: `internal/datastore/memdb
/readonly.go`'s `newMemdbTupleIterator` / `newSubjectSortedIterator` drop any relationship
whose `OptionalExpiration.Before(now)` unless the caller opts out via `SkipExpiration`, and
`internal/services/v1/relationships.go`'s `ReadRelationships` handler sets
`SkipExpiration: !traits.AllowsExpiration` -- i.e. filtering is *always on* for any relation
declared `with expiration`, on every read path (`Check`, `ReadRelationships`,
`LookupResources`, `LookupSubjects`), with no request-level flag to see the not-yet-collected
row. GC reclaims storage; it has no bearing on visibility, which is already gone the instant
the timestamp passes.

### `use expiration` is one of the few `use` flags both tools accept

Verified live on this store: `use expiration` + `with expiration` deploys directly via
`WriteSchema` (no compile step), and dropping the `use` line reproduces the documented trap
exactly (`could not lookup caveat 'expiration' for relation 'viewer': caveat with name
'expiration' not found`). Unlike `partial`/`import` (`modular`'s findings), `expiration` is
recognized by **both** the server's `use`-flag enumeration and zed v0.31.1's own local parser
(`zed validate` on a `use expiration` schema returns `Success!` directly, no compile step
needed) -- confirmed directly per this store's task brief rather than assumed.

### The gate decision: detection, options, and where it's recorded

**Corrected per owner review: this is a Class B gate decision, presented to the user with
both options and their tradeoffs, defaulting to native expiration.** An earlier version of
this section shipped caveat as an unconditional recommendation; that violated
pack-contract item 3 (present the choice, don't pick silently) and rested its reasoning on a
tooling limitation of this harness rather than on what serves a production system best. Both
mistakes are corrected below.

**Detection.** Model-only, no code needed:

1. Cheap pre-filter -- scan every `condition`/`caveat` body for a comparison between a
   parameter supplied at check time and an expression that sums (or otherwise derives from)
   two parameters bound at write time (`current_time < grant_time + grant_duration`, or
   equivalent). `grep -n 'condition.*(' <model>` narrows the search; confirm by reading each
   body, since the parameter names are not guaranteed to say "time" or "duration."
2. Cross-reference the store's own fixture data: if the flagged parameter is supplied
   per-check (a `context:` value that differs across the store's own `tests:`/assertion
   entries) rather than bound once at write time alongside the others, that confirms a
   temporal-access shape rather than an unrelated arithmetic caveat.
3. Note whether the condition enforces a lower bound (`current_time >= grant_time` or
   equivalent). Its absence doesn't change which option to offer, but both options inherit
   the gap identically, and it is worth surfacing in the same decision record.

**Options:**

| Option | Cost |
|---|---|
| **Native expiration** (`use expiration`, `with expiration`, `optional_expires_at`) -- **recommended default** | Rated `effort`. Zero per-check CEL evaluation (a caveat-bound relationship is re-evaluated on every check, forever); the datastore physically reclaims expired rows via GC; enforced identically on all four read paths (`Check`, `ReadRelationships`, `LookupResources`, `LookupSubjects`), verified live. Not hard syntactically -- one `use` line, one `with expiration`, one `optional_expires_at`, and the translator collapses the source's two stored parameters into one absolute timestamp computed once at conversion time. The cost is the scope narrowing below, plus: this pack's own harness cannot verify the result end to end (see "Why the harness only verifies the caveat form"), so verification has to happen against a live server instead, as it did here. |
| **Caveat** -- the alternative for a source model whose call sites genuinely need "as of a time" | Rated `clean` -- no new construct, first-attempt `PARITY OK`. Preserves the source's check-time flexibility exactly (an ordinary parameter the caller supplies at check time, matching how this store's own `tests:` blocks already work), and is the only form this pack's own tooling verifies deterministically. Cost: a caveat-bound relationship is evaluated on every check for as long as it exists, and the datastore never reclaims it on its own -- an expired grant is a permanent write-then-explicit-delete obligation, not a GC-handled one. |

**The scope narrowing that makes this a gate decision, not a default:** native expiration
can only answer "is this valid right now" -- a production system that needs to evaluate
access **as of** a past or future instant (an audit query, a "what could this user see last
Tuesday" report, a scheduled grant that should already be active before its writer's clock
catches up) cannot get that from native expiration in any configuration. Choosing it without
checking whether any call site genuinely needs an "as of" query silently deletes a capability
the source system had -- that is exactly why this is a gate decision the user resolves, not a
rule this pack applies automatically to every temporal-access condition it finds.

**Record:** the chosen option per flagged condition, under `migration-plan.md`'s `Decisions`
→ `Per-blocker resolutions`, alongside the evidence for the choice (does any call site need
an "as of" instant, not merely "is this valid now") and, if native expiration is chosen,
that the translator computed one absolute timestamp from the source's two stored parameters
at conversion time -- a one-way step worth recording since the source's separate
`grant_time`/`grant_duration` values are not reconstructable from it afterward.

**This corpus store's own artifacts stay as previously committed, deliberately**:
`corpus-runs/temporal-access/schema.zed` (caveat form) remains the only artifact this pack's
harness ritual validates -- `validation.yaml` and `migration-map.json` are unchanged by this
correction, since the caveat form is still the only one the harness can certify, which is a
statement about the harness (see above), not a re-endorsement of caveat as the production
default. `corpus-runs/temporal-access/schema-native-expiration.zed` now documents the
**recommended** path rather than a fallback; it was verified the way any harness-unreachable
recommendation must be -- directly against a live server (see "What happens to an expired
relationship before GC runs" and "`use expiration`..." above) -- not by a green harness run,
since no green harness run is possible for this option.

### The gate decision applies unchanged when the condition sits behind arrows, corpus-confirmed through two nested hops (`superadmin`), then three (`modeling-guide`)

`openfga/sample-stores/stores/superadmin` carries the identical `current_time < grant_time +
grant_duration` shape (its own condition is named `non_expired_time_grant`, renamed and with
its parameters reordered, but semantically the same as `temporal_access`) applied to
`organization.helpdesk_member: [employee with non_expired_time_grant]` -- but nothing checks
`helpdesk_member` directly. `task.viewer` reaches it through **two** arrow hops
(`task->project`'s `project.viewer`, itself `project->organization`'s
`organization.helpdesk_member__perm`). At the time this was written, every other
caveat-bearing corpus store put its condition at most one hop away
(`condition-data-types`/`temporal-access`/`advanced-entitlements`: zero hops, the condition on
the checked relation itself; `ip-based-access`: one hop, `document.can_view`'s
`organization->ip_based_access_policy__perm`) -- two later-added caveat stores round out the
one-hop tier without changing it: `banking`'s `account.can_make_bank_transfer` arrows once to
`bank->transfer_limit_policy__perm`, and `groups-resource-attributes`'s `document.can_access`
arrows once to `organization->can_access_docs__perm`. Both the gate's two encodings were
verified through the full two-hop chain, live:

- **Caveat** (`corpus-runs/superadmin/schema.zed`): `zed validate` and a live server both
  resolve `task:create-example#viewer@employee:john with
  {"current_time":"2024-01-01T00:10:00Z"}` correctly (`true`) and its `editor` counterpart
  correctly (`false`), matching `fga model test`'s own `Checks 8/8 passing` -- the caveat
  context supplied at the top of the walk reaches the caveat bound two arrow-hops down with
  no special handling.
- **Native expiration** (`corpus-runs/superadmin/schema-native-expiration.zed`, the
  recommended default per the gate below, deployed to a separate keyspace): a
  `helpdesk_member` relationship written with `--expiration-time` 6s in the future checked
  `true` through the same two-hop `task.viewer` walk immediately after write, and 8 seconds
  later (inside SpiceDB's GC interval) checked `false` and had vanished from `zed
  relationship read`, `LookupResources`, and `LookupSubjects` alike -- the same
  gone-from-every-read-path-independent-of-GC mechanism "What happens to an expired
  relationship before GC runs" documents, now confirmed to survive arrow indirection rather
  than only a direct check.

No new rule was needed for either form -- arrows and the caveat/expiration mechanism were
already independently established, and a caveat or an expiration timestamp is evaluated once
at the relationship that carries it regardless of how many permission-graph hops a check
walks to reach it. This is recorded as a confirmation, not a new construct.

**Gate decision, applied unchanged, not re-derived:** this store forces no new choice --
native expiration is this pack's recommended default per the existing gate above, and
`corpus-runs/superadmin/schema-native-expiration.zed` is that recommendation, verified live
exactly as described. `corpus-runs/superadmin/schema.zed` stays on the caveat form for the
identical reason `temporal-access`'s does: this store's own source assertions are themselves
fixed-instant (`current_time: "2024-01-01T00:10:00Z"`), not real-clock-relative, so it is the
only form this pack's harness ritual can verify end to end. See `corpus-runs/README.md`'s
`superadmin` section for the per-store record.

**A third hop (`modeling-guide`, batch 4).** OpenFGA's own modeling-guide walkthrough
(`step-10-fine-grained-api-access.fga.yaml`, converted into this pack as the `modeling-guide`
corpus store) reuses this exact gate a second time, `condition time_based_grant(current_time,
grant_time, grant_duration)` applied to `system.super_admin: [user with time_based_grant]` --
renamed parameters, identical shape, the same reapplication `superadmin` already was. What is
new: the permission actually checked, `document.can_edit` (and `can_view`, which falls back to
it), sits **three** arrow hops from the caveated relation, one deeper than `superadmin`'s two
-- `document->folder` (`parent->can_edit`), `folder->organization`
(`organization->can_edit_documents`), then `organization->system`
(`system->super_admin__perm`), with two same-type permission references (`can_edit_documents`
unioning `admin`; `admin` unioning `system->super_admin__perm`) adding no further hops between
the second and third crossings. Verified live and via the harness exactly as `superadmin` was:
`zed validate` and a live v1.56.0 server both resolve
`document:welcome#can_edit@user:sam with {"current_time":"2024-07-21T00:00:09Z"}` to `true` and
the same check nine seconds into the next day to `false`, matching `fga model test`'s own
`Checks 30/30 passing`. No new rule was needed here either, for the identical reason
`superadmin`'s confirmation needed none: hop count is not a parameter of the caveat/arrow
mechanism. Recorded as a second confirmation extending the hop-count no prior store had
tested, not a new construct. See `corpus-runs/README.md`'s `modeling-guide` section for the
per-store record.

## IP-based access: caveat vs. materialized marker

Corpus-verified on `openfga/sample-stores/stores/ip-based-access` (v1.56.0 / zed v0.31.1),
both via `zed validate` / the harness and end to end against a live server. This is the
encoding-choice the "Deliberately not written yet" section named this store to check before
it was touched, and it is where `pack-contract.md` item 3 was violated once already this
loop (an earlier iteration silently defaulted a different encoding choice, `temporal-access`,
without presenting the tradeoff -- see that section's own "Correction" note). This section
does not repeat that mistake.

### The mechanical caveat translation needed one new construct-table row, nothing else

```zed
caveat in_company_network(user_ip ipaddress, cidr string) {
	cidr != "" && user_ip.in_cidr(cidr)
}

definition organization {
	relation member: user
	relation ip_based_access_policy: organization#member with in_company_network

	permission ip_based_access_policy__perm = ip_based_access_policy
}

definition document {
	relation organization: organization
	relation viewer: user

	permission can_view = (viewer & organization->ip_based_access_policy__perm)
}
```

Three things line up with rules already in this file (the split rule's final bullet, the
`__perm` arrow-alias rule, "Always fully parenthesize"), and one is genuinely new, now
recorded in the construct table above:

- **`[organization#member with in_company_network]` is a caveat on a *userset*-typed subject
  reference, not a bare type.** Every prior corpus caveat (`condition-data-types`,
  `temporal-access`, `abac-with-rebac`'s deliberately-avoided case) attached `with cond` to a
  bare `user`. Nothing in "Caveat parameter types and expression bodies" or the split rule
  excluded a userset target, and none needed to: verified live, a check that resolves through
  `ip_based_access_policy@organization#member` requires **two** things to both hold --
  membership in the userset (`user:anne` ∈ `organization:acme#member`, an ordinary,
  uncaveated hop) *and* the caveat bound to the edge that names that userset as its subject
  (`in_company_network`, evaluated with the bound `cidr` and the caller-supplied `user_ip`).
  Both layers resolved correctly with no special handling, for both the matching and the
  non-matching address, and via `LookupResources` as well as `check`.
- The type list has exactly one entry and no operator, so `ip_based_access_policy` stays a
  plain `relation` -- the split rule's final bullet, already covered, now confirmed against a
  userset-with-condition entry rather than only a bare-type-with-condition one.
- `ip_based_access_policy from organization` reverses into `organization->ip_based_access_policy__perm`
  the same as any other arrow onto a bare relation -- the `__perm` alias rule, unmodified.
- `can_view: viewer and ip_based_access_policy from organization` has no type list, so it
  stays a plain `permission`, and its one `and` node becomes one parenthesized `&` group.
  This is the corpus's **first real confirmation of SpiceDB intersection at all** -- see
  "Always fully parenthesize"'s new note. No new rule; the existing "one source node, one
  parenthesized group" rule already covered it, it had simply never been forced by a real
  store before this one.

This store reached `PARITY OK` on the **first attempt**. The only thing it actually forces is
the encoding choice below, not the syntax -- the same shape `temporal-access` had, with the
opposite resolution.

### What the benchmark's three encodings mean for a per-request-supplied attribute

The task that selected this store supplied an independent, large-scale benchmark of three
ways to encode one attribute-gated `LookupResources` path -- caveat context, a wildcard
marker checked via intersection, and the attribute encoded directly in the relation name --
with a measured ordering (relation-name fastest, caveat context second, wildcard-marker-plus-
intersection slowest, by a wide margin) that is cited here for its **ordering only**, not
reproduced or requoted as a number this store's own tiny data set could ever measure. Two of
the three do not apply to this store's actual construct **in their literal form**, and
knowing why is itself part of the finding, not a shortcut past it:

- **Relation-name encoding is unavailable here.** It requires a small, enumerable,
  schema-known vocabulary of values (`blockers.md`'s transitive-wildcard discussion and the
  benchmark's own framing both assume this). This store's `cidr` is an arbitrary string an
  organization admin configures at write time -- not a fixed company-wide vocabulary a schema
  author could enumerate as named relations (`viewer_from_hq`, `viewer_from_vpn`, ...) without
  a schema change every time an organization's network topology changes. If a product's real
  network policy genuinely *is* a small fixed set of named zones known at schema-authoring
  time, this option becomes viable and should be evaluated against the same cited ordering --
  that is a product fact this store's own data does not supply.
- **The literal `user:*` wildcard-marker form is unavailable here too, for a different
  reason.** A wildcard subject means "any subject of this type" -- there is no `ipaddress:*`
  or equivalent, because an IP address is a per-request **value** supplied as caveat context,
  never a **subject type** SpiceDB's type system has any notion of. The three-way benchmark's
  middle option, read literally, has no syntactic target in this store's schema at all.

What *does* generalize, and is the actual second option this store admits: the **general
architecture** the wildcard-marker row represents -- pre-materialize the gated fact as a
plain, uncaveated relationship, and check it via intersection instead of evaluating CEL on
every call -- has a legitimate instantiation here even though the literal wildcard syntax
does not apply. Built and verified live, alongside the caveat form, on the same store:

```zed
definition organization {
	relation member: user
	relation ip_based_access_policy: organization#member with in_company_network
	relation network_verified_member: user

	permission ip_based_access_policy__perm = ip_based_access_policy
	permission network_verified_member__perm = network_verified_member
}

definition document {
	relation organization: organization
	relation viewer: user

	permission can_view = (viewer & organization->ip_based_access_policy__perm)
	permission can_view_verified = (viewer & organization->network_verified_member__perm)
}
```

`network_verified_member` is a plain relation the application writes once it has verified,
by whatever means (login-time IP check, VPN certificate, device posture), that a member's
current session originates from the allowed network -- and removes when that verification
should no longer hold. Verified live: `can_view_verified` returns `true` for a verified
member with **no caveat context supplied at all** (`zed permission check document:1
can_view_verified user:anne` -- no `--caveat-context` flag, unlike `can_view`, which returns
`caveated` without one), `false` for an unverified one, and `LookupResources` against it
returns the correct resource set with zero caveat context and zero per-candidate CEL
evaluation -- structurally the same "plain graph walk" every non-ABAC permission in this
pack's corpus already gets.

### Why the naive "materialize it for speed" argument does not hold here

This is the trap the task brief named directly, and it applies with full force to the
alternative just built: `can_view_verified` is architecturally the same shape the cited
benchmark's middle row measures -- a pre-materialized boolean-like relation, gated via
intersection, with zero per-check CEL. The benchmark's own measured ordering puts that
pattern **behind**, not ahead of, plain caveat context for `LookupResources` -- AuthZed's own
documented recommendation to prefer a wildcard/marker pattern over caveats is, per the cited
ordering, about **static-data hygiene** (do not let stale caveat context on infrequently-
changing data drift out of sync with reality), **not** about latency, and reaching for it here
on a latency argument would be reaching for the wrong justification. Two real costs attach to
`can_view_verified` regardless of the speed question:

- **It answers a different, weaker question than the source model asks.** OpenFGA's own
  condition compares the **current** request's IP against the policy, every single check.
  `can_view_verified` answers "was this member's network verified as of the last write to
  `network_verified_member`" -- a session-hijacking or IP-roaming scenario after that write is
  invisible to it. This is a real capability narrowing, the same category of cost
  `temporal-access`'s native-expiration alternative carried (there: "as of an arbitrary time"
  narrows to "right now"; here: "this literal request" narrows to "as of last
  (re)verification") -- but pointed the opposite direction: for a security-perimeter control
  that exists specifically to check the *current* request, narrowing to a periodically-
  refreshed marker is a materially different guarantee, not a purely operational tradeoff.
- **It is a new, ongoing sync obligation with no source-model analog.** OpenFGA's model has no
  notion of "verified session" at all -- every check re-evaluates `user_ip` fresh. Adopting
  the marker means inventing a reverification cadence and a revocation path (logout, IP
  change, TTL) that does not exist in the source system today, exactly the kind of obligation
  `pack-contract.md` item 6 requires surfacing at the gate rather than discovering later.

### Materialize adds a real, unresolved tension on top of this

Separately from `LookupResources` latency: `can_view`, the caveat form, depends on a caveat
and is therefore -- per the given fact that a materialized permission path supports neither
caveats nor wildcards -- definitionally ineligible for SpiceDB Materialize. A customer that
wants both real-time, per-request IP enforcement **and** Materialize-scale `LookupResources`
on the same permission cannot have both on `can_view` as written. `can_view_verified` removes
the caveat, but this store's evidence does not establish that an intersection-shaped
permission is itself Materialize-eligible (the given fact rules out caveats and wildcards
specifically, and says nothing about intersection); no Materialize environment was available
to test this directly, so it is recorded as an **open question**, not a settled second-option
recommendation. The honest statement: a customer needing both properties on one permission
faces a real architectural tension this pack does not yet have a full answer for.

### The gate decision: detection, options, and where it's recorded

**Class B gate decision, presented with both options and their tradeoffs -- not a silent
default, and not resolved the same direction as `temporal-access`'s gate.** The two stores
share a shape (a caveat comparing a write-time-bound value against something supplied
per-check) and reach opposite recommendations, because the thing compared per-check differs
in kind: `temporal-access`'s "now" is a wall-clock instant every production caller means the
same way, and SpiceDB has a first-class structural feature (`with expiration`) purpose-built
for exactly that comparison. This store's "current request's source IP" has no structural
SpiceDB equivalent, and the alternative that exists (a periodically-refreshed marker) trades
away the literal per-request check that is this construct's entire reason to exist, for a
materialize-for-speed argument the cited benchmark itself undercuts.

**Detection.** Model-only, no code needed:

1. Cheap pre-filter -- scan every `condition`/`caveat` body for a parameter whose type is
   `ipaddress`, or more generally any parameter that a store's own fixtures supply with a
   **different value on every check** against the same write-time-bound relationship (contrast
   with `temporal-access`'s detection rule, which looks for a parameter derived from two
   others summed at write time -- this rule looks for a parameter that varies per call with no
   corresponding write-time counterpart at all).
2. Confirm the write-time-bound half of the comparison is relatively static administrative
   data (a CIDR range, an allow-listed value set) rather than something that itself changes
   as often as the per-check value -- if both sides vary per-request, this is not the shape
   this section covers.
3. Note whether the compared value set is small and enumerable at schema-authoring time. If it
   is, also evaluate relation-name encoding per the cited benchmark ordering; this store's own
   `cidr` is not enumerable, so that branch is ruled out here, not merely unconsidered.

**Options:**

| Option | Cost |
|---|---|
| **Caveat context -- recommended default** | Rated `clean` -- no new construct beyond the userset-with-condition row above, first-attempt `PARITY OK`. Preserves the source's per-request semantics exactly (the caller supplies the current value as check context, matching how this store's own `tests:` blocks already work), and is the only form this pack's own tooling verifies deterministically end to end, including `LookupResources`. Cost: per-check CEL evaluation on every call, forever, and -- per the cited benchmark's ordering -- a real `LookupResources` latency cost at scale that a low-cardinality relation-name encoding could beat if this store's own value happened to be enumerable (it is not). Also definitionally ineligible for a materialized SpiceDB permission path. |
| **Materialized "verified" marker -- the alternative, only where a periodically-refreshed check is an accepted tradeoff** | Rated `effort` -- syntactically simple (one plain relation, one `__perm` alias, one intersection), but a conscious, recordable decision to check a weaker, staler property than the source model checks, plus a new reverification/revocation sync obligation with no source-model analog. Verified live to need zero caveat context and zero per-check CEL, matching the "plain graph walk" cost of any non-ABAC permission -- but the cited benchmark's own ordering shows this *shape* (marker + intersection) measured **slower**, not faster, than the caveat form for `LookupResources`, so the usual "materialize for speed" justification does not apply on the evidence available; the honest reason to choose this option is accepting the weaker guarantee for an application reason (e.g. Materialize-eligibility, once that is separately confirmed), not latency. |

**Record:** the chosen option per flagged condition, under `migration-plan.md`'s `Decisions`
→ `Per-blocker resolutions`, alongside: which call sites (if any) are security-perimeter
controls that must see the literal current request (a strong signal toward caveat); whether
the compared value set is enumerable (a signal toward re-evaluating relation-name encoding
instead of either option here); and, if the marker is chosen, the reverification cadence and
revocation path being invented, since neither exists in the source model.

**This corpus store's own artifacts:** `corpus-runs/ip-based-access/schema.zed` is the caveat
form -- both the recommended default **and** the only form this pack's harness ritual
validates, so unlike `temporal-access` there is no split between "what the harness verifies"
and "what is recommended." The materialized-marker alternative was verified directly against
a live server (`can_view_verified` / `network_verified_member`, see above) but is not
committed as a separate `corpus-runs` artifact, since -- unlike `temporal-access`'s
native-expiration alternative -- it is not this section's recommended path; the schema
fragment above is reproducible directly from this section's own text.

## Usage-quota entitlements: caveat vs. materialized marker

Corpus-verified on `openfga/sample-stores/stores/advanced-entitlements` (v1.56.0 / zed
v0.31.1), both via `zed validate` / the harness and end to end against a live server. This
store's own `README.md` cites Notion's pricing tiers as its model, and it is the corpus
candidate the task brief that selected it named directly: OpenFGA's `feature.has_feature:
[plan#subscriber, plan#subscriber with is_below_collaborator_limit, plan#subscriber with
is_below_row_sync_limit, plan#subscriber with is_below_page_history_days_limit]` is the
shape most likely to expose a genuine, hard SpiceDB limitation -- a caveat that would need
to compare two independently-*stored* facts (`used < quota`, both persisted) rather than one
stored value against one value the caller supplies. **This store turns out not to be that
case** -- determined from its own model and tuples, not assumed, and confirmed live -- but
working out *why* it isn't, precisely, is what settles the boundary for the harder shape
that remains open.

### What the store's own tuples prove about which side is "stored" and which is "supplied"

`store.fga.yaml`'s `tuples:` block binds only the *quota* half of each condition at write
time -- `is_below_collaborator_limit`'s `collaborator_limit: 10` (free) / `100` (pro),
`is_below_row_sync_limit`'s `row_sync_limit: 100` / `20000`,
`is_below_page_history_days_limit`'s `page_history_days_limit: 7` / `30`. The *usage* half
-- `collaborator_count`, `row_sync_count`, `page_history_days_count` -- never once appears
in `tuples:`, anywhere in the file; it appears only in `tests:`' own `check.context` blocks,
a different value on every check (`page_history_days_count: 10` in one assertion,
`page_history_days_count: 1` in another, against the identical relationship). That is the
store's own model answering the question this section exists to settle: OpenFGA never
persists "used" either -- it is supplied by the caller at query time in the *source* system,
before any SpiceDB migration is even in scope. The caveat translation carries this over
verbatim, with the identical split `temporal-access` (`grant_time`/`grant_duration` bound,
`current_time` supplied) and `ip-based-access` (`cidr` bound, `user_ip` supplied) already
established:

```zed
caveat is_below_collaborator_limit(collaborator_count int, collaborator_limit int) {
	!(collaborator_count > collaborator_limit)
}

definition feature {
	relation has_feature: plan#subscriber |
		plan#subscriber with is_below_collaborator_limit |
		plan#subscriber with is_below_row_sync_limit |
		plan#subscriber with is_below_page_history_days_limit
}
```

No relation in this store's entire schema is ever split (`organization.member`,
`plan.subscriber`, and `feature.has_feature` are each a bare type list with no operator --
the split rule's final bullet, now confirmed through three levels of userset nesting:
`organization#member` → `plan#subscriber` → `feature#has_feature`, each a plain `relation`).
This store reached `PARITY OK` on the **first attempt**; the only thing it actually forces
is the capability question below and the encoding choice that follows from it, not new
mapping syntax.

### The actual increment: a bare form combined with more than one distinct caveat, not N-way mixing itself

The task's other stated fact to verify, not assume: that SpiceDB allows a relation's type
list to mix caveated and uncaveated forms of the same subject type
(`viewer: user | user with cav_a | user with cav_b`), with the uncaveated form making the
caveat *optional* rather than mandatory. **This is not new in the corpus at either extreme
alone** -- `temporal-access` already unions a bare form with 1 caveat
(`user | user with temporal_access`), and `condition-data-types` already unions **9** distinct
caveat names on one relation (`user with is_valid_string | user with is_valid_int | ...`),
with no bare form at all. `feature.has_feature` is the first store to combine **both**: the
bare form *and* more than one distinct caveat name (3, `is_below_collaborator_limit` /
`is_below_row_sync_limit` / `is_below_page_history_days_limit`) on the identical subject
reference (`plan#subscriber`) in one union. `WriteSchema` accepted it unedited, and three live
probes confirm the four alternatives are evaluated completely independently, with no
crosstalk:

```
$ zed permission check feature:can-invite-collaborator has_feature user:anne \
    --caveat-context '{"page_history_days_count":1}'    # the WRONG caveat's key
caveated   # collaborator_count is still reported missing -- the other caveat's key
           # does not satisfy this one

$ zed permission check feature:basic-page-analytics has_feature user:anne \
    --caveat-context '{"nonsense_key":1}'                # basic-page-analytics has no
true       # caveated alternative on has_feature at all -- any caveat context supplied is
           # simply irrelevant, confirming "uncaveated form present -> caveat optional"

$ zed permission check feature:can-sync-rows has_feature user:beth \
    --caveat-context '{"collaborator_count":999999}'     # again the wrong key
caveated   # row_sync_count is still missing
```

Each alternative's caveat evaluates strictly against its own declared parameters; supplying
an unrelated key never accidentally satisfies (or interferes with) a different alternative
on the same relation, and a feature with no caveated alternative at all (`basic-page-
analytics`, `advanced-page-analytics`, `enterprise-support` -- see the tuples table) is
completely unaffected by any caveat context supplied, matching "including the uncaveated
form makes the caveat optional" exactly. No new rule is needed. The isolation property itself
-- multiple distinct caveats coexisting on one relation and resolving correctly -- was already
exercised at 9-way scale by `condition-data-types`; what this store adds is narrower and
specific: confirming that isolation survives when a bare, uncaveated alternative is unioned
in alongside more than one caveat, a combination neither predecessor store's shape forced.

### Confirming the hard limit: a caveat cannot read another relationship or aggregate

This is the capability question the task brief asked to settle, not assume. Structural proof
from source (SpiceDB v1.56.0, `pkg/caveats/eval.go`): `EvaluateCaveatWithConfig(caveat
*CompiledCaveat, contextValues map[string]any, ...)` is the **only** entry point that
evaluates a caveat's CEL body, and its only data input is a flat `map[string]any` -- built by
merging the relationship's bound context with the check's supplied context (per the given
fact: bound wins on key conflict). There is no datastore reader, no relationship-graph
handle, and no aggregate/count primitive anywhere in that signature or the `Environment` /
`asCelEnvironment` machinery that compiles a caveat's parameter list (`pkg/caveats/env.go`).
A caveat can compare two JSON-shaped values against each other; it cannot look up, count, or
sum anything else SpiceDB knows.

Verified live, not merely asserted from source -- the most literal possible temptation for
SpiceDB to aggregate on its own is right there in this store's own graph: `collaborator_count`
sounds exactly like "how many members does this organization have," which SpiceDB already
tracks as `organization#member` relationships:

```
$ zed permission check feature:can-invite-collaborator has_feature user:beth \
    --caveat-context '{"collaborator_count":20}'
true                                    # organization:okta has 1 member (beth) at this point

$ zed relationship create organization:okta member user:carol
$ zed relationship create organization:okta member user:dave    # okta now has 3 members

$ zed permission check feature:can-invite-collaborator has_feature user:beth \
    --caveat-context '{"collaborator_count":20}'
true                                    # unchanged -- SpiceDB never noticed, never counted
```

Adding real members to `organization:okta` has zero effect on any `has_feature` check,
because nothing in the schema ties `collaborator_count` to `organization#member`'s actual
cardinality at all -- the parameter is, and can only ever be, whatever the caller's *next*
`--caveat-context` says it is. This is not a bug or an oversight in this store's schema; it
is the structural fact the source proof above predicts, made concrete: **counting
"how many collaborators does this org have right now" is not a question SpiceDB answers,
in OpenFGA's model or in SpiceDB's, before or after migration.** The pack-contract item 6
sync-obligation this could have introduced simply is not introduced here, because the source
system already externalized it.

### Where the capability goes if quota *and* usage were both stored facts

This store is not the `used < quota, both stored` case `blockers.md`'s framing calls
genuinely `blocked` -- but the source proof above answers precisely where the capability
would have to go if a future store's usage counter *were* itself a persisted SpiceDB fact
(for example, a running `plan:free#collaborator_used_count` relation the application
increments and decrements as members join and leave, rather than accepting a caller-supplied
count). A caveat cannot read that relation's cardinality no matter how it is named or shaped
-- `EvaluateCaveatWithConfig` never receives a datastore handle, so there is no CEL
expression that could `count(...)` over it. The comparison would have to leave the caveat
system entirely: **the application reads both stored facts itself (a `LookupSubjects`/count
call for "used," an ordinary check or read for "quota") and supplies the *result* of that
comparison, or the raw count, as check-time caveat context** -- functionally identical to
the partial workaround the task brief describes, except now the "used" side would be
migrated *out of* SpiceDB relationships and back into a value the calling application
assembles per request, same as this store's own `collaborator_count` already is. No corpus
store has a usage counter modeled as a persisted SpiceDB relation rather than a caller-
supplied value, so this remains a derived conclusion from source and from this store's own
near-miss, not a corpus-forced `blockers.md` entry in its own right (spec decision D11: rules
come from a real corpus model forcing them). It is recorded here as the answer the task asked
for, scoped honestly to what this store does and does not settle -- parallel to how
`abac-with-rebac` left the transitive-wildcard blocker's wildcard-at-the-far-end case
explicitly open rather than guessing at it.

### The gate decision: detection, options, and where it's recorded

**Class B gate decision, presented with both options and their tradeoffs, resolved the same
direction as `ip-based-access` and for the identical stated reason.** `temporal-access` and
`ip-based-access` share one shape (a caveat comparing a write-time-bound value against
something supplied per-check) and reach opposite recommendations because the per-check value
differs in kind: a wall-clock instant every caller means identically, for which SpiceDB has a
purpose-built structural feature (`with expiration`) -- versus a per-request attribute with
no SpiceDB structural analog at all. Usage-quota entitlements are the latter: there is no
`use quota` or equivalent counting primitive in SpiceDB, so -- per the same principle --
caveat stays the recommended default here too.

**Detection.** Model-only, no code needed:

1. Cheap pre-filter -- scan every `condition`/`caveat` body for a numeric comparison
   (`<=`, `<`, `>=`, `>`) between two `int`/`double`/`uint` parameters that are not a
   timestamp/duration pair (that shape is `temporal-access`'s, not this one).
2. Cross-reference the store's own fixture data, exactly as `temporal-access`'s and
   `ip-based-access`'s detection rules do: if one parameter is bound once per relationship at
   write time (a quota, an allowance, a limit) and the other is supplied fresh on every check
   in the store's own `tests:` blocks (a count, a usage figure), this is the request-supplied
   shape and the caveat form applies cleanly.
3. **The genuinely hard variant, which this detection rule must also flag rather than silently
   pass:** if the "usage" side is *not* supplied per-check in the source's own fixtures but is
   instead itself backed by a stored fact (a running counter tuple/attribute, or something the
   source model computes by counting other relationships), this is the harder,
   not-yet-corpus-confirmed shape "Where the capability goes if quota and usage were both
   stored facts" above describes -- halt and report rather than mechanically applying this
   section's recommendation.

**Options:**

| Option | Cost |
|---|---|
| **Caveat context -- recommended default** | Rated `clean` -- no new construct (a bare type list mixing caveated and uncaveated variants, split rule's final bullet), first-attempt `PARITY OK`. Preserves the source's per-request semantics exactly, including the free choice of *which* usage figure to supply (this store computes three independent ones), and is the only form this pack's own tooling verifies deterministically end to end, including `LookupResources`. Cost: per-check CEL evaluation on every call, forever, and the same materialize-ineligibility `ip-based-access` already established for any caveated permission. |
| **Materialized "verified" marker -- the alternative, only where a periodically-recomputed quota check is an accepted tradeoff** | Rated `effort` -- syntactically simple (one plain relation per gated feature, written/`TOUCH`ed or deleted whenever the application recomputes usage against quota), but a conscious, recordable decision to check a **staler** quota state than the source model checks on every call, plus a new recompute-and-rewrite sync obligation with no analog in the source model (which recomputes fresh on every check). Verified live (`has_feature_verified`, see `corpus-runs/advanced-entitlements/schema-materialized-marker.zed`): returns `true`/`false` with **no caveat context supplied at all**, zero per-check CEL -- the same "plain graph walk" cost every non-ABAC permission gets, at the price of the application now owning exactly when usage gets recomputed. |

**Record:** the chosen option per flagged condition, under `migration-plan.md`'s `Decisions`
→ `Per-blocker resolutions`, alongside: whether any call site needs the literal current usage
figure (a strong signal toward caveat, since quota checks that gate a live write -- "can this
org invite one more collaborator right now" -- are exactly the kind of security/business-limit
enforcement a stale marker would silently under- or over-enforce); and, if the marker is
chosen, the recompute cadence and the write path that keeps it current, since neither exists
in the source model today.

**This corpus store's own artifacts:** `corpus-runs/advanced-entitlements/schema.zed` is the
caveat form -- both the recommended default **and** the only form this pack's harness ritual
validates, so as with `ip-based-access` there is no split between "what the harness verifies"
and "what is recommended." Unlike `ip-based-access` (whose materialized-marker fragment is
reproducible from its own section's text and was not separately committed),
`corpus-runs/advanced-entitlements/schema-materialized-marker.zed` **is** committed alongside
`schema.zed` -- it is the deployed source of the "Confirming the hard limit" and "gate
decision" evidence above (`has_feature_verified`, live-verified in a separate keyspace under
the same relationship data), and keeping it as a real file lets a future iteration redeploy
and re-check it directly rather than re-authoring it from prose.

## Resource attributes: caveat vs. relation-name encoding

Corpus-verified on `openfga/sample-stores/stores/groups-resource-attributes` (v1.56.0 / zed
v0.31.1), both via `zed validate` / the harness and end to end against a live server. This is
the store the "Deliberately not written yet" section named as the plausible candidate for the
one branch `ip-based-access`'s own gate decision left open: "a genuinely enumerable,
low-cardinality attribute where all three [benchmark] forms apply in their literal shape and
the relation-name encoding is a live contender." It settles that branch -- not by flipping the
recommended default, but by finally building the alternative `ip-based-access` could only rule
out in the abstract, and by using this store's own test design to show precisely why the
default still holds.

### The mechanical caveat translation needed zero new constructs

```zed
caveat doc_viewer_condition(document_attributes map<string>, allowed_statuses list<string>) {
	allowed_statuses.exists(s, s == document_attributes["status"])
}

definition organization {
	relation member: user
	relation can_access_docs: group#member with doc_viewer_condition

	permission can_access_docs__perm = can_access_docs
}

definition document {
	relation organization: organization

	permission can_access = organization->can_access_docs__perm
}
```

Every piece is a confirmation of an existing rule, not a new one: `group#member with
doc_viewer_condition` is a caveat on a **userset**-typed subject reference, the exact shape
`ip-based-access` already added to the construct table (row 33); `can_access_docs` is a bare
type list with one entry and no operator, so it stays a plain `relation` (split rule, final
bullet); `can_access_docs from organization` reverses into `organization->can_access_docs__perm`
(the `__perm` alias branch, since `can_access_docs` never splits). The one genuinely new
composition -- a `map<string>` value indexed by a literal key and tested via `in` against a
*different* `list<string>` parameter, rather than each container type exercised in isolation
the way `condition-data-types` exercised them -- also needs no new rule, though it does surface
a previously-unverified runtime footgun; see "A missing map key is a hard evaluation error"
above. This store reached `PARITY OK` on the **first attempt**; the only thing it actually
forces is the encoding choice below, not the syntax.

### The missing piece from `ip-based-access`: relation-name encoding is finally buildable

`ip-based-access` ruled out the benchmark's fastest encoding -- baking the attribute directly
into relation names -- because its own attribute (`cidr`, an admin-configured string) had no
small, schema-known vocabulary to enumerate. This store's attribute does: every value any
`allowed_statuses` list in the source's own tuples names, and every value the source's own
`tests:` block checks, is one of exactly two strings, `"draft"` and `"published"` --
the same two-valued document-status category `abac-with-rebac` (a sibling corpus store)
already models as a plain relation, and whose own `README.md` states the general guidance this
store's caveat form deliberately does not follow ("if you can model your attribute as a
relation, you should"). Built and verified live, in a separate keyspace, reusing
`abac-with-rebac`'s own self-loop marker pattern:

```zed
definition organization {
	relation member: user
	relation can_access_draft_docs: group#member
	relation can_access_published_docs: group#member

	permission can_access_draft_docs__perm = can_access_draft_docs
	permission can_access_published_docs__perm = can_access_published_docs
}

definition document {
	relation organization: organization
	relation draft: document
	relation published: document

	permission can_access_when_draft = organization->can_access_draft_docs__perm
	permission can_access_when_published = organization->can_access_published_docs__perm
	permission can_access = (draft->can_access_when_draft + published->can_access_when_published)
}
```

(`corpus-runs/groups-resource-attributes/schema-materialized-marker.zed`.) All four of the
source's checks reproduce exactly, and -- the decisive difference from the caveat form -- with
**no `--caveat-context` at all**: `zed permission check document:1 can_access user:anne`
returns a plain `true`/`false`, never `caveated`. Unlike `ip-based-access`'s `cidr` and
`advanced-entitlements`' usage counts, this store's attribute genuinely admits all three of
the benchmark's encodings in their literal form, resolving the item "Deliberately not written
yet" had left open.

### Why materialized encoding still isn't the default: the store's own test design proves the attribute is per-request

Building the alternative is what makes the "still caveat by default" call honest rather than
assumed, because it exposes a concrete cost the abstract discussion in `ip-based-access` never
had to face: this store's own `tests:` block checks the **same object**, `document:1`, at
**two different status values**, `"draft"` and `"published"`, within one assertion set --
matching how the fixture treats `document_attributes` as a value the caller supplies fresh per
request, never as a stored fact. The caveat form answers both from one static relationship
graph, zero writes, by swapping check-time context. The materialized form cannot: `document:1`
can hold the `draft` marker or the `published` marker, never a context-dependent choice of
either, so reproducing the source's own two checks against **the same document** requires an
actual write between them -- verified live:

```
$ zed relationship create document:1 draft document:1
$ zed permission check document:1 can_access user:anne    # draft
true
$ zed permission check document:1 can_access user:bob     # draft
false

$ zed relationship delete document:1 draft document:1
$ zed permission check document:1 can_access user:anne    # mid-transition: neither marker set
false                                                      # -- true production risk, not a test artifact
$ zed relationship create document:1 published document:1
$ zed permission check document:1 can_access user:anne    # published
true
$ zed permission check document:1 can_access user:bob     # published
true
```

The mid-transition line is the concrete form of the fail-closed window `pack-contract.md` item
6 requires surfacing: even granting SpiceDB's own `WriteRelationships` the ability to apply the
delete and the create as one atomic call (eliminating the SpiceDB-internal gap shown above),
the irreducible gap is between the customer's own database commit (`documents.status =
'published'`) and the SpiceDB write that mirrors it -- two different systems, no distributed
transaction spans them, and every check in between sees stale state. This is exactly **one**
sync obligation by pack-contract item 6's count -- one source-of-truth attribute (document
status) needing permanent replication -- but it is the corpus's first sync obligation actually
built and exercised end to end rather than described in the abstract: a write path (the
delete-then-create above, on every status change), a backfill (materializing `draft` or
`published` for every existing document at migration time), a reconciliation job (to catch
drift if a status update's SpiceDB half fails independently of its database half), and the
fail-closed window demonstrated live above. Zero such obligations exist under the recommended
default: the caveat form needs the application to read the document's current status from its
own database and pass it as check context, which is a read the application already needs to
authorize the same request in the first place, not an incremental write path.

### The gate decision: detection, options, and where it's recorded

**Class B gate decision, presented with both options and their tradeoffs, resolved the same
direction as `ip-based-access` and `advanced-entitlements`, for the identical stated
principle.** This store's rejected alternative is fully built, deployed live, and committed
as an artifact (matching, not exceeding, `advanced-entitlements`' own precedent of committing
`schema-materialized-marker.zed`). `ip-based-access` also built and live-verified its own
materialized-marker alternative (`network_verified_member` / `can_view_verified`, see that
section's own artifacts note above) but committed no artifact for it; what *it* ruled out on
paper was the distinct **relation-name** encoding, since `cidr` has no enumerable vocabulary
to build that against. What
*is* new here, corpus-wide: this store is the first to build and exercise a live
fail-closed-window sync-obligation transition end to end, rather than describing the
obligation only in the abstract -- see the previous section's live write/delete/check
sequence. SpiceDB has no first-class structural feature for "the current
value of a caller's or a resource's mutable state, read fresh on every request" -- the same
absence that kept `ip-based-access`'s network check and `advanced-entitlements`' usage count on
the caveat side of their gates. This store's status attribute is small and enumerable, which is
*sufficient* for relation-name encoding to compile and run correctly, but the source model's
own design -- and this store's own test fixture -- already answer the harder question of
whether the attribute is naturally request-supplied or naturally persisted: `allowed_statuses`
(admin policy, rarely changes, paired with a relationship write the source model already makes)
is bound at write time in both this store's tuples; `document_attributes` (the document's
actual current status) is supplied fresh on every check, never once written to a tuple anywhere
in the source. The gate decision follows the source's own judgment rather than overriding it.

**Detection.** Model-only, no code needed:

1. Cheap pre-filter -- scan every `condition`/`caveat` body for a container-typed parameter
   (`map<T>`/`list<T>`) indexed or membership-tested against a comparison value, where the
   *compared* side is supplied per-check in the store's own fixtures (contrast with
   `temporal-access`'s wall-clock shape and `advanced-entitlements`' numeric-quota shape).
2. Cross-reference the store's own fixture data, the same way as every prior gate in this
   family: if the per-check side never appears in `tuples:`/`relationships:` at all, this is
   the request-supplied shape and caveat applies cleanly.
3. Enumerate the values the per-check parameter actually takes across the store's own tuples
   and tests. If the set is small and closed (as `"draft"`/`"published"` is here), relation-name
   encoding is buildable -- evaluate it per this section, rather than ruling it out
   automatically the way `ip-based-access`'s detection rule does for an unbounded value (an IP
   address, a free-form string). Buildable is not the same as recommended; step 4 still governs.
4. Decide request-supplied vs. persisted the way this store's own source already does: does an
   admin/config-style write bind the compared value at relationship-write time (→ leave that
   half as a relation, as `allowed_statuses` already is here), or does every call site fetch the
   current value fresh from the resource's own record (→ caveat context, not a schema change).

**Options:**

| Option | Cost |
|---|---|
| **Caveat context -- recommended default** | Rated `clean` -- no new construct beyond the userset-with-condition row `ip-based-access` already added, first-attempt `PARITY OK`. Matches the source model's own design exactly (the attribute is never persisted as a tuple anywhere in the source either), needs zero ongoing SpiceDB sync obligations (the application already reads the resource's current attributes to authorize the request; passing them as context is not incremental work), and is the only form this pack's own tooling verifies deterministically end to end. Cost: per-check CEL evaluation on every call, forever, and the same materialize-ineligibility every prior caveated permission in this pack's corpus carries. |
| **Relation-name / materialized marker -- the alternative, now fully buildable** | Rated `effort` -- syntactically simple (one marker relation per enumerable value, one arrow per value, one union), verified live to reproduce every one of the source's checks with **zero** caveat context. Cost: exactly **one** ongoing sync obligation per materialized attribute, per pack-contract item 6's count -- a write path on every status change (a delete + a create, or one atomic multi-update `WriteRelationships` call), a backfill for existing resources, a reconciliation job, and an irreducible fail-closed window between the source-of-truth commit and the SpiceDB commit, demonstrated live above. Only a genuine option when the attribute's vocabulary is both small/enumerable **and** the customer is willing to take on that permanent write-path commitment; this store's own source design suggests its original author already declined that trade for this exact attribute. |

**Record:** the chosen option per flagged condition, under `migration-plan.md`'s `Decisions`
→ `Per-blocker resolutions`, alongside: whether the per-check value ever appears bound in a
source tuple (a strong signal toward leaving it as-is rather than re-modeling); the enumerated
value set size and whether it is genuinely closed or grows with customer configuration (the
same enumerability test `ip-based-access`'s detection rule already applies); and, if the marker
is chosen, the write path, backfill plan, reconciliation cadence, and expected fail-closed
window being taken on, since none of the four exists in the source model today.

**This corpus store's own artifacts:** `corpus-runs/groups-resource-attributes/schema.zed` is
the caveat form -- both the recommended default **and** the only form this pack's harness
ritual validates. `corpus-runs/groups-resource-attributes/schema-materialized-marker.zed` is
committed alongside it, matching `advanced-entitlements`' precedent (not `ip-based-access`'s,
whose materialized fragment was left reproducible from prose only) -- it is the deployed
source of every live result in this section, including the fail-closed-window transition,
letting a future iteration redeploy and re-check it directly.

## Multiple isolated test fixtures colliding in one converted graph

`fga model test` treats each `tests:` block as an independent dataset. SpiceDB has one flat
relationship graph with no such scoping, so a `.fga.yaml` file with more than one `tests:`
entry can collapse two isolated scenarios into one that neither test individually describes.
Two distinct mechanisms produce this, verified on two different corpus stores -- one is
caught loudly at load time, the other is not caught at all by the harness and must be
diagnosed by hand.

### Same triple, different caveat context (`condition-data-types`)

A `.fga.yaml` file can reuse the identical `(object, relation, user)` triple across `tests:`
blocks, scoped apart only by `fga model test`'s own isolation, and only one relationship may
exist per triple in SpiceDB. `condition-data-types` does exactly this -- both of its
`tests:` blocks assert against `datatype_test:one#is_valid@user:<X>` for the same nine `X`
-- and `zed validate`'s loader rejects the naive merge outright:

```
error: found repeated relationship `datatype_test:one#is_valid@user:uint[is_valid_int:{"_int":1}]`
```

Once one relationship per triple is chosen (as it must be, to load at all), a check that
belongs to the *other* logical test can still evaluate to the right boolean for the wrong
reason: SpiceDB merges request-supplied caveat context with the relationship's own bound
caveat and context, so a check that supplies unrelated or absent context against an
already-satisfied bound caveat returns `true` without ever touching the caveat the source
check nominally names. Verified live end to end: in this store, one subject's two blocks
name genuinely *different* caveats for the same triple (`is_valid_uint` at write time vs.
`is_valid_int` at check time, both for `user:int`). Resolving the collision by keeping the
write-time binding leaves `is_valid_int` referenced by zero relationships in the converted
graph -- yet checking it anyway, with any context or none, still returns `true`, and both
`zed validate` and the harness stay green:

```
$ zed permission check datatype_test:one is_valid user:int --caveat-context '{"_totally_unrelated_key":999}'
true
```

**Detection.** Before combining tuples from more than one `tests:` block into one converted
graph, check for `(object, relation, user)` triples that recur across blocks. If found,
record which caveat each block names for the colliding triple. A converted graph can only
back one of them; the other's boolean answer in the harness is unverified, not merely
untested -- it can pass by coincidence, invisibly. There is no schema fix and this is not a
`zed validate` or harness failure to chase -- it is an inherent consequence of collapsing an
isolated-per-test source into one shared graph. Record the specific colliding triples in
`migration-plan.md` and flag them for a hand-written check in phase 5 rather than trusting
the converted validation file's boolean answer for them.

### Same object ID, different relation, mutually exclusive scenarios (`abac-with-rebac`)

A related but distinct collision, and one `zed validate` never sees at all. Instead of a
recurring identical triple, a `.fga.yaml` file's *per-test* `tuples:` block (the sanctioned
way to attach data OpenFGA's own comments describe as either a persisted write or "sent as a
contextual tuple") can write a **different relation** onto the **same object** in each
`tests:` block, so each block represents one real-world state of that object (draft vs.
published; pending vs. approved) rather than a duplicate fact. `abac-with-rebac`'s two
`tests:` blocks each add exactly one such tuple to the same `document:readme` --
`#draft@document:readme` in one block, `#published@document:readme` in the other -- and every
one of its twelve checks targets one of two permissions (`can_edit`, `can_view`) whose
definitions arrow through whichever one is present. No raw triple repeats, so `zed
validate`'s loader accepts either choice, or even both at once, without complaint. But the
harness's `load_fga_assertions` does not read
`tuples:` **at all** (only `check:` blocks), so it flattens both tests' assertions into one
list keyed on `(subject, permission, resource, context)` alone -- and for any check whose
answer depends on which scenario is active, the two tests supply the *same key with
different expected values*. `migration_harness.parity._dedupe` (built for the collision
above) correctly recognizes this as a same-side conflict and reports it as `AMBIGUOUS`, but
unlike the caveat-context case there is **no way to resolve it by picking one relationship**:
picking either scenario's tuple (or, worse, writing both at once -- confirmed live, see
below) makes the *other* test's colliding checks come out wrong, and the two colliding keys
are excluded from the harness's comparison **regardless of what the converted validation
file asserts for them**, because the exclusion is computed from the OpenFGA side alone
before the SpiceDB side is even consulted. Concretely, for the shape this store uses --
recorded from the pack's own validation run, **not** a step to perform (the harness is not
shipped with this plugin; see `SKILL.md`, "The parity harness is not part of this plugin"):

```
$ uv run migration-harness --store store.fga.yaml --converted validation.yaml --map migration-map.json
PARITY FAILED (4 assertions compared)
AMBIGUOUS     document:readme#can_view@user:anne same-side conflict: expected=False vs expected=True
AMBIGUOUS     document:readme#can_edit@user:bob same-side conflict: expected=True vs expected=False
```

This reproduces on **any** correct conversion of this store -- confirmed by writing a fully
correct schema and relationships and observing the identical two `AMBIGUOUS` lines with zero
`MISSING`/`EXTRA`/`CONTRADICTION`. It is a harness-comparator limitation, not a conversion
defect (see `corpus-runs/README.md`'s "Known harness gaps"), and it also confirms that
merging both scenarios' tuples into one persisted graph is a **real** modeling error, not
just a harness artifact: with both `#draft@document:readme` and `#published@document:readme`
present simultaneously on a live server, `can_edit` for the owner and `can_view` for a
verified-but-non-owner viewer both flip to `true` -- silently wrong for whichever one of the
two source scenarios is not the one currently intended.

**Detection.** Before combining tuples from more than one `tests:` block into one converted
graph, check whether any two blocks write **different relations onto the same object**
(not just recurring identical triples -- see the sibling case above) where a downstream
permission's answer depends on which relation is present. If a `check:` entry's expected
value differs between such blocks for the same `(subject, permission, resource)`, the
harness's whole-store run cannot reach `PARITY OK` for this store no matter how the
conversion is written, and this is expected, not a bug to chase.

**Handling.** There is no single-graph fix, because the scenarios are, by construction,
mutually exclusive states of the same object. Pick the state that best represents the
canonical/steady-state seed data for the shipped `validation.yaml` (record the choice), and
verify the state(s) the harness cannot see either by (a) running the harness a second time
per scenario against a derived, single-scenario `--store` file holding only the one relevant
`tests:` block (each such run reaches `PARITY OK` independently -- confirmed on
`abac-with-rebac`, see `corpus-runs/abac-with-rebac/`), or (b) toggling the relevant
relationship on a live server and checking every affected permission by hand. Either way,
record in `migration-plan.md` which checks the canonical harness run cannot verify and how
they were verified instead -- the same discipline the caveat-collision case above already
requires.

## Modular models: `fga.mod`, `module`, `extend type`

Corpus-verified on `openfga/sample-stores/stores/modular` against **live SpiceDB v1.54.0 and
v1.56.0 servers** (`WriteSchema`, real relationship writes, `CheckPermission`) and **zed
v0.31.1** (`validate` / `schema compile`). This is the corpus's only store whose source is
not one file. `fga.mod` requires `schema 1.2` and lists
`.fga` files under `contents:`; each listed file opens with `module <name>` instead of a
`model`/`schema` header; `extend type T { relations ... }` adds relations/permissions to a
type declared in a *different* module file. OpenFGA flattens all of this into one ordinary
model before evaluating anything -- `fga model get` renders the combined model with
`# module: X, file: Y` provenance comments, but the type and relation names themselves are
exactly the flat names a single-file model would use, and condition names stay globally
unique across every module. This section is about exactly that flattening step: getting
from N files with `module` / `extend type` to one deployable SpiceDB schema.

### The mapping

Every construct *inside* a module file still follows every other rule in this document
unchanged -- `module core` carries no meaning for `type` / `define` translation, it only
says which file a definition lives in. The only new construct is `extend type T { ... }`,
and it has no direct SpiceDB syntax: SpiceDB has no way to reopen a `definition` once
declared elsewhere. The mechanical translation:

1. Translate each module file's own `type` declarations into ordinary `definition` blocks,
   exactly as in a single-file model.
2. Translate each `extend type T { ... }` block into a `partial` block instead of a
   `definition` -- one partial per extending module, holding exactly the relation/permission
   lines that module's `extend type` adds. Verified: a `partial` may hold `relation` lines,
   `permission` lines, or both, so this covers an `extend type` block that adds either
   shape -- even though `modular`'s own two `extend type` blocks happen to be
   permission-only (`can_create_space`, `can_create_project`, both plain aliases of
   `admin`).
3. Write one root file that `import`s every module file and declares the extended type's
   `definition`, spreading in the base module's own contribution alongside every extending
   module's partial:

   ```zed
   import "./core.zed"
   import "./wiki.zed"
   import "./issue-tracker/projects.zed"

   definition organization {
       ...core_organization
       ...wiki_organization_ext
       ...issuetracker_organization_ext
   }
   ```

   This root file is the SpiceDB analogue of `fga.mod` -- it is the *one* place that has to
   know about every module touching a given type, which is new: no single `.fga` module
   file in the OpenFGA source ever has to know that another module extends a type it
   declares.
4. A partial's lines may freely reference a relation/permission declared in a *different*
   file's partial or definition -- e.g. `permission can_create_space = admin` inside the
   wiki extension, where `admin` is declared in core's partial -- **with no import needed in
   that file specifically**. Verified: symbol resolution runs over the whole compiled
   program reachable from the root's `import` graph, not per source file; a file that itself
   never imports the file declaring a name it references still compiles cleanly as long as
   the root ties both into one graph. One consequence worth knowing before it causes
   confusion: a `partial` with an unresolved reference is not even checked until something
   spreads it into a definition, so `zed validate` on one extension file in isolation will
   not catch a broken reference inside it.

**Rating: `effort`.** Mechanical and lossless once the shape above is known -- verified end
to end on a live SpiceDB v1.56.0 server, which reproduced every one of `modular`'s expected
answers, including `can_create_project`, a permission the store's own top-level test file
(`store.fga.yaml`) never exercises (`issue-tracker.fga.yaml`, one of the store's *per-module*
test files, does). It is not a per-line `clean` translation like most of the table above,
though: it requires inventing a root file with no corresponding OpenFGA source file, and
deciding, per extended type, which module's contribution is the base `definition` and which
become `partial`s spread into it.

### `partial` and `import` need opposite syntax on the two tools that read them

**Correction, verified against a live server after an earlier version of this section got
it wrong.** The earlier claim here was that neither `partial` nor `import` reaches
`WriteSchema` at all. That is false for `partial`. The decisive test is each tool's own
bogus-flag enumeration, which lists everything it recognizes:

```
$ echo 'use bogusflag

definition user {}' > /tmp/b.zed

$ zed schema write /tmp/b.zed      # -> the live server's answer
Unknown use flag: `bogusflag`. Options are: expiration, import, partial, self, typechecking

$ zed validate /tmp/b.zed          # -> zed's own local parser's answer
Unknown use flag: `bogusflag`. Options are: expiration
```

SpiceDB's `WriteSchema` knows `partial` (and `self`, `typechecking`) as real `use` flags.
**Whether your `zed` agrees depends on its version, and this is the single most
version-sensitive area in this file.** zed **v1.2.0** knows them; zed **v0.31.1**'s local
parser (used by `validate` and `schema compile`) knew only `expiration`, and on that client
the two tools disagreed about which syntax was valid **in opposite directions** for the
identical construct.

**On zed v1.2.0 the `partial` skew is gone.** Re-verified against a live SpiceDB v1.56.0:
`use partial` + `partial { ... }` validates (exit 0) *and* writes, while the bare
`partial { ... }` form that v0.31.1 required is now **rejected** by `validate`. So a single
file with `use partial` is valid input to both tools, and the workaround below is no longer
needed on a current client.

**`import` is unchanged, and is not a version problem.** Re-verified on zed v1.2.0:
`use import` validates locally and `WriteSchema` still rejects it with
`import statements are not allowed in this context`. `zed schema compile` still resolves
imports into a single file, and that remains the only way to ship an `import`-bearing
schema.

The historical matrix, kept because a project may still be pinned to an older `zed`:

| Form | Live `WriteSchema` (v1.54.0 / v1.56.0) | zed v0.31.1 `validate` / `schema compile` |
|---|---|---|
| `use partial` + `partial { ... }` | **accepted** -- merges the partial into the target `definition`, verified by `zed schema read` afterward | rejected: ``Unknown use flag: `partial`. Options are: expiration`` |
| bare `partial { ... }`, no `use` | rejected: ``Unexpected token at root level: TokenTypeIdentifier`` | **accepted** -- this is the "extended syntax" `zed schema compile --help` refers to |
| `use import` + `import "..."` | rejected, deliberately: ``import statements are not allowed in this context`` | rejected: ``Unknown use flag: `import`. Options are: expiration`` |
| bare `import "..."`, no `use` | rejected: ``Unexpected token at root level: TokenTypeIdentifier`` | **accepted** |

Two constructs, two different stories:

- **`partial` is a real, working `WriteSchema` feature** as of the fold this pack's "Target
  version floor" describes (v1.52.0) -- it just requires the `use partial` flag, which zed's
  own local parser does not know about on this toolchain (v0.31.1). Verified live: a
  single-file schema with `use partial` at the top, one `partial` block, and one definition
  spreading it in deploys directly via `zed schema write`, and `zed schema read` afterward
  shows the partial already merged into the target definition -- **no compile step
  required** when the source is one file with no `import`.
- **`import` is genuinely, unconditionally rejected in the `WriteSchema` context**, and the
  server's specific error (`import statements are not allowed in this context`, distinct
  from the generic token error) shows this is a deliberate rule, not a grammar gap -- this
  matches what this section originally said, before an incorrect blanket rewrite covered
  `partial` too. There is no `use import` form, no bare form, no way to `WriteSchema` a
  schema containing an `import` statement, ever.

**Consequence, on zed v0.31.1 only:** no single file text was simultaneously valid input to
`zed validate`/`zed schema compile` **and** to a live `WriteSchema`, for a schema using
`partial`. That was a toolchain-pairing skew on that version combination, **fixed in zed
v1.2.0** -- check your client version before designing around it, and note the general
lesson: a capability the server has had all along can look absent because the client cannot
express it. It was never a designed restriction -- and it is *why* compiling is the practical
default even though it is not, strictly, the only way to deploy `partial`. See "Three ways
to ship a modular schema" below for what each option actually buys and costs, verified.

### A green `zed validate` can certify an undeployable schema -- but only from one specific,
avoidable misconfiguration

The trap is narrower than an earlier version of this section claimed. `zed validate` (both
directly on a `.zed` file and via a validation YAML's `schemaFile:`) uses the same
extended-syntax parser as `zed schema compile`, so it resolves bare `import`/`partial`
transparently. Pointing a validation YAML's `schemaFile:` at the raw, uncompiled multi-file
root **when the YAML sits in the same directory as that root** reports:
```
Success! - 3 relationships loaded, 5 assertions run, 0 expected relations validated
```
identical to what the compiled, deployable form gives -- on a file that, if handed to
`WriteSchema` as-is (a multi-file source necessarily uses `import`), would be rejected
outright. **But relative `import` paths resolve against the validation YAML's own
directory, not the schema file's**, and the canonical repo layout this pack uses keeps
`validation.yaml` and the multi-file source in *different* directories
(`corpus-runs/<store>/validation.yaml` next to `schema.zed`; the multi-file source in a
`modules/` subdirectory). Pointing that canonical `validation.yaml` at `modules/manifest.zed`
does not stay quietly green -- it fails loudly:
```
$ zed validate validation.yaml   # schemaFile: modules/manifest.zed
error: parse error in ``, line N, column 1: failed to read import in schema file
```
exit 1 from `zed`, **exit 2 from the harness** -- the harness's normal, correct "your
schema doesn't validate" signal. The false-green case only reproduces when someone
co-locates the validation YAML with the multi-file source itself (e.g. authoring or
debugging in place inside `modules/`), which is an easy mistake to make but not the layout
this pack's own artifacts use. State it precisely: **co-locating `schemaFile:` with a
multi-file `import`-bearing source is a real, silent trap; the canonical separated layout
is not silently vulnerable to it, and fails loudly instead.** Either way, the only correct
target for `--converted` / `schemaFile:` in the canonical layout is the post-`zed schema
compile` single file -- the multi-file source (or a single-file `use partial` source, see
below) should never be what gets validated or written, both because of this layout hazard
and because `zed validate` cannot parse `use partial` at all regardless of layout.

One more form worth naming so it is not confused with either: a validation YAML's *inline*
`schema:` block (as opposed to `schemaFile:`) uses a **different, non-extended** compiler --
verified: a bare `partial` inside an inline `schema:` block fails the same way a
`WriteSchema` call would (`Unexpected token at root level`), even though the identical text
in a `schemaFile:`-referenced `.zed` file is accepted. `schema:` and `schemaFile:` are not
interchangeable for a `partial`/`import`-bearing schema.

### Three ways to ship a modular schema

Not one canonical answer -- three real options, verified, with different costs:

| Option | Form | Deploys via `WriteSchema`? | `zed validate`-able? | `zed schema compile`-able? |
|---|---|---|---|---|
| **A. Fully flattened** | One file, no `partial`/`import` at all -- every module's contribution hand- or compiler-merged into ordinary `definition` blocks | Yes, directly | Yes | N/A (nothing to compile) |
| **B. Single file, `use partial`** | One file, `use partial` at the top, one `partial` block per extending module, spread into the base type | **Yes, directly -- no compile step** | **No** -- zed v0.31.1 rejects `use partial` before it ever reaches a server | No -- same rejection |
| **C. Multi-file, bare `partial`/`import`** | Several files (the `fga.mod` analogue), `import`ed from one root, using the *bare* keyword forms | No -- `import` is unconditionally rejected | Yes | Yes -- compiles to option A's shape |

Option A is what this pack's harness-facing artifacts use, and what the rest of this pack
already assumed before this store: it is the only option compatible with the harness as
built (`zed validate` on the artifact that also has to be the deployable one) and it needs
no version-specific `use`-flag knowledge at all. Option C is the multi-file *authoring*
form -- keep it under version control as the human-editable, `fga.mod`-shaped source, and
generate option A from it with `zed schema compile` before it is ever validated or
deployed; this is the pipeline `corpus-runs/modular/` demonstrates.

**Option B is real and was wrongly ruled out by an earlier version of this section.**
Verified: a single file with `use partial`, containing every module's contribution as a
`partial` spread into the base type, deploys directly to a live SpiceDB v1.56.0 server with
no compile step, and `zed schema read` confirms the partials already merged. Its real cost
is tooling, not deployability: it **cannot** be validated, compiled, or diffed by zed
v0.31.1 at all -- not by this pack's harness, not by `zed validate` in CI, not by `zed
schema compile` for a human to eyeball the merged form. It is a legitimate answer for a team
willing to deploy without local `zed`-side validation (e.g. validating entirely against a
staging server instead), and a bad fit for this pack's harness-based ritual specifically,
which is why option A remains this pack's default recommendation. Which option to use is an
operational choice with a real cost difference -- record it at the gate rather than picking
silently, the same way this pack treats other multi-form encoding choices.

### Provenance survives compilation only on the line it is written on

OpenFGA's own combined view (`fga model get`) annotates each type with
`# module: X, file: Y`. SpiceDB's `partial`/`import` compiler has no built-in equivalent,
but plain `//` comments placed **directly above the specific `relation` or `permission`
line** do survive `zed schema compile` into the merged definition, attached to that same
line -- verified by compiling this store's own annotated module files. A comment on a
`partial`'s own declaration line (`// wiki module\npartial wiki_ext { ... }`) does **not**
survive; only comments immediately preceding an individual member line do, and each spread
partial's lines carry their own comments independently into whatever definition they end up
merged into. This is optional, not a mapping requirement -- the multi-file source under
version control is the durable provenance record regardless -- but it is a correct,
mechanical way to reproduce `fga model get`'s annotation in the file that actually gets
deployed, for a team that wants it.

**Misattribution hazard.** A file-header comment block preceding the file's first
`definition` also survives -- but it survives attached to *that definition specifically*,
not to the file as a whole, which is easy to misread as a per-file header. Verified on
`core.zed`'s own multi-paragraph header (describing the whole file, not just `user`): it
lands entirely above `definition user {}` alone in the compiled output, because that is the
first declaration the comment precedes -- nothing marks it as file-level, and nothing
carries it to `organization`, the definition `core.zed`'s content mostly exists to feed via
a `partial`. Do not rely on a leading file comment to document a file's role once compiled;
put per-construct comments on the specific lines that need them instead.

## Always fully parenthesize

SpiceDB's precedence runs tightest-to-loosest `arrows > + > & > -`. **Union binds tighter
than intersection**, which is the opposite of what most readers assume. Verified on
v1.56.0 with discriminating fixtures:

| Written | Parses as | Evidence |
|---|---|---|
| `aaa + bbb & ccc` | `(aaa + bbb) & ccc` | subject in `aaa` only → **false** |
| `aaa - bbb + ccc` | `aaa - (bbb + ccc)` | subject in `aaa` and `ccc` → **false** |

OpenFGA's grammar forbids mixing operators at one level without parentheses, so the source
parse tree is already unambiguous. **Walk the source tree and emit one parenthesized group
per source node.** Never rely on SpiceDB precedence, and never flatten two source levels
into one expression.

**This rule governs hand- or translator-authored output, not `zed schema compile`'s own
output.** `zed schema compile` does not add these parentheses -- verified on `modular`: a
source `permission member = (member__direct + admin)`, written with the parenthesization
this rule requires, compiles through a `partial` spread and comes back out as
`permission member = member__direct + admin`, parens dropped, because the compiler's
pretty-printer does not follow this convention. Where the deployable artifact **is**
literally `zed schema compile`'s output (the modular pipeline's option A -- see "Modular
models" → "Three ways to ship a modular schema"), commit that output unedited rather than
hand-adding parentheses back in: the point of generating a file is that it can be
regenerated byte-for-byte from its source, and hand-editing a generated artifact breaks
that guarantee silently. This rule keeps governing every schema a translator writes
directly, which is every non-modular store in the corpus so far.

A chain of one operator is one group: `a or b or c` → `(a + b + c)`, not `((a + b) + c)`.

**First real-corpus confirmation of intersection.** Every rule above was verified against
hand-built discriminating fixtures, not a corpus store -- no store's own converted schema
had used `&`/`and` at all until `ip-based-access` (`document.can_view: viewer and
ip_based_access_policy from organization` → `permission can_view = (viewer &
organization->ip_based_access_policy__perm)`). No new rule was needed: one source `and`
node, one parenthesized group, exactly as the union/arrow cases already documented, and it
matched OpenFGA's own `Checks 2/2 passing` exactly on a live v1.56.0 server. Recorded as a
confirmation, not a new construct.

## `use` flags are load-bearing

`use` statements go at the top of the file, before every definition and caveat.

- **`use expiration` whenever `with expiration` appears.** Without the flag, `with
  expiration` means *"with a caveat named `expiration`"*. On v1.56.0 that fails loudly
  (``could not lookup caveat `expiration` ... not found``, at both `zed validate` and
  `WriteSchema`) **unless the schema also defines a caveat named `expiration`** -- in
  which case it silently binds to the caveat and everything validates clean. That silent
  case is the one worth guarding against, and normalization makes it reachable: any source
  condition whose name normalizes to `expiration` sets the trap.
- **`use typechecking` whenever a permission carries a type annotation.** Without the
  flag, annotations are silently discarded and the schema validates clean.
- **`use self` whenever `self` is used -- and check your `zed` version before concluding it
  is unsupported.** Verified on **zed v1.2.0** against a live SpiceDB v1.56.0: `use self` plus
  `permission read_profile = self` validates, writes, and answers correctly (`true` for a
  subject against itself, `false` against another) **with no relationship written at all**.
  Without the flag on that version, validation fails outright.

  **On zed v0.31.1 the same schema is impossible, and the failure is misleading in a way
  worth knowing about.** That client rejects the flag as unknown (`Options are: expiration`)
  and accepts a bare `self`, which then fails at `WriteSchema` with ``relation/permission
  `self` not found``. **The server supported `self` the whole time; the client was too old to
  express it.** So an "unsupported" verdict reached with an old `zed` can be a client
  limitation wearing a server error's clothes -- record the `zed` version alongside any
  capability claim, and re-check on a current client before designing around the gap.
  Without a flag, `self` lexes as an ordinary
  identifier and compiles to a computed userset that fails at `WriteSchema`.
- **`use import` is rejected by `WriteSchema` unconditionally**, deliberately, with a
  distinct error (``import statements are not allowed in this context``) rather than a
  generic parse failure -- confirmed live on v1.54.0 and v1.56.0. Multi-file output that
  uses `import` must go through `zed schema compile` into a single file before deployment;
  there is no form of `import`, flagged or bare, that a live `WriteSchema` will ever accept.
- **`use partial`, by contrast, is accepted directly by `WriteSchema`** as of the fold this
  pack's "Target version floor" describes (v1.52.0) -- verified live: a single-file schema
  opening with `use partial`, containing one or more `partial` blocks spread into a
  `definition`, deploys with no compile step, and `zed schema read` afterward shows the
  partial already merged in. The catch is zed v0.31.1's own local parser (`validate` /
  `schema compile`) does not recognize the `use partial` flag at all -- it only recognizes
  the *bare*, unflagged form of `partial`, which is the opposite of what `WriteSchema`
  requires. No single file text satisfies both tools for a `partial`-bearing schema on this
  toolchain pairing. See "Modular models" below ("`partial` and `import` need opposite
  syntax on the two tools that read them") for the full verified matrix and what to do about
  it.

For an OpenFGA source, none of these fire from a `clean` translation: `self`, type
annotations, and native expiration are SpiceDB-only constructs with no OpenFGA source.
They fire only when a translation *deliberately* emits one -- for example
encoding a temporal condition as native expiration instead of as a caveat, which is a Class B
gate decision, not a silent mechanical mapping. See "Temporal access: caveat vs. native
expiration" for the decision itself (native expiration is the recommended default; caveat is
the alternative for a source model whose call sites need "as of a time") and the verified
evidence behind it.

## Codegen rules

Constraints a generator hits immediately. All verified on v1.56.0.

- **One declaration per line.** `definition doc { relation viewer: user }` on one line is
  a parse error (`Expected end of statement or definition, found: TokenTypeRightBrace`).
  The lexer only emits a synthetic statement terminator after `Identifier`, `Keyword`,
  `RightBrace`, `RightParen`, or `Star` -- notably `...` does not terminate on a newline.
- **Line wrapping has a direction.** A permission expression may span lines only with the
  operator at the **end** of a line:

  ```zed
  permission view = viewer +      // OK
      editor
  ```

  A leading operator on the continuation line is a parse error (`Expected end of statement
  or definition, found: TokenTypePlus`). A pretty-printer hits this on its first wrap.
- **Collapse single-child unions and intersections.** SpiceDB rejects a union or
  intersection with fewer than two children. The DSL cannot express one, but the
  authorization-model **JSON** can, so this fires when converting from JSON rather than
  from `.fga`. Emit the single child alone.
- **Never emit a `_`-prefixed identifier.** SpiceDB's docs claim leading-underscore
  "private" identifiers are supported; that is false on every release, verified by
  compiling the docs' own example.
- **Watch the direction of implication.** Where a source expresses "weaker role implied by
  stronger", SpiceDB expresses the union on the *weaker* permission. OpenFGA's `or` maps
  operand-for-operand so this is trivially satisfied -- except at arrows, where the order
  genuinely reverses. That is where the regression test belongs.
- **A carried-over source name that happens to end in its own type's name triggers a
  `relation-name-references-parent` lint warning -- suppress it in place, do not rename.**
  Corpus-confirmed on `advanced-entitlements`: `feature.has_feature` (verbatim from the
  OpenFGA source) triggers ``Relation "has_feature" references parent type "feature" in its
  name; it is recommended to drop the suffix (relation-name-references-parent)`` under
  `--fail-on-warn`. Verified from source (`pkg/development/warningdefs.go`,
  `lintRelationReferencesParentType`): this lint is a plain suffix-string comparison against
  the enclosing definition's name -- nothing in the check function reads the definition's
  contents, subject types, or resolution behavior, so unlike `arrow-references-relation`
  (fixed via the `__perm` alias -- see "Point arrows at permissions, not relations" above)
  there is no basis to suspect any effect on resolution. The two lints also differ in what
  fixing them costs: the `__perm` alias *adds* a name without touching the original, so
  nothing downstream loses fidelity to the source; satisfying this lint has no such option --
  the write path and the check path are the same one name, so renaming it to comply would
  trade fidelity to the source's own relation name for a purely cosmetic nudge. SpiceDB has a
  purpose-built escape hatch instead, verified live:
  a `// spicedb-ignore-warning: relation-name-references-parent` comment placed directly
  above the flagged `relation`/`permission` line (not above the enclosing `definition`)
  suppresses the warning at both `zed validate --fail-on-warn` and `WriteSchema`, confirmed
  to survive `WriteSchema` unedited (`zed schema read` afterward still shows the comment).
  Prefer the ignore-comment over a rename whenever the flagged name is the source's own and
  the only cost of leaving it is this lint.

## Worked example

A repository-hosting model, converted whole, chosen to exercise every split shape this file
establishes in one store: a plain userset-recursive relation, a type list fused with a
same-type permission reference, and a three-way fusion of a type list, a same-type
permission reference, and an arrow. The output below was verified end to end on this
toolchain: `zed validate` clean, `zed schema write` accepted by a real SpiceDB v1.56.0, and
eight assertions run green against it.

```
model
  schema 1.1

type user

type crew
  relations
    define member: [user, crew#member]

type image
  relations
    define pusher: [user, crew#member] or push_grant from registry
    define maintainer: [user, crew#member] or pusher
    define registry: [registry]
    define puller: [user, crew#member] or scanner or pull_grant from registry
    define scanner: [user, crew#member] or publisher
    define publisher: [user, crew#member] or maintainer or publish_grant from registry

type registry
  relations
    define member: [user] or steward
    define steward: [user]
    define push_grant: [user, registry#member]
    define pull_grant: [user, registry#member]
    define publish_grant: [user, registry#member]
```

```zed
definition user {}

definition crew {
	relation member: user | crew#member
}

definition registry {
	relation member__direct: user
	relation steward: user
	relation push_grant: user | registry#member
	relation pull_grant: user | registry#member
	relation publish_grant: user | registry#member

	permission member = (member__direct + steward)
	permission push_grant__perm = push_grant
	permission pull_grant__perm = pull_grant
	permission publish_grant__perm = publish_grant
}

definition image {
	relation pusher__direct: user | crew#member
	relation maintainer__direct: user | crew#member
	relation registry: registry
	relation puller__direct: user | crew#member
	relation scanner__direct: user | crew#member
	relation publisher__direct: user | crew#member

	permission pusher = (pusher__direct + registry->push_grant__perm)
	permission maintainer = (maintainer__direct + pusher)
	permission scanner = (scanner__direct + publisher)
	permission publisher = (publisher__direct + maintainer + registry->publish_grant__perm)
	permission puller = (puller__direct + scanner + registry->pull_grant__perm)
}
```

Every rule in this file is visible in that output:

- `crew.member` has only a type list → plain relation, no split.
- `registry.member` mixes a list with `or` → split into `member__direct` + permission
  `member`; the `registry#member` userset references elsewhere keep working because
  the permission kept the name.
- `registry.steward` and the three `*_grant` relations are pure-direct → plain relations;
  the three used as arrow targets get `__perm` aliases, the unused `steward` does not.
- Every permission body is one parenthesized group, matching one source `or` node.
- `push_grant from registry` reversed into `registry->push_grant__perm`.

Data consequences to record in `migration-plan.md`: six relations were renamed with
`__direct`, so every stored tuple on `registry#member`, `image#pusher`,
`image#maintainer`, `image#puller`, `image#scanner`, and `image#publisher` is rewritten in phase 3.

## Deliberately not written yet

These are known gaps, held open on purpose until a corpus store forces the rule. Halt and
report rather than guessing:

- Condition/caveat **parameter types and expression bodies** for anything outside the nine
  types "Caveat parameter types and expression bodies" now covers (resolved by
  `condition-data-types`).
- Any **encoding choice** with more than one valid SpiceDB form: attribute as caveat
  context vs. wildcard marker vs. relation-name encoding. This is a decision with measured
  cost differences, and the pack must present the choice at the gate rather than pick
  silently -- so it is not a mapping rule at all. Four named instances are now resolved:
  temporal conditions as caveats vs. native expiration (`temporal-access`, see "Temporal
  access: caveat vs. native expiration"); a per-request-supplied attribute (source IP) as
  caveats vs. a materialized marker (`ip-based-access`, see "IP-based access: caveat vs.
  materialized marker" -- this one also settled that the literal wildcard-marker and
  relation-name forms do not apply to an IP-typed attribute at all, and generalized the
  wildcard-marker *architecture* to a plain-relation-plus-intersection marker instead);
  usage-quota entitlements as caveats vs. a materialized "verified" marker
  (`advanced-entitlements`, see "Usage-quota entitlements: caveat vs. materialized marker" --
  resolved the same direction as `ip-based-access`, for the same stated principle: no
  first-class SpiceDB structural feature for the per-check-supplied side of the comparison);
  and a genuinely enumerable, low-cardinality resource attribute where all three benchmark
  forms apply in their literal shape (`groups-resource-attributes`, see "Resource attributes:
  caveat vs. relation-name encoding" -- relation-name encoding is buildable here, unlike
  `ip-based-access`'s ruled-out attempt, but the gate still resolves toward caveat by default,
  for the same stated principle plus a sync-obligation cost this store is the first to build
  and count concretely). No further branch of this open item remains earmarked.
- Everything with a `heavy` or `blocked` rating -- see `blockers.md`.
- The **unbounded-permission-surface** variant of runtime-defined roles: a role that must
  grant a permission or resource shape the schema did not already anticipate.
  `custom-roles`' own shape (a bounded, pre-declared permission surface) is resolved -- see
  "Runtime-defined ('custom') roles" -- but this harder variant is not. **Correction:** an
  earlier iteration named `advanced-entitlements` as the next candidate; per that store's own
  "boundary" note above, it does not settle this -- no corpus candidate is currently
  earmarked.
- The **aggregated-usage-counter** variant of quota entitlements: a caveat comparing two
  independently-*stored* facts (both "used" and "quota" persisted, rather than one supplied
  per-check). `advanced-entitlements`' own shape (usage supplied per-check, matching its
  OpenFGA source exactly) is resolved -- see "Usage-quota entitlements: caveat vs.
  materialized marker" -- but this harder variant is not corpus-forced yet; that section's
  "Where the capability goes if quota and usage were both stored facts" derives the answer
  from source rather than from a real store, pending one that actually models a persisted
  usage counter.
