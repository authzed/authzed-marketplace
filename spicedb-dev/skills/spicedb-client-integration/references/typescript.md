# TypeScript

API facts below (constructor names, method signatures, error types) were verified against the
vendored `spicedb-typescript` client's real source at commit
`549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`. Every sample was run against a live `spicedb
serve-testing` `v1.56.0` instance; output shown is real, not illustrative. Read
`references/core-concepts.md` first for `Relationship`/`Filter`/`Transaction`, consistency
helpers, and streaming iteration -- this file only covers what's specific to TypeScript on top of
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

## Construction

`createSpiceDBClient(endpoint, token, options?)` (`spicedb-typescript/src/client.ts:739`) is a
thin factory over `new SpiceDBClient({ endpoint, token, ... })` (the constructor itself, `:85`).
`options` accepts `insecure`, `headers`, and `maxRetries` (default 3, see "Error handling"
below).

```ts
import { createSpiceDBClient } from "@spicedb/client";

const client = createSpiceDBClient("localhost:50092", "task4key", { insecure: true });
console.log("client constructed:", client.constructor.name);
```

```
client constructed: SpiceDBClient
```

## Relationships: reads and writes

Relationships are plain object literals matching the `Relationship` interface (`resourceType`,
`resourceId`, `resourceRelation`, `subjectType`, `subjectId`, `subjectRelation?`, ...). Batch
writes with a `Transaction` (`.create()`/`.touch()`/`.delete()`, `.mustMatch()`/
`.mustNotMatch()`, plus TypeScript's own `.withMetadata()` for watch-visible tracing metadata --
`spicedb-typescript/src/types.ts:173-255`, every method returns `this`), then submit with `async
write(txn: Transaction): Promise<string>` (`client.ts:245`).

```ts
import { Transaction } from "@spicedb/client";

const txn = new Transaction()
  .touch({ resourceType: "document", resourceId: "doc1", resourceRelation: "direct_viewer", subjectType: "user", subjectId: "alice" })
  .touch({ resourceType: "group", resourceId: "eng", resourceRelation: "member", subjectType: "user", subjectId: "bob" })
  .touch({ resourceType: "document", resourceId: "doc1", resourceRelation: "group_viewer", subjectType: "group", subjectId: "eng", subjectRelation: "member" })
  .mustNotMatch({ resourceType: "document", resourceId: "doc1", resourceRelation: "editor" });
const revision = await client.write(txn);
console.log(`wrote 3 relationships at revision: ${revision}`);
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDIzMTAwNzA1ODUwMDASCDAzM2EwN2Zl
```

Read them back with `async *readRelationships(filter, consistency): AsyncIterableIterator<Relationship>`
(`client.ts:215`):

```ts
for await (const r of client.readRelationships({ resourceType: "document", resourceId: "doc1" }, atLeast(revision))) {
  const subj = r.subjectRelation ? `${r.subjectType}:${r.subjectId}#${r.subjectRelation}` : `${r.subjectType}:${r.subjectId}`;
  console.log(`relationship: ${r.resourceType}:${r.resourceId}#${r.resourceRelation}@${subj}`);
}
```

```
relationship: document:doc1#direct_viewer@user:alice
relationship: document:doc1#group_viewer@group:eng#member
```

## Checks

`async checkPermission(consistency, check: CheckRequest): Promise<boolean>` (`client.ts:103`)
and the bulk form `checkPermissions(consistency, ...checks: CheckRequest[]): Promise<boolean[]>`
(`:140`). `CheckRequest` has its own `permission: string` field (`src/types.ts:78-86`) -- like
Go, and unlike Python, the permission is always explicit, never inferred from the relationship.

**Fails open on caveats at the pinned commit.** Both surfaces return `true` for
`CONDITIONAL_PERMISSION` (`client.ts:128-130` single, `:174-176` bulk) -- the state SpiceDB
returns when a caveated relationship's context was not supplied at check time. A `true` here
means "the server could not evaluate the condition," not "permitted," and TypeScript is the only
one of the seven that answers this way; the other six return `false`. If your schema uses caveats,
do not use these booleans directly as an authorization decision -- vendor a newer commit, whose
three-valued `CheckResult` distinguishes the case, or check permissionship on the raw response.

```ts
const viewAllowed = await client.checkPermission(atLeast(revision), {
  resourceType: "document", resourceId: "doc1", permission: "view", subjectType: "user", subjectId: "alice",
});
console.log(`alice can view document:doc1 = ${viewAllowed}`);

const editAllowed = await client.checkPermission(atLeast(revision), {
  resourceType: "document", resourceId: "doc1", permission: "edit", subjectType: "user", subjectId: "alice",
});
console.log(`alice can edit document:doc1 = ${editAllowed} (permission is an explicit field on the CheckRequest, not read off a relationship)`);
```

```
alice can view document:doc1 = true
alice can edit document:doc1 = false (permission is an explicit field on the CheckRequest, not read off a relationship)
```

Bulk, mixing a directly-granted, a group-granted, and a denied subject in one call:

```ts
const bulkResults = await client.checkPermissions(
  atLeast(revision),
  { resourceType: "document", resourceId: "doc1", permission: "view", subjectType: "user", subjectId: "alice" },
  { resourceType: "document", resourceId: "doc1", permission: "view", subjectType: "user", subjectId: "bob" },
  { resourceType: "document", resourceId: "doc1", permission: "view", subjectType: "user", subjectId: "carol" },
);
console.log(`bulk view results [alice, bob(via group), carol] = ${JSON.stringify(bulkResults)}`);
```

```
bulk view results [alice, bob(via group), carol] = [true,true,false]
```

## Lookups

`async *lookupResources(params: LookupResourcesParams, consistency): AsyncIterableIterator<string>`
(`client.ts:286`) and `lookupSubjects(params: LookupSubjectsParams, consistency):
AsyncIterableIterator<string>` (`:321`) -- both async generators, both take `permission` inside
their params object.

```ts
for await (const resourceId of client.lookupResources(
  { resourceType: "document", permission: "view", subjectType: "user", subjectId: "bob" },
  atLeast(revision),
)) {
  console.log(`bob can view: document:${resourceId}`);
}
```

```
bob can view: document:doc1
```

## Consistency

TypeScript's names match `references/core-concepts.md`'s table (`full()`, `minLatency()`,
`atLeast(rev)`, `atLeastOrFull(rev)`, `atLeastOrMinLatency(rev)`, `snapshot(rev)` -- all in
`spicedb-typescript/src/consistency.ts:12-77`). Live, back to back on the same relationship:

```ts
const viewCheck = { resourceType: "document", resourceId: "doc1", permission: "view", subjectType: "user", subjectId: "alice" };
const fullResult = await client.checkPermission(full(), viewCheck);
console.log(`full(): alice can view document:doc1 = ${fullResult}`);
const minLatResult = await client.checkPermission(minLatency(), viewCheck);
console.log(`minLatency(): alice can view document:doc1 = ${minLatResult}`);
```

```
full(): alice can view document:doc1 = true
minLatency(): alice can view document:doc1 = true
```

## Iteration

`readRelationships`, `lookupResources`, and `lookupSubjects` are all `async *method()` generators
(`AsyncIterableIterator<T>`) -- pages are pulled from the server on demand as you `for await`
over them, not buffered up front. See `references/core-concepts.md`'s iteration table for how
this compares to the other six languages (all lazy except Rust).

## Error handling

TypeScript has a typed error hierarchy: `SpiceDBError extends Error`
(`spicedb-typescript/src/errors.ts:6`) with 7 subclasses mapped from gRPC status codes
(`PermissionDeniedError`, `NotFoundError`, `AlreadyExistsError`, `InvalidArgumentError`,
`CancelledError`, `FailedPreconditionError`, `UnavailableError` -- `errors.ts:16-76`). Catch the
base class to handle any SpiceDB failure, or `instanceof` a specific subclass to handle one kind:

```ts
import { SpiceDBError } from "@spicedb/client";

const badTxn = new Transaction().touch({
  resourceType: "document", resourceId: "doc1", resourceRelation: "not_a_real_relation", subjectType: "user", subjectId: "alice",
});
try {
  await client.write(badTxn);
} catch (e) {
  const err = e as Error;
  console.log(`write with undefined relation raised: ${err.constructor.name}: ${err.message}`);
  console.log(`is instance of base SpiceDBError: ${e instanceof SpiceDBError}`);
}
```

```
write with undefined relation raised: FailedPreconditionError: [failed_precondition] relation/permission `not_a_real_relation` not found under definition `document`
is instance of base SpiceDBError: true
```

TypeScript also retries transient failures automatically (`client.ts`'s private `withRetry`,
`maxRetries` option, default 3, exponential backoff) -- covered once, for the languages that have
it, in `references/core-concepts.md`'s "Trust the code, not the docs" section rather than
repeated here.
