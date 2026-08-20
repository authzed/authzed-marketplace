import pathlib

import pytest

from migration_harness.model import Assertion
from migration_harness.spicedb_val import (
    load_spicedb_assertions,
    parse_assertion_string,
)


def write(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp_path / "validation.yaml"
    p.write_text(body)
    return p


def test_parse_simple_assertion_string():
    got = parse_assertion_string("document:somedoc#view@user:jimmy", True)
    assert got == Assertion("user", "jimmy", "", "view", "document", "somedoc", True, "")


def test_parse_assertion_string_with_subject_relation():
    got = parse_assertion_string("repo:acme#admin@organization:openfga#member", True)
    assert got.subject_relation == "member"
    assert got.resource_type == "repo"


def test_parse_assertion_string_with_context():
    got = parse_assertion_string(
        'document:d1#view@user:sarah with {"b": 2, "a": 1}', True
    )
    assert got.context == '{"a":1,"b":2}'
    assert got.subject_id == "sarah"


def test_parse_assertion_string_rejects_missing_at():
    with pytest.raises(ValueError, match="not a valid assertion"):
        parse_assertion_string("document:d1#view", True)


def test_parse_assertion_string_rejects_missing_permission():
    with pytest.raises(ValueError, match="not a valid assertion"):
        parse_assertion_string("document:d1@user:jimmy", True)


def test_parse_assertion_string_empty_context_collapses_to_empty_string():
    got = parse_assertion_string("document:d1#view@user:sarah with {}", True)
    assert got.context == ""


def test_parse_assertion_string_rejects_missing_subject_colon():
    with pytest.raises(ValueError, match="not a valid assertion"):
        parse_assertion_string("document:d1#view@user_jimmy", True)


def test_parse_assertion_string_rejects_missing_resource_colon():
    with pytest.raises(ValueError, match="not a valid assertion"):
        parse_assertion_string("document#view@user:jimmy", True)


def test_parse_assertion_string_rejects_invalid_json_context():
    with pytest.raises(ValueError, match="not a valid assertion"):
        parse_assertion_string("document:d1#view@user:sarah with {not json}", True)


def test_load_splits_true_false_and_caveated(tmp_path):
    p = write(tmp_path, """
schema: |
  definition user {}
relationships: |
  document:d1#viewer@user:jimmy
assertions:
  assertTrue:
    - "document:d1#view@user:jimmy"
  assertFalse:
    - "document:d1#edit@user:jimmy"
  assertCaveated:
    - "document:d1#view@user:sarah"
""")
    assertions, caveated = load_spicedb_assertions(p)
    assert sorted(a.expected for a in assertions) == [False, True]
    assert caveated == ["document:d1#view@user:sarah"]


def test_load_tolerates_absent_assertion_lists(tmp_path):
    p = write(tmp_path, """
schema: |
  definition user {}
assertions:
  assertTrue: []
""")
    assertions, caveated = load_spicedb_assertions(p)
    assert assertions == []
    assert caveated == []
