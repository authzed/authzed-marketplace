# Cutover Strategies

Phases 0-5 (`SKILL.md`'s phase pipeline) convert a schema, a dataset, application code, and
a test suite. That produces artifacts a reviewer can read and a validator can check -- it
does not move a single production request from the source system to SpiceDB. This file is
the playbook for the part after the artifacts exist: how a migration actually crosses from
the source system to SpiceDB, in production, without an outage, a silent authorization
regression, or a rollback nobody rehearsed.

It is framework-owned and source-agnostic, the same way `pack-contract.md` and
`findings-report.md` are: nothing below depends on which source system produced the schema
and data being cut over, and a future pack inherits it unchanged. A pack supplies the
*what* of the conversion; this file supplies the *how* a converted system earns production
traffic.

## Precedent

The seven steps below are adapted from four documented migrations onto production
authorization systems. Three target Zanzibar-style relationship graphs, the same shape
SpiceDB is: Grafana's move to OpenFGA, Airbnb's Himeji, and Carta's AuthZ. The fourth --
AWS's guide to migrating Open Policy Agent deployments to Amazon Verified Permissions
(Cedar) -- targets a policy language, not a relationship graph; its data model differs from
SpiceDB's. It is cited here for its cutover mechanics (parallel deployment, feature-flagged
traffic shift, staged decommission), which are the same shape as the other three, not for
any claim that Cedar is Zanzibar-inspired.

Every quotation below was read at its source, not carried forward from a summary. Where a
step's guidance is this file's own synthesis rather than a documented precedent, it says so
instead of attaching a citation that doesn't hold up.

| Precedent | Author(s), venue, date | Cited for |
|---|---|---|
| Grafana &rarr; OpenFGA | Jo Guerreiro (Engineering Manager, Grafana Labs), *Keys and Craft*, Apr 22 2025 | Steps 3, 4, 5, 6, 7 |
| Airbnb's Himeji | Alan Yao, Dipak Pawar, Blair Wu, Abhishek Parmar, *The Airbnb Tech Blog*, Apr 20 2021 | Step 5 |
| Carta's AuthZ | Aaron Tainter, *Building Carta*, Jun 11 2021 | Steps 6, 7 |
| AWS OPA &rarr; Verified Permissions (Cedar) | Samuel Folkes (Senior Security Solutions Architect, AWS), *AWS Security Blog*, 05 Nov 2025 | Steps 2, 3, 4, 6, 7 |

Full citations are given at each precedent's first use below. One source considered and
dropped: an early pass at this file also had a KubeCon EU 2025 slide deck as a second
Grafana citation. The deck is almost entirely rasterized images and could not be verified
text-by-text; Jo Guerreiro's own blog post is a written recap of that same talk and is
readable and verifiable, so the deck is not cited and the blog post carries the Grafana
precedent alone.

## Where this sits in the pipeline

The seven steps are not a seventh phase bolted onto the six in `SKILL.md` -- they wrap
around the existing pipeline and extend past its end, and the mapping is not one-to-one:

| Step | What runs | Automated by the plugin? |
|---|---|---|
| 1. Inventory | Phase 0 (`/spicedb-dev:migrate`), the pack's scoping questionnaire (`pack-contract.md` item 9) | yes |
| 2. Translate + validate | Phases 1-2 (`/spicedb-dev:migrate-schema`, the `schema-validator` agent) | yes |
| 3. One representative resource type | A scoped rehearsal of phases 3-5 (`/spicedb-dev:migrate-data`, `/spicedb-dev:migrate-code`, `/spicedb-dev:migrate-tests`), run against one type instead of the whole model | phases yes, scope choice no |
| 4. Dual-write, shadow-read | The differential harness (`differential-harness.md`), emitted by `/spicedb-dev:migrate-verify` | yes |
| 5. Reconciliation job | -- | no |
| 6. Cut over behind a flag | -- | no |
| 7. Remove the source system | -- | no |

Step 2 is not "phase 1" alone: schema conversion (phase 1) and validation (phase 2) are two
separate rows in `SKILL.md`'s phase table but one step here, because a customer experiences
them as a single unit of work -- translate, then confirm the translation holds, before
either is exposed to a call site. Step 3 does not introduce a new command; it is phases 3
through 5 run at a deliberately narrower scope than a full migration, so that whatever
friction the model, the data, or the call sites produce is cheap to fix while only one
resource type is affected. Step 4 is the first step with no phase-pipeline analog at all --
it is where the differential harness runs, and its contract, safety properties, and four
capabilities (dual-run, diff, replay, snapshot-to-assertions) are defined in
`differential-harness.md`. **Both `differential-harness.md` and `/spicedb-dev:migrate-verify`
are written and shipped** -- the command emits a working harness satisfying that contract
into the customer's own project, in their own language, and step 4 runs through the plugin
today the same way steps 1 through 3 do. Steps 5 through 7 have no command behind them at
all, by design rather than by gap; see "What the plugin does not automate," below.

## The seven steps

### 1. Inventory

Run the pack's scoping questionnaire (`pack-contract.md` item 9) before estimating
anything else about the migration. This is phase 0's job: `/spicedb-dev:migrate` launches
the `migration-analyzer` agent, which reads the complete authorization model and sweeps the
codebase for every Class A blocker in the pack's catalog (`migrate.md`, step 2), and it is
deliberately front-loaded -- every decision that could reshape the estimate (a hard blocker
with no mechanical fix, a tenancy choice, an identifier collision) is surfaced once, in one
batch, before any file is converted (`SKILL.md`, "Overview").

Budget a day for it. `pack-contract.md` item 9 calls the questionnaire "the pack's cheapest
high-value output" precisely because it produces the actual estimate, not just a go/no-go.
An inventory that runs long is usually not scoping the migration anymore -- it has become
the migration, with a Class A finding being resolved live instead of recorded and batched
at the gate the way `findings-report.md` requires.

### 2. Translate the declarative core, then validate

Convert the schema (phase 1, `/spicedb-dev:migrate-schema`) and hold it to `zed validate`
plus the `schema-validator` agent (phase 2) before touching any application code. The `.zed`
schema is checked against its own assertions here, not against a store carrying real data or
a call site expecting a particular answer -- this is the cheapest point in the whole
migration to catch a mistranslation, and the last point at which fixing one is free.

`findings-report.md`'s Class A/B/C taxonomy governs what may pass this point: never convert
past an unresolved Class A finding, and a Class B finding (a naming or ID normalization that
changes stored data) must be seen and owned before phase 3 touches a single relationship.
Get this step's ordering backwards -- writing call sites against a schema that hasn't been
validated -- and the first sign of a translation error becomes a production check answering
wrong, not a local `zed validate` failure.

**Precedent.** AWS's guide to migrating OPA deployments to Amazon Verified Permissions holds
to the identical ordering, for the identical reason:

> "After migrating your policies to Cedar, you need to update your application code to
> integrate with Verified Permissions."

-- Samuel Folkes, "Migrating from Open Policy Agent to Amazon Verified Permissions," *AWS
Security Blog*, 05 Nov 2025
(<https://aws.amazon.com/blogs/security/migrating-from-open-policy-agent-to-amazon-verified-permissions>),
"Application integration changes." The same guide's own best-practice list makes the
validation half explicit too: "Schema-first design: Start with a comprehensive schema design
before writing policies. A well-designed schema makes policy authoring more maintainable"
(Folkes, cited above).

### 3. Migrate one representative resource type end to end

Run phases 3 through 5 -- data, code, tests -- against one resource type before running them
against every type the model has. This is a scoped rehearsal, not a smaller copy of the real
migration done separately: the same commands, the same ID codec, the same
`migration-map.json`, deliberately pointed at a narrow slice so the friction the model, the
data, or the call sites produce shows up while it is still cheap to fix.

Choose the slice for structural realism, not simplicity. A resource type with a real
parent-child relationship exercises the arrow-based permission logic (`->`) a leaf-only type
never touches, and that logic is where a schema-conversion mistake is most likely to hide,
because it is where SpiceDB's rewrite semantics differ most from a flat role check.

**Precedent.**

> "Don't attempt a full overhaul at once. Choose a representative resource (e.g., a
> specific feature set like folders and dashboards) to begin the migration. This allows for
> testing assumptions and gradual generalization."

-- Jo Guerreiro, "Migrating Legacy Access Control to OpenFGA: Strategies, Challenges, and
Lessons Learned," *Keys and Craft*, Apr 22 2025
(<https://jguer.space/blog/migrating-legacy-access-control-to-openfga>), "Start Small."
Grafana's own choice -- folders and dashboards -- is itself a parent-child hierarchy, not an
arbitrary "small" feature; the representativeness is structural, which is why this step is
stated here as "start where the hierarchy is real" rather than "start wherever is easiest."

AWS's guide reaches the same "start small" conclusion from a different axis -- risk, rather
than structure: "Don't attempt to migrate everything at once. Start with basic, low-risk
policies and gradually move to more complex scenarios" (Folkes, cited above, "Incremental
approach"). The two criteria aren't in tension: a resource type that is both a real
hierarchy and low-risk is a better first slice than one that is only either, and a migration
with both available should prefer it.

### 4. Dual-write, shadow-read

The source system stays authoritative. Every write goes to it first -- dual-write exists to
keep SpiceDB's copy current, not to make SpiceDB a source of truth -- and every check is
answered by the source system while SpiceDB answers the identical question in parallel,
unseen by the caller. Disagreements are logged, never enforced: nothing in this step ever
lets SpiceDB's answer change what a user can do.

This is what the differential harness is designed to support, and `/spicedb-dev:migrate-verify`
is the command that emits one: run it once phase 3 has completed with a passed verification, and
it writes a working harness -- in the project's own language, wired to both the source system and
SpiceDB -- into the customer's repository. `differential-harness.md` defines the contract that
harness satisfies -- dual-run, diff, replay, and snapshot-to-assertions, plus the safety property
that a harness failure must never deny or grant anything in the live path. The harness's mechanics
belong to that reference, not this one; this file states only the operational role the step plays
in a cutover, which is unchanged by the harness now being generated rather than hand-built: the
source system stays authoritative, dual-write and shadow-read run beside it, exactly as the
precedents below describe.

**Precedent.** Grafana names both halves of this step explicitly, as two practices to
implement together, not one:

> "Implement shadow calls to verify the new access control service is working as
> expected."
>
> "Implement dual-write capabilities, ensuring changes write to both the legacy and new
> systems simultaneously."

-- Jo Guerreiro, cited above, "Implement Shadow Calls" and "Implement Dual-Write."

AWS's guide describes the same shape under a different name -- "audit mode" -- and is
explicit that it must not affect the live answer:

> "Deploy Verified Permissions alongside your existing OPA infrastructure and route a small
> percentage of authorization requests to the new system. Log and compare results between
> both systems, focusing on non-critical operations initially to minimize risk during the
> transition process."
>
> "Start in audit mode: Calculate and log the policy decisions for both systems. This will
> help you to compare results without impacting runtime authorization."

-- Folkes, cited above, "Parallel deployment" and "Start in audit mode."

### 5. Reconciliation job

Assume drift is normal, not exceptional. A dual-write path that never drops a write and a
backfill that never misses a row are both things to verify, not things to assume -- treat
every gap between the two systems' state as an expected, recurring event with its own job,
not an incident.

Some of that drift is not a surprise at all -- it was named at the gate. `pack-contract.md`
item 6 defines what a sync obligation is; `data-mapping.md`'s "Sync obligations"
section documents, for OpenFGA sources, exactly which schema-conversion decisions create
one. `findings-report.md`'s `## Sync obligations` table in
`migration-plan.md` (`obligation | source | write path | backfill | reconciliation`) is
where each one is already recorded, one row per obligation, before cutover ever begins --
this step is where those rows become a running process, not new information. A plan whose
table reads `None.` still needs this step, for drift the schema-conversion decisions didn't
anticipate; a plan with rows in it needs this step for the drift the pack already told you
to expect.

**Precedent.** Grafana treats one-time data migration as insufficient on its own, for a
reason that generalizes past their specific on-premise/cloud split -- any two systems kept
in sync by application-level writes can fall out of sync from a dropped write, a retried
request, or a schema change that outpaces the sync code:

> "On-premise deployments frequently involve upgrades and downgrades. Don't rely solely on
> one-time data migrations. Continuously verify and reconcile the state between the old and
> new systems with periodic reconciliation jobs."

-- Jo Guerreiro, cited above, "Handle Upgrade/Downgrade Cycles."

Backfilling a Zanzibar-style store from an existing system is itself substantial,
purpose-built engineering, not a script -- Airbnb built dedicated tooling for the one-time
half of this problem before Himeji could serve a single check against existing entities:

> "Migrating the existing permission checks into Himeji required us to backfill the
> permission tuples for existing entities. Instead of each data service owner building
> their own backfill flow, we built a generic solution based on Apache Airflow and Apache
> Spark."

-- Alan Yao, Dipak Pawar, Blair Wu, and Abhishek Parmar, "Himeji: A Scalable Centralized
System for Authorization at Airbnb," *The Airbnb Tech Blog*, Apr 20 2021
(<https://medium.com/airbnb-engineering/himeji-a-scalable-centralized-system-for-authorization-at-airbnb-341664924574>),
"Configuration-based backfill." Backfill and reconciliation are different problems -- one
loads a store once, the other corrects it repeatedly -- so this is cited for the weaker of
the two claims this step depends on: even the one-time half of keeping two systems'
permission state consistent was enough work, at Airbnb's scale, to justify generic tooling.
Airbnb's public account does not describe an ongoing reconciliation job, and is not cited as
if it did.

### 6. Cut over per resource type behind a flag, with a fallback path

Move traffic from the source system to SpiceDB one resource type at a time, gated by a flag,
and keep the fallback path live until the flag has been at its new setting long enough to
trust. "Behind a flag" means the flag decides which system answers a check -- not a
deploy-time constant, something that can be flipped back without a deploy.

**Precedent.** Grafana's warning is worth repeating verbatim, because the failure mode it
names is not hypothetical caution -- it is what happens once a toggle exists at all:

> "Feature toggles controlling the migration will be flipped by users/customers. Ensure
> both legacy and new systems have the same behavior. If you fix an undesired corner case in
> the new system, fix it in the legacy system as well. That corner case is for sure going to
> be the reason a user clings to the legacy system."

-- Jo Guerreiro, cited above, "Plan for Toggles." The consequence: a flag is not a private
implementation detail an engineering team fully controls the schedule of. Once it exists,
someone outside the team decides when it flips, in both directions, and the two systems must
already agree before that happens -- which is exactly what step 4's shadow-read period is
for.

AWS's guide describes the fallback half of this step as a first-class mechanism, not an
afterthought:

> "Use feature flags to control the migration process through various flag types. These
> include percentage-based rollout... Feature flags provide several benefits, including
> instant rollback capability if issues arise, granular control over migration scope, A/B
> testing of authorization decisions, and safe experimentation with new policies."

-- Folkes, cited above, "Feature flag implementation." The same guide's gradual-rollout
phase pairs the flag with an explicit circuit breaker: "Gradually increase the percentage of
requests routed to Verified Permissions while monitoring system performance, error rates,
and authorization accuracy. Implement circuit breaker patterns to fall back to OPA if
needed..." (Folkes, cited above, "Gradual traffic shift").

A flag is not the only mechanism that produces a per-resource, reversible cutover. Carta
built a compatibility proxy instead, so existing call sites could be served by the new
system without every consumer changing its own code on the migration's schedule:

> "People wanted to use the new system to query old permissions. To drive further adoption,
> we implemented a legacy permissions proxy. The proxy enables teams to call AuthZ for
> legacy permissions instead of using the JWT token... But the proxy encouraged a single
> interface. It is much easier to migrate customers on a single interface than it is on two
> disparate systems."

-- Aaron Tainter, "AuthZ: Carta's highly scalable permissions system," *Building Carta*,
Jun 11 2021
(<https://medium.com/building-carta/authz-cartas-highly-scalable-permissions-system-782a7f2c840f>).
This is a differently-shaped answer than a flag routing between two backends -- it moves the
backend first and lets consumers adopt the new call surface at their own pace -- and it is
included here as a real alternative to a flag, not a second description of the same
mechanism: cutting over incrementally, with a path back if a consumer isn't ready.

### 7. Remove the source system only after reconciliation has been quiet for a full usage cycle

Decommissioning the source system is the last step, not a cleanup task -- keep it running,
patched, and reachable until step 5's reconciliation job has found nothing to reconcile for
a full cycle of how the product is actually used (a billing cycle, an academic term,
whatever period would surface a code path that only runs occasionally). A quiet week is not
the same evidence as a quiet cycle; infrequent code paths are exactly the ones a premature
removal breaks.

**"Quiet" and "compared" are not the same claim, and only one of them is evidence.** A
reconciliation job that found nothing because nothing was ever successfully compared reads
exactly like one that found nothing because the two systems agree. Where step 4's differential
harness is the thing producing that evidence, `differential-harness.md`'s "The health gate"
states the minimum a quiet cycle has to clear before it counts -- a floor on *distinct*
questions that actually compared, bounded rates of records that could not, an explicit list of
`(resource type, permission)` pairs that produced no records at all, and a breadth condition
requiring the floor to survive dropping each pair's busiest resource and busiest subject. Check
it before reading quiet as agreement.

**And read a passing gate for what it is.** Every one of those conditions ranges over the
questions production happened to ask; none ranges over the schema. A pair can clear all four
while a permission's arrow, wildcard, or caveated branch was never exercised once -- volume and
spread are evidence, but they are not coverage. Enumerating each pair's resolution paths and
confirming a comparable record for each remains the customer's own work, alongside the judgment
this step already assigns them: whether the cycle was long enough for this product.

**Precedent.** AWS's guide keeps the source system running deliberately past the point
traffic has moved, and ties removal to a confirmed, monitored condition rather than a
calendar date:

> "Route all traffic to Verified Permissions but keep OPA infrastructure running
> temporarily. Monitor system behavior under full production load and decommission OPA
> infrastructure after stability is confirmed and you are confident in the performance of
> the new system."

-- Folkes, cited above, "Full migration." AWS's own phrase is "after stability is
confirmed," not "a full usage cycle" -- the stronger, cycle-length bar is this file's own
recommendation, made because a stability check run over days can still miss a path that only
executes monthly or quarterly.

Grafana's guidance points the same direction from the other end -- not "when to remove it"
but "what to do with it while you can't yet":

> "Migrations take time. Stabilize the legacy system but prevent its expansion by directing
> new development towards the new access control service."

-- Jo Guerreiro, cited above, "Contain the Legacy System."

Carta's own account is honest about how long this step actually takes in practice: their
write-up on the compatibility proxy from step 6 ends not with its removal but with an
open item:

> "We're working to replace the legacy permissions proxy with native AuthZ calls."

-- Tainter, cited above. Read against the rest of Carta's account, this is not a
counterexample to "remove it once it's quiet" -- it is evidence that "quiet" is a real bar
that takes real teams a long time to clear, which is why this step is a separate, gated
decision rather than an assumed conclusion of steps 1 through 6.

## What the plugin does not automate

Phases 0 through 5 are commands, and steps 1 through 4 of this playbook run on top of them
today -- `/spicedb-dev:migrate-verify` closed the step-4 gap this section used to describe.
Steps 5 through 7 are a permanent gap: they have no command behind them by design, and saying
otherwise would overstate what this plugin is for:

- **The reconciliation job (step 5) is the customer's to build and run.** The plugin
  surfaces *what* needs reconciling -- `migration-plan.md`'s `## Sync obligations` table --
  and the differential harness (step 4) can show *that* drift exists on a given run. Neither
  writes the job that runs on a schedule, alerts on drift, or decides how to resolve a
  disagreement once found.
- **The flag, and the decision of when to flip it per resource type (step 6), are the
  customer's.** Feature-flag infrastructure is specific to the customer's own deployment,
  and the decision of which resource type is safe to cut over next depends on data --
  differential-harness output, error budgets, on-call load -- that only the customer has.
- **The decision to remove the source system (step 7) is the customer's, and is not a
  technical decision alone.** It is a statement that the team is willing to lose the
  fallback path, made by whoever owns that risk -- not something a tool can certify on a
  team's behalf, no matter how quiet the reconciliation job has been.

`tools/migration-harness/` -- the corpus-validation harness this plugin's own pack
development uses (`pack-contract.md` item 10) -- is a different tool for a different
purpose: it proves the *converter* is correct against a corpus this project controls, not
that any customer's migration is correct against traffic this project will never see. It is
internal, unshipped, and has no role in any step above.
