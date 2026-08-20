# Java

API facts below (constructor names, method signatures, error types) were verified against the
vendored `spicedb-java` client's real source at commit `549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`.
Every sample was run against a live `spicedb serve-testing` `v1.56.0` instance; output shown is
real, not illustrative. Read `references/core-concepts.md` first for `Relationship`/`Filter`/
`Transaction`, consistency helpers, and streaming iteration -- this file only covers what's
specific to Java on top of that.

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

Three static factories, all in
`spicedb-java/lib/src/main/java/com/authzed/spicedb/SpiceDBClient.java`:
`createPlaintext(String endpoint, String presharedKey)` (`:76`),
`createSystemTls(String endpoint, String presharedKey)` (`:84`), and
`create(String endpoint, String presharedKey, ClientOption... options)` (`:97`) for the
plaintext, system-CA-TLS, and fully-configurable cases respectively. `SpiceDBClient` implements
`AutoCloseable`, so try-with-resources works.

```java
import com.authzed.spicedb.SpiceDBClient;

var client = SpiceDBClient.createPlaintext("localhost:50092", "task5key");
System.out.println("client constructed: " + client);
```

```
client constructed: com.authzed.spicedb.SpiceDBClient@233fe9b6
```

**`ClientOption` has exactly one factory, and it is a static method on `SpiceDBClient`
itself**: `public static ClientOption withInsecure()` (`SpiceDBClient.java:113`). Without
that pointer, the third factory's `ClientOption... options` parameter has no discoverable
argument -- `ClientOption` is a `@FunctionalInterface` (`:108`) with a single
`void apply(ManagedChannelBuilder<?> builder)`, so anything else has to be written by hand
against gRPC's builder. `withInsecure()` disables TLS, which is what `createPlaintext` does
for you; reach for `create(...)` when you need it *plus* your own option. Executed against a
live `spicedb serve-testing` v1.56.0, OpenJDK 25.0.2 / Gradle 9.4.0:

```java
try (var client =
    SpiceDBClient.create("localhost:50051", "javadoc", SpiceDBClient.withInsecure())) {
  var txn = new Transaction();
  txn.touch(Relationship.of("document", "doc1", "direct_viewer", "user", "alice"));
  String revision = client.write(txn);
  System.out.println("wrote at revision: " + revision);
  boolean allowed =
      client.checkPermission(
          Consistency.atLeastOrMinLatency(revision),
          "view",
          Relationship.of("document", "doc1", "view", "user", "alice"));
  System.out.println("alice can view document:doc1 = " + allowed);
}
```

```
wrote at revision: Gh8KEzE3ODY3NjUyMzg3NTk5MTQwMDASCGEzZmMxMDA5
alice can view document:doc1 = true
```

## Relationships: reads and writes

Build relationships with `Relationship.of(resourceType, resourceID, resourceRelation,
subjectType, subjectID, subjectRelation)` (or the 5-arg overload for no subject relation) --
`Relationship.java:39-77`. Batch writes with a `Transaction` (`create`/`touch`/`delete`,
`mustNotMatch`/`mustMatch` -- `Transaction.java`), then submit with `String write(Transaction
txn)` (`SpiceDBClient.java:195`).

```java
import com.authzed.spicedb.Filter;
import com.authzed.spicedb.Relationship;
import com.authzed.spicedb.Transaction;

var txn = new Transaction();
txn.touch(Relationship.of("document", "doc1", "direct_viewer", "user", "alice"));
txn.touch(Relationship.of("group", "eng", "member", "user", "bob"));
txn.touch(Relationship.of("document", "doc1", "group_viewer", "group", "eng", "member"));
txn.mustNotMatch(Filter.of("document").withResourceID("doc1").withRelation("editor"));
String revision = client.write(txn);
System.out.println("wrote 3 relationships at revision: " + revision);
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDUyOTYxMDY2NDkwMDASCGJhYjQ0YjE4
```

Read them back with `Stream<Relationship> readRelationships(Consistency consistency, Filter
filter)` (`SpiceDBClient.java:221`). The returned `Stream` is itself `AutoCloseable` and should
be closed when done -- a try-with-resources block, as below, does that automatically:

```java
import com.authzed.spicedb.Consistency;

try (var stream = client.readRelationships(Consistency.atLeast(revision), Filter.of("document").withResourceID("doc1"))) {
    stream.forEach(r -> System.out.println("relationship: " + r));
}
```

```
relationship: document:doc1#direct_viewer@user:alice
relationship: document:doc1#group_viewer@group:eng#member
```

## Checks

`boolean checkPermission(Consistency consistency, String permission, Relationship r)`
(`SpiceDBClient.java:125`) and the bulk form `List<Boolean> checkPermissions(Consistency
consistency, String permission, Relationship... relationships)` (`:134`, single-item batches
under `BulkCheckPermissions` just like Go/TypeScript). In Java, `permission` is always an
explicit string argument -- it is never inferred from the relationship, the opposite of Python's
`check_permission` (see `references/python.md`).

```java
boolean viewAllowed = client.checkPermission(Consistency.atLeast(revision), "view",
    Relationship.of("document", "doc1", "view", "user", "alice"));
System.out.println("alice can view document:doc1 = " + viewAllowed);

boolean editAllowed = client.checkPermission(Consistency.atLeast(revision), "edit",
    Relationship.of("document", "doc1", "edit", "user", "alice"));
System.out.println("alice can edit document:doc1 = " + editAllowed
    + " (permission is an explicit string arg to checkPermission, not read off the relationship)");
```

```
alice can view document:doc1 = true
alice can edit document:doc1 = false (permission is an explicit string arg to checkPermission, not read off the relationship)
```

Bulk, mixing a directly-granted, a group-granted, and a denied subject in one call:

```java
List<Boolean> bulkResults = client.checkPermissions(Consistency.atLeast(revision), "view",
    Relationship.of("document", "doc1", "view", "user", "alice"),
    Relationship.of("document", "doc1", "view", "user", "bob"),
    Relationship.of("document", "doc1", "view", "user", "carol"));
System.out.println("bulk view results [alice, bob(via group), carol] = " + bulkResults);
```

```
bulk view results [alice, bob(via group), carol] = [true, true, false]
```

## Lookups

`Stream<String> lookupResources(Consistency consistency, String resourceType, String
permission, String subjectType, String subjectID)` (`SpiceDBClient.java:263`) and
`Stream<String> lookupSubjects(Consistency consistency, String resourceType, String resourceID,
String permission, String subjectType)` (`:339`). Both take no cursor/limit parameter -- pages of
512 are fetched from the server internally as the `Stream` is consumed (`iteration_style` below).

```java
try (var stream = client.lookupResources(Consistency.atLeast(revision), "document", "view", "user", "bob")) {
    stream.forEach(resourceID -> System.out.println("bob can view: document:" + resourceID));
}
```

```
bob can view: document:doc1
```

## Consistency

Java's names match `references/core-concepts.md`'s table (`full()`, `minLatency()`,
`atLeast(rev)`, `atLeastOrFull(rev)`, `atLeastOrMinLatency(rev)`, `snapshot(rev)` -- all in
`spicedb-java/lib/src/main/java/com/authzed/spicedb/Consistency.java:33-106`). Live, back to
back on the same relationship:

```java
var viewRel = Relationship.of("document", "doc1", "view", "user", "alice");
boolean fullResult = client.checkPermission(Consistency.full(), "view", viewRel);
System.out.println("Consistency.full(): alice can view document:doc1 = " + fullResult);
boolean minLatResult = client.checkPermission(Consistency.minLatency(), "view", viewRel);
System.out.println("Consistency.minLatency(): alice can view document:doc1 = " + minLatResult);
```

```
Consistency.full(): alice can view document:doc1 = true
Consistency.minLatency(): alice can view document:doc1 = true
```

## Iteration

`readRelationships`, `lookupResources`, `lookupSubjects`, and `updates` all return a
`java.util.stream.Stream<T>` backed by a lazy `Iterator` that fetches pages from the server on
demand (`Spliterators.spliteratorUnknownSize` over a custom `Iterator`) -- not one big buffer up
front, though each page itself is buffered into a small internal list before its items are
yielded one at a time. The returned `Stream` implements `AutoCloseable`; close it (try-with-
resources, as in the examples above) when you don't consume it to exhaustion, so the underlying
gRPC call is released. See `references/core-concepts.md`'s iteration table for how this compares
to the other six languages (all lazy except Rust, which buffers into a `Vec`).

## Error handling

Java has a typed exception hierarchy, but a **narrower** one than the other five typed-hierarchy
languages: `SpiceDBException extends RuntimeException`
(`spicedb-java/lib/src/main/java/com/authzed/spicedb/errors/SpiceDBException.java:9`), with only
**4** subclasses -- `PermissionDeniedException`, `NotFoundException`, `AlreadyExistsException`,
`InvalidArgumentException` (one file each under `errors/`). `ErrorMapper.toSpiceDBException`
(`errors/ErrorMapper.java:26-39`) maps exactly those four gRPC codes (`PERMISSION_DENIED`,
`NOT_FOUND`, `ALREADY_EXISTS`, `INVALID_ARGUMENT`) to their matching subclass; every other gRPC
code -- including `FAILED_PRECONDITION`, `UNAVAILABLE`, `CANCELLED`, `RESOURCE_EXHAUSTED`,
`DEADLINE_EXCEEDED` -- falls through to the base `SpiceDBException` itself (`default -> new
SpiceDBException(message, e)`, `ErrorMapper.java:37`). C#, TypeScript, Python, Rust, and Ruby all
give each of those five codes its own subclass; Java is the outlier with only a base-class
catch-all for them. Practically: `catch (FailedPreconditionException e)` -- a pattern that
compiles and works in the other five typed-hierarchy languages -- has no equivalent to catch in
Java; you get the base `SpiceDBException` instead. Catch the base class to handle any SpiceDB
failure:

```java
import com.authzed.spicedb.errors.SpiceDBException;

var badTxn = new Transaction();
badTxn.touch(Relationship.of("document", "doc1", "not_a_real_relation", "user", "alice"));
try {
    client.write(badTxn);
} catch (SpiceDBException e) {
    System.out.println("write with undefined relation raised: " + e.getClass().getName() + ": " + e.getMessage());
    System.out.println("is instance of base SpiceDBException: " + (e instanceof SpiceDBException));
}
```

```
write with undefined relation raised: com.authzed.spicedb.errors.SpiceDBException: relation/permission `not_a_real_relation` not found under definition `document`
is instance of base SpiceDBException: true
```

That live output is itself the confirmation: a `FAILED_PRECONDITION` failure (an undefined
relation) surfaces as the bare `com.authzed.spicedb.errors.SpiceDBException`, not a
`FailedPreconditionException`, because no such subclass exists in this client.

Java also retries transient failures automatically (`SpiceDBClient.java`'s private `withRetry`,
`MAX_RETRIES = 3`, exponential backoff via `Thread.sleep`) -- covered once, for the languages
that have it, in `references/core-concepts.md`'s "Trust the code, not the docs" section rather
than repeated here.
