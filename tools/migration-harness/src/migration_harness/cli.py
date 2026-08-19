"""Command-line entry point for the parity harness."""

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

import yaml

from .fga_store import load_fga_assertions
from .idmap import IdMap
from .model import InputError
from .parity import ParityReport, compare
from .spicedb_val import load_spicedb_assertions

_EPILOG = (
    "exit codes:\n"
    "  0  parity OK -- at least one assertion compared, and none disagreed\n"
    "  1  parity failure (missing/extra/contradicting/ambiguous assertions,\n"
    "     or zero assertions compared, which proves nothing)\n"
    "  2  `zed validate` failed against --converted\n"
    "  3  harness input error (missing/unreadable file, malformed YAML or\n"
    "     JSON, a check block that is not valid .fga.yaml, an unparseable\n"
    "     assertion string, or a migration-map.json that is invalid or maps\n"
    "     two source names onto one SpiceDB name)\n"
)


def run_check(
    store_yaml: pathlib.Path,
    converted_yaml: pathlib.Path,
    map_json: pathlib.Path,
) -> ParityReport:
    """Compare one store's assertions against its converted counterpart."""
    fga = load_fga_assertions(store_yaml)
    spicedb, caveated = load_spicedb_assertions(converted_yaml)
    return compare(fga, spicedb, IdMap.load(map_json), caveated)


def run_zed_validate(converted_yaml: pathlib.Path) -> tuple[bool, str]:
    """Run `zed validate`; returns (passed, combined output)."""
    if shutil.which("zed") is None:
        return False, "zed not found on PATH"
    proc = subprocess.run(
        ["zed", "validate", str(converted_yaml)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout + proc.stderr


def _render(report: ParityReport) -> str:
    lines: list[str] = []
    for left, right in report.contradictions:
        lines.append(
            f"CONTRADICTION {left.resource_type}:{left.resource_id}"
            f"#{left.permission}@{left.subject_type}:{left.subject_id} "
            f"openfga={left.expected} spicedb={right.expected}"
        )
    for first_seen, conflicting in report.ambiguous:
        lines.append(
            f"AMBIGUOUS     {first_seen.resource_type}:{first_seen.resource_id}"
            f"#{first_seen.permission}@{first_seen.subject_type}:{first_seen.subject_id} "
            f"same-side conflict: expected={first_seen.expected} "
            f"vs expected={conflicting.expected}"
        )
    for a in report.missing:
        lines.append(
            f"MISSING       {a.resource_type}:{a.resource_id}"
            f"#{a.permission}@{a.subject_type}:{a.subject_id} expected={a.expected}"
        )
    for a in report.extra:
        lines.append(
            f"EXTRA         {a.resource_type}:{a.resource_id}"
            f"#{a.permission}@{a.subject_type}:{a.subject_id} expected={a.expected}"
        )
    for raw in report.caveated:
        lines.append(f"CAVEATED      {raw}  (needs human review)")
    if report.compared == 0:
        lines.append(
            "NOTHING COMPARED  zero assertions were asked of both sides -- "
            "this run is not evidence of parity. Check that the store has "
            "`check:` blocks (`list_objects` / `list_users` blocks are "
            "dropped) and that the converted file has assertTrue/assertFalse "
            "entries."
        )
    return "\n".join(lines)


def _input_error(path: pathlib.Path, what: str, exc: object) -> int:
    """Print one actionable, traceback-free line naming the bad input; return 3."""
    print(f"input error: {what} {path}: {exc}", file=sys.stderr)
    return 3


def main() -> int:
    """Parse args, run the harness, and return an exit code.

    Exit codes: 0 parity OK, 1 parity failure (including a run that compared
    nothing), 2 `zed validate` failure, 3 harness input error (bad/missing
    file, malformed YAML or JSON, invalid content, non-injective map). See the
    argparse epilog (``--help``) for the operator-facing version.
    """
    parser = argparse.ArgumentParser(
        prog="migration-harness",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--store", required=True, type=pathlib.Path)
    parser.add_argument("--converted", required=True, type=pathlib.Path)
    parser.add_argument("--map", required=True, type=pathlib.Path)
    parser.add_argument("--skip-zed", action="store_true")
    args = parser.parse_args()

    # Stat every input *before* invoking zed. `zed validate` fails on a
    # nonexistent --converted and would return 2, reporting an operator typo
    # as a schema failure -- the one exit code the hardening loop reads as
    # "the conversion is wrong". Missing files are input errors (3) on every
    # argument, not just the two that happen to be read after zed runs.
    for path, what in (
        (args.store, "store file"),
        (args.converted, "converted validation file"),
        (args.map, "migration map"),
    ):
        if not path.is_file():
            return _input_error(path, f"no such {what}:", "not a readable file")

    if not args.skip_zed:
        passed, output = run_zed_validate(args.converted)
        if not passed:
            print("zed validate FAILED", file=sys.stderr)
            print(output, file=sys.stderr)
            return 2

    # Loaded individually (rather than via run_check) so each failure can be
    # attributed to the specific path that caused it -- an operator typo
    # must be reported distinctly from a genuine parity mismatch, since the
    # corpus-hardening loop keys off the exit code alone.
    #
    # `InputError` covers content validation (a check block with both `user`
    # and `users`, an unparseable object reference, an assertion string with
    # no `@`, a migration map that merges two names). It is caught rather than
    # bare `ValueError` on purpose: a `ValueError` raised anywhere else is a
    # harness bug and must still surface as a traceback, not be relabelled an
    # operator error.
    try:
        fga = load_fga_assertions(args.store)
    except (OSError, yaml.YAMLError, InputError) as exc:
        return _input_error(args.store, "could not load store file", exc)

    try:
        spicedb, caveated = load_spicedb_assertions(args.converted)
    except (OSError, yaml.YAMLError, InputError) as exc:
        return _input_error(
            args.converted, "could not load converted validation file", exc
        )

    try:
        idmap = IdMap.load(args.map)
    except (OSError, json.JSONDecodeError, InputError) as exc:
        return _input_error(args.map, "could not load migration map", exc)

    try:
        report = compare(fga, spicedb, idmap, caveated)
    except InputError as exc:
        # Applying the map re-encodes object ids, so a store id SpiceDB can
        # never accept is only discovered here, not at load time.
        return _input_error(args.store, "could not map store assertions", exc)

    rendered = _render(report)
    if rendered:
        print(rendered)
    print(
        f"{'PARITY OK' if report.ok else 'PARITY FAILED'} "
        f"({report.compared} assertions compared)",
        file=sys.stderr,
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
