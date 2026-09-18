# Data mapping: Oso facts → SpiceDB relationships

Pack contract item 6. How to extract Oso Cloud's facts and transform them, plus which
conversions create ongoing sync obligations.

## Extraction is possible, and awkward. Plan for it.

Policy export is trivial: `GET /policy` and `GET /policy_metadata`, both cheap, both
usually in the customer's git already.

**Fact export is the awkward part.** Oso documents an export path and states that you own
your facts, but four frictions are real and all of them change the plan:

- **There is no "list predicates" endpoint.** You must know every predicate name up front.
  Recover them from the policy -- which is why `policy-mapping.md` says to pull the policy
  before touching data.
- **`GET /facts` is absent from the OpenAPI spec** even though every SDK's `get()` uses it,
  and **no SDK paginates it**. Large environments have to shard the export by argument
  value.
- **Developer accounts are capped at 1000 facts per call**, and a larger export errors
  rather than truncating. Verify the tier before sizing the export.
- **There is no bulk-export endpoint, no streaming, and no watch/CDC API.** Write webhooks
  are a paid tier and Oso's own docs disclaim them as unsuitable for archival, so they are
  not a substitute for a snapshot.

**Use point-in-time recovery.** Oso supports restoring into a new environment. That yields
a frozen snapshot to export from at leisure instead of racing a live system during
cutover, and it is the single most useful operational trick in an Oso data migration.

## The CLI shape

```bash
oso-cloud get <predicate> <arg1> <arg2> <arg3>
```

Each argument is a wildcard (`_`) or a typed value. Output is typed tuples:

```
has_role(User:alice, String:steward, Dataset:atlas)
has_relation(Dataset:atlas, String:lab, Lab:genomics)
```

Every argument is a `{type, id}` pair on the wire, so primitives travel as pseudo-types --
`{type:"Boolean", id:"true"}`, `{type:"Integer", id:"42"}`. **A `String:` argument is
usually a relation name, not data**; that is the whole reason the 3-ary shape maps so well.

## The transform

Per `blockers.md`'s arity table:

| Fact shape | Becomes |
|---|---|
| `has_role(User:alice, String:steward, Lab:gx)` | `lab:gx#steward__direct@user:alice` |
| `has_relation(Dataset:atlas, String:lab, Lab:gx)` | `dataset:atlas#lab@lab:gx` |
| `is_open_access(Dataset:atlas)` | a marker or wildcard edge -- see the encoding decision below |
| `has_tier(Lab:gx, String:basic)` | `lab:gx#tier_basic@...` (literal becomes the relation) |
| `tier_allowance(Tier:std, Metric:storage, Integer:10)` | edge with caveat context `{"quota":10}` |
| arity 4-5 | a synthetic object per fact, then edges to it |

**Target the relation side of a split.** A `has_role` fact writes to the `__direct`
relation, never to the permission -- SpiceDB rejects a write to a permission outright.
Read the name from `migration-map.json`'s `relation_splits`; never append the suffix by
hand.

**Oso refuses a fact whose shape the policy does not use.** Inserting one returns a 400:
`has_role(User, "steward", Lab) is not used in policy. Help: use this fact in your
policy, or add 'declare has_role(...)' to your policy to force Oso to accept facts of this
type.` Two consequences for a migration: the source store cannot contain a fact shape the
policy has no rule for (unless it was `declare`d), so the predicate inventory recovered from
the policy is more complete than it first appears; and if you replay facts into a *second*
Oso environment during dual-run, load the policy first or every insert fails.

**Drop facts the policy never reads.** Oso environments accumulate predicates that no rule
mentions. Migrating them costs storage and review attention and buys nothing. Diff the
predicate list against the policy and report the difference rather than silently importing
it.

## Sync obligations this conversion creates

This is the section to read before estimating. Record each under `sync_obligations`.

**Every unary fact becomes a sync obligation.** In Oso the customer already syncs it; the
migration does not remove that work, it moves it. But there is **no maintained AuthZed
tooling for continuous sync from a customer's database** -- `authzed/connector-postgresql`
is archived, and its own README warns it should not be run in production. Today the
customer writes this themselves. Say so at the gate; it is usually the largest line in the
estimate and it is invisible in the schema diff.

**Context facts that were edges become stored relationships, and this is measured, not
argued.** Oso lets a request carry facts that vanish afterwards. Where those facts are
genuinely request-scoped, a caveat with request context preserves the shape. Where they are
**edges** the application recomputed each time, they must be written, updated, and deleted --
a new write path that did not exist before.

Verified against a real application that passes its issue hierarchy as context facts on
every call -- `has_relation(Issue, "repository", Repository)` and
`has_role(User, "creator", Issue)`, both recomputed from its database per request. Same
policy, same stored data, three ways:

| Question | Oso, context facts passed | SpiceDB, edges **not** written | SpiceDB, edges written |
|---|---|---|---|
| org admin reads the issue | allowed | **denied** | allowed |
| org admin closes the issue | allowed | **denied** | allowed |
| issue creator closes it | allowed | **denied** | allowed |
| creator reads it (no path) | denied | denied | denied |

**Three of four answers are wrong until the edges are written, and the conversion cannot
detect that on its own** -- the schema is correct, the stored data is correct, and every
failure is a *denial*, so nothing errors and no check looks broken. It fails closed, which
is the safe direction and the hard one to notice. Count the context facts an application
passes and record each as a sync obligation before phase 4, not after.

**Global blocks need an edge per object.** See `policy-mapping.md`. Nothing in the Polar
source looks like a relationship, so this is easy to miss entirely.

## Loading

`ImportBulkRelationships` handles volume fine; the import is the easy half. Verify at the
three levels `migrate-data.md` prescribes, and note its wildcard carve-out: a public grant
encoded as `user:*` cannot be probed with a permission check, because SpiceDB rejects `*`
as a check subject. Use an existence read for those rows.
