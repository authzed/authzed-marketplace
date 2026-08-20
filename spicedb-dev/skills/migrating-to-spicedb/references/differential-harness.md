# Differential Harness

This file defines what a differential harness *is* -- the record shape it produces and the
four capabilities built on that shape -- before any code is written against it. It is
framework-owned and source-agnostic, the same way `pack-contract.md`, `findings-report.md`,
and `cutover-strategies.md` are: nothing below depends on which source system a migration
started from, and a future pack inherits this contract unchanged, supplying only an adapter
that translates its own source's answers into the vocabulary defined here. A pack supplies
the *what* of a conversion (`pack-contract.md`); this file supplies the *what* of proving
that conversion holds against traffic no corpus or test suite ever sees.

**This document is the contract, not the implementation.** Two things consume it: a per-source
`source-adapter.md` (the seam a pack implements -- given a question in this file's vocabulary,
ask the source system and translate its answer back into that same vocabulary) and
`/spicedb-dev:migrate-verify`, the command that emits a working harness satisfying this
contract into a customer's own repository. Both now exist -- `openfga-to-spicedb`'s
`references/source-adapter.md` implements the seam for OpenFGA, Okta FGA, and Auth0 FGA, and
`/spicedb-dev:migrate-verify` is the command that emits the harness. Neither redefines this
file's vocabulary or rules; both cite it rather than restating it, the same discipline this
file expects of any future pack's adapter.

**What ships is a specification, not a service this project operates.** The harness this
file describes runs entirely inside a customer's own infrastructure, against their own
traffic, using their own SpiceDB client and their own source client -- it has no runtime
dependency on this repository at all. That is what makes it *the customer's* harness: it is
built once, by `/spicedb-dev:migrate-verify`, and then it is theirs to run, extend, and
eventually retire, the same way the schema, data, and code phases 1-5 emit are theirs once
converted.

## Where this sits in the pipeline

`cutover-strategies.md` step 4, "Dual-write, shadow-read," is this file's only consumer in
the seven-step cutover playbook: the source system stays authoritative, every check is
answered by it as usual, and SpiceDB answers the identical question in parallel, unseen by
the caller, with disagreements logged and never enforced. That file states the operational
role this step plays in a cutover; this file states the mechanics -- the four capabilities
below (dual-run, diff, replay, snapshot-to-assertions) are exactly the ones it names and
declines to define further, deferring to here.

The four capabilities, in the order this file defines them:

| Capability | What "correct" means, in one line |
|---|---|
| Dual-run | Ask SpiceDB the same question a production request just answered, in parallel, off the request's critical path, with zero write risk. |
| Diff | Compare the two answers using the record shape below -- never by collapsing either side to a bare boolean. |
| Replay | Re-issue a captured question later, against a schema or dataset that has since changed, and get a result comparable to the one captured at capture time. |
| Snapshot-to-assertions | Turn a *confirmed agreement* into SpiceDB validation YAML compatible with what `/spicedb-dev:migrate-tests` already produces. |

## The record shape

Every other section in this file depends on this one, and it is written first for that
reason. A comparison whose record shape cannot carry a distinct value for "the call errored"
will store that error as whatever the record's zero-value happens to be -- and if that
zero-value is the same value a genuine denial would carry, the two become indistinguishable
downstream, permanently. No amount of comparison logic recovers information a data structure
never had a slot for; the fix has to happen here, before the first line of dual-run or diff
logic exists.

### Question

What was asked, in a form neutral to which system answers it:

```yaml
question:
  resource: "<type>:<id>"      # SpiceDB-side vocabulary; the source adapter's job is
                                # mapping this onto whatever the source calls the same object
  permission: "<name>"         # the permission or relation being asked about
  subject: "<type>:<id>"       # "<type>:<id>#<relation>" for a userset subject
  context: {}                  # optional request-time attributes, canonicalized the same
                                # way a pack's test-mapping reference canonicalizes caveat
                                # context (`pack-contract.md` item 8; sorted keys, compact
                                # separators) so two records asking the same question hash
                                # identically
  request_id: "<opaque correlation id>"   # ties a record to the production request that
                                            # triggered it -- never to anything that gates it
  asked_at: "<RFC3339 timestamp>"
  origin: CHECK | BATCH_CHECK | LIST_SAMPLED   # which call shape this question came from --
                                                # required, no default; see below
```

**`origin` is required, and it exists because a rule stated in three separate files of this pack
-- here, in `/spicedb-dev:migrate-verify`'s step 7, and in `source-adapter.md`'s
`listObjects`/`listUsers` section -- is otherwise unenforceable.** (Cited by location, not
counted: a tally of how often a pack repeats itself is measured at read time, and one written
down inside the same edit that does the repeating is circular besides.)
Snapshot-to-assertions must never consume a record derived by
sampling out of a list result, and enumeration-shaped operations are explicitly a "distinct,
lower-confidence comparison mode" ("What is not comparable at all," below). But a sampled
`listObjects` probe becomes an ordinary single-resource, single-subject, single-permission
question the moment it is re-asked as a check -- byte-for-byte indistinguishable from a record
that came from a real `check` call site. Downstream code cannot honor a distinction the record
does not carry: told "never feed a list-derived record into snapshot-to-assertions," a
`snapshot_to_assertions` implementation looking only at `verdict: AGREE` has no field to test,
and complies or doesn't by accident of which records happen to reach it.

This is the same class of defect as the error-collapse the "Outcome" vocabulary below exists to
prevent -- a distinction the rules depend on that no variant of the record can express -- and it
gets the same fix, in the record shape rather than in the rules: `LIST_SAMPLED` marks a record as
ineligible *structurally*, so eligibility is a field test rather than a convention. `CHECK` and
`BATCH_CHECK` are kept apart because a per-item batch result has its own correlation and
per-item-error handling that a single check does not, and a triage view that cannot separate them
is reading two failure modes as one. Do not default the field: a record whose origin is unknown
is not a `CHECK` record, and treating it as one is precisely the guess this field exists to
prevent.

### Outcome

The vocabulary both sides' answers are translated into. **Every source adapter must
translate its own answer -- boolean, exception, timeout, whatever shape its client returns --
into this same closed vocabulary before a record is ever compared.** A source with no
check-shaped answer at all has nothing for this contract to consume.

| State | Meaning | Which sides can carry it |
|---|---|---|
| `ALLOWED` | The system evaluated the question and the answer was yes. | source, target |
| `DENIED` | The system evaluated the question and the answer was no. | source, target |
| `CAVEATED` | The system evaluated the question and the answer depends on request context that was not supplied. | target only |
| `ERRORED` | The system was asked and returned a failure instead of an answer. | source, target |
| `NOT_ANSWERED` | No answer ever reached the harness -- the system was never asked, or was asked and nothing came back before the deadline. | source, target |

This is the four states the design constraint requires (`ALLOWED`, `DENIED`, `ERRORED`,
`NOT_ANSWERED`), plus one addition: `CAVEATED` is real on SpiceDB's side -- caveats are
native to it -- and has no independent equivalent on a source whose check API is strictly
boolean. **Verified** against a live v1.56.0 instance: a relationship written with a caveat
but no bound context, checked with no context supplied either, returns neither `true` nor
`false` from `zed permission check` but the distinct string `caveated`; the wire enum behind
it (`CheckPermissionResponse.Permissionship`) has three non-error values --
`PERMISSIONSHIP_HAS_PERMISSION`, `PERMISSIONSHIP_NO_PERMISSION`,
`PERMISSIONSHIP_CONDITIONAL_PERMISSION` -- mapping onto `ALLOWED`/`DENIED`/`CAVEATED` above.
Because a source has no fourth value to answer with, a `CAVEATED` target outcome can never be
compared against a source outcome as if the two were answering the same yes/no question --
see "What counts as a disagreement," below.

`ERRORED` and `NOT_ANSWERED` are distinct on purpose. `ERRORED` means the system was asked
and answered with a failure -- a real event, from a real call. `NOT_ANSWERED` means the
harness itself never got an answer to log: the question was sampled out, a queue was full
before dispatch, a client-side deadline expired before any response arrived, the observation
point that captures the production decision missed it, or a replay batch skipped it
deliberately. Neither may ever be coerced into `DENIED`. **Verified**:
checking a permission against an object type the schema doesn't declare returns a gRPC
`FailedPrecondition` error and a non-zero exit from `zed`, not `false` -- confirming that
SpiceDB's own wire behavior already keeps "denied" and "the call failed" apart; a harness
collapsing the two on top of a protocol that already distinguishes them would be adding the
defect, not inheriting it.

**This vocabulary assumes a source whose own answer space is effectively binary** -- allow or
deny, the shape a Zanzibar-style relationship-graph check API answers with, and the shape
`ALLOWED`/`DENIED` are built around. Not every source is shaped that way: a policy-language
system can have more than two non-error outcomes of its own -- XACML's standard decision
vocabulary, for instance, is `Permit`/`Deny`/`NotApplicable`/`Indeterminate`, not a plain
boolean. Forcing a source's `NotApplicable` or `Indeterminate` into `ERRORED` would
misrepresent it -- neither is a failure -- and forcing it into `NOT_ANSWERED` would be a
stretch, since the source did answer, just not with a value this vocabulary has a slot for.
This contract does not yet define how a source with more than two non-error outcomes maps in;
a pack built for one should extend the source-side vocabulary explicitly -- documenting its
own additional outcome states and exactly how Diff treats each one -- rather than force-fit
them into `ALLOWED`/`DENIED`/`ERRORED`/`NOT_ANSWERED` and lose the distinction silently.

One limitation this record shape cannot fix, and should not claim to: if a source's own
client already swallows an error into a false denial *before* the point a source adapter
observes it (the exact fail-closed hazard `pack-contract.md` item 7 names for converted call
sites), the adapter never sees `ERRORED` to report -- it sees whatever the client handed
back, already collapsed. The record shape guarantees SpiceDB's own path can't be swallowed
this way inside the harness; it can only guarantee the source's path so far as the adapter's
observation point sits above the swallowing. A source adapter should say plainly, for each
call surface it observes, whether it sits above or below that point, rather than implying
uniform error visibility it doesn't have.

### DifferentialRecord

One record per question, holding both sides' answers and the comparison between them:

```yaml
question: { ... }               # as above

source:
  outcome: ALLOWED | DENIED | ERRORED | NOT_ANSWERED
  detail: "<error message/code>"      # ERRORED only
  marker: "<opaque, source-specific>" # a consistency/version handle, if the source exposes
                                        # one; carried for audit, never compared directly --
                                        # see Diff, which does not depend on this being present

target:
  outcome: ALLOWED | DENIED | CAVEATED | ERRORED | NOT_ANSWERED
  detail: "<error message/code, or the unresolved caveat/context names>"
  zedtoken: "<the revision this answer was actually served at>"

comparison:
  verdict: AGREE | DISAGREE | INCONCLUSIVE
  disposition: null | UNTRIAGED | CONFIRMED_DEFECT | ACCEPTED_DIVERGENCE   # DISAGREE only
  reason: null | STALE_READ | CAVEAT_GAP | RECONCILIATION_FAILED
        | SOURCE_ERROR | TARGET_ERROR | SOURCE_NOT_ANSWERED | TARGET_NOT_ANSWERED
                                                                            # INCONCLUSIVE only
                                # every token here is produced by a rule in Diff, below; a token
                                # no rule can emit is dead vocabulary that invites a hand-rolled
                                # variant to invent its own meaning for it

reconciliation:                 # present only when Diff rule 4's re-ask ran; omitted/null
                                 # on a record that never reached candidate-DISAGREE status
  attempted: bool
  attempts: <int>                # how many bounded re-asks were actually made
  outcome: ALLOWED | DENIED | CAVEATED | ERRORED | NOT_ANSWERED | null   # the re-ask's own
                                                                          # answer -- null only
                                                                          # if attempted: false
  zedtoken: "<the revision the re-ask was actually served at>"          # null if attempted: false
```

**`reconciliation` records the re-ask's own evidence, separately from what it did to the
verdict.** Rule 4's `STALE_READ` branch says to "keep the original record rather than discarding
it -- it is confirmatory evidence about the write path's timing, not the authorization
model" -- that
guidance is only followable if the record shape has somewhere to keep the re-ask's own answer and
the revision it was served at. Without this block, only the fact that reconciliation ran and
reclassified the verdict survives; the re-ask's own `zedtoken` -- the one piece of evidence that
would let a customer later measure their own actual staleness window from a batch of `STALE_READ`
records, the same way this file's own "Verified" staleness measurements above were produced -- is
otherwise discarded the moment reconciliation completes. `reconciliation.outcome` and
`reconciliation.zedtoken` are populated identically whichever of rule 4's four branches the
re-ask lands in (`STALE_READ`, finalized `DISAGREE`, `RECONCILIATION_FAILED`, or `CAVEAT_GAP`); a `DISAGREE`
finalized *without* reconciliation ever having been attempted (which does not occur under rule 4
as written, since every candidate disagreement is reconciled, but could occur in a hand-rolled
variant that skips it) leaves this block `attempted: false` rather than fabricating zero values
for a re-ask that never happened.

`source.outcome` is populated by **observing** the production request's own already-computed
decision, not by issuing a second call to the source system. This matters for the safety
property below: **in dual-run**, the harness's only outbound call is the one to SpiceDB. It adds
no incremental read load, and no incremental risk of any kind, to the source system -- exactly
the shape `cutover-strategies.md` step 4 describes ("every check is answered by the source
system while SpiceDB answers the identical question in parallel, unseen by the caller"). Replay's
parity mode is the one capability that does call the source, deliberately and offline; see the
safety property's own point 1 for how that is bounded.

## Dual-run

**Correct behavior:** for a sampled production request, fire one additional, out-of-band call
to SpiceDB asking the identical `Question` the production request just resolved against the
source, and record the result as a `DifferentialRecord`. Three properties make this safe
rather than merely functional:

- **Off the critical path.** The SpiceDB call is issued after (or alongside, on a separate
  goroutine/thread/queue) the request that actually needs an answer, and its result is never
  awaited by that request. A slow or hung SpiceDB call, once the harness's own deadline
  expires, produces `NOT_ANSWERED` -- never `ERRORED`, which is reserved for a response that
  actually came back reporting failure; a deadline exceeded with nothing on the wire is the
  harness giving up waiting, not SpiceDB reporting anything. Either way it never adds latency
  to the production response, which has already been served by the source system alone.
- **No new write surface, on either system.** Dual-run makes exactly one call per sampled
  question -- `CheckPermission` (or the bulk/lookup equivalent, subject to "What is not
  comparable at all," below) -- and never a write. Recording the resulting
  `DifferentialRecord` is itself best-effort and asynchronous; a failure to persist one is a
  lost data point, not a failed request.
- **`minimize_latency` by default, not `fully_consistent`.** Dual-run exists to compare
  steady-state answers under realistic conditions, not to prove the two systems agree at the
  instant of a write -- `fully_consistent` bypasses SpiceDB's cache and doesn't scale
  (`consistency-deep-dive.md`, "Common Mistakes"), and using it for the bulk of dual-run
  traffic would reintroduce the latency risk this capability exists to avoid. The consequence
  -- a check fired immediately after a relevant write can legitimately answer stale -- is real
  and is handled in Diff, not avoided here by paying for full consistency on every call.

Always capture the target's `CheckedAt` token, regardless of which consistency was requested.
**Verified** against a live v1.56.0 instance: `CheckPermissionResponse.checked_at` comes back
populated on every response, including under `minimize_latency` -- the wire protocol always
reports which revision a check actually answered at, which is what makes the staleness
reconciliation in Diff possible without any special access to the write path.

## Diff

**Correct behavior:** compare a `DifferentialRecord`'s two outcomes and produce a `verdict`,
never by testing `source.outcome == target.outcome` on raw values and never by treating
either side's absence of an answer as if it were `DENIED`. The rule, applied in this order:

1. **Either side `ERRORED` or `NOT_ANSWERED` &rarr; `INCONCLUSIVE`.** This check runs first
   and unconditionally. An errored or unanswered side is not an authorization answer to
   compare against anything -- it is a report that the comparison itself couldn't run. This is
   the rule that exists specifically to prevent the collapse the design constraint warns
   about: a record shape and a comparison rule that both, independently, refuse to let "the
   call failed" read as "the call said no." **The verdict here is `INCONCLUSIVE` regardless of
   which side failed or how many did** -- one side failing is enough, and a second side also
   failing changes nothing about the verdict. What changes is only which `reason` token gets
   attached, and that needs its own fixed rule, because both `source` and `target` can be in a
   non-answer state at once (a source-side observation gap during the same window SpiceDB
   itself was unreachable, for instance) and `reason` holds exactly one value.

   **`reason` precedence, applied in this order, stops at the first match:**
   1. `TARGET_ERROR` -- the target returned a failure. Checked first because the target call
      is the harness's own outbound request (Dual-run, above); a response that came back
      reporting failure is the most direct, most actionable signal the harness has, and it
      takes priority over anything the source side did.
   2. `SOURCE_ERROR` -- the source returned a failure and the target did **not** return a
      failure. The target's own state is otherwise irrelevant here: it may have answered
      normally (`ALLOWED`/`DENIED`/`CAVEATED`) or not answered at all (`NOT_ANSWERED`) -- either
      way, the source's returned failure is the reason this comparison could not run. A returned
      failure outranks a bare non-response, so an `ERRORED` source beats a `NOT_ANSWERED` target
      here.
   3. `TARGET_NOT_ANSWERED` -- the target never answered and neither side reported `ERRORED`.
   4. `SOURCE_NOT_ANSWERED` -- the source alone is `NOT_ANSWERED` and neither side reported
      `ERRORED`; the target answered normally.

   **These four clauses are exhaustive over every combination rule 1 can route here, and that
   exhaustiveness is the point** -- a precedence that lets a real combination fall through
   matches nothing, leaves `reason` unset, and defeats the "two implementations label the same
   failure combination identically" guarantee this list exists to provide. The combination worth
   naming explicitly, because it is the most common one and because an earlier revision of this
   list scoped clause 2 narrowly enough to exclude it: a source that `ERRORED` while the target
   answered normally is `SOURCE_ERROR`, by clause 2. Implementations should assert this rather
   than assume it -- if a record reaches the end of this precedence with no clause matched, that
   is a defect in the implementation of this list, not a fifth category.

   This precedence decides only which single `reason` token a comparison attaches for triage
   grouping -- it does **not** discard information. `source.outcome`/`source.detail` and
   `target.outcome`/`target.detail` are independently required fields on every
   `DifferentialRecord` (see the record shape, above) and stay populated with each side's own
   real outcome regardless of which one `reason` names; anyone inspecting a record still sees
   the full truth of what happened on both sides. Two implementations following this
   precedence will label the same failure combination identically, which is the property this
   rule exists to guarantee -- and, unconditionally, neither a `TARGET_ERROR` nor a
   `SOURCE_NOT_ANSWERED` label, nor any label this precedence can produce, is ever attached to
   a verdict other than `INCONCLUSIVE`. No combination of failures on either side ever
   resolves toward `AGREE`.
2. **Target `CAVEATED` &rarr; `INCONCLUSIVE`, `reason: CAVEAT_GAP`.** A source has no state to
   agree or disagree with here -- see "What counts as a disagreement," below -- so this is
   never `AGREE` and never `DISAGREE`, regardless of what the source answered.
3. **Both `ALLOWED` or both `DENIED` &rarr; `AGREE`.**
4. **One `ALLOWED`, one `DENIED` &rarr; candidate `DISAGREE`, subject to reconciliation.**
   Before finalizing, re-ask SpiceDB for the same `Question` at `at_least_as_fresh` against
   the target's own `zedtoken` from the original record (or, if the harness has visibility
   into a captured post-write token for the object in question, against that). **Populate the
   record's `reconciliation` block (`attempted: true`, `attempts`, `outcome`, `zedtoken`) from
   this re-ask regardless of which of the four outcomes below it lands in** -- the record shape's
   own "The record shape" section states why: the re-ask's own answer and revision are evidence
   this rule's branches below discard if there is nowhere to keep them. Four outcomes, not two:
   - If the re-ask's answer now agrees with the source, reclassify the record as
     `INCONCLUSIVE`, `reason: STALE_READ`, and keep the original record rather than
     discarding it -- it is confirmatory evidence about the write path's timing, not the
     authorization model.
   - If the re-ask still disagrees, finalize as `DISAGREE`, `disposition: UNTRIAGED`.
   - **If the re-ask itself errors or times out, apply rule 1's own discipline to it rather
     than folding it into either branch above:** finalize as `INCONCLUSIVE`, `reason:
     RECONCILIATION_FAILED`. A failed re-ask is not evidence the disagreement is real --
     "still disagrees" means the re-ask *answered* and the answer still didn't match, and a
     re-ask that never produced an answer at all cannot honestly report that. Folding a
     reconciliation failure into "still disagrees" would manufacture exactly the kind of
     noise this whole file exists to prevent, one level deeper than rule 1 already guards
     against. If reconciliation cannot complete after a small, bounded number of attempts,
     the record stays `INCONCLUSIVE`/`RECONCILIATION_FAILED` rather than blocking on an
     unbounded retry loop -- disposition never starts anywhere but `UNTRIAGED`, so nothing is
     silently certified either way while this is unresolved.
   - **If the re-ask itself returns `CAVEATED`, apply rule 2 to it, not this rule.** Finalize
     as `INCONCLUSIVE`, `reason: CAVEAT_GAP` -- the same reason a `CAVEATED` target gets on a
     first ask, never `STALE_READ` and never `DISAGREE`. This is not hypothetical: **verified**
     against a live v1.56.0 instance, a relationship written with a caveat and re-checked at
     `at_least_as_fresh` against the exact `zedtoken` that write produced, with no context
     supplied, returns `CONDITIONAL_PERMISSION` (`caveated`) -- consistency choice governs
     which revision is read, not what shape of answer can come back, so a reconciliation
     re-ask is exactly as capable of landing on a caveat gap as any other check. A `CAVEATED`
     target answered against a boolean source is a known, expected divergence class by
     construction (`CAVEATED`'s own definition, above) -- reaching it through reconciliation
     rather than on the first ask doesn't make it a migration bug that "still disagrees"
     would misfile it as; it means the write path or the elapsed time between the original ask
     and the re-ask changed which caveat context the answer actually depends on, and the
     record should say that plainly rather than manufacture a `DISAGREE` out of it.

   **If there is no `zedtoken` to re-ask against, reconciliation cannot run -- say so, do not
   substitute a weaker consistency.** A record whose `target.zedtoken` is null (the usual cause
   is a client surface that never exposed `CheckedAt` in the first place) finalizes
   `INCONCLUSIVE`, `reason: RECONCILIATION_FAILED`, the same as a re-ask that errored. What it
   must never do is silently degrade to a `minimize_latency` or `fully_consistent` re-ask and
   report the result as if a revision had been pinned -- the first proves nothing about
   staleness, and the second answers a different question at a cost this rule already declines
   to pay. This failure is *uniform*, not occasional: if one record lacks a token because the
   client cannot produce one, every record does, and 100% of candidate disagreements will
   finalize `RECONCILIATION_FAILED` while the `AGREE` rate among the remaining comparable
   records stays perfect. "The health gate," below, exists to catch exactly that shape.

   **Reconcile only candidate disagreements, not every record.** Re-asking at a stronger
   consistency for every dual-run call would reintroduce the same cache-bypass cost
   `consistency-deep-dive.md` warns against, at the scale of all sampled traffic instead of
   the much smaller set that actually disagreed.

   **Verified** against a live v1.56.0 instance (`spicedb serve-testing`, single node,
   persistent gRPC connection): a `minimize_latency` check fired immediately after the
   `WriteRelationships` call that made it true returned the pre-write (stale) answer in 143 of
   150 trials (95.3%) at a median write-to-check gap around 360 microseconds. Delaying the
   same check collapses the staleness quickly, not gradually: 1ms &rarr; 69.3% stale
   (104/150), 2ms &rarr; 46.0% (69/150), 3ms &rarr; 23.3% (35/150), 5ms &rarr; 0.0% (0/150),
   and 0.0% at every gap tested out to 100ms (300 trials, no tail). A `fully_consistent`
   control run at the same sub-millisecond gap returned 0/200 stale, confirming the effect is
   specific to `minimize_latency` and not an artifact of the measurement itself.

   **`serve-testing`'s own staleness window is a hardcoded constant, not the production
   default -- verified directly against the v1.56.0 source, not assumed.**
   `internal/middleware/pertoken/pertoken.go:23-24` declares `revisionQuantization = 10 *
   time.Millisecond` (alongside `gcWindow = 1 * time.Hour`), and line 59 constructs every
   per-token in-memory datastore with `memdb.NewMemdbDatastore(0, revisionQuantization,
   gcWindow)` -- the 5ms collapse point measured above is consistent with that 10ms constant
   governing the window, not a coincidental match to an unrelated number. A real `spicedb
   serve` deployment does not share this value at all: its own
   `--datastore-revision-quantization-interval` defaults to **5 seconds** (`spicedb serve
   --help`), 500&times; larger than the test server's hardcoded window -- so a production
   deployment's own stale-read window could plausibly extend into the seconds, not collapse by
   5ms the way this single-node test server's does. Neither number needs to be known precisely
   for rule 4's reconciliation step to work, and that is deliberate: reconciliation re-asks
   `at_least_as_fresh` against the `zedtoken` the original answer actually carried, never after
   a fixed sleep tuned to one server's quantization constant -- it is correct regardless of
   which window the deployment in front of it actually uses. A harness that reports every
   stale-window disagreement as a genuine authorization defect will drown real ones in noise
   from the first hour it runs, on any deployment, whichever window applies there.

`disposition` only ever starts at `UNTRIAGED`. A human (or a documented, versioned rule
applied by a human's own prior decision -- never an automatic default) moves it to
`CONFIRMED_DEFECT` (the conversion is wrong; fix it) or `ACCEPTED_DIVERGENCE` (the answer
changed on purpose, and the new one is correct) -- see "What counts as a disagreement," next.

## What counts as a disagreement worth reporting

Not every `DISAGREE` or `INCONCLUSIVE` record is the same kind of event, and reporting them
identically buries the one kind a harness exists to surface. At least three are genuinely
different:

- **A check whose answer legitimately changed because the source system was wrong.** The
  conversion is correct and SpiceDB's new answer is the intended one; the source's old answer
  was itself the bug. This is a `DISAGREE` record, and it starts `UNTRIAGED` exactly like any
  other disagreement -- the harness has no way to know this on first sight, and must not guess
  it. Once a human confirms it, its `disposition` becomes `ACCEPTED_DIVERGENCE`, and every
  future record matching the same `(resource type, permission)` pair (or a broader rule the
  reviewer states) should be recognized against that decision rather than re-alerting on
  repeat occurrences -- but the *first* instance of any new pair must always surface as a real
  disagreement. A harness that silently assumes "the source was probably wrong" for any
  disagreement it hasn't seen before is not distinguishing this case, it is discarding the
  case it exists to catch.
- **A caveat needing request context the source never had.** `verdict: INCONCLUSIVE`,
  `reason: CAVEAT_GAP` -- rule 2 in Diff, above. This is not a disagreement about whether
  access should be granted; it is SpiceDB asking a question shaped differently than the
  source's boolean check ever could. It should stay visible in aggregate (it is a real,
  measurable coverage gap -- eventually either the calling code needs to start supplying the
  missing context, or the gap needs hand verification) but it must never appear in the same
  queue a `CONFIRMED_DEFECT` or fresh `UNTRIAGED` disagreement does.
- **Consistency-driven staleness.** `verdict: INCONCLUSIVE`, `reason: STALE_READ` -- rule 4's
  reconciliation step, above. Distinct from both of the above: nothing about the authorization
  model or the caveat's applicability is in question here, only *when* SpiceDB was asked
  relative to a write that hadn't finished propagating to the revision `minimize_latency`
  selected. Measured directly, and cited above.

A dashboard, alert, or report that groups all three into one "disagreement" count is
answering a different, less useful question than the one this file's contract is built to
answer. The count worth watching is `DISAGREE` records with `disposition: UNTRIAGED` or
`CONFIRMED_DEFECT` -- those are the ones that mean something is actually wrong.

## Replay

**Correct behavior:** re-issue a previously captured `Question` later and produce a result
comparable to the one recorded at capture time, even though the schema, the dataset, or both
may have changed in between. Replay is a distinct capability from Diff, not the same
operation re-run, because a schema or data change can make yesterday's captured `Question`
mean something different today -- and conflating "the same question, answered differently
now" with "a fresh parity comparison" would make Replay's results as unreliable as an
unversioned migration plan.

Every stored `Question` this capability consumes must carry, alongside it, the schema and
dataset revision that was live when it was captured (a `zed` schema hash, a dataset
checkpoint, whatever identifies "what this answer was actually true of"). Two distinct uses
follow from that record:

- **Regression replay.** Re-ask only SpiceDB, and diff the new answer against the *recorded*
  target outcome from capture time -- not against a fresh source call. This answers "did a
  schema or data change flip an answer that used to agree," and is the check to run after any
  schema edit, before that edit reaches production traffic again.
- **Parity replay.** Re-ask both sides fresh -- functionally a batched dual-run over a
  captured corpus of questions rather than live traffic. Useful for re-validating an entire
  captured set after a schema fix, or for re-checking a batch of previously `STALE_READ`
  records once enough time has passed that a fresh ask is no longer subject to the same
  reconciliation question. **This is the only capability that places a call to the source
  system**, through the adapter's `ask()` entry point, and it is therefore the only one that
  adds load to a system still authoritative for every real user: run it offline and
  operator-initiated, never on a request path, and always under an explicit rate limit or
  against a read replica. A captured corpus is as large as the shadow window made it -- replaying
  one as fast as the batch loop can go is a load test against production. The safety property's
  point 1 states this as the scoped exception it is.

A replay whose captured schema/dataset revision no longer matches the live one is not
producing the same comparison Diff would over fresh traffic -- it is producing evidence about
*how* the answer changed, which is exactly what regression replay is for, and exactly why it
must never be silently reported as a fresh `AGREE`/`DISAGREE` parity result.

## Snapshot-to-assertions

**Correct behavior:** take every `DifferentialRecord` that passes **both** eligibility tests --
`verdict: AGREE` AND `question.origin` in `{CHECK, BATCH_CHECK}` -- and only those, and render
them into SpiceDB validation YAML in the same construct grammar
`/spicedb-dev:migrate-tests` already emits, so that a run of shadow traffic accumulates into a
regression suite rather than a log nobody revisits. The two tests are stated together here, in
the sentence that first defines what this path consumes, because a filter written from a
`verdict`-only reading of it is exactly the defect `question.origin` was added to make
impossible.

**Eligibility is narrow on purpose, and is two tests, not one: `verdict: AGREE` AND
`question.origin` in `{CHECK, BATCH_CHECK}`.** `AGREE` means both sides independently reached
the identical yes/no answer -- that is the only state worth freezing as ground truth -- and
`origin` is what distinguishes a real check-shaped comparison from a sampled probe drawn out of
a list result, which can also be `AGREE` and is explicitly not eligible ("What is not comparable
at all," below). A record with `origin: LIST_SAMPLED` is rejected here on the field, not on a
caller's discipline; a record with no `origin` at all is rejected too, rather than assumed to be
a check. Every other verdict is excluded, for reasons that mirror the treatment a pack's own
test-mapping reference (`pack-contract.md` item 8) gives the identical question on the
conversion side:

- `DISAGREE` and `INCONCLUSIVE` records are excluded outright. Freezing either into a
  regression suite would assert the very thing not yet established -- an `UNTRIAGED`
  disagreement as ground truth locks in an unreviewed answer; a `STALE_READ` or `CAVEAT_GAP`
  record was never a same-question comparison to begin with.
- A `CAVEATED` target outcome is never eligible on its own, because no `AGREE` record can ever
  contain one -- rule 2 in Diff always routes it to `INCONCLUSIVE`. This is the same position
  a pack's own test-mapping reference takes for `assertCaveated` on the conversion side: it is
  real target vocabulary, but nothing here *produces* one automatically. A human may still
  hand-write an `assertCaveated` entry into a snapshot's output file, the same way a pack's
  test-mapping reference documents for its own supplementary checks -- this capability
  doesn't emit one, but doesn't forbid the file from carrying one either.

**Output shape, matched field-for-field.** For each eligible record: `ALLOWED` renders as an
`assertTrue` entry, `DENIED` as `assertFalse`, both in the identical
`"<resource>#<permission>@<subject>"` string form `/spicedb-dev:migrate-tests` itself renders
for a check -- never the write-target relation name a split construct uses in its own
`relationships:` block; a pack's own test-mapping reference (`pack-contract.md` item 8) is the
authority for that distinction, on the conversion side and here alike. A non-empty
`question.context` renders as the identical ` with {json}` suffix, canonicalized the same way
(`sort_keys=True`, compact separators) -- a file this capability writes and a file
`/spicedb-dev:migrate-tests` writes share one grammar, and a file from each can be
validated with the same `zed validate --fail-on-warn` command without either
needing to know the other exists. **They cannot simply be concatenated, and this file said
otherwise until it was tried** -- each is a complete YAML document with its own top-level
`schemaFile:`, `relationships:`, and `assertions:` keys, so `cat a.yaml b.yaml` produces a
document with duplicate mapping keys that `zed validate` rejects outright (`yaml: unmarshal
errors: mapping key "schemaFile" already defined at line 1`, exit 1). Shared grammar means each
file is independently valid against the same command and the same schema, which is what makes a
growing set of them usable as one regression suite; merging two into one file means merging
their `relationships:` and `assertions:` blocks under a single set of top-level keys, not
appending bytes.

**The `relationships:` block is the harder half, and is this file's own judgment call.**
`/spicedb-dev:migrate-tests`'s own output is self-contained -- every relationship an
assertion needs is embedded in the file, so `zed validate` can check it entirely offline
against an empty store. Snapshot-to-assertions should match that convention rather than
depending on a customer's live SpiceDB instance already holding the right data, reading
whatever it exports at the target's own `zedtoken` from the moment the check was answered --
not whatever is live when the snapshot step runs, which could already have moved past what
actually produced the recorded answer.

**Resource+subject relationships alone are *not* sufficient whenever the permission resolves
through an arrow (`->`), and this is the common case, not the exception.** For a permission with
no arrow anywhere in its expression, the resource's own relationships are enough. But for any
permission that resolves partly or wholly through `parent->...` (or any other arrow), the
resource's own relationships omit the fact that actually grants access. **Verified live**: schema
`file { relation parent: folder; permission can_read = parent->viewer }`,
`folder { relation owner: user; permission viewer = owner }`, `folder:f1#owner@user:alice`,
`file:f1#parent@folder:f1` -- `file:f1` checks `can_read` for `user:alice` as `true`, but
`file:f1`'s own relationships are exactly `file:f1 parent folder:f1`; the grant-bearing tuple
(`folder:f1#owner@user:alice`) is on a different resource entirely, one whose type and id appear
nowhere in the `Question` (`resource: "file:f1"`, `subject: "user:alice"`) at all -- there is no
literal field to read it from, and a read scoped to the `Question`'s own resource and subject
cannot discover it by construction, not merely by oversight.

**The general algorithm is to walk the permission's own expand tree, not the schema.** For each
eligible `ALLOWED` record, call `ExpandPermissionTree` (`zed permission expand <permission>
<resource> --consistency-at-exactly <zedtoken>`) on the `Question`'s own resource and permission,
at the target's own captured `zedtoken` -- SpiceDB resolves the whole tree, arrow hops included,
server-side in one call (`code-mapping.md`'s "`expand` tree shape": "the whole tree is resolved
server-side in one call, always"). **Verified live**, the same schema above, `zed permission
expand can_read file:f1 --json` names every object the permission actually touched, at every
level of the arrow chain:

```json
{"expandedObject": {"objectType": "file", "objectId": "f1"}, "expandedRelation": "can_read",
 "intermediate": {"children": [{"expandedObject": {"objectType": "folder", "objectId": "f1"},
   "expandedRelation": "viewer", "intermediate": {"children": [
     {"expandedObject": {"objectType": "folder", "objectId": "f1"}, "expandedRelation": "owner",
      "leaf": {"subjects": [{"object": {"objectType": "user", "objectId": "alice"}}]}}
   ]}}]}}
```

Walk the returned tree and collect objects from **two** places, not one:

1. **Every node's `(expandedObject.objectType, expandedObject.objectId)` pair** -- `file:f1` and
   `folder:f1` in the transcript above.
2. **Every leaf subject carrying an `optionalRelation`** -- that is, every *userset* subject
   (`leaf.subjects[].object` where that entry's `optionalRelation` is set). For each one, call
   `ExpandPermissionTree` again on *that* object and *that* relation and walk the returned tree
   by these same two rules, recursively, memoizing `(objectType, objectId, relation)` so a
   cyclic or diamond-shaped membership graph terminates instead of looping.

Then export every collected object's relationships at the same `zedtoken`.

**Step 2 is not optional, and skipping it is the one way this algorithm silently emits YAML that
fails `zed validate`.** `ExpandPermissionTree` does not recurse into userset subjects on its own:
a userset appears in the response only as a *leaf subject*, never as an `expandedObject` node, so
a walk collecting `expandedObject` pairs alone never learns that the userset's own object exists
and never exports the relationships that actually carry the grant. **Verified live** against a
real migrated application whose converted schema puts `group#member`/`group#admin` subject types
on its `folder`/`file` relations -- `folder:shared-docs#can_share__direct@group:engineering#admin`
with `group:engineering#admin@user:bob`, so `folder:shared-docs can_share user:bob` is `true`
through the userset:

```json
{"expandedObject": {"objectType": "folder", "objectId": "shared-docs"},
 "expandedRelation": "can_share", "intermediate": {"operation": "OPERATION_UNION", "children": [
   {"expandedObject": {"objectType": "folder", "objectId": "shared-docs"},
    "expandedRelation": "can_share__direct",
    "leaf": {"subjects": [{"object": {"objectType": "group", "objectId": "engineering"},
                           "optionalRelation": "admin"}]}},
   {"expandedObject": {"objectType": "folder", "objectId": "shared-docs"},
    "expandedRelation": "owner",
    "leaf": {"subjects": [{"object": {"objectType": "user", "objectId": "alice"}}]}}]}}
```

Every `expandedObject` in that response is `folder:shared-docs`; `group:engineering` appears
**only** under `leaf.subjects[].object`, alongside `optionalRelation: "admin"`. A rule-1-only walk
therefore exports `folder:shared-docs`'s four relationships and nothing else, and
`zed validate --fail-on-warn` rejects the file it produces (exit 1), naming the exact hop the walk
never exported:

```
error: parse error in `folder:shared-docs#can_share@user:bob`: Expected relation or permission
folder:shared-docs#can_share@user:bob to exist
  ⨉ folder:shared-docs can_share
  ├── ⨉ folder:shared-docs owner
  └── ⨉ folder:shared-docs can_share__direct
      └── ⨉ group:engineering admin        <- the hop a rule-1-only walk never exports
```

Adding rule 2 -- expanding `admin` on `group:engineering` and exporting that object's own
relationships -- makes the identical file validate: `Success! - 12 relationships loaded,
4 assertions run, 0 expected relations validated`, exit 0. **Arrow-resolved and wildcard-resolved
records both validate under a rule-1-only walk**, which is exactly why a walk missing rule 2 can
look correct on the cases most likely to be tested first: an arrow's grant-bearing object *is* an
`expandedObject` node (`folder:f1` above), and a `user:*` grant is a plain leaf subject on the
resource's own relationships. Only a userset subject escapes.

**The recursion in rule 2 is load-bearing too, not a formality**: a userset whose own relation is
itself satisfied through another userset needs the second hop exported as well. **Verified live**
on `team { relation member: user | team#member }` with `doc:d1#viewer@team:outer#member`,
`team:outer#member@team:inner#member`, `team:inner#member@user:zoe` -- expanding `member` on
`team:outer` returns `team:inner` as another `optionalRelation: "member"` leaf subject, again
never as an `expandedObject`. Exporting one hop (`doc:d1` + `team:outer`) fails
`zed validate --fail-on-warn` with exit 1; recursing to `team:inner` passes with exit 0. Exporting
just the userset object's own relationships without recursing is therefore a *floor*, correct only
where no userset's relation is satisfied by a further userset -- prefer the recursive walk, which
is correct either way.

Take the union across every branch of a union/intersection/exclusion, not only the
branch that produced the `true` leaf -- determining which branch actually carried the grant is
unnecessary complexity a superset export avoids paying for, and the extra rows cost nothing a
`zed validate` run would object to. This is a same-system operation on SpiceDB's own answer, not a
cross-system comparison -- it is unaffected by `expand`'s "not comparable at all" ruling for
Dual-run/Diff (that ruling is about diffing OpenFGA's expand tree against SpiceDB's, a different
question entirely; see `source-adapter.md`, "`expand` -- not comparable at all").

**This walk is for `ALLOWED` records only.** A `DENIED` record needs no supporting relationships
to validate as `assertFalse` -- a blank store already denies everything -- so exporting relationships
for a denial only risks accidentally including a tuple that would flip the assertion; export
nothing beyond the `Question`'s own resource/subject for a `DENIED` eligible record, if anything.

This still captures only what one `Question`'s permission touched, not a full-store export, and
remains this file's own judgment call, not a claim of covering the whole permission graph; it
should not be read as a general data migration mechanism.

**A `zedtoken`-scoped read has a shelf life, and this step must respect it.** SpiceDB garbage
collects revisions older than its GC window; a read pinned to a `zedtoken` past that window
fails outright rather than silently returning slightly-wrong data. **Verified directly**:
against the v1.56.0 source, `serve-testing`'s own in-memory per-token store hardcodes a
one-hour window (`internal/middleware/pertoken/pertoken.go:23`, `gcWindow = 1 * time.Hour`,
fed into the same `memdb.NewMemdbDatastore` call cited above); a real `spicedb serve`
deployment instead defaults `--datastore-gc-window` to **24 hours** (`spicedb serve --help`)
and this is operator-tunable, not fixed. `consistency-deep-dive.md`'s own "At Exact Snapshot"
section already documents the failure mode this produces: a read pinned to a GC'd revision can
fail with `"Snapshot Expired"` rather than return data. The consequence for this step: run the
`relationships:` export promptly after a batch of `AGREE` records is confirmed, well inside
whichever GC window the deployment actually has configured, rather than batching snapshot
exports on a cadence that could outlast it. If an export does hit an expired `zedtoken`
anyway, do not silently substitute the *current* live state for the captured one -- that
defeats the entire reason this step pins to a `zedtoken` in the first place, since "current"
may no longer be what produced the recorded answer. Instead, either drop that record from this
batch (it ages out the same way an untriaged record simply isn't ready yet) or re-export it
explicitly against the live store, with an advisory comment in the emitted file stating
plainly that its relationships reflect current state, not the state the answer was originally
checked against.

**File placement.** Write shadow-traffic snapshots to their own file
(`validation-shadow-<batch-id>.yaml` or equivalent), never overwriting the `validation.yaml`
phase 5 already produced -- a shadow-traffic regression suite accumulates over the life of a
cutover; the phase-5 conversion oracle is a one-time artifact. Sort `relationships:` lines
(matching phase 5's own rule); preserve capture order for `assertTrue`/`assertFalse`, the same
"don't invent a sort key" choice a pack's own test-mapping reference makes for its own
assertion output (`pack-contract.md` item 8).

**Validate before trusting.** Run `zed validate --fail-on-warn` against every emitted file
before treating it as part of the regression suite, exactly as phase 5 does. A file that
doesn't validate isn't a regression test yet -- it's a syntax error waiting to be discovered
the next time someone runs it.

## Sampling and volume

A customer cannot dual-run every request forever, and does not need to.

- **Coverage should track the permission surface, not raw request volume.** The goal is
  confidence across every distinct `(resource type, permission)` pair the application
  exercises, not statistical significance on whichever ones happen to be highest-traffic. A
  rarely-called permission needs a much higher *fraction* of its own traffic sampled than a
  high-volume one, or it may never accumulate enough records to say anything at all.
- **Weight sampling toward what's structurally risky**, the same principle
  `cutover-strategies.md` step 3 applies to choosing a rehearsal resource type: a permission
  resolved through an arrow (`->`) or a parent-child rewrite is more likely to diverge from a
  flat source check than a leaf permission with no rewrite logic, and any permission touching
  a Class B or Class C finding recorded in `migration-plan.md` is a known area of risk before
  a single record comes back. Sample those first and heaviest.
- **Coverage should taper on the same schedule cutover moves**, not on a separate calendar.
  Near-total coverage during step 3's single-resource-type rehearsal is cheap, because traffic
  is inherently narrow there; as each resource type clears step 4 and its flag moves toward
  step 6's cutover, its own dual-run sample rate can taper -- but never to zero while the
  source system remains the one actually deciding access.
- **Use a consistent sampling key, not a request-count cap.** Hash on resource or subject ID
  to decide inclusion, so the sampled set stays representative of the traffic mix over time
  rather than front-loading whatever happened to arrive in a given window.
- **Stop dual-running a given `(resource type, permission)` only when three conditions all
  hold for a full cycle of how the product is actually used** -- the first two are the
  identical bar `cutover-strategies.md` step 7 sets for removing the source system entirely,
  cited rather than restated: (1) every calling pattern for that surface has been sampled at
  least once, (2) the `AGREE` rate among comparable records (excluding `INCONCLUSIVE`) has
  held at its target threshold for that same cycle, with zero `UNTRIAGED` or unresolved
  `CONFIRMED_DEFECT` records outstanding, and (3) **the health gate below passes for that
  pair.** A quiet week is not the same evidence as a quiet cycle -- an infrequently-called
  permission is exactly the one a shorter window misses.

  **"Calling pattern" in condition (1) means a distinct question, and it is the health gate's
  conditions (1) and (4) that measure it** -- a `(resource, permission, subject, context)` tuple,
  canonicalized as the `Question` shape defines, counted distinctly and required to survive
  dropping the pair's busiest resource and busiest subject. Left as prose, "calling pattern" is
  not a test a harness can run and not a claim a reviewer can check, and "sampled at least once"
  is satisfied by a question that was sampled and then never successfully compared. Read
  condition (1) through the gate's operational form, never on its own. Note the ceiling on what
  even that form establishes: it counts questions production asked, not the paths through the
  schema they resolved by -- see "What this gate still does not measure," below.

## The health gate

**Conditions (1) and (2) above are both satisfiable by a harness that compared nothing, and
that is not a hypothetical.** Every route to a genuine `DISAGREE` runs through rule 4's
reconciliation. If reconciliation fails systematically rather than occasionally, every
candidate disagreement finalizes as `INCONCLUSIVE`/`RECONCILIATION_FAILED` -- which condition
(2) then *excludes from its own denominator*, yielding a 100% `AGREE` rate over whatever
handful of records did compare, with zero `UNTRIAGED` and zero `CONFIRMED_DEFECT` because no
record ever reached `DISAGREE` to be triaged. Condition (1) fares no better: a question that
produced an `INCONCLUSIVE` was still *sampled*, so "every calling pattern sampled at least
once" is satisfied by a run in which not one of those patterns was ever actually compared. Both
conditions read green and the customer cuts over on evidence that does not exist.

Two failure shapes produce exactly this, and both are ordinary rather than exotic:

- **No revision to reconcile against.** A build using the idiomatic vendored client rather than
  the raw-stub fallback never receives `CheckedAt`, so `target.zedtoken` is null on every
  record and every `at_least_as_fresh` re-ask has nothing to pin to. Reconciliation then fails
  for structural reasons, uniformly, on 100% of candidate disagreements.
- **A question that cannot be translated at all.** A source relation with no entry in
  `migration-map.json` -- a Class C relation with no conversion target, or one added to the
  source during the shadow window, which this playbook deliberately runs for a full usage
  cycle while the source system is still being developed -- has no SpiceDB permission name to
  put in a `Question`. See "Untranslatable questions," below, for what must happen instead.

**A third shape defeats condition (1) from the opposite direction: a harness that compared a
great deal, all of it the same shape.** Where a harness that compared nothing satisfies condition
(1) because an `INCONCLUSIVE` still counts as sampled, a harness sampling one high-fan-out
resource satisfies it because that one resource genuinely produces an enormous number of
comparisons. Both leave "every calling pattern for that surface has been sampled" reading true
over a surface most of whose patterns were never touched. Condition (4) below is the answer to
this one; the phrase "calling pattern" is given its operational definition in "Sampling and
volume" above, because as prose it is not a test any harness can run.

**Therefore, before "done" may be claimed for any `(resource type, permission)` pair, all four
of the following must hold, per pair, and be recorded rather than asserted:**

1. **A floor on comparable *questions*, not on comparable records.** At least *N* **distinct**
   questions for that pair carried `verdict: AGREE` or `verdict: DISAGREE`, where two records
   count as the same question when their `(resource, permission, subject, context)` tuples match
   -- the `Question` shape above already canonicalizes `context` for exactly this comparison, so
   the key is available on every record without re-deriving anything. `INCONCLUSIVE` records do
   not count toward the floor, by the same logic that excludes them from the `AGREE` rate's
   denominator. Counting records rather than questions lets one hot call site re-asking one
   question satisfy an arbitrarily high floor: 5,000 records of a single question is one
   comparison run 5,000 times, and the second through five-thousandth carry no information the
   first did not. A pair below the floor is not "passing," it is **undetermined**, and must be
   reported as its own state rather than folded into a pass.
2. **A ceiling on non-comparison.** The `INCONCLUSIVE` share of that pair's total records, and
   separately the `RECONCILIATION_FAILED` share of its candidate disagreements, must each stay
   under a stated ceiling for the cycle. Exceeding either is a **harness fault, not a migration
   verdict** -- it says the comparison could not run, which is a different finding from the two
   systems agreeing, and it must never be reported as agreement.
3. **A zero-record coverage list.** Every `(resource type, permission)` pair present in
   `migration-map.json` that accumulated **zero** records over the cycle must be listed
   explicitly, by name, alongside the pairs that passed. A pair that never appeared is invisible
   to conditions (1) and (2) entirely -- they can only speak about records that exist -- so it
   has to be surfaced by enumerating the map, not by inspecting the record stream.
4. **Breadth within the pair: the floor must survive dropping the pair's single busiest
   resource, and again its single busiest subject.** Group the pair's comparable records by
   `question.resource`, discard every record belonging to the most frequent one, and require the
   remaining distinct questions to still meet *N*. Repeat independently, grouping by
   `question.subject`. Both leave-one-out runs must clear the floor. This costs no fifth
   configuration number -- it reuses *N* -- and it is two `group by` passes over records the
   harness already holds.

   **Why a distinct-question floor is not enough on its own, worked against real data.** A
   resource carrying an unconditional wildcard grant -- `file:public-notice` with
   `can_read__direct@user:*`, true for every subject -- floods the `(file, can_read)` bucket with
   thousands of records that are *all genuinely distinct questions*, because each names a
   different subject. Condition (1) is satisfied; so is condition (2), since the same flood
   dilutes the `INCONCLUSIVE` share; condition (3) sees a pair with plenty of records. Every
   count reads green while the same pair's arrow-resolved path -- `file:budget-2026` reaching
   `can_read` through `parent->viewer` on `folder:shared-docs`, the structurally risky shape
   "Sampling and volume" says to sample *first and heaviest* -- was compared zero times.
   Dropping the busiest resource collapses that pair's surviving question count to
   approximately zero and the pair is correctly reported **undetermined**. The subject-side pass
   is the mirror image: a single service account or admin subject exercising thousands of
   resources is one principal's path, sampled repeatedly, and it fails the same way.

   **A pair whose real surface is genuinely that narrow** -- one resource, or one legitimate
   caller -- cannot clear this and should not be quietly exempted. Report it as `undetermined`
   and accept it in writing, per pair, with the reason, the same way an untranslatable
   `(source type, source relation)` entry is accepted below. An automatic pass for a pair the
   harness could not establish breadth for is the exact failure this condition exists to stop.

**`N`, the two ceilings, and condition (2)'s own target `AGREE` rate are four numbers this
contract deliberately does not pick, and equally deliberately does not let go unset.** The right
values depend on the pair's real traffic, the customer's risk tolerance, and how long a cycle is
for that product -- but "unset" is not a neutral default, it is the state in which the gate
silently passes everything. A harness build must take all four as explicit configuration with no
built-in default, and the value actually chosen for each must be recorded in `migration-plan.md`
where a later reviewer can see what bar was actually cleared. Zero is never a valid floor, and
100% is never a valid ceiling.

### What this gate still does not measure, and who owns it

**Every condition above ranges over the questions production happened to ask. None of them
ranges over the schema.** Condition (4) makes the volume harder to fake by requiring the
comparisons to be spread across more than one resource and more than one subject, which is a
real strengthening and closes the wildcard-flood shape worked through above -- but spread is
still a property of the observed traffic distribution, not a proof that each way a permission
can resolve was exercised. A pair can clear all four conditions with a broad, well-distributed
sample in which every record resolved through the same union branch, and an arrow, a wildcard,
or a caveated branch that only a rare code path reaches was never compared once. The harness
cannot see this from its own record stream: a `DifferentialRecord` says what was asked and what
each side answered, and nothing about which branch of the permission produced the answer.

**So state the boundary rather than letting a green gate imply more than it establishes.** A
passing health gate is evidence that comparisons ran, in volume, across a spread of resources
and subjects, and agreed. It is **not** evidence that the pair's permission was exercised
through every path its schema defines. What the customer still owns, per pair, and what a
harness build should say plainly in its own report rather than leaving to be inferred:

- **Enumerating the pair's resolution paths from the schema** -- each union/intersection operand,
  each arrow, each wildcard, each caveated branch -- and confirming at least one comparable
  record exercised each. This is a schema-reading exercise, not a record-counting one.
- **Deciding what to do about a path production never exercises during the shadow window.**
  Traffic that never takes a branch produces no evidence about it; a rare branch may need a
  directed, hand-written comparison rather than a longer wait.
- **The judgment that a cycle was long enough for this product**, which no threshold in this
  file encodes.

A build that wants to narrow this gap further, rather than only reporting it, has one avenue
already in the pipeline: the snapshot-to-assertions path calls `ExpandPermissionTree` on eligible
`AGREE` records, and the objects that walk returns identify whether the grant came from the
question's own resource or through a hop onto another object. Tallying that per pair turns
"which paths were actually exercised" into something partly observable. It is a live call, and
it only speaks about `ALLOWED` records, so it strengthens the report rather than replacing any
condition above -- and this file does not require it.

### Untranslatable questions

A source relation with **no entry in `migration-map.json`** cannot become a `Question` at all --
there is no SpiceDB permission name to put in it. Three things follow, and a harness that does
any of them differently is under-reporting its own blindness:

- **Never guess the name.** Not by assuming identity with the source relation, not by appending
  or stripping a split suffix. `migration-map.json` is the single source of truth for every name
  this harness resolves; a name absent from it is absent, not derivable.
- **Never emit a `DifferentialRecord` for it.** A record whose `question.permission` is invented
  is worse than no record: `NOT_ANSWERED` on the target side would make it look like ordinary
  sampling loss, and a fabricated permission name could just as easily resolve to something real
  and produce a confident, meaningless comparison.
- **Count it, per `(source type, source relation)`, and report it.** `observe()` returns an
  explicit *untranslatable* signal rather than a `(Question, Outcome)` pair, and the harness
  keeps a running tally keyed by the source name it could not translate. That tally is part of
  the health gate: a non-zero count means the harness is blind to a call surface that is live in
  production, and "done" cannot be claimed until each entry is either mapped (re-run the
  conversion phases for it) or explicitly accepted in writing, one entry per source relation,
  with the reason -- a Class C finding with no conversion target being the legitimate case.

## What the record stream holds, and how long to hold it

**The `DifferentialRecord` stream is the only artifact in this whole pipeline that durably
persists real production request data, and it does so continuously, unattended, for a period
measured in billing cycles.** Everything phases 1-5 emit is derived from a schema, a store
export, or a test fixture. This is different in kind, and the difference is easy to miss
precisely because every individual field looks innocuous:

- `question.resource` and `question.subject` are real object identifiers from live traffic --
  customer ids, document ids, account ids, and in many models a subject id *is* a user id.
- `request_id` correlates a record back to the production request that triggered it, and
  therefore to whatever else that request id appears in (logs, traces, support tooling).
- **`question.context` is the field to look at hardest.** In a deployment that uses caveats at
  all, this carries exactly the request-time attributes the caveat evaluates over -- timestamps,
  IP addresses, tenant identifiers, email addresses, group or role claims -- because that is
  what caveat context *is*. A harness storing it verbatim is storing a slice of every sampled
  request's identity payload.
- `source.detail` and `target.detail` are error strings from live calls, which routinely embed
  the identifiers of whatever was being checked (`source-adapter.md`'s own transcripts show a
  full `document:...#...@user:...` tuple inside an OpenFGA error message).

A harness build must therefore make three decisions explicitly rather than inheriting them from
whatever the logging stack happens to do:

1. **Retention.** Set a maximum age for the record stream and enforce it by deletion, not by
   disk pressure. The floor is operational: records must outlive the reconciliation cycle they
   feed and the snapshot batches drawn from them -- and note that a `zedtoken`-pinned
   `relationships:` export has a much shorter shelf life than the record itself does (the GC
   window, above), so a record older than that window can no longer be snapshotted anyway. The
   ceiling is the customer's own data-retention policy, which this contract does not override
   and does not know. If the two conflict, the policy wins and the shadow window shortens.
2. **Minimization.** Decide, per field, what is stored raw and what is reduced. `question.context`
   is the one where "store it all, forever, in a new place" is least likely to be the right
   answer: storing only the *key names* plus a stable hash of the values keeps caveat-gap
   analysis working (which context was expected, which was supplied) while storing none of the
   values themselves. Where the raw values genuinely are needed for triage, say so and scope it
   -- and never write them somewhere with weaker access controls than the source system's own
   logs. Subject ids are similarly reducible where a pseudonym would serve the analysis.
3. **Volume.** The stream's size is the sampling rate times the traffic times the window, and
   all three are chosen elsewhere in this file without anyone doing that multiplication. Do it
   before turning sampling up, not after: a rate that is trivial for one resource type over a
   week is a different proposition across every `(resource type, permission)` pair over a
   billing cycle. Volume is also a retention argument -- the cheapest record to protect and to
   delete is the one never written.

None of these three is a decision this contract can make for a customer, and none is a decision
a harness may silently default. What it *can* require is that each be recorded alongside the
harness's other configuration, so that a reviewer can see what was chosen rather than
reconstructing it from the storage backend.

## The safety property

This runs beside production, and its failure modes must all point the same direction: toward
doing nothing, never toward granting or denying something the source system didn't already
decide. `cutover-strategies.md` states the outcome this property guarantees -- "nothing in
this step ever lets SpiceDB's answer change what a user can do" -- this section states the
mechanics that make it true:

1. **Read-only to the source of truth.** The harness never calls a write API on the source
   system, in any capability, ever. **In dual-run -- the capability that runs beside live
   production traffic, and the one this property is about -- it does not call a *read* API on
   the source either**: `source.outcome` comes from observing a decision that was already going
   to be made (Dual-run, above), so dual-run's only outbound call is a read against SpiceDB and
   it adds no load of any kind to the source system.

   **Replay's parity mode is the one exception, and it is scoped rather than silent.** Parity
   replay re-asks *both* sides fresh (Replay, above), which means the adapter's `ask()` really
   does call the source -- that is the entry point's whole purpose. What keeps this compatible
   with the property above is *when* and *how much*, not *whether*: parity replay is an
   offline, operator-initiated batch over a captured corpus, never on a production request's
   path, and it must run under an explicit rate limit or against a read replica rather than
   replaying a captured corpus at whatever rate the batch loop can manage. A captured corpus is
   as large as the shadow window made it, so an unbounded parity replay is a self-inflicted
   load test against the system that is still authoritative for every real user -- the one
   system this whole capability exists to avoid disturbing. Regression replay re-asks only
   SpiceDB and calls the source not at all.
2. **Never a decision path.** SpiceDB's answer is logged and diffed. It is never returned to
   any caller and never consulted by any authorization gate, in any `DifferentialRecord`
   state, agreement or not. The source system's answer is what the request actually receives,
   full stop, for the entire duration this capability is in use.
3. **Every failure mode is a missing or `ERRORED`/`NOT_ANSWERED` record, never a `DENIED`
   one.** If the harness process crashes, times out, loses its connection to SpiceDB, or the
   comparison pipeline itself throws, the only permitted effect is that a record is missing or
   carries one of those two states. This is exactly why those states exist in the record shape
   at all: a harness with no way to say "I didn't get an answer" is a harness whose own
   failures are invisible, which is a worse property than having failures in the first place.
4. **Bounded, asynchronous latency budget.** The SpiceDB call is issued off the production
   request's critical path (Dual-run, above), so a slow or hung call extends nothing but the
   harness's own bookkeeping.
5. **No new write surface on the production path.** Persisting a `DifferentialRecord` is
   itself best-effort and asynchronous. Losing one to a storage failure is a lost data point,
   never a failed production request.

## What is not comparable at all

Some operations have no meaningful cross-system comparison, and a harness that implies full
coverage by staying silent about them is worse than one that says so plainly.

- **Enumeration-shaped operations** -- list every resource a subject can access, list every
  subject who can access a resource -- have no small, stable comparison key the way a single
  check does. Two correct answer sets can differ in cardinality, ordering, or pagination
  boundary without either system being wrong, and a naive set-equality diff over them produces
  constant false-disagreement noise that drowns out real defects. Where enumeration coverage
  matters, sample check-shaped assertions *out of* a list result (does this one of the N
  returned entries also check true on the other side) rather than diffing two full sets, treat
  that as a distinct, lower-confidence comparison mode, and never feed its records into
  snapshot-to-assertions -- **which is enforceable rather than advisory only because every such
  record carries `question.origin: LIST_SAMPLED`** (see "Question," above); without that field
  the record is byte-identical to one from a real `check` call site and the rule cannot be
  checked at the point it has to hold. This is the same position `pack-contract.md` item 8 takes on the
  conversion side, where a pack's own test-mapping reference records that list-shaped
  assertions have no validation-YAML equivalent and stay advisory rather than converted.
- **Administrative and schema-management calls** are not authorization decisions and have no
  "did access hold" shape to compare at all -- out of scope entirely, the same category
  `findings-report.md`'s Class C reserves for "call sites or endpoints with no conversion
  target at all."
- **A caveat check whose context the calling code never supplies at any layer** -- not merely
  a gap the source's check API can't express, but one absent at the call site itself -- is a
  call-site question, not a systems-comparison question. Flag it for the application owner
  rather than forcing it into a `DifferentialRecord` the diff logic was never meant to answer.
- **Any comparison across two revisions that were never contemporaneous.** Replaying a
  captured `Question` against a schema that has since changed what the permission in it means
  is not the same comparison it was at capture time -- Replay, above, treats this as its own
  case (a regression check against the recorded answer) precisely so it is never mistaken for
  a fresh parity claim.
