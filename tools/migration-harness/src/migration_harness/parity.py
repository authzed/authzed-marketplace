"""Compare OpenFGA and SpiceDB assertion sets."""

import dataclasses

from .idmap import IdMap
from .model import Assertion

_Key = tuple[str, str, str, str, str, str, str]


def _key(a: Assertion) -> _Key:
    """Everything that identifies the *question*, excluding the answer."""
    return (
        a.subject_type,
        a.subject_id,
        a.subject_relation,
        a.permission,
        a.resource_type,
        a.resource_id,
        a.context,
    )


def _dedupe(
    assertions: list[Assertion],
) -> tuple[dict[_Key, Assertion], dict[_Key, tuple[Assertion, Assertion]]]:
    """Collapse same-key assertions on one side, flagging real conflicts.

    Two assertions that ask the identical question (same ``_key``) can
    legitimately occur on one side alone -- e.g. two ``.fga.yaml``
    ``tests:`` blocks that both happen to check the same tuple. If they
    agree on the answer, the duplicate is harmless and is silently
    collapsed to a single entry.

    If they disagree, the question is ambiguous on this side: the source
    data disputes itself, so there is no trustworthy answer to compare
    against the other side. The first-seen assertion and the first one
    that conflicts with it are recorded as one ``(first_seen,
    conflicting)`` pair, and the key is *removed* from the returned
    mapping -- it must be reported solely via the ambiguous pair, never
    incidentally as a missing/extra/contradiction entry derived from
    whichever assertion happened to remain in the dict.
    """
    seen: dict[_Key, Assertion] = {}
    ambiguous: dict[_Key, tuple[Assertion, Assertion]] = {}
    for a in assertions:
        k = _key(a)
        if k in ambiguous:
            continue  # already flagged; later duplicates add no information
        if k not in seen:
            seen[k] = a
        elif seen[k].expected != a.expected:
            ambiguous[k] = (seen[k], a)
            del seen[k]
        # else: same key, same answer -- a harmless duplicate; keep the first
    return seen, ambiguous


@dataclasses.dataclass(frozen=True)
class ParityReport:
    """The outcome of comparing an OpenFGA assertion set to a SpiceDB one.

    The dataclass itself is frozen, but its list/tuple fields stay
    ordinarily mutable in place -- that is intentional, not an oversight.
    """

    missing: list[Assertion]
    extra: list[Assertion]
    contradictions: list[tuple[Assertion, Assertion]]
    ambiguous: list[tuple[Assertion, Assertion]]
    caveated: list[str]
    compared: int = 0
    """How many questions were actually asked of *both* sides.

    Empty-set arithmetic makes every other field vacuously clean when nothing
    was compared, so a store whose oracle is entirely `list_objects` /
    `list_users` -- or whose `tests:` is empty -- would otherwise report parity
    while having verified nothing at all. This is the only field that
    distinguishes "the conversion agrees with the source" from "no evidence was
    examined".
    """

    @property
    def ok(self) -> bool:
        """True only if something was compared *and* nothing disagreed."""
        return self.compared > 0 and not (
            self.missing or self.extra or self.contradictions or self.ambiguous
        )


def compare(
    fga: list[Assertion],
    spicedb: list[Assertion],
    idmap: IdMap,
    caveated: list[str],
) -> ParityReport:
    """Set-compare the two sides after mapping the OpenFGA side forward.

    A question present on both sides with opposite answers is a contradiction
    -- a correctness failure -- and is reported apart from mere absence, which
    is only a coverage failure.

    A question that appears more than once on the *same* side with
    different answers is ambiguous: the source data disagrees with itself,
    so there is nothing trustworthy to compare against the other side. Each
    side is deduplicated independently (see ``_dedupe``), and any key found
    ambiguous on either side is dropped from *both* sides' working sets
    before missing/extra/contradiction are computed. That guarantees each
    problem is reported exactly once -- via ``ambiguous`` -- and an
    ambiguous question never also surfaces as missing, extra, or
    contradicting.

    ``caveated`` is carried through unexamined and never affects ``ok``.

    ``compared`` counts the questions both sides answered -- the only positive
    evidence a run produces. Everything else this function reports is an
    absence, and absences are all vacuously satisfied by an empty input.
    """
    mapped = [idmap.apply(a) for a in fga]
    left, left_ambiguous = _dedupe(mapped)
    right, right_ambiguous = _dedupe(spicedb)

    for k in left_ambiguous.keys() | right_ambiguous.keys():
        left.pop(k, None)
        right.pop(k, None)

    both = left.keys() & right.keys()
    contradictions = [
        (left[k], right[k]) for k in both if left[k].expected != right[k].expected
    ]
    missing = [left[k] for k in sorted(left.keys() - right.keys())]
    extra = [right[k] for k in sorted(right.keys() - left.keys())]
    ambiguous = sorted([*left_ambiguous.values(), *right_ambiguous.values()])

    return ParityReport(
        missing=missing,
        extra=extra,
        contradictions=sorted(contradictions),
        ambiguous=ambiguous,
        caveated=list(caveated),
        compared=len(both),
    )
