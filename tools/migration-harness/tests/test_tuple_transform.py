import json
import pathlib

import pytest

from migration_harness.idmap import IdMap
from migration_harness.tuple_transform import transform_tuple


def mapfile(tmp_path: pathlib.Path, **over) -> IdMap:
    doc = {"types": {}, "permissions": {},
           "id_encoding": {"mode": "none", "types": []}}
    doc.update(over)
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    return IdMap.load(p)


def test_plain_tuple(tmp_path):
    t = {"user": "user:anne", "relation": "viewer", "object": "document:1"}
    assert transform_tuple(t, mapfile(tmp_path)) == "document:1#viewer@user:anne"


def test_userset_subject(tmp_path):
    t = {"user": "group:eng#member", "relation": "viewer", "object": "document:1"}
    assert transform_tuple(t, mapfile(tmp_path)) == "document:1#viewer@group:eng#member"


def test_wildcard_subject(tmp_path):
    t = {"user": "user:*", "relation": "viewer", "object": "document:1"}
    assert transform_tuple(t, mapfile(tmp_path)) == "document:1#viewer@user:*"


def test_split_relation_targets_write_side(tmp_path):
    idmap = mapfile(tmp_path, relation_splits={
        "org": {"member": {"relation": "member__direct", "permission": "member"}}})
    t = {"user": "user:anne", "relation": "member", "object": "org:acme"}
    assert transform_tuple(t, idmap) == "org:acme#member__direct@user:anne"


def test_condition_becomes_caveat(tmp_path):
    t = {"user": "user:anne", "relation": "viewer", "object": "document:1",
         "condition": {"name": "in_window", "context": {"b": 2, "a": 1}}}
    got = transform_tuple(t, mapfile(tmp_path))
    assert got == 'document:1#viewer@user:anne[in_window:{"a":1,"b":2}]'


def test_condition_without_context(tmp_path):
    t = {"user": "user:anne", "relation": "viewer", "object": "document:1",
         "condition": {"name": "in_window"}}
    assert transform_tuple(t, mapfile(tmp_path)) == "document:1#viewer@user:anne[in_window]"


def test_object_id_encoded_when_type_listed(tmp_path):
    idmap = mapfile(tmp_path, id_encoding={"mode": "base64url", "types": ["user"]})
    t = {"user": "user:alice@corp.com", "relation": "viewer", "object": "document:1"}
    got = transform_tuple(t, idmap)
    assert "alice@corp.com" not in got
    assert got.startswith("document:1#viewer@user:")


def test_rejects_illegal_object_id_when_unencoded(tmp_path):
    t = {"user": "user:alice@corp.com", "relation": "viewer", "object": "document:1"}
    # Brief's transcription used `match="not a valid SpiceDB object id"`, a
    # paraphrase. The actual raiser (validation_gen._check_object_id, reused
    # here via `relationship_string` rather than re-derived -- see
    # tuple_transform.py's module docstring) names the id and the pattern:
    # "subject id 'alice@corp.com' does not satisfy SpiceDB's object-id
    # pattern '...'". Matching that real message instead of the brief's
    # paraphrase, per task instructions ("match your implementation's actual
    # message ... or adjust the assertion ... say which you did and why").
    with pytest.raises(ValueError, match="does not satisfy SpiceDB's object-id pattern"):
        transform_tuple(t, mapfile(tmp_path))
