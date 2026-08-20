import pathlib

import pytest

from migration_harness.fga_store import load_fga_assertions
from migration_harness.model import Assertion


def write(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    p = tmp_path / "store.fga.yaml"
    p.write_text(body)
    return p


def test_single_user_single_object(tmp_path):
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        object: document:1
        assertions:
          view: true
          edit: false
""")
    got = load_fga_assertions(p)
    assert got == [
        Assertion("user", "anne", "", "view", "document", "1", True, ""),
        Assertion("user", "anne", "", "edit", "document", "1", False, ""),
    ]


def test_fan_out_is_the_cross_product(tmp_path):
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    check:
      - users: [user:anne, user:bob]
        objects: [document:1, document:2]
        assertions:
          view: true
""")
    got = load_fga_assertions(p)
    assert len(got) == 4
    assert set((a.subject_id, a.resource_id) for a in got) == {
        ("anne", "1"), ("anne", "2"), ("bob", "1"), ("bob", "2"),
    }


def test_subject_relation_is_preserved(tmp_path):
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    check:
      - user: organization:openfga#member
        object: repo:openfga/openfga
        assertions:
          admin: true
""")
    got = load_fga_assertions(p)
    assert got[0].subject_type == "organization"
    assert got[0].subject_relation == "member"
    assert got[0].resource_id == "openfga/openfga"


def test_context_is_canonicalized_to_sorted_json(tmp_path):
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        object: document:1
        context:
          b: 2
          a: 1
        assertions:
          view: true
""")
    got = load_fga_assertions(p)
    assert got[0].context == '{"a":1,"b":2}'


def test_multiple_tests_accumulate(tmp_path):
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        object: document:1
        assertions: {view: true}
  - name: t2
    check:
      - user: user:bob
        object: document:2
        assertions: {view: false}
""")
    assert len(load_fga_assertions(p)) == 2


def test_rejects_both_user_and_users(tmp_path):
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        users: [user:bob]
        object: document:1
        assertions: {view: true}
""")
    with pytest.raises(ValueError, match="cannot contain both 'user' and 'users'"):
        load_fga_assertions(p)


def test_rejects_neither_object_nor_objects(tmp_path):
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        assertions: {view: true}
""")
    with pytest.raises(ValueError, match="must specify 'object' or 'objects'"):
        load_fga_assertions(p)


def test_list_objects_and_list_users_blocks_are_reported_not_parsed(tmp_path):
    # list_objects has no SpiceDB validation-YAML equivalent (spec 12). It must
    # not silently vanish; the loader exposes it for the advisory report.
    p = write(tmp_path, """
name: T
tests:
  - name: t1
    list_objects:
      - user: user:anne
        type: document
        assertions:
          view: [document:1]
""")
    got = load_fga_assertions(p)
    assert got == []
