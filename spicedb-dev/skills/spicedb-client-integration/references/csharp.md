# C#

API facts below (constructor names, method signatures, error types) were verified against the
vendored `spicedb-csharp` client's real source at commit
`549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`. Every sample was run against a live `spicedb
serve-testing` `v1.56.0` instance; output shown is real, not illustrative. Read
`references/core-concepts.md` first for `Relationship`/`Filter`/`Transaction`, consistency
helpers, and streaming iteration -- this file only covers what's specific to C# on top of that.

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

Three static factories, all in `spicedb-csharp/SpiceDB.Client/SpiceDBClient.cs`:
`CreatePlaintext(string endpoint, string presharedKey)` (`:47`), `CreateSystemTls(string
endpoint, string presharedKey)` (`:59`), and `CreateFromChannel(GrpcChannel channel, string
presharedKey)` (`:70`) for the plaintext, system-CA-TLS, and bring-your-own-channel cases
respectively. `SpiceDBClient` implements `IAsyncDisposable`.

```csharp
using SpiceDB.Client;

var client = SpiceDBClient.CreatePlaintext("localhost:50092", "task5key");
Console.WriteLine($"client constructed: {client.GetType()}");
```

```
client constructed: SpiceDB.Client.SpiceDBClient
```

## Relationships: reads and writes

Build relationships with `Relationship.FromTriple(resourceType, resourceID, resourceRelation,
subjectType, subjectID, subjectRelation = "")` (`Relationship.cs:29-47`). Batch writes with a
`Transaction` (`Create`/`Touch`/`Delete`, `MustNotMatch`/`MustMatch` -- `Transaction.cs`), then
submit with `async Task<string> WriteAsync(Transaction transaction, CancellationToken
cancellationToken = default)` (`SpiceDBClient.cs:186`).

```csharp
var txn = new Transaction();
txn.Touch(Relationship.FromTriple("document", "doc1", "direct_viewer", "user", "alice"));
txn.Touch(Relationship.FromTriple("group", "eng", "member", "user", "bob"));
txn.Touch(Relationship.FromTriple("document", "doc1", "group_viewer", "group", "eng", "member"));
txn.MustNotMatch(new Filter("document").WithResourceID("doc1").WithRelation("editor"));
var revision = await client.WriteAsync(txn);
Console.WriteLine($"wrote 3 relationships at revision: {revision}");
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDU0MjA0NTMxOTQwMDASCGJhYjQ0YjE4
```

Read them back with `IAsyncEnumerable<Relationship> ReadRelationshipsAsync(ConsistencyStrategy
consistency, Filter filter, ...)` (`SpiceDBClient.cs:211`):

```csharp
await foreach (var r in client.ReadRelationshipsAsync(Consistency.AtLeast(revision), new Filter("document").WithResourceID("doc1")))
{
    Console.WriteLine($"relationship: {r}");
}
```

```
relationship: document:doc1#direct_viewer@user:alice
relationship: document:doc1#group_viewer@group:eng#member
```

## Checks -- and where `CancellationToken` sits

`async Task<bool> CheckPermissionAsync(ConsistencyStrategy consistency, string permission,
Relationship relationship, CancellationToken cancellationToken = default)`
(`SpiceDBClient.cs:142`) and the bulk form `async Task<bool[]> CheckPermissionsAsync(
ConsistencyStrategy consistency, string permission, CancellationToken cancellationToken =
default, params Relationship[] relationships)` (`:104`). As in Go/TypeScript/Java, `permission`
is always an explicit string argument, never inferred from the relationship (contrast Python,
`references/python.md`).

```csharp
var viewAllowed = await client.CheckPermissionAsync(Consistency.AtLeast(revision), "view",
    Relationship.FromTriple("document", "doc1", "view", "user", "alice"));
Console.WriteLine($"alice can view document:doc1 = {viewAllowed}");

var editAllowed = await client.CheckPermissionAsync(Consistency.AtLeast(revision), "edit",
    Relationship.FromTriple("document", "doc1", "edit", "user", "alice"));
Console.WriteLine($"alice can edit document:doc1 = {editAllowed} (permission is an explicit string arg to CheckPermissionAsync, not read off the relationship)");
```

```
alice can view document:doc1 = True
alice can edit document:doc1 = False (permission is an explicit string arg to CheckPermissionAsync, not read off the relationship)
```

**`CheckPermissionsAsync`'s parameter order is not the normal .NET convention.** Compare the two
signatures above: the single-relationship `CheckPermissionAsync` overload places
`CancellationToken` *last*, as normal .NET convention dictates. `CheckPermissionsAsync`,
`CheckAnyAsync` (`:155`), and `CheckAllAsync` (`:168`) all place `CancellationToken` *before*
their trailing `params Relationship[] relationships` -- because C# requires `params` to be the
last formal parameter, so a positional `CancellationToken` cannot follow it on any overload that
also takes one. This isn't an arbitrary style choice; it's forced by the language. When calling
these three, pass `default` explicitly for the token (or name the `relationships:` parameter to
skip it):

```csharp
var bulkResults = await client.CheckPermissionsAsync(
    Consistency.AtLeast(revision), "view", default,
    Relationship.FromTriple("document", "doc1", "view", "user", "alice"),
    Relationship.FromTriple("document", "doc1", "view", "user", "bob"),
    Relationship.FromTriple("document", "doc1", "view", "user", "carol"));
Console.WriteLine($"bulk view results [alice, bob(via group), carol] = [{string.Join(", ", bulkResults)}]");
```

```
bulk view results [alice, bob(via group), carol] = [True, True, False]
```

## Lookups

`IAsyncEnumerable<string> LookupResourcesAsync(ConsistencyStrategy consistency, string
resourceType, string permission, string subjectType, string subjectID, ...)`
(`SpiceDBClient.cs:288`) and `LookupSubjectsAsync(...)` (`:341`). Neither takes a cursor/limit
parameter -- 512-item pages are fetched from the server internally as you `await foreach`.

```csharp
await foreach (var resourceId in client.LookupResourcesAsync(Consistency.AtLeast(revision), "document", "view", "user", "bob"))
{
    Console.WriteLine($"bob can view: document:{resourceId}");
}
```

```
bob can view: document:doc1
```

## Consistency

C#'s names match `references/core-concepts.md`'s table (`Full()`, `MinLatency()`,
`AtLeast(rev)`, `AtLeastOrFull(rev)`, `AtLeastOrMinLatency(rev)`, `Snapshot(rev)` -- all in
`spicedb-csharp/SpiceDB.Client/Consistency.cs:36-107`). Live, back to back on the same
relationship:

```csharp
var viewRel = Relationship.FromTriple("document", "doc1", "view", "user", "alice");
var fullResult = await client.CheckPermissionAsync(Consistency.Full(), "view", viewRel);
Console.WriteLine($"Consistency.Full(): alice can view document:doc1 = {fullResult}");
var minLatResult = await client.CheckPermissionAsync(Consistency.MinLatency(), "view", viewRel);
Console.WriteLine($"Consistency.MinLatency(): alice can view document:doc1 = {minLatResult}");
```

```
Consistency.Full(): alice can view document:doc1 = True
Consistency.MinLatency(): alice can view document:doc1 = True
```

## Iteration

`ReadRelationshipsAsync`, `LookupResourcesAsync`, `LookupSubjectsAsync`, `ExportRelationshipsAsync`,
and `UpdatesAsync` all return `IAsyncEnumerable<T>` implemented with `yield return` inside the
method -- a true lazy async stream. Pages are pulled from the server on demand as the caller
`await foreach`s, not buffered up front. See `references/core-concepts.md`'s iteration table for
how this compares to the other six languages (all lazy except Rust, which buffers into a `Vec`).

## Error handling

C# has a typed exception hierarchy: `SpiceDBException : Exception`
(`spicedb-csharp/SpiceDB.Client/Errors.cs:10`) with 9 subclasses mapped from gRPC status codes
(`PermissionDeniedException`, `NotFoundException`, `AlreadyExistsException`,
`InvalidArgumentException`, `FailedPreconditionException`, `UnavailableException`,
`CancelledException`, `ResourceExhaustedException`, `DeadlineExceededException` --
`Errors.cs:18-78`). Catch the base class to handle any SpiceDB failure, or catch a specific
subclass to handle one kind:

```csharp
var badTxn = new Transaction();
badTxn.Touch(Relationship.FromTriple("document", "doc1", "not_a_real_relation", "user", "alice"));
try
{
    await client.WriteAsync(badTxn);
}
catch (SpiceDBException e)
{
    Console.WriteLine($"write with undefined relation raised: {e.GetType().Name}: {e.Message}");
    Console.WriteLine($"is instance of base SpiceDBException: {e is SpiceDBException}");
}
```

```
write with undefined relation raised: FailedPreconditionException: relation/permission `not_a_real_relation` not found under definition `document`
is instance of base SpiceDBException: True
```

C# also retries transient failures automatically (`SpiceDBClient.cs`'s private `RetryAsync`,
`MaxRetryAttempts = 5`, exponential backoff starting at 100ms) -- covered once, for the languages
that have it, in `references/core-concepts.md`'s "Trust the code, not the docs" section rather
than repeated here.
