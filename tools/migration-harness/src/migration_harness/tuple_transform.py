"""OpenFGA tuple -> SpiceDB relationship-string transform (phase 3, data migration).

An OpenFGA tuple (``{user, relation, object, condition?}``) is only implicitly
typed -- nothing in the tuple itself says whether ``relation`` split into a
``relation``/``permission`` pair during schema conversion, or whether the
resource type encodes its object ids. Both answers live in the model's
already-computed :class:`~migration_harness.idmap.IdMap`, so this module is a
pure consumer of it plus :mod:`~migration_harness.model`'s parsing helpers --
it invents no new normalization rule of its own.

**Same transform as phase 5, same implementation, not just the same
semantics.** :mod:`~migration_harness.validation_gen` needs this exact
per-tuple rendering too -- one call per entry in a store's collected
``tuples:`` block -- to build the ``relationships:`` half of a converted
``validation.yaml``. Phase 3 (this module, writing tuples straight to a live
SpiceDB instance) and phase 5 (validation_gen, rendering the same tuples into
a YAML fixture) must produce byte-identical relationship strings for the same
tuple: a relationship phase 3 writes is later checked against an assertion
phase 5 converted, so any divergence between the two would silently break
that lockstep. Rather than keep two copies in agreement by convention, the
shared logic lives in one place, ``validation_gen.tuple_relationship``
(public despite living in that module, precisely because both phases need
it), and ``transform_tuple`` below delegates to it. The delegation is an
implementation detail, not part of this module's contract -- callers only
need ``transform_tuple(t, idmap) -> str``.
"""

from .idmap import IdMap
from .validation_gen import tuple_relationship


def transform_tuple(t: dict, idmap: IdMap) -> str:
    """Render one OpenFGA tuple as a SpiceDB relationship-write string.

    ``t`` is one ``tuples:`` entry: ``{"user": ..., "relation": ...,
    "object": ..., "condition"?: {"name": ..., "context"?: {...}}}``. The
    ``user`` and ``object`` fields are ``type:id`` or ``type:id#relation``
    references (``model.parse_object_ref``); ``user`` may also be the
    wildcard form ``type:*``.

    **Tuples are writes** (`test-mapping.md`, "Tuples are writes;
    assertions are checks"): the resource side's relation is resolved
    through ``idmap.write_relation(resource_type, relation)``, not
    ``idmap.apply``, so a split relation's write targets the generated
    ``__direct`` relation rather than the bare permission SpiceDB refuses to
    accept a write against. Everything else -- type/id mapping on both
    sides, and a userset subject's own ``#relation`` suffix, which always
    stays unsuffixed even when the *subject's* type has a same-named split
    relation -- reuses ``idmap.apply``'s ordinary check-surface resolution.

    A ``condition:`` block becomes a caveat suffix on the relationship
    line, ``[name:{json}]``, or bare ``[name]`` when the condition carries
    no context. The caveat name passes through unnormalized (``idmap`` has
    no caveat namespace) but is validated against SpiceDB's caveat-name
    grammar, and the context is canonicalized (sorted keys, compact
    separators) -- not for a round-trip guarantee (nothing in this codebase
    re-parses a written relationship's caveat context; ``spicedb_val.
    parse_assertion_string`` only re-canonicalizes the ` with {json}` suffix
    on an *assertion* line, never a relationship's `[name:{json}]` bracket
    suffix), but because the serialization must be deterministic and `zed
    validate` must accept it -- both properties `test_generated_file_passes_
    zed_validate` exercises directly on caveat-bearing relationship lines in
    the `banking` corpus store.

    Raises ``InputError`` (a ``ValueError`` subclass) naming the offending
    value if either object id fails SpiceDB's object-id grammar, or if a
    condition name fails the caveat-name grammar -- never emits a
    relationship string SpiceDB would reject outright.
    """
    return tuple_relationship(t, idmap)
