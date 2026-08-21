---
name: Oso to SpiceDB
description: Use when converting an application from Oso Cloud (Polar policies and
  facts) to SpiceDB - supplies the conversion-pack rules the migration pipeline reads:
  policy mapping, blocker catalog, identifier normalization, fact mapping, code
  mapping, and test mapping
---

# Oso to SpiceDB

The conversion pack for **Oso Cloud**. `migrating-to-spicedb` owns the pipeline, the
gate protocol, and the report formats; this skill supplies the source-specific rules
that pipeline reads, per `migrating-to-spicedb/references/pack-contract.md`'s ten items.

Nothing here restates the framework. If you are looking for how phases sequence, what a
Class A finding is, or what `migration-map.json` holds, that is the framework skill.

## What this pack covers

**Oso Cloud** -- the hosted (or self-hosted) service: a Polar policy plus facts, queried
over the Check API. This is the supported source.

**The deprecated `osohq/oso` library is not covered.** The open-source embedded library
(Polar files loaded in-process, host-language classes registered with the runtime) is
marked deprecated by its own repository. Its policies are Polar and much of the policy
mapping below still applies, but its data lives in the application's own objects rather
than as facts, so there is nothing to export and `references/data-mapping.md` does not
apply. If a project is on the OSS library, say so at the gate and treat the data phase as
a hand-written extraction rather than a conversion.

## The shape of the problem, in one paragraph

Oso's declarative core -- `actor`/`resource` blocks with `roles`, `permissions`,
`relations`, and shorthand implication rules -- is close enough to Zanzibar that it
translates mechanically. A `has_role(User{"alice"}, "steward", Lab{"genomics"})` fact
*is* a relationship tuple, with the role string doing exactly the job a SpiceDB relation
name does. What does not translate is everything Polar can express *beyond* that core:
facts with values instead of objects, comparisons between two stored facts, rules with a
variable in the role position, and the local-filtering API that returns SQL. **The cost of
an Oso migration is almost never the schema. It is attributes, list endpoints, and the
data-sync obligations the conversion creates.**

## Detection (pack contract item 1)

An Oso Cloud project is recognized by its SDK dependency, its policy file, or its
configuration:

```bash
# dependency, across manifest formats
grep -rniI 'oso-cloud\|oso_cloud\|osohq\|"oso"\|sqlalchemy-oso' \
  --include=package.json --include=requirements.txt --include=pyproject.toml \
  --include=go.mod --include=Gemfile --include=pom.xml --include=build.gradle \
  --include='*.csproj' .

# policy files
find . -name '*.polar' | grep -v node_modules

# configuration and client construction
grep -rniI 'OSO_URL\|OSO_AUTH\|OSO_API_KEY\|oso-cloud.com\|Oso(' \
  --exclude=migration-plan.md --exclude=migration-map.json \
  --exclude-dir={.git,node_modules,vendor,dist,build,target,.venv,__pycache__} .
```

**A zero from the dependency sweep does not mean "not an Oso project."** A `.polar` file
alone is sufficient evidence, and a project may call the REST API directly with no SDK at
all. Record which form was found -- SDK, REST, policy-file-only, or the deprecated OSS
library -- because it decides what phase 4 can do.

**Distinguish Oso Cloud from the deprecated `osohq/oso` library**, because the data phase
differs completely. Oso Cloud: an `oso-cloud` dependency, an API key, facts held by the
service. OSS library: an `oso` dependency, `Oso()` constructed in-process with host classes
registered, `.polar` files loaded at startup, and **no facts** -- authorization data lives
in the application's own objects. If it is the OSS library, say so at the gate: the policy
mapping still applies, but there is nothing to export.

## Model extraction (pack contract item 2)

The complete model is two calls, and phase 0 should make both before classifying anything:

| Source | What it gives | Use it for |
|---|---|---|
| `GET /policy` | Complete Polar source, **capped at 1 MB** | The free-standing rules, where every hard construct lives |
| `GET /policy_metadata` | Structured resources, roles, permissions, relations | The declarative core -- a ready-made translator input |

Prefer the metadata for the declarative core and the source for everything else: the
metadata does not carry the free-standing `has_permission(...) if ...` rules. A `.polar`
file in the repository is usually the same content and is a valid substitute for the
source, but confirm it matches what is deployed -- a checked-in policy can lag the live one.

## Scoping questionnaire (pack contract item 9)

The cheapest high-value artifact in the pack. `GET /policy` plus one question predicts most
of the migration cost, and both are obtainable in the first call.

1. **Pull `GET /policy` and `GET /policy_metadata`.** Classify every rule against the
   fidelity table below.
2. **Count unary facts.** This is the largest cost line in a typical migration and the one
   most often missed. The count *is* the estimate.
3. **Count predicates by arity**, from `declare` statements -- never inferred from a
   predicate's name, since arity is policy-defined.
4. **Grep for a variable in the role position** and for comparison operators binding two
   rule variables.
5. **Ask: "do you call `listLocal` or `authorizeLocal` anywhere?"** This single question
   separates a materially easier migration from one that may need SpiceDB Materialize.

Also worth establishing early, because it changes what parity means during dual-run: **does
the customer construct Oso clients per request, or read in service B after writing in
service A?** If so they have had no read-your-writes at all -- see `references/code-mapping.md`.

## Validation corpus (pack contract item 10)

**Oso's own sample application (`osohq/gitcloud`)** -- its policy contains every construct
class in this pack: the declarative core, unary facts, boolean-
valued facts, universal grants, self-reference, group inheritance, recursive nested groups,
customer-defined roles, default roles, and Polar test blocks.

**Four policies, 312 comparisons, 0 mismatches.** Each was loaded into Oso's own dev server,
converted, loaded into SpiceDB v1.56.0, and compared question by question:

| Policy | Compared | Result | What it exercised that the others did not |
|---|---|---|---|
| Oso's own sample application | 100 | 0 mismatches *(after a fix)* | Role splits through a three-level arrow chain; a bodiless universal grant |
| Independent app A | 70 | 0, first attempt | Dotted permission names; a relation targeting the actor type; `test fixture`; `iff` assertions |
| Independent app B | 110 | 0, first attempt | `global` blocks and the `global` keyword; unary facts; `or` inside a shorthand; **`role if role on "rel"`** |
| Independent app C | 32 | 0, first attempt | `allow` rules instead of `has_permission`; universal grants via `if true`; a boolean-valued fact; a hyphenated role |

**The `heavy` construct is now verified rather than reasoned.** `role if role on "belongs_to"`
-- a variable in the role position -- appears in a real third-party policy, and expanding it
per declared role (`blockers.md` item 3's option) reproduced Oso's answers exactly.

**Three of the four are independent applications, not Oso's own**, which makes them the
better test: a vendor's sample can only show that the pack agrees with the system it was
derived from. They were found by code search over public repositories using the Oso SDK.
Between them they carry dotted permission names, a relation whose target is the actor type
used as a role source, tautological rules, `test fixture` blocks, `iff` assertions, and the
`role if role on "rel"` construct this pack rates `heavy`.

**Validate against it rather than against the documentation.** Oso's shipped sample apps
contain constructs the documentation never demonstrates -- `and` inside a shorthand
right-hand side is the notable one -- and a docs-driven converter silently mistranslates
them. See `references/policy-mapping.md` on why the published grammars are not a semantic
spec either.

**What is evidenced.** `gitcloud`'s policy was converted and then checked against a
**live Oso** rather than against the documentation: Oso's own dev server running the real
policy, SpiceDB v1.56.0 running the converted schema, the same facts loaded into both, and
**100 (actor, permission, resource) questions compared across all three resource types --
0 mismatches.** The schema also passes `zed validate --fail-on-warn` and reproduces
gitcloud's own Polar test block.

**Run Oso locally; it is free and it makes this cheap.** `public.ecr.aws/osohq/dev-server`
starts in test mode and prints the token to use. `POST /api/policy` with `{"src": "..."}`
loads a policy, `POST /api/facts` inserts one fact, and `POST /api/authorize` answers a
question. That is enough to build the differential above, and enough for
`/spicedb-dev:migrate-verify` to dual-run without touching the customer's environment.
**The SDKs work against it too**, which makes the whole code surface testable locally:
point the client at the dev server's URL with its printed token and `policy()`, `insert()`,
`authorize()`, `list()`, `actions()`, `get()`, and `delete()` all behave. gitcloud's own
compose file wires its services this way.

**`GET /api/policy_metadata` 404s only when no policy has been loaded**, and returns 200
once one has -- verified on a fresh server before and after a `POST /api/policy`. An early
draft of this pack recorded that 404 as "the dev server lacks the endpoint," which was
reading an empty state as a missing capability. It is there.

**The differential earned its place immediately.** The first run disagreed on 5 of 100, all
of them `read` on `Organization` for every actor including one with no facts at all. The
cause was a dropped universal grant: gitcloud's `has_permission(_: User, "read", _:
Organization);` has no body, and the hand conversion had simply not emitted a `read`
permission. `references/policy-mapping.md` warns about exactly that rule shape, and the
conversion violated its own rule anyway -- **a review that reads the policy will miss this;
a differential will not.**

**What is evidenced on the code side.** The SDK call surface was enumerated mechanically
from the client rather than from the documentation, and exercised against the dev server:
`authorize`, `list`, `actions`, `insert`, `get`, and wildcard `delete` all verified live.
`list` returns bare id strings with no type, as `code-mapping.md` says, and a wildcard
`delete` with `None` arguments removes matching facts.

**A phase-4 conversion has now been run end to end.** One independent application's seed
program writes its authorization data through `oso.batch()` with `tx.insert(...)`, and clears
it with wildcard `oso.delete((pred, None, None, None))`. Its call sites were converted per
`references/code-mapping.md` and both programs run against their own live systems over
identical entities: **165 comparisons, 62 allowed and 103 denied, 0 mismatches**, and the
run is idempotent -- executing it twice leaves the same relationship count, which is what
actually exercises the delete conversion, since on a first run the deletes have nothing to
remove.

That verifies four mappings that had been documentary: `batch()` + `tx.insert` to one
`WriteRelationships`, a middle-argument wildcard delete to **one `DeleteRelationships` call
per relation**, `Value(type, id)` to `type:id`, and unary facts to wildcard relations.

**Context facts are verified, including their failure mode.** A real application's
per-request edge facts were compared three ways -- Oso with them passed, SpiceDB without
them written, SpiceDB with them written. The middle column gets **three of four answers
wrong**, and every one of them is a *denial*: nothing errors, no check looks broken, and the
conversion cannot detect it. `references/data-mapping.md` carries the table.

**What is not.** Fact export **at scale** -- the 1000-per-call developer cap, sharding a
large export, the absent pagination -- cannot be exercised locally, because the dev server's
facts go in through the same API they would come out of and it does not enforce the hosted
limits. The same is true of `list`'s page-size rule. And no end-to-end phase-4 conversion of
a real application's call sites has been run. Treat those as less evidenced than the schema
mapping, and say so at the gate rather than implying otherwise.

**One thing found while converting, worth repeating:** gitcloud ships **two** `.polar`
files -- `policy/authorization.polar` and `services/jobs/src/authorization.polar`. A
project can carry more than one policy, for more than one service, and converting only the
first produces a schema that is correct and incomplete. Enumerate them.

## Fidelity at a glance

Full table with per-construct rules in `references/policy-mapping.md`. Summary:

| Rating | What lands here |
|---|---|
| `clean` | Resource/actor blocks, roles, permissions, relations, shorthand implications, `on` arrows, global blocks, negation, recursive rules |
| `effort` | Unary facts (attributes), context facts, `list`, `actions`, wildcard deletes, customer-defined role *data* |
| `heavy` | An open-ended permission vocabulary, a variable in the role position, facts with arity above three |
| `blocked` | `listLocal`/`authorizeLocal`, comparison across two stored facts, multi-variable Query Builder |

## Reference files

| Need to... | Read this |
|---|---|
| Translate a Polar construct to `.zed` | `references/policy-mapping.md` |
| Detect a `heavy`/`blocked` construct and offer options | `references/blockers.md` |
| Turn a Polar string into a legal SpiceDB identifier | `references/naming-normalization.md` |
| Export facts and transform them into relationships | `references/data-mapping.md` |
| Rewrite Oso SDK call sites | `references/code-mapping.md` |
| Convert Polar `test` blocks to validation YAML | `references/test-mapping.md` |
| Dual-run Oso beside SpiceDB | `references/source-adapter.md` |

## Red Flags

If you find yourself:
- Translating a policy by reading Oso's **published grammar** rather than real policies --
  stop. `osohq/tree-sitter-polar` and `osohq/polar-grammar` are deliberately permissive:
  they parse multi-hop `on` chains the real parser rejects, and several tokens lex but are
  refused. They are not a semantic spec. See `references/policy-mapping.md`'s corpus note.
- Assuming a predicate's arity from its name -- arity is policy-defined. The same
  predicate name is unary in one real policy and binary in another.
- Reporting a customer-defined-role policy as `blocked` -- most of it maps as data. See
  `references/blockers.md`.
- Treating the schema as the hard part -- it usually is not. Count unary facts and grep for
  `listLocal` before estimating anything.
