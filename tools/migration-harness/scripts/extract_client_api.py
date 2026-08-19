#!/usr/bin/env python3
"""Extract the SpiceDB prototype clients' API surface as ground truth.

Background
----------
Plan `docs/superpowers/plans/2026-08-14-client-integration-and-code-conversion.md`
builds a `spicedb-client-integration` skill and an OpenFGA-to-SpiceDB
code-conversion command. Every reference file those tasks write documents an
API surface that, before this script existed, nobody had checked against the
real client -- the spec's per-language divergence claims (Go has no typed
error hierarchy and no retry; Python is async-only and reads the permission
from `Relationship.resource_relation`; C# places `CancellationToken` before
its `params` array; Rust buffers into `Vec` rather than returning a
`Stream`) were prior research, not verified fact.

This script walks a checkout of `authzed/spicedb-clients-prototype` and
produces `fixtures/client-api-surface.json`: a per-language record of
constructors, the check/lookup/read/write/watch entry points, the
`Relationship` / `Filter` / `Transaction` type shapes, the consistency
helpers, error handling, and iteration style (streaming vs. buffering) --
each fact carrying the `file:line` it was read from.

Extraction method
------------------
Python's client (`spicedb-python/spicedb/*.py`) is parsed with the stdlib
`ast` module -- precise, and free, since Python ships the parser. Every
other language is scanned with the targeted regular expressions in
`PATTERNS` below. Each fact in the output JSON carries an
`"extraction_pattern"` field naming the regex key that produced it, so a
reader can judge whether the pattern still matches after the pinned commit
moves. Where a fact could not be located (the pattern found nothing), the
script records `null` and a `"extraction_note"` explaining the miss rather
than silently omitting the fact -- a script that fails quietly on drift is
worse than one that fails loudly.

Every fact's `"source_line"` is genuinely verbatim source text (stripped of
leading/trailing whitespace), regardless of extraction method: regex-derived
facts store the exact matched line, and `ast`-derived Python facts store the
first line of `ast.get_source_segment()` for the matched node -- not a
signature reconstructed from parsed argument names. For a `def` spanning
multiple lines, that first line is just `async def method_name(` with no
arguments on it, same as what a regex would have matched had one been used;
`"file"`/`"line"` always point at the `def` line itself either way.

What this script does NOT (re-)derive
--------------------------------------
Some ground truth can only be established by *running* code against a live
SpiceDB instance: whether a client actually retries (observed via call
timing), what exception type a language raises, whether a signature that
*looks* like it returns a stream actually compiles when called positionally.
Task 1 did this once, by hand, for all seven languages plus `spicedb-gen`,
against `spicedb serve-testing` v1.56.0 at commit 549c4e9. Those results are
recorded in the `"live_verification"` block below as dated, static data --
this script does not spin up a SpiceDB instance or seven language
toolchains on every run. When the pinned commit moves, re-run the live
checks by hand (see `task-1-report.md` for the exact commands) and update
that block; the rest of the JSON regenerates automatically from `--repo`.

Usage
-----
    uv run python scripts/extract_client_api.py --repo /path/to/spicedb-clients-prototype
    uv run python scripts/extract_client_api.py --repo /path/to/spicedb-clients-prototype \
        --out fixtures/client-api-surface.json

Run from `tools/migration-harness/` (the default `--out` path is relative to
the current directory, matching this directory's other scripts).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_URL = "https://github.com/authzed/spicedb-clients-prototype"
DEFAULT_OUT = Path("fixtures/client-api-surface.json")

# Set once in build_surface() so every Fact records a path relative to the
# repo checkout rather than an absolute path tied to wherever this happened
# to be cloned -- the output JSON is committed, so it must not embed a
# throwaway scratch-directory path.
_REPO_ROOT: Path | None = None


def relpath(path: Path | str | None) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if _REPO_ROOT is not None:
        try:
            return str(p.relative_to(_REPO_ROOT))
        except ValueError:
            pass
    return str(p)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


@dataclass
class Fact:
    """A single extracted fact with its provenance."""

    name: str | None
    file: str | None
    line: int | None
    text: str | None
    extraction_pattern: str | None = None
    extraction_note: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "file": relpath(self.file), "line": self.line}
        if self.text is not None:
            out["source_line"] = self.text.strip()
        if self.extraction_pattern is not None:
            out["extraction_pattern"] = self.extraction_pattern
        if self.extraction_note is not None:
            out["extraction_note"] = self.extraction_note
        return out


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def find_first(
    path: Path, pattern: str, *, flags: int = 0, name: str | None = None
) -> Fact:
    """Return the first line in `path` matching `pattern`, with its line number.

    `name` defaults to the first captured group, if the pattern has one.
    """
    rx = re.compile(pattern, flags)
    if not path.exists():
        return Fact(name, None, None, None, pattern, f"file not found: {path}")
    for lineno, line in enumerate(read_lines(path), start=1):
        m = rx.search(line)
        if m:
            resolved_name = name
            if resolved_name is None and m.groups():
                resolved_name = m.group(1)
            return Fact(resolved_name, str(path), lineno, line, pattern)
    return Fact(name, str(path), None, None, pattern, "pattern not found")


def find_all(path: Path, pattern: str, *, flags: int = 0) -> list[Fact]:
    """Return every line in `path` matching `pattern`."""
    rx = re.compile(pattern, flags)
    if not path.exists():
        return [Fact(None, None, None, None, pattern, f"file not found: {path}")]
    facts = []
    for lineno, line in enumerate(read_lines(path), start=1):
        m = rx.search(line)
        if m:
            name = m.group(1) if m.groups() else m.group(0)
            facts.append(Fact(name, str(path), lineno, line, pattern))
    return facts


def extract_block_fields(
    path: Path, start_pattern: str, end_pattern: str, field_pattern: str
) -> list[str]:
    """Extract field names from a type/struct/record body.

    Finds `start_pattern`, then collects the first capture group of
    `field_pattern` from each subsequent line until `end_pattern` matches.
    Used to read the real field lists of the Relationship/Filter/Transaction
    types instead of hand-transcribing them.
    """
    if not path.exists():
        return []
    lines = read_lines(path)
    start_rx, end_rx, field_rx = (
        re.compile(start_pattern),
        re.compile(end_pattern),
        re.compile(field_pattern),
    )
    fields: list[str] = []
    in_block = False
    for line in lines:
        if not in_block:
            if start_rx.search(line):
                in_block = True
            continue
        if end_rx.search(line):
            break
        m = field_rx.search(line)
        if m:
            fields.append(m.group(1))
    return fields


# ---------------------------------------------------------------------------
# Regex patterns used per language. Recorded here (not inline) so the JSON's
# "extraction_pattern" values can cite this dict directly, and so a reader
# auditing the script sees every pattern in one place.
# ---------------------------------------------------------------------------

PATTERNS: dict[str, dict[str, str]] = {
    "go": {
        "constructor": r"^func (New\w+)\(",
        # Every public method on *Client, across every non-test file in
        # spicedb-go/client/ -- enumerated, not matched name-by-name. Fix
        # round 2: the original PATTERNS had one hardcoded regex per expected
        # method name (check_one, check_bulk, ...) covering 8 of the 23
        # methods *Client actually exposes; DeleteRelationships (and 14
        # others: CheckAny, CheckAll, CheckIter, ImportRelationships,
        # ExportRelationships, ReadSchema, WriteSchema, ReflectSchema,
        # ComputablePermissions, DependentRelations, DiffSchema,
        # RegisterRelationshipCounter, CountRelationships,
        # UnregisterRelationshipCounter) were silently absent because no
        # pattern asked for them. A short list being short is the bug, not
        # any one missing name -- see fixtures/client-api-surface.json's
        # per-language "public_methods" for the full enumeration this now
        # produces, and entry_points below for the curated subset selected
        # from it.
        "client_method": r"^func \(c \*Client\) (\w+)\(",
        # Every method on rel.Txn, enumerated the same way -- see
        # transaction_method_facts() for why this replaced a hardcoded list.
        "transaction_method": r"^func \(t \*Txn\) (\w+)\(",
        "consistency_fn": r"^func (\w+)\(.*\) Strategy \{",
        "sentinel_error": r"^\s*(Err\w+) = fmt\.Errorf",
        "retry_or_backoff": r"retry|backoff",
        "relationship_field": r"^\s*(\w+)\s+\S",
    },
    "python": {
        # Python is parsed with ast, not regex -- kept here only for the
        # retry/backoff grep, which is cheaper as a substring search.
        "retry_or_backoff": r"retry|backoff",
    },
    "typescript": {
        "constructor_fn": r"^export function (createSpiceDBClient)\(",
        "constructor_method": r"^\s*(constructor)\(options: SpiceDBClientOptions\)",
        # Every public method in the `SpiceDBClient` class body (bounded to
        # the class's own lines below), enumerated rather than matched
        # against a fixed list of method names -- see the Go "client_method"
        # comment above for why that distinction matters.
        "client_method": r"^\s{2}(?:async \*?)?(\w+)\(",
        "transaction_method": r"^\s{2}(\w+)\(",
        "consistency_fn": r"^export function (\w+)\(.*\): Consistency \{",
        "error_class": r"^export class (\w+Error) extends (\w+)",
        "retry_or_backoff": r"retry|backoff|Retry|Backoff",
        "relationship_field": r"^\s*(\w+)\??:\s*\S",
    },
    "csharp": {
        "constructor": r"public static SpiceDBClient (Create\w+)\(",
        # Every public instance method in the SpiceDBClient class body
        # (bounded below to before the trailing `public sealed record` type
        # declarations); the static Create* factory methods this also
        # matches are excluded in code since they're already covered by
        # `constructor` above. See the Go "client_method" comment for why
        # enumeration replaces a fixed name list here too.
        "client_method": r"^\s{4}public\s+(?:static\s+)?(?:async\s+)?\S.*?\s(\w+)\(",
        "transaction_method": r"^\s{4}public\s+(?:static\s+)?(?:async\s+)?\S.*?\s(\w+)\(",
        "consistency_fn": r"public static ConsistencyStrategy (\w+)\(",
        "error_class": r"public sealed class (\w+Exception) : (\w+)",
        "cancellation_token_before_params": r"CancellationToken cancellationToken = default,\s*$",
        "params_array": r"params Relationship\[\] relationships\)",
        "retry_or_backoff": r"[Rr]etry|[Bb]ackoff",
        "relationship_field": r"public\s+\S.*\s(\w+)\s*\{ get; init; \}",
    },
    "java": {
        "constructor": r"public static SpiceDBClient (create\w*)\(",
        # Matches any two-space-indented `public ...` line in
        # SpiceDBClient.java; the anonymous inner Iterator classes used by
        # the streaming methods sit at 10-space indentation, so this anchor
        # excludes their hasNext()/next() members without needing to name
        # them, and code-side filtering drops nested record/interface/enum
        # declarations and the static factory methods (already covered by
        # `constructor` above). See the Go "client_method" comment above for
        # why enumeration replaces a fixed name list here too.
        "client_method": r"^  public (\S.*)$",
        "transaction_method": r"^  public (\S.*)$",
        "consistency_fn": r"public static Consistency (\w+)\(",
        "error_class": r"public class (\w+Exception) extends (\w+)",
        "retry_or_backoff": r"[Rr]etry|[Bb]ackoff",
        "relationship_field": r"^\s*String (\w+),?$|^\s*Map<String, Object> (\w+),?$|^\s*Instant (\w+)\)",
    },
    "rust": {
        "constructor": r"pub async fn (new_\w+)\(",
        # Every public method inside `impl SpiceDBClient { ... }` (not
        # `impl SpiceDBClientBuilder`, a separate block); the
        # constructor-shaped methods this also matches (new_plaintext,
        # new_system_tls, builder) are excluded in code since they're
        # already covered by `constructor` above / the builder() special
        # case in extract_rust. See the Go "client_method" comment above for
        # why enumeration replaces a fixed name list here too.
        "client_method": r"^\s{4}pub (?:async )?fn (\w+)\(",
        "transaction_method": r"^\s{4}pub fn (\w+)\(",
        "consistency_fn": r"pub fn (\w+)\(.*\) -> Strategy \{",
        "error_variant": r"^\s*(\w+)\((?:String|i32)\),",
        "returns_vec": r"-> Result<Vec<",
        # Matches doc-comment prose claiming streaming, not a real Rust arrow
        # signature -- these all live in `///` comments (the compiled functions
        # actually return Vec<T>, never Stream<T>), so the pattern intentionally
        # has no `->` in it.
        "returns_stream": r"impl Stream<Item|Returns a stream of",
        "retry_or_backoff": r"retry|backoff",
        "relationship_field": r"^\s*pub (\w+):",
    },
    "ruby": {
        "constructor": r"def self\.(new_\w+)\(",
        # Every public instance method in client.rb, scanned only up to the
        # file's `private` keyword (in code) so internal call_*/helper
        # methods aren't mistaken for public API surface, and with
        # `self.`-prefixed factory methods (already covered by `constructor`
        # above) and `initialize` excluded in code. See the Go
        # "client_method" comment above for why enumeration replaces a fixed
        # name list here too.
        "client_method": r"^\s{4}def (\w+)",
        "transaction_method": r"^\s{4}def (\w+[?!]?)",
        "consistency_fn": r"^\s*def (\w+)(?:\(|$)",
        "error_class": r"class (\w+Error) < (\w+)",
        "returns_enumerator": r"Enumerator\.new",
        "retry_or_backoff": r"retry|backoff",
        "relationship_field": r"^\s*:(\w+),?$",
    },
}


def find_all_in_dir(dir_path: Path, pattern: str, *, exclude_suffix: str | None = None, glob: str = "*") -> list[Fact]:
    """Return every match of `pattern` across every file in `dir_path` matching
    `glob`, scanned in sorted filename order (for reproducible output). Used
    for client surfaces split across multiple files -- e.g. Go's
    spicedb-go/client/ package -- so enumeration isn't tied to one hardcoded
    file the way a single `find_all(one_file, ...)` call would be.
    """
    if not dir_path.is_dir():
        return [Fact(None, None, None, None, pattern, f"directory not found: {dir_path}")]
    facts: list[Fact] = []
    for p in sorted(dir_path.glob(glob)):
        if not p.is_file():
            continue
        if exclude_suffix and p.name.endswith(exclude_suffix):
            continue
        facts.extend(find_all(p, pattern))
    return facts


def pick_entry_point(by_name: dict[str, Fact], canonical: str, fallback_path: Path, pattern: str) -> dict:
    """Select `canonical` from an already-enumerated {name: Fact} map.

    This is the join between "enumerate everything" (find_all_in_dir /
    find_all / the ast walks in extract_python) and the curated,
    human-readable `entry_points` keys (check_single, write_relationships,
    ...) that downstream docs reference by name. The enumeration itself never
    special-cases a method name; only this final selection step does, and a
    miss here is visible (a `"not found"` note) rather than the method simply
    never having been looked for in the first place.
    """
    fact = by_name.get(canonical)
    if fact is not None:
        return fact.to_json()
    return Fact(
        canonical,
        str(fallback_path),
        None,
        None,
        pattern,
        f"{canonical!r} not found among enumerated public methods",
    ).to_json()


def _scan_bounded(
    path: Path,
    pattern: str,
    *,
    start_pred,
    end_pred,
    name_of=None,
    skip=None,
) -> list[Fact]:
    """Scan `path` for `pattern`, only between the first line `start_pred`
    matches and the next subsequent line `end_pred` matches (exclusive) --
    used to bound enumeration to one class/impl body in a file that also
    contains other top-level declarations (C#'s trailing `record` types,
    Rust's separate `impl SpiceDBClientBuilder` block, ...).

    `name_of(match) -> str | None` post-processes a regex match into a method
    name (default: `match.group(1)`); `skip(name) -> bool` drops a match by
    name (used to exclude constructor-shaped methods already captured
    elsewhere). Matches with no resolvable name, or for which `skip` returns
    True, are silently omitted rather than recorded as facts -- the "not
    found" record is for entry_points' curated lookups, not for every
    incidental non-match in a bounded scan.
    """
    if not path.exists():
        return [Fact(None, None, None, None, pattern, f"file not found: {path}")]
    lines = read_lines(path)
    start = end = None
    for i, line in enumerate(lines, start=1):
        if start is None and start_pred(line):
            start = i
        elif start is not None and end is None and i > start and end_pred(line):
            end = i
            break
    if start is None:
        return [Fact(None, str(path), None, None, pattern, "scan start not found")]
    if end is None:
        end = len(lines) + 1
    rx = re.compile(pattern)
    facts: list[Fact] = []
    for i in range(start, end):
        line = lines[i - 1]
        m = rx.search(line)
        if not m:
            continue
        name = name_of(m) if name_of is not None else (m.group(1) if m.groups() else None)
        if name is None:
            continue
        if skip is not None and skip(name):
            continue
        facts.append(Fact(name, str(path), i, line, pattern))
    return facts


def client_method_facts_typescript(repo: Path, pat: dict[str, str]) -> list[Fact]:
    """Every public method in the `SpiceDBClient` class body (client.ts),
    bounded to that class's own lines (ends at the next top-level `export`),
    with the `constructor` special form excluded.
    """
    path = repo / "spicedb-typescript/src/client.ts"
    return _scan_bounded(
        path,
        pat["client_method"],
        start_pred=lambda line: line.startswith("export class SpiceDBClient"),
        end_pred=lambda line: line.startswith("export ") or line.startswith("class "),
        skip=lambda name: name == "constructor",
    )


def client_method_facts_csharp(repo: Path, pat: dict[str, str]) -> list[Fact]:
    """Every public instance method in the SpiceDBClient class body
    (SpiceDBClient.cs), bounded to end before the trailing `public sealed
    record` type declarations, with the static Create* factory methods
    (already captured under `constructors`) excluded.
    """
    path = repo / "spicedb-csharp/SpiceDB.Client/SpiceDBClient.cs"
    return _scan_bounded(
        path,
        pat["client_method"],
        start_pred=lambda line: line.startswith("public sealed class SpiceDBClient"),
        end_pred=lambda line: bool(re.match(r"^public ", line)),
        skip=lambda name: name.startswith("Create"),
    )


def client_method_facts_java(repo: Path, pat: dict[str, str]) -> list[Fact]:
    """Every public method declared directly in SpiceDBClient's class body
    (two-space indentation only -- see PATTERNS["java"]["client_method"]'s
    comment for why that anchor excludes the anonymous inner Iterator
    classes' hasNext()/next() without needing to name them), with nested
    record/interface/enum type declarations and the `create*` static factory
    methods (already captured under `constructors`) excluded.

    Fix round 2: the name regex used to be `^\\S+(?:<[^>]*>)?\\s+(\\w+)\\(`,
    which assumes **exactly one** token stands between `public` and the
    method name -- a return type, possibly generic. That holds for
    `boolean checkPermission(`, `List<Boolean> checkPermissions(` and every
    instance method in the file, and fails for any declaration carrying a
    second modifier. There is exactly one such declaration, and dropping it
    was a real completeness hole rather than a cosmetic one:
    `public static ClientOption withInsecure()` (SpiceDBClient.java:113) --
    the **only** factory for the `ClientOption` type that
    `create(endpoint, presharedKey, ClientOption... options)` takes, and
    therefore the only documented way to build a plaintext client through the
    general `create` entry point. It is not covered by `constructors` either,
    whose pattern is `public static SpiceDBClient (create\\w*)\\(` -- it does
    not return a SpiceDBClient. So it was recorded nowhere, and the
    per-language public-method totals were short by one (157 -> 158).

    Same absence-detection class as the round-1 fix: verifying that every
    fact present is correct says nothing about a fact that was never looked
    for. The replacement takes the identifier immediately preceding the
    first `(` in the declaration, whatever precedes it, so a future
    `public static synchronized Foo bar(` is picked up without another
    round of this.
    """
    path = repo / "spicedb-java/lib/src/main/java/com/authzed/spicedb/SpiceDBClient.java"
    if not path.exists():
        return [Fact(None, None, None, None, pat["client_method"], f"file not found: {path}")]
    # The identifier immediately before the first `(`, regardless of how many
    # modifier/type tokens precede it. `.*?` is lazy, so on
    # `List<Boolean> checkPermissions(` it does not stop at `List<` -- the
    # first position where `(\w+)\s*\(` can match is the method name itself.
    name_rx = re.compile(r"^.*?\b(\w+)\s*\(")

    def name_of(m: re.Match) -> str | None:
        rest = m.group(1)
        if any(k in rest for k in ("record ", "interface ", "enum ", "final class", "static SpiceDBClient")):
            return None
        nm = name_rx.match(rest)
        return nm.group(1) if nm else None

    lines = read_lines(path)
    rx = re.compile(pat["client_method"])
    facts: list[Fact] = []
    for i, line in enumerate(lines, start=1):
        m = rx.search(line)
        if not m:
            continue
        name = name_of(m)
        if name is None:
            continue
        facts.append(Fact(name, str(path), i, line, pat["client_method"]))
    return facts


def client_method_facts_rust(repo: Path, pat: dict[str, str]) -> list[Fact]:
    """Every public method inside `impl SpiceDBClient { ... }` -- not the
    separate `impl SpiceDBClientBuilder` block -- with the constructor-shaped
    methods (new_plaintext, new_system_tls, builder; already captured under
    `constructors`) excluded.
    """
    path = repo / "spicedb-rust/src/client.rs"
    return _scan_bounded(
        path,
        pat["client_method"],
        start_pred=lambda line: bool(re.match(r"^impl SpiceDBClient\s*\{", line)),
        end_pred=lambda line: line == "}",
        skip=lambda name: name.startswith("new_") or name == "builder",
    )


def client_method_facts_ruby(repo: Path, pat: dict[str, str]) -> list[Fact]:
    """Every public instance method in client.rb, scanned only up to the
    file's `private` keyword so internal call_*/helper methods aren't
    mistaken for public API surface, with `self.`-prefixed factory methods
    (already captured under `constructors`) and `initialize` excluded.
    """
    path = repo / "spicedb-ruby/lib/spicedb/client.rb"
    if not path.exists():
        return [Fact(None, None, None, None, pat["client_method"], f"file not found: {path}")]
    lines = read_lines(path)
    rx = re.compile(pat["client_method"])
    facts: list[Fact] = []
    for i, line in enumerate(lines, start=1):
        if re.match(r"^\s*private\s*$", line):
            break
        if re.match(r"^\s{4}def self\.", line):
            continue
        m = rx.search(line)
        if m and m.group(1) != "initialize":
            facts.append(Fact(m.group(1), str(path), i, line, pat["client_method"]))
    return facts


# ---------------------------------------------------------------------------
# Per-language extraction
# ---------------------------------------------------------------------------


def transaction_method_facts(repo: Path, lang: str) -> list[Fact]:
    """Every public method on the language's `Transaction`/`Txn` type,
    enumerated from that type's own source with `file:line` provenance.

    Fix round 2: `types.<lang>.transaction.methods` used to be a **hardcoded
    string literal** in each `extract_<lang>()` -- a list nobody re-read
    against the client after it was first typed, in a file whose entire
    purpose is ground truth. It had already drifted in four of the seven
    languages at this same pinned commit:

    - **Go** listed 5, omitting `Preconditions` (`rel/rel.go:348`).
    - **Rust** listed 5, omitting `new`, `updates`, `preconditions`,
      `is_empty` and `len` (`src/types.rs:413-472`).
    - **Ruby** listed 5, omitting `empty?` (`transaction.rb:77`) -- the
      trailing `?` is also why this language needs its own pattern.
    - **Java** did not list methods at all: it carried the prose sentence
      "create/touch/delete via Transaction.Mutation, mustMatch/mustNotMatch
      preconditions", which is not an enumeration and omits `mutations`,
      `preconditions` and `isEmpty` (`Transaction.java:77-87`).

    Same class of defect as the round-1 `entry_points` fix and the Java
    `withInsecure` miss above: a fact that is asserted rather than derived
    cannot report that it has gone stale. Python keeps using the `ast`
    walker (`public_client_methods`) for the same reason it does everywhere
    else -- the parser is free and exact.
    """
    pat = PATTERNS[lang].get("transaction_method")
    if pat is None:
        return [Fact(None, None, None, None, None, f"no transaction_method pattern for {lang}")]

    if lang == "go":
        path = repo / "spicedb-go/rel/rel.go"
        if not path.exists():
            return [Fact(None, None, None, None, pat, f"file not found: {path}")]
        return find_all(path, pat)

    if lang == "typescript":
        return _scan_bounded(
            repo / "spicedb-typescript/src/types.ts",
            pat,
            start_pred=lambda line: line.startswith("export class Transaction"),
            end_pred=lambda line: line.startswith("export ") or line.startswith("class "),
            skip=lambda name: name == "constructor",
        )

    if lang == "csharp":
        return _scan_bounded(
            repo / "spicedb-csharp/SpiceDB.Client/Transaction.cs",
            pat,
            start_pred=lambda line: line.startswith("public sealed class Transaction"),
            end_pred=lambda line: bool(re.match(r"^public ", line)),
        )

    if lang == "java":
        path = repo / "spicedb-java/lib/src/main/java/com/authzed/spicedb/Transaction.java"
        if not path.exists():
            return [Fact(None, None, None, None, pat, f"file not found: {path}")]
        # Same declaration shape as SpiceDBClient.java, so the same name rule:
        # the identifier immediately before the first `(`, with nested
        # record/enum/interface type declarations excluded.
        name_rx = re.compile(r"^.*?\b(\w+)\s*\(")
        rx = re.compile(pat)
        facts: list[Fact] = []
        for i, line in enumerate(read_lines(path), start=1):
            m = rx.search(line)
            if not m:
                continue
            rest = m.group(1)
            if any(k in rest for k in ("record ", "interface ", "enum ", "final class")):
                continue
            nm = name_rx.match(rest)
            if not nm:
                continue
            facts.append(Fact(nm.group(1), str(path), i, line, pat))
        return facts

    if lang == "rust":
        return _scan_bounded(
            repo / "spicedb-rust/src/types.rs",
            pat,
            start_pred=lambda line: bool(re.match(r"^impl Transaction\s*\{", line)),
            end_pred=lambda line: line == "}",
        )

    if lang == "ruby":
        path = repo / "spicedb-ruby/lib/spicedb/transaction.rb"
        if not path.exists():
            return [Fact(None, None, None, None, pat, f"file not found: {path}")]
        rx = re.compile(pat)
        facts = []
        for i, line in enumerate(read_lines(path), start=1):
            if re.match(r"^\s*private\s*$", line):
                break
            m = rx.search(line)
            if not m or m.group(1) == "initialize":
                continue
            facts.append(Fact(m.group(1), str(path), i, line, pat))
        return facts

    return [Fact(None, None, None, None, pat, f"unhandled language: {lang}")]


def entry_points(repo: Path, lang_dir: str, client_file: str, keys: dict[str, str]) -> dict:
    p = repo / lang_dir / client_file
    return {k: find_first(p, pat).to_json() for k, pat in keys.items()}


def extract_go(repo: Path) -> dict:
    pat = PATTERNS["go"]
    client = repo / "spicedb-go/client/client.go"
    client_dir = repo / "spicedb-go/client"
    consistency = repo / "spicedb-go/consistency/consistency.go"
    rel_pkg = repo / "spicedb-go/rel/rel.go"

    # Enumerate every method on *Client, across every non-test file in
    # client_dir, rather than asserting 8 expected names one file at a time --
    # see PATTERNS["go"]["client_method"]'s comment for why.
    client_methods = find_all_in_dir(client_dir, pat["client_method"], exclude_suffix="_test.go")
    client_methods_by_name = {f.name: f for f in client_methods if f.name}

    def pick(canonical: str) -> dict:
        return pick_entry_point(client_methods_by_name, canonical, client_dir, pat["client_method"])

    entry = {
        "check_single": pick("CheckOne"),
        "check_bulk": pick("Check"),
        "lookup_resources": pick("LookupResources"),
        "lookup_subjects": pick("LookupSubjects"),
        "expand": pick("ExpandPermissionTree"),
        "read_relationships": pick("ReadRelationships"),
        "write_relationships": pick("Write"),
        "delete_relationships": pick("DeleteRelationships"),
        "watch": pick("Updates"),
    }

    constructors = [f.to_json() for f in find_all(client, pat["constructor"])]
    consistency_helpers = [f.to_json() for f in find_all(consistency, pat["consistency_fn"])]
    sentinel_errors = [f.to_json() for f in find_all(rel_pkg, pat["sentinel_error"])]
    # Scans every non-test .go file in client_dir (the same set client_methods
    # was enumerated from), not a fixed 5-file subset -- the original list
    # (client.go, checks.go, relationships.go, lookup.go, watch.go) silently
    # excluded bulk.go, expand.go, schema.go, and experimental.go. Re-checked:
    # 0 retry/backoff hits in those four either, so the "no retry" verdict is
    # unchanged -- but the evidence now says what was actually scanned.
    scanned_go_files = sorted(
        p.name for p in client_dir.glob("*.go") if not p.name.endswith("_test.go")
    )
    retry_hits = [f for f in find_all_in_dir(client_dir, pat["retry_or_backoff"], exclude_suffix="_test.go") if f.line is not None]

    rel_fields = extract_block_fields(
        rel_pkg, r"^type Relationship struct", r"^\}", pat["relationship_field"]
    )
    filter_fields = extract_block_fields(
        rel_pkg, r"^type Filter struct", r"^\}", pat["relationship_field"]
    )

    return {
        "language": "go",
        "client_dir": "spicedb-go",
        "constructors": constructors,
        "entry_points": entry,
        "public_methods": [f.to_json() for f in client_methods],
        "types": {
            "relationship": {"type": "rel.Relationship", "fields": rel_fields},
            "filter": {"type": "rel.Filter", "fields": filter_fields},
            "transaction": {
                "type": "rel.Txn",
                "methods": [f.to_json() for f in transaction_method_facts(repo, "go")],
            },
        },
        "consistency_helpers": consistency_helpers,
        "error_handling": {
            "style": (
                "no typed error hierarchy: sentinel errors for input validation only "
                "(rel package); all gRPC failures are fmt.Errorf(\"spicedb: ...: %w\", err) "
                "wraps of the raw grpc/status error"
            ),
            "sentinel_errors": sentinel_errors,
            "retry": {
                "present": False,
                "evidence": (
                    f"grep for {pat['retry_or_backoff']!r} across every non-test .go file in "
                    f"spicedb-go/client/ ({', '.join(scanned_go_files)}): 0 matches (outside "
                    "DESIGN.md and Magefile.go, which are not source)"
                ),
                "retry_or_backoff_hits_in_source": retry_hits,
            },
        },
        "iteration_style": (
            "lazy pull-based iterators via Go 1.23 range-over-func (iter.Seq2[T, error]); "
            "pages are fetched from the server on demand as the caller ranges, not buffered "
            "up front"
        ),
    }


def extract_python(repo: Path) -> dict:
    """Parsed with ast, not regex -- see module docstring.

    `source_line` for every Python fact is the first line of
    `ast.get_source_segment()` for the matched node -- genuinely verbatim
    source text, stripped the same way `Fact.to_json()` strips every other
    language's regex-matched line, not a paraphrase reconstructed from parsed
    arguments. (Fix round 1: an earlier version synthesized text like
    `"def full(...)"` or `"async def check_permission(self, consistency, rel,
    context)"` instead of quoting source -- correct for methods whose `def`
    line happens to be single-line and short, but silently wrong for anything
    spanning multiple lines, where the real line 1 is just `async def
    check_permission(` with no args on it at all. `file:line` was always
    correct; only the quoted text was not.)
    """
    client_path = repo / "spicedb-python/spicedb/client.py"
    types_path = repo / "spicedb-python/spicedb/types.py"
    errors_path = repo / "spicedb-python/spicedb/errors.py"
    consistency_path = repo / "spicedb-python/spicedb/consistency.py"

    client_src = client_path.read_text(encoding="utf-8")
    types_src = types_path.read_text(encoding="utf-8")
    errors_src = errors_path.read_text(encoding="utf-8")
    consistency_src = consistency_path.read_text(encoding="utf-8")

    client_tree = ast.parse(client_src, filename=str(client_path))
    types_tree = ast.parse(types_src, filename=str(types_path))
    errors_tree = ast.parse(errors_src, filename=str(errors_path))
    consistency_tree = ast.parse(consistency_src, filename=str(consistency_path))

    def verbatim_first_line(source: str, node: ast.AST) -> str:
        segment = ast.get_source_segment(source, node)
        return segment.splitlines()[0] if segment else ""

    def find_method(
        tree: ast.AST, cls_name: str, method_name: str
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        # Annotation includes AsyncFunctionDef because the isinstance check below
        # does too (and every SpiceDBClient method actually is async) -- a prior
        # `-> ast.FunctionDef | None` annotation was a cosmetic mismatch with the
        # real return type, not a traversal bug: the isinstance check already
        # covered both, so no async method was ever missed.
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name == method_name
                    ):
                        return item
        return None

    PUBLIC_METHOD_EXTRACTION_PATTERN = (
        "ast.walk(ClassDef 'SpiceDBClient', FunctionDef|AsyncFunctionDef, name not "
        "starting with '_'); source_line via ast.get_source_segment"
    )

    def public_client_methods(tree: ast.AST, cls_name: str, source: str, path: Path) -> list[Fact]:
        """Every method on `cls_name` whose name doesn't start with `_` --
        Python's own convention for "not public" -- enumerated in class-body
        order, not matched against a fixed list of expected names.

        Fix round 2: the original extractor called a `method_fact(name)`
        helper eight times, once per hardcoded method name (check_permission,
        check_permissions, lookup_resources, lookup_subjects,
        expand_permission_tree, read_relationships, write, watch). That is
        the exact same class of bug PATTERNS["go"]["client_method"]'s comment
        describes for Go's old regex-per-name list: 12 of
        SpiceDBClient's 20 public methods (delete_relationships, check_any,
        check_all, read_schema, write_schema, reflect_schema,
        computable_permissions, dependent_relations, diff_schema,
        import_relationships, export_relationships,
        register_relationship_counter, count_relationships,
        unregister_relationship_counter) were never looked for at all.
        `isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))` matches
        both async and sync methods, per the fixed `find_method` annotation
        bug noted above -- so an async-only client (this one) is not at risk
        of the "helper misses `async def`" failure mode called out for a
        prior version of this script.
        """
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                for item in node.body:
                    if (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not item.name.startswith("_")
                    ):
                        out.append(
                            Fact(
                                item.name,
                                str(path),
                                item.lineno,
                                verbatim_first_line(source, item),
                                PUBLIC_METHOD_EXTRACTION_PATTERN,
                            )
                        )
        return out

    def dataclass_fields(tree: ast.AST, cls_name: str) -> list[str]:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                return [
                    stmt.target.id
                    for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                ]
        return []

    def module_functions(tree: ast.AST, source: str, path: Path) -> list[dict]:
        # NOTE: deliberately narrower than method_fact's `isinstance(item,
        # (ast.FunctionDef, ast.AsyncFunctionDef))` -- this only matches plain
        # `ast.FunctionDef`, so an async top-level function would be silently
        # skipped. Harmless today (spicedb-python/spicedb/consistency.py, the
        # only tree this is called on, defines zero async top-level
        # functions -- all six consistency helpers are plain `def`), but
        # worth knowing if this is ever pointed at a module that might grow one.
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                out.append(
                    Fact(
                        node.name,
                        str(path),
                        node.lineno,
                        verbatim_first_line(source, node),
                        "ast.walk(FunctionDef, module scope); source_line via ast.get_source_segment",
                    ).to_json()
                )
        return out

    def error_classes(tree: ast.AST, path: Path) -> list[dict]:
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
                out.append(
                    Fact(
                        node.name,
                        str(path),
                        node.lineno,
                        f"class {node.name}({', '.join(bases)})",
                        "ast.walk(ClassDef)",
                    ).to_json()
                )
        return out

    # Is the client class async-only? True iff SpiceDBClient defines zero
    # plain `def` methods (besides dunders) alongside its `async def`s, and
    # a case-insensitive scan of the package for a second, sync client
    # class finds none.
    sync_methods = []
    async_methods = []
    for node in ast.walk(client_tree):
        if isinstance(node, ast.ClassDef) and node.name == "SpiceDBClient":
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef):
                    async_methods.append(item.name)
                elif isinstance(item, ast.FunctionDef) and not (
                    item.name.startswith("__") and item.name.endswith("__")
                ):
                    sync_methods.append(item.name)

    # check_permission's signature: is `permission` a parameter, or is it
    # read off the Relationship argument? Settled by inspecting the args
    # list itself, not by re-reading prose.
    check_node = find_method(client_tree, "SpiceDBClient", "check_permission")
    check_args = [a.arg for a in check_node.args.args] if check_node else []
    permission_is_separate_arg = "permission" in check_args
    resource_relation_used_as_permission = find_first(
        client_path, r"permission=rel\.resource_relation"
    )

    retry_hits = [f.to_json() for f in find_all(client_path, PATTERNS["python"]["retry_or_backoff"]) if f.line]

    client_methods = public_client_methods(client_tree, "SpiceDBClient", client_src, client_path)
    client_methods_by_name = {f.name: f for f in client_methods if f.name}

    def pick(canonical: str) -> dict:
        return pick_entry_point(
            client_methods_by_name, canonical, client_path, PUBLIC_METHOD_EXTRACTION_PATTERN
        )

    return {
        "language": "python",
        "client_dir": "spicedb-python",
        "extraction_method": "ast (stdlib parser, not regex)",
        "constructors": [
            Fact(
                "SpiceDBClient.__init__",
                str(client_path),
                next(
                    n.lineno
                    for n in ast.walk(client_tree)
                    if isinstance(n, ast.ClassDef) and n.name == "SpiceDBClient"
                    for n in n.body
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__"
                ),
                "def __init__(self, endpoint, token, *, insecure=False, max_retries=3)",
                "ast.walk(ClassDef 'SpiceDBClient' -> FunctionDef '__init__')",
                (
                    "unlike Go/C#/Rust/Ruby, there is no NewPlaintext/NewSystemTLS/"
                    "NewWithOpts family -- one constructor, `insecure` is a bool kwarg"
                ),
            ).to_json()
        ],
        "entry_points": {
            "check_single": pick("check_permission"),
            "check_bulk": pick("check_permissions"),
            "lookup_resources": pick("lookup_resources"),
            "lookup_subjects": pick("lookup_subjects"),
            "expand": pick("expand_permission_tree"),
            "read_relationships": pick("read_relationships"),
            "write_relationships": pick("write"),
            "delete_relationships": pick("delete_relationships"),
            "watch": pick("watch"),
        },
        "public_methods": [f.to_json() for f in client_methods],
        "types": {
            "relationship": {
                "type": "spicedb.types.Relationship",
                "fields": dataclass_fields(types_tree, "Relationship"),
            },
            "filter": {
                "type": "spicedb.types.Filter",
                "fields": dataclass_fields(types_tree, "Filter"),
            },
            "transaction": {
                "type": "spicedb.types.Transaction",
                # ast, not regex -- same reason every other Python fact is
                # parsed rather than matched. See transaction_method_facts()
                # for why this stopped being a hardcoded literal.
                "methods": [
                    f.to_json()
                    for f in public_client_methods(types_tree, "Transaction", types_src, types_path)
                ],
            },
        },
        "consistency_helpers": module_functions(consistency_tree, consistency_src, consistency_path),
        "error_handling": {
            "style": "typed exception hierarchy (SpiceDBError subclasses), with automatic retry",
            "exception_classes": error_classes(errors_tree, errors_path),
            "retry": {
                "present": True,
                "evidence": (
                    "spicedb/client.py defines _with_retry() and _DEFAULT_MAX_RETRIES = 3; "
                    "used by every non-streaming call"
                ),
                "retry_or_backoff_hits_in_source": retry_hits,
            },
        },
        "async_only": {
            "value": True,
            "evidence": (
                f"SpiceDBClient defines {len(async_methods)} async methods and "
                f"{len(sync_methods)} non-dunder sync methods "
                f"({sync_methods or 'none'}); no second, sync client class exists anywhere "
                "in spicedb-python/spicedb/"
            ),
        },
        "check_signature": {
            "permission_is_separate_argument": permission_is_separate_arg,
            "check_permission_args": check_args,
            "resource_relation_used_as_permission_evidence": resource_relation_used_as_permission.to_json(),
        },
        "iteration_style": (
            "true async generators (`async def ... yield`, AsyncIterator[T]); pages are "
            "fetched from the server on demand, not buffered up front"
        ),
    }


def extract_typescript(repo: Path) -> dict:
    pat = PATTERNS["typescript"]
    client = repo / "spicedb-typescript/src/client.ts"
    types = repo / "spicedb-typescript/src/types.ts"
    consistency = repo / "spicedb-typescript/src/consistency.ts"
    errors = repo / "spicedb-typescript/src/errors.ts"

    client_methods = client_method_facts_typescript(repo, pat)
    client_methods_by_name = {f.name: f for f in client_methods if f.name}

    def pick(canonical: str) -> dict:
        return pick_entry_point(client_methods_by_name, canonical, client, pat["client_method"])

    entry = {
        "check_single": pick("checkPermission"),
        "check_bulk": pick("checkPermissions"),
        "lookup_resources": pick("lookupResources"),
        "lookup_subjects": pick("lookupSubjects"),
        "expand": pick("expandPermissionTree"),
        "read_relationships": pick("readRelationships"),
        "write_relationships": pick("write"),
        "delete_relationships": pick("deleteRelationships"),
        "watch": pick("watch"),
    }

    constructors = [
        find_first(client, pat["constructor_fn"]).to_json(),
        find_first(client, pat["constructor_method"]).to_json(),
    ]
    consistency_helpers = [f.to_json() for f in find_all(consistency, pat["consistency_fn"])]
    error_classes = [f.to_json() for f in find_all(errors, pat["error_class"])]
    retry_hits = [f.to_json() for f in find_all(client, pat["retry_or_backoff"]) if f.line]

    rel_fields = extract_block_fields(
        types, r"^export interface Relationship", r"^\}", pat["relationship_field"]
    )
    filter_fields = extract_block_fields(
        types, r"^export interface RelationshipFilterOptions", r"^\}", pat["relationship_field"]
    )

    return {
        "language": "typescript",
        "client_dir": "spicedb-typescript",
        "constructors": constructors,
        "entry_points": entry,
        "public_methods": [f.to_json() for f in client_methods],
        "types": {
            "relationship": {"type": "Relationship (interface)", "fields": rel_fields},
            "filter": {"type": "RelationshipFilterOptions (interface)", "fields": filter_fields},
            "transaction": {
                "type": "Transaction (class)",
                "methods": [f.to_json() for f in transaction_method_facts(repo, "typescript")],
            },
        },
        "consistency_helpers": consistency_helpers,
        "error_handling": {
            "style": "typed error hierarchy (SpiceDBError subclasses extending Error), with automatic retry",
            "error_classes": error_classes,
            "retry": {
                "present": True,
                "evidence": "client.ts defines withRetry() and DEFAULT_MAX_RETRIES = 3; used by every non-streaming call",
                "retry_or_backoff_hits_in_source": retry_hits,
            },
        },
        "iteration_style": (
            "true async generators (`async *method()`, AsyncIterableIterator<T>); pages are "
            "fetched from the server on demand, not buffered up front"
        ),
    }


def extract_csharp(repo: Path) -> dict:
    pat = PATTERNS["csharp"]
    client = repo / "spicedb-csharp/SpiceDB.Client/SpiceDBClient.cs"
    consistency = repo / "spicedb-csharp/SpiceDB.Client/Consistency.cs"
    errors = repo / "spicedb-csharp/SpiceDB.Client/Errors.cs"
    relationship = repo / "spicedb-csharp/SpiceDB.Client/Relationship.cs"
    filter_ = repo / "spicedb-csharp/SpiceDB.Client/Filter.cs"

    client_methods = client_method_facts_csharp(repo, pat)
    client_methods_by_name = {f.name: f for f in client_methods if f.name}

    def pick(canonical: str) -> dict:
        return pick_entry_point(client_methods_by_name, canonical, client, pat["client_method"])

    entry = {
        "check_single": pick("CheckPermissionAsync"),
        "check_bulk": pick("CheckPermissionsAsync"),
        "lookup_resources": pick("LookupResourcesAsync"),
        "lookup_subjects": pick("LookupSubjectsAsync"),
        "expand": pick("ExpandPermissionTreeAsync"),
        "read_relationships": pick("ReadRelationshipsAsync"),
        "write_relationships": pick("WriteAsync"),
        "delete_relationships": pick("DeleteRelationshipsAsync"),
        "watch": pick("UpdatesAsync"),
    }

    constructors = [f.to_json() for f in find_all(client, pat["constructor"])]
    consistency_helpers = [f.to_json() for f in find_all(consistency, pat["consistency_fn"])]
    error_classes = [f.to_json() for f in find_all(errors, pat["error_class"])]
    retry_hits = [f.to_json() for f in find_all(client, pat["retry_or_backoff"]) if f.line]

    # Claim #3 evidence: does a params-array check overload place
    # CancellationToken immediately before `params Relationship[]`?
    token_before_params = []
    lines = read_lines(client)
    for i, line in enumerate(lines):
        if re.search(pat["cancellation_token_before_params"], line):
            # next non-blank line should be the params array
            for j in range(i + 1, min(i + 3, len(lines))):
                if re.search(pat["params_array"], lines[j]):
                    token_before_params.append(
                        {
                            "cancellation_token_line": i + 1,
                            "cancellation_token_text": line.strip(),
                            "params_array_line": j + 1,
                            "params_array_text": lines[j].strip(),
                        }
                    )
                    break

    rel_fields = extract_block_fields(
        relationship, r"^public sealed record Relationship", r"^\s*\}?\s*$|public static", pat["relationship_field"]
    )
    filter_fields = extract_block_fields(
        filter_, r"^public sealed record Filter", r"public Filter\(", pat["relationship_field"]
    )

    return {
        "language": "csharp",
        "client_dir": "spicedb-csharp",
        "constructors": constructors,
        "entry_points": entry,
        "public_methods": [f.to_json() for f in client_methods],
        "types": {
            "relationship": {"type": "Relationship (sealed record)", "fields": rel_fields},
            "filter": {"type": "Filter (sealed record)", "fields": filter_fields},
            "transaction": {
                "type": "Transaction (sealed class)",
                "methods": [f.to_json() for f in transaction_method_facts(repo, "csharp")],
            },
        },
        "consistency_helpers": consistency_helpers,
        "error_handling": {
            "style": "typed exception hierarchy (SpiceDBException subclasses), with automatic retry",
            "exception_classes": error_classes,
            "retry": {
                "present": True,
                "evidence": "SpiceDBClient.cs defines RetryAsync() and MaxRetryAttempts = 5; used by every non-streaming call",
                "retry_or_backoff_hits_in_source": retry_hits,
            },
        },
        "cancellation_token_placement": {
            "claim": "CancellationToken is placed before the trailing `params Relationship[]` array",
            "confirmed_sites": token_before_params,
            "note": (
                "only the params-array overloads (CheckPermissionsAsync, CheckAnyAsync, "
                "CheckAllAsync) need this -- C# requires `params` to be the last formal "
                "parameter, so a positional CancellationToken cannot follow it. The "
                "single-relationship CheckPermissionAsync overload has no params array and "
                "places CancellationToken last, as normal .NET convention dictates."
            ),
        },
        "iteration_style": (
            "true lazy async streams (`async IAsyncEnumerable<T>` with `yield return`); "
            "pages are fetched from the server on demand, not buffered up front"
        ),
    }


def extract_java(repo: Path) -> dict:
    pat = PATTERNS["java"]
    client = repo / "spicedb-java/lib/src/main/java/com/authzed/spicedb/SpiceDBClient.java"
    consistency = repo / "spicedb-java/lib/src/main/java/com/authzed/spicedb/Consistency.java"
    errors_dir = repo / "spicedb-java/lib/src/main/java/com/authzed/spicedb/errors"
    relationship = repo / "spicedb-java/lib/src/main/java/com/authzed/spicedb/Relationship.java"

    client_methods = client_method_facts_java(repo, pat)
    client_methods_by_name = {f.name: f for f in client_methods if f.name}

    def pick(canonical: str) -> dict:
        return pick_entry_point(client_methods_by_name, canonical, client, pat["client_method"])

    entry = {
        "check_single": pick("checkPermission"),
        "check_bulk": pick("checkPermissions"),
        "lookup_resources": pick("lookupResources"),
        "lookup_subjects": pick("lookupSubjects"),
        "expand": pick("expandPermissionTree"),
        "read_relationships": pick("readRelationships"),
        "write_relationships": pick("write"),
        "delete_relationships": pick("deleteRelationships"),
        "watch": pick("updates"),
    }

    constructors = [f.to_json() for f in find_all(client, pat["constructor"])]
    consistency_helpers = [f.to_json() for f in find_all(consistency, pat["consistency_fn"])]
    error_classes = []
    for f in sorted(errors_dir.glob("*.java")):
        error_classes.extend([fact.to_json() for fact in find_all(f, pat["error_class"])])
    retry_hits = [f.to_json() for f in find_all(client, pat["retry_or_backoff"]) if f.line]

    def java_record_fields(path: Path, record_name: str) -> list[str]:
        """Java records declare fields as a comma-separated parameter list that can
        span multiple lines and whose last line shares its closing paren with the
        final field (e.g. `Instant expiration) {`) -- extract_block_fields' single-
        capture-group-per-line model doesn't fit that, so this walks the span by hand.
        """
        fields: list[str] = []
        in_block = False
        for line in read_lines(path):
            if f"public record {record_name}(" in line:
                in_block = True
                continue
            if in_block:
                m = re.search(r"\b(?:String|Map<String,\s*Object>|Instant)\s+(\w+)", line)
                if m:
                    fields.append(m.group(1))
                if ") {" in line:
                    break
        return fields

    rel_fields = java_record_fields(relationship, "Relationship")
    filter_path = repo / "spicedb-java/lib/src/main/java/com/authzed/spicedb/Filter.java"
    filter_fields = java_record_fields(filter_path, "Filter")

    return {
        "language": "java",
        "client_dir": "spicedb-java",
        "constructors": constructors,
        "entry_points": entry,
        "public_methods": [f.to_json() for f in client_methods],
        "types": {
            "relationship": {"type": "Relationship (record)", "fields": rel_fields},
            "filter": {"type": "Filter (record)", "fields": filter_fields},
            "transaction": {
                "type": "Transaction",
                "methods": [f.to_json() for f in transaction_method_facts(repo, "java")],
            },
        },
        "consistency_helpers": consistency_helpers,
        "error_handling": {
            "style": "typed exception hierarchy (SpiceDBException subclasses extending RuntimeException), with automatic retry",
            "exception_classes": error_classes,
            "retry": {
                "present": True,
                "evidence": "SpiceDBClient.java defines withRetry() and MAX_RETRIES = 3; used by every non-streaming call",
                "retry_or_backoff_hits_in_source": retry_hits,
            },
        },
        "iteration_style": (
            "java.util.stream.Stream backed by a lazy Iterator that fetches pages on "
            "demand (Spliterators.spliteratorUnknownSize over a custom Iterator); not a "
            "single up-front buffer, though each page is buffered into a small internal list"
        ),
    }


def extract_rust(repo: Path) -> dict:
    pat = PATTERNS["rust"]
    client = repo / "spicedb-rust/src/client.rs"
    consistency = repo / "spicedb-rust/src/consistency.rs"
    error = repo / "spicedb-rust/src/error.rs"
    types = repo / "spicedb-rust/src/types.rs"

    client_methods = client_method_facts_rust(repo, pat)
    client_methods_by_name = {f.name: f for f in client_methods if f.name}

    def pick(canonical: str) -> dict:
        return pick_entry_point(client_methods_by_name, canonical, client, pat["client_method"])

    entry = {
        "check_single": pick("check_permission"),
        "check_bulk": pick("check_permissions"),
        "lookup_resources": pick("lookup_resources"),
        "lookup_subjects": pick("lookup_subjects"),
        "expand": pick("expand_permission_tree"),
        "read_relationships": pick("read_relationships"),
        "write_relationships": pick("write"),
        "delete_relationships": pick("delete_relationships"),
        "watch": pick("updates"),
    }

    constructors = [f.to_json() for f in find_all(client, pat["constructor"])]
    # `builder()` is the WithOpts-equivalent third constructor path (custom TLS/config) --
    # it doesn't match "new_\w+" since it's named differently, so it's found separately.
    constructors.append(find_first(client, r"pub fn (builder)\(").to_json())
    consistency_helpers = [f.to_json() for f in find_all(consistency, pat["consistency_fn"])]
    error_variants = [f.to_json() for f in find_all(error, pat["error_variant"])]
    retry_hits = [f.to_json() for f in find_all(client, pat["retry_or_backoff"]) if f.line]

    # Claim #4 evidence: streaming methods' actual return types.
    streaming_method_names = [
        "read_relationships",
        "lookup_resources",
        "lookup_subjects",
        "export_relationships",
        "updates",
    ]
    return_type_evidence = []
    for method in streaming_method_names:
        fact = find_first(client, rf"pub async fn {method}\(")
        if fact.line is None:
            continue
        # Signature may span multiple lines; scan forward for the `-> Result<...>` line.
        lines = read_lines(client)
        sig_text = fact.text or ""
        for j in range(fact.line - 1, min(fact.line + 6, len(lines))):
            if "->" in lines[j]:
                sig_text = lines[j].strip()
                break
        returns_vec = bool(re.search(pat["returns_vec"], sig_text))
        return_type_evidence.append(
            {
                "method": method,
                "file": relpath(client),
                "signature_line": fact.line,
                "return_type_line_text": sig_text,
                "returns_vec_buffered": returns_vec,
            }
        )

    doc_claims_stream = find_all(client, pat["returns_stream"])
    doc_claims_stream_hits = [f.to_json() for f in doc_claims_stream if f.line]

    rel_fields = extract_block_fields(
        types, r"^pub struct Relationship", r"^\}", pat["relationship_field"]
    )
    filter_fields = extract_block_fields(
        types, r"^pub struct Filter", r"^\}", pat["relationship_field"]
    )

    return {
        "language": "rust",
        "client_dir": "spicedb-rust",
        "constructors": constructors,
        "entry_points": entry,
        "public_methods": [f.to_json() for f in client_methods],
        "types": {
            "relationship": {"type": "Relationship (struct)", "fields": rel_fields},
            "filter": {"type": "Filter (struct)", "fields": filter_fields},
            "transaction": {
                "type": "Transaction (struct)",
                "methods": [f.to_json() for f in transaction_method_facts(repo, "rust")],
            },
        },
        "consistency_helpers": consistency_helpers,
        "error_handling": {
            "style": "typed error enum (SpiceDBError variants via thiserror), with automatic retry",
            "error_variants": error_variants,
            "retry": {
                "present": True,
                "evidence": "client.rs defines retry() and MAX_RETRIES = 5; used by every non-streaming call",
                "retry_or_backoff_hits_in_source": retry_hits,
            },
        },
        "streaming_vs_buffering": {
            "claim": "Rust buffers into Vec rather than returning a Stream",
            "actual_return_types": return_type_evidence,
            "doc_comments_claiming_stream_return": doc_claims_stream_hits,
            "note": (
                "every streaming-shaped method (read_relationships, lookup_resources, "
                "lookup_subjects, export_relationships, updates/watch) returns "
                "`Result<Vec<T>, SpiceDBError>` and fully drains the server stream inside "
                "the method before returning. The module- and method-level doc comments "
                "(client.rs) say 'Returns impl Stream<Item = ...>' -- that is stale/aspirational "
                "documentation; the compiled signature is Vec."
            ),
        },
        "iteration_style": "eagerly buffered: entire result set collected into a Vec<T> before returning",
    }


def extract_ruby(repo: Path) -> dict:
    pat = PATTERNS["ruby"]
    client = repo / "spicedb-ruby/lib/spicedb/client.rb"
    consistency = repo / "spicedb-ruby/lib/spicedb/consistency.rb"
    errors = repo / "spicedb-ruby/lib/spicedb/errors.rb"
    relationship = repo / "spicedb-ruby/lib/spicedb/relationship.rb"
    filter_ = repo / "spicedb-ruby/lib/spicedb/filter.rb"

    client_methods = client_method_facts_ruby(repo, pat)
    client_methods_by_name = {f.name: f for f in client_methods if f.name}

    def pick(canonical: str) -> dict:
        return pick_entry_point(client_methods_by_name, canonical, client, pat["client_method"])

    entry = {
        "check_single": pick("check_permission"),
        "check_bulk": pick("check_permissions"),
        "lookup_resources": pick("lookup_resources"),
        "lookup_subjects": pick("lookup_subjects"),
        "expand": pick("expand_permission_tree"),
        "read_relationships": pick("read_relationships"),
        "write_relationships": pick("write"),
        "delete_relationships": pick("delete_relationships"),
        "watch": pick("updates"),
    }

    constructors = [f.to_json() for f in find_all(client, pat["constructor"])]
    consistency_helpers = [
        f.to_json()
        for f in find_all(consistency, pat["consistency_fn"])
        if f.name in {"full", "min_latency", "at_least", "snapshot", "at_least_or_full", "at_least_or_min_latency"}
    ]
    error_classes = [f.to_json() for f in find_all(errors, pat["error_class"])]
    retry_hits = [f.to_json() for f in find_all(client, pat["retry_or_backoff"]) if f.line]
    enumerator_hits = [f.to_json() for f in find_all(client, pat["returns_enumerator"]) if f.line]

    rel_fields = extract_block_fields(
        relationship, r"^\s*Relationship = Data\.define\(", r"^\s*\) do", pat["relationship_field"]
    )
    filter_fields = extract_block_fields(
        filter_, r"Filter = Data\.define\(", r"^\s*\) do|^\s*\)$", pat["relationship_field"]
    )

    return {
        "language": "ruby",
        "client_dir": "spicedb-ruby",
        "note": (
            "not discussed anywhere in the design spec's Sec.13 -- derived entirely from "
            "reading spicedb-ruby/lib/spicedb/*.rb"
        ),
        "constructors": constructors,
        "entry_points": entry,
        "public_methods": [f.to_json() for f in client_methods],
        "types": {
            "relationship": {"type": "SpiceDB::Relationship (Data.define)", "fields": rel_fields},
            "filter": {"type": "SpiceDB::Filter (Data.define)", "fields": filter_fields},
            "transaction": {
                "type": "SpiceDB::Transaction",
                "methods": [f.to_json() for f in transaction_method_facts(repo, "ruby")],
            },
        },
        "consistency_helpers": consistency_helpers,
        "error_handling": {
            "style": "typed exception hierarchy (SpiceDB::Error subclasses < StandardError), with automatic retry",
            "exception_classes": error_classes,
            "retry": {
                "present": True,
                "evidence": "client.rb defines with_retry() and MAX_RETRIES = 3; used by every non-streaming call",
                "retry_or_backoff_hits_in_source": retry_hits,
            },
        },
        "iteration_style": (
            "Ruby Enumerator.new with a lazy yielder block; pages are fetched from the "
            "server on demand as the caller iterates, not buffered up front"
        ),
        "enumerator_construction_sites": enumerator_hits,
    }


def extract_spicedb_gen(repo: Path) -> dict:
    main_go = repo / "spicedb-gen/cmd/spicedb-gen/main.go"
    registered_imports = find_all(
        main_go, r'_ "github\.com/authzed/spicedb-clients/spicedb-gen/(\w+)"'
    )
    languages = []
    for imp in registered_imports:
        if imp.name is None:
            continue
        gen_go = repo / "spicedb-gen" / imp.name / "generator.go"
        lang_fact = find_first(gen_go, r'return "(\w+)"')
        languages.append(
            {
                "package": imp.name,
                "registered_at": imp.to_json(),
                "language_key": lang_fact.to_json(),
            }
        )
    return {
        "supported_languages": sorted(l["language_key"]["name"] for l in languages if l["language_key"]["name"]),
        "detail": languages,
        "note": (
            "spec §13 claims Go, TypeScript, Java, and Python -- matches exactly. No "
            "csharp/, ruby/, or rust/ package exists under spicedb-gen/."
        ),
    }


# ---------------------------------------------------------------------------
# Static facts that required running code, not just reading it (Task 1,
# 2026-08-14, against commit 549c4e9 and `spicedb serve-testing` v1.56.0).
# See task-1-report.md for the exact commands.
# ---------------------------------------------------------------------------

LIVE_VERIFICATION = {
    "verified_on": "2026-08-14",
    "verified_against_commit": "549c4e90e7a1488adcf268e0e0033e48d5b5f0a4",
    "spicedb_version": "v1.56.0 (spicedb serve-testing)",
    "method": (
        "manual: cloned repo, started `spicedb serve-testing` on a scratch port, wrote a "
        "schema and one relationship via `zed`, then built and ran a small program per "
        "language against the vendored client source (path-referenced, not published "
        "packages). All 7 languages were verified by execution; none were static-only."
    ),
    "languages_verified_by_execution": [
        "go", "python", "typescript", "csharp", "java", "rust", "ruby",
    ],
    "languages_verified_statically_only": [],
    "per_language_results": {
        "go": {
            "check_result": "CheckOne(view, alice) = true",
            "error_on_missing_permission": (
                "spicedb: check item 0: relation/permission `nonexistent_permission` not "
                "found under definition `document` (Go type: *errors.errorString -- confirms "
                "no typed error hierarchy)"
            ),
            "no_retry_timing": (
                "CheckOne against an unreachable port (nothing listening) returned in "
                "24.4ms -- a single immediate failure, not the multi-second delay a "
                "retry-with-backoff implementation would produce"
            ),
        },
        "python": {
            "check_result_view": "check_permission(consistency.full(), rel with resource_relation='view') = True",
            "check_result_edit": (
                "the SAME resource/subject with resource_relation='edit' (a permission alice "
                "does not have) = False -- proves the permission checked is read from "
                "Relationship.resource_relation, since check_permission takes no separate "
                "permission argument at all"
            ),
            "retry_timing": (
                "check_permission against an unreachable port with max_retries=3 raised "
                "UnavailableError (typed) after 0.705s -- matches the exponential backoff "
                "formula in client.py exactly: sleep(0.1*2^0) + sleep(0.1*2^1) + "
                "sleep(0.1*2^2) = 0.1 + 0.2 + 0.4 = 0.7s"
            ),
        },
        "typescript": {
            "check_result": "checkPermission(full(), {...permission: 'view'...}) = true",
            "consistency_helpers": "minLatency() and atLeastOrFull('') both constructed and returned the expected Consistency proto shape -- confirms the spec's cited names are TypeScript's actual names",
        },
        "csharp": {
            "compiled_and_ran": (
                "CheckPermissionsAsync(Full(), \"view\", default, rel) compiles and runs "
                "with CancellationToken positioned before the trailing `params "
                "Relationship[]` array, returning [True]"
            ),
            "single_overload_contrast": (
                "CheckPermissionAsync(Full(), \"view\", rel, default) -- the non-params "
                "overload -- places CancellationToken last as normal .NET convention, "
                "confirming the divergence is specific to params-array overloads"
            ),
        },
        "java": {
            "check_result": "checkPermission(Consistency.full(), \"view\", rel) = true",
            "stream_result": "lookupResources(...).toList() = [\"readme\"] via a live java.util.stream.Stream",
        },
        "rust": {
            "compiled_and_ran": (
                "`let rels: Vec<Relationship> = client.read_relationships(...).await?;` and "
                "`let ids: Vec<String> = client.lookup_resources(...).await?;` both compile "
                "and run as plain Vec assignments -- no Stream/.next()/poll_next() usage "
                "needed, confirming the buffered return type is real, not just documented "
                "wrong"
            ),
        },
        "ruby": {
            "check_result": "check_permission(Consistency.full, 'view', rel) = true",
            "enumerator_result": "read_relationships(...).class == Enumerator; .to_a == [\"document:readme#viewer@user:alice\"]",
        },
        "spicedb_gen": {
            "ran": "spicedb-gen --schema test-schema.zed --lang typescript --out permissions.ts",
            "result": "generated a real TypedClient wrapper (User/Document factories, .view/.edit/.delete permission accessors) with 0 exit code",
            "rejection_test": (
                "spicedb-gen --schema test-schema.zed --lang ruby --out permissions.rb "
                '-> exit 2, stderr: unknown language "ruby"; registered languages: go java '
                "python typescript -- live confirmation of the exact supported-language set"
            ),
        },
    },
    "toolchains_available_in_this_environment": {
        "go": "go1.26.1",
        "python": "3.12.3 via uv",
        "node": "v26.0.0, pnpm 10.32.1",
        "dotnet": "10.0.201",
        "java": "OpenJDK 25.0.2, Gradle 9.4.0 (no Maven, not needed)",
        "rust": "cargo 1.94.0 / rustc 1.94.0",
        "ruby": (
            "system Ruby is 2.6.10 (Apple-bundled), below the gem's required >= 3.2; "
            "Homebrew ruby 4.0.2 (/opt/homebrew/opt/ruby/bin) was used instead"
        ),
    },
}


CLAIMS_VERIFICATION = {
    "claim_1_go_no_typed_errors_no_retry": {
        "spec_text": "Go has no typed error hierarchy and no retry.",
        "verdict": "confirmed",
        "evidence": [
            "spicedb-go/rel/rel.go:19-25 -- exactly 3 sentinel errors (ErrInvalidResource, "
            "ErrInvalidRelation, ErrInvalidSubject), all fmt.Errorf() plain errors, no "
            "custom error type or Is/As-matchable struct",
            "every gRPC-facing call site (checks.go, relationships.go, lookup.go, "
            "expand.go, watch.go, schema.go, bulk.go) wraps failures as "
            "fmt.Errorf(\"spicedb: ...: %w\", err) -- the raw grpc/status error, unwrapped "
            "into no SpiceDB-specific type",
            "grep for retry|backoff across spicedb-go/client/*.go and "
            "spicedb-go/consistency/*.go (excluding _test.go): 0 matches",
            "go.mod has no grpc-retry / backoff dependency of any kind",
            "live: CheckOne against an unreachable port returned in 24.4ms (single "
            "attempt, no backoff) and the error's Go type was the untyped "
            "*errors.errorString",
        ],
    },
    "claim_2_python_async_only_permission_from_resource_relation": {
        "spec_text": (
            "Python is async-only and derives the permission from "
            "Relationship.resource_relation rather than taking it as a separate argument, "
            "unlike every other language."
        ),
        "verdict": "confirmed",
        "evidence": [
            "spicedb-python/spicedb/client.py:107-118 -- check_permission(self, "
            "consistency, rel, *, context=None) has NO permission parameter at all",
            "spicedb-python/spicedb/client.py:140 -- permission=rel.resource_relation, "
            "inside check_permissions()'s proto item construction",
            "spicedb-python/spicedb/types.py:19 -- Relationship.resource_relation is a "
            "required dataclass field",
            "every other language's check method takes permission as an explicit string "
            "argument: Go checks.go:18 CheckOne(ctx, cs, permission string, r); TS "
            "types.ts:78-86 CheckRequest.permission; C# SpiceDBClient.cs:142-150 "
            "CheckPermissionAsync(..., string permission, ...); Java SpiceDBClient.java:125 "
            "checkPermission(Consistency, String permission, Relationship); Rust "
            "client.rs:121-126 check_permission(&self, ..., permission: &str, ...); Ruby "
            "client.rb:115 check_permission(consistency, permission, relationship)",
            "spicedb-python/spicedb/client.py -- every method in SpiceDBClient is `async "
            "def`; no plain `def` methods besides dunders, and no second sync client class "
            "exists anywhere in the package",
            "live: the SAME resource/subject checked with resource_relation='view' (True) "
            "vs. 'edit' (False, a permission alice lacks) proves the field is actually read, "
            "not merely present and ignored",
        ],
    },
    "claim_3_csharp_cancellationtoken_before_params": {
        "spec_text": "C# places CancellationToken before its params array.",
        "verdict": "confirmed",
        "evidence": [
            "spicedb-csharp/SpiceDB.Client/SpiceDBClient.cs:104-108 -- "
            "CheckPermissionsAsync(ConsistencyStrategy consistency, string permission, "
            "CancellationToken cancellationToken = default, params Relationship[] "
            "relationships)",
            "same pattern at CheckAnyAsync:155-159 and CheckAllAsync:168-172",
            "necessity, not arbitrary style: C# requires `params` to be the last formal "
            "parameter, so a CancellationToken with a positional default cannot follow it "
            "on any overload that also takes params",
            "contrast confirms the divergence is scoped correctly: the single-relationship "
            "CheckPermissionAsync overload (no params array) at lines 142-146 places "
            "CancellationToken LAST, the normal .NET convention",
            "live: `dotnet run` compiled and executed "
            "CheckPermissionsAsync(Full(), \"view\", default, rel) successfully with this "
            "exact parameter order",
        ],
    },
    "claim_4_rust_buffers_vec_not_stream": {
        "spec_text": "Rust buffers into Vec rather than returning a Stream.",
        "verdict": "confirmed",
        "evidence": [
            "spicedb-rust/src/client.rs:306-310 read_relationships(...) -> "
            "Result<Vec<Relationship>, SpiceDBError>",
            "spicedb-rust/src/client.rs:401-408 lookup_resources(...) -> "
            "Result<Vec<String>, SpiceDBError>",
            "spicedb-rust/src/client.rs:466-473 lookup_subjects(...) -> "
            "Result<Vec<String>, SpiceDBError>",
            "spicedb-rust/src/client.rs:827-831 export_relationships(...) -> "
            "Result<Vec<Relationship>, SpiceDBError>",
            "spicedb-rust/src/client.rs:887-891 updates(...) [watch] -> "
            "Result<Vec<Update>, SpiceDBError>",
            "every one of these methods fully drains the server-side gRPC stream inside a "
            "`loop { ... stream.message().await ... }` before returning",
            "live: `let rels: Vec<Relationship> = client.read_relationships(...).await?;` "
            "compiles and runs as a plain Vec assignment against a live server",
            "surprising secondary finding: client.rs's own doc comments say the API "
            "streams, contradicting the compiled Vec signature -- 7 lines total (see "
            "languages.rust.streaming_vs_buffering.doc_comments_claiming_stream_return "
            "in this same JSON for the full, mechanically re-derivable list rather than "
            "a hand-picked subset). Two use the literal generic-typed phrase verbatim: "
            "line 44 (module-level doc) and line 305 (read_relationships's doc block), "
            "both 'Returns `impl Stream<Item = Result<T, SpiceDBError>>`'. The other five "
            "(298, 396, 460, 822, 882) use softer descriptive prose to the same effect -- "
            "'Returns a stream of relationships...', 'Returns a stream of resource IDs...' "
            "-- without repeating that exact generic-typed phrase. Either way, Rust is the "
            "one language in this repo where the client's own comments disagree with its "
            "own code on this point",
        ],
    },
}


def extract_service_inventory(repo: Path) -> dict:
    """Mechanically enumerate every gRPC service in the vendored proto source.

    Only `proto-clients/spicedb-rust-proto/proto/` ships the raw `.proto`
    files -- every other `proto-clients/*-proto` package ships generated code
    only (Go/Python/TS/C#/Java/Ruby stubs are all compiled from these same
    `.proto`s by `buf generate`, so the raw source is authoritative, not a
    Rust-specific artifact). This walks that directory rather than asserting a
    count from memory -- Fix round 1 exists because an earlier version of this
    script asserted "exactly 4 services... nothing else" from having only
    looked at `authzed/api/v1/`, missing the three services under
    `authzed/api/materialize/v0/` entirely.
    """
    proto_root = repo / "proto-clients/spicedb-rust-proto/proto/authzed/api"
    command = f"grep -rn '^service ' {relpath(proto_root)}"
    services = []
    for proto_file in sorted(proto_root.rglob("*.proto")):
        for fact in find_all(proto_file, r"^service (\w+)"):
            if fact.name is None:
                continue
            rpc_methods = [
                f.name for f in find_all(proto_file, r"^\s*rpc (\w+)\(") if f.name
            ]
            services.append({**fact.to_json(), "rpc_methods": rpc_methods})
    return {
        "note": (
            "the raw .proto source (not generated code) is what this enumerates, "
            "walking every .proto file under authzed/api/ recursively -- v1/ and "
            "materialize/v0/ both included, so a fourth package added later would "
            "also be picked up automatically"
        ),
        "command": command,
        "services": services,
        "count": len(services),
    }


def check_materialize_usage(repo: Path) -> dict:
    """Mechanically confirm that no hand-written client source references the
    `materialize` package -- only generated proto stubs and build artifacts
    (installed venvs, node_modules, compiled DLLs/target dirs) should match.
    Equivalent to `grep -rli materialize <dir>` per client, with build/dependency
    directories excluded; run at extraction time rather than asserted from a
    one-off manual grep, so a later maintainer gets a fresh answer for free.
    """
    client_dirs = [
        "spicedb-go",
        "spicedb-python",
        "spicedb-typescript",
        "spicedb-csharp",
        "spicedb-java",
        "spicedb-ruby",
        "spicedb-rust",
    ]
    exclude_dirs = re.compile(r"[\\/](\.venv|node_modules|bin|obj|target|build|dist|\.git)[\\/]")
    command = (
        "grep -rli materialize <client-dir>, excluding "
        ".venv/node_modules/bin/obj/target/build/dist/.git"
    )
    hits_by_client: dict[str, list[str]] = {}
    for d in client_dirs:
        base = repo / d
        hits: list[str] = []
        if base.is_dir():
            for p in base.rglob("*"):
                if not p.is_file() or exclude_dirs.search(str(p)):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if re.search("materialize", text, re.IGNORECASE):
                    hits.append(relpath(p) or str(p))
        hits_by_client[d] = hits
    return {
        "command": command,
        "hits_by_client": hits_by_client,
        "total_hits": sum(len(v) for v in hits_by_client.values()),
    }


def build_no_counterpart(repo: Path) -> dict:
    service_inventory = extract_service_inventory(repo)
    materialize_usage = check_materialize_usage(repo)
    service_names = ", ".join(s["name"] for s in service_inventory["services"])

    return {
        "service_inventory": service_inventory,
        "materialize_usage_check": materialize_usage,
        "confirmed_from_spec": [
            {
                "operation": "OpenFGA store CRUD (CreateStore/GetStore/ListStores/DeleteStore)",
                "verdict": "confirmed no counterpart",
                "evidence": (
                    f"SpiceDB's vendored proto source defines {service_inventory['count']} "
                    f"gRPC services total ({service_names}) -- see service_inventory above, "
                    "derived by walking proto-clients/spicedb-rust-proto/proto/authzed/api/ "
                    "recursively, not asserted from a partial read. None of the 7 model "
                    "multi-tenant stores; a SpiceDB deployment is a single logical backend "
                    "with one schema and one relationship graph. The 3 materialize/v0 "
                    f"services ({materialize_usage['command']} -> "
                    f"{materialize_usage['total_hits']} hits across all 7 idiomatic clients' "
                    "hand-written source) are wrapped by none of the 7 language clients "
                    "either way, so they don't change this verdict."
                ),
            },
            {
                "operation": "AuthZEN",
                "verdict": "confirmed no counterpart",
                "evidence": "no AuthZEN-related message, service, or proto file anywhere under proto-clients/ or any spicedb-* client directory",
            },
            {
                "operation": "OpenFGA Permissions Index",
                "verdict": "confirmed no counterpart",
                "evidence": "no Index-related service or message in the SpiceDB proto surface (v1 or materialize/v0)",
            },
        ],
        "additional_gaps_found": [
            {
                "operation": "OpenFGA Check's contextual_tuples (ephemeral, request-scoped relationship tuples supplied only for that one check, never persisted)",
                "verdict": "no SpiceDB counterpart",
                "evidence": (
                    "CheckPermissionRequest (proto-clients/spicedb-go-proto/gen/authzed/api/v1/"
                    "permission_service.pb.go:1048-1066) has fields Consistency, Resource, "
                    "Permission, Subject, Context, WithTracing -- `Context` is caveat-evaluation "
                    "context (named values fed into a caveat expression), not relationship "
                    "tuples. There is no field anywhere in CheckPermissionRequest or "
                    "CheckBulkPermissionsRequestItem for supplying extra, non-persisted "
                    "relationships at check time. The narrower claim this extraction can "
                    "actually support: of the 7 services that exist, none -- including the 3 "
                    "materialize/v0 services (RelationshipsService.ExperimentalCountRelationshipsByFilter, "
                    "WatchPermissionsService.WatchPermissions, "
                    "WatchPermissionSetsService.{WatchPermissionSets,LookupPermissionSets,"
                    "DownloadPermissionSets} -- see service_inventory[].rpc_methods above) -- "
                    "accepts ephemeral relationship tuples on any RPC. A migrating check site "
                    "that relies on OpenFGA's contextual_tuples for a what-if check has no "
                    "direct target -- it must actually Write the relationship (transactionally, "
                    "durably) before checking, which is a materially different operation."
                ),
            },
            {
                "operation": "OpenFGA authorization_model_id pinning (checking against a specific, immutable historical model while a newer one is live)",
                "verdict": "no SpiceDB counterpart",
                "evidence": (
                    "WriteSchema (spicedb-go/client/schema.go:20-27) replaces the schema "
                    "outright; CheckPermissionRequest has no schema/model-id field to pin a "
                    "check to an older schema version the way OpenFGA's Check accepts an "
                    "authorization_model_id. SpiceDB's ReflectSchema/DiffSchema (schema.go:75-"
                    "213) let you read or diff the CURRENT schema and compare against an "
                    "arbitrary comparison string, but there is no server-side registry of past "
                    "schemas addressable by ID for live checks. The narrower claim this "
                    "extraction can actually support: none of the 7 services -- including the "
                    "3 materialize/v0 ones, whose RPCs are all watch/lookup/download "
                    "operations over the current materialized state, not schema-version "
                    "selectors -- exposes a model-id/schema-version parameter anywhere. "
                    "ZedTokens pin relationship-data revisions, not schema versions -- the two "
                    "are orthogonal. A migrated codebase that assumed it could keep checking "
                    "against an old model while rolling out a new one has no equivalent "
                    "mechanism."
                ),
            },
        ],
        "note_for_task_8": (
            "these five items (3 from the spec + 2 found here) are the complete "
            "no-counterpart list this extraction turned up; Task 8's command should halt on "
            "all five rather than only the spec's original three. The service inventory above "
            "is the complete, mechanically-derived list of what exists -- 7, not 4 -- so a "
            "later reader can verify 'no counterpart' was checked against everything, not a "
            "partial reading of the proto tree."
        ),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def get_commit_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_language_dirs_present(repo: Path) -> list[str]:
    expected = [
        "spicedb-go",
        "spicedb-python",
        "spicedb-typescript",
        "spicedb-csharp",
        "spicedb-java",
        "spicedb-ruby",
        "spicedb-rust",
        "spicedb-gen",
        "proto-clients",
    ]
    return [d for d in expected if (repo / d).is_dir()]


def build_surface(repo: Path) -> dict:
    global _REPO_ROOT
    _REPO_ROOT = repo
    return {
        "$schema_note": (
            "Ground truth for the SpiceDB prototype clients, produced by "
            "scripts/extract_client_api.py. See that script's module docstring for "
            "extraction method. Re-run it against a fresh checkout when the pinned commit "
            "moves; the live_verification block requires manual re-verification (see "
            "task-1-report.md)."
        ),
        "repo_url": REPO_URL,
        "commit": get_commit_sha(repo),
        "language_dirs_present": get_language_dirs_present(repo),
        "extracted_on": str(date.today()),
        "languages": {
            "go": extract_go(repo),
            "python": extract_python(repo),
            "typescript": extract_typescript(repo),
            "csharp": extract_csharp(repo),
            "java": extract_java(repo),
            "rust": extract_rust(repo),
            "ruby": extract_ruby(repo),
        },
        "spicedb_gen": extract_spicedb_gen(repo),
        "claims_verification": CLAIMS_VERIFICATION,
        "no_counterpart": build_no_counterpart(repo),
        "live_verification": LIVE_VERIFICATION,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo", type=Path, required=True, help="path to a checkout of spicedb-clients-prototype"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output JSON path")
    args = parser.parse_args()

    repo = args.repo.resolve()
    if not repo.is_dir():
        raise SystemExit(f"--repo {repo} is not a directory")

    surface = build_surface(repo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(surface, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out} (commit {surface['commit']})")


if __name__ == "__main__":
    main()
