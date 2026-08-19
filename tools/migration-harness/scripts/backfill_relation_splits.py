#!/usr/bin/env python3
"""Backfill `relation_splits` into every `corpus-runs/<store>/migration-map.json`.

Background
----------
`relation_splits` (added in `b3a8b86`) lets `IdMap.write_relation` find the
generated `__direct` relation a split relation's *write* side must target --
SpiceDB rejects a relationship write aimed at a permission outright. All 39
corpus maps predate the key, so `write_relation` silently falls back to the
check-side (permission) name for every split relation, and any write built
from one of these maps names the wrong thing.

This script derives the missing key mechanically, from data already
committed in each store:

  1. Parse `schema.zed` for every SpiceDB definition that declares both
     `relation <X>__direct: ...` and `permission <X> = ...` -- the generated
     pair a relation/permission split always produces together.
  2. Invert the map's own `types` table (SpiceDB definition name -> source
     type name) to recover which *source* type that definition came from.
  3. Invert that source type's `permissions` table (source relation name ->
     SpiceDB permission name) to recover which *source* relation produced
     permission `X`.
  4. Emit `relation_splits[<source_type>][<source_relation>] =
     {"relation": "<X>__direct", "permission": "<X>"}`.

`relation_splits` is keyed by *source* names because `IdMap.write_relation`
and `validation_gen._tuple_relationship` are both called with the type and
relation names as they appear in the source `.fga.yaml` `tuples:` entries,
not with SpiceDB names. Keying by SpiceDB names instead would produce a map
that loads cleanly, passes `IdMap.load`'s injectivity check, and then
answers every `write_relation` lookup with the unsplit fallback again --
silently wrong in exactly the way this backfill exists to fix.

Deliberately does not touch `types`, `permissions`, or `id_encoding` -- only
ever inserts a new `relation_splits` top-level key (or leaves a store alone
entirely, if `schema.zed` has no split relations at all).

Usage
-----
    uv run python scripts/backfill_relation_splits.py            # report only
    uv run python scripts/backfill_relation_splits.py --write     # backfill

Run from `tools/migration-harness/` (the default `--corpus-runs` path is
relative to the current directory).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

_DEFINITION_RE = re.compile(r"definition\s+(\S+)\s*\{([^}]*)\}", re.DOTALL)
_DIRECT_RELATION_RE = re.compile(r"relation\s+(\w+)__direct\s*:")
_PERMISSION_RE = re.compile(r"permission\s+(\w+)\s*=")


class DerivationError(Exception):
    """A store's schema/map pair could not be inverted unambiguously."""


def parse_schema_splits(schema_text: str) -> dict[str, list[str]]:
    """Return ``{spicedb_definition_name: [X, ...]}``.

    ``X`` ranges over every name for which the *same* definition body
    declares both ``relation X__direct: ...`` and ``permission X = ...`` --
    the generated pair a relation/permission split always produces together.
    Relies on SpiceDB schema having no nested ``{``/``}`` inside a
    definition body (permission expressions use only parentheses), verified
    against all 39 committed `schema.zed` files before writing this parser.
    """
    result: dict[str, list[str]] = {}
    for m in _DEFINITION_RE.finditer(schema_text):
        def_name = m.group(1)
        body = m.group(2)
        direct_names = set(_DIRECT_RELATION_RE.findall(body))
        perm_names = set(_PERMISSION_RE.findall(body))
        splits = sorted(direct_names & perm_names)
        if splits:
            result[def_name] = splits
    return result


def invert_injective(mapping: dict[str, str], label: str) -> dict[str, str]:
    """Invert a str->str mapping, raising if it is not actually injective.

    `IdMap.load` already enforces injectivity on `types` globally and on
    each `permissions[type]` table individually, so a non-injective table
    here would mean the committed map itself violates its own documented
    contract -- worth failing loudly on rather than guessing.
    """
    inverted: dict[str, str] = {}
    for source, target in mapping.items():
        if target in inverted:
            raise DerivationError(
                f"{label}: both {inverted[target]!r} and {source!r} map to "
                f"{target!r} -- not injective, cannot invert"
            )
        inverted[target] = source
    return inverted


def backfill_store(store_dir: pathlib.Path) -> tuple[dict[str, dict[str, dict[str, str]]], int]:
    """Derive the `relation_splits` value for one store.

    Returns the (possibly empty) `relation_splits` dict and the number of
    splits found. Raises `DerivationError` if a split relation in
    `schema.zed` cannot be traced back to a source type/relation via the
    map's own `types`/`permissions` tables -- that indicates a parsing or
    inversion bug, not a store to skip.
    """
    schema_text = (store_dir / "schema.zed").read_text()
    doc = json.loads((store_dir / "migration-map.json").read_text())
    types: dict[str, str] = doc.get("types") or {}
    permissions: dict[str, dict[str, str]] = doc.get("permissions") or {}

    reverse_types = invert_injective(types, f"{store_dir.name}: types")

    def_splits = parse_schema_splits(schema_text)

    relation_splits: dict[str, dict[str, dict[str, str]]] = {}
    count = 0
    for spicedb_type, split_names in sorted(def_splits.items()):
        source_type = reverse_types.get(spicedb_type)
        if source_type is None:
            raise DerivationError(
                f"{store_dir.name}: schema.zed definition {spicedb_type!r} has "
                f"split relation(s) {split_names} but no source type in the map's "
                "'types' table resolves to it"
            )
        perm_map = permissions.get(source_type) or {}
        reverse_perm = invert_injective(
            perm_map, f"{store_dir.name}: permissions[{source_type!r}]"
        )

        for x in split_names:
            source_relation = reverse_perm.get(x)
            if source_relation is None:
                raise DerivationError(
                    f"{store_dir.name}: no source relation in "
                    f"permissions[{source_type!r}] maps to SpiceDB permission "
                    f"{x!r} (split relation {x}__direct in definition "
                    f"{spicedb_type!r})"
                )
            relation_splits.setdefault(source_type, {})[source_relation] = {
                "relation": f"{x}__direct",
                "permission": x,
            }
            count += 1

    return relation_splits, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the derived 'relation_splits' key back into each "
        "migration-map.json. Default is report-only.",
    )
    parser.add_argument(
        "--corpus-runs",
        default="corpus-runs",
        help="Path to the corpus-runs directory (default: %(default)s)",
    )
    args = parser.parse_args()

    root = pathlib.Path(args.corpus_runs)
    store_dirs = sorted(p for p in root.iterdir() if p.is_dir())

    total_splits = 0
    stores_with_splits = 0
    for store_dir in store_dirs:
        schema_path = store_dir / "schema.zed"
        map_path = store_dir / "migration-map.json"
        if not schema_path.exists() or not map_path.exists():
            continue

        relation_splits, count = backfill_store(store_dir)
        if count == 0:
            continue

        stores_with_splits += 1
        total_splits += count
        print(f"{store_dir.name}: {count} split(s)")

        if args.write:
            doc = json.loads(map_path.read_text())
            doc["relation_splits"] = relation_splits
            map_path.write_text(json.dumps(doc, indent=2) + "\n")

    mode = "WROTE" if args.write else "REPORT-ONLY"
    print(f"[{mode}] TOTAL: {total_splits} split(s) across {stores_with_splits} store(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
