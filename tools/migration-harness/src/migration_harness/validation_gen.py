"""Generate SpiceDB validation YAML from an OpenFGA `.fga.yaml` store file.

The check-block fan-out (the |users| x |objects| x |assertions| expansion and
its context canonicalization) is exactly `fga_store.load_fga_assertions`'s
job, so this module calls it directly rather than re-deriving that logic.
`tuples:` entries have no counterpart there -- that loader only reads
`tests:` -- so this module walks them itself.

The rule that matters most: **tuples are writes, assertions are checks.**
  - `tuples:` entries map their relation through `idmap.write_relation(...)`,
    because a relationship write to a split relation must target the
    generated `__direct` relation -- SpiceDB rejects a write to a permission
    outright.
  - `check:`-derived assertions map through `idmap.apply(...)`, which keeps a
    split relation's *permission* name, because that is what a check
    evaluates against.
Swapping the two produces a validation file that loads and looks correct but
tests the wrong surface.
"""

import dataclasses
import json
import logging
import pathlib
import re
from typing import Any

import yaml

from .fga_store import load_fga_assertions
from .idmap import IdMap
from .model import Assertion, InputError, parse_object_ref

logger = logging.getLogger(__name__)

_OBJECT_ID_RE = re.compile(r"^[a-zA-Z0-9/_|\-=+]{1,1024}$")
_CAVEAT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}[a-z0-9]$")


class _LiteralStr(str):
    """Marker subclass so the YAML dumper emits this value as a `|` block."""


def _literal_representer(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.SafeDumper.add_representer(_LiteralStr, _literal_representer)


def _canonical_context(raw: Any) -> str:
    """Serialize a context mapping to stable JSON.

    Deliberately the same algorithm as `fga_store._canonical_context`
    (sorted keys, compact separators) rather than an import of that
    underscore-prefixed name across module boundaries: the ` with {json}`
    suffix this produces must compare byte-for-byte with what
    `spicedb_val.parse_assertion_string` re-canonicalizes on the read side,
    and the two are the same one-line algorithm.
    """
    if not raw:
        return ""
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def _check_object_id(value: str, label: str) -> None:
    """Reject an id SpiceDB's object-id grammar can never accept.

    ``^[a-zA-Z0-9/_|\\-=+]{1,1024}$``, except the ``*`` wildcard, which is a
    distinct grammar token (``idmap.IdMap._id`` already special-cases it the
    same way rather than encoding it).
    """
    if value == "*":
        return
    if not _OBJECT_ID_RE.fullmatch(value):
        raise InputError(
            f"{label} id {value!r} does not satisfy SpiceDB's object-id "
            f"pattern {_OBJECT_ID_RE.pattern!r}"
        )


def _check_caveat_name(name: str) -> None:
    """Reject a caveat name the relationship-string suffix grammar can't carry.

    An OpenFGA `condition:` name is only validated as a condition identifier
    at model-definition time, which is looser than what SpiceDB accepts
    inside a `[name:{...}]` relationship-string suffix --
    ``^[a-z][a-z0-9_]{1,62}[a-z0-9]$``, the same identifier grammar
    `idmap.normalize_name` targets for every type/relation/permission name.
    A caveat name that violates it would deploy fine as a schema `caveat`
    declaration and then fail every relationship line that references it
    with "invalid relationship string".

    Deliberately not normalized the way a type or relation name is --
    `idmap` has no caveat namespace to normalize consistently against, so
    silently renaming it here would only make this module's output disagree
    with whatever name the schema-generation step gave the caveat.
    """
    if not _CAVEAT_NAME_RE.fullmatch(name):
        raise InputError(
            f"caveat name {name!r} does not satisfy SpiceDB's caveat-name "
            f"pattern {_CAVEAT_NAME_RE.pattern!r}"
        )


def relationship_string(a: Assertion) -> str:
    """Render ``<res_type>:<res_id>#<relation>@<subj_type>:<subj_id>[#<subj_rel>]``.

    Used both for relationship-write lines and, with a ` with {json}` suffix
    appended by the caller, for assertion lines -- this is exactly the
    grammar `spicedb_val.parse_assertion_string` parses back in, so output
    from this function must round-trip through it.
    """
    _check_object_id(a.resource_id, "resource")
    _check_object_id(a.subject_id, "subject")
    text = f"{a.resource_type}:{a.resource_id}#{a.permission}@{a.subject_type}:{a.subject_id}"
    if a.subject_relation:
        text += f"#{a.subject_relation}"
    return text


def _assertion_line(a: Assertion) -> str:
    line = relationship_string(a)
    if a.context:
        line += f" with {a.context}"
    return line


def tuple_relationship(entry: dict, idmap: IdMap) -> str:
    """Render one OpenFGA tuple (a `tuples:` entry) as a relationship-write line.

    Public and general-purpose: nothing in this function's body reads
    `.fga.yaml` document shape (no `tests:`/root distinction, no fan-out) --
    it takes exactly one tuple, `{"user", "relation", "object", "condition"?}`,
    and returns exactly one relationship string. `generate_validation` is one
    caller, iterating this over every tuple a store collects (see
    `all_tuple_entries`); `tuple_transform.transform_tuple` is the other,
    phase 3's per-tuple entry point -- both must emit byte-identical output
    for the same tuple, since a relationship written by phase 3 is checked
    against an assertion converted by phase 5, so this lives here, imported
    rather than duplicated, precisely so that invariant is structural (one
    implementation) rather than incidental (two copies that happen to agree).

    The relation is mapped through `idmap.write_relation` (the write-side
    name), not `idmap.apply` (the check-side name) -- see the module
    docstring. Everything else about the line (subject/resource type and id
    mapping, subject-relation mapping) is the same transform `apply` already
    performs, so this builds a throwaway `Assertion` purely to reuse that
    logic and then overrides the one field that differs.

    A `condition:` block (OpenFGA's tuple-level condition, e.g. the
    `banking` sample store) becomes a SpiceDB caveat suffix,
    `[name:{json}]` (or bare `[name]` with no context) appended to the whole
    line, matching the convention already committed in
    `corpus-runs/banking/validation.yaml`. `idmap` has no condition/caveat
    namespace, so the name passes through unchanged.
    """
    s_type, s_id, s_rel = parse_object_ref(entry["user"])
    r_type, r_id, _ = parse_object_ref(entry["object"])
    relation = entry["relation"]

    raw = Assertion(s_type, s_id, s_rel, relation, r_type, r_id, True, "")
    mapped = dataclasses.replace(
        idmap.apply(raw), permission=idmap.write_relation(r_type, relation)
    )
    line = relationship_string(mapped)

    condition = entry.get("condition")
    if condition:
        name = condition["name"]
        _check_caveat_name(name)
        ctx = _canonical_context(condition.get("context"))
        line += f"[{name}:{ctx}]" if ctx else f"[{name}]"
    return line


def all_tuple_entries(doc: dict) -> list[dict]:
    """Collect every `tuples:` entry a store defines, top-level and per-test.

    Public: originally a `generate_validation`-only helper (`_all_tuple_
    entries`), promoted the same way `_tuple_relationship` was promoted to
    `tuple_relationship` -- a second caller appeared. That caller is
    `tests/test_tuple_transform_corpus.py`, which needs the identical
    root-plus-nested collection this module already does, to build the same
    write set phase 3's transform is checked against; re-deriving it there
    (even by reading `doc.get("tuples")` alone) would silently drop any
    `tests:`-block-scoped tuple, the exact bug this function exists to fix.

    OpenFGA's `.fga.yaml` format allows `tuples:` at the document root *and*
    scoped to an individual `tests:` block -- `fga model test` treats each
    `tests:` block as an isolated dataset, so a block's own `tuples:` seeds
    state for that scenario alone, layered on top of (never replacing) any
    root-level tuples. A store can carry both, root-level tuples only, or --
    as `condition-data-types` does -- no root-level `tuples:` key at all,
    with every write living inside its two `tests:` blocks. Reading only
    `doc.get("tuples")` silently drops that second, block-scoped source
    entirely, which is exactly what left `condition-data-types` generating
    an empty `relationships:` block despite the store defining 18 tuple
    writes.

    Order is preserved -- root-level entries first, then each `tests:`
    block's own entries in file order -- because `dedupe_tuple_lines`
    relies on that order to decide which of two colliding writes wins.
    """
    entries = list(doc.get("tuples") or [])
    for test in doc.get("tests") or []:
        entries.extend(test.get("tuples") or [])
    return entries


def dedupe_tuple_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    """Collapse relationship-write lines that collide on the same triple.

    Public: originally `_dedupe_tuple_lines`, a `generate_validation`-only
    helper, promoted for the same reason `all_tuple_entries` was --
    `tests/test_tuple_transform_corpus.py` needs this exact collapse before
    handing a store's transformed tuples to `zed validate`, since the same
    (resource, relation, subject) collision this function resolves for
    phase 5's converted `validation.yaml` can equally appear in the raw
    `.fga.yaml` tuple set that test assembles. Reusing this function keeps
    both callers' notion of "the tuples that actually get written" in
    lockstep, the same rationale `tuple_relationship`'s promotion documents.

    That collision is a fixture artifact, not something either migration
    phase's real input ever produces: a live OpenFGA store holds one row per
    `(object, relation, user)` triple, so phase 3's actual source data
    cannot contain it. It arises only here, from `.fga.yaml`'s multiple
    isolated `tests:` blocks being folded into one shared SpiceDB graph --
    which is exactly why this function's fix belongs in the *test's*
    assembly of "what phase 5 already had to solve", not in
    `tuple_transform.transform_tuple` itself (a pure per-tuple function with
    no visibility into any other tuple, so it could never detect a
    cross-tuple collision in the first place).

    SpiceDB has one flat relationship graph -- no notion of an isolated
    `tests:`-block dataset -- so once every `tuples:` entry across every
    test block is folded into one converted graph, two blocks that both
    write the *same* (resource, relation, subject) triple collide. When
    their caveat suffix differs, `zed validate`'s own loader rejects the
    naive union outright (`found repeated relationship`), so keeping every
    line is not an option.

    First-seen wins, matching the corpus's own hand-verified precedent
    (`condition-data-types`, `corpus-runs/README.md` finding 3, and
    `schema-mapping.md`'s "Multiple isolated test fixtures colliding in one
    converted graph"): keep the earliest block's binding, drop the rest.
    But "first-seen wins" is only half of that precedent -- the same
    passage goes on to *require* recording the collision ("record the
    specific colliding triples ... and flag them for a hand-written check
    in phase 5 rather than trusting the converted validation file's boolean
    answer for them"), because the dropped scenario's checks can still
    return the "right" boolean for the wrong reason: the surviving caveat
    binding can happen to already satisfy them, so nothing downstream
    (`zed validate`, the parity harness) ever sees that the discarded
    scenario went untested. Silently keeping one value with no trace would
    honor only the first half of that guidance.

    This is deliberately *not* the same move as `parity.py`'s `_dedupe`,
    despite solving the same class of problem: that function treats a
    same-key conflict as `AMBIGUOUS` and drops the key from *both* sides,
    never silently keeping one value -- correct there because it is
    comparing two independently-produced answers with no basis to prefer
    either. Here there is only one side being *generated*: dropping the
    triple entirely is not an option (SpiceDB still needs exactly one
    relationship per triple to answer anything about it), and the corpus
    precedent is explicit about which binding to keep. So this function
    returns both the resolved write lines *and* the list of what it
    discarded, and the caller renders the latter into the file as a
    `# NOTE(spicedbmigration):` comment -- the same channel `advisory_notes` uses for its
    own structurally identical problem (data the generator cannot
    faithfully convert) -- instead of letting the drop disappear untraced.

    Two lines that render identically collapse harmlessly either way and
    produce no advisory note; there is no information loss to report. The
    collision key is everything before the caveat suffix's opening `[` --
    object/relation/subject ids can never themselves contain `[`, since
    SpiceDB's object-id grammar (`_OBJECT_ID_RE`) excludes it, so splitting
    on the first `[` cannot misfire on id content.
    """
    seen: dict[str, str] = {}
    order: list[str] = []
    notes: list[str] = []
    for line in lines:
        key = line.split("[", 1)[0]
        if key in seen:
            if line != seen[key]:
                note = (
                    f"{key}: kept `{seen[key]}`, discarded conflicting write "
                    f"`{line}` -- two tests: blocks wrote different caveat "
                    "bindings to this same triple; the discarded scenario's "
                    "checks are unverified by this converted graph, not "
                    "merely untested (they can pass by coincidence)"
                )
                notes.append(note)
                logger.warning("generate_validation: %s", note)
            continue
        seen[key] = line
        order.append(key)
    return [seen[k] for k in order], notes


def _cross_block_relation_conflicts(doc: dict) -> list[str]:
    """Flag objects that two different `tests:` blocks write different relations onto.

    A sibling risk to the same-triple collision `dedupe_tuple_lines` deals
    with, and one `zed validate` never sees at all (`schema-mapping.md`,
    "Same object ID, different relation, mutually exclusive scenarios",
    confirmed against `abac-with-rebac`). Instead of a recurring identical
    triple, two `tests:` blocks can each write a *different* relation onto
    the *same* object -- `abac-with-rebac`'s two blocks write `draft` and
    `published` respectively onto the same `document:readme` -- so each
    block represents one real-world state of that object rather than a
    duplicate fact. No raw triple repeats, so `dedupe_tuple_lines`'s
    key-based check never fires, and `zed validate`'s loader accepts the
    union (or either choice alone) without complaint. Verified live and
    documented in the reference above: with both relations present at once,
    downstream permissions that arrow through whichever one is present can
    silently resolve to the wrong answer for whichever scenario is not the
    one "active".

    This is a purely data-driven, schema-independent detection, matching
    that reference's own "Detection" step: only tuples *scoped to a
    `tests:` block* are considered (root-level tuples are shared baseline
    state present in every scenario, not an isolated fixture, so they carry
    none of this risk), and it flags a candidate for a human to check --
    it does not and cannot decide whether the conflict is real, since that
    requires the schema and the downstream permissions, neither of which
    this function receives.
    """
    written_by: dict[str, dict[str, set[str]]] = {}
    for test in doc.get("tests") or []:
        name = test.get("name", "<unnamed test>")
        for entry in test.get("tuples") or []:
            r_type, r_id, _ = parse_object_ref(entry["object"])
            obj = f"{r_type}:{r_id}"
            written_by.setdefault(obj, {}).setdefault(entry["relation"], set()).add(name)

    notes: list[str] = []
    for obj, relations in sorted(written_by.items()):
        contributing_blocks: set[str] = set()
        for blocks in relations.values():
            contributing_blocks |= blocks
        if len(relations) > 1 and len(contributing_blocks) > 1:
            detail = "; ".join(
                f'{rel} (from {", ".join(sorted(blocks))})'
                for rel, blocks in sorted(relations.items())
            )
            note = (
                f"{obj}: written under {len(relations)} different relations "
                f"by {len(contributing_blocks)} different tests: blocks -- "
                f"{detail}. These may encode mutually exclusive real-world "
                "states merged into one graph with no per-block isolation; "
                "verify by hand whether any downstream permission's answer "
                "depends on which relation is present before trusting this "
                "object's converted checks."
            )
            notes.append(note)
            logger.warning("generate_validation: %s", note)
    return notes


def advisory_notes(doc: dict) -> list[str]:
    """Describe every `list_objects`/`list_users` block found in the store.

    Public: this is the structured form of the advisory data, one plain
    string per block (``test "<name>": list_objects (<n> entries)``). Callers
    that need the list itself -- e.g. a command recording advisory findings
    in `migration-plan.md` -- should call this directly rather than scraping
    it back out of `generate_validation`'s embedded YAML comment; that
    comment (and the `logging.warning` calls below) exist for the
    human-facing channel and stay exactly as they are.

    Neither `list_objects` nor `list_users` has a validation-YAML equivalent
    (there is no expected-object-set or expected-subject-set construct for a
    plain assertTrue/assertFalse file), so they cannot be converted -- but
    dropping them without a trace would quietly erase part of the store's
    oracle. `doc` is a store's already-parsed YAML (`yaml.safe_load` of a
    `.fga.yaml` file), matching how `generate_validation` calls this.
    """
    notes: list[str] = []
    for test in doc.get("tests") or []:
        name = test.get("name", "<unnamed test>")
        for key in ("list_objects", "list_users"):
            entries = test.get(key)
            if entries:
                note = f'test "{name}": {key} ({len(entries)} entries)'
                notes.append(note)
                logger.warning(
                    "generate_validation: %s has no validation-YAML "
                    "equivalent and was not converted (%s)",
                    key,
                    note,
                )
    return notes


def generate_validation(store_path: pathlib.Path, idmap: IdMap, schema_ref: str) -> str:
    """Render a store's `tuples:`/`check:` content as validation-YAML text."""
    doc = yaml.safe_load(store_path.read_text()) or {}

    tuple_lines, collision_notes = dedupe_tuple_lines(
        [tuple_relationship(entry, idmap) for entry in all_tuple_entries(doc)]
    )
    relationships = sorted(tuple_lines)

    true_lines: list[str] = []
    false_lines: list[str] = []
    for raw in load_fga_assertions(store_path):
        mapped = idmap.apply(raw)
        line = _assertion_line(mapped)
        (true_lines if mapped.expected else false_lines).append(line)

    data = {
        "schemaFile": schema_ref,
        "relationships": _LiteralStr("\n".join(relationships)),
        "assertions": {
            "assertTrue": true_lines,
            "assertFalse": false_lines,
        },
    }
    out = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)

    # Header shape (findings-report.md, "Inline markers", "(b) Generated-file header
    # manifest"): one line of context, then one line per item -- never a multi-line
    # prose preamble. This function has no `migration-plan.md` to point at (it runs
    # over a bare store file, inside and outside this harness alike), so the context
    # line points at the source artifact instead.
    advisories = advisory_notes(doc)
    if advisories:
        header = (
            "# NOTE(spicedbmigration): list_objects/list_users block(s) below have no "
            "validation-YAML equivalent and were not converted -- review the source "
            ".fga.yaml store directly:\n"
        )
        body = "\n".join(f"#   - {note}" for note in advisories)
        out = f"{header}{body}\n{out}"

    risk_notes = collision_notes + _cross_block_relation_conflicts(doc)
    if risk_notes:
        header = (
            "# NOTE(spicedbmigration): merging isolated tests: block(s) below into one "
            "shared graph carries risk zed validate can't see -- verify each by hand "
            "(see schema-mapping.md, \"Multiple isolated test fixtures colliding in one "
            "converted graph\"):\n"
        )
        body = "\n".join(f"#   - {note}" for note in risk_notes)
        out = f"{header}{body}\n{out}"

    return out
