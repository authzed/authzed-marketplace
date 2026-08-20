# Data Mapping: OpenFGA → SpiceDB

Extract/transform/load rules for phase 3 (live OpenFGA store → live SpiceDB instance), plus
the ongoing synchronization work a conversion can create and the identifier codec that must
stay in lockstep with phase 4.

## Scope of this file

**This file is the algorithm**, in the same sense `test-mapping.md` and
`naming-normalization.md` are: apply the rules below directly against a live OpenFGA store
and a live SpiceDB instance -- there is no tool to call, and this pack ships no code
(`SKILL.md`, "The parity harness is not part of this plugin"). A reference implementation of
the transform half, `transform_tuple`/`tuple_relationship`, exists in the plugin's source
repository at `tools/migration-harness/src/migration_harness/tuple_transform.py` and
`validation_gen.py`. **It is not shipped and must not be imported or run** -- reproducing it
means cloning `authzed/authzed-marketplace`, which a customer repository does not have. It is
cited here because it is covered by 240 passing tests (246 collected, 6 skipped) spanning all
39 corpus stores, plus the live-server behavioral checks reproduced throughout this file, so
**the two must not diverge**: every rule below was checked against what that code actually
does and against a live v1.56.0 server, not against an aspirational reading of the design
spec. `/spicedb-dev:migrate-data` (phase 3, shipped) generates an extract/transform/load
script implementing this algorithm using the `fga` and `zed` CLIs and/or a client SDK --
never a dependency on this repository's internal tooling.

This file assumes phase 1 (schema conversion) and its `migration-map.json` already exist.
Data migration is a pure **consumer** of that artifact -- `IdMap.write_relation` for the
relationship's resource side, `IdMap.apply` for everything else -- and invents no new
normalization rule of its own. See `schema-mapping.md` for the relation/permission split and
`naming-normalization.md` for the identifier algorithm; this file only states how those two
already-decided artifacts get applied when moving a live store's relationship data.

Everything here was re-verified against **SpiceDB v1.56.0**, **zed v0.31.1**, and **`fga`
v0.7.20** (floor: SpiceDB v1.52.0), using an isolated `spicedb serve-testing` instance
(`--endpoint`/`--token` passed explicitly to `zed`, never `zed context use`, which rewrites
shared global config -- and note `serve-testing` takes no `--grpc-preshared-key` in v1.56.0,
a broken invocation form that shipped in 3 files at this repository's initial commit
(`for c in $(git log --format=%h --reverse); do echo "$c $(git grep -l -- 'grpc-preshared-key' $c 2>/dev/null | wc -l)"; done`
→ 3 at `cb61395` -- `spicedb-dev/README.md`, `spicedb-dev/commands/test-permissions.md`,
`spicedb-dev/skills/authorization-testing/references/test-patterns.md` -- dropping to 0 by
`c33a980`) and a real `openfga/openfga:latest` server for the OpenFGA-side commands. Every
count is derived mechanically, with the command stated.

## Extraction

Three ways to pull relationship data out of a live OpenFGA store; the pack recommends one of
them and actively warns off another.

| Method | What it gets you | Verdict |
|---|---|---|
| `fga tuple read --output-format=simple-json --max-pages 0` | Every tuple, paginated exhaustively | **Recommended** |
| `fga store export` | Model **and** tuples in one file | Fine for a small/POC store only -- silently truncates otherwise |
| Raw `Read` RPC with continuation tokens | What the CLI above does internally | **Required** for any extraction that must resume across separate invocations -- the `fga` CLI cannot do this at all (see "Resumability" below), regardless of whether it is installed; also the fallback in an SDK-only environment |

**`fga tuple read --max-pages 0` is the recommended extraction path**, verified to paginate
correctly past the CLI's own single-page default. `--page-size` caps up to 100 per page (a
SpiceDB-side-style RPC limit on the OpenFGA API itself) and `--max-pages` caps how many pages
the CLI will follow before stopping -- **default 20**, so a store with more than
page-size × 20 tuples is silently under-read unless `--max-pages 0` (follow every
continuation token until the server reports none left) is passed explicitly. Verified live
against a store seeded with 259 tuples (the `github` sample store's 9 plus 250 synthetic
`reader` grants):

```
$ fga tuple read --store-id $STORE_ID --output-format simple-json --page-size 50 --max-pages 2 \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
100
$ fga tuple read --store-id $STORE_ID --output-format simple-json --page-size 50 --max-pages 0 \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
259
```

`--output-format simple-json` is the right output mode for feeding a transform script: a bare
JSON array of `{"object", "relation", "user"}` objects, with no continuation-token
bookkeeping or per-tuple timestamp to strip back out.

**A caveated tuple carries one `condition` object, nested -- not sibling `condition_name` /
`condition_context` keys.** Getting this wrong is a silent fail-open, not a parse error: a
transform looking for keys that do not exist finds none, concludes the tuple is uncaveated,
and emits a conditional grant as an **unconditional** relationship. No verification level
catches it -- levels 1 and 2 are counts, and level 3's sample checks expect `true`, which is
exactly what an over-broad grant returns. Verified live against `openfga/openfga:latest` with
`fga` v0.7.20:

```
$ fga tuple write --store-id $STORE_ID user:anne viewer document:d1 \
    --condition-name in_window --condition-context '{"grant_time":"now"}'
$ fga tuple read --store-id $STORE_ID --output-format simple-json --max-pages 0
[
  {
    "condition": {
      "context": { "grant_time":"now" },
      "name":"in_window"
    },
    "object":"document:d1",
    "relation":"viewer",
    "user":"user:anne"
  }
]
```

Read the name from `tuple["condition"]["name"]` and the context from
`tuple["condition"].get("context")`, and treat the whole `condition` key's absence -- not a
missing `condition_name` -- as "uncaveated". The default `json` format nests these same
records one level deeper, under a `tuples[].key` object (the identical `condition` object
included) alongside a write timestamp and a `continuation_token` field the CLI has already
consumed on your behalf when `--max-pages 0` is set -- there is nothing left for a caller to
do with it.

**`fga store export` is not the recommended path, and its default is an active trap.**
`--max-tuples` defaults to **100** and the command gives **no warning** when a store has
more. Verified live, on the same 259-tuple store:

```
$ fga store export --store-id $STORE_ID --output-file export.yaml
model written to export.yaml
{}
$ python3 -c "import yaml; print(len(yaml.safe_load(open('export.yaml'))['tuples']))"
100
```

No error, no "truncated" notice -- `model written to export.yaml` and an empty `{}` line is
the entire output, and the file silently holds 100 of the store's 259 real tuples. There is
also no "unlimited" sentinel: `--max-tuples 0` does not mean "no limit," it means **zero**
tuples exported (verified: an export with `--max-tuples 0` against the same store produces a
`tuples:` list of length 0). Since a migrating agent has no cheap way to know a store's true
tuple count in advance of extracting it, there is no safe fixed `--max-tuples` value to pass,
which is why this method is not the recommendation for anything but a store already known to
be small (a POC, or one confirmed empty/tiny in a prior extraction pass). Where it *is*
useful: it bundles the model text alongside the tuples in one file, convenient for a quick
manual inspection -- just never trust its tuple count without independently passing
`--max-tuples` comfortably above a count you already obtained another way.

**Get the model separately, with `fga model get --format fga`.** Phase 1 already needed this
(`pack-contract.md` item 2), so it costs nothing extra here:

```
$ fga model get --store-id $STORE_ID --format fga | head -3
model
  schema 1.1

```

## The transform needs the model, not just the tuple stream

**State this plainly before writing a single transform rule, because it is the mistake a
tuple-shaped input invites:** an OpenFGA tuple, `{user, relation, object, condition?}`, is
only **implicitly typed**. Quoting `tuple_transform.py`'s own module docstring directly,
because it states the reason precisely: "nothing in the tuple itself says whether `relation`
split into a `relation`/`permission` pair during schema conversion, or whether the resource
type encodes its object ids." Both answers live in `migration-map.json` -- the model's
already-computed output from phase 1 -- never in the tuple.

Concretely, given one raw tuple from the `github` sample store,

```json
{"user": "user:erik", "relation": "member", "object": "organization:openfga"}
```

nothing in this JSON object says that `organization.member` is one of this pack's corpus-wide
88 split relations (`schema-mapping.md`, "The relation/permission split") and therefore must
be written to `member__direct`, not `member`. Two tuples that are
byte-identical in shape can require opposite treatment depending only on what the *model*
did with that relation name on that type -- `repo.owner` in the same store never splits
(pure type list, no operator) and writes under its own bare name. A transform that pattern-
matches on the tuple alone, with no `migration-map.json` in hand, cannot tell these two cases
apart, and gets every split relation wrong in one direction or the other.

**How much of a model that is, derived across all 39 maps:** 88 of 916 mapped relations
split, **9.6% corpus-wide**, with a **median per-store rate of 3.7%**. It is a minority of
relations, not half of them -- but it is not evenly spread, and the tail is what matters:
`custom-roles` splits **77.3%** of its relations and `github` **50.0%** (this file's own
worked example, which is why "roughly half" reads plausibly from here and is wrong
everywhere else), then `slack` 42.9%, `superadmin` 35.7%, `file-storage` 31.0%.

```
$ python3 -c "
import json, pathlib, statistics
rows = []
for d in sorted(pathlib.Path('corpus-runs').iterdir()):
    f = d / 'migration-map.json'
    if not f.is_file(): continue
    m = json.loads(f.read_text())
    nsplit = sum(len(v) for v in (m.get('relation_splits') or {}).values())
    nrel = sum(len(v) for v in (m.get('permissions') or {}).values())
    rows.append((d.name, nsplit, nrel))
tot_s, tot_r = sum(r[1] for r in rows), sum(r[2] for r in rows)
print('stores', len(rows))
print('corpus-wide', tot_s, '/', tot_r, '=', round(100*tot_s/tot_r, 1))
print('median per-store pct:', round(statistics.median(100*s/r for _, s, r in rows if r), 1))
print('top:', sorted(((round(100*s/r,1), n) for n, s, r in rows if r), reverse=True)[:5])
"
stores 39
corpus-wide 88 / 916 = 9.6
median per-store pct: 3.7
top: [(77.3, 'custom-roles'), (50.0, 'github'), (42.9, 'slack'), (35.7, 'superadmin'), (31.0, 'file-storage')]
```

The consequence does not scale down with the percentage: a store where 3.7% of relations
split still has every one of those relations' tuples written to the wrong name, and
`write_relation` must still be called on **every** tuple to get the un-split 96.3% right by
the same code path (see "Tuples are writes" below).

## Transform

### Tuples are writes: the resource-side relation is `write_relation`, everything else is `apply`

**The single highest-consequence rule in this file**, restated from the write side of
`test-mapping.md`'s own "Tuples are writes; assertions are checks" (phase 3 and phase 5 share
one transform -- see "Same transform as phase 5" below): a split relation has two SpiceDB
names, and a relationship **write** must always target the relation (`X__direct`), never the
permission (`X`). `zed`'s own error names the exact reason:

```
$ zed relationship create document:d1 viewer user:alice --endpoint localhost:50799 --token t --insecure
error: rpc error: code = InvalidArgument desc = cannot write a relationship to permission `viewer` under definition `document`
$ zed relationship create document:d1 viewer__direct user:alice --endpoint localhost:50799 --token t --insecure
Gh8KEzE3ODY2OTMzNTM5MzQ2MzgwMDASCDNiMGRlZmQ2
$ zed permission check document:d1 viewer user:alice --endpoint localhost:50799 --token t --insecure
true
```

Reading `tuple_relationship`'s body (`validation_gen.py`) shows exactly how narrow the
override is -- it is **not** two separately-derived mappings, it is one ordinary `apply` call
with one field substituted afterward:

```python
raw = Assertion(s_type, s_id, s_rel, relation, r_type, r_id, True, "")
mapped = dataclasses.replace(
    idmap.apply(raw), permission=idmap.write_relation(r_type, relation)
)
```

`idmap.apply(raw)` maps **everything** the ordinary check surface would -- both types
(`types`), both object ids (`encode_id`, per `encoded_types`), and the subject-side
`#relation` suffix when the subject is a userset -- through its normal, split-oblivious
logic. Only the one field standing in for "the relation being written" is then overridden
with `idmap.write_relation(r_type, relation)`, which is the accessor that actually consults
`relation_splits` and falls back to the ordinary `permissions[type][relation]` mapping when
the relation never split. This is why "un-split relation writes under the same name it is
checked under" is true by construction, not by a second code path that happens to agree: an
un-split relation's `write_relation` call and its `apply` call read the exact same
`permissions[type]` entry.

**A userset subject reference is the one place inside a write that names the *permission*,
not the relation** -- confirmed on the same store `test-mapping.md` uses, where
`organization.member` splits and both positions occur in one file:

```
organization:openfga#member__direct@user:erik              # resource side: write target
organization:openfga#repo_admin@organization:openfga#member  # subject side: stays unsuffixed
```

The subject-side `#member` never goes through `write_relation` at all -- it comes out of the
ordinary `idmap.apply(raw).subject_relation` field, same as a check assertion's subject
relation would. `write_relation` is called exactly once per tuple, on the resource side only.

**Mechanical count, this plan's own corpus.** 28 of the 39 corpus stores' committed
`migration-map.json` carry at least one `relation_splits` entry, 88 split relations in total:

```
$ python3 -c "
import json, pathlib
root = pathlib.Path('corpus-runs')
stores, total = 0, 0
for d in sorted(root.iterdir()):
    f = d / 'migration-map.json'
    if not f.is_file(): continue
    n = sum(len(v) for v in (json.loads(f.read_text()).get('relation_splits') or {}).values())
    if n: stores += 1; total += n
print(stores, total)
"
28 88
```

A store with zero entries in `relation_splits` still requires `write_relation` to be called
on every tuple -- the accessor's fallback path *is* the correctness guarantee for the
un-split majority of relations, not an optimization to skip when a store "doesn't need it."

### Name normalization

Types and un-split relation/permission names go through `migration-map.json`'s `types` and
`permissions` tables exactly as a check assertion would (`IdMap.apply`) -- see
`naming-normalization.md` for the reduction algorithm itself (lowercase, separators to
underscore, illegal characters stripped, length-padded/truncated with a disambiguating hash
suffix on collision). Nothing about the data phase adds a new normalization rule; it is a
pure consumer of the map phase 1 already produced.

### Object-ID encoding

Object ids must satisfy `^[a-zA-Z0-9/_|\-=+]{1,1024}$` -- **but do not compile that literal
in Go**, whose RE2 engine rejects repeat counts above 1000 and panics at init on it, with
`go build` and `go vet` both passing. Use an unbounded class match plus an explicit length
check (`naming-normalization.md`'s "Object IDs" row carries the Go form). That combined form is this pack's own
formulation (`_OBJECT_ID_RE`, `validation_gen.py`, and `naming-normalization.md`), not a
literal quote off the wire -- reading `ObjectReference.object_id`'s actual constraint directly
(`grpcurl ... describe authzed.api.v1.ObjectReference`) shows the server splits it into two
separate declarations, `pattern: "^(([a-zA-Z0-9/_|\-=+]{1,})|\*)$"` and a standalone
`max_bytes: 1024`, with the wildcard as an inline alternation inside the pattern rather than a
carve-out layered on top of it. The two formulations are equivalent for every id this file
ever constructs -- `_OBJECT_ID_RE` special-cases `*` before applying its own regex
(`_check_object_id`: "except the `*` wildcard, which is a distinct grammar token"), landing on
the same accept/reject boundary the combined wire pattern's alternation does directly -- but an
agent should read the pack's regex as the pack's own restatement, verified equivalent to the
wire constraint, not as a transcription of it. Either way, one carve-out matters for encoding
purposes regardless of which formulation you read it from: the wildcard subject id `*` is a
distinct grammar token and is **never encoded**, whatever the `id_encoding.mode`. `encode_id`
(`idmap.py`) implements two modes:

- `"none"` -- pass through unchanged.
- `"base64url"` -- `base64.urlsafe_b64encode`. Output uses only `A-Za-z0-9-_=`, all legal in
  the object-id charset, so no post-mangling is ever needed and the encoding stays
  reversible. Verified live -- the encoded form writes and reads back; the raw form (an id
  containing `@`) is rejected outright:

  ```
  $ python3 -c "import base64; print(base64.urlsafe_b64encode(b'alice@corp.com').decode())"
  YWxpY2VAY29ycC5jb20=
  $ zed relationship create document:d1 viewer__direct 'user:YWxpY2VAY29ycC5jb20=' --endpoint localhost:50799 --token t --insecure
  Gh8KEzE3ODY2OTM3MDg2MjE4NDYwMDASCDNiMGRlZmQ2
  $ zed relationship create document:d1 viewer__direct 'user:alice@corp.com' --endpoint localhost:50799 --token t --insecure
  error: invalid relationship string
  ```

Encoding is per source **type** (`id_encoding.types`), not global -- a type absent from that
list passes its ids through unchanged even under `"base64url"` mode. An empty id, or an
encoded id that would exceed 1024 characters, is a hard error, never a silent truncation
(`encode_id`'s own docstring: "Never truncates -- that would silently break reversibility").
`naming-normalization.md` already notes OpenFGA caps `object` at 256 bytes, so 4/3 inflation
tops out near 344, well inside the 1024 ceiling -- length is not a practical concern for this
mode, only correctness of which types are listed.

### Condition → caveat context

A tuple-level `condition: {name, context?}` block becomes a caveat suffix on the
relationship-write line: `[name:{json}]`, or bare `[name]` when the condition carries no
context. Two independent rules govern it, and getting either wrong fails at a different
point in the pipeline:

**The JSON must be canonical** (`json.dumps(context, sort_keys=True, separators=(",", ":"))`)
-- not for a round-trip guarantee on the write path itself (SpiceDB stores caveat context as
a structured `google.protobuf.Struct`, not as re-parsed text, so key order in what you send
over the wire does not matter to the *server*), but because this is the exact same
serialization phase 5's assertion-side ` with {json}` suffix uses, and the two must agree
byte-for-byte wherever a written relationship's caveat is later checked against a converted
`validation.yaml` assertion carrying context. Verified round-trip, write-time-bound context
resolving with no context needed at check time:

```
$ zed relationship create document:condtest viewer user:pat --caveat 'c:{"a":5}' --endpoint localhost:50799 --token t3 --insecure
$ zed permission check document:condtest view user:pat --endpoint localhost:50799 --token t3 --insecure
true
$ zed relationship create document:condtest2 viewer user:pat --caveat 'c:{"a":-1}' --endpoint localhost:50799 --token t3 --insecure
$ zed permission check document:condtest2 view user:pat --endpoint localhost:50799 --token t3 --insecure
false
```

**The caveat name passes through unnormalized but must satisfy the strict identifier grammar
`^[a-z][a-z0-9_]{1,62}[a-z0-9]$`, and this is a real trap, not a formality.** `idmap.py` has
no caveat namespace, so nothing in this transform renames a condition name -- but SpiceDB's
schema compiler and its relationship-string parser enforce two **different** grammars for the
same name, and a name that clears the first silently fails the second. Verified live, using
the exact single-character name `naming-normalization.md` already established as the loosest
legal caveat declaration:

```
$ cat schema-caveat2.zed
caveat c(a int) { a > 0 }
definition document {
	relation viewer: user with c
	permission view = viewer
}
$ zed schema write schema-caveat2.zed --endpoint localhost:50799 --token t3 --insecure
     # (no error -- the declaration deploys)
$ zed schema read --endpoint localhost:50799 --token t3 --insecure
caveat c(a int) { a > 0 }
...
$ zed validate --fail-on-warn validation-caveat.yaml   # relationships: document:d1#viewer@user:alice[c:{"a":1}]
error: error parsing relationship `document:d1#viewer@user:alice[c:{"a":1}]`: invalid relationship string
```

The schema deploys cleanly -- `zed validate`/`WriteSchema` only requires a caveat name to lex
as an identifier (verified elsewhere in this pack: 1-character names, leading digits, leading
underscores, and uppercase all deploy). The **relationship-string grammar** (what both `zed`
and this transform's output use to express a caveated write) enforces the strict regex, so a
schema built with a loose caveat name silently produces a construct that can never actually
be *written to* through the string form -- the failure surfaces here, at data-migration time,
not at schema-deploy time. **Raise rather than silently normalize** (`_check_caveat_name`'s
own docstring: normalizing here "would only make this module's output disagree with whatever
name the schema-generation step gave the caveat" -- the schema still declares the raw name,
so silently renaming it in the data phase would produce a write that references a caveat the
schema never declared). If phase 1 already renamed this condition, apply that exact same
rename here -- read it from `migration-map.json`'s `caveat_renames`, **not** from
`migration-plan.md`, which `findings-report.md` forbids any phase to read as state; otherwise halt and report the
mismatch rather than guessing a fix.

**Corpus exposure**: `test-mapping.md`'s own mechanical count -- Python over the root and
nested `tuples:` conditions of each store's **selected** test file (that file's "Selecting the
source test file" rule, *not* a hard-coded `store.fga.yaml`, which skips `modeling-guide`
entirely and undercounts by one) -- establishes tuple-level `condition:` blocks appear in
**8 of 39** stores: `advanced-entitlements` (6), `banking` (2), `condition-data-types` (18),
`groups-resource-attributes` (2), `ip-based-access` (1), `modeling-guide` (1), `superadmin`
(1), `temporal-access` (2). Every one of these 8 stores' writes needs this section's two
rules applied; every other store's tuples never carry a `condition:` block at all and this
section is a no-op for them.

### Same transform as phase 5, same implementation, not just the same semantics

`transform_tuple` (phase 3) delegates to the exact same `tuple_relationship` function
`generate_validation` (phase 5) calls per tuple -- not two implementations kept in agreement
by convention, one function two callers. This matters operationally, not just as an
implementation note: a relationship phase 3 writes to a live server is later checked against
an assertion phase 5 converts from the same source tuple, so any divergence between "what got
written" and "what the test file expects to find" would silently break parity. A migrating
agent implementing this transform from scratch (since the reference code isn't shipped) must
implement the resource-side/subject-side split exactly as stated above in both the
data-loading script and the test-generation step, or the two will disagree on some subset of
split relations with no error anywhere short of a live parity check.

## Load

### Two write paths

| Path | Call | Batch limit | Semantics |
|---|---|---|---|
| Bulk | `ImportBulkRelationships` (`PermissionsService`, not the deprecated `ExperimentalService.BulkImportRelationships`) | none published; whole stream is one transaction | Fails wholesale if **any** relationship already exists |
| Incremental | `WriteRelationships` | **1000** updates/call | Use `TOUCH`, not `CREATE` -- idempotent |

Confirmed the correct, current RPC name directly from the server's own reflection, since a
name collision exists: `grpcurl -plaintext localhost:PORT list` on a v1.56.0 instance shows
`ImportBulkRelationships` living on `authzed.api.v1.PermissionsService`, while
`authzed.api.v1.ExperimentalService.BulkImportRelationships` (transposed name) is a
`deprecated = true` RPC left over from an earlier API generation. Use the `PermissionsService`
one.

**`ImportBulkRelationships` runs the entire stream as one transaction.** Verified live: a
two-chunk stream where the first chunk writes two brand-new relationships and the second
chunk collides with a relationship already present fails the whole call, and **the first
chunk's successful writes are rolled back with it** --

```
$ grpcurl ... ImportBulkRelationships < bulk-stream.json   # chunk1: 2 new, chunk2: 1 dup + 1 new
ERROR: Code: AlreadyExists  Message: could not CREATE relationship `document:d1#viewer__direct@user:bob`, as it already existed...
$ zed relationship read document:bulk1 ...   # from the FIRST, non-colliding chunk
                                              # (empty -- nothing printed)
$ zed relationship read document:bulk2 ...
                                              # (empty)
```

Nothing from either chunk survives. This is why a partial prior run must be **detected**, not
merely retried -- see "Resumability" below.

**`WriteRelationships` rejects a call over 1000 updates, and the error names the reason to
switch tools.** Verified live, exact error text:

```
$ # 1001 TOUCH updates in one WriteRelationships call
ERROR: Code: InvalidArgument
Message: too many updates (1001) for WriteRelationships call (maximum: 1000); consider using ImportBulkRelationships API instead
```

Exactly 1000 in one call succeeds (verified: the identical call trimmed to 1000 updates
returns a `writtenAt` token with no error).

**Use `TOUCH`, never `CREATE`, for `WriteRelationships`.** `zed relationship create` maps to
`OPERATION_CREATE` and fails on a relationship that already exists; `zed relationship touch`
maps to `OPERATION_TOUCH` and is idempotent. Verified live, same relationship, both
operations:

```
$ zed relationship create document:d1 viewer__direct user:bob ...     # first time
Gh8K...
$ zed relationship create document:d1 viewer__direct user:bob ...     # second time, CREATE
error: rpc error: code = AlreadyExists desc = could not CREATE relationship `document:d1#viewer__direct@user:bob`, as it already existed. If this is persistent, please switch to TOUCH operations or specify a precondition
$ zed relationship touch document:d1 viewer__direct user:bob ...      # TOUCH instead
Gh8K...
```

A failed or partially-completed run replayed entirely through `TOUCH` batches converges on
the same end state regardless of exactly where it stopped -- this is the property the
resumability recipe below depends on.

**The `zed relationship` CLI cannot bulk-load caveated data, and this is a real limitation,
not a style preference.** Its stdin batch mode (`-b/--batch-size`, default 100) takes one
space-separated `resource relation subject` triple per line -- **not** the compact
`resource#relation@subject[caveat]` relationship-string form this transform produces -- and
`--caveat` is a single flag applied identically to **every line in the batch**, with no
per-line caveat syntax at all. Verified live: piping two relationships through one call with
one shared `--caveat` flag stamps the identical caveat binding onto both:

```
$ printf 'document:sdcav2 viewer user:stan\ndocument:sdcav3 viewer user:stan\n' \
  | zed relationship touch --caveat 'c:{"a":1}' ...
$ zed relationship read document:sdcav2 ...
document:sdcav2 viewer user:stan[c:{"a":1}]
$ zed relationship read document:sdcav3 ...
document:sdcav3 viewer user:stan[c:{"a":1}]
```

Any store whose tuples carry more than one distinct caveat binding (any of the 8 stores named
above) cannot be bulk-loaded through this CLI path at all -- there is no way to express "line
1 gets context A, line 2 gets context B" in one batched invocation. This is exactly why
`pack-contract.md` item 6 specifies "a **generated** (not hand-written) extract/transform/load
script": the script must call `WriteRelationships`/`ImportBulkRelationships` directly (via a
client SDK, in whatever language the target application uses), where each
`RelationshipUpdate.relationship.optional_caveat` is set independently per relationship. The
`zed relationship` CLI remains useful for spot writes, dry-run smoke tests, and any store with
zero caveated tuples, but it is not the mechanism for a real bulk load once caveats are in
play.

### Sort order

Sort the relationship-write set by `(resource type, resource id, relation, subject type,
subject id, subject relation)` before loading. This has no correctness effect on SpiceDB
itself -- `WriteRelationships`/`ImportBulkRelationships` do not care about call order within
one call -- but it makes a generated load script's output deterministic and diffable across
two runs against the same source data, and it matches the practical effect of what
`generate_validation` already does for the sibling test-conversion output
(`relationships = sorted(tuple_lines)`, `validation_gen.py`): sorting the fully-rendered
relationship string produces the same field-major ordering for ordinary alphanumeric ids,
since the string's own field separators (`:`, `#`, `@`) are constant across every line.

### Limits

Four hard limits, all confirmed directly from a running v1.56.0 server -- both from its own
startup configuration dump (`spicedb serve-testing`'s structured log line lists
`MaximumUpdatesPerWrite`, `MaxCaveatContextSize`, `MaxRelationshipContextSize`) and by
tripping each one live:

| Limit | Value | Verified error |
|---|---|---|
| `WriteRelationships` updates per call | 1000 | `too many updates (1001) for WriteRelationships call (maximum: 1000); consider using ImportBulkRelationships API instead` |
| Relationship caveat context (stored) | 25,000 bytes | `provided relationship ... exceeded maximum allowed caveat size of 25000` (`context_size: 26090`) |
| Request caveat context (check-time) | 4096 bytes | `request caveat context should have less than 4096 bytes but had 4230` |
| `ExportBulkRelationships` per response | 10,000 | from server config (`MaxBulkExportRelationshipsLimit`); relevant to the verification pass below, not to loading |

The first three were each tripped directly with an over-limit payload via `grpcurl` against a
live instance; the fourth is read from the server's own reported configuration
(`MaxBulkExportRelationshipsLimit":10000` in `serve-testing`'s structured startup log) and
used below for the post-load verification read-back, not for loading itself.

## Resumability, dry-run, and a verification pass

### Resumability: bulk-first, TOUCH-fallback

Because `ImportBulkRelationships` fails the **entire** stream on any single collision, and
gives no signal about how much of a prior attempt actually landed, detecting "was this
partially run before" from the error alone is not possible -- and it does not need to be.
Verified live, the complete recipe:

1. **First attempt: `ImportBulkRelationships`** over the whole transformed relationship set,
   chunked as needed. On an empty target, this succeeds in one shot:

   ```
   $ grpcurl ... ImportBulkRelationships < bulk-clean.json   # 9 relationships, fresh target
   {"numLoaded": "9"}
   ```

2. **On `AlreadyExists`, stop retrying `ImportBulkRelationships` and switch to
   `WriteRelationships` + `TOUCH`, replaying the *entire* source set, not just an estimated
   remainder.** Verified: a naive retry of the identical bulk stream against the now-partially-
   or fully-populated target fails wholesale again (same `ERROR_REASON_ATTEMPT_TO_RECREATE_
   RELATIONSHIP`), but replaying the same 9 relationships through `WriteRelationships` with
   `TOUCH` succeeds and leaves the count unchanged at 9 -- no duplicates, regardless of
   whether the interrupted run had landed 0, 3, or all 9 of them:

   ```
   $ grpcurl ... ImportBulkRelationships < bulk-clean.json   # naive retry
   ERROR: Code: AlreadyExists  Message: could not CREATE relationship `document:imp0#viewer__direct@user:zoe`...
   $ grpcurl ... WriteRelationships < touch-clean.json       # same 9, as TOUCH updates
   {"writtenAt": {"token": "..."}}
   $ zed relationship read document: ... | wc -l
   9
   ```

   This works *because* TOUCH is idempotent and the replay set is deterministic (the same
   transform applied to the same extracted tuples) -- there is no progress cursor to track,
   because "replay everything with TOUCH" converges on the correct end state from any partial
   starting point.

3. **On a store large enough that extraction itself must be resumed across separate
   invocations** (not just loading), checkpoint the OpenFGA-side continuation token -- but
   **the `fga` CLI cannot do this at all, and this is a silent-truncation trap, not a missing
   convenience.** Corrected after this file originally stated the CLI's own `json`-format
   output could be checkpointed page by page: verified live (`fga` v0.7.20) that the CLI's
   own reported `continuation_token` field is **always empty**, even mid-pagination, and that
   there is no flag to feed a token back in:

   ```
   $ fga tuple read --store-id $STORE_ID --page-size 10 --max-pages 1
   {"continuation_token":"","tuples":[ ... 10 of this store's 25 tuples ... ]}
   $ fga tuple read --store-id $STORE_ID --page-size 10 --max-pages 1 --debug 2>&1 | tail -1
   {"tuples":[ ... same 10 ... ],"continuation_token":"MTB8"}
   $ fga tuple read --store-id $STORE_ID --continuation-token MTB8
   Error: unknown flag: --continuation-token
   ```

   The real token exists -- `--debug` shows it in the raw API response the CLI itself just
   received -- the CLI simply never surfaces it as usable output, and has no flag to accept
   one back in either. **An agent that reads the CLI's own (always-empty)
   `continuation_token` as "no more data" after a `--max-pages N` call, for `N` short of what
   the store actually needs, stops there with no error** -- the same silent-truncation
   failure mode as `fga store export`'s 100-tuple default above, reappearing on the path this
   file recommends as safe.

   **The raw `Read` API -- HTTP gateway or gRPC, not the CLI -- is what a cross-invocation
   resumed extraction must call.** Verified live, paging a fresh 25-tuple store 10 at a time
   through the HTTP gateway (`POST /stores/{store_id}/read`), each response's own
   `continuation_token` correctly advances the next request, and the union of all three pages
   is the store's full 25 tuples with no duplicates:

   ```
   $ curl -s -XPOST localhost:8080/stores/$STORE_ID/read -d '{"page_size":10}' \
     | python3 -c 'import json,sys;d=json.load(sys.stdin);print(len(d["tuples"]),d["continuation_token"])'
   10 MTB8
   $ curl -s -XPOST localhost:8080/stores/$STORE_ID/read -d '{"page_size":10,"continuation_token":"MTB8"}' \
     | python3 -c 'import json,sys;d=json.load(sys.stdin);print(len(d["tuples"]),d["continuation_token"])'
   10 MjB8
   $ curl -s -XPOST localhost:8080/stores/$STORE_ID/read -d '{"page_size":10,"continuation_token":"MjB8"}' \
     | python3 -c 'import json,sys;d=json.load(sys.stdin);print(len(d["tuples"]),repr(d["continuation_token"]))'
   5 ''
   ```

   `grpcurl -plaintext -d '{"store_id":"'$STORE_ID'","page_size":10}' localhost:8081
   openfga.v1.OpenFGAService.Read` returns the identical `continuation_token` for the same
   first page, confirming this is not an HTTP-gateway-only quirk -- gRPC exposes the same
   field correctly; only the `fga` CLI's own output does not. Persist each response's
   `continuation_token` to a checkpoint file before requesting the next page; a resumed
   extraction starts from the last persisted token rather than from the beginning.

   This is independent of the load-side TOUCH-replay recipe above, and independent of the
   single-invocation `--max-pages 0` path this file recommends -- that path is unaffected and
   still exhaustively correct in one call, verified earlier in this section (259/259 tuples).
   This resumability recipe applies only once a store is large enough that a single
   `--max-pages 0` invocation itself needs to survive being interrupted and restarted as a
   separate process; a store that fits in one such call never needs any of this. See
   "Verification pass" below for why a broken resumability recipe cannot be caught by
   comparing the target only against whatever the (possibly truncated) extraction produced --
   a second, source-side check is required.

### Dry run

Per `pack-contract.md` item 6's own answer, a dry run **reports counts without writing**: run
extraction and the full transform (including every local grammar check --
`_check_object_id`, `_check_caveat_name` -- which raise before any network call is made for a
malformed id or an ungrammatical caveat name), and report the relationship count, the count
per resource type, the count of relationships carrying a caveat, and the count of writes
targeting a `__direct` split relation, with zero calls to `WriteRelationships` or
`ImportBulkRelationships`. Every raised `InputError` during this pass is a finding to fix
before the real load, not a per-relationship failure to skip past.

Two stronger, optional checks for a store this pack has already converted a schema for. They
catch different things, and they do **not** cost the same to run -- (a) needs no server at
all, so reach for it first:

**(a) Run the transformed relationships through `zed validate`**, against a scratch
validation-YAML file built from them with `schemaFile:` pointing at the converted
`schema.zed`. **This is entirely in-process and needs no running SpiceDB** (the same fact
this file's Verification-pass section and `/spicedb-dev:migrate-tests`' own Notes state:
`zed validate` reads nothing from any endpoint, and the global `--endpoint`/`--token` flags
it inherits are accepted and ignored). What it catches is every way a relationship can
disagree with the *shape* of the deployed schema -- and it catches all of them offline, exit
1, with nothing listening anywhere:

```
$ pgrep -fl "spicedb serve" || echo "(no spicedb process)"
(no spicedb process)
$ zed validate --fail-on-warn v.yaml     # document:d1#nonexistent_relation@user:alice
error: parse error in `document:d1#nonexistent_relation@user:alice`: relation/permission
`nonexistent_relation` not found under definition `document`
$ echo $?
1
```

Same offline rejection, exit 1, for an unknown definition (`object definition `widget` not
found`), a subject type outside the relation's allowed-type list (`subjects of type `team`
are not allowed on relation `document#viewer__direct``), an undeclared caveat name, and --
usefully for this phase -- **a write aimed at a permission instead of its `__direct` relation**
(`cannot write a relationship to permission `viewer` under definition `document``), which is
this file's single highest-consequence transform error. None of that needs a server; do not
credit any of it to a live target.

**(b) Actually write them to a throwaway `spicedb serve-testing` instance** (deploy the
converted `schema.zed` to a fresh token first) and spot-check a handful of
`zed permission check` calls before touching the real target. This is the one that needs a
server, and what it adds over (a) is the **write path itself**: the real RPCs, the client SDK
or CLI actually used, and the server-enforced **limits**, which `zed validate` has no concept
of because it never makes a call. Demonstrated on the same relationship -- a 26,011-byte
caveat context passes (a) and is rejected by (b):

```
$ zed validate --fail-on-warn v.yaml            # 26,011-byte caveat context, offline
Success! - 1 relationships loaded, 0 assertions run, 0 expected relations validated

$ zed relationship create document:d1 viewer__direct user:alice --caveat "big:$(cat ctx.json)" \
    --endpoint localhost:50799 --token big1 --insecure
error: rpc error: code = InvalidArgument desc = provided relationship
`document:d1#viewer__direct@user:alice` exceeded maximum allowed caveat size of 25000
```

That is the accurate division of labour: **(a) for schema-shape correctness, free and
serverless; (b) for the limits and the write path**, per the "Limits" table above. Both were
exercised live throughout this file's own verification.

### Concurrency: a one-shot copy is stale the moment extraction ends

Everything above describes a **point-in-time copy**. State the consequence plainly, because
the verification pass below is otherwise easy to misread: on a source still taking writes,
every tuple written after extraction ends exists in OpenFGA and does **not** exist in
SpiceDB. That is **unmigrated data**, not measurement noise, and it is not something a
migration should be signed off around. A count mismatch on a live store is the same fact the
truncation check exists to catch, arriving by a different route -- treat it as a fail, not as
drift to explain away.

Two correct ways to close it. Prefer the first:

1. **Quiesce the source for the extract → load window** -- a maintenance window, or the
   source system put in a read-only mode for the duration. Then extraction is a true
   snapshot, and a level-1 count mismatch is unambiguously truncation, with no second
   explanation available.
2. **If the source cannot be quiesced, converge instead of accepting a delta.** Re-extract
   and replay the *entire* transformed set through `WriteRelationships`/`TOUCH` -- idempotent,
   per "Resumability" above -- and repeat until a fresh source count and the target count
   agree. Each pass narrows the window to the duration of that pass. Do **not** stop at "the
   delta is small and looks explainable"; a delta is a list of relationships that are missing
   from SpiceDB, and replaying is cheap precisely because `TOUCH` makes it safe from any
   starting point.

**Catch up the incremental writes with OpenFGA's `ReadChanges` API, not another full
extraction.** `ReadChanges` returns the store's ordered write/delete log, and -- unlike
`fga tuple read`, whose continuation token the CLI discards (see "Resumability" point 3) --
the CLI surfaces this one *and* accepts it back. Verified live (`fga` v0.7.20,
`openfga/openfga:latest`); `fga tuple read --help` mentions no continuation token at all,
while `fga tuple changes --help` documents the flag:

```
$ fga tuple read --help | grep -ci continuation
0
$ fga tuple changes --help | grep continuation
      --continuation-token string   Continuation token to start changes from.
$ TOK=$(fga tuple changes --store-id $STORE_ID | python3 -c "import json,sys; print(json.load(sys.stdin)['continuation_token'])")
$ # ... a write lands on the live store after the token was captured ...
$ fga tuple changes --store-id $STORE_ID --continuation-token "$TOK"
TUPLE_OPERATION_WRITE document:d3 viewer user:carl        # exactly the one new write, nothing replayed
```

Capture a token *before* extraction starts, then replay `ReadChanges` from it after the load
to apply everything that landed in between.

**`ReadChanges` reports deletes, and this is why a count alone is not enough.** A tuple
deleted from the source after extraction is still present on the target, and no amount of
`TOUCH` replay will ever remove it -- the converge loop above only ever adds. Worse, a delete
and a write in the same window **cancel out in the count** while leaving the target wrong in
two places at once. Verified live on the same store:

```
$ fga tuple changes --store-id $STORE_ID --continuation-token "$TOK"
TUPLE_OPERATION_DELETE document:d3 viewer user:carl
TUPLE_OPERATION_WRITE  document:d4 viewer user:dee
$ fga tuple read --store-id $STORE_ID --output-format simple-json --max-pages 0 \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
3        # unchanged from before the window -- the count nets to zero, the contents do not
```

So: apply each change by its own `operation` (`TUPLE_OPERATION_WRITE` → `TOUCH`,
`TUPLE_OPERATION_DELETE` → a relationship delete), and read a matching level-1 count as
**necessary but not sufficient** on a live store.

**None of this makes a one-shot copy safe for a cutover.** It makes it *convergent*, which is
a different and weaker property. The durable form is the cutover playbook carried by
the framework skill's `cutover-strategies.md`: **dual-write** (the application writes both
systems, source stays authoritative), **shadow-read** (SpiceDB answers in parallel,
disagreements logged rather than enforced), and a standing **reconciliation job** on the
assumption that drift is normal rather than exceptional. A migration that ends at "the counts
matched once" has no mechanism for the next write.

### Verification pass

After loading, verify against the deployed target, not against the source system's own
oracle in isolation -- the source oracle proves the *source* was read correctly; only a
post-load read proves the *target* now matches it. **Three levels, run in this order**,
matching the pack's existing convention (`schema-mapping.md` and `test-mapping.md` verify
live throughout, not just via `zed validate`):

- **Extraction completeness, first -- against the source, before the target is even
  queried.** The two checks below only prove the *target* matches the *transformed
  extraction*; neither can detect an extraction that itself under-read the source, which is
  exactly the failure the broken CLI-resumability recipe above produces silently, with no
  error anywhere else in the pipeline (the "Resumability" section's own closing note). Before
  trusting the transformed set as an oracle for anything, independently re-ask the source for
  its true total and compare it to the transformed line count: OpenFGA has no dedicated
  "count tuples" RPC, but the single-invocation `--max-pages 0` path is already established
  above as exhaustively correct in one call (259/259, verified), so a **fresh**
  `fga tuple read --output-format simple-json --max-pages 0` run, counted (not
  re-transformed), is a trustworthy oracle for "how many tuples does this store actually
  have" regardless of how the primary extraction was performed. Verified live, reproducing
  exactly the failure this check exists to catch: a deliberately truncated extraction (one
  page of a 25-tuple store, `--max-pages 1 --page-size 10`) was transformed and loaded in
  full -- the target then agreed perfectly with the truncated oracle (10 relationships on
  both sides), so a verification pass that only compared target-to-oracle reported a clean
  **PASS** on a 60%-truncated migration. Re-asking the source independently caught it:

  ```
  $ # target-vs-oracle only (the two checks below, in isolation):
  target count=10, oracle count=10 -> PASS   # both sides built from the same truncated read
  $ # source-side completeness, re-querying OpenFGA fresh:
  $ fga tuple read --store-id $STORE_ID --output-format simple-json --max-pages 0 \
    | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
  25
  ```

  25 (the source's real total) against 10 (the oracle) is an immediate, unambiguous fail --
  halt here, before the two checks below, since they would otherwise report success on a
  target that only agrees with a wrong oracle. This check has no false positive on a
  complete extraction: the same recipe against an untruncated 25/25 extraction reports
  `source total: 25 oracle: 25`, matching. **On a store still taking writes it has a second
  true positive, not a false one** -- see "Concurrency" above: the delta is relationships
  that exist in OpenFGA and not in SpiceDB. Converge (re-extract, TOUCH-replay, apply
  `ReadChanges` by operation) until the counts agree; do not sign the migration off against
  an unexplained-but-small delta, and remember a matching count is necessary rather than
  sufficient once deletes are in play.
- **Count and read-back.** `ExportBulkRelationships` (paginated, `optional_cursor`, up to
  `MaxBulkExportRelationshipsLimit` = 10,000 per response -- verified live, returns an
  `afterResultCursor` token to page with) or `zed relationship read` for a smaller store, to
  confirm the total relationship count on the target matches the transformed source set's
  count, and that no line was silently dropped or duplicated by an interrupted run's recovery
  path.
- **Sample checks.** Spot-check a handful of `zed permission check`/`LookupResources`/
  `LookupSubjects` calls against source-known-true and source-known-false facts, the same
  live-probe style `schema-mapping.md`'s tenancy and role sections use -- a green relationship
  count proves the *rows* landed, not that the *permissions* built from them resolve
  correctly, which is a schema-correctness question this file's transform cannot itself
  guarantee.

No level requires the unshipped harness; all three are ordinary `zed`/`fga` CLI calls or
direct RPCs, exactly as demonstrated throughout this file.

## Sync obligations

**The most consequential section in this file**, because it decides what kind of project this
actually is. Quoting `pack-contract.md` item 6 directly: a source construct "that reads state
SpiceDB cannot hold... becomes a replicated edge the customer owns permanently, with a write
path, a backfill, a reconciliation job, and a fail-closed window between the source-of-truth
write and the SpiceDB write. The obligation *count* is what separates a migration from an
ongoing synchronization project, and it must be surfaced at the gate, not discovered later."

### What creates one

A sync obligation is created whenever a Class B gate decision resolves a construct by
**materializing** a fact into a stored SpiceDB relationship that the source system computed
or received fresh on every check, rather than by passing that fact as request-time caveat
context. This pack has already built and measured this exact choice for three constructs,
and the pattern is consistent across all three:

- **Caveat context, the recommended default in every one of them, creates zero new sync
  obligations** -- the application already reads the relevant current value to authorize the
  request; passing it as check context is not incremental work
  (`groups-resource-attributes`'s own gate section: "needs zero ongoing SpiceDB sync
  obligations").
- **A materialized "verified"/marker relation -- the alternative, taken only when a
  periodically-refreshed answer is an accepted tradeoff -- creates exactly one**, per
  materialized attribute: a write path on every value change, a backfill for existing
  resources, a reconciliation job for drift, and an irreducible fail-closed window.

One construct class is a documented exception to "caveat = zero obligations":
`blockers.md`'s contextual-tuples blocker states plainly that **both** of its `effort`-rated
resolutions -- materializing the ephemeral edge as a real relationship, *or* re-modeling it as
caveat context -- "create phase-3 sync obligations; count them." Re-modeling still requires a
new caveat on the relevant relation and a place to source its context from, which for a
genuinely per-request-ephemeral value (as opposed to an already-authoritative current
attribute) is not free the way `ip-based-access`'s caveat form is. Do not assume every caveat
resolution is obligation-free without checking which of these two shapes it actually is.

### The four-part cost, demonstrated live

`schema-mapping.md`'s `groups-resource-attributes` section built and exercised one of these
end to end, including the fail-closed window itself, not just described it in the abstract.
Its own live demonstration (reproduced here because it is the concrete evidence this
section's abstract claim rests on):

```
$ zed relationship create document:1 draft document:1
$ zed permission check document:1 can_access user:anne    # draft
true
$ zed relationship delete document:1 draft document:1
$ zed permission check document:1 can_access user:anne    # mid-transition: neither marker set
false                                                       # true production risk, not a test artifact
$ zed relationship create document:1 published document:1
$ zed permission check document:1 can_access user:anne    # published
true
```

The mid-transition line **is** the fail-closed window, concretely: even granting SpiceDB's
own atomic `WriteRelationships` the ability to apply a delete-and-create as one call
(eliminating the gap the two separate calls above show), the irreducible gap is between the
customer's own database commit (`documents.status = 'published'`) and the SpiceDB write that
mirrors it -- two different systems, no distributed transaction spans them, and every check
in that window sees stale state.

### Netflix's own account of rejecting this pattern

AuthZed and Netflix jointly published an account of Netflix prototyping exactly this
materialize-and-replicate pattern on SpiceDB and rejecting it for production use, which is
directly on point here and is reproduced verbatim rather than paraphrased, since the exact
wording is the evidentiary claim:

> "The most salient being that it wasn't resilient to an absence of relationship data, e.g.
> if a new autoscaling group started and reporting its presence to SpiceDB had not yet
> happened, the autoscaling group members would be missing necessary permissions to run."

-- Chris Wolfe, Joey Schorr, and Victor Roldan Betancort, "ABAC on SpiceDB: Enabling
Netflix's Complex Identity Types," AuthZed blog, May 18 2023
(<https://authzed.com/blog/abac-on-spicedb-enabling-netflix-complex-identity-types>,
cross-posted to the Netflix Technology Blog:
<https://netflixtechblog.com/abac-on-spicedb-enabling-netflixs-complex-identity-types-c118f374fa89>).
The same post states Netflix went on to sponsor the caveated-relationships proposal that
became SpiceDB's `caveat` feature, specifically because this materialize-and-replicate shape
failed to meet their production freshness requirements. This is the same failure mode as the
`groups-resource-attributes` mid-transition window above, at production scale rather than a
two-line demonstration: a fact whose source of truth lives elsewhere, replicated into
SpiceDB's graph, has a window in which the replica lags the source and a check silently
resolves against stale state -- fail-closed if the missing edge denies (Netflix's case: new
autoscaling group members wrongly denied), but the general shape is symmetric and can just as
easily fail open if the replica lags in the other direction. This is offered as the industry
precedent for taking the count in this section seriously, not as a claim that every
materialized marker is unsafe -- both `ip-based-access`'s and `groups-resource-attributes`'s
own gate sections rate their respective marker alternative `effort`, not `blocked`, when the
weaker, periodically-refreshed guarantee is an accepted tradeoff for that specific call site.

### Detecting and counting them for `migration-plan.md`

1. **Read `migration-plan.md`'s `Decisions` → `Per-blocker resolutions` first.** Every
   materialized-marker or contextual-tuple resolution phase 0/1 already recorded is a sync
   obligation by construction; count each one once, per flagged construct/call site.
2. **If the plan predates this rule** (an older plan with no such entries recorded), re-derive
   from the committed `schema.zed`, using two pre-filters that each need a human/agent
   judgment call afterward -- neither is a precise detector on its own:
   - **A same-type self-relation** (`relation X: T` inside `definition T`), unioned into a
     permission with no caveat anywhere near it, is a *candidate* for "a status/state flag
     materialized as a persistent edge." It is only a candidate. A brace-matching parse of
     all 39 canonical `schema.zed` files for this raw shape (a relation whose allowed-type
     list includes its own enclosing definition, no `with`) returns **18 relations across 10
     stores** -- the unit is the relation, not the store, and the two counts differ because
     four stores contribute more than one hit:

     ```
     $ python3 selfrel.py     # brace-matching definition split; see command below
     10 stores / 18 relations
     abac-with-rebac.user.email_verified: user
     abac-with-rebac.document.draft: document
     abac-with-rebac.document.published: document
     expenses.employee.manager: employee
     file-storage.folder.parent: drive | folder
     gdrive.folder.parent: folder
     github.team.member: user | team#member
     github.organization.repo_admin: user | organization#member
     github.organization.repo_reader: user | organization#member
     github.organization.repo_writer: user | organization#member
     human-resources.team.parent_team: team
     issue-tracking.collection.parent_collection: collection
     knowledge-base.container.parent_container: container
     modeling-guide.group.member: user | group#member
     modeling-guide.folder.parent: folder
     modeling-guide.document.published: document
     multitenant-rbac.group.member: user | group#member
     multitenant-rbac.role.assignee: user | role#assignee | group#member
     ```

     Reading all 18 individually: **14 are not sync obligations**, splitting evenly into two
     groups of **seven**.

     Seven are ordinary recursive hierarchies: `expenses.employee.manager`,
     `human-resources.team.parent_team`, `issue-tracking.collection.parent_collection`,
     `knowledge-base.container.parent_container`, and `folder.parent` in each of
     `file-storage`/`gdrive`/`modeling-guide`.

     Seven mix a self-type userset into an otherwise ordinary group-membership or
     role-assignment pattern: `github.team.member`, `modeling-guide.group.member`,
     `multitenant-rbac.group.member`, `multitenant-rbac.role.assignee`, and
     `github.organization.repo_{admin,reader,writer}` -- the last being three relations
     sharing one pattern (an org-member userset admitted into a role relation), which is why
     this group is seven relations and not five.

     **Four are genuine materialized status flags, across two stores**, and because an
     obligation is counted per materialized *attribute* (this section's own "What creates
     one" rule) those four relations are **three obligations**:

     | Relation(s) | Obligation | Evidence |
     |---|---|---|
     | `abac-with-rebac.document.draft` + `.published` | 1 (one status attribute, two markers) | Store's own tuple comment: "This tuple can be written to OpenFGA when the document status changes or can be sent as a contextual tuple"; `blockers.md` names it directly |
     | `abac-with-rebac.user.email_verified` | 1 | Store's own tuple comment: "Whenever bob/anne verify their email, these two tuples should be created" |
     | `modeling-guide.document.published` | 1 | Feeds `permission can_view = ((viewer & published->viewer) + can_edit)` -- structurally identical to `abac-with-rebac`'s; source file `step-5-relation-based-abac.fga.yaml` opens "# Non published documents can be viewed only by editors" |

     Do not treat the raw pre-filter as the count -- it is a list to read, not a verdict, and
     14 of its 18 hits are not sync obligations. The parse itself must be brace-matching, not
     line-oriented: deciding whether `relation X: T` names its *own* enclosing definition
     requires knowing which definition the line sits in.

     ```
     $ cat selfrel.py
     import pathlib, re
     def defs(text):
         out = []
         for m in re.finditer(r'^definition\s+([A-Za-z0-9_/]+)\s*\{', text, re.M):
             name, i, depth = m.group(1), m.end() - 1, 0
             for j in range(i, len(text)):
                 if text[j] == '{': depth += 1
                 elif text[j] == '}':
                     depth -= 1
                     if depth == 0:
                         out.append((name, text[i+1:j])); break
         return out
     hits = []
     for d in sorted(pathlib.Path('corpus-runs').iterdir()):
         f = d / 'schema.zed'
         if not f.is_file(): continue
         for name, body in defs(f.read_text()):
             for rm in re.finditer(r'^\s*relation\s+([A-Za-z0-9_]+)\s*:\s*(.+)$', body, re.M):
                 rel, types = rm.group(1), rm.group(2).strip()
                 if 'with' in types: continue
                 if any(p.strip().split('#')[0] == name for p in types.split('|')):
                     hits.append((d.name, name, rel, types))
     print(len(set(h[0] for h in hits)), 'stores /', len(hits), 'relations')
     for h in hits: print(f'{h[0]}.{h[1]}.{h[2]}: {h[3]}')
     ```
   - **A sibling `schema-materialized-marker.zed`-style alternate file** next to the canonical
     `schema.zed` marks the caveat-vs-marker gate decision's *rejected* alternative, kept for
     comparison -- not a committed obligation. `ls corpus-runs/*/schema-materialized-marker.zed`
     finds exactly two, `advanced-entitlements` and `groups-resource-attributes`: both built
     and benchmarked this shape but shipped the caveat form as their actual conversion, so they
     contribute **zero** obligations as committed. `ip-based-access` discussed the same gate
     decision in prose but committed no alternate file at all.
   This is a fact about this plan's own corpus (**three committed sync obligations across two
   stores**, from four of the 18 pre-filtered relations, found by reading all 18 candidates
   rather than by trusting the pre-filter count), not a claim about any other codebase's
   likely count -- re-derive per project, and expect the pre-filter to over-match there too:
   here it over-matched 18 to 4 at the relation level.
3. **Record the result in `migration-plan.md`'s `## Sync obligations` table**
   (`findings-report.md`'s format: `obligation | source | write path | backfill |
   reconciliation`), one row per obligation, and **state the count explicitly** even when it
   is zero -- `findings-report.md`: "`None.` is a valid and useful value." The count is what
   a reader uses to answer "is this a one-time migration or an ongoing synchronization
   project we're signing up to run," and it is mispriced whenever it surfaces late.

## The ID codec

**One codec module, emitted once into the customer's project, consumed identically by phase 3
(this file) and phase 4 (client code, `/spicedb-dev:migrate-code`).** `naming-normalization.md`'s "One
codec, two consumers" states the risk precisely: "relationships stored under
`base64url(email)` while the application still checks `user:alice@corp.com`" is a silent
half-migration -- every check against a live relationship the data phase wrote would fail
forever, with no error anywhere, because the two phases encoded the same logical id two
different ways.

**Verified, both directions, to show exactly what "half-migration" looks like on a live
server:**

```
$ zed relationship create document:d1 viewer__direct 'user:YWxpY2VAY29ycC5jb20=' ...   # encoded, phase 3's write
Gh8K...
$ zed permission check document:d1 viewer 'user:YWxpY2VAY29ycC5jb20=' ...              # encoded check: correct
true
$ zed permission check document:d1 viewer 'user:alice@corp.com' ...                    # raw check: what a
error: rpc error: code = InvalidArgument desc = validation error:                      # not-yet-migrated
  subject.object.object_id: does not match regex pattern                               # call site would send
  `^(([a-zA-Z0-9/_|\-=+]{1,})|\*)$`
```

The second check does not even reach a `false` -- the raw, unencoded subject id fails
SpiceDB's own object-id grammar before any graph walk happens, since a raw email contains
`@`, outside `^[a-zA-Z0-9/_|\-=+]{1,1024}$`. Note the error text differs by surface, though
both are hard failures: a **check** is rejected server-side by protobuf validation, naming the
offending field and the wire pattern (above), while a **write** through the relationship-string
form is rejected client-side by `zed`'s own parser before any RPC is sent
(`zed relationship create document:d1 viewer__direct 'user:alice@corp.com'` → `error: invalid
relationship string`). Neither returns a silent `false`. A half-migrated application does not
get a silently-wrong answer here; it gets a hard error on every check against a migrated relation,
for every subject of the encoded type, until the call site is updated to encode the same way.
(A different half-migration shape -- two *different* encodings on the two sides, both
individually well-formed -- would instead produce a silent, permanent `false`, which is the
harder-to-notice failure mode this section exists to prevent.)

**The codec's contract, mirroring `encode_id` exactly** (`idmap.py`, the specification this
module reproduces in the target application's own language):

- `encode(source_type: str, source_id: str) -> str`
- `decode(source_type: str, spicedb_id: str) -> str` (the inverse, needed by any code path
  that reads a SpiceDB id back and must show or use the original value)
- Mode is per source type, driven by `migration-map.json`'s `id_encoding.types` list -- a
  type not listed passes through unchanged in both directions.
- The wildcard subject id `*` is never encoded or decoded, regardless of type.
- `base64url` mode: **padded** base64url -- Python's
  `base64.urlsafe_b64encode`/`urlsafe_b64decode`, or the target language's padded equivalent
  -- no further mangling. **"The language's equivalent" is not specific enough on its own:
  most languages ship both a padded and an unpadded base64url, both correct, producing
  different strings for the same input.** The `=` in this mode's stated charset is there
  because padding is expected. Any codec emitted from this contract must name the exact API
  it used in its header comment, so a second codec written later in another language
  (`migrate-code.md` step 3 rule 4 allows this) cannot silently pick the other variant --
  when they disagree, every id written by one is unfindable by the other and nothing errors
  at any layer.

  | Language | Use | Not |
  |---|---|---|
  | Go | `base64.URLEncoding` | `base64.RawURLEncoding` (unpadded) |
  | Python | `base64.urlsafe_b64encode` / `urlsafe_b64decode` | -- (padded only) |
  | TypeScript/JS | `Buffer.from(s).toString('base64url')` **plus** explicit `=` padding | bare `'base64url'` (unpadded) |
  | Java | `Base64.getUrlEncoder()` | `Base64.getUrlEncoder().withoutPadding()` |
  | C# | `Convert.ToBase64String` + `+/` -> `-_` | any unpadded helper |
  | Rust | `base64::engine::general_purpose::URL_SAFE` | `URL_SAFE_NO_PAD` |
  | Ruby | `Base64.urlsafe_encode64(s)` | `Base64.urlsafe_encode64(s, padding: false)` |
- An empty id, or an id whose encoded form would exceed SpiceDB's 1024-character object-id
  limit, is a hard error at encode time -- never a silent truncation, which would break the
  decode direction irrecoverably.

**Where it gets emitted, and why that is load-bearing:** phase 3 emits this module as a real
source file into the customer's project (in the application's own language, not this
plugin's Python), *before* generating the load script, and the load script imports it rather
than inlining the base64 call at each write site. Phase 4 then imports the **same** file when
rewriting call sites to encode/decode ids at the API boundary. One file, two importers, is
what makes "both encode identically" a structural guarantee rather than a convention two
independently-written pieces of code have to happen to agree on -- exactly the same
"structural, not incidental" argument `tuple_transform.py`'s own docstring makes for why
phase 3 and phase 5 share one `tuple_relationship` implementation rather than two copies kept
in sync by hand.

**Corpus-untested, stated plainly:** zero of the 39 corpus stores' committed
`migration-map.json` files use `id_encoding.mode != "none"`
(`for f in corpus-runs/*/migration-map.json; do python3 -c "import json,sys; d=json.load(open('$f')); print(d.get('id_encoding'))" ; done | grep -v "'mode': 'none'"` → no output), so the
codec's `base64url` branch is verified against the live server directly (as shown above), not
against any committed corpus fixture. `naming-normalization.md`'s and `test-mapping.md`'s own
"Deliberately not written yet" sections already flag the same gap from the schema-conversion
and test-conversion sides respectively; this is the data-conversion side of the identical,
still-open gap.

## Worked example: `github`, end to end

`test-mapping.md`'s own worked example converts this same store's test side; this file's own
example extends that with the extraction and load steps, composing into one full
extraction-through-verification picture. `schema-mapping.md`'s own "Worked example" section
demonstrates the identical relation-split mechanics this store's schema also exercises,
using a repository-hosting model of its own.

**Extraction** (live, against a real `openfga/openfga:latest` server seeded with this store's
model and its 9 root-level tuples):

```
$ fga tuple read --store-id $STORE_ID --output-format simple-json --max-pages 0
[
  {"object":"repo:openfga/openfga","relation":"writer","user":"user:beth"},
  {"object":"team:openfga/core","relation":"member","user":"user:charles"},
  {"object":"organization:openfga","relation":"member","user":"user:erik"},
  {"object":"team:openfga/core","relation":"member","user":"team:openfga/backend#member"},
  {"object":"repo:openfga/openfga","relation":"reader","user":"user:anne"},
  {"object":"repo:openfga/openfga","relation":"owner","user":"organization:openfga"},
  {"object":"repo:openfga/openfga","relation":"admin","user":"team:openfga/core#member"},
  {"object":"team:openfga/backend","relation":"member","user":"user:diane"},
  {"object":"organization:openfga","relation":"repo_admin","user":"organization:openfga#member"}
]
```

**Transform.** `organization.member` and `repo.{admin,reader,writer}` split
(`schema-mapping.md`'s own worked example shows why); `organization.owner`, `repo.owner`, and
`team.member` never split. Applying `write_relation` to the resource side of every line
above, and leaving every subject-side userset reference (`team:openfga/core#member`,
`organization:openfga#member`) unsuffixed, produces exactly `corpus-runs/github/
migration-map.json` plus this file's rules -- and matches, line for line, the `relationships:`
block `test-mapping.md`'s own worked example shows for the same store's converted
`validation.yaml`:

```
repo:openfga/openfga#owner@organization:openfga
organization:openfga#repo_admin@organization:openfga#member
organization:openfga#member__direct@user:erik
repo:openfga/openfga#admin__direct@team:openfga/core#member
repo:openfga/openfga#reader__direct@user:anne
repo:openfga/openfga#writer__direct@user:beth
team:openfga/core#member@user:charles
team:openfga/core#member@team:openfga/backend#member
team:openfga/backend#member@user:diane
```

**Load.** 9 relationships, well under every limit in this file -- one `ImportBulkRelationships`
call on a freshly-schema-deployed target, or one `WriteRelationships` call with `TOUCH`, both
verified to succeed in one shot earlier in this file.

**Verify.** `zed validate --fail-on-warn` against `corpus-runs/github/validation.yaml`
(committed, unedited) reaches `Success! - 9 relationships loaded, 6 assertions run, 0
expected relations validated` -- the assertions are `test-mapping.md`'s domain, not this
file's, but a green run here is exactly the kind of independent cross-check "Verification
pass" above recommends: the same 9 relationships, loaded through this file's own transform,
resolve every one of that store's converted checks correctly.

## Deliberately not written yet

Known gaps, held open on purpose until either a corpus store or a real migration forces the
rule, matching this pack's existing convention (`schema-mapping.md`'s own closing section,
spec decision D11).

- **`base64url` id encoding against a real customer store.** As stated above, zero of 39
  corpus stores exercise `id_encoding.mode != "none"`; every live verification of the codec
  in this file was constructed directly against a running server, not derived from a
  committed fixture. The interaction between a real object id near the 1024-character ceiling
  and `encode_id`'s hard-error-on-overflow behavior is untested end to end.
- **A generated load script in a second language, to confirm the codec/transform contract
  transfers cleanly.** Every live command in this file uses `zed`/`fga`/`grpcurl` directly;
  no reference implementation of "the generated script" (`pack-contract.md` item 6's own
  phrase) has been built in an application language, so the exact shape
  `/spicedb-dev:migrate-data` should generate is specified here but not yet built and run.
- **A real multi-caveat, multi-chunk `ImportBulkRelationships` failure at scale.** This file's
  resumability recipe was verified against a 9-relationship stream; the same recipe applied
  to a store spanning many thousand-relationship `WriteRelationships` batches, some
  interrupted mid-batch by a network failure rather than a clean `AlreadyExists`, has not been
  exercised.
- **Sync-obligation counting on a store whose obligation comes from a contextual-tuple
  resolution rather than a materialized-marker gate decision.** This file's worked evidence
  (`groups-resource-attributes`, `abac-with-rebac`) covers the marker shape end to end;
  `blockers.md`'s contextual-tuple options table offers both a materialize-as-relationships
  resolution and a re-model-as-caveat-context one; this file's evidence covers the first end
  to end, and the caveat-context resolution has no live, this-file-level worked example yet.
