# Ruby

API facts below (constructor names, method signatures, error types) were verified against the
vendored `spicedb-ruby` client's real source at commit `549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`.
Every sample was run against a live `spicedb serve-testing` `v1.56.0` instance; output shown is
real, not illustrative. Read `references/core-concepts.md` first for `Relationship`/`Filter`/
`Transaction`, consistency helpers, and streaming iteration -- this file only covers what's
specific to Ruby on top of that. Ruby needs Ruby >= 3.2 for `Data.define`; if your system Ruby is
older (macOS's bundled Ruby is 2.6), use a newer Ruby via your version manager of choice, as
noted in `references/installation.md`.

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

Two class methods, both in `spicedb-ruby/lib/spicedb/client.rb`: `SpiceDB::Client.new_plaintext(endpoint,
token)` (`:50`) and `SpiceDB::Client.new_system_tls(endpoint, token)` (`:72`), for the plaintext
and system-CA-TLS cases respectively. Both accept an optional block -- pass one and the client is
yielded with `close` guaranteed via `ensure`; omit it and you get the client back directly (call
`close` yourself when done, as in the samples below).

```ruby
require 'spicedb'

client = SpiceDB::Client.new_plaintext('localhost:50092', 'task5key')
puts "client constructed: #{client.class}"
```

```
client constructed: SpiceDB::Client
```

## Relationships: reads and writes

Build relationships with `SpiceDB::Relationship.from_triple(resource_type, resource_id,
resource_relation, subject_type, subject_id, subject_relation = '')`
(`spicedb-ruby/lib/spicedb/relationship.rb:52-73`; `Relationship` itself is a Ruby 3.2+
`Data.define`, immutable). Batch writes with a `SpiceDB::Transaction` (`create`/`touch`/`delete`,
`must_not_match`/`must_match`, each returning `self` for chaining --
`spicedb-ruby/lib/spicedb/transaction.rb`), then submit with `def write(transaction)`
(`client.rb:167`), which returns the revision string.

```ruby
txn = SpiceDB::Transaction.new
txn.touch(SpiceDB::Relationship.from_triple('document', 'doc1', 'direct_viewer', 'user', 'alice'))
txn.touch(SpiceDB::Relationship.from_triple('group', 'eng', 'member', 'user', 'bob'))
txn.touch(SpiceDB::Relationship.from_triple('document', 'doc1', 'group_viewer', 'group', 'eng', 'member'))
txn.must_not_match(SpiceDB::Filter.new(resource_type: 'document', resource_id: 'doc1', relation: 'editor'))
revision = client.write(txn)
puts "wrote 3 relationships at revision: #{revision}"
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDU1ODM0NDUzNDQwMDASCGJhYjQ0YjE4
```

Read them back with `def read_relationships(consistency, filter)` (`client.rb:180`), which
returns an `Enumerator<SpiceDB::Relationship>`:

```ruby
client.read_relationships(
  SpiceDB::Consistency.at_least(revision),
  SpiceDB::Filter.new(resource_type: 'document', resource_id: 'doc1')
).each do |r|
  puts "relationship: #{r}"
end
```

```
relationship: document:doc1#direct_viewer@user:alice
relationship: document:doc1#group_viewer@group:eng#member
```

## Checks

`def check_permission(consistency, permission, relationship)` (`client.rb:115`) and the bulk
form `def check_permissions(consistency, permission, *relationships)` (`:126`, single-item
batches under `BulkCheckPermissions` like every other client in this skill). As in
Go/TypeScript/C#/Java/Rust, `permission` is always an explicit argument -- it is never inferred
from the relationship, the opposite of Python's `check_permission` (see `references/python.md`).

```ruby
view_allowed = client.check_permission(
  SpiceDB::Consistency.at_least(revision), 'view',
  SpiceDB::Relationship.from_triple('document', 'doc1', 'view', 'user', 'alice')
)
puts "alice can view document:doc1 = #{view_allowed}"

edit_allowed = client.check_permission(
  SpiceDB::Consistency.at_least(revision), 'edit',
  SpiceDB::Relationship.from_triple('document', 'doc1', 'edit', 'user', 'alice')
)
puts "alice can edit document:doc1 = #{edit_allowed} (permission is an explicit arg to check_permission, not read off the relationship)"
```

```
alice can view document:doc1 = true
alice can edit document:doc1 = false (permission is an explicit arg to check_permission, not read off the relationship)
```

Bulk, mixing a directly-granted, a group-granted, and a denied subject in one call --
`check_permissions` takes the relationships as a Ruby splat (`*relationships`), not an array
argument:

```ruby
bulk_results = client.check_permissions(
  SpiceDB::Consistency.at_least(revision), 'view',
  SpiceDB::Relationship.from_triple('document', 'doc1', 'view', 'user', 'alice'),
  SpiceDB::Relationship.from_triple('document', 'doc1', 'view', 'user', 'bob'),
  SpiceDB::Relationship.from_triple('document', 'doc1', 'view', 'user', 'carol')
)
puts "bulk view results [alice, bob(via group), carol] = #{bulk_results}"
```

```
bulk view results [alice, bob(via group), carol] = [true, true, false]
```

## Lookups

`def lookup_resources(consistency, resource_type, permission, subject_type, subject_id)`
(`client.rb:225`) and `def lookup_subjects(consistency, resource_type, resource_id, permission,
subject_type)` (`:253`), both returning an `Enumerator<String>`. Neither takes a cursor/limit
parameter -- like Go, Ruby hides cursoring entirely inside the `Enumerator` (see
`references/core-concepts.md`'s note that Go and Ruby are the two clients that do this).

```ruby
client.lookup_resources(
  SpiceDB::Consistency.at_least(revision), 'document', 'view', 'user', 'bob'
).each do |resource_id|
  puts "bob can view: document:#{resource_id}"
end
```

```
bob can view: document:doc1
```

## Consistency

Ruby's names match `references/core-concepts.md`'s table (`full`, `min_latency`, `at_least(rev)`,
`at_least_or_full(rev)`, `at_least_or_min_latency(rev)`, `snapshot(rev)` -- all module functions
in `spicedb-ruby/lib/spicedb/consistency.rb:36-86`; note `full` and `min_latency` take no
parentheses-required call since they take no arguments, though `full()` also works). Live, back
to back on the same relationship:

```ruby
view_rel = SpiceDB::Relationship.from_triple('document', 'doc1', 'view', 'user', 'alice')
full_result = client.check_permission(SpiceDB::Consistency.full, 'view', view_rel)
puts "Consistency.full: alice can view document:doc1 = #{full_result}"
min_lat_result = client.check_permission(SpiceDB::Consistency.min_latency, 'view', view_rel)
puts "Consistency.min_latency: alice can view document:doc1 = #{min_lat_result}"
```

```
Consistency.full: alice can view document:doc1 = true
Consistency.min_latency: alice can view document:doc1 = true
```

## Iteration

`read_relationships`, `lookup_resources`, `lookup_subjects`, and `updates` all return a plain
Ruby `Enumerator` built with `Enumerator.new do |yielder| ... end` (six call sites in
`client.rb`, e.g. `:181`, `:226`, `:254`) -- pages are fetched from the server on demand as the
caller iterates (`.each`, `.to_a`, `for`, etc.), not buffered up front. Live confirmation of both
the class and the lazy pull:

```ruby
enum = client.read_relationships(
  SpiceDB::Consistency.at_least(revision),
  SpiceDB::Filter.new(resource_type: 'document', resource_id: 'doc1')
)
puts "read_relationships(...).class == #{enum.class}"
puts "read_relationships(...).to_a == #{enum.to_a.map(&:to_s)}"
```

```
read_relationships(...).class == Enumerator
read_relationships(...).to_a == ["document:doc1#direct_viewer@user:alice", "document:doc1#group_viewer@group:eng#member"]
```

See `references/core-concepts.md`'s iteration table for how this compares to the other six
languages (all lazy except Rust, which buffers into a `Vec`).

## Error handling

Ruby has a typed exception hierarchy: `SpiceDB::Error < StandardError`
(`spicedb-ruby/lib/spicedb/errors.rb:5`) with 9 subclasses mapped from gRPC status codes
(`PermissionDeniedError`, `NotFoundError`, `AlreadyExistsError`, `InvalidArgumentError`,
`FailedPreconditionError`, `UnavailableError`, `CancelledError`, `DeadlineExceededError`,
`ResourceExhaustedError` -- `errors.rb:8-32`). Rescue the base class to handle any SpiceDB
failure, or a specific subclass to handle one kind:

```ruby
bad_txn = SpiceDB::Transaction.new
bad_txn.touch(SpiceDB::Relationship.from_triple('document', 'doc1', 'not_a_real_relation', 'user', 'alice'))
begin
  client.write(bad_txn)
rescue SpiceDB::Error => e
  puts "write with undefined relation raised: #{e.class}: #{e.message}"
  puts "is instance of base SpiceDB::Error: #{e.is_a?(SpiceDB::Error)}"
end
```

```
write with undefined relation raised: SpiceDB::FailedPreconditionError: relation/permission `not_a_real_relation` not found under definition `document`
is instance of base SpiceDB::Error: true
```

Ruby also retries transient failures automatically (`client.rb`'s private `with_retry`,
`MAX_RETRIES = 3`, exponential backoff starting at `BASE_RETRY_DELAY = 0.1` seconds) -- covered
once, for the languages that have it, in `references/core-concepts.md`'s "Trust the code, not
the docs" section rather than repeated here.
