# Blocker Catalog: OpenFGA → SpiceDB

The **Class A** findings for this source -- count the `## <n>.` sections below rather than trusting a number stated here; the catalog is added to as new shapes are found. Class A means no mechanical fix exists and
the conversion cannot proceed until the user decides. **Never convert past an unresolved
Class A finding** -- that is what the gate exists for.

Each entry gives a detection rule, a fidelity rating with its evidence, the options to
offer, and what gets recorded in `migration-plan.md`.

Detection is phase 0's job -- the `migration-analyzer` agent, launched by
`/spicedb-dev:migrate`, which holds the gate; phase 1 only reads the resolution.
`/spicedb-dev:migrate-schema` run **standalone**, with no `migration-plan.md` present, runs
detection and holds a reduced gate inline instead (its step 3b). Three of these four
blockers (contextual tuples, multi-store tenancy, model-ID pinning) are invisible in the
model and need a repository-wide grep; that sweep is the part most easily skipped when
detection and conversion happen in the same command, so it is called out explicitly there
as well. Only the transitive wildcard is model-only -- its own "Detection" section below
needs no grep at all.

Three of these are invisible in the model -- they live in application code and config -- so
a model-only review will miss them.

---

## 1. Transitive wildcard

**What it is.** A relation whose allowed types include a userset `T#rel`, where `rel` is a
**relation** that itself allows a public wildcard `U:*`. OpenFGA accepts it. SpiceDB
rejects the schema:

```
for relation `viewer`: relation/permission `team#member` includes wildcard type `user`
via relation `team#member`: wildcard relations cannot be transitively included
```

Verified on v1.56.0 at both `zed validate` and `WriteSchema` -- this one cannot slip
through to deploy time, it simply stops the conversion.

**The constraint is narrower than it looks: one hop, and only when the userset target is a
relation.** Verified on v1.56.0:

| Shape | Result |
|---|---|
| userset → **relation** whose own type list has the wildcard | rejected |
| userset → relation → userset → relation with the wildcard | rejected, at the **inner** hop only |
| userset → **permission** that is a bare alias of a wildcard-bearing relation | compiles |
| userset → **permission** that unions a wildcard-bearing relation | compiles |
| userset → **permission** that is an arrow onto a wildcard-bearing relation | compiles |

**Rating: `effort` -- still provisional.** It was originally rated `blocked`; that was
wrong. `migrating-to-spicedb/SKILL.md`'s "Fidelity ratings" section requires a `blocked` rating to state where the capability goes instead, and
here it may not have to go anywhere: pointing the userset at a permission rather than at
the relation compiles, writes, and resolves. The rating stays provisional and the finding
stays Class A.

**Corpus status (updated by the `github` store).** The store carries **no `:*` anywhere**,
so it does not exercise this blocker and cannot settle it. It does exercise the
*mechanism* the leading option depends on, which narrows what is left open:

- **Confirmed.** A userset subject pointing at a **permission** resolves, and resolves with
  the semantics OpenFGA gives the same model. `github` stores
  `organization:openfga#repo_admin@organization:openfga#member` where the split rule turned
  `member` into a permission; the check `repo:openfga/openfga#reader@user:erik` resolves
  through `repo_admin -> member -> member__direct` and matches OpenFGA's own `true`. So the
  userset→permission indirection is semantically transparent in general.
- **Still unconfirmed.** Whether that transparency *survives a wildcard* at the far end --
  the actual claim of this blocker. Reaching `user:*` through a permission alias is a
  different question from reaching an ordinary subject through one, and no corpus store has
  exercised it yet.

Do not promote the alias to a mapping rule on the strength of the confirmed half. A store
with a pure-direct, wildcard-bearing relation referenced as a userset is what settles this;
the corpus candidates to check first are the ones flagged `public`/`*` in their models.

**Corpus status (updated by the `gdrive` store, batch 3, iteration 14).** This is the
corpus's first store to carry a live `:*` at all (`grep -l ':\*' */schema.zed` across every
prior committed store returns nothing). It does **not** settle the blocker: `gdrive`'s only
userset type-list reference anywhere is `group#member` (`grep -n '#' model.fga`), and
`group.member` carries no wildcard, so the store never constructs the flagged shape (a
relation whose allowed types include `T#rel` where `rel`'s own type list has the wildcard).
The rating stays `effort`, still provisional, for the same reason it was provisional before:
no corpus store has yet exercised a userset pointing at a wildcard-bearing *bare relation*.

What `gdrive` *does* confirm, for the first time with real wildcard data rather than by
analogy to `github`'s non-wildcard case: `folder.viewer: [user, user:*, group#member] or
owner or viewer from parent` fuses the wildcard with an operator, so the split rule turns it
into `relation viewer__direct: user | user:* | group#member` plus `permission viewer =
(viewer__direct + owner + parent->viewer)` automatically -- and every arrow that reaches it
(`doc.can_read`'s `parent->viewer`, and `folder.viewer`'s own self-referential `parent->
viewer`) therefore lands on a permission, not the bare relation. Verified live end to end on
SpiceDB v1.56.0 with a subject absent from the source fixture (`user:zzz-not-in-fixture`,
added as a fresh `folder:product-2021#viewer__direct@user:*` tuple): `doc:2021-roadmap`'s
`can_read` resolves `true` through the full `doc -> parent -> folder.viewer ->
viewer__direct` arrow chain, and reverts to `false` after the tuple is deleted -- matching
OpenFGA's own wildcard semantics exactly, and confirming the wildcard itself, not just an
ordinary subject, survives the userset→permission indirection this blocker's leading option
depends on. This retires the "leading candidate" option's uncertainty for the case where the
split rule already applies automatically (the common case: a source `define` that fuses a
wildcard with any operator). It leaves open only the narrower case the blocker is actually
about -- a *pure-direct* wildcard-bearing relation, with no operator to trigger the split --
which for a long time had no corpus store of its own. **That gap has since been closed --
see "Corpus confirmation" below, which supersedes this paragraph**; the mechanism was first
established on a synthetic control and later reproduced on a real corpus store. Read the two
together rather than treating this sentence as the current state.

**New fact, verified live, not previously written anywhere in this file: an arrow (`->`) is
exempt from the transitive-wildcard typesystem check entirely, independent of the split.**
`gdrive`'s own real schema never needed this -- its one wildcard-carrying relation always
splits before any arrow reaches it -- but the adjacent question (does the *split* avoid the
restriction, or was the restriction never about arrows at all?) is natural to ask when
reviewing a wildcard-bearing arrow chain, and the answer changes the Detection rule's scope
below. Verified with a synthetic control schema, unsplit and pure-direct on purpose:

```
definition folder {
	relation owner: user
	relation parent: folder
	relation viewer: user | user:* | group#member
	permission can_read = viewer + owner + parent->viewer
}
```

`WriteSchema` accepts this **unedited** on SpiceDB v1.56.0 -- no rejection, no warning --
and `zed permission check folder:child can_read user:<anyone> --explain` resolves `true`
through `parent->viewer` straight into the bare, wildcard-bearing `viewer` relation. Compare
against the actual rejected shape (a relation's own type list containing `T#rel`, e.g.
`relation viewer: user | team#member` where `team.member` carries the wildcard), which fails
`WriteSchema` with the exact error quoted at the top of this section. The restriction is
therefore narrower than "any transitive reference to a wildcard-bearing relation": it fires
only for the literal userset **type-list** syntax (`[T#rel]` on a relation's own subject
list), never for a computed-userset **arrow**, whether or not the arrow's target relation is
split. Detection step 3 below is scoped accordingly -- it was already correct (it only ever
scanned type lists), but this closes the ambiguity a careful reader could otherwise raise
about whether it should also scan arrow targets. It should not, and now there is a verified
reason on file for why.

**Real-corpus confirmation of the arrow exemption (`role-assignments`, batch 4), not just the
synthetic control above.** `openfga/sample-stores/stores/role-assignments`' `role` type
declares `can_view_project`/`can_edit_project` as `[user:*]` -- pure-direct, no operator, so
neither splits -- and `role_assignment.can_view_project: assignee and can_view_project from
role` arrows straight into that bare, wildcard-bearing relation (`role->
can_view_project__perm`, aliased per "Point arrows at permissions, not relations" since the
target never split). This is the exact narrower shape the paragraph above calls out as "still
needs its own corpus store": a *pure-direct* wildcard-bearing relation reached through an
arrow, with intersection (`and`) added on top rather than union. `WriteSchema` accepted the
schema unedited, the harness reached `PARITY OK` on the first attempt, and a live probe with a
subject entirely outside the source fixture confirmed the wildcard itself -- not just an
assigned subject -- survives both the arrow and the intersection: granting
`role_assignment:acme-project-admin-openfga#assignee@user:zzz-not-in-fixture` (a user with no
other tuple anywhere in the fixture) flips `project:openfga#can_view@user:zzz-not-in-fixture`
from `false` to `true`, because `role:acme-project-admin`'s `can_view_project` grants
everyone via `user:*` and the intersection's other operand (`assignee`) is now satisfied too.
This retires the "still needs its own corpus store" caveat for the pure-direct case -- the
mechanism was already correctly predicted by the synthetic control, and now has real corpus
data behind it as well. No new rule: same detection scope, same options table.

Note the interaction with the split rule in `schema-mapping.md`: a source `define` that
fuses a wildcard with an operator (`[user, user:*] or owner`) already splits into a
relation plus a permission that keeps the original name -- so the userset reference lands
on a permission and the model translates with no user decision at all. Only a source
`define` that is *pure-direct* and wildcard-bearing stays a bare relation and trips the
constraint. Expect the flagged set to be much smaller than a naive scan suggests.

**Detection.** Model-only, no code needed:

1. Cheap pre-filter -- if the model contains no `:*` at all, this cannot fire.
2. **Run detection against the post-split output shape, not the source model.** The split
   rule turns many wildcard-bearing targets into permissions, and that changes the answer.
   Detecting on the source model over-reports.
3. For each relation `R` whose allowed types include a userset `U#rel`, look at **one hop
   only**: if `rel` on `U` translated to a SpiceDB `relation` and that relation's own type
   list contains a wildcard, flag `R`. If `rel` translated to a permission, do not flag it,
   whatever that permission expands to. **Scan only subject type lists (`[T#rel]`), never
   arrow (`->`) targets** -- verified on `gdrive` that an arrow into a bare, wildcard-bearing
   relation compiles and resolves correctly regardless of split status, so flagging arrow
   targets here would over-report a shape SpiceDB never rejects.
4. Report the offending pair (`R` → `U#rel`) and the wildcard's home relation, so the user
   can see which hop introduced it.

**Options:**

| Option | Cost |
|---|---|
| **Alias the intermediate relation as a permission** and point the userset at the alias | **Leading candidate, live-confirmed with real wildcard data on `gdrive` (batch 3).** Verified to compile, write, and resolve on v1.56.0, and it is the same shape the split rule already generates whenever the source fuses a wildcard with an operator. The corpus confirmed a userset→permission reference is semantically transparent for ordinary subjects (`github`) and now, per `gdrive`, for a wildcard subject too. Still not corpus-confirmed for the narrower *pure-direct, no-operator* case, since no store has forced that shape yet -- do not apply silently there. |
| Flatten the wildcard onto the outer relation | **Broader than the original**: the outer relation becomes unconditionally public rather than public via that one userset. Only safe when the intermediate relation is always populated. |
| Drop the wildcard and enumerate subjects | Exact for a bounded subject set; becomes a permanent write-path obligation (every new subject needs a tuple) and does not scale to "everyone". |
| Hand-redesign the affected sub-model | Not mechanical. Usually the right answer when the wildcard encodes something real, like published-vs-draft state. |
| Abort | The wildcard is load-bearing and no option above is acceptable. |

**Record:** the chosen option per flagged pair, under `Decisions` → `Per-blocker
resolutions`. If enumeration was chosen, it is also a sync obligation for phase 3.

---

## 2. Contextual tuples

**What it is.** Relationships passed *per request* at the call site
(`contextualTuples` / `contextual_tuples`) and never stored. They exist only for the
duration of one check, and they are **invisible in the model** -- a perfectly clean model
conversion can still be wrong because of them.

SpiceDB has no per-request relationship input. Its per-request channel is caveat
**context**: values, not edges.

**Rating: `effort` or `blocked`, per call site** -- this one must be classified
individually, not in bulk:

- `effort` where the ephemeral edge can be persisted as a real relationship, or where the
  contextual tuple is really carrying a *value* (an attribute, a flag, a timestamp) that
  re-models as caveat context. Expressible, but it creates a new write path.
- `blocked` where the edge must stay ephemeral -- a value computed per request that must
  never be durable. The capability moves into application code: the caller decides before
  or after the check.

**Detection.** Code-side, and it must sweep the whole repository:

```bash
grep -rn 'contextualTuples\|contextual_tuples\|ContextualTuples' .
grep -rn 'ContextualTupleKeys\|--contextual-tuple' .
```

Include `.fga.yaml` files in the sweep -- a test that supplies contextual tuples is
evidence of a production call site that does too. Count the **distinct call sites**, not
the matches; that count is the number of individual decisions the gate has to carry.

**Options:**

| Option | Cost |
|---|---|
| Materialize as real relationships around the check | Write before, delete after -- correct, but adds latency and a cleanup failure mode. |
| Re-model as caveat context | Idiomatic when the tuple carries a value rather than an edge. Needs a caveat on the relevant relation, so it is a schema change, not just a call-site change. |
| Restructure so the edge is persistent | Best long-term answer where the "ephemeral" edge turns out to be durable state the app was recomputing. |
| Leave the call site failing closed with a `TODO(spicedbmigration):` marker (`findings-report.md`'s "Inline markers") | Explicit, safe, and honest. The check denies until someone does the work. Never leave it failing *open* -- a marker on an open failure is a vulnerability with a sticky note on it, not a finding. |

**Record:** one resolution per call site, with `file:line`. Materialize and re-model both
create phase-3 sync obligations; count them.

**Corpus confirmation of the `effort` branch.** `openfga/sample-stores/stores/abac-with-rebac`
is a `.fga.yaml`-level instance of exactly this sweep target: its two `tests:` blocks each
attach one tuple (`draft`/`published`) that the store's own comments describe as either "written
to OpenFGA when the document status changes, or ... sent as a contextual tuple." Materializing
it as a real, persisted relationship (the `effort` option above) is corpus-verified fully
correct on a live v1.56.0 server for both scenarios individually. What it does *not* resolve is
a harness-only wrinkle -- two mutually exclusive scenarios that share an object ID cannot both
be represented in one converted graph at once, so the migration-harness's whole-store parity
run cannot certify both scenarios in a single invocation no matter which one is materialized.
See `schema-mapping.md`'s "Multiple isolated test fixtures colliding in one converted graph" →
"Same object ID, different relation, mutually exclusive scenarios" for the mechanism and the
verification workaround. This is not a new blocker -- the conversion itself is fully correct
and deployable -- it is a corpus-verified example of this section's own `effort` classification.

---

## 3. Multi-store tenancy

**What it is.** The application uses more than one OpenFGA store -- typically one per
tenant. SpiceDB has no store concept: one instance is one namespace, shared by every
definition.

**This blocker is about the store count, not about whether the model has a tenant
concept.** A store that already models tenancy *within* one store -- a tenant root type
(`organization`, `account`, `tenant`, ...) that every tenant-scoped resource references by
relation -- is not this blocker at all, no matter how central that type is to the model.
Detection only fires on evidence of **multiple** stores (distinct store IDs, store CRUD
calls); a single-store model with an internal tenant type produces neither signal, and the
type itself translates via the ordinary `type` → `definition` rule with no decision to make
-- see "Not a blocker: type-based (single-store) tenancy" below, corpus-confirmed on
`multitenant-rbac`. Do not run this section's gate against a model just because it has a
type that plays the tenant role; run it only when the *detection* rule below actually
fires.

**Rating: `effort`.** Every option is expressible in SpiceDB; the cost is structural
redesign plus operational change, not lost capability. It is Class A anyway, because the
choice constrains the identifier strategy, the data migration, and every call site -- it
is the one decision that must be made before anything else.

**Detection.** Config and code:

```bash
grep -rn 'storeId\|store_id\|FGA_STORE_ID' .
grep -rniI 'createstore\|liststores\|getstore\|deletestore' .
grep -rniI 'fga store \(create\|list\|get\|delete\)' .
```

Count **distinct** store IDs across config files, environment templates, deployment
manifests, and test fixtures. Store CRUD calls are a second, stronger signal: an
application that creates stores at runtime is provisioning tenants, and that provisioning
path itself has to be rewritten (SpiceDB has no target for it). Note also that Okta FGA's
data plane has no store CRUD at all, so those calls 404 there.

**Options:**

| Option | Cost |
|---|---|
| N SpiceDB deployments, one per tenant | True isolation, and the closest match to what a store gave you. N× the operational cost, and cross-tenant queries become impossible. |
| One instance with a `tenant` resource type | The idiomatic answer. Every resource gains a tenant edge and every permission path routes through it; isolation becomes a schema property rather than an infrastructure one. |
| **Does not apply -- single store** | The right answer when detection fired only on scaffolding: store CRUD confined to test harnesses, fixture bootstraps, or CI jobs that spawn a throwaway server, with no evidence of more than one store in production. This is a real resolution, not a way of skipping the question: record the sites it fired on and why each is scaffolding, so a reviewer can check the reasoning. Do not force this onto one of the restructuring options below to clear the gate -- they describe changes to make, and there is nothing to change. |
| Definition prefixes per tenant (`acme__document`) | Only when the models genuinely differ per tenant. Otherwise it is schema bloat that grows with the customer list and makes every schema change an N-way rewrite. |

**Record:** the tenancy choice under `Decisions` → `Tenancy`, before any other decision.
If a `tenant` type was chosen, the schema conversion has to add it, and phase 3 has to
write a tenant edge for every migrated object.

---

## 4. Model-ID pinning

**What it is.** The application pins requests to a specific authorization model version
(`authorizationModelId`), so a model change can be rolled out gradually and old code keeps
evaluating against the model it was written for.

SpiceDB has no per-request schema version. `WriteSchema` is global and immediate; every
subsequent request sees the new schema.

**Rating: `blocked`.** The capability does not exist in SpiceDB and cannot be encoded in a
schema. Where it goes: into the deployment process -- schema changes become
backward-compatible-by-construction, staged in an additive order (add new relations,
backfill, cut over readers, remove old ones), with the coordination handled by the release
process rather than by the API.

**Detection.**

```bash
grep -rniI 'authorizationmodelid\|authorization_model_id\|FGA_MODEL_ID\|--model-id' .
```

Distinguish two shapes, because they price differently:

- **Config-level pinning** (one ID in config, used by every call): usually just a
  deployment habit. Often resolvable by dropping it.
- **Per-request pinning** (an ID threaded through call sites, or several distinct IDs in
  use at once): a real rollout mechanism with live dependents. This is the expensive case.

**Options:**

| Option | Cost |
|---|---|
| Drop pinning and accept the change | Correct where the ID was config-level and never varied. Free. |
| Emulate with a schema-version gate | The app carries its own version marker (a relation, or a feature flag consulted before the check) and staged rollouts route through it. Reproduces the intent, at the cost of app-side machinery SpiceDB does not provide. |
| Flag for a manual rollout plan | The honest answer when several models are live at once. Schema evolution becomes an explicitly planned, additive sequence rather than an API parameter. |

**Record:** the resolution, plus every pinned call site with `file:line`, under
`Deferred / manual` if a rollout plan is needed.

---

## A note on ratings versus classes

A fidelity rating describes the **construct**; a finding class describes what the **user
must do about it**. They are orthogonal, and three entries here are rated `effort` while
still being Class A halts: multi-store tenancy, the persistable half of contextual tuples,
and (provisionally) the transitive wildcard are all expressible in SpiceDB, but none has a
confirmed mechanical translation, and each constrains decisions that come after it. Rating
them `heavy` to justify the halt would misreport the cost -- `heavy` means schema writes
enter the product's hot path, which is not what any of these does.

Report the rating and the class separately, and state the evidence for the rating. A
rating asserted without evidence is worse than no rating: the transitive wildcard was
rated `blocked` here on the strength of a compiler rejection alone, and testing the
adjacent shapes showed the capability was reachable after all. **Test the neighbourhood of
a construct before calling it blocked** -- the rejection message tells you what SpiceDB
refused, not what it cannot do.

---

## Not a blocker: runtime-defined ("custom") roles

Checked and ruled out, not left untested. A customer defining their own roles at runtime is
exactly the shape this file's own "note on ratings versus classes" above warns was wrongly
assumed `blocked` in a prior, independent analysis (and turned out `heavy`). Corpus-verified
on `openfga/sample-stores/stores/custom-roles`: for the shape that store exercises, it is
neither. It is fully expressible as pure data -- no generated schema, no reification --
once the source model already lists `[role#assignee]` (or equivalent) as a directly
assignable type on every permission a role might grant, which is exactly the pattern
OpenFGA's own `custom-roles` sample store uses. See `schema-mapping.md`'s "Runtime-defined
('custom') roles" section for the mapping, the `effort` rating, and the live-verified
evidence that introducing a brand-new role costs zero schema writes. That section also
states the one shape this result does not cover -- a role that must grant a permission the
schema did not already anticipate -- which remains open and is not this file's fifth
blocker; it simply has not been settled yet.

---

## Not a blocker: type-based (single-store) tenancy

Checked and ruled out, not left ambiguous. `openfga/sample-stores/stores/multitenant-rbac`
is OpenFGA's own worked example of "how do I do multi-tenant RBAC in one store" --
`organization` is the tenant root, every tenant-scoped relation lives on it or reaches it
through an arrow (`document.editor = organization->document_manager`), and the store's own
`README.md` frames this as the alternative to store-per-tenant. It never uses more than one
OpenFGA store, so "3. Multi-store tenancy" above does not fire on it, and this section
confirms that absence is correct, not a detection gap.

**The conversion needed zero tenancy-specific decisions.** `organization` translates by the
same `type` → `definition` rule as every other type; the tenant edge on `document`
(`define organization: [organization]`) translates by the same pure-type-list-stays-a-
relation rule as `document.category` in `custom-roles`. No new construct, no `use` flag, no
compile step: `WriteSchema` accepted the converted schema unedited, `zed schema read`
afterward is byte-identical modulo formatting, and the harness reached `PARITY OK` on the
first attempt. This is the shape `blockers.md`'s own "One instance with a `tenant` resource
type" option describes as "idiomatic" -- the store demonstrates that when a source model is
already built that way, there is nothing left for the migration to decide; it is the same
`clean` mapping a type with no tenancy role at all would get.

**Cross-tenant isolation was probed directly, since this is the one property a check-only
harness run cannot certify (the source store itself has only one tenant, `acme`, and never
asserts a negative against a second one).** A second tenant (`beta`: its own admin, group,
role, and document, wired the same way `acme`'s are) was written to a live v1.56.0 server
alongside the converted `acme` data, and every cross-tenant probe denied correctly --
verified in both directions and through both APIs:

- `check`: an `acme` admin (`anne`) against `beta`'s document and billing permission, a
  `beta` admin (`mallory`) against `acme`'s document and billing permission, and an
  `acme`-only group/role userset presented directly as the subject of a `beta` permission
  check -- all six `false`.
- `LookupSubjects`/`LookupResources` (the exhaustive-set APIs `list_users`/`list_objects`
  cover, and the known harness gap this pack's corpus runs track separately): `beta`'s
  viewer set for its own document excludes every `acme` user and vice versa; `acme`'s
  `can_edit_billing` resource set for `anne` contains only `organization:acme`. The
  in-tenant case matches the source's own `list_users` oracle exactly (`emily`, `anne`,
  `ian`; `francis` correctly excluded).

This section's positive result (isolation holds) covers only the type that carries the
explicit tenant edge. **It does not cover the whole model** -- see the Class C finding
immediately below, which this probing also surfaced and which a reader must not miss by
stopping here. See `schema-mapping.md`'s "Type-based tenancy (tenant-as-resource-type)"
section for the mapping detail and the full probe list this section summarizes.

---

## 5. Embedded OpenFGA server -- no client to convert

**What it is.** The application does not call OpenFGA over the network; it imports the server
and runs it in-process (`github.com/openfga/openfga`), storing relationships in its own
database through OpenFGA's storage interface. `code-mapping.md`'s "Embedded OpenFGA server"
section describes the shape and is the authority on it.

**Detection.** A dependency on the OpenFGA **server** module rather than (or in addition to)
a client SDK, plus in-process construction and lifecycle calls -- `NewServerWithOpts`,
`Start`/`Stop`/`Close`/`Healthy` on the resulting object:

```bash
grep -rniI 'openfga/openfga\|NewServerWithOpts\|openfgaserver' \
  --exclude-dir={.git,node_modules,vendor,dist,build,target,.venv,__pycache__} .
```

**Why it is Class A.** SpiceDB has no in-process mode. Converting means removing a library and
standing up a separate service: a new deployment unit, connection configuration, and failure
modes the application did not have. That is an architectural decision, not a call-site rewrite,
and no mechanical mapping exists -- the lifecycle methods have no SpiceDB equivalent.

**Options:**

| Option | Cost |
|---|---|
| Run SpiceDB as a service and convert the call sites | The intended end state. Adds an operational component the project must own, and the lifecycle code is replaced rather than mapped. |
| Keep the embedded server; migrate schema and data only, defer the code change | Lets phases 0-3 deliver value now and plans phase 4 separately. The application still depends on OpenFGA. |
| Stop after phase 2 | Correct when the operational change is not acceptable. Record why. |

**Unlike blockers 1-4, this one does not block phase 1.** The schema converts normally, so
resolve it before **phase 4** rather than before phase 1: a `resolution: null` here must not
halt schema conversion. It does affect phase 3 -- see `code-mapping.md`'s note that extraction
from an embedded store's own tables is not covered by `data-mapping.md`.

## Class C: tenant-root reachability gap in subject-aggregation types

**This is not a blocker and does not halt the gate -- it is exactly the kind of Class C
advisory finding that must still be surfaced and recorded, not silently inherited.** It sits
here, immediately after "Not a blocker: type-based (single-store) tenancy," specifically so
a reader of that section cannot stop at "isolation holds" without also seeing where it
doesn't -- the isolation guarantee and this gap are equally load-bearing facts about the
same tenancy choice, verified in the same probing pass.

**What it is.** A tenant-scoped model (see the "Type-based tenancy" mapping above) is
isolated only for the types that carry an explicit relation or permission path back to the
tenant root. A **subject-aggregation type** -- a type whose own relations only ever list
`user`, itself, or another subject-aggregation type as allowed subjects, and which some
tenant-scoped type references as a userset subject (`T#rel`) -- can be entirely absent from
that reachability graph. Nothing in either OpenFGA's or SpiceDB's type system requires such
a type to carry a tenant edge, so nothing stops one instance of it from being wired into two
different tenants' permission graphs. Corpus-confirmed on `multitenant-rbac`: `role` and
`group` are both subject-aggregation types with no relation back to `organization`, in the
*source* OpenFGA model as much as the converted SpiceDB one. Reusing one tenant's `role`
object inside a second tenant's `admin` union grants every member of the first tenant's role
full access under the second, with zero schema change -- see the "Not a blocker" section
above for the live-verified command sequence.

**Detection.** Model-only, run once the tenancy shape is confirmed as type-based
(single-store, tenant-as-resource-type -- if the model doesn't have that shape at all, this
does not apply):

1. **Cheap pre-filter** -- if every type in the model has a direct or one-hop-arrow relation
   back to the tenant root type `T`, this cannot fire; skip the rest.
2. Identify `T`, the tenant root type recorded under `Decisions` → `Tenancy`. **This is the
   step that has no instructions of its own when the model needs them most.** The gate
   (`/spicedb-dev:migrate` step 5, row 2) only asks the Tenancy question -- and only then
   names a `T` -- when the multi-store detection rule fires (distinct store IDs, store CRUD
   calls). A **single-store** model, the exact shape this whole section applies to, gets
   "single store, no tenancy decision required" recorded instead, with **no `T` named at
   all**. Reading a `Decisions` → `Tenancy` field that was never written is not a
   malfunction; it means this step's actual job -- picking `T` -- has not been done by
   anything upstream, and doing it is this step's own responsibility whenever the model
   lacks a type named `tenant`/`organization`/`account` (or the field is simply absent).
   When a candidate is obvious by name (`organization` in `multitenant-rbac`), use it and
   move on. Otherwise:
   1. **List every root candidate**: types with **no outgoing bare belongs-to edge** at all
      (nothing they point to as a parent, by step 3's own edge rule below -- compute step 3
      first if needed), **excluding the model's subject types**. A model can have more than
      one; a strict hierarchy produces exactly one, sitting above everything else.

      **The exclusion is not optional, and skipping it breaks this step on every model.** A
      pure subject type -- `type user` with no relations at all, or a subject-aggregation
      type like `group` whose relations name only subjects -- has no outgoing edges by
      definition, so it satisfies "no outgoing bare belongs-to edge" trivially and would be
      nominated every time. It is not a tenant root; it is an actor, sitting outside the
      containment hierarchy rather than above it, and following it leads straight to step 4's
      "no discernible tenant-root type" on models that plainly have one.

      **Identifying a subject type, precisely.** A type is a subject type when **every one of
      its relations is a bare direct-assignment list** -- no `or`, no `and`, no `but not`, no
      arrow -- **and** every type those lists name is itself a subject type. A type with no
      relations at all (`type user`) is the base case and anchors the recursion. Compute it by
      starting from the no-relation types and adding types that qualify, until nothing new
      qualifies.

      **The operator-and-arrow condition is the load-bearing half; without it the rule
      cascades over the whole model.** "Every type its relations name is a subject type,"
      taken alone, has no stopping point: in one real 17-type model, the root type's bare entries name only `user`
      and `group#member`, so `server` would qualify, and then every type whose bare entries
      name only `user`/`group#member` qualifies too, until all 17 types are "subject types,"
      the candidate list is empty, and step 4 reports no tenant root. The operator test stops
      that: `server.operator` is `[user, group#member] or admin`, and `certificate.can_edit`
      carries `admin from server`, so neither is a bare membership list and neither type is a
      subject. On this model the rule yields exactly `{user, group}` -- an actor and a group
      of actors -- which is the answer it should give.

      **Apply the same exclusion to the edges, not only to the candidate list** -- an edge to
      a subject type is not a belongs-to edge. A resource does not *belong to* its actors:
      `server.admin: [user, group#member]` says who administers the server, not that the
      server sits under `user`. Excluding subject types from the candidate list alone is not
      enough and produces the opposite error: in one real 17-type model, the root type's bare `user` entries would
      count as outgoing edges, disqualifying `server` as a root candidate, emptying the list
      and sending step 4 to "no discernible tenant-root type" on a model that has one. When
      computing "no outgoing bare belongs-to edge," ignore entries naming a subject type on
      both sides of the test. What remains are the types
      that own resources.
   2. **A lone root candidate is not automatically `T`.** Check whether the application
      treats it as a **singleton** -- one object, never parameterized, never created at
      runtime (a niladic constructor/accessor in the source code is the strongest signal; a
      config-level default object ID is another). A singleton root is "the deployment
      itself," not a partition within it, and is the wrong level to call a tenant boundary.
      When it is a singleton, `T` is not this type -- descend one hop: consider the type(s)
      whose own bare parent edge points at the singleton root instead.
   3. **Among the remaining candidates, prefer the type that is both structurally central
      and operationally multi-instance**: the one most other types reach (directly or
      transitively) by a bare belongs-to edge, **and** which the application itself creates
      and scopes more than once at runtime (a parameterized constructor/accessor, an ID
      argument, a CRUD API, more than one stored object). Breadth of reach alone is not
      enough -- a category type can be widely referenced without being a tenant; multiplicity
      is what makes a boundary a *tenant* boundary rather than just a common ancestor.
   4. **If nothing clears both bars, conclude there is no tenant root, plainly, and stop
      here** -- do not guess and do not silently skip the whole section without saying why.
      Record: "No discernible tenant-root type; Class C reachability sweep does not apply,"
      with the belongs-to graph evidence for the candidates considered and rejected. A flat
      model, or one where every non-root type reaches only the singleton root with no
      intermediate multi-instance type, is a real, legitimate outcome of this check, not a
      failure to look hard enough.

   **Worked example, from a real production Go project (17 types, no type literally named
   `tenant`/`organization`/`account`):** step (1), **after excluding subject types**, finds
   exactly one root candidate: `server`. The exclusion is what makes that true and is worth
   watching here -- `server`'s own relations name `user` bare (`server.admin: [user,
   group#member]`, `server.authenticated: [user:*]`), so `server -> user` is a real edge by
   step 3's rule and `server` is *not* zero-parent in the raw graph. `user` is the only type
   with no outgoing edge at all, and it is excluded as a subject type; `group` is excluded
   for the same reason. That leaves `server` as the sole candidate among resource-owning
   types. But `server` is a singleton: its own object accessor takes **no** identifying argument
   at all, confirming there is exactly one `server` object system-wide. Step (2) therefore
   rules `server` out and descends one hop to the types whose bare parent edge points at
   `server`. **There are four** -- `certificate`, `network_integration`, `project`, and
   `storage_pool`, each carrying `define server: [server]` -- so this hop does not by itself
   select anything, and step (3)'s tiebreak is what decides. It picks `project`: that type's own
   accessor takes a name argument, so it is parameterized rather than a singleton, and the
   application exposes a real multi-instance CRUD surface for it (a project CRUD handler and a
   `/projects` REST collection)
   -- and **10 of that model's other 16 types** (`image`, `image_alias`, `instance`, `network`,
   `network_acl`, `network_address_set`, `network_zone`, `profile`, `storage_bucket`,
   `storage_volume`) carry a direct bare edge to `project`. `T = project`.

   **What the rest of the algorithm then flags on this model: `group`, and only `group`.**
   Its sole relation is `member: [user]`, so it has no bare edge to `project` (or to
   `server`) and step 4 leaves it outside the tenant-scoped set; `group#member` is then
   listed as a userset subject on tenant-scoped types' relations (`instance.can_exec`,
   `storage_volume.can_view`, `project.viewer`, and many more -- 66 `group#member` positions
   across the model, all of the single distinct userset), which is exactly step 5's condition. It is the
   only such type here, because `group#member` is the **only** `Type#relation` userset
   anywhere in this model.

   **`certificate`, `network_integration`, and `storage_pool` are *not* flagged, and the
   reason is worth stating because they look like they should be.** Each carries a bare edge
   only to `server`, never to `project`, so step 4 does leave them outside the tenant-scoped
   set too -- but step 5 requires a candidate to *also* appear as a userset subject
   (`Type#relation`) on some tenant-scoped type's relation, and these three never appear in
   that position at all. **The trap is direction, and it is easy to fall into: all three do
   carry `group#member` *on* their own relations (`certificate.can_edit`,
   `network_integration.can_edit`, `storage_pool.can_edit`), so they look related to
   usersets when grepped casually.** That is the reverse of what step 5 asks. The test is
   whether the candidate is the *subject* (`certificate#something` appearing inside another
   type's list), not whether its own lists contain some other type's userset. Grep for
   `<candidate>#`, not for `#` on the candidate's relations. They are resource types, not
   subject-aggregation types. Being
   outside the tenant-scoped set is necessary for this finding, not sufficient: the whole
   point of this Class C sweep is subject aggregation, per this section's own title, so a
   type that aggregates no subjects cannot produce it no matter how it is scoped. Reporting
   these three would be a false positive.

   Note also that on this model the choice of `T` does **not** change the flagged set --
   `group` reaches neither candidate, so it falls outside the tenant-scoped set under
   `T = server` just as under `T = project`, and the userset condition is what binds. `T`
   still has to be identified correctly (it determines the tenant-scoped set, and on a model
   whose aggregation types *do* carry parent edges it determines the answer), but do not
   expect a worked `T` choice to justify itself by changing the outcome.
3. Build the model's **belongs-to graph**: for every type `X`, the set of types `X` reaches
   is every type named as a **bare (non-userset) entry that is not itself a subject type** in
   one of `X`'s own relation type
   lists, plus every type reachable through an arrow's tupleset-then-target hop. **Exclude
   userset entries (`T2#rel`) from this edge set.** A bare entry (`[organization]`) means
   "this relation's value *is* the parent/tenant object" -- the genuine belongs-to link a
   resource carries. A userset entry (`[role#assignee]`) means "members of `T2`, reached via
   its own `rel`, may be granted this relation" -- subjects flowing *into* `X` from `T2`, the
   opposite direction of a belongs-to edge, and frequently present on relations (role
   assignment, group membership) that have nothing to do with tenant scoping at all. Folding
   both entry kinds into one edge set is a second, subtler way to misapply this algorithm --
   see the `custom-roles` derivation below, where it produces a coincidental one-hop path
   that would wrongly exempt a real gap.
4. Compute **tenant-scoped types**: every type `X` (including `T` itself, trivially) from
   which `T` is reachable by walking `X`'s **own** belongs-to edges from step 3 forward,
   possibly through several hops. This is **resource → root, not root → resource** -- the
   walk starts at each candidate type and asks "does following my own belongs-to edges,
   transitively, ever arrive at `T`," it does not start at `T` and spread outward. Getting
   this backward is the single most likely way to misapply this algorithm: starting from `T`
   and walking its own edges forward reaches `T`'s *subject* types (who can be granted
   access to the tenant), not the *resource* types the tenant scopes -- the two sets are
   usually disjoint, and swapping them produces exactly the false negative this section
   exists to catch (see the derivation below).
5. Compute **subject-aggregation types**: every type that (a) is *not* in the tenant-scoped
   set from step 4, and (b) appears as a userset subject type (`Type#relation`) on some
   tenant-scoped type's relation, directly or through another subject-aggregation type
   (e.g. `organization.admin: [user, role#assignee]` makes `role` a candidate; `role`
   itself listing `group#member` makes `group` a candidate too).
6. For each type found in step 5, check whether it has **any** belongs-to relation or
   permission -- direct, or transitive through its own type list, using the same bare-edge
   rule as step 3 -- whose reference chain leads back to `T`. If none exists, flag the type.
   (This restates step 4's own test for exactly the types step 5 selected -- steps 4 and 6
   must never disagree on direction or on which edges count; if they do, one of them is
   misapplied.)
7. **Confirm every flag on a live server, against the actual converted schema, not by
   static reasoning alone**: write one relationship that references the flagged type's own
   object from a second, fabricated tenant's permission graph, and check whether a subject
   reachable only through the first tenant gains access under the second. A `true` result
   confirms the gap.

**Derivation, hand-executed, not asserted -- `multitenant-rbac`
(`corpus-runs/multitenant-rbac/schema.zed`):**

- Step 2: `T = organization`.
- Step 3's belongs-to edges (bare entries only; userset entries listed but excluded, marked
  `(userset, excluded)`): `document -> organization` (bare, from
  `document.organization: [organization]`); `organization -> user` (bare, from
  `organization.admin: [user, role#assignee]`'s `user` entry; `role#assignee` is
  `(userset, excluded)`; the other four `organization` relations are already-split
  `__direct` relations onto `role#assignee` alone and add no bare edge); `role -> user`
  (bare, from `role.assignee: [user, role#assignee, group#member]`'s `user` entry;
  `role#assignee` and `group#member` are both `(userset, excluded)`); `group -> user` (bare,
  from `group.member: [user, group#member]`'s `user` entry; `group#member` is
  `(userset, excluded)`).
- Step 4, walked correctly (does following *my own* belongs-to edges reach
  `T = organization`?): `document -> organization` -- one hop, arrives at `T`.
  **`document` is tenant-scoped.** `role -> user` -- `user` has no outgoing edges at all
  (dead end); following every belongs-to edge `role` has, transitively, never produces
  `organization`. **`role` is not tenant-scoped**, and by the identical argument (`group ->
  user`, dead end) **neither is `group`.** `organization` is tenant-scoped trivially (it is
  `T`). Tenant-scoped set: `{organization, document}`.
  *(Contrast with walking outward from `T` instead, the earlier, wrong reading of this step:
  `organization -> user` only, a dead end, giving `{organization, user}` -- `document` never
  appears in that set either, which is exactly backward and exactly as broken as the
  all-edges version this fix also removes: swapping the walk direction back would exclude
  `role`/`group` from step 5's candidate pool as "already tenant-scoped" the moment any edge
  toward them existed, and never flag the real gap.)*
- Step 5: candidates are subject-aggregation types referenced as a userset subject on a
  tenant-scoped type's relation. `organization.admin: [user, role#assignee]` makes `role` a
  candidate; `role.assignee: [user, role#assignee, group#member]` makes `group` a candidate
  too (organization is tenant-scoped, and the userset chain `organization -> role -> group`
  carries the candidacy transitively, per step 5's own text -- note this is exactly the
  `role#assignee`/`group#member` traffic step 3 excluded from the belongs-to graph, used
  here for its correct purpose, identifying candidates, not for reachability). Neither
  `role` nor `group` is in the tenant-scoped set from step 4, so both remain candidates.
- Step 6: does `role` have any belongs-to relation/permission whose reference chain leads
  back to `T`? No -- confirmed by the same forward walk as step 4. Same for `group`. **Both
  flagged.**
- Step 7: live probe (documented in "Not a blocker: type-based (single-store) tenancy"
  above) returns `true` -- confirmed.

**Result: `{role, group}` flagged, `{organization, document}` not flagged** -- matching this
section's own worked finding above, now derived rather than asserted.

**Cross-check on a second corpus store with the identical shape --
[`openfga/sample-stores`](https://github.com/openfga/sample-stores)'s
`stores/custom-roles/model.fga`, where the bare/userset distinction in step 3 is
load-bearing, not cosmetic:**

- `T = org`. Step 3's belongs-to edges, bare entries only: `asset-category -> org` (bare,
  from `asset-category.org: [org]`); `asset -> asset-category` (bare, from
  `asset.category: [asset-category]`); `org -> user` (bare, from `org.owner: [user]` and
  `org.member`'s `[user] or owner` -- every other `org` relation is `[role#assignee] or ...`,
  `(userset, excluded)`, contributing no bare edge); `role -> user` (bare, from
  `role.assignee: [user, team#member, org#member]`'s `user` entry -- `team#member` and
  `org#member` are **both** `(userset, excluded)`, even though `org` is literally `T`);
  `team -> user` (bare, from `team.member: [user]`).
- Step 4: `asset-category -> org` -- one hop, arrives at `T`. **`asset-category` is
  tenant-scoped.** `asset -> asset-category -> org` -- two hops, arrives at `T`. **`asset`
  is tenant-scoped.** `role -> user`, a dead end -- following `role`'s belongs-to edges never
  reaches `org`. **`role` is not tenant-scoped.** `team -> user`, a dead end. **`team` is
  not tenant-scoped.** Tenant-scoped set: `{org, asset-category, asset}`.
  **This is the step where the bare/userset distinction changes the answer.** `org#member`
  is one of `role.assignee`'s allowed subject types, so an all-entries reading of step 3
  (the version fixed in this same round) would draw the edge `role -> org` directly and
  mark `role` tenant-scoped in one coincidental hop -- exempting it from flagging even
  though nothing in the schema actually scopes a `role` object to one org. The bare-only
  rule correctly excludes that edge: `org#member` describes *subjects flowing into*
  `role.assignee` (an org's members may be assigned this role), not `role` *belonging to*
  an org, and the two are semantically opposite despite both mentioning `org` in the same
  type-list literal.
- Step 5: `org`'s many `*__direct` relations list `role#assignee`, making `role` a
  candidate; `role.assignee` lists `team#member`, making `team` a candidate too (`org` is
  tenant-scoped, carrying the candidacy transitively through `role`, itself a candidate, per
  step 5's own text). Neither `role` nor `team` is in the tenant-scoped set from step 4.
- Step 6: `role` and `team` both dead-end at `user`, confirmed by the same walk. **Both
  flagged.**
- Step 7: not run against a live server for this store as part of this correction (no
  fabricated second-org data was written) -- recorded here as the still-open verification
  step this algorithm's own step 7 requires before trusting the flag, not as a completed
  check. The mechanism is identical to the one verified live on `multitenant-rbac` (a
  `role`/`team` object referenced from two different orgs' relations, with no schema
  change).

**Result: `{role, team}` flagged, `{org, asset-category, asset}` not flagged.** The two
corpus stores land on the same *shape* of answer (root and its resource chain safe, the
role-like and group-like aggregation types flagged) but for a genuinely re-derived reason
each time -- `custom-roles`' `role` very nearly (and, under the earlier, wrong all-entries
reading of step 3, actually did) come out the other way. Re-derive per model; do not assume
`multitenant-rbac`'s `{role, group}` pattern transfers by name.

**Applicability gate, `openfga/sample-stores/stores/condition-data-types`:** this store has
one type (`datatype_test`) and no tenant root at all -- the "Detection" preamble's
applicability condition ("if the model doesn't have that shape at all, this does not apply")
short-circuits before step 2 ever runs. Zero flags, correctly, and not because step 4
happens to produce an empty set -- the algorithm never executes on a model with no tenancy
shape to begin with.

**Zero-candidate confirmation, and a surfaced scope limit --
`openfga/sample-stores`'s `stores/superadmin`** (the converted schema is recorded in the
plugin's source repository at `tools/migration-harness/corpus-runs/superadmin/schema.zed`;
see `SKILL.md`, "The parity harness is not part of this plugin")**:** this store was selected
specifically as a candidate counterexample -- a `system` root type that sits *above* the
tenant (`organization`) and whose admins (`employee`/`application` subjects) are meant to
reach every tenant's resources by design (`organization.admin: [user] or admin from
system`). Run the algorithm on it and it produces **zero flags**, but the derivation shows
this is a narrower result than "the algorithm evaluated the pattern and correctly exempted
it":

- Step 2: `T = organization` (the SaaS customer, same tenancy shape multitenant-rbac uses --
  `project.organization: [organization]` and `task.project: [project]` give resources a path
  back to it).
- Step 3's belongs-to edges (bare entries only): `project -> organization` (bare, from
  `project.organization: [organization]`); `task -> project` (bare, from `task.project:
  [project]`); `organization -> system` (bare, from `organization.system: [system]` --
  every organization tuple must declare which `system` it defers to); `organization -> user`
  (bare, `admin`'s and `member`'s `user` entries); `organization -> employee` (bare,
  `helpdesk_member`'s `employee with non_expired_time_grant` entry, condition notwithstanding
  -- a caveat on a bare entry does not change whether it is bare); `system -> employee`,
  `system -> application` (bare, `system.admin: [employee, application]`). `user`,
  `employee`, and `application` are all empty types (`type user` / `type employee` / `type
  application`, no relations) and contribute no further edges.
- Step 4 (does walking each type's *own* edges reach `T`?): `project -> organization` --
  one hop, arrives at `T`. **`project` is tenant-scoped.** `task -> project -> organization`
  -- two hops, arrives at `T`. **`task` is tenant-scoped.** `system -> employee` / `system ->
  application` -- both dead ends (`employee`/`application` have no outgoing edges); following
  `system`'s own edges never reaches `organization`. **`system` is not tenant-scoped.**
  Tenant-scoped set: `{organization, project, task}`.
- Step 5 (subject-aggregation candidates: types outside the tenant-scoped set that appear as
  a **userset subject** (`Type#relation`) on some tenant-scoped type's relation): **empty.**
  This store contains **zero** `Type#relation` entries anywhere in any type list -- every
  subject reference is either a bare type (`[user]`, `[employee, application]`, `[employee
  with non_expired_time_grant]`) or reached through an arrow (`admin from system`,
  `helpdesk_member from organization`), never a userset subject inside a `[...]` list. `role`
  and `group` in `multitenant-rbac` were candidates because `organization.admin: [user,
  role#assignee]` and `role.assignee: [user, role#assignee, group#member]` put them there
  directly; nothing in `superadmin` does the equivalent for `system`.
- Step 6: never runs -- there is no candidate to check.

**Result: `{}` flagged.** This is a genuine negative-control instance of the algorithm *as
literally written* -- but the reason it is silent is that `system`'s cross-tenant reach uses
a reference shape (an arrow target, fed by a bare tenant-to-root edge) that step 5's
candidate filter was never built to examine, not because steps 5-6 evaluated `system` and
concluded it was safe. Confirmed live before treating the silence as trustworthy: writing a
second tenant (`organization:beta`) that also declares `organization:beta#system@system:global`
lets every `system:global` admin (`employee:anne`) reach `beta`'s resources exactly as
designed (`true` on `check`, and `beta-task` appears in `anne`'s own `LookupResources` set)
-- but a *third* tenant (`organization:gamma`) declaring a **different** system root
(`organization:gamma#system@system:other-vendor`) is correctly **unreachable** by `anne`
(`false` on `check`, absent from her `LookupResources` set, and `LookupSubjects` on
`gamma`'s task returns only `system:other-vendor`'s own admin). The edge genuinely governs;
this is not an accidental leak. That is what makes `system` structurally unlike `role`/`group`
in `multitenant-rbac` -- there, *nothing* tied a role/group object to any tenant in either
direction; here, `organization` carries an explicit, singular, bare edge *up* to its
governing root, and reach is scoped by that edge, verified in both the positive and the
negative direction.

**But the algorithm did not establish that governance -- the live probe did, because step 5
never looked.** This distinction matters because it bounds how far the algorithm's current
silence can be trusted to generalize: the same reference shape (a subject-aggregating type
reached only via an arrow, never a type-list userset entry) would sail through step 5
identically on a hypothetical store where the arrow-fed root carries **no** governing bare
edge from the tenant root -- an actual, ungoverned cross-tenant leak in the same shape as
`role`/`group`, just fed through an arrow instead of a type-list entry, and step 5 would
never generate it as a candidate to check in step 6 either. `superadmin` does not have that
failure mode (its edge is real and governs correctly, proven above), so this is not a bug
report against this store's own conversion -- but it is a real, demonstrated boundary of the
Detection algorithm's step 5, worth recording precisely rather than leaving implicit:
**step 5's candidate net is scoped to userset-subject-in-type-list references only; it does
not examine arrow targets.** A future broadening of step 5 to also treat arrow targets as
candidates is *not* a safe drop-in fix without a companion exemption, confirmed by the same
derivation: naively extending step 5 to include `system` (arrow target of
`organization.admin`) would hand it to step 6, which would flag it (`system -> employee` /
`system -> application` still never reaches `organization` in step 4's forward walk) -- a
**false positive** on exactly the legitimate, governed pattern this store demonstrates. The
exemption a future extension would need: skip flagging a step-5-arrow-target candidate `S`
when the tenant root `T`'s own belongs-to edges (step 3) already include a bare edge to `S`
(`organization -> system` here) -- that shape marks `S` as a designed super-root the tenant
explicitly, singularly opts into, not an unguarded leak. This is recorded as an open
sharpening item for the Detection algorithm, not applied here: no corpus store yet forces the
broadened step 5 (no store has an arrow-fed cross-tenant path that is *also* ungoverned), so
adding it now would be speculative complexity the algorithm's own worked derivations could
not yet exercise.

**Record:** every flagged type, under `migration-plan.md`'s `Decisions` → `Tenancy`
subsection -- the same place the tenancy shape itself is recorded, immediately following
it, so a reader of the tenancy decision cannot miss the reachability gap in the same
decision. State plainly, per flagged type: it is isolated by write-path discipline only, not
by the schema, in both the source and the target; adopting a stronger guarantee (adding a
tenant edge to the flagged type) is an optional hardening change outside the scope of a
parity-preserving migration, and if the team wants it, record that separately as a
`Deferred / manual` item with the schema location, since it becomes a real schema change at
that point.

---

## Not a blocker: usage-quota entitlements with a request-supplied usage figure

Checked and ruled out, not assumed. This is the shape the task brief that selected
`openfga/sample-stores/stores/advanced-entitlements` named directly as the corpus's best test
of a stated hard limit: **a SpiceDB caveat compares stored context against request context --
it cannot read another relationship, and it cannot aggregate.** A prior, independent analysis
of a comparable system found the entitlements pattern (`used < quota`, both values
independently *stored* facts) genuinely `blocked`, with the workaround of storing quota as
caveat context and having the application supply `used` per request -- correct, but a
capability relocation, not a pure translation.

`advanced-entitlements`' own model and tuples settle which case it is: `store.fga.yaml` binds
only the *quota* half of each condition at write time (`collaborator_limit`, `row_sync_limit`,
`page_history_days_limit`); the *usage* half (`collaborator_count`, `row_sync_count`,
`page_history_days_count`) never once appears in `tuples:` anywhere in the file -- only in
`tests:`' own `check.context` blocks, a different value on every check. OpenFGA's own model
never persists "used" either; the application already supplies it fresh per request, before
any SpiceDB migration is in scope. This is the **request-supplied** case, not the
**both-stored** case, and the caveat translation carries the split over unchanged with no new
mapping rule and no aggregation of any kind. See `schema-mapping.md`'s "Usage-quota
entitlements: caveat vs. materialized marker" for the mapping, the `clean` rating for the
caveat form, and the live-verified evidence -- including a direct probe that writes real
`organization#member` relationships and confirms SpiceDB never notices or counts them when
evaluating a `collaborator_count`-gated caveat, which is the most literal opportunity this
store's own graph offers for an implicit aggregation to occur, and does not.

**Where the capability goes if a future store *is* the both-stored case.** Confirmed from
SpiceDB v1.56.0 source, not merely inferred: `pkg/caveats/eval.go`'s
`EvaluateCaveatWithConfig` is the only entry point that runs a caveat's CEL body, and its only
data input is a flat `map[string]any` merged from the relationship's bound context and the
check's supplied context -- no datastore reader, no relationship-graph handle, and no
count/sum primitive anywhere in it or in the `Environment` that compiles a caveat's parameter
list. A caveat can compare two JSON-shaped values; it cannot look up, count, or sum anything
else SpiceDB knows, regardless of how the comparison is named or which relation the values
would nominally come from. If a future store's "used" figure turns out to be backed by a
persisted SpiceDB fact (a running counter relation the application increments and decrements,
rather than a value it computes and supplies fresh per request), the comparison cannot move
into a caveat at all -- it has to leave the caveat system entirely: the application reads both
stored facts itself (an ordinary check/read for "quota," a `LookupSubjects`/count call or
equivalent for "used") and supplies the *result*, or the raw count, as check-time caveat
context. That is the same partial workaround the task brief describes, generalized: the
counter is not something a caveat can ever own, in this store's shape or in the harder one, so
the workaround is not a fallback for a missing rule -- it is the only shape the capability
boundary above allows.

No corpus store has a usage counter modeled as a persisted SpiceDB relation rather than a
caller-supplied value, so this is not this file's fifth blocker -- consistent with spec
decision D11 (rules and blockers come from a real corpus model forcing them, not from
research), it is recorded here as the derived answer to a question this store came close to
forcing but did not, so a future iteration that does meet the both-stored shape does not have
to re-derive the capability boundary from scratch.
