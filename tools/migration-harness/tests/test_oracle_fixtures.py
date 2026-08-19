"""Tests for the two synthetic oracle-gap fixtures under ``fixtures/``.

The 39-store corpus in ``corpus-runs/`` is this project's correctness
oracle, but every one of those 39 stores has ``id_encoding.mode: "none"``
and essentially no relation/permission renaming (2 of 224 type entries, 0 of
916 relation/permission entries) -- see ``corpus-runs/README.md`` and
``data-mapping.md`` for the derivation. Two real code paths --
``idmap.encode_id``/``IdMap._id`` and the rename half of
``IdMap.apply``/``IdMap.write_relation`` -- are therefore never genuinely
exercised by the parametrized corpus tests: disabling either one outright
still passes all 117 corpus-parametrized tests (39 stores x 2 tests in
``test_validation_gen_corpus.py``, plus 39 in ``test_tuple_transform_corpus.
py``).

``fixtures/encoding-store`` and ``fixtures/renaming-store`` are small,
hand-authored stores -- not derived from ``openfga/sample-stores`` --
designed so those two code paths are load-bearing: an email- and a
colon-shaped subject id that SpiceDB's object-id grammar rejects outright
unless encoded, and CamelCase/hyphenated/colliding/over-64-character
relation names that are illegal (or ambiguous) unless renamed. Deliberately
kept out of ``corpus-runs/`` -- that directory is frozen, its "39" is cited
as a derived count throughout the shipped docs, and both corpus test
modules build their ``STORES`` parametrization by globbing it directly, so
anything placed there is auto-enrolled sight unseen. Fixtures live in a
separate top-level directory instead, imported here by an explicit,
hardcoded path -- structurally, not just conventionally, invisible to
``test_validation_gen_corpus.py`` / ``test_tuple_transform_corpus.py``
(``test_fixtures_directory_is_invisible_to_corpus_tests`` below pins that).

Every assertion here reads a parsed field (``yaml.safe_load`` plus either
``spicedb_val.parse_assertion_string`` or ``spicedb_val.
load_spicedb_assertions``), never a substring match against rendered YAML --
substring-matching rendered output has previously passed silently past three
distinct bugs in this project (see the task brief this module was written
against).
"""

import base64
import pathlib
import shutil
import subprocess

import pytest
import yaml

from migration_harness.fga_store import load_fga_assertions
from migration_harness.idmap import IdMap
from migration_harness.parity import compare
from migration_harness.spicedb_val import load_spicedb_assertions, parse_assertion_string
from migration_harness.validation_gen import generate_validation

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus-runs"
ENCODING = FIXTURES / "encoding-store"
RENAMING = FIXTURES / "renaming-store"


def _generate(run: pathlib.Path) -> str:
    idmap = IdMap.load(run / "migration-map.json")
    return generate_validation(run / "store.fga.yaml", idmap, "schema.zed")


def load_spicedb_assertions_from_text(rendered: str):
    """``load_spicedb_assertions`` reads a path; several tests below need to
    parse ``generate_validation``'s in-memory output without writing a
    fixture-scoped file first, so this writes it to a throwaway temp path
    once per call.

    A tiny wrapper, not a reimplementation: it delegates to the real parser
    for every byte of actual parsing.
    """
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(rendered)
        path = pathlib.Path(f.name)
    try:
        return load_spicedb_assertions(path)
    finally:
        path.unlink(missing_ok=True)


def _relationship_lines(rendered: str) -> list[str]:
    """The parsed ``relationships:`` block, split into individual lines.

    ``relationships:`` is itself a flat newline-delimited protocol (SpiceDB
    has no further structure below "one relationship-write string per
    line"), so splitting it into lines is the parse, not a shortcut around
    one -- each line is then handed to ``parse_assertion_string`` (the same
    grammar as an assertion line, minus the ` with {json}` suffix, which
    none of these fixtures' relationship lines use) rather than grepped.
    """
    doc = yaml.safe_load(rendered)
    return [ln for ln in doc["relationships"].splitlines() if ln.strip()]


def _parsed_relationships(run: pathlib.Path, rendered: str):
    """Every relationship-write line, parsed into an ``Assertion``-shaped tuple."""
    return [parse_assertion_string(ln, True) for ln in _relationship_lines(rendered)]


# --- Fixture isolation: prove the corpus tests cannot see these fixtures ----


def test_fixtures_directory_is_invisible_to_corpus_tests():
    """``fixtures/`` is a sibling of ``corpus-runs/``, not a member of it.

    Pins the structural guarantee the module docstring claims: both corpus
    test modules build their parametrization by iterating ``corpus-runs/``
    directly (``CORPUS.iterdir()`` in ``test_validation_gen_corpus.py`` and
    ``test_tuple_transform_corpus.py``), so a fixture placed anywhere else
    is mechanically unreachable by that glob, not merely unlisted by
    convention.
    """
    assert FIXTURES.parent == CORPUS.parent
    assert FIXTURES != CORPUS
    assert not str(FIXTURES).startswith(str(CORPUS) + "/")
    corpus_store_names = {p.name for p in CORPUS.iterdir() if p.is_dir()}
    assert ENCODING.name not in corpus_store_names
    assert RENAMING.name not in corpus_store_names


# --- Both fixtures: the generated file must be legal SpiceDB and must ------
# --- fully agree with the source store's own oracle. ------------------------


@pytest.mark.skipif(shutil.which("zed") is None, reason="zed not installed")
@pytest.mark.parametrize("run", [ENCODING, RENAMING], ids=["encoding-store", "renaming-store"])
def test_fixture_generated_file_passes_zed_validate(run):
    """The generated relationships/assertions must be legal against the fixture's own schema.

    This is the discriminator for a renaming bug specifically: schema.zed is
    a hand-written, independent artifact (not derived from idmap), so if
    apply()/write_relation() stop renaming, the generated file references a
    relation/permission (e.g. the literal, un-normalized "can-edit") that
    schema.zed never defines, and zed rejects it outright.
    """
    generated = run / ".generated-for-test.yaml"
    try:
        generated.write_text(_generate(run))
        proc = subprocess.run(
            ["zed", "validate", "--fail-on-warn", generated.name],
            cwd=run, capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"{run.name}: {proc.stdout}{proc.stderr}"
    finally:
        generated.unlink(missing_ok=True)


@pytest.mark.parametrize("run", [ENCODING, RENAMING], ids=["encoding-store", "renaming-store"])
def test_fixture_generated_validation_achieves_full_parity_with_source(run, tmp_path):
    """generate_validation's output must agree, in full, with the fixture's own source oracle.

    Reuses ``parity.compare`` -- the same function ``cli.run_check`` (the
    harness's real, production comparison) calls -- rather than re-deriving
    a second notion of "agrees". Both fixtures were designed with an equal
    number of true and false assertions per identifier under test, so a
    stray missing/extra/contradiction/ambiguous entry here means something
    concrete resolved to the wrong id or the wrong name, not merely that
    coverage was thin.
    """
    idmap = IdMap.load(run / "migration-map.json")
    generated = tmp_path / "generated.yaml"
    generated.write_text(_generate(run))

    fga = load_fga_assertions(run / "store.fga.yaml")
    spicedb, caveated = load_spicedb_assertions(generated)
    report = compare(fga, spicedb, idmap, caveated)

    assert report.compared > 0, f"{run.name}: nothing was compared"
    assert report.ok, (
        f"{run.name}: parity failed -- missing={report.missing} "
        f"extra={report.extra} contradictions={report.contradictions} "
        f"ambiguous={report.ambiguous}"
    )


# --- Fixture A: object-id encoding -------------------------------------------


def test_encoding_subject_ids_round_trip_through_base64url():
    """The email- and colon-shaped subject ids must appear base64url-encoded
    and decode back to the exact original id.

    Reads the parsed ``assertTrue`` list (``load_spicedb_assertions``, which
    itself parses each line with ``parse_assertion_string`` -- not a
    substring search), locates the specific assertion by its *unencoded*
    identity (resource + permission), and checks the encoded subject_id
    field it actually carries.
    """
    rendered = _generate(ENCODING)
    spicedb, _ = load_spicedb_assertions_from_text(rendered)

    alice_encoded = base64.urlsafe_b64encode(b"alice@corp.example").decode()
    qa_encoded = base64.urlsafe_b64encode(b"qa:on-call").decode()

    owner_assertions = [
        a for a in spicedb
        if a.resource_type == "doc" and a.resource_id == "quarterly-report"
        and a.permission == "owner" and a.expected
    ]
    assert len(owner_assertions) == 1
    assert owner_assertions[0].subject_id == alice_encoded
    assert base64.urlsafe_b64decode(owner_assertions[0].subject_id).decode() == "alice@corp.example"

    viewer_true = [
        a for a in spicedb
        if a.resource_type == "doc" and a.resource_id == "quarterly-report"
        and a.permission == "viewer" and a.expected and a.subject_id == qa_encoded
    ]
    assert len(viewer_true) == 1
    assert base64.urlsafe_b64decode(viewer_true[0].subject_id).decode() == "qa:on-call"


def test_encoding_wildcard_subject_is_never_encoded():
    """``user:*`` must appear literally in the write, never base64url-encoded.

    Parses the specific relationship line for doc:public-notes's
    viewer__direct grant and checks its structured subject_id field, rather
    than searching the rendered text for the literal '*' character (which
    would also match inside base64url output that happens to end up
    containing one, however unlikely).
    """
    rendered = _generate(ENCODING)
    rels = _parsed_relationships(ENCODING, rendered)
    public_grant = [
        r for r in rels
        if r.resource_type == "doc" and r.resource_id == "public-notes"
        and r.permission == "viewer__direct"
    ]
    assert len(public_grant) == 1
    assert public_grant[0].subject_type == "user"
    assert public_grant[0].subject_id == "*"


def test_encoding_write_side_and_check_side_agree_on_the_same_id():
    """The same real subject id must encode identically whether it reaches
    the harness through a ``tuples:`` write or a ``check:`` assertion.

    doc:quarterly-report's viewer__direct relationship (write side, via
    ``idmap.write_relation`` + ``idmap.apply``) and its ``viewer`` assertTrue
    entry for the same real user (check side, via ``idmap.apply`` alone)
    must carry the byte-identical encoded subject_id -- if the two paths
    ever encoded independently (e.g. different byte-order, different base64
    alphabet), this is exactly the kind of divergence that would silently
    turn a real "yes" into a converted "no evidence either way".
    """
    rendered = _generate(ENCODING)
    rels = _parsed_relationships(ENCODING, rendered)
    spicedb, _ = load_spicedb_assertions_from_text(rendered)

    write_side = [
        r for r in rels
        if r.resource_type == "doc" and r.resource_id == "quarterly-report"
        and r.permission == "viewer__direct"
    ]
    check_side = [
        a for a in spicedb
        if a.resource_type == "doc" and a.resource_id == "quarterly-report"
        and a.permission == "viewer" and a.expected
        and a.subject_id != base64.urlsafe_b64encode(b"alice@corp.example").decode()
    ]
    assert len(write_side) == 1
    assert len(check_side) == 1
    assert write_side[0].subject_id == check_side[0].subject_id


# --- Fixture B: identifier renaming -------------------------------------------


def test_renaming_hyphen_and_underscore_relations_stay_distinct():
    """"can-edit" and "can_edit" both normalize to "can_edit" -- the map must
    disambiguate them to two different SpiceDB names, not merge them.

    Reads the loaded IdMap's own ``permissions`` table (parsed JSON
    structure, not the generated YAML) for the authoritative claim, then
    confirms the generated relationships carry those two distinct names on
    the two different subjects that earned them.
    """
    idmap = IdMap.load(RENAMING / "migration-map.json")
    can_edit_target = idmap.permissions["project-plan"]["can-edit"]
    can_edit_underscore_target = idmap.permissions["project-plan"]["can_edit"]
    assert can_edit_target != can_edit_underscore_target
    assert can_edit_target == "can_edit"
    assert can_edit_underscore_target == "can_edit_7d7be7"

    rendered = _generate(RENAMING)
    rels = _parsed_relationships(RENAMING, rendered)
    bob = [r for r in rels if r.subject_id == "bob"]
    carol = [r for r in rels if r.subject_id == "carol"]
    assert len(bob) == 1 and bob[0].permission == can_edit_target
    assert len(carol) == 1 and carol[0].permission == can_edit_underscore_target


def test_renaming_reaches_the_write_side_for_a_split_camelcase_relation():
    """"Approver" (CamelCase, splits) must write to the renamed __direct relation.

    dave's Approver grant is a ``tuples:`` entry, so it goes through
    ``idmap.write_relation`` -- must land on "approver__direct", the name
    ``relation_splits["project-plan"]["Approver"]["relation"]`` records, not
    on the literal source text "Approver" and not on the bare "approver"
    permission (SpiceDB rejects a write to a permission outright).
    """
    rendered = _generate(RENAMING)
    rels = _parsed_relationships(RENAMING, rendered)
    dave = [r for r in rels if r.subject_id == "dave"]
    assert len(dave) == 1
    assert dave[0].resource_type == "project_plan"
    assert dave[0].permission == "approver__direct"


def test_renaming_reaches_the_check_side_for_a_split_camelcase_relation():
    """The "Approver" check must resolve to the "approver" permission, not
    the write-only "approver__direct" relation nor the literal source name.
    """
    rendered = _generate(RENAMING)
    spicedb, _ = load_spicedb_assertions_from_text(rendered)
    dave_true = [
        a for a in spicedb
        if a.subject_id == "dave" and a.resource_type == "project_plan" and a.expected
    ]
    assert len(dave_true) >= 1
    assert any(a.permission == "approver" for a in dave_true)
    assert not any(a.permission == "approver__direct" for a in dave_true)
    assert not any(a.permission == "Approver" for a in dave_true)


def test_renaming_truncates_an_over_64_character_relation_name():
    """A source relation name over 64 characters must be hash-truncated to
    fit, on both the write side and the check side, and stay <= 64 chars.
    """
    source_name = (
        "ReviewerLevelAssignmentForEnterpriseGradeComplianceAuditingProcess"
    )
    assert len(source_name) > 64

    idmap = IdMap.load(RENAMING / "migration-map.json")
    target = idmap.permissions["project-plan"][source_name]
    assert len(target) <= 64

    rendered = _generate(RENAMING)
    rels = _parsed_relationships(RENAMING, rendered)
    erin = [r for r in rels if r.subject_id == "erin"]
    assert len(erin) == 1
    assert erin[0].permission == target

    spicedb, _ = load_spicedb_assertions_from_text(rendered)
    erin_true = [
        a for a in spicedb
        if a.subject_id == "erin" and a.resource_type == "project_plan" and a.expected
    ]
    assert any(a.permission == target for a in erin_true)
