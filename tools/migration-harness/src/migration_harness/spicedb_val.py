"""Load SpiceDB validation YAML assertions into canonical assertions."""

import json
import pathlib

import yaml

from .model import Assertion, InputError, parse_object_ref

_WITH = " with "


def parse_assertion_string(raw: str, expected: bool) -> Assertion:
    """Parse ``resource:id#permission@subject:id[#rel][ with {json}]``."""
    text = raw.strip()
    context = ""
    if _WITH in text:
        text, _, ctx = text.partition(_WITH)
        try:
            parsed = json.loads(ctx)
            context = json.dumps(parsed, sort_keys=True, separators=(",", ":")) if parsed else ""
        except ValueError as exc:
            raise InputError(f"not a valid assertion: {raw!r}") from exc
        text = text.strip()

    if "@" not in text:
        raise InputError(f"not a valid assertion: {raw!r}")
    resource_part, subject_part = text.split("@", 1)
    if "#" not in resource_part:
        raise InputError(f"not a valid assertion: {raw!r}")

    try:
        r_type, r_id, permission = parse_object_ref(resource_part)
    except ValueError as exc:
        raise InputError(f"not a valid assertion: {raw!r}") from exc
    try:
        s_type, s_id, s_rel = parse_object_ref(subject_part)
    except ValueError as exc:
        raise InputError(f"not a valid assertion: {raw!r}") from exc

    return Assertion(
        subject_type=s_type,
        subject_id=s_id,
        subject_relation=s_rel,
        permission=permission,
        resource_type=r_type,
        resource_id=r_id,
        expected=expected,
        context=context,
    )


def load_spicedb_assertions(
    path: pathlib.Path,
) -> tuple[list[Assertion], list[str]]:
    """Return (comparable assertions, raw assertCaveated strings).

    assertCaveated is returned uncompared: OpenFGA checks are boolean and have
    no conditional third state, so these need human review rather than parity.
    """
    doc = yaml.safe_load(path.read_text()) or {}
    block = doc.get("assertions") or {}

    out: list[Assertion] = []
    for key, expected in (("assertTrue", True), ("assertFalse", False)):
        for raw in block.get(key) or []:
            out.append(parse_assertion_string(raw, expected))

    caveated = [str(x).strip() for x in (block.get("assertCaveated") or [])]
    return out, caveated
