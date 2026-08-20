# Installation

This is the only file in this skill that describes how to obtain a SpiceDB client. Everything
else assumes you already have one vendored and building. That split is deliberate: when the
clients move from prototype to published packages, this file changes to normal package installs
and nothing else in this skill needs to move.

---

## Prototype status

> **PROTOTYPE -- not for production use.** The SpiceDB clients are in early development. APIs,
> types, and behaviors may change or break at any time, and bugs are expected. Pin to a specific
> commit, and budget time for breakage if you track upstream.

(That warning is the vendored repo's own, from its README -- it is not this skill being
cautious on top of a stable project.)

**"Prototype" scopes to *these* clients, not to SpiceDB clients in general.** Authzed's
established, long-published client family is a different thing entirely, is generally
available, and is what the rest of this plugin uses (`spicedb-best-practices/references/
client-patterns.md` tables it; `/spicedb-dev:implement-spicedb-checks` writes code importing
it). Verified live, the day this was written:

| Language | Published, established client | Latest version | Checked with |
|---|---|---|---|
| Go | `github.com/authzed/authzed-go` | `v1.10.0` | `curl https://proxy.golang.org/github.com/authzed/authzed-go/@latest` |
| TypeScript | `@authzed/authzed-node` (npm) | `1.6.1` | `npm view @authzed/authzed-node version` |
| Python | `authzed` (PyPI) | `1.25.0` | `curl https://pypi.org/pypi/authzed/json` |
| C# | `Authzed.Net` (NuGet) | `1.6.0` | `curl https://api.nuget.org/v3-flatcontainer/authzed.net/index.json` |

Reach for those if you want a supported, versioned client in Go, TypeScript, Python, or C#
today and do not specifically need this prototype's API or one of the three languages only it
covers (Java, Rust, Ruby).

**The seven prototype clients this file installs are not published on any registry**, and
that is what makes vendoring the only supported path *for them*. Verified, per language,
rather than asserted:

- npm has no `@spicedb/client` (the name in `spicedb-typescript/package.json`), no `spicedb`,
  and no `@authzed/spicedb` -- all three `npm view` 404. The package is also marked
  `"private": true`, which blocks `npm publish` outright.
- `go get` fails for a reason worth knowing, because it is not "the repo is unreachable": the
  Go module proxy *does* serve the prototype's source
  (`.../spicedb-clients-prototype/spicedb-go@549c4e90e7a1...` resolves to pseudo-version
  `v0.0.0-20260527225752-549c4e90e7a1`), but `spicedb-go/go.mod` declares its module path as
  `github.com/authzed/spicedb-clients/spicedb-go` -- a repository that does not exist
  publicly. The download succeeds and the build then fails with `module declares its path
  as: github.com/authzed/spicedb-clients/spicedb-go but was required as:
  github.com/authzed/spicedb-clients-prototype/spicedb-go`. There is no import path that
  resolves.
- crates.io has no `spicedb` crate; RubyGems has no `spicedb` gem; Maven Central has nothing
  under `com.authzed`.

**Two package names that do resolve are other people's code, not this client** -- which is a
stronger reason to avoid a package-manager install than "it will fail", because these
succeed:

- PyPI `spicedb` is **`0.1.0a2`, "An unofficial SpiceDB client for Python"**
  (`gitlab.com/aedge/spicedb-python`) -- unrelated to Authzed and to this prototype.
- NuGet `SpiceDb` is **`1.6.2`, "SpiceDb/Authzed grpc compatible permissions library"** by a
  community author (`github.com/JalexSocial/SpiceDb`) -- also not Authzed's.

`pip install spicedb` and `dotnet add package SpiceDb` therefore install a stranger's library
under a name that looks right. The only supported path to *this* client is vendoring the
source at the pinned commit below.

## What's pinned

| | |
|---|---|
| Repository | `https://github.com/authzed/spicedb-clients-prototype` |
| Commit | `549c4e90e7a1488adcf268e0e0033e48d5b5f0a4` |
| Verified against | `spicedb` `v1.56.0` (`serve-testing`) |
| SpiceDB version floor for this skill | `v1.52.0` |

Every code sample and every wiring recipe below was executed against this exact commit. If your
vendored copy is at a different commit, re-verify before trusting these instructions literally
-- especially the per-language wiring steps, which depend on the exact shape of each client's
own dependency manifest at this commit, not on any guarantee that shape is stable.

## What to vendor: two directories, not one

For each language, vendor **the language's client directory** and **its sibling
`proto-clients/<language>-proto` directory** -- both, preserving `proto-clients/` as a sibling
of the language directory rather than nesting it inside. That relative layout matters: every
client's own dependency manifest at this commit already declares a relative
`../proto-clients/<language>-proto` dependency on its proto sibling (Go's `go.mod`, via
`replace`; Python's `pyproject.toml`, via `[tool.uv.sources]`; TypeScript's `package.json`, via
a pnpm workspace reference; C#'s `.csproj`, via `<ProjectReference>`; Java's
`settings.gradle.kts`, via `includeBuild`; Rust's `Cargo.toml`, via a path dependency; Ruby's
`Gemfile`, via `path:`). Preserve the sibling relationship and most of that wiring keeps working
without edits to the vendored source itself.

| Language | Client directory | Proto directory |
|---|---|---|
| Go | `spicedb-go/` | `proto-clients/spicedb-go-proto/` |
| Python | `spicedb-python/` | `proto-clients/spicedb-python-proto/` |
| TypeScript | `spicedb-typescript/` | `proto-clients/spicedb-typescript-proto/` |
| C# | `spicedb-csharp/` | `proto-clients/spicedb-csharp-proto/` |
| Java | `spicedb-java/` | `proto-clients/spicedb-java-proto/` |
| Rust | `spicedb-rust/` | `proto-clients/spicedb-rust-proto/` |
| Ruby | `spicedb-ruby/` | `proto-clients/spicedb-ruby-proto/` |

General recipe:

1. Clone `spicedb-clients-prototype` at the pinned commit somewhere outside your project (a
   scratch directory, never inside your own repo).
2. Copy the two directories for your language into your project, keeping `proto-clients/` a
   sibling of the language directory -- e.g. `third_party/spicedb-clients/spicedb-go/` next to
   `third_party/spicedb-clients/proto-clients/spicedb-go-proto/`.
3. Wire the vendored client into your own project's dependency manifest (below -- this is where
   each language differs, and where two of the seven need a real edit, not just a path).
4. Build or import as usual.

Do not commit the full monorepo clone, and do not vendor languages you don't need -- each
language's client and proto directories are independent of every other language's.

## Per-language wiring

Every recipe below was verified by actually vendoring, wiring, and building in a scratch
project. **Go's was verified end-to-end against a live `spicedb serve-testing` v1.56.0**
instance -- client construction, a write, and a permission check that flips from denied to
allowed. The other six were verified by vendoring, wiring, and a successful
build/import/construction (not a live permission check) against the same pinned commit.

### Go

Go ignores `replace` directives declared inside a *dependency's* `go.mod` -- only the directives
in your own module's `go.mod` take effect. `spicedb-go/go.mod` already has
`replace github.com/authzed/spicedb-clients/proto-clients/spicedb-go-proto => ../proto-clients/spicedb-go-proto`,
but that line does nothing once `spicedb-go` is someone else's dependency; you have to redeclare
it yourself, pointed at wherever you vendored the proto directory.

Also: don't name the destination directory `vendor`. Go treats a repo-root `vendor/` directory
specially (it expects a consistent `vendor/modules.txt`), and a plain vendored copy without one
breaks `go build` with `inconsistent vendoring` errors that have nothing to do with SpiceDB.
Use any other name -- `third_party/spicedb-clients/`, `internal/spicedb-clients/`, etc.

```
myapp/
  go.mod
  main.go
  third_party/spicedb-clients/
    spicedb-go/                          <- vendored
    proto-clients/spicedb-go-proto/      <- vendored, sibling of spicedb-go
```

`go.mod`:

```
module myapp

go 1.24

require github.com/authzed/spicedb-clients/spicedb-go v0.0.0

replace github.com/authzed/spicedb-clients/spicedb-go => ./third_party/spicedb-clients/spicedb-go

replace github.com/authzed/spicedb-clients/proto-clients/spicedb-go-proto => ./third_party/spicedb-clients/proto-clients/spicedb-go-proto
```

**Do not run `go mod tidy` yet.** Nothing in the project imports the client at this point,
so tidy removes the `require` line you just added and every later build fails with
`module ... is replaced but not required`. Add an import first, then tidy:

1. Write (or convert) at least one file that imports the client -- the import block below.
2. `go get github.com/authzed/spicedb-clients/spicedb-go` to resolve the `require` against
   the `replace` target.
3. `go mod tidy` last, once a real import exists to anchor it.

With an importing file present, the client builds and imports normally:

```go
import (
	"github.com/authzed/spicedb-clients/spicedb-go/client"
	"github.com/authzed/spicedb-clients/spicedb-go/consistency"
	"github.com/authzed/spicedb-clients/spicedb-go/rel"
)

c, err := client.NewPlaintext("localhost:50091", "task23key")
```

**Live verification** (`spicedb serve-testing` v1.56.0, schema with `document.direct_viewer` and
`permission view = direct_viewer + group_viewer`, real `go run` output):

```
installation.md walkthrough: user:alice can view document:doc1 (before any write) = false
wrote relationship at revision: Gh8KEzE3ODY3MzkwNzkyNTk3MTMwMDASCGI4YTg1MzE5
installation.md walkthrough: user:alice can view document:doc1 (after write) = true
```

### Python

`spicedb-python/pyproject.toml` declares its proto dependency via
`[tool.uv.sources] spicedb-python-proto = { path = "../proto-clients/spicedb-python-proto" }`.
Unlike Go, `uv` honors this automatically: adding the vendored client as a local dependency
turns it into a `uv` workspace member, and `uv` resolves its nested proto path dependency
without you redeclaring anything.

```
myapp/
  pyproject.toml
  third_party/spicedb-clients/
    spicedb-python/
    proto-clients/spicedb-python-proto/
```

```
uv add "spicedb-python @ file:///path/to/myapp/third_party/spicedb-clients/spicedb-python"
uv sync
```

`uv add` rewrites your `pyproject.toml` to:

```toml
dependencies = ["spicedb-python"]

[tool.uv.workspace]
members = ["third_party/spicedb-clients/spicedb-python"]

[tool.uv.sources]
spicedb-python = { workspace = true }
```

Verified: `uv sync` resolved 24 packages including the nested proto dependency with no further
configuration, and `uv run python -c "from spicedb.client import SpiceDBClient; print(SpiceDBClient)"`
printed `<class 'spicedb.client.SpiceDBClient'>`.

If you're on plain `pip` instead of `uv`, install the proto directory first, then the client:
`pip install -e third_party/spicedb-clients/proto-clients/spicedb-python-proto -e third_party/spicedb-clients/spicedb-python`.

### TypeScript

`spicedb-typescript/package.json` declares its proto dependency as
`"@spicedb/proto": "workspace:*"` -- a **pnpm workspace protocol**, not a plain version or path.
Outside a pnpm workspace this does not resolve. Either:

- add both vendored packages to your own `pnpm-workspace.yaml` `packages:` list, or
- rewrite that one line in the vendored `package.json` to a plain relative reference so plain
  `npm`/`yarn` can resolve it too:

```diff
-    "@spicedb/proto": "workspace:*"
+    "@spicedb/proto": "file:../proto-clients/spicedb-typescript-proto"
```

Then add the client itself as a `file:` dependency in your own `package.json`:

```json
{
  "dependencies": {
    "@spicedb/client": "file:third_party/spicedb-clients/spicedb-typescript"
  }
}
```

Verified: `npm install` resolved 100 packages, and `node_modules/@spicedb/client` and
`node_modules/@spicedb/proto` both symlinked correctly to the vendored directories
(`require.resolve('@spicedb/client/package.json')` and `require.resolve('@spicedb/proto/package.json')`
both succeeded).

### C#

A plain `<ProjectReference>` to the vendored client is not enough. The .NET SDK's default
project glob compiles **every** `.cs` file under your own project's directory tree, recursively
-- including, once you've vendored `spicedb-csharp/` as a subdirectory, its own
`SpiceDB.Client.Tests/` project (which references test-only packages like `FluentAssertions`
you haven't installed) and the generated `obj/` folders under each vendored subproject
(producing `CS0579: Duplicate ... Attribute` errors from colliding `AssemblyInfo.cs` files).
Exclude the vendored tree from your own default compile items and let the
`<ProjectReference>` pull in only the already-separately-compiled client project:

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
  </PropertyGroup>

  <ItemGroup>
    <Compile Remove="third_party/**/*.cs" />
    <ProjectReference Include="third_party/spicedb-clients/spicedb-csharp/SpiceDB.Client/SpiceDB.Client.csproj" />
  </ItemGroup>
</Project>
```

The referenced `.csproj`'s own `<ProjectReference Include="../../proto-clients/spicedb-csharp-proto/SpiceDB.Proto.csproj" />`
is already relative and needs no changes, as long as `proto-clients/` stays two levels up from
`SpiceDB.Client/SpiceDB.Client.csproj` -- i.e. a sibling of `spicedb-csharp/` itself.

Verified: `dotnet build` succeeded (0 warnings, 0 errors) after adding the `<Compile Remove>`
line, and `dotnet run` printed `constructed: SpiceDB.Client.SpiceDBClient`.

### Java

`spicedb-java/settings.gradle.kts` already does
`includeBuild("../proto-clients/spicedb-java-proto")` with a dependency substitution for its own
proto sibling. Gradle composite builds honor a nested `includeBuild` transitively, so including
the whole `spicedb-java` directory as a composite build from your own `settings.gradle.kts` pulls
in the proto dependency automatically -- no separate `includeBuild` needed for the proto
directory itself:

```kotlin
// settings.gradle.kts
rootProject.name = "myapp"
include("app")
includeBuild("third_party/spicedb-clients/spicedb-java")
```

Depend on it using the vendored project's own coordinate -- its `group` (`com.authzed`) plus its
subproject name (`lib`) plus its version (`0.1.0-SNAPSHOT`), all read from
`spicedb-java/build.gradle.kts`, not the repository or directory name:

```kotlin
// app/build.gradle.kts
repositories {
    maven {
        name = "buf"
        url = uri("https://buf.build/gen/maven")
        content { includeGroup("build.buf.gen") }
    }
    mavenCentral()
}

dependencies {
    implementation("com.authzed:lib:0.1.0-SNAPSHOT")
}
```

The `repositories {}` block matters: composite builds don't share repository configuration, so
your own build needs the same repositories the vendored one declares (`mavenCentral()` plus the
`buf.build/gen/maven` repo the generated proto types are published to) or dependency resolution
fails with `Cannot resolve external dependency ... no repositories are defined`.

Verified: `gradle :app:run` built `spicedb-java-proto`, `spicedb-java:lib`, and `app` in that
order and printed `constructed: com.authzed.spicedb.SpiceDBClient`.

### Rust

Unlike Go, Cargo path dependencies declared inside a *dependency's own* `Cargo.toml` are honored
automatically by consumers -- `spicedb-rust/Cargo.toml`'s
`spicedb-proto = { path = "../proto-clients/spicedb-rust-proto" }` needs no redeclaration in
your project.

```
myapp/
  Cargo.toml
  src/main.rs
  third_party/spicedb-clients/
    spicedb-rust/
    proto-clients/spicedb-rust-proto/
```

```toml
# Cargo.toml
[dependencies]
spicedb = { path = "third_party/spicedb-clients/spicedb-rust" }
tokio = { version = "1", features = ["full"] }
```

Verified: `cargo build` resolved and compiled `spicedb-proto` and `spicedb` from the vendored
paths with no further configuration, against
`use spicedb::client::SpiceDBClient; SpiceDBClient::builder("localhost:1", "x")`.

### Ruby

`spicedb-ruby.gemspec` declares its proto dependency only as a version constraint
(`spec.add_dependency 'spicedb-proto', '~> 0.1'`) -- the `path:` override lives in
`spicedb-ruby`'s own `Gemfile`, which Bundler doesn't consult when you depend on the gem from a
different project. Declare both as `path:` gems in your own `Gemfile`, mirroring what the
vendored `Gemfile` does for itself:

```ruby
# Gemfile
source 'https://rubygems.org'

gem 'spicedb', path: 'third_party/spicedb-clients/spicedb-ruby'
gem 'spicedb-proto', path: 'third_party/spicedb-clients/proto-clients/spicedb-ruby-proto'
```

Verified: `bundle install` resolved cleanly (`Bundle complete! 2 Gemfile dependencies, 7 gems now
installed`), and `bundle exec ruby -e "require 'spicedb'; puts SpiceDB::Client.respond_to?(:new_plaintext)"`
printed `true`. (Ruby's client requires Ruby >= 3.2; if your system Ruby is older, as macOS's
bundled Ruby often is, use a newer Ruby via your version manager of choice.)

## `spicedb-gen` (typed wrappers)

`spicedb-gen` is self-contained -- it depends directly on `github.com/authzed/spicedb` (the
server module, for schema parsing), not on `proto-clients/`. Vendor `spicedb-gen/` alone, with
no sibling directory needed:

```
go build -o spicedb-gen ./cmd/spicedb-gen   # run from inside the vendored spicedb-gen/ directory
```

It supports exactly four of the seven languages -- Go, Java, Python, TypeScript. Verified live:
`./spicedb-gen --schema schema.zed --lang ruby --out out.rb` fails with
`unknown language "ruby"; registered languages: <the four above>`. (The four names are stable;
their order in the error text isn't -- it comes from Go map iteration and varies run to run, so
don't treat any one ordering as something to match against.)

> **Read `references/spicedb-gen.md`'s "Known limitations" before building on this.** The
> generator has a documented crash that a build recipe alone does not hint at: a
> **self-referential resource type** (`relation parent: folder` inside `definition folder`, an
> extremely common hierarchy shape) crashes it outright in all four languages -- `exit 2`,
> `goroutine stack exceeds 1000000000-byte limit`. That reference is the only file recording
> it, and it is not otherwise linked from this file or from `SKILL.md`. A fix is in progress
> upstream; treat the recipe above as "how to build it if you want to try it", not as a
> recommendation to depend on it.

## When the clients are published

Once the clients are published as real packages, this file is the only one that changes:

| Language | Today | Once published |
|---|---|---|
| Go | vendor + `replace` | `go get github.com/authzed/...` |
| Python | vendor + `uv add file://...` | `pip install` / `uv add` |
| TypeScript | vendor + `file:` dependency | `npm install` / `pnpm add` |
| C# | vendor + `<ProjectReference>` | `dotnet add package` |
| Java | vendor + composite build | a Maven/Gradle coordinate |
| Rust | vendor + path dependency | `cargo add` |
| Ruby | vendor + `path:` gems | `gem install` / `bundle add` |

`references/core-concepts.md` and the per-language references describe the client's behavior,
not how it's obtained, so none of them need to change when that day comes.
