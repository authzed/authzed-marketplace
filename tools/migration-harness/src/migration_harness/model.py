"""Canonical assertion representation shared by both parsers."""

from dataclasses import dataclass


class InputError(ValueError):
    """A harness *input* is malformed -- not a harness bug.

    Raised by every content-validation site in the loaders (`_effective`,
    `parse_object_ref`, `parse_assertion_string`, `IdMap.load`) so `cli.main`
    can report exit 3 for a bad `.fga.yaml` / validation YAML /
    `migration-map.json` while still letting a genuine ``ValueError`` from a
    harness defect escape as a traceback. Catching bare ``ValueError`` in the
    CLI would hide the second kind inside the first.

    Subclasses ``ValueError`` so callers that only care that the input was
    rejected keep working unchanged.
    """


@dataclass(frozen=True, order=True)
class Assertion:
    """One resolved permission question and its expected answer.

    Both the OpenFGA and SpiceDB parsers normalize into this shape so the
    comparator can do plain set arithmetic.
    """

    subject_type: str
    subject_id: str
    subject_relation: str
    permission: str
    resource_type: str
    resource_id: str
    expected: bool
    context: str


def parse_object_ref(ref: str) -> tuple[str, str, str]:
    """Split ``type:id`` or ``type:id#relation`` into its three parts.

    The id may contain ``/`` (common in sample-stores: ``repo:openfga/openfga``)
    and may be the wildcard ``*``.
    """
    if ":" not in ref:
        raise InputError(f"not a valid object reference: {ref!r}")
    obj_type, remainder = ref.split(":", 1)
    if "#" in remainder:
        obj_id, relation = remainder.split("#", 1)
    else:
        obj_id, relation = remainder, ""
    return obj_type, obj_id, relation
