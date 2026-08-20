# spicedb-gen

`spicedb-gen` facts below (supported languages, flags, generated code shape) were verified
against the vendored `spicedb-gen` tool's real source and real output at commit
`549c4e90e7a1488adcf268e0e0033e48d5b5f0a4`. Every generation command below was actually run;
every call site shown was compiled or run for real -- Go and TypeScript against a live `spicedb
serve-testing` `v1.56.0` instance, Python against the same instance with `pyright` for static
checking, Java compiled with `javac` via Gradle. Read `references/core-concepts.md` first for
`Relationship`/`Filter`/`Transaction` vocabulary -- the code `spicedb-gen` generates is a typed
wrapper around exactly those concepts, not a replacement for them.

## What it does

Every idiomatic client (see `references/installation.md` and the per-language references) takes
resource types, relations, permissions, and subject types as plain strings. Nothing stops you
from writing `"conversaton"` instead of `"conversation"`, or passing a subject type the schema
doesn't actually allow on that relation -- the mistake surfaces only at runtime, as a gRPC error
or (worse) a silently-wrong `false`.

`spicedb-gen` reads a `.zed` schema and generates, per language, a wrapper module that turns
those strings into types: one type per resource definition, with a method or property per
relation and per permission, and a subject type restricted to whatever the schema actually
allows on that relation. A resource type, permission name, or subject type that doesn't match
the schema becomes a build-time error instead of a runtime one -- a real compiler error in Go
and Java, a build-blocking `tsc` error in TypeScript, and (since Python has no compile step of
its own) a `pyright`/`mypy` error that only surfaces if something actually runs the type checker.
It changes *when* a schema/code mismatch is caught, not what SpiceDB itself allows.

## Supported languages

Four of the seven idiomatic clients have generator support. Confirmed by reading the tool's
own language registry (`spicedb-gen/cmd/spicedb-gen/main.go:13-16`, which blank-imports one
package per supported language) and each package's own `Language()`/`language()` identifier:

| Language | Registered as | Source |
|---|---|---|
| Go | `go` | `spicedb-gen/golang/generator.go:26` |
| Java | `java` | `spicedb-gen/java/generator.go:27` |
| Python | `python` | `spicedb-gen/python/generator.go:26` |
| TypeScript | `typescript` | `spicedb-gen/typescript/generator.go:26` |

**C#, Ruby, and Rust have no generator support** -- there is no `csharp/`, `ruby/`, or `rust/`
package under `spicedb-gen/` at this commit. For those three languages, use the idiomatic client
directly (`references/csharp.md`, `references/ruby.md`, `references/rust.md`); there is no typed
wrapper to reach for.

Asking for an unsupported language fails clearly rather than silently:

```
$ ./spicedb-gen -schema chat.zed -lang ruby -out permissions.rb
unknown language "ruby"; registered languages: java python typescript go
```

Exit code `2`. The four names in that message are stable; their order isn't -- it comes from Go
map iteration and changes between runs, so don't script against a specific ordering, only
against the set `{go, java, python, typescript}`.

## Obtaining and building it

`references/installation.md` covers vendoring and building `spicedb-gen` -- it needs no sibling
`proto-clients/` directory (unlike the seven idiomatic clients), since it depends on the SpiceDB
server module directly for schema parsing, not on generated proto code. The build is one command
run from inside the vendored `spicedb-gen/` directory:

```
go build -o spicedb-gen ./cmd/spicedb-gen
```

## Running it

Three flags, all required:

```
Usage of ./spicedb-gen:
  -lang string
        target language, e.g. "typescript" (required)
  -out string
        output file path (required)
  -schema string
        path to .zed schema file (required)
```

Java additionally requires a fourth, undocumented-in-`-h` flag: `--java.package=<package>`.
Language-specific options follow a general `--<lang>.<key>=<value>` convention that the tool
strips out of `os.Args` before the standard `flag` package ever parses arguments (that's why
`-h` doesn't mention it -- `-h` is handled entirely by the standard package). At this commit,
Java is the only language that actually requires one:

```
$ ./spicedb-gen -schema chat.zed -lang java -out Permissions.java
generation error: java generator requires --java.package=<package> option
```

## The example schema

The commands and generated code below all run against this schema -- a small chat/messaging
model with organizations, groups, conversations, and messages:

```
definition user {}

definition organization {
    relation member__direct: user
    relation admin: user

    permission member = (member__direct + admin)
    permission admin__perm = admin
}

definition group {
    relation organization: organization
    relation member: user

    permission can_manage = organization->admin__perm
    permission can_view = (member + can_manage)
}

definition conversation {
    relation organization: organization
    relation owner: user
    relation member__direct: user | group#member

    permission organization_admin = organization->admin__perm
    permission member = (member__direct + owner)
    permission can_delete = (owner + organization_admin)
    permission can_edit = can_delete
    permission can_add_member = can_delete
    permission can_remove_member = can_delete
    permission can_post = (member + can_edit)
    permission can_view = can_post
}

definition message {
    relation conversation: conversation
    relation sender: user

    permission can_view = (sender + conversation->can_view)
    permission can_edit = sender
    permission can_delete = (sender + conversation->organization_admin)
    permission can_reply = conversation->can_view
}
```

`conversation#can_post` requires being a `member` (directly, or via `group#member`, or the
conversation's `owner`) or having `can_edit` (an org admin); `message#can_view` walks up to the
parent conversation's `can_view`. Nothing exotic -- ordinary relation arrows and set operations.

## Generating for real

```
$ ./spicedb-gen -schema chat.zed -lang go -out permissions.go
{"level":"trace","time":"2026-08-14T18:53:35-04:00","message":"adding object definition"}
{"level":"trace","time":"2026-08-14T18:53:35-04:00","message":"adding object definition"}
{"level":"trace","time":"2026-08-14T18:53:35-04:00","message":"adding object definition"}
{"level":"trace","time":"2026-08-14T18:53:35-04:00","message":"adding object definition"}
{"level":"trace","time":"2026-08-14T18:53:35-04:00","message":"adding object definition"}
wrote permissions.go
```

Exit code `0`. The five trace lines (one per top-level `definition`) print on every run
regardless of language; they're omitted below for brevity. TypeScript and Java (with the
required flag) generate the same way:

```
$ ./spicedb-gen -schema chat.zed -lang typescript -out permissions.ts
wrote permissions.ts

$ ./spicedb-gen -schema chat.zed -lang java -out Permissions.java --java.package=com.example.perms
wrote Permissions.java
```

Python is different -- on *this* schema, it fails:

```
$ ./spicedb-gen -schema chat.zed -lang python -out permissions.py
generation error: definition "organization" is referenced as both a subject and as a resource with its own permissions/relations/sub-refs; the python generator does not yet support merging these into a single class. Split the schema so this definition is used only as a subject or only as a resource, or open an issue
```

Exit code `2`. `organization` is used both as a bare resource (it has its own `member`/
`admin__perm` permissions) and as a bare subject type (`group.organization: organization`,
`conversation.organization: organization`) -- Python's generator detects that shape and refuses
to generate rather than produce something wrong. See "Known limitations" below; this is one of
three real gaps this reference ran into, not a one-off.

## What each language generates

All four generators produce one file: constructor functions per resource type (`Conversation(id)`,
`Message(id)`, ...), a permission/relation accessor per resource, a subject type restricted to
what the schema allows, and a small typed client wrapping the real client for `check`/`touch`/
`create`/`delete`/lookups. The mechanism each language uses to enforce "only a valid subject
type compiles" differs:

**Go** -- sealed interfaces. Every relation gets its own interface
(`ConversationMemberDirectSubject`) implemented only by the ref types the schema allows for that
relation, via an unexported marker method:

```go
type ConversationMemberDirectSubject interface {
    Subject
    isConversationMemberDirectSubject()
}

func (GroupMemberRef) isConversationMemberDirectSubject() {}
func (UserRef) isConversationMemberDirectSubject() {}

func (d ConversationRef) MemberDirect(subject ConversationMemberDirectSubject) TypedRelationship {
    ...
}
```

Permission accessors return a `Permission` value; a package-level `Check`/`LookupResources`/
`LookupSubjects` and `TypedClient.Touch`/`Create`/`Delete` do the actual client calls:

```go
func (d ConversationRef) CanPost() Permission {
    return Permission{resourceType: "conversation", resourceID: d.id, permission: "can_post"}
}

func Check(ctx context.Context, tc *TypedClient, cs consistency.Strategy, perm Permission, subject Subject) (bool, error) {
    ...
}
```

**TypeScript** -- structural typing over tagged object literals, not classes. Each resource
factory returns an object whose relation methods and permission fields all carry a literal
`_type`/`_permission`/`_relation` tag:

```ts
export function Conversation(id: string) {
    return {
        _type: "conversation" as const,
        _id: id,
        can_post: { _type: "conversation" as const, _id: id, _permission: "can_post" as const },
        member__direct: (subject: UserRef | GroupMemberRef) => ({
            _type: "conversation" as const, _id: id,
            _relation: "member__direct" as const, _subject: subject,
        }),
        // ... one entry per relation and per permission
    };
}
```

`TypedClient.check` is a single method with one `async check(...)` overload signature per
permission, each constraining both the resource shape and the allowed subject union:

```ts
async check(c: Consistency, p: { _type: "conversation"; _id: string; _permission: "can_post" }, s: UserRef | GroupMemberRef): Promise<boolean>;
```

**Java** -- Java's own `sealed interface ... permits ...` (Java 17+) for subject restriction,
plus generics binding the permission's subject type to the `check` call's subject argument:

```java
public sealed interface ConversationMemberDirectSubject extends Subject
    permits GroupMemberRef, GroupMemberType, UserRef, UserType {}

public record Permission<S extends Subject>(String resourceType, String resourceId, String permission) {}

public <S extends Subject> boolean check(Consistency c, Permission<S> perm, S subject) { ... }
```

**Python** -- `@dataclass(frozen=True)` resource classes with `@overload`-annotated `check`,
one overload per permission, each pinning the allowed subject type via a `Union`:

```python
@dataclass(frozen=True)
class Device:
    id: str
    _type: ClassVar[str] = "device"

    @property
    def can_rename_device(self) -> _DeviceCanRenameDevicePermission:
        return _DeviceCanRenameDevicePermission(_resource_type="device", _resource_id=self.id, _permission="can_rename_device")

    def it_admin(self, subject: DeviceItAdminSubject) -> TypedRelationship: ...
```

Because Python has no compiler, these `@overload` annotations only matter if something actually
type-checks the file -- `pyright` or `mypy` in CI or your editor. Running the file with plain
`python` never rejects a bad call; the type error only exists if you check for it. (This is also
why the generated project ships a `pyrightconfig.json` -- see
`spicedb-gen/testdata/python/` in the vendored tool for the pattern.)

## Typed call sites vs. stringly-typed calls

Two real mistakes, shown both ways: a subject of the wrong type, and a misspelled permission
name. In every language, the untyped call *compiles* and fails only when it reaches the server
(or, for the permission typo, it might not fail at all -- a different but equally valid
permission name would silently check the wrong thing).

### Go

Typed -- passing `Organization("acme")` where the schema only allows `user` or `group#member`
fails at `go build`, before the program ever runs:

```go
_ = Conversation("general").MemberDirect(Organization("acme"))
```

```
# spicedbgen-demo-typeerror
./main.go:10:43: cannot use Organization("acme") (value of struct type permissions.OrganizationRef) as permissions.ConversationMemberDirectSubject value in argument to Conversation("general").MemberDirect: permissions.OrganizationRef does not implement permissions.ConversationMemberDirectSubject (missing method isConversationMemberDirectSubject)
```

Untyped -- the identical mistake, written with the raw client's plain strings, compiles fine and
fails only when the write actually reaches the server:

```go
var txn rel.Txn
txn.Touch(rel.MustFromTriple("conversation", "general", "member__direct", "organization", "acme", ""))
_, err := c.Write(ctx, txn)
```

```
write with invalid subject type compiled fine; runtime result: spicedb: write: rpc error: code = InvalidArgument desc = subjects of type `organization` are not allowed on relation `conversation#member__direct`
```

The permission-name case is similar but sharper: `CnView()` (missing an `a`) simply doesn't
exist as a method, so Go rejects it before the misspelling could ever reach the network:

```go
_ = Conversation("general").CnView()
```

```
# spicedbgen-demo-typeerror2
./main.go:10:30: Conversation("general").CnView undefined (type permissions.ConversationRef has no field or method CnView)
```

```go
allowed, err := c.CheckOne(ctx, consistency.Full(), "cn_view",
    rel.MustFromTriple("conversation", "general", "cn_view", "user", "alice", ""))
```

```
CheckOne with typo'd permission "cn_view": allowed=false err=spicedb: check item 0: relation/permission `cn_view` not found under definition `conversation`
```

SpiceDB happened to reject `cn_view` outright because no relation or permission by that name
exists on `conversation`. If the typo had instead collided with a *real* but different
permission name, the untyped call would return a normal, wrong answer with no error at all --
the typed call can't make that mistake, because `CnView` (or any other name not in the schema)
never compiles.

### TypeScript

Same shape, caught by `tsc` instead of a Go compiler:

```ts
const bad = Conversation("general").member__direct(Organization("acme"));
```

```
src/typeerror.ts(5,52): error TS2345: Argument of type '{ _type: "organization"; ...omitted... }'
  is not assignable to parameter of type 'UserRef | GroupMemberRef'.
  Property '_relation' is missing in type '...' but required in type 'GroupMemberRef'.
```

### Python

Caught by `pyright`, using a schema where Python's generator succeeds (an IoT device/device-group
model -- see "Known limitations" for why `chat.zed` itself can't generate for Python):

```python
bad = Device("camera-1").security_guard(DeviceGroup("site-a").it_admin)
```

```
typeerror.py:5:41 - error: Argument of type "DeviceGroupItAdmin" cannot be assigned to parameter "subject" of type "DeviceSecurityGuardSubject" in function "security_guard"
    Type "DeviceGroupItAdmin" is not assignable to type "DeviceSecurityGuardSubject"
      "DeviceGroupItAdmin" is not assignable to "DeviceGroupSecurityGuard"
      "DeviceGroupItAdmin" is not assignable to "User" (reportArgumentType)
1 error, 0 warnings, 0 informations
```

The correct call, compiled by nothing (Python has no compiler) but accepted by `pyright` and
run live against `spicedb serve-testing`:

```python
tc = TypedClient(SpiceDBClient("localhost:50093", "task6pykey", insecure=True))
await tc.touch(
    DeviceGroup("site-a").for_it_admin(User("alice")),
    Device("camera-1").it_admin(DeviceGroup("site-a").it_admin),
    Device("camera-1").security_guard(User("bob")),
)
alice_can_rename = await tc.check(full(), Device("camera-1").can_rename_device, User("alice"))
```

```
wrote 3 relationships at revision: Gh8KEzE3ODY3NDgxNjk4ODUxMjUwMDASCDlkYTg1ODZi
alice can_rename_device on device:camera-1 (via device_group#it_admin) = True
bob can_rename_device on device:camera-1 (security_guard only) = False
bob can_view_live_video on device:camera-1 (direct security_guard) = True
```

## Known limitations

Three real gaps turned up while producing this reference -- verified by actually running the
generator, not inferred from reading its source:

**1. Self-referential resource types crash the generator entirely, in every language.** A
schema where a definition's relation points back to itself, directly or through another
definition (e.g. `folder { relation parent: drive | folder }`), sends the generator's subject
resolution into unbounded recursion:

```
runtime: goroutine stack exceeds 1000000000-byte limit
fatal error: stack overflow

runtime.throw(...)
github.com/authzed/spicedb-clients/spicedb-gen/schema.extractAllowedSubjects(...)
    spicedb-gen/schema/parse.go:97
github.com/authzed/spicedb-clients/spicedb-gen/schema.subjectsForRelation(...)
    spicedb-gen/schema/parse.go:231
```

Confirmed for Go, TypeScript, Java, and Python, all against the same schema (a self-referential
`folder`/`drive` hierarchy); Python additionally crashed the same way on two other schemas with
an equivalent self-referential shape (a `container`/`parent_container` hierarchy, and a
`collection`/`parent_collection` hierarchy). The crash is in the shared schema-parsing package used before
any language-specific code generation runs, not in one language's generator -- it isn't
language-specific, and it isn't a soft failure. If your schema has a resource type that can
contain another instance of itself, test-generate before depending on this tool for it; there is
no flag to work around it at this commit.

**2. A definition used both as a bare subject and as a resource with its own permissions is
handled inconsistently across languages.** This is exactly `organization` and `conversation` in
the schema above (each is referenced elsewhere as a bare subject type -- `group.organization:
organization`, `message.conversation: conversation` -- *and* declares its own permissions).

- **Go and TypeScript handle it correctly** -- every example in this document uses `organization`
  and `conversation` this way, and both languages generated complete, working accessors, as
  shown throughout.
- **Python detects it and refuses to generate**, with the clear error shown above in "Generating
  for real."
- **Java does not detect it, and silently generates an incomplete class** -- no error, exit code
  `0`, but `OrganizationRef` and `ConversationRef` come out with *no* permission accessors and
  *no* relation-writer methods at all, only the bare subject-identity methods. `Message` and
  `Group` (neither referenced elsewhere as a bare subject) come out complete. Confirmed by
  attempting to compile against the generated file:

  ```java
  var post = Conversation("general").canPost();
  ```
  ```
  Demo.java:10: error: cannot find symbol
    symbol:   method canPost()
    location: class ConversationRef
  ```
  ```java
  var rel = Organization("acme").admin(User("admin1"));
  ```
  ```
  Demo.java:11: error: cannot find symbol
    symbol:   method admin(UserRef)
    location: class OrganizationRef
  ```

  Java's silent, exit-0 failure mode is the more dangerous of the two: a clean generation run
  tells you nothing about whether the output is actually complete. **Always try compiling a real
  call site against every resource type your schema defines before trusting a Java generation
  run** -- don't rely on the generator's exit code alone.

  The workaround, if you hit this: fall back to the idiomatic Java client's raw
  `Relationship`/`Transaction` calls for the affected resource type, and use the typed API for
  everything else. Compiled and run live -- `organization` and `conversation` written with the
  raw client, `group` and `message` (both complete) written and checked through the typed one:

  ```java
  Transaction rawTxn = new Transaction();
  rawTxn.touch(Relationship.of("organization", "acme", "admin", "user", "admin1", ""));
  rawTxn.touch(Relationship.of("conversation", "general", "organization", "organization", "acme", ""));
  raw.write(rawTxn);

  String revision = tc.touch(
      Group("eng").forMember(User("bob")),
      Message("msg1").conversation(Conversation("general")),
      Message("msg1").sender(User("bob"))
  );
  boolean bobCanViewMsg = tc.check(Consistency.full(), Message("msg1").canView(), User("bob"));
  ```
  ```
  wrote relationships at revision: Gh8KEzE3ODY3NDg0NzE0MzE0MzcwMDASCGEwZmMzZGI5
  bob can_view message:msg1 (sender) = true
  bob can_reply message:msg1 (conversation member via group:eng#member) = true
  ```

**3. Java needs an extra, undocumented-in-`-h` flag.** Covered above under "Running it" --
`--java.package=<package>` is required for `-lang java` and doesn't appear in `./spicedb-gen -h`.

## Regenerating

Every generated file opens with `// Code generated by spicedb-gen. DO NOT EDIT.` (or the
language's equivalent comment syntax) -- there's no merge step, and hand edits are silently
overwritten the next time someone regenerates. Re-run `spicedb-gen` whenever the schema changes,
commit the regenerated file like any other source file, and let your language's compiler or type
checker (not this tool) tell you what call sites broke.
