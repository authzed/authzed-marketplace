# Rust

API facts below (constructor names, method signatures, error types) were verified against the
vendored `spicedb-rust` client's real source at commit `549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`.
Every sample was run against a live `spicedb serve-testing` `v1.56.0` instance; output shown is
real, not illustrative. Read `references/core-concepts.md` first for `Relationship`/`Filter`/
`Transaction`, consistency helpers, and streaming iteration -- this file only covers what's
specific to Rust on top of that. **Read the "Buffered, not streamed" section below before
porting any read-heavy code to Rust** -- it's the one fact in this file most likely to surprise
someone coming from another language's client, or from this client's own doc comments.

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

## Construction

Three ways to build a client, all `async` (the connection is established during construction),
in `spicedb-rust/src/client.rs`: `SpiceDBClient::new_plaintext(endpoint, token)` (`:83`),
`SpiceDBClient::new_system_tls(endpoint, token)` (`:93`), and `SpiceDBClient::builder(endpoint,
token)` (`:101`), which returns a `SpiceDBClientBuilder` for finer control (`.plaintext()` then
`.build()`).

```rust
use spicedb::client::SpiceDBClient;

let client = SpiceDBClient::new_plaintext("localhost:50092", "task5key").await?;
println!("client constructed: SpiceDBClient");
```

```
client constructed: SpiceDBClient
```

## Relationships: reads and writes

Build relationships with `Relationship::new(resource_type, resource_id, resource_relation,
subject_type, subject_id, subject_relation)`, returning `Result<Self, RelationshipError>`
(`spicedb-rust/src/types.rs:35-56`; `Relationship::from_objects` is a 5-arg shorthand for no
subject relation). Batch writes with a `Transaction` (`create`/`touch`/`delete` take `&Relationship`
by reference; `must_not_match`/`must_match` take a `Filter` by value -- `types.rs:411-451`), then
submit with `async fn write(&self, txn: &Transaction) -> Result<String, SpiceDBError>`
(`client.rs:244`).

```rust
use spicedb::types::{Filter, Relationship, Transaction};

let alice_direct = Relationship::new("document", "doc1", "direct_viewer", "user", "alice", "")?;
let bob_member = Relationship::new("group", "eng", "member", "user", "bob", "")?;
let eng_group_viewer = Relationship::new("document", "doc1", "group_viewer", "group", "eng", "member")?;
let mut txn = Transaction::new();
txn.touch(&alice_direct);
txn.touch(&bob_member);
txn.touch(&eng_group_viewer);
txn.must_not_match(Filter::new("document").with_resource_id("doc1").with_relation("editor"));
let revision = client.write(&txn).await?;
println!("wrote 3 relationships at revision: {revision}");
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDU1MjI1NzEzOTUwMDASCGJhYjQ0YjE4
```

Read them back with `async fn read_relationships(&self, consistency: &Strategy, filter: &Filter)
-> Result<Vec<Relationship>, SpiceDBError>` (`client.rs:306`) -- note the return type, covered in
full below. **This buffered shape is the pinned commit's; upstream `main` returns
`impl Stream<Item = Result<Relationship, SpiceDBError>>` instead, so the loop below changes shape
if you vendor a newer commit.**

```rust
use spicedb::consistency;

let rels: Vec<Relationship> = client
    .read_relationships(&consistency::at_least(&revision), &Filter::new("document").with_resource_id("doc1"))
    .await?;
for r in &rels {
    println!("relationship: {r}");
}
```

```
relationship: document:doc1#direct_viewer@user:alice
relationship: document:doc1#group_viewer@group:eng#member
```

## Checks

`async fn check_permission(&self, consistency: &Strategy, permission: &str, relationship:
&Relationship) -> Result<CheckResult, SpiceDBError>` (`client.rs:121`) and the bulk form
`check_permissions(&self, consistency: &Strategy, permission: &str, relationships:
&[Relationship]) -> Result<Vec<bool>, SpiceDBError>` (`:140`). Note `check_permission` (singular)
returns a `#[must_use] CheckResult { has_permission: bool }` wrapper, not a bare `bool` --
unwrap it via `.has_permission`. As in Go/TypeScript/C#/Java, `permission` is always an explicit
`&str` argument, never inferred from the relationship (contrast Python, `references/python.md`).

```rust
let view_rel = Relationship::new("document", "doc1", "view", "user", "alice", "")?;
let view_result = client.check_permission(&consistency::at_least(&revision), "view", &view_rel).await?;
println!("alice can view document:doc1 = {}", view_result.has_permission);

let edit_rel = Relationship::new("document", "doc1", "edit", "user", "alice", "")?;
let edit_result = client.check_permission(&consistency::at_least(&revision), "edit", &edit_rel).await?;
println!("alice can edit document:doc1 = {} (permission is an explicit &str arg to check_permission, not read off the relationship)", edit_result.has_permission);
```

```
alice can view document:doc1 = true
alice can edit document:doc1 = false (permission is an explicit &str arg to check_permission, not read off the relationship)
```

Bulk, mixing a directly-granted, a group-granted, and a denied subject in one call -- note
`check_permissions` (plural) returns a plain `Vec<bool>`, unlike the singular form's wrapper
struct:

```rust
let bulk_rels = vec![
    Relationship::new("document", "doc1", "view", "user", "alice", "")?,
    Relationship::new("document", "doc1", "view", "user", "bob", "")?,
    Relationship::new("document", "doc1", "view", "user", "carol", "")?,
];
let bulk_results = client.check_permissions(&consistency::at_least(&revision), "view", &bulk_rels).await?;
println!("bulk view results [alice, bob(via group), carol] = {bulk_results:?}");
```

```
bulk view results [alice, bob(via group), carol] = [true, true, false]
```

## Lookups

`async fn lookup_resources(&self, consistency: &Strategy, resource_type: &str, permission: &str,
subject_type: &str, subject_id: &str) -> Result<Vec<String>, SpiceDBError>` (`client.rs:401`)
and `lookup_subjects(...)` (`:466`). Both take no cursor/limit parameter -- pagination is handled
internally, and (unlike every other language covered in this skill) the *entire* result is
already collected into the returned `Vec` by the time `.await` resolves.

```rust
let resource_ids: Vec<String> = client
    .lookup_resources(&consistency::at_least(&revision), "document", "view", "user", "bob")
    .await?;
for resource_id in &resource_ids {
    println!("bob can view: document:{resource_id}");
}
```

```
bob can view: document:doc1
```

## Consistency

Rust's names match `references/core-concepts.md`'s table (`full()`, `min_latency()`,
`at_least(rev)`, `at_least_or_full(rev)`, `at_least_or_min_latency(rev)`, `snapshot(rev)` -- all
in `spicedb-rust/src/consistency.rs:52-93`). Live, back to back on the same relationship:

```rust
let full_result = client.check_permission(&consistency::full(), "view", &view_rel).await?;
println!("consistency::full(): alice can view document:doc1 = {}", full_result.has_permission);
let min_lat_result = client.check_permission(&consistency::min_latency(), "view", &view_rel).await?;
println!("consistency::min_latency(): alice can view document:doc1 = {}", min_lat_result.has_permission);
```

```
consistency::full(): alice can view document:doc1 = true
consistency::min_latency(): alice can view document:doc1 = true
```

## Buffered, not streamed -- and the client's own doc comments disagree

**Rust is the one language in this skill whose "streaming" reads are not lazy.** `read_relationships`
(`client.rs:306`), `lookup_resources` (`:401`), `lookup_subjects` (`:466`), `export_relationships`
(`:827`), and `updates` (`:887`, the watch call) all return `Result<Vec<T>, SpiceDBError>`. Every
one of them fully drains the underlying gRPC stream inside a `loop { ... stream.message().await
... }` *before returning* -- so for a large result set, Rust holds the entire thing in memory and
you get nothing until it has all arrived. That's a materially different memory/latency profile
than the other six languages' clients for the identical call. Confirmed live above: the
`read_relationships` and `lookup_resources` calls both type-check and run as plain `Vec`
assignments -- no `Stream`, `.next()`, or `poll_next()` anywhere in this file's samples.

This contradicts what the client's own source *says* about itself. The module-level doc comment
on `SpiceDBClient` (`client.rs:44`) and `read_relationships`'s own doc comment (`:305`) both say,
verbatim, `` Returns `impl Stream<Item = Result<T, SpiceDBError>>` `` -- and five more doc
comments elsewhere in the same file (`:298`, `:396`, `:460`, `:822`, `:882`) describe a "stream"
in softer prose ("Returns a stream of relationships...", "Returns a stream of resource IDs...").
None of that is what the compiled signatures do. Trust the return type in the code shown above,
not the doc comments next to it -- `references/core-concepts.md`'s "Trust the code, not the
docs" section covers this gap once, for both Rust here and the one other language affected
(Go's error/retry story), rather than repeating the general point in each file.

## Iteration

See the section above -- Rust is the sole exception to "lazy by default" among the seven clients
covered by this skill. `references/core-concepts.md`'s iteration table has the one-line summary
across all seven languages.

## Error handling

Rust has a typed error enum via `thiserror`: `SpiceDBError`
(`spicedb-rust/src/error.rs:11-52`) with variants `PermissionDenied(String)`, `NotFound(String)`,
`AlreadyExists(String)`, `InvalidArgument(String)`, `FailedPrecondition(String)`,
`Unavailable(String)`, `Cancelled(String)`, `Transport(String)` (connection-level failures, not a
gRPC status), plus a catch-all `Status { code: i32, message: String }` for any gRPC code without
its own variant. Match on the enum to handle one kind, or use `Display`/`Debug` to handle any of
them uniformly:

```rust
let mut bad_txn = Transaction::new();
let bad_rel = Relationship::new("document", "doc1", "not_a_real_relation", "user", "alice", "")?;
bad_txn.touch(&bad_rel);
match client.write(&bad_txn).await {
    Ok(_) => println!("unexpected success"),
    Err(e) => {
        println!("write with undefined relation raised: {e:?}");
        println!("Display: {e}");
    }
}
```

```
write with undefined relation raised: FailedPrecondition("relation/permission `not_a_real_relation` not found under definition `document`")
Display: failed precondition: relation/permission `not_a_real_relation` not found under definition `document`
```

Rust also retries transient failures automatically (`client.rs`'s private `retry` method,
`MAX_RETRIES = 5`, exponential backoff starting at 100ms) -- covered once, for the languages that
have it, in `references/core-concepts.md`'s "Trust the code, not the docs" section rather than
repeated here.
