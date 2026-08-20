# Go

API facts below (constructor names, method signatures, error types) were verified against the
vendored `spicedb-go` client's real source at commit `549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`.
Every sample was run against a live `spicedb serve-testing` `v1.56.0` instance; output shown is
real, not illustrative. Read `references/core-concepts.md` first for `Relationship`/`Filter`/
`Transaction`, consistency helpers, and streaming iteration -- this file only covers what's
specific to Go on top of that.

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

Three constructors, all in `spicedb-go/client/client.go`: `NewPlaintext(endpoint, presharedKey
string)` (`:41`), `NewSystemTLS(endpoint, presharedKey string)` (`:47`), and `NewWithOpts(endpoint,
presharedKey string, opts ...Option)` (`:52`), for the plaintext, system-CA-TLS, and
fully-configurable cases respectively.

```go
import "github.com/authzed/spicedb-clients/spicedb-go/client"

c, err := client.NewPlaintext("localhost:50092", "task4key")
fmt.Printf("client constructed: %T\n", c)
```

```
client constructed: *client.Client
```

## Relationships: reads and writes

Build relationships with `rel.MustFromTriple(resourceType, resourceID, resourceRelation,
subjectType, subjectID, subjectRelation)` (or `rel.FromTriple` for the error-returning form).
Batch writes with a `rel.Txn` (`Touch`/`Create`/`Delete`, `MustMatch`/`MustNotMatch` --
`spicedb-go/rel/rel.go`), then submit with `(c *Client) Write(ctx, txn) (revision string, err
error)` (`spicedb-go/client/relationships.go:21`).

```go
var txn rel.Txn
txn.Touch(rel.MustFromTriple("document", "doc1", "direct_viewer", "user", "alice", ""))
txn.Touch(rel.MustFromTriple("group", "eng", "member", "user", "bob", ""))
txn.Touch(rel.MustFromTriple("document", "doc1", "group_viewer", "group", "eng", "member"))
txn.MustNotMatch(rel.NewFilter("document").WithResourceID("doc1").WithRelation("editor"))
revision, err := c.Write(ctx, txn)
fmt.Printf("wrote 3 relationships at revision: %s\n", revision)
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDIwOTQzNDI4MTMwMDASCDAzM2EwN2Zl
```

Read them back with `(c *Client) ReadRelationships(ctx, cs, f rel.Filter) iter.Seq2[rel.Relationship,
error]` (`relationships.go:39`):

```go
for r, err := range c.ReadRelationships(ctx, consistency.AtLeast(revision), rel.NewFilter("document").WithResourceID("doc1")) {
    fmt.Printf("relationship: %s\n", r.String())
}
```

```
relationship: document:doc1#direct_viewer@user:alice
relationship: document:doc1#group_viewer@group:eng#member
```

## Checks

`(c *Client) CheckOne(ctx, cs, permission string, r rel.Relationship) (bool, error)`
(`checks.go:48`) and the bulk form `Check(ctx, cs, permission string, rs ...rel.Relationship)
([]bool, error)` (`checks.go:18`, single-item batches under `BulkCheckPermissions`). In Go,
`permission` is always an explicit string argument -- it is never inferred from the
relationship, which is the opposite of Python's `check_permission` (see `references/python.md`).

```go
viewAllowed, _ := c.CheckOne(ctx, consistency.AtLeast(revision), "view",
    rel.MustFromTriple("document", "doc1", "view", "user", "alice", ""))
fmt.Printf("alice can view document:doc1 = %v\n", viewAllowed)

editAllowed, _ := c.CheckOne(ctx, consistency.AtLeast(revision), "edit",
    rel.MustFromTriple("document", "doc1", "edit", "user", "alice", ""))
fmt.Printf("alice can edit document:doc1 = %v (permission is an explicit string arg to CheckOne, not read off the relationship)\n", editAllowed)
```

```
alice can view document:doc1 = true
alice can edit document:doc1 = false (permission is an explicit string arg to CheckOne, not read off the relationship)
```

Bulk, mixing a directly-granted, a group-granted, and a denied subject in one call:

```go
results, _ := c.Check(ctx, consistency.AtLeast(revision), "view",
    rel.MustFromTriple("document", "doc1", "view", "user", "alice", ""),
    rel.MustFromTriple("document", "doc1", "view", "user", "bob", ""),
    rel.MustFromTriple("document", "doc1", "view", "user", "carol", ""))
fmt.Printf("bulk view results [alice, bob(via group), carol] = %v\n", results)
```

```
bulk view results [alice, bob(via group), carol] = [true true false]
```

## Lookups

`LookupResources(ctx, cs, resourceType, permission, subjectType, subjectID string)
iter.Seq2[string, error]` (`lookup.go:18`) and `LookupSubjects(ctx, cs, resourceType,
resourceID, permission, subjectType string) iter.Seq2[string, error]` (`lookup.go:68`). Both
take no cursor/limit parameter -- the iterator pages internally (see
`references/core-concepts.md`'s note that Go and Ruby are the two clients that hide cursoring
entirely).

```go
for resourceID, err := range c.LookupResources(ctx, consistency.AtLeast(revision), "document", "view", "user", "bob") {
    fmt.Println("bob can view: document:" + resourceID)
}
```

```
bob can view: document:doc1
```

## Consistency

Go's names match `references/core-concepts.md`'s table exactly (`Full()`, `MinLatency()`,
`AtLeast(rev)`, `AtLeastOrFull(rev)`, `AtLeastOrMinLatency(rev)`, `Snapshot(rev)` -- all in
`spicedb-go/consistency/consistency.go:20-71`). Live, back to back on the same relationship:

```go
viewRel := rel.MustFromTriple("document", "doc1", "view", "user", "alice", "")
fullResult, _ := c.CheckOne(ctx, consistency.Full(), "view", viewRel)
fmt.Printf("consistency.Full(): alice can view document:doc1 = %v\n", fullResult)
minLatResult, _ := c.CheckOne(ctx, consistency.MinLatency(), "view", viewRel)
fmt.Printf("consistency.MinLatency(): alice can view document:doc1 = %v\n", minLatResult)
```

```
consistency.Full(): alice can view document:doc1 = true
consistency.MinLatency(): alice can view document:doc1 = true
```

## Iteration

`LookupResources`, `LookupSubjects`, and `ReadRelationships` all return `iter.Seq2[T, error]` --
Go's native lazy pull iterator (Go 1.23 range-over-func), consumed with a plain `for ... range`
as shown above. See `references/core-concepts.md`'s iteration table for how this compares to the
other six languages (all lazy except Rust, which buffers into a `Vec`).

## Error handling

Go has **no typed error hierarchy** for gRPC failures. `spicedb-go/rel/rel.go:19-25` defines
exactly three sentinel errors, all for input validation before any network call --
`ErrInvalidResource` (`:21`), `ErrInvalidRelation` (`:23`), `ErrInvalidSubject` (`:25`). Every
gRPC-level failure comes back wrapped as `fmt.Errorf("spicedb: ...: %w", err)` around the raw
`google.golang.org/grpc/status` error -- to distinguish error kinds, unwrap and check the gRPC
code rather than a type-switch:

```go
var badTxn rel.Txn
badTxn.Touch(rel.MustFromTriple("document", "doc1", "not_a_real_relation", "user", "alice", ""))
_, err := c.Write(ctx, badTxn)
fmt.Printf("write with undefined relation: err=%v\n", err)
if st, ok := status.FromError(errors.Unwrap(err)); ok {
    fmt.Printf("unwrapped gRPC code: %s\n", st.Code())
}
fmt.Printf("is this a typed SpiceDB exception? no -- it's %T, a plain wrapped error\n", err)
```

```
write with undefined relation: err=spicedb: write: rpc error: code = FailedPrecondition desc = relation/permission `not_a_real_relation` not found under definition `document`
unwrapped gRPC code: FailedPrecondition
is this a typed SpiceDB exception? no -- it's *fmt.wrapError, a plain wrapped error
```

For the sentinel errors, `errors.Is` works normally:

```go
_, err := rel.FromTriple("", "doc1", "direct_viewer", "user", "alice", "")
errors.Is(err, rel.ErrInvalidResource) // true
```

Go also has no built-in retry: `spicedb-go/client/*.go` and `spicedb-go/consistency/*.go` contain
no retry/backoff logic anywhere, and `go.mod` has no retry dependency -- unlike the other six
clients, which retry transient failures automatically (3-5 attempts, exponential backoff). If you
need resilience to transient gRPC failures, add your own retry/backoff around calls; re-verify
this note if your vendored commit differs from the one pinned above. `references/core-concepts.md`'s
"Trust the code, not the docs" section covers the broader retry/error-typing comparison across all
seven clients -- cited there rather than repeated here.
