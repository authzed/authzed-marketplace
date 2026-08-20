# Python

API facts below (constructor signature, method signatures, error types) were verified against
the vendored `spicedb-python` client's real source at commit
`549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`. Every sample was run against a live `spicedb
serve-testing` `v1.56.0` instance; output shown is real, not illustrative. Read
`references/core-concepts.md` first for `Relationship`/`Filter`/`Transaction`, consistency
helpers, and streaming iteration -- this file only covers what's specific to Python on top of
that.

The examples below share one schema:

```
definition user {}
definition group {
    relation member: user
}
definition document {
    relation direct_viewer: user
    relation group_viewer: group#member
    relation editor: user
    permission view = direct_viewer + group_viewer + editor
    permission edit = editor
}
```

## Async-only, one constructor

Unlike Go/C#/Rust/Ruby, Python has no `New*`/`Create*` constructor family and no separate
sync/async client -- there's exactly one class, and every method on it is `async def`:
`SpiceDBClient(endpoint, token, *, insecure=False, max_retries=3)` (`spicedb-python/spicedb/
client.py:38`).

```python
from spicedb.client import SpiceDBClient

client = SpiceDBClient("localhost:50092", "task4key", insecure=True)
print(f"client constructed: {type(client)}")
```

```
client constructed: <class 'spicedb.client.SpiceDBClient'>
```

Calling this (or any `async def`) client from a synchronous framework call site (a Flask/Django
view, a plain-`def` Celery task) needs a sync-to-async bridge -- typically a persistent background
event loop plus `run_coroutine_threadsafe`. That bridge has its own second-order deadlock trap in
its lazy-construction path; see `openfga-to-spicedb/references/code-mapping.md`'s "The
synchronous-caller bridge, and a second-order deadlock in the obvious version of it" for the
verified repro and the fix. Not repeated here -- this file covers the client's own API surface,
not the calling framework's threading model.

## Relationships: reads and writes

Build a `Relationship(resource_type, resource_id, resource_relation, subject_type, subject_id,
subject_relation="")` and batch writes with a `Transaction` (`.create()`/`.touch()`/`.delete()`,
`.must_match()`/`.must_not_match()`, each returning `self` for chaining --
`spicedb-python/spicedb/types.py`), then submit with `async def write(self, txn: Transaction) ->
str` (`client.py:321`).

```python
from spicedb.types import Filter, Relationship, Transaction

txn = (
    Transaction()
    .touch(Relationship("document", "doc1", "direct_viewer", "user", "alice"))
    .touch(Relationship("group", "eng", "member", "user", "bob"))
    .touch(Relationship("document", "doc1", "group_viewer", "group", "eng", "member"))
    .must_not_match(Filter(resource_type="document", resource_id="doc1", relation="editor"))
)
revision = await client.write(txn)
print(f"wrote 3 relationships at revision: {revision}")
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDIxNDEwMzMzODIwMDASCDAzM2EwN2Zl
```

Read them back with `async def read_relationships(self, filter, consistency) ->
AsyncIterator[Relationship]` (`client.py:197`):

```python
async for r in client.read_relationships(Filter(resource_type="document", resource_id="doc1"), consistency.at_least(revision)):
    subj = f"{r.subject_type}:{r.subject_id}"
    if r.subject_relation:
        subj += f"#{r.subject_relation}"
    print(f"relationship: {r.resource_type}:{r.resource_id}#{r.resource_relation}@{subj}")
```

```
relationship: document:doc1#direct_viewer@user:alice
relationship: document:doc1#group_viewer@group:eng#member
```

## Checks -- the permission divergence

**This is the one fact in this file that matters most for cross-language porting.**
`check_permission(self, consistency, rel, *, context=None)` (`client.py:107`) has **no
`permission` parameter at all**. The permission checked is whatever string is in
`rel.resource_relation` -- `client.py:140` builds the proto request with
`permission=rel.resource_relation`. Every other language (Go, TypeScript, C#, Java, Rust, Ruby)
takes `permission` as its own explicit argument; Python is the only outlier. Code that ports a
check from another language by copy-pasting the relationship and permission separately will not
compile -- there's nowhere to put the permission except inside the relationship itself.

Live, on the *same* resource/subject pair, changing only `resource_relation`:

```python
view_rel = Relationship("document", "doc1", "view", "user", "alice")
view_allowed = await client.check_permission(consistency.at_least(revision), view_rel)
print(f"rel.resource_relation='view'  -> check_permission = {view_allowed}")

edit_rel = Relationship("document", "doc1", "edit", "user", "alice")
edit_allowed = await client.check_permission(consistency.at_least(revision), edit_rel)
print(f"rel.resource_relation='edit'  -> check_permission = {edit_allowed}")
```

```
rel.resource_relation='view'  -> check_permission = True
rel.resource_relation='edit'  -> check_permission = False
```

The bulk form, `check_permissions(self, consistency, *rels, context=None)` (`client.py:120`),
follows the same rule per-relationship:

```python
bulk_results = await client.check_permissions(
    consistency.at_least(revision),
    Relationship("document", "doc1", "view", "user", "alice"),
    Relationship("document", "doc1", "view", "user", "bob"),
    Relationship("document", "doc1", "view", "user", "carol"),
)
print(f"bulk view results [alice, bob(via group), carol] = {bulk_results}")
```

```
bulk view results [alice, bob(via group), carol] = [True, True, False]
```

## Lookups

`lookup_resources(self, resource_type, permission, subject, consistency, *, context=None)`
(`client.py:222`) and `lookup_subjects(self, resource, permission, subject_type, consistency, *,
subject_relation="", context=None)` (`client.py:282`) -- both `async def ... yield` generators,
and both do take `permission` as an explicit argument (only `check_permission`'s signature omits
it).

```python
async for resource_id in client.lookup_resources("document", "view", ("user:bob", ""), consistency.at_least(revision)):
    print(f"bob can view: document:{resource_id}")
```

```
bob can view: document:doc1
```

## Consistency

Python's names match `references/core-concepts.md`'s table (`full()`, `min_latency()`,
`at_least(rev)`, `at_least_or_full(rev)`, `at_least_or_min_latency(rev)`, `snapshot(rev)` -- all
in `spicedb-python/spicedb/consistency.py:18-55`). Live, back to back on the same relationship:

```python
full_result = await client.check_permission(consistency.full(), view_rel)
print(f"consistency.full(): alice can view document:doc1 = {full_result}")
min_lat_result = await client.check_permission(consistency.min_latency(), view_rel)
print(f"consistency.min_latency(): alice can view document:doc1 = {min_lat_result}")
```

```
consistency.full(): alice can view document:doc1 = True
consistency.min_latency(): alice can view document:doc1 = True
```

## Iteration

`read_relationships`, `lookup_resources`, `lookup_subjects`, and `watch` are all true `async def
... yield` generators -- pages are pulled from the server on demand as you iterate, not buffered
up front. See `references/core-concepts.md`'s iteration table for how this compares to the other
six languages (all lazy except Rust).

## Error handling

Python has a typed exception hierarchy: `SpiceDBError` (`spicedb-python/spicedb/errors.py:8`)
with 7 subclasses covering the common gRPC codes (`PermissionDeniedError`, `NotFoundError`,
`AlreadyExistsError`, `InvalidArgumentError`, `FailedPreconditionError`, `UnavailableError`,
`CancelledError`) -- `errors.py:12-36`. Catch the base class to handle any SpiceDB failure, or a
specific subclass to handle one kind:

```python
from spicedb.errors import SpiceDBError

bad_txn = Transaction().touch(Relationship("document", "doc1", "not_a_real_relation", "user", "alice"))
try:
    await client.write(bad_txn)
except SpiceDBError as e:
    print(f"write with undefined relation raised: {type(e).__name__}: {e}")
    print(f"is instance of base SpiceDBError: {isinstance(e, SpiceDBError)}")
```

```
write with undefined relation raised: FailedPreconditionError: relation/permission `not_a_real_relation` not found under definition `document`
is instance of base SpiceDBError: True
```

Python also retries transient failures automatically (`client.py`'s `_with_retry`,
`max_retries=3` by default, exponential backoff) -- covered once, for the languages that have it,
in `references/core-concepts.md`'s "Trust the code, not the docs" section rather than repeated
here.
