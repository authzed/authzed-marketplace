# Code mapping: Oso Cloud SDK → SpiceDB client

Pack contract item 7. Oso's client surface, what each call becomes, and where the
semantics change rather than the name.

Oso ships SDKs for Node.js, Python, Go, Ruby, Java, and .NET. The v2 SDKs added a query
builder and simplified fact management, so a codebase may contain more than one generation
-- check the SDK version before assuming a call shape.

**Fact literals changed shape between SDK generations.** Current Python clients take facts
as **tuples** -- `("has_role", Value("User","alice"), "steward", Value("Lab","gx"))` -- and
raise `TypeError: Oso: expected tuple, found <class 'dict'>` on the older dict form
(`{"name": ..., "args": [...]}`) that shipped applications still contain. When converting,
read the call site's actual form rather than assuming; and when running the *source* side
during dual-run, pin the SDK version the application uses, or context facts it builds as
dicts will fail before they are ever compared.

**Enumerate the SDK's surface rather than working from this table alone.** The table below
was built by listing the client's public methods mechanically, and an earlier draft written
from the documentation was missing six of fifteen. In Python:

```python
import inspect
from oso_cloud import Oso
print(sorted(m for m, _ in inspect.getmembers(Oso, inspect.isfunction) if not m.startswith("_")))
```

A method absent from this table is a gap in this table, not a call with no consequence.

**The surface is the same across SDKs, but the casing is not -- and a case-sensitive sweep
finds nothing.** Enumerated from both clients: Python exposes 15 public methods in
`snake_case` (`authorize_local`, `list_paginated`, `get_policy_metadata`), Node exposes the
same 14 in `camelCase` (`authorizeLocal`, `listPaginated`, `getPolicyMetadata`). The only
difference beyond casing is Python's `for_agents`, which Node does not have. Both were
verified working against the local dev server, returning identical answers for the same
policy and facts.

So any detection sweep must be case-insensitive or spell both forms:

```bash
grep -rniI 'authorize_local\|authorizelocal\|list_local\|listlocal\|actions_local\|actionslocal' \
  --exclude-dir={.git,node_modules,vendor,dist,build,target,.venv,__pycache__} .
```

That matters most for the `blocked` local-authorization constructs: a sweep written for
`list_local` alone returns zero on a Node codebase that calls `listLocal` everywhere, and
the migration then reports its hardest blocker as absent.

## The call mapping

| Oso Cloud | SpiceDB | Note |
|---|---|---|
| `authorize(actor, action, resource)` | `CheckPermission` | Direct. Add a ZedToken -- see Consistency |
| `authorize_resources(actor, action, resources)` | `CheckBulkPermissions` | Filter a known list |
| `list(actor, action, resource_type)` | `LookupResources` | **Semantics change.** See below |
| `actions(actor, resource)` | `CheckBulkPermissions` | Fan-out over the declared permissions of that type |
| `insert` / `bulk` / `bulk_load` | `WriteRelationships` / `ImportBulkRelationships` | Target the `__direct` side of a split |
| `delete` (with `null` args as wildcards) | `DeleteRelationships` filter | **Middle-argument wildcards need per-relation iteration.** See below |
| `get(predicate, ...)` | `ReadRelationships` | Filter shapes differ; see below |
| `query` / `evaluate_query` / `build_query` | — | `blocked`. See `blockers.md` |
| `list_paginated` | `LookupResources` + cursor | Same target as `list`; the cursor is already explicit, so the call shape maps closely |
| `batch` | one `WriteRelationships` call | Oso's ordered transaction. SpiceDB writes are atomic per call, so a batch becomes one call, not a loop |
| `policy` | `WriteSchema` | Loads the policy. The converted equivalent writes `schema.zed` |
| `get_policy_metadata` | — | No target, and none needed: it reads the *policy*, and after conversion the schema is the policy. Drop the call |
| `actions_local` | — | `blocked`, same as `authorize_local`/`list_local` |
| `for_agents` | — | Scopes a client to an agent identity. No SpiceDB analogue; carry as a Needs-action finding |
| `list_local` / `authorize_local` | — | `blocked`. See `blockers.md` |
| context facts on a call | caveat context, or written relationships | See `data-mapping.md` |

## Where the semantics change, not just the name

### `list` → `LookupResources`

The most likely endpoint to be load-bearing in the customer's product, and the one whose
differences are easiest to miss because both "return a list".

| | Oso `/list` | SpiceDB `LookupResources` |
|---|---|---|
| Transport | one response + `next_page_token` | server stream |
| Page size | **required, minimum 10 000** on the hosted API | `optional_limit`, max 1 000/page |
| Returns | bare ID strings | object ID + permissionship |
| Total count | no | no |
| Duplicates | not documented | possible across cursor pages |
| Subject | concrete actor required | concrete subject required |
| Fails when | a rule variable has no searchable fact domain -- **at query time** | unconstrained variables -- **at schema-write time** |

Three consequences for a call site:

1. **Results carry a type now.** Oso returns bare ids because the caller supplied the type;
   SpiceDB returns typed objects. Any code that concatenated the type back on must stop.
2. **Deduplicate.** Duplicates are possible across cursor pages, overwhelmingly so rather
   than within one call. A single small probe will not reproduce one and is not evidence
   that dedup can be skipped.
3. **Neither system gives a total count**, so a "Showing 1-20 of 150" pager was already not
   implementable on Oso. If the customer has one, they are counting some other way -- find
   out how before assuming it survives.

**The local dev server does not enforce the page-size rule**, so a probe against it will
accept an omitted `page_size` and a `page_size` of 5 and tell you nothing about production.
Verified: all three of omitted / 5 / 10 000 return results locally. Size list behaviour
against the hosted API, or against the documented limits, not against the dev server.

**A useful precedent when the customer objects to the 1 000-per-page cap:** Oso's own
`page_size` has a documented *minimum* of 10 000 and Oso positions centralized `list` as
optimal up to roughly 10 000 authorized resources per user -- strikingly close to SpiceDB's
own practical ceiling. The ceiling is not new; only its shape is.

### The Oso API is not symmetric, and the migration inherits that

Oso's read endpoints do not accept the same policies. `authorize` and `actions` evaluate a
rule with the resource **bound**; `list` and `authorize_resources` have to **solve** for it,
and solving requires every variable to be reachable from stored facts. Polar lets you write
rules that are checkable but not enumerable -- a variable bound only by a comparison is the
classic case, and it returns a runtime error from `list` while `authorize` succeeds.

**This matters for conversion in two directions.** A policy that already fails on `list` is
one the customer has been working around, so ask how. And SpiceDB moves the same failure
from query time to **schema-write time**, which is an improvement worth stating: the
unconvertible rule is refused when you write the schema, not when a user hits the endpoint.

### `delete` with wildcards

Oso's delete accepts `null` in argument positions as a wildcard. SpiceDB's
`DeleteRelationships` filter supports resource and subject wildcards, but **the relation is
a required, static field** -- so a delete that wildcarded the *middle* argument becomes one
call per relation. Enumerate the relations from `migration-map.json` rather than guessing
which exist.

### `get` → `ReadRelationships`

Both read stored data rather than computing. The filter shapes differ: Oso filters by
predicate plus positional wildcards, SpiceDB by resource type, optional resource id,
optional relation, and optional subject filter. A `get` that wildcarded the predicate has
no single equivalent -- it becomes one read per relation.

## Consistency

Oso has **no documented consistency guarantee**. What exists is an undocumented
`OsoOffset` header: mutations return it, and the v2 SDKs cache it in memory and stamp it on
subsequent requests. Architecturally that is a zookie -- but it is **per client instance,
in-process, non-portable across services, and lost on restart**. Oso's own engineering
writing puts replica lag at roughly 500 ms to 1 s depending on region, and notes fallback
nodes can lag far longer.

This inverts the usual objection to SpiceDB's ZedTokens. Threading a token is more work,
but it replaces an invisible per-process mechanism with an explicit, portable one. **If the
customer constructs Oso clients per request, or reads in service B after writing in service
A, they have had no read-your-writes at all and probably do not know it.** That is worth
establishing early, because it changes what "parity" means during dual-run.

Apply the framework's three-tier rule from
`openfga-to-spicedb/references/code-mapping.md`'s consistency section -- thread a ZedToken;
else `full()` with a marker; never `minLatency()` on a read-after-write path -- and note it
governs lookups as well as checks, where a stale answer is an empty list rather than one
wrong object.

**Be accurate about SpiceDB's own edge.** `fully_consistent` does not guarantee
read-after-write on CockroachDB. Lead with ZedTokens, not with a blanket consistency claim.
