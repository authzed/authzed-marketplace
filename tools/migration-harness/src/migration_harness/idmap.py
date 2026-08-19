"""Identifier normalization and the migration-map.json contract."""

import base64
import dataclasses
import hashlib
import json
import pathlib
import re
from collections.abc import Iterable

from .model import Assertion, InputError

_SEPARATORS = re.compile(r"[-./]")
_ILLEGAL = re.compile(r"[^a-z0-9_]")
_MAX = 64
_MIN = 3
_MAX_OBJECT_ID = 1024
_VALID_MODES = {"none", "base64url"}


def normalize_name(name: str) -> str:
    """Reduce a source identifier to SpiceDB's ``^[a-z][a-z0-9_]{1,62}[a-z0-9]$``.

    Deterministic: lowercase, separators to underscore, strip illegal
    characters, pad to the 3-character minimum, and truncate over-long names
    with a hash suffix so distinct inputs stay distinct.
    """
    digest = hashlib.sha256(name.encode()).hexdigest()[:6]
    out = _ILLEGAL.sub("", _SEPARATORS.sub("_", name.lower())).strip("_")

    if not out or not out[0].isalpha():
        out = f"x{out}" if out else f"x{digest}"
    if len(out) > _MAX:
        out = f"{out[: _MAX - 7]}_{digest}"
    while len(out) < _MIN:
        out += "0"
    return out.strip("_") or f"x{digest}"


def _disambiguate(raw_names: list[str]) -> dict[str, str]:
    """Normalize a list of source names into a collision-free registry.

    ``normalize_name`` alone is not collision-resistant: distinct inputs like
    ``"can-edit"`` and ``"can_edit"``, or ``"MyDoc"`` and ``"mydoc"``, reduce
    to the same output. This builds a registry over a whole batch of names at
    once so later collisions can be detected and disambiguated.

    Deterministic: the first occurrence of a name (in input order) keeps its
    clean ``normalize_name`` form. A later input that would normalize to a
    name already claimed gets suffixed with a hash of its own raw text
    (``name[:57] + "_" + sha256(raw)[:6]``, 64 characters max) so distinct
    inputs never collapse onto the same SpiceDB name.
    """
    registry: dict[str, str] = {}
    used: set[str] = set()
    for raw in raw_names:
        candidate = normalize_name(raw)
        if candidate in used:
            digest = hashlib.sha256(raw.encode()).hexdigest()[:6]
            candidate = f"{candidate[:57]}_{digest}"
            if candidate in used:
                raise InputError(
                    f"could not disambiguate name {raw!r}: "
                    f"{candidate!r} is already in use"
                )
        used.add(candidate)
        registry[raw] = candidate
    return registry


def _assert_injective(mapping: dict[str, str], namespace: str) -> dict[str, str]:
    """Raise if two distinct source names claim the same target name.

    ``IdMap.build`` guarantees injectivity by construction (``_disambiguate``);
    ``IdMap.load`` trusts a map some *other* process wrote -- a hand-built one,
    or one emitted by the ``/spicedb-dev:migrate-schema`` agent, which cannot
    call ``build`` because it has no Bash tool. So the invariant has to be
    re-checked on the way in, or nothing checks it at all.

    A non-injective map silently *merges* two source identifiers. Every
    assertion, tuple, and call site naming either source name rewrites to the
    same target, so ``parity._dedupe`` collapses the merged pair as a harmless
    duplicate whenever the two happen to agree -- and the run reports PARITY OK
    having quietly stopped asking one of the source's questions. Failing loudly
    here is the only place that lands.

    Returns the target-to-source registry built along the way (``claimed``),
    so a caller that must also check a *second*, related mapping into the
    same SpiceDB namespace -- ``relation_splits``' write-target names live in
    the same per-definition namespace as ``permissions`` -- can keep adding to
    it instead of starting a fresh registry that would miss a collision
    between the two mappings.
    """
    claimed: dict[str, str] = {}
    for source, target in sorted(mapping.items()):
        if target in claimed:
            raise InputError(
                f"{namespace}: {claimed[target]} and {source!r} both map to "
                f"{target!r} -- two distinct source names cannot share one "
                f"SpiceDB name; disambiguate one of them"
            )
        claimed[target] = repr(source)
    return claimed


def encode_id(value: str, mode: str) -> str:
    """Encode an object id into SpiceDB's ``[a-zA-Z0-9/_|\\-=+]`` charset.

    base64url output uses only ``A-Za-z0-9-_=``, all of which SpiceDB permits,
    so no post-mangling is needed and the transform stays reversible.

    Raises for inputs SpiceDB's object-id regex
    (``^[a-zA-Z0-9/_|\\-=+]{1,1024}$``) can never accept: an empty id, or (for
    ``base64url``) an id whose encoded form would exceed 1024 characters.
    Never truncates -- that would silently break reversibility.
    """
    if not value:
        raise InputError(
            "object id must not be empty (SpiceDB requires 1-1024 characters)"
        )
    if mode == "none":
        return value
    if mode == "base64url":
        encoded = base64.urlsafe_b64encode(value.encode()).decode()
        if len(encoded) > _MAX_OBJECT_ID:
            raise InputError(
                f"base64url-encoded object id would be {len(encoded)} characters, "
                f"exceeding SpiceDB's {_MAX_OBJECT_ID}-character object-id limit "
                f"(input was {len(value.encode())} bytes)"
            )
        return encoded
    raise InputError(f"unknown id encoding mode: {mode!r}")


@dataclasses.dataclass(frozen=True)
class IdMap:
    """The machine-readable half of migration-plan.md."""

    types: dict[str, str]
    permissions: dict[str, dict[str, str]]
    encoding_mode: str
    encoded_types: frozenset[str]
    relation_splits: dict[str, dict[str, dict[str, str]]] = dataclasses.field(
        default_factory=dict
    )
    """Per source type, the source relations a schema conversion split.

    ``permissions[type]`` answers one question -- the *check* target -- for
    every source relation or permission, split or not: a split's permission
    keeps its original name, so that table alone is enough for ``apply`` to
    rewrite an assertion. It is not enough for a relationship *write*: the
    resource side of a split relation must name the generated ``__direct``
    relation instead (SpiceDB rejects a write to a permission outright), and
    no existing field records that generated name -- it previously lived only
    in ``migration-plan.md``'s human-readable **Relation splits** table.

    Keyed by source type, then by source relation, each entry names both
    resulting SpiceDB names explicitly (``{"relation": ..., "permission":
    ...}``) rather than relying on position or a bare string, so a reader --
    human or code -- cannot mix up which one is the write target and which is
    the check target. Absent entirely on a map with no splits, or for any
    source type/relation that did not split; ``write_relation`` falls back to
    the ordinary ``permissions`` mapping in both of those cases, and
    ``apply`` never reads this field at all.
    """

    @classmethod
    def load(cls, path: pathlib.Path) -> "IdMap":
        """Read a `migration-map.json`, rejecting one that merges two names.

        Injectivity is checked in the two namespaces SpiceDB actually has:
        ``types`` globally, since definition names share one namespace, and
        each source type's ``permissions`` entry against itself alone, since
        relation/permission names only have to be unique *within* one
        definition. Two types each having a ``viewer`` is not a collision.

        ``relation_splits``' write-target (``__direct``) names share that
        same per-type namespace with ``permissions``, so they are checked
        for injectivity against it too -- both against each other (two split
        relations generating the same write-target name) and against
        ``permissions`` itself (a split's generated name colliding with some
        other, unrelated relation or permission on the same type). Absent
        (or `null`) ``relation_splits`` is treated as empty, matching the
        other two top-level keys, so every map committed before this key
        existed keeps loading -- and keeps producing the identical
        ``apply`` output -- unchanged.
        """
        doc = json.loads(path.read_text())
        enc = doc.get("id_encoding") or {}
        mode = enc.get("mode", "none")
        if mode not in _VALID_MODES:
            raise InputError(f"unknown id encoding mode: {mode!r}")

        types = doc.get("types") or {}
        permissions = doc.get("permissions") or {}
        relation_splits = doc.get("relation_splits") or {}
        _assert_injective(types, "types")

        for source_type in sorted(set(permissions) | set(relation_splits)):
            names = permissions.get(source_type) or {}
            claimed = _assert_injective(names, f"permissions[{source_type!r}]")

            splits = relation_splits.get(source_type) or {}
            for source_relation, split in sorted(splits.items()):
                if (
                    not isinstance(split, dict)
                    or not split.get("relation")
                    or not split.get("permission")
                ):
                    raise InputError(
                        f"relation_splits[{source_type!r}][{source_relation!r}] "
                        "must be an object with non-empty 'relation' and "
                        f"'permission' string keys, got {split!r}"
                    )
                target = split["relation"]
                label = f"{source_relation!r} (relation_splits write target)"
                if target in claimed:
                    raise InputError(
                        f"relation_splits[{source_type!r}]: {claimed[target]} "
                        f"and {label} both map to {target!r} -- two distinct "
                        "source names cannot share one SpiceDB name; "
                        "disambiguate one of them"
                    )
                claimed[target] = label

        return cls(
            types=types,
            permissions=permissions,
            encoding_mode=mode,
            encoded_types=frozenset(enc.get("types") or []),
            relation_splits=relation_splits,
        )

    @classmethod
    def build(
        cls,
        types: list[str],
        relations: dict[str, list[str]],
        *,
        encoding_mode: str = "none",
        encoded_types: Iterable[str] = (),
    ) -> "IdMap":
        """Build an IdMap directly from source type and relation names.

        Where ``load`` reads a pre-computed ``migration-map.json`` and only
        *verifies* that it merges no two names, ``build`` runs normalization
        itself and resolves the collisions a pure ``normalize_name`` call
        cannot detect on its own, so its output satisfies ``load``'s check by
        construction.

        Two separate namespaces: type names are disambiguated against one
        global registry, since SpiceDB definition names share a single
        namespace. Each source type's relations are disambiguated against a
        registry scoped to that type alone, since SpiceDB only requires
        relation/permission names to be unique *within* one definition -- the
        same relation name recurring on two different types is not a
        collision.

        Deterministic: within each registry, the first occurrence of a name
        (in the order given) keeps the clean normalized form; a later input
        that would collide gets a hash-suffixed disambiguated name instead.

        Produces the same ``types`` / ``permissions`` / encoding fields that
        ``load`` produces, so ``apply`` works identically either way.
        """
        if encoding_mode not in _VALID_MODES:
            raise InputError(f"unknown id encoding mode: {encoding_mode!r}")

        type_map = _disambiguate(types)
        permission_map = {
            raw_type: _disambiguate(relations.get(raw_type, [])) for raw_type in types
        }

        return cls(
            types=type_map,
            permissions=permission_map,
            encoding_mode=encoding_mode,
            encoded_types=frozenset(encoded_types),
        )

    def _id(self, obj_type: str, obj_id: str) -> str:
        if obj_id == "*" or obj_type not in self.encoded_types:
            return obj_id
        return encode_id(obj_id, self.encoding_mode)

    def _relation(self, subject_type: str, relation: str) -> str:
        if not relation:
            return relation
        return self.permissions.get(subject_type, {}).get(relation, relation)

    def apply(self, a: Assertion) -> Assertion:
        """Rewrite one OpenFGA-side assertion into its SpiceDB-side form."""
        return dataclasses.replace(
            a,
            subject_type=self.types.get(a.subject_type, a.subject_type),
            subject_id=self._id(a.subject_type, a.subject_id),
            subject_relation=self._relation(a.subject_type, a.subject_relation),
            permission=self.permissions.get(a.resource_type, {}).get(
                a.permission, a.permission
            ),
            resource_type=self.types.get(a.resource_type, a.resource_type),
            resource_id=self._id(a.resource_type, a.resource_id),
        )

    def write_relation(self, source_type: str, source_relation: str) -> str:
        """The relation name a **relationship write** targets, on ``source_type``.

        Deliberately separate from ``apply``, which resolves the *check*
        surface (a permission or a subject-side userset reference) and must
        keep returning a split relation's unsuffixed permission name --
        changing that would invalidate every assertion `apply` has ever
        rewritten, including the parity evidence already recorded for the
        committed corpus. The write path is different in kind: SpiceDB
        rejects a relationship write to a permission outright, so the
        resource side of a stored tuple on a split relation must use the
        generated ``__direct`` relation instead.

        Looks up ``source_relation`` in ``relation_splits[source_type]``
        first; if it split, returns the recorded write-target relation. Every
        other case -- no split recorded for this type/relation, or no
        ``relation_splits`` in the map at all -- falls back to the ordinary
        ``permissions`` mapping ``apply`` itself would produce, so an
        un-split relation writes under the same name it is checked under.
        """
        if not source_relation:
            return source_relation
        split = self.relation_splits.get(source_type, {}).get(source_relation)
        if split is not None:
            return split["relation"]
        return self.permissions.get(source_type, {}).get(
            source_relation, source_relation
        )
