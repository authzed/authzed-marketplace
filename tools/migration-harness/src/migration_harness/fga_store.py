"""Load OpenFGA .fga.yaml store files into canonical assertions."""

import json
import pathlib
from typing import Any

import yaml

from .model import Assertion, InputError, parse_object_ref


def _canonical_context(raw: Any) -> str:
    """Serialize check context to stable JSON so it compares by value."""
    if not raw:
        return ""
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def _effective(entry: dict, single: str, plural: str) -> list[str]:
    """Resolve the `user`/`users` (or `object`/`objects`) pair.

    Mirrors the fga CLI's own validation, including its error wording.
    """
    has_single, has_plural = single in entry, plural in entry
    if has_single and has_plural:
        raise InputError(f"cannot contain both '{single}' and '{plural}'")
    if has_single:
        return [entry[single]]
    if has_plural:
        return list(entry[plural])
    raise InputError(f"must specify '{single}' or '{plural}'")


def load_fga_assertions(path: pathlib.Path) -> list[Assertion]:
    """Expand every `check` block in a store file into individual assertions.

    One block yields |users| x |objects| x |assertions| entries.

    `list_objects` and `list_users` blocks are **dropped silently**. Nothing in
    the harness surfaces them -- there is no advisory-finding path -- so the
    operator must read the source store to learn how much of its oracle went
    uncompared; `corpus-runs/README.md` records that count per store, and the
    CLI's compared-assertion count is the only in-band signal.

    Corrected: an earlier revision of this docstring claimed the two are not
    equally inexpressible -- that a `list_users` block's expected subject set
    (`assertions.users`) has a validation-YAML counterpart in the `validation:`
    expected-relations block. Verified false against zed v0.31.1: `validation:`
    requires a per-subject *resolution path* (`"[user:alice] is
    <document:doc1#viewer>"`), which `zed` checks against the schema, not a flat
    membership claim -- and `assertions.users` only ever records the flat claim,
    never the path. No `.fga.yaml` construct records that path, and no `zed`
    flag computes it offline (see `openfga-to-spicedb/references/test-mapping.md`,
    "Two corrections to a naive reading of this table", for the reproduction).
    `list_objects` and `list_users` are equally inexpressible in a converted
    validation-YAML file; this loader and `spicedb_val.load_spicedb_assertions`
    drop both alike, correctly, since neither reads anything but boolean
    `check`/`assertTrue`/`assertFalse` pairs.
    """
    doc = yaml.safe_load(path.read_text()) or {}
    out: list[Assertion] = []

    for test in doc.get("tests") or []:
        for entry in test.get("check") or []:
            users = _effective(entry, "user", "users")
            objects = _effective(entry, "object", "objects")
            context = _canonical_context(entry.get("context"))

            for user in users:
                s_type, s_id, s_rel = parse_object_ref(user)
                for obj in objects:
                    r_type, r_id, _ = parse_object_ref(obj)
                    for relation, expected in (entry.get("assertions") or {}).items():
                        out.append(
                            Assertion(
                                subject_type=s_type,
                                subject_id=s_id,
                                subject_relation=s_rel,
                                permission=relation,
                                resource_type=r_type,
                                resource_id=r_id,
                                expected=bool(expected),
                                context=context,
                            )
                        )
    return out
