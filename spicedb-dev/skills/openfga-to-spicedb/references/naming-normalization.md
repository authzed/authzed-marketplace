# Naming Normalization: OpenFGA → SpiceDB

Two different constraints, so two different strategies: **names** (types, relations,
permissions, caveats) get deterministic mangling; **object IDs** get base64url.

Verified against SpiceDB v1.56.0 / zed v0.31.1 unless a line says otherwise.

**Scope note.** This file covers only whether a name is *legal* -- character set, length,
reserved words, collisions. Whether a permission's name is *styled* as a noun or a verb is a
separate, non-mechanical gate decision, not part of `normalize_name` below --
`schema-mapping.md`'s "Permission naming style" section covers it. `normalize_name` never
changes what a name means, only whether it compiles; the noun/verb decision can change a
renamed permission's meaning to a reader, which is exactly why it is a decision the user
makes rather than an algorithm this file runs unconditionally.

## The rule sets

| Kind | SpiceDB rule |
|---|---|
| Definition / relation / permission names | `^[a-z][a-z0-9_]{1,62}[a-z0-9]$` -- lowercase, digits and `_` only, **minimum 3 characters**, maximum 64, no leading or trailing underscore. Enforced: a 2-character relation name fails with ``invalid Relation.Name: value does not match regex pattern`` |
| Caveat names | **Looser than the above** -- see below |
| Object IDs | `^[a-zA-Z0-9/_\|\-=+]{1,1024}$` (see the Go note below this table) -- mixed case allowed, but no `@`, no `.`, no `*` (except the bare wildcard `*`) |


**Do not transcribe this pattern into Go.** Go's RE2 rejects any repeat count above 1000, so
`regexp.MustCompile` on `{1,1024}` **panics at package init** -- and `go build` and `go vet`
both pass, so `migrate-code.md` step 7's build check does not catch it; only running the code
does. This is a property of the regex engine, not of the pattern: the grammar below is the
correct specification of a SpiceDB object ID. In Go (and any other RE2-based engine) express
it as an unbounded character-class match plus a separate length test:

```go
var objectIDChars = regexp.MustCompile(`^[a-zA-Z0-9/_|\-=+]+$`)

func validObjectID(s string) bool {
    return len(s) >= 1 && len(s) <= 1024 && objectIDChars.MatchString(s)
}
```

PCRE-based engines (Python, Java, .NET, Ruby, JavaScript) accept the bounded form as printed.


### Caveat names are not bound by the name regex

The name regex governs definition, relation, and permission names. Caveat names are only
required to lex as identifiers. Verified accepted at both `zed validate` and
`WriteSchema`: `caveat c(...)` (1 character), `caveat Cav(...)` (uppercase),
`caveat _cav(...)` (leading underscore), `caveat 1cav(...)` (leading digit), and a
303-character name. Rejected: `-` and `.` in the name (they do not lex).

**Normalize caveat names anyway.** Not because the compiler demands it, but because the
relationship-string grammar does. A caveated relationship is written
`<res>#<rel>@<subj>[<caveat>[:<json>]]`, and that parser **does** enforce the strict name
regex -- verified: with `caveat cav`, `doc:d1#viewer@user:alice[cav:{"a":1}]` parses and
the assertion runs; with `caveat c`, `caveat MyCav`, `caveat _cav`, or `caveat 1cav`, the
identical relationship string fails with `invalid relationship string`. So a schema using
a loose caveat name compiles, deploys, and then cannot have a caveated relationship
written against it in string form -- which is exactly what phase 3 and phase 5 emit.

Two consequences worth stating at the gate:

- Normalizing a caveat name that the compiler would have accepted is a **conservative
  choice with a real justification**, not a requirement of the schema language.
- Every unnecessary rename still costs something: it becomes a user-facing Class B finding
  and it has to be applied to every caveat reference in phases 3 and 5. Prefer leaving a
  name alone when it already satisfies the strict regex.

Confirmed by `condition-data-types` (the first corpus store with any caveats): all nine of
its condition names (`is_valid_string`, `is_valid_uint`, ...) already satisfy the strict
`^[a-z][a-z0-9_]{1,62}[a-z0-9]$` regex, so none needed renaming -- a live example of "prefer
leaving a name alone," not a counter-example to it.

This rule is about the caveat's own top-level name only. Caveat **parameter** names are a
different, looser case still: they are plain CEL identifiers inside the schema, never
appear in the relationship-string grammar at all (context is a JSON object, and JSON keys
aren't lexed as identifiers), and OpenFGA idiomatically prefixes them with `_`
(`_string`, `_uint`, ...) -- which the codegen rule banning leading-underscore identifiers
elsewhere in this pack does not reach. See "Caveat parameter names are not identifiers
anyone renames" in `schema-mapping.md` for why they should be left alone anyway, for a
reason specific to parameters and unrelated to this section's name-regex argument.

## What OpenFGA allows that SpiceDB rejects

All verified against the real compiler.

**Names:**

| OpenFGA | Why SpiceDB rejects it |
|---|---|
| `type User` | uppercase |
| `type My-Doc` | hyphen |
| `type a/b`, `type a.b` | slash, dot |
| `type u` | under the 3-character minimum |
| `type _hidden` | leading underscore |
| `type relation`, `type model` | keywords used as identifiers -- but only *some* of these actually collide; see below |

**Object IDs:** anything containing `@`, `.`, `%`, or `*` -- the grammar above is the
authority, and this shortlist is only the common cases. **`%` is on it because of a trap that
looks like the opposite of a problem:** an application that escapes its own identifiers before
storing them can turn a *legal* character into an illegal one. One production project's own `escape()` helper
replaces `/` with `%2F` on every object
element -- but `/` is legal in a SpiceDB object ID and `%` is not, so escaping is what breaks
it. Verified on v1.56.0: `image_alias:default/a/b` is accepted, `image_alias:default%2Fa%2Fb`
is rejected with `invalid relationship string`. Look for the application's own
escaping/encoding helpers, not only for raw identifiers -- a percent-encoding, a URL escape,
or a slug function applied on the way in will not appear in any fixture and is invisible to a
sweep that only reads literals. **Emails as subject IDs are the
common case** -- `user:alice@corp.com` is rejected outright by SpiceDB
(`invalid relationship string`).

### Reserved words

`type` and `model` are OpenFGA keywords, not SpiceDB ones, so they survive normalization
fine. The words that actually break are SpiceDB's own. Verified by writing each as both a
definition name and a relation name against a real server:

| Rejected as an identifier | Accepted as an identifier |
|---|---|
| `relation`, `permission`, `definition`, `caveat`, `nil`, `with` | `use`, `type`, `model`, `schema`, `module`, `extend`, `expiration`, `self`, `all`, `any` |

Two caveats on the accepted column: `self` is only an ordinary identifier while
`use self` is absent, and `expiration` as a *caveat* name silently shadows native
expiration (see `schema-mapping.md`). Treat both as reserved when the schema uses those
features.

**The normalization algorithm does not escape reserved words** (see "What normalization
does not do"). Rename them on the input side, before normalizing.

## The algorithm

**This document is the algorithm.** Apply the numbered steps below directly -- there is no
tool to call, and this pack ships no code.

A reference implementation, `normalize_name`, exists in the plugin's source repository at
`tools/migration-harness/src/migration_harness/idmap.py` (see `SKILL.md`, "The parity
harness is not part of this plugin"). It is not shipped and not required, but it is
covered by tests, so **the two must not diverge**: whoever changes one changes the other,
and the harness's tests arbitrate. Do not paraphrase these steps into a different
algorithm at conversion time -- data written under one name and checked under another
fails silently.

Given a source name:

1. Compute `digest = sha256(<raw name>)[:6]` -- of the **raw** input, before any other
   step, so the suffix stays tied to the original.
2. Lowercase.
3. Replace each `-`, `.`, and `/` with `_`.
4. Delete every remaining character outside `[a-z0-9_]`.
5. Strip leading and trailing `_`.
6. If the result is empty, use `x` + `digest`. If it merely starts with a digit, prefix
   `x`.
7. If longer than 64 characters, truncate to 57 and append `_` + `digest` -- exactly 64
   characters, and distinct over-long inputs stay distinct.
8. While shorter than 3 characters, append `0`.

Worked outputs, produced by running the implementation:

| Source | SpiceDB | Rule |
|---|---|---|
| `User` | `user` | 2 |
| `My-Doc` | `my_doc` | 3 |
| `a/b`, `a.b` | `a_b` | 3 (both -- a collision, see below) |
| `_hidden` | `hidden` | 5 |
| `trailing_` | `trailing` | 5 |
| `123abc` | `x123abc` | 6 |
| `!!!` | `xe84c53` | 6 (nothing survived step 4) |
| `u` | `u00` | 8 |
| `A`×70 | `aaaa…aaa_01d3a1` (64 chars) | 7 |
| `user` | `user` | unchanged |

**First real-corpus confirmation of the 3-character minimum (rule 8).** Every row above
was produced by running the implementation against a synthetic or hand-built input, not a
real OpenFGA type name from a corpus store, until `openfga/sample-stores/stores/ads`: its
`type ad` is a genuine 2-character type name (`ad:banner-001` is the store's own object
naming), and `zed validate` rejects the untransformed `definition ad {}` outright
(``invalid NamespaceDefinition.Name: value does not match regex pattern``). Applying rule 8
mechanically (`ad` → `ad0`) resolves it with no further change -- confirmed end to end via
`zed validate --fail-on-warn` and the migration harness reaching `PARITY OK` on the renamed
schema. No new rule; this is the first live-model instance of a rule the worked-outputs
table above had only demonstrated synthetically (`u` → `u00`).

Properties, confirmed by fuzzing the implementation over 20,000 random inputs: every
output matches SpiceDB's name regex, and the function is **idempotent** -- normalizing an
already-normalized name returns it unchanged.

## What normalization does not do

Three gaps. Each has to be handled by the caller, before or after `normalize_name`.

### 1. It is not collision-resistant

Distinct inputs collapse: `can-edit` and `can_edit` both give `can_edit`; `MyDoc` and
`mydoc` both give `mydoc`; `a.b` and `a/b` both give `a_b`. A collision that reaches the
schema silently merges two relations into one, which is a correctness bug, not a cosmetic
one.

Collision resolution is a **batch** property, so it lives in
`IdMap.build(types, relations)` (via `_disambiguate`), not in `normalize_name`:

- **Two namespaces, not one -- and a caveat name is in neither of them.** Type names are
  disambiguated against a single global registry, because SpiceDB definition names share one
  namespace. Each type's relation and permission names are disambiguated against a registry
  **scoped to that type alone**, because SpiceDB only requires those names to be unique
  *within* one definition. Two definitions may each have a `viewer`; that is not a collision
  and must not be disambiguated as one. Caveat names are declared at the schema's top level,
  same as definitions, but SpiceDB keeps them in their **own** global registry, separate from
  both -- a relation or permission may legally share a spelling with a caveat name with no
  collision at all. Corpus-confirmed on `openfga/sample-stores/stores/banking`: its source
  model names a relation and its own governing condition identically
  (`define transfer_limit_policy: [... with transfer_limit_policy]`,
  `condition transfer_limit_policy(...)`), and the mechanical translation carries this over
  unchanged (`relation transfer_limit_policy: bank#customer with transfer_limit_policy | ...`,
  `caveat transfer_limit_policy(...)`) -- verified to compile, deploy via `WriteSchema`, and
  resolve correctly with no name-collision error of any kind. No corpus store had reused an
  identifier across the caveat namespace and a relation/permission namespace before this one
  (`grep`-verified against every other committed `schema.zed`). Do not run a caveat name
  through the same per-type disambiguation registry as that type's relations and
  permissions -- it is a batch collision check against the *caveat* registry alone.
- **First occurrence wins.** In input order, the first name to claim a normalized form
  keeps the clean form. A later name that would collide becomes
  `<normalized>[:57] + "_" + sha256(<its own raw name>)[:6]`. Deterministic, and stable
  across runs given a stable input order.
- Example: `_disambiguate(["can-edit", "can_edit"])` →
  `{"can-edit": "can_edit", "can_edit": "can_edit_7d7be7"}`.

Every collision is a **Class B finding**: mechanical, but it changes stored data, so the
user must see and own it at the gate.

### 2. Duplicate raw names corrupt the registry

`_disambiguate`'s registry is keyed by the raw name, so **a raw name appearing twice in
the same input list overwrites the first occurrence's mapping**, and the clean form is
lost entirely:

```python
_disambiguate(["viewer", "viewer"])   # -> {"viewer": "viewer_d35ca5"}
```

Valid OpenFGA never produces this -- type names are unique, and relation names are unique
within a type -- so it only fires on a caller bug: passing the same type twice, or
flattening relations from several types into one list. **Deduplicate the input, preserving
first-occurrence order, before calling `build`.** The clean name silently turning into a
hash-suffixed one is the symptom.

### 3. It does not escape reserved words

`normalize_name("relation")` returns `relation`, which SpiceDB rejects as an identifier.
Handle this on the input side: rename the source name before normalizing (e.g. `relation`
→ `relation_type`), feed the renamed value through the normal path so collision detection
still covers it, and record the rename in the identifier map like any other. Raise it as a
Class B finding -- the user is entitled to know a name in their model changed meaning.

## Object IDs → base64url

Names get mangled; IDs get encoded. Base64 is reversible and collision-free by
construction, which mangling is not, and both standard base64 (`+`, `/`, `=`) and
base64url (`-`, `_`, `=`) land entirely inside SpiceDB's object-ID charset with **no
post-mangling needed**. Verified: `user:YWxpY2VAY29ycC5jb20=` (base64url of
`alice@corp.com`, padding included) is accepted by a real server, while
`user:alice@corp.com` is rejected.

Size is not a concern: OpenFGA caps `object` at 256 bytes, so 4/3 inflation tops out near
344 -- well under SpiceDB's 1024.

Rules, as implemented by `encode_id`:

- Modes are `"none"` (pass through) and `"base64url"`. Anything else is an error.
- **The wildcard subject ID `*` is never encoded**, whatever the mode.
- Encoding is per source type: `id_encoding.types` lists which types get encoded; a type
  not listed passes its IDs through unchanged even under `"base64url"`.
- An empty ID, or an encoded form exceeding 1024 characters, is a **hard error**. Never
  truncate -- truncation breaks reversibility, which is the entire reason for choosing
  base64.

**Gate options** (a Class B decision, recorded in `migration-plan.md`):

1. base64url-encode all IDs of an affected type -- uniform, trivially reversible, and
   unreadable in the console.
2. Encode only the violating values -- keeps clean IDs readable, at the cost of a mixed
   representation that every reader and writer must handle.
3. A user-supplied mapping function -- when the application already has a stable internal
   ID it would rather use.

**Only options 1 and "do nothing" are machine-representable today.** `migration-map.json`'s
`id_encoding.mode` accepts `"none"` or `"base64url"` and rejects anything else on load
(`findings-report.md`'s `id_encoding` section), so options 2 and 3 have nowhere to be
recorded as a `mode` and nothing downstream implements them: the emitted codec encodes a type
or does not. If the user picks either, say plainly that the pipeline cannot carry it, then
record **`mode: "none"` together with `status: "unresolved"` and a populated `violations`
list** -- `status` is what stops a later phase reading that `none` as "the identifiers are
fine," which is the one way this decision turns into a runtime outage. File the real choice
under **Deferred / manual -> Needs action** with the affected types and the reason: the codec
they need is theirs to write, phase 3 must not load until it exists, and phase 4 must not
point converted code at SpiceDB. Do not record `base64url` as an
approximation of option 2; it encodes every value of the type, which is a different and
irreversible decision once data is loaded.

## One codec, two consumers

Data (phase 3) and code (phase 4) **must encode identically**, or the migration silently
half-works: relationships stored under `base64url(email)` while the application still
checks `user:alice@corp.com`. Everything downstream is generated from the identifier map
in `migration-plan.md` / `migration-map.json`, so there is exactly one source of truth.
Phase 3 emits a small codec module into the project; phase 4 rewrites call sites to route
through it rather than inlining encoding at each site.

Phase 1's job is to produce that map -- see `/spicedb-dev:migrate-schema`.
