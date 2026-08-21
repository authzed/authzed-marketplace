# Differential-harness source adapter: Oso Cloud

The Oso side of the contract `migrating-to-spicedb/references/differential-harness.md`
defines. That file owns the record shape, the five states, the diff rules, and the health
gate; this file supplies only what is Oso-specific.

## `observe()` and `ask()`

The harness distinguishes **observing** production traffic (never calls the source) from
**asking** the source a question during replay (the one live call). For Oso:

- `observe()` reads the application's existing Oso call and its answer. Oso's `authorize`
  returns a boolean, so the source side of a record is `ALLOWED` or `DENIED` with no third
  state to preserve.
- `ask()` issues `POST /authorize` with the same actor, action, resource, and any context
  facts the original call carried. **Context facts are part of the question**: replaying
  without them asks a different question and produces a false disagreement.

## Mapping Oso answers to the five states

| Oso result | Record state |
|---|---|
| `{"allowed": true}` | `ALLOWED` |
| `{"allowed": false}` | `DENIED` |
| HTTP error, timeout, or transport failure | `ERRORED` |
| A `list` call that returned a runtime error for an unsolvable variable | `ERRORED` -- see below |
| — | Oso has no `CAVEATED` analogue; only the SpiceDB side can produce it |

**`CAVEATED` on the SpiceDB side against a boolean source is not automatically a
disagreement.** It means SpiceDB needed context the call did not supply. Per the harness
contract, re-ask at `at_least_as_fresh` and, if it stays `CAVEATED`, record
`INCONCLUSIVE`/`CAVEAT_GAP` rather than scoring it either way -- it is a finding about the
conversion, not about the data.

## The asymmetry that produces false disagreements

Oso's `authorize` and `actions` **bind** the resource; `list` and `authorize_resources`
must **solve** for it. A policy can be checkable but not enumerable, so the same rule can
answer correctly through `authorize` and return a 400 through `list`. When the harness
compares a SpiceDB `LookupResources` against an Oso `list`:

- An Oso `list` error is `ERRORED`, and diff rule 1 outranks everything else -- do not read
  it as SpiceDB being wrong.
- A SpiceDB result set that is *larger* than Oso's is the expected shape when the Oso rule
  was silently unenumerable. Investigate before recording it as a SpiceDB defect.

## Consistency during dual-run

Oso's read-your-writes is an in-process `OsoOffset` cached per client instance. The harness
runs in a different process from the application, so **it has no offset and gets whatever
the replica gives it** -- meaning the harness can observe an Oso answer staler than the one
the application saw. Every record must carry the SpiceDB ZedToken, and reconciliation
re-asks at `at_least_as_fresh`; there is no equivalent token to pin the Oso side, so
treat a single Oso/SpiceDB disagreement under concurrent writes as unproven until it
reproduces on re-ask.

## Sampling

`GET /policy` gives the complete permission vocabulary, so the harness can enumerate
question shapes rather than only replaying observed traffic. Use it for the health gate's
breadth condition: a run that only ever exercised three permissions has not established
parity across the policy, however high its agreement rate.
