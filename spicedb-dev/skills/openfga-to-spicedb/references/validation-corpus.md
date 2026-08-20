# Validation Corpus: OpenFGA → SpiceDB

Two different facts, kept apart on purpose: what this pack has actually been run against,
and what it is meant to be run against.

## What was actually run

**All 39 stores** in [`openfga/sample-stores`](https://github.com/openfga/sample-stores)
have been converted, deployed to SpiceDB v1.56.0, and checked assertion-by-assertion against
their own `.fga.yaml` oracle. **38 of 39 reach `PARITY OK`** against that canonical file
(mechanical: `grep -c '^\*\*Final harness run' corpus-runs/README.md` → 39, piped to
`grep -c 'PARITY OK'` → 38). The exception is `abac-with-rebac`: its canonical run against
its own `store.fga.yaml` exits **1** with **`PARITY FAILED`** (two `AMBIGUOUS` findings) --
a documented harness limitation (two mutually exclusive document states flattened into one
comparison), not a conversion defect. The same schema and data reach `PARITY OK` against the
two derived per-scenario store files committed alongside it; see `corpus-runs/README.md`'s
`abac-with-rebac` section. The full run -- every store's own findings, every accounting
correction, and the command that reproduces every comparative claim in this section --
lives in the plugin's source repository at `tools/migration-harness/corpus-runs/README.md`,
not shipped with the plugin; the summary below is drawn from that record, not a substitute
for it.

**The honest summary is that the corpus is exhausted and the pack survived it -- not that the
pack has converged.** 21 of the 39 stores required no pack change at all; 18 filed at least
one finding (`corpus-runs/README.md`'s per-store `### Findings` sections, mechanically
`None.` vs. not -- 39 sections, one per store). The longest unbroken run of zero-finding
stores is **8**, spanning iterations 17 and 18 back to back (batches 6 and 7, 4 stores each);
no longer run exists once same-iteration stores are correctly treated as unordered rather
than assumed to fall in a favorable sequence. **No new *mapping* rule has been forced by a
conversion since iteration 11** (`groups-resource-attributes`) -- every finding from
iteration 12 onward was a worked example or a documentation clarification of a rule already
on file, not a new construct. That is evidence of diminishing returns on this specific
39-store sample, not proof there is nothing left: a construct this pack has never seen is
exactly as unhandled on a 40th store as it was on the first.

**One exception, and it matters more than a mapping rule would:** at iteration 17, a
safety-critical rule was added that a conversion did not force -- the four stores converted
in that same batch were all zero-finding. It is the multi-type-tupleset `__perm`-alias gap in
"Point arrows at permissions, not relations" (`references/schema-mapping.md`): a silent
authorization hole that compiles clean under `zed validate --fail-on-warn` and was found by
review, not by a store. `file-storage` (iteration 16) is the only store in the entire corpus
that uses a multi-type tupleset as an arrow's left operand at all, and it is the one that
forced the underlying alias *rule*; the mechanical detection *script* that catches a
violation was hardened twice more afterward by further review, closing parsing blind spots
rather than responding to a new store. Read that section for exactly what the script does and
does not catch -- it is not being oversold here.

Coverage is uneven across construct families, and the unevenness is informative. Restricted
to the 21 zero-finding stores: wildcards (`knowledge-base`), SpiceDB intersection
(`developer-portal`), type-based multi-tenancy, nested-userset arrow chains, and same-name
recursive arrows are each confirmed clean by at least one store. Two families are not:
**every one of the 8 caveat-bearing stores in the corpus filed at least one finding** --
caveats have never once converted clean -- and the multi-type-tupleset construct has exactly
one bearer in the whole corpus (`file-storage`), which filed a finding too. Neither absence
means the underlying rule is wrong; it means "zero-finding" and "exercised" are different
claims, and only the first is evidence that a construct is easy.

**Several written rules carry zero corpus validation, not just thin validation.** No
committed store's schema exercises the `a - b` exclusion operator (`define view: a but not
b`, rated `clean`) at all. Of the Class A blockers in `references/blockers.md`, **the
transitive wildcard and contextual tuples now have corpus confirmation** (`role-assignments`
and `abac-with-rebac` respectively -- each has its own "Corpus confirmation" passage in that
file, which is the authority on how far the confirmation reaches). **Multi-store tenancy and
model-ID pinning do not**: they remain real, written rules with no corpus store to confirm
the rating against, because both are properties of how an application is *deployed* rather
than of any model a store can carry. Every other rule in `references/schema-mapping.md`,
`references/naming-normalization.md`, and `references/blockers.md` traces to one of the 39
runs above; the exclusion-operator gap and those two blockers do not, and should be treated
as unverified rather than as untested-because-unlikely.

**That is survivable because the pack halts instead of guessing.** A construct with no
rule is reported as an unhandled construct and stops the conversion
(`/spicedb-dev:migrate-schema`, step 5). The failure mode of an unfinished pack is
therefore a stop with a source line attached, not a schema that compiles cleanly and
answers a question differently than OpenFGA did. Treat every halt as the next hardening
input.

The oracle is also partial where it did run: the parity check compares `check:` assertions
only, which is **1436 of 1777 source assertions (80.8%)** summed across all 39 stores from
`corpus-runs/README.md`'s own "Harness-visible fraction" derived set (`Checks / (Checks +
ListObjects + ListUsers)`, per store, from `fga model test`), and as low as 33.3% on
`gdrive`. `list_objects` and `list_users` blocks are not compared by the automated harness
(see the scoping questionnaire, item 5) -- every store's gap was closed instead by direct
live-server verification, recorded in that store's own `corpus-runs/README.md` section.

## What has not been run

**Tier 2 -- application code: zero repositories.** `theopenlane/core` (Go, a production
`fga/` subsystem with its own schema codegen), `openfga/flask-demo` (Python, minimal), and
`embesozzi/keycloak-openfga-workshop` (JS, event-driven) are the intended Tier 2 and have
not been touched. `references/code-mapping.md` and its consumer, `/spicedb-dev:migrate-code`
(phase 4), are both written now (see Status) -- what's missing is exercising either against
a real, pre-existing codebase's call sites, a different and harder test than the clean,
live-verified worked examples both currently rest on.

**Prior art, read but not run:** [`openfga/agent-skills`](https://github.com/openfga/agent-skills)
-- OpenFGA's own model-authoring skills. Models authored with it follow its idioms, so this
pack has to handle them.

