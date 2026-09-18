# Policy mapping: Polar → SpiceDB schema

Pack contract item 3. Construct-by-construct translation rules, each carrying a fidelity
rating (`clean` / `effort` / `heavy` / `blocked` -- defined in `migrating-to-spicedb/SKILL.md`).

## Where the model comes from

Two endpoints, both cheap, and phase 0 should pull both before classifying anything:

- `GET /policy` -- the complete Polar source. **Capped at 1 MB**, so it is always readable
  in one call. Usually in the customer's git as well.
- `GET /policy_metadata` -- structured resources, roles, permissions, and relations. This
  is a ready-made translator input and is preferable to parsing Polar yourself for the
  declarative core.

Use the metadata for the declarative core and the source for everything else: the metadata
does not carry the free-standing rules (`has_permission(...) if ...`) where the hard
constructs live.

## Do not build this off the published grammars

`osohq/tree-sitter-polar` and `osohq/polar-grammar` are **deliberately permissive**.
tree-sitter parses multi-hop `on` chains that Oso's real parser rejects, and several
tokens (`forall`, `print`, `debug`, `type`) lex but are refused. They are not a semantic
spec, and a converter written against them silently mistranslates real policies.

Worse, Oso's own shipped sample apps contain constructs the documentation never
demonstrates -- notably `and` inside a shorthand right-hand side:

```polar
"read" if is_listed(resource) and "member" on "consortium";
```

**Validate against real policies, not against the docs.** Oso's own sample application (`osohq/gitcloud`) is the reference: its policy contains every
construct class below, including the ones the documentation omits.

## The core mapping

| Oso Cloud | SpiceDB | Fidelity | Note |
|---|---|---|---|
| `actor` / `resource` block | `definition` | `clean` | Direct |
| `roles = [...]` | `relation` | `clean` | One relation per declared role |
| `permissions = [...]` | `permission` | `clean` | Names are arbitrary strings; see `naming-normalization.md` |
| `relations = {...}` | `relation` to a definition | `clean` | Many-valued on both sides |
| `"a" if "b";` | `permission a = b` | `clean` | Implication becomes union |
| `"a" if "b" on "rel";` | `rel->b` | `clean` | Oso's `on` is single-hop; so is SpiceDB's arrow |
| `global` block | singleton definition + arrow | `clean` | Conventional Zanzibar pattern |
| `not` on a fact | `-` exclusion | `clean` | Parenthesise it |
| recursive rules | recursive permission | `clean` | SpiceDB's recursion is *less* restricted than Oso's |
| unary fact `is_open_access(d)` | marker relation, wildcard, or caveat | `effort` | Becomes data you must sync |
| context facts (attributes) | caveat + request context | `effort` | Good fit when genuinely request-scoped |
| context facts (edges) | stored relationships | `effort` | New write-path obligation; cannot stay ephemeral |
| customer-defined role *data* | `role#member` subject sets | `effort` | Role objects and grants map as data |
| open permission vocabulary | generated schema | `heavy` | Permission names are schema identifiers |
| `role if role on "org"` | generated union per role | `heavy` | A variable in the role position |
| 4- and 5-ary predicates | reification | `heavy` | A synthetic type to hang extra arguments from |
| comparison across two stored facts | — (application) | `blocked` | A caveat cannot read another relationship |
| multi-variable Query Builder | — (application join) | `blocked` | SpiceDB answers one question shape at a time |
| `listLocal` / `authorizeLocal` | — | `blocked` | SpiceDB never returns SQL |

`heavy` and `blocked` rows each have a detection rule and an options list in
`blockers.md`. Do not improvise options for them here.

## The declarative core, worked

Oso:

```polar
resource Dataset {
  permissions = ["download", "annotate"];
  roles = ["analyst", "curator"];
  relations = { lab: Lab };

  "download" if "analyst";
  "annotate" if "curator";
  "analyst" if "curator";
  "curator" if "steward" on "lab";
}
```

SpiceDB:

```zed
definition dataset {
    relation lab: lab

    relation curator__direct: user
    relation analyst__direct: user

    permission curator = curator__direct + lab->steward__perm
    permission analyst = analyst__direct + curator

    permission download = analyst
    permission annotate = curator
}
```

Three things are happening, and only the first is obvious.

**1. Implication inverts.** Polar's `"download" if "analyst"` reads as "download is granted
*by* analyst". SpiceDB's `permission download = analyst` reads the same way, so the
translation is a direct rewrite -- but note the direction: the *left* side of Polar's `if`
becomes the left side of SpiceDB's `=`, and everything to the right of `if` becomes the
union on the right.

**2. Roles split, exactly as fused defines do for OpenFGA.** An Oso role is *both*
directly assignable (a `has_role` fact) and derivable (a shorthand rule). SpiceDB cannot
have both under one name -- a relation is written to, a permission is computed, and
writing to a permission is rejected outright. So any role that appears on the left of a
shorthand rule **and** receives `has_role` facts splits into a `__direct` relation plus a
permission. This is the framework's relation-split obligation, and
`migrating-to-spicedb/references/findings-report.md`'s `relation_splits` key records it.
Read the suffix out of `migration-map.json`; never construct it.

A role that is *only* assignable (no rule grants it) does not split. A role that is *only*
derived (no `has_role` facts ever name it) becomes a bare permission. Establish which by
reading the policy **and** the facts -- a role with no rule today can still receive facts.

**3. Arrows need an alias on the target.** `lab->steward` requires
`steward` to be a *permission* on `lab`, not a bare relation, or
`zed validate` emits `arrow-references-relation`. When the target is a bare relation,
generate a `__perm` alias on the target definition and point the arrow at that. Record it
in `migration-map.json`'s `arrow_aliases`.

## Two shapes worth naming, both seen in real policies

**A relation whose target is the actor type, used as a role source.** `relations = {
shared_with: User }` with `"viewer" if "shared_with";` is a relation, not a role -- but it
appears on the right of a shorthand rule exactly where a role would. It converts directly:
`relation shared_with: user`, then include it in the union like any role. Verified against a
real policy; no special handling needed, but it is easy to misread as a role and then look
for `has_role` facts that do not exist. The facts are `has_relation`.

**A tautological rule.** `"owner" if "owner";` grants a role from itself and is a no-op.
Emit nothing for it. It is not an error and not a finding -- policies accumulate these --
but do not let it produce a self-referential permission, which SpiceDB rejects.

## Global blocks

```polar
global {
  roles = ["operator", "staff"];
  permissions = ["register_instrument"];
  "register_instrument" if "staff";
  "staff" if "operator";
}

resource Lab {
  roles = ["steward"];
  "steward" if global "operator";
}
```

SpiceDB has no global scope. Use the conventional Zanzibar answer: a singleton definition,
one object, referenced by an arrow.

```zed
definition platform {
    relation operator__direct: user
    relation staff__direct: user
    permission operator = operator__direct
    permission staff = staff__direct + operator
    permission register_instrument = staff
}

definition lab {
    relation platform: platform
    relation steward__direct: user
    permission steward = steward__direct + platform->operator
}
```

Every object of every type carrying a `global` reference needs a `system` edge written --
that is a **data obligation**, not just a schema one, and it is easy to miss because
nothing in the Polar source looks like a relationship. Record it under `sync_obligations`.
Pick the singleton's object id at the gate and record it; `system:system` is a reasonable
default but it is a decision, not a fact.

## Negation

Polar's `not` over a fact becomes SpiceDB exclusion. Parenthesise it -- SpiceDB's exclusion
binds tighter than reading order suggests, and an unparenthesised mix of `+` and `-`
validates while meaning something other than the Polar it came from:

```polar
"download" if "analyst" and not is_embargoed(resource);
```

```zed
permission download = (analyst - embargoed)
```

`archived` is a marker relation, which makes this an `effort` row rather than `clean` --
the marker is data someone now has to write and unwrite. See `blockers.md`'s unary-fact
entry.

## Free-standing rules

Everything above is the declarative core. Real policies also carry rules written directly
against the predicates -- **and they come in two spellings, so grep for both.**

`has_permission(actor, "read", resource)` is the one the documentation leads with.
`allow(actor, "read", resource)` is the top-level entry point Oso's own test blocks assert
against (`assert allow(...)`), and a policy may define its rules entirely in terms of it:

```polar
allow(actor: Actor, "download", ds: Dataset) if
    is_open_access(ds, true) and
    has_role(actor, "guest", ds);
```

Verified on a real third-party policy whose **every** free-standing rule is an `allow` rule
and which contains the string `has_permission` zero times. **An agent that greps only for
`has_permission` will report that policy as having no free-standing rules, when it has
eight** -- and will translate the declarative core while silently dropping every permission
the policy actually grants. Treat `allow` and `has_permission` as the same construct for
conversion purposes; both fold into the target permission's union.

`has_role` and `has_relation` also appear on the left of free-standing rules, granting a
role or an edge by rule rather than by fact. Those fold into the role's union and the
relation's type set respectively.

```polar
has_permission(_: Actor, "download", ds: Dataset) if is_open_access(ds);
```

Classify each by what appears on the right:

- **Only roles, relations, and `on` hops** -- fold into the permission's union. `clean`.
- **A unary fact** -- marker relation, wildcard, or caveat. `effort`.
- **A fact with a value argument** -- caveat context. `effort` (3-ary) or `heavy` (higher).
- **A variable in the role or permission position** -- `heavy`. See `blockers.md`.
- **A comparison binding two rule variables** -- `blocked`. See `blockers.md`.

**Self-reference is `clean`, and it needs the `use self` flag.**
`has_permission(user: User, "view_own_record", user: User)` -- the same variable in both
positions -- is "users can read their own profile". SpiceDB expresses it directly:

```zed
use self

definition user {
    permission view_own_record = self
}
```

Verified on zed v1.2.0 against a live SpiceDB v1.56.0: this validates, writes, and answers
`true` for a subject against itself and `false` against another, **with no relationship
written**. That is why it is `clean` rather than `effort` -- there is no per-user data
obligation.

**Check the `zed` version before concluding otherwise.** On zed v0.31.1 the flag is rejected
as unknown and a bare `self` validates locally and then fails at `WriteSchema` -- the server
supported `self` all along and the client could not express it. `schema-mapping.md`'s Flags
discussion carries the detail.

**A rule with no body is a universal grant, and it is the easiest rule in the file to lose
entirely.** `has_permission(_: User, "browse", _: Lab);` grants every user browse on
every lab. It becomes a wildcard relation plus one edge per object:

```zed
definition lab {
    relation public_browser: user:*
    permission browse = public_browser
}
```

The schema half is trivial; the data half is one relationship per object, written and
maintained. **Verified the hard way:** a hand conversion of `gitcloud` omitted the `read`
permission altogether, and the omission survived review because these rules sit apart from
the resource blocks and look like commentary. A live differential caught it as 5 identical
disagreements -- every actor, including one with no facts. Grep for `has_permission` and
`has_role` rules with no `if` before declaring a policy converted.
