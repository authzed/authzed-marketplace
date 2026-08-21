# Blocker catalog: Oso Cloud

Pack contract item 4. Every `heavy` and `blocked` construct from `policy-mapping.md`, with
a detection rule and the concrete options to put to the user. These are what turn into
Class A findings at the gate.

Count the `## <n>.` sections below rather than trusting a number stated anywhere; this
catalog is added to as new shapes are found.

**One artifact predicts most of the cost.** `GET /policy` is capped at 1 MB and returns the
complete source. Grep it for: `declare` statements with arity above three or a non-object
final argument; a `Role`-typed argument to `has_role`; a variable in the role position; and
comparison operators binding two rule variables. That plus "do you call `listLocal`
anywhere?" predicts nearly all of the migration cost, and both are obtainable in the first
call. See `../SKILL.md` and the scoping questionnaire in `pack-contract.md` item 9.

---

## 1. Unary facts (attributes)

**Rating: `effort`.** Not a blocker, listed first because it is the **largest cost line in
a typical Oso migration** and the one most often missed at scoping.

**What it is.** A one-argument fact standing for a property of a resource:
`is_open_access(Dataset{"atlas"})`, `is_embargoed(...)`, `is_finalized(Run{"7"}, false)`.

**Detection.**

```bash
# every unary predicate in the policy, with its use count
grep -nE '^\s*(declare\s+)?[a-z_]+\([^,)]+\)\s*;?' policy.polar
grep -c 'is_' policy.polar
```

Read `GET /policy_metadata` for declared predicates, then grep the source for use.

**Why it costs.** In Oso an attribute is a fact the customer already syncs. In SpiceDB it
becomes a relationship, so it inherits a write path: something must write it on create,
rewrite it on change, and delete it on delete. There is **no maintained AuthZed tooling for
continuous sync from a customer's database** -- `authzed/connector-postgresql` is archived
and its own README warns it should not be run in production. Today the customer writes this.

**Options:**

| Option | Cost |
|---|---|
| Marker relation (`relation archived: system`) | Simplest, greps well, one tuple per flagged object. Adds a sync obligation per attribute. |
| Wildcard edge (`relation public: user:*`) | Natural for "everyone" attributes. Same sync obligation. |
| Caveat with request context | Right when the attribute is genuinely request-scoped rather than stored. Removes the sync obligation, but the caller must now supply it on every check. |

**The encoding choice is not cosmetic: it swings list latency substantially.** Do not pick
one silently -- put it to the user per attribute, and record the count of unary facts in
the plan, because that count *is* the estimate.

**Record** each attribute under `sync_obligations` with its chosen encoding.

---

## 2. An open-ended permission vocabulary

**Rating: `heavy`.**

**What it is.** The customer's application invents new permission names at runtime -- a
tenant-configurable role editor, say. Permission names in SpiceDB are **schema
identifiers**, so a new name means a new schema.

**Detection.** A permission name that reaches Oso as a variable rather than a literal, or
an application table of permission names:

```bash
grep -rnE 'grants_permission|permission_name|custom_permission' --include='*.polar' .
grep -rnE 'authorize\([^,]+,\s*[a-z_]+\s*,' .   # action argument is a variable, not a string
```

**Why it is heavy rather than blocked.** Codegen closes it: regenerate the schema and
write it whenever the vocabulary changes. That works -- but it makes schema writes part of
the application's hot path for tenant configuration, and **schema writes are global, not
per-tenant**, so one tenant's role editor rewrites the schema every other tenant reads.

**Options:**

| Option | Cost |
|---|---|
| Fix the vocabulary | Oso's own best-practice guidance keeps this set small and fixed, so many customers can. Cheapest by far if acceptable. |
| Codegen the schema | Works, verified. Schema writes enter the application's write path and are global. |
| Keep the permission check in the application | SpiceDB answers the role question; the application maps role → permitted actions. Loses a single source of truth. |

**Ask during scoping.** Many customers never hit this.

---

## 3. A variable in the role position

**Rating: `heavy`.** Closely related to 2, and it is what most "customer-defined roles"
policies actually reduce to.

**What it is.**

```polar
has_role(actor: Actor, role: String, ds: Dataset) if
    lab matches Lab and
    has_relation(ds, "lab", lab) and
    has_default_role(lab, role) and
    has_role(actor, "staff", lab);
```

`role` is bound by a fact, not written literally. SpiceDB relation names are static.

**Detection.** A `has_role` whose second argument is a variable, or is typed `Role`:

```bash
grep -nE 'has_role\([^,]+,\s*[a-z_][a-z_0-9]*\s*,' policy.polar   # 2nd arg not quoted
grep -nE 'has_role\([^,]+,\s*[a-z_]+:\s*Role' policy.polar
```

**Why it is heavy rather than blocked.** Most of the customer-defined-role story maps as
*data*: role objects become a definition, and user→role and role→permission grants become
relationships and subject sets. What does not map is the *variable*, and codegen closes it
-- one relation plus a union per role, emitted per known role.

**Options:** as for 2, plus:

| Option | Cost |
|---|---|
| Enumerate the roles and generate a union per role | Verified. Works whenever the *set* of roles is knowable at schema-write time even if assignment is dynamic. |

---

## 4. Fact arity above three, or a value in the final position

**Rating: `heavy`** (arity 4-5) / **`effort`** (3-ary with a trailing value).

**What it is.** An Oso fact is a predicate with **one to five** arguments. Each argument is
either a typed object reference (`User{"alice"}`) or a literal (`"admin"`, `42`). A SpiceDB
relationship has exactly three positions and the middle one is a static schema identifier.

Arity alone does not predict difficulty. What matters is **how many arguments are object
references, and whether a literal can become a relation name**:

| Oso fact shape | Example | SpiceDB |
|---|---|---|
| 1-ary, object | `is_open_access(Dataset)` | No subject at all -- marker or wildcard edge (see 1) |
| 2-ary, object + object | `has_relation(Dataset, Lab)` | Direct relationship. Clean |
| 2-ary, object + literal | `has_tier(Lab, "basic")` | Literal becomes the relation name |
| 3-ary, object + literal + object | `has_role(User, "steward", Lab)` | The canonical case; maps exactly |
| 3-ary, object + object + value | `tier_allowance(Tier, Metric, 10)` | The value goes into caveat context |
| 4- and 5-ary | — | Reification: a synthetic type carrying the extra arguments |

The third row is why Oso feels close to Zanzibar: the role string does exactly the job a
relation name does. **Most real Oso facts are this shape.**

The fifth row survives intact, with the value on the edge:

```zed
definition metric {}

caveat allowance_at_least(allowance int, requested int) {
    requested <= allowance
}

definition tier {
    relation allowance: metric with allowance_at_least
}
```

```
tier:standard#allowance@metric:storage[allowance_at_least:{"allowance":10}]
```

Genuine 4- and 5-ary facts need a synthetic object to hang the extra arguments from -- one
new definition and one tuple per fact instead of one tuple. That multiplies relationship
count and makes the schema markedly harder to read, which is why it is `heavy`.

**Detection.** Read `declare` statements; do not infer arity from a predicate's name.
**Arity is policy-defined** -- the same predicate name is unary in one real policy and
binary in another.

```bash
grep -nE '^\s*declare\s+[a-z_]+\(' policy.polar
```

**Worth knowing before you estimate:** the documentation contains no 4- or 5-ary example.
The type system permits them and the docs' own examples top out at three, so this may be a
capability few customers use. Check the customer's `declare` statements rather than
assuming either way.

---

## 5. Comparison across two stored facts

**Rating: `blocked`.**

**What it is.** Oso's entitlements pattern compares two independently-fetched fact values:

```polar
has_storage_remaining(lab: Lab, tier: Tier) if
    has_allowance(lab, tier, allowance) and
    storage_used(lab, tier, used) and
    used < allowance;
```

**Why it is blocked.** A caveat compares *stored context against request context*. It
cannot read another relationship, and it cannot aggregate.

**Detection.** A comparison operator whose both operands are rule variables bound by
separate fact lookups:

```bash
grep -nE '[a-z_]+\s*(<|>|<=|>=|==|!=)\s*[a-z_]+\s*;' policy.polar
```

Then read each hit: a comparison against a *literal* is fine and usually becomes caveat
context; a comparison between two **variables** is this blocker.

**Options:**

| Option | Cost |
|---|---|
| Move the comparison into the application | Store one side as caveat context and pass the other per request. Verified to work -- but SpiceDB is no longer answering the question on its own. |
| Precompute the predicate and store it as a relationship | Whoever changes either value must recompute. A real sync obligation, and it can be stale. |
| Keep this check in Oso, or in the application, and migrate the rest | Often the right answer. Say so plainly rather than forcing it. |

---

## 6. Multi-variable query (Query Builder)

**Rating: `blocked`.**

**What it is.** Oso's Query Builder (`/evaluate_query`) answers conjunctive queries
returning bindings for **several variables at once**, with cross-products. SpiceDB's read
APIs answer one question shape at a time: resources for a subject, or subjects for a
resource.

**Detection.**

```bash
grep -rnE 'evaluate_query|evaluate\(|buildQuery|QueryBuilder' --exclude-dir={.git,node_modules,vendor} .
```

**Options:** the join moves into the application, with the fan-out cost that implies.
There is no schema-side answer. Size it by counting the call sites and the arity of each
query.

---

## 7. `listLocal` / `authorizeLocal` (local filtering)

**Rating: `blocked`.** Treat it as a **qualifying question**, not a blocker, because many
Oso customers never touch it.

**What it is.** Oso compiles the policy into a SQL fragment the application drops into its
own `WHERE` clause. Authorization becomes part of the customer's existing query -- correct
pagination, correct counts, correct joins, one round trip.

**SpiceDB never returns SQL, and there is no equivalent in the current architecture.**

**Detection.**

```bash
grep -rnE 'list_local|listLocal|authorize_local|authorizeLocal|list_query|authorize_query' \
  --exclude-dir={.git,node_modules,vendor,dist,build,target,.venv,__pycache__} .
```

**Three outcomes:**

| Finding | What follows |
|---|---|
| Not used | The migration is materially easier. Proceed. |
| Used on small collections | `LookupResources` + an `IN` clause, accepting the semantics changes in `code-mapping.md` |
| Used on large collections with sort and pagination | SpiceDB Materialize, or the migration is at risk. Say so plainly. |

**The restriction that matters here.** Materialize forbids caveats, wildcards, and `.all()`
on a materialized permission path. Wildcards are a recommended encoding for the attributes
in 1, and caveats are the recommended encoding for request context -- so **the customer
cannot have ABAC and Materialize-backed filtering on the same permission.** For an Oso
customer, whose policy is likely to mix both, that restriction lands exactly where it
hurts. Do not discover it at phase 4.
