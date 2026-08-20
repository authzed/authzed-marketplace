import json
import pathlib

import pytest
import yaml

from migration_harness.idmap import IdMap
from migration_harness.validation_gen import generate_validation, relationship_string
from migration_harness.model import Assertion, InputError


def identity_map(tmp_path: pathlib.Path) -> IdMap:
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
    }))
    return IdMap.load(p)


def test_relationship_string_plain():
    a = Assertion("user", "anne", "", "viewer", "document", "1", True, "")
    assert relationship_string(a) == "document:1#viewer@user:anne"


def test_relationship_string_with_subject_relation():
    a = Assertion("group", "eng", "member", "viewer", "document", "1", True, "")
    assert relationship_string(a) == "document:1#viewer@group:eng#member"


def test_generates_schema_file_reference(tmp_path):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
model: |
  model
    schema 1.1
  type user
tuples: []
tests: []
""")
    out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    assert "schemaFile: schema.zed" in out


def test_true_and_false_assertions_split(tmp_path):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples:
  - user: user:anne
    relation: viewer
    object: document:1
tests:
  - name: t
    check:
      - user: user:anne
        object: document:1
        assertions:
          view: true
          edit: false
""")
    out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    assertions = yaml.safe_load(out)["assertions"]
    assert "document:1#view@user:anne" in assertions["assertTrue"]
    assert "document:1#edit@user:anne" in assertions["assertFalse"]
    # Not just present somewhere -- specifically absent from the *other*
    # bucket, so a true/false-routing inversion (view -> assertFalse,
    # edit -> assertTrue) cannot pass by both substrings merely existing
    # in the document.
    assert "document:1#view@user:anne" not in assertions["assertFalse"]
    assert "document:1#edit@user:anne" not in assertions["assertTrue"]


def test_check_context_becomes_with_suffix(tmp_path):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples: []
tests:
  - name: t
    check:
      - user: user:anne
        object: document:1
        context:
          b: 2
          a: 1
        assertions:
          view: true
""")
    out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    assert 'document:1#view@user:anne with {"a":1,"b":2}' in out


def test_tuple_relation_uses_write_side_name(tmp_path):
    # A split relation: writes target member__direct, checks target member.
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
        "relation_splits": {
            "org": {"member": {"relation": "member__direct", "permission": "member"}}
        },
    }))
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples:
  - user: user:anne
    relation: member
    object: org:acme
tests: []
""")
    out = generate_validation(store, IdMap.load(p), "schema.zed")
    assert "org:acme#member__direct@user:anne" in out
    assert "org:acme#member@user:anne" not in out


# --- Additions beyond the brief's verbatim test list ---
#
# The brief's implementation notes require two things it doesn't hand over
# tests for: (1) list_objects/list_users must be recorded, not silently
# dropped, and (2) emitted object ids must satisfy SpiceDB's object-id
# pattern. A third (tuple `condition:` -> caveat suffix) isn't mentioned at
# all, but real sample stores (e.g. banking) use it and the corpus's own
# validation.yaml already records the resulting convention, so dropping it
# silently would repeat the exact mistake the brief warns against for
# list_objects/list_users. A fourth pins the other half of the write/check
# split -- the brief's own test only pins the tuple/write side, so an
# inverted bug on the check/assertion side (using write_relation instead of
# the ordinary permission mapping) passed every test in this file until this
# one was added. Covered here so a later change can't regress any of the
# four unnoticed.


def test_check_assertion_uses_check_side_name(tmp_path):
    # Same split-relation fixture as test_tuple_relation_uses_write_side_name,
    # but exercised from a check: block instead of a tuples: entry. Pins the
    # other half of the write/check split: an assertion against a split
    # relation must target the permission (`member`), never the write-only
    # `member__direct` relation -- SpiceDB checks evaluate permissions, not
    # the raw stored relation, and `member__direct` isn't even the name the
    # schema conversion exposes for checking. Discriminates a bug that swaps
    # idmap.apply for idmap.write_relation on the assertion path: verified by
    # temporarily making that swap in generate_validation and confirming this
    # test (only) fails.
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
        "relation_splits": {
            "org": {"member": {"relation": "member__direct", "permission": "member"}}
        },
    }))
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples: []
tests:
  - name: t
    check:
      - user: user:anne
        object: org:acme
        assertions:
          member: true
""")
    out = generate_validation(store, IdMap.load(p), "schema.zed")
    assert "org:acme#member@user:anne" in out
    assert "org:acme#member__direct@user:anne" not in out


def test_list_objects_and_list_users_recorded_not_dropped(tmp_path, caplog):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples: []
tests:
  - name: List objects for admin
    list_objects:
      - user: user:alice
        type: document
        assertions:
          can_view:
            - document:1
  - name: List users who can view doc
    list_users:
      - object: document:1
        user_filter:
          - type: user
        assertions:
          can_view:
            users:
              - user:alice
""")
    with caplog.at_level("WARNING"):
        out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    assert "list_objects" in out
    assert "list_users" in out
    assert any("list_objects" in r.message for r in caplog.records)
    assert any("list_users" in r.message for r in caplog.records)


def test_tuple_condition_becomes_caveat_suffix(tmp_path):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples:
  - user: bank:acme#customer
    relation: transfer_limit_policy
    object: bank:acme
    condition:
      name: transfer_limit_policy
      context:
        transaction_limit: 100
tests: []
""")
    out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    assert (
        "bank:acme#transfer_limit_policy@bank:acme#customer"
        '[transfer_limit_policy:{"transaction_limit":100}]'
    ) in out


# --- Fix round 1 additions: pin all_tuple_entries / dedupe_tuple_lines directly ---
#
# These three were added on review: the corpus fixture (condition-data-types)
# that originally forced all_tuple_entries and dedupe_tuple_lines into
# existence is not a specification, and the one store that would exercise the
# root+nested combined case (abac-with-rebac) is skipped in both parametrized
# corpus tests -- so before this addition, that combination had no coverage at
# all, corpus or unit.


def test_nested_tuples_only_are_included(tmp_path):
    # tuples: scoped inside a tests: block, with no root-level tuples: key at
    # all, must still be written -- this is condition-data-types' own shape
    # (18 tuple writes, zero at the document root), which generate_validation
    # silently dropped (empty relationships: block) before all_tuple_entries
    # existed.
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tests:
  - name: t
    tuples:
      - user: user:anne
        relation: viewer
        object: document:1
    check:
      - user: user:anne
        object: document:1
        assertions:
          viewer: true
""")
    out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    relationships = yaml.safe_load(out)["relationships"]
    assert "document:1#viewer@user:anne" in relationships


def test_root_and_nested_tuples_merge_root_first(tmp_path):
    # A root-level tuples: entry and a tests: block's own tuples: entry that
    # collide on the same (resource, relation, subject) triple must resolve
    # in favor of the root-level one -- pinning all_tuple_entries' documented
    # collection order (root-level entries first, then each tests: block in
    # file order), which dedupe_tuple_lines' first-seen-wins rule depends on.
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples:
  - user: bank:acme#customer
    relation: transfer_limit_policy
    object: bank:acme
    condition:
      name: policy_a
      context:
        transaction_limit: 100
tests:
  - name: t
    tuples:
      - user: bank:acme#customer
        relation: transfer_limit_policy
        object: bank:acme
        condition:
          name: policy_b
          context:
            transaction_limit: 200
    check: []
""")
    out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    relationships = yaml.safe_load(out)["relationships"]
    assert '[policy_a:{"transaction_limit":100}]' in relationships
    assert '[policy_b:{"transaction_limit":200}]' not in relationships


def test_nested_tuple_collision_keeps_first_seen_and_records_drop(tmp_path, caplog):
    # Two tests: blocks that each write their own tuples: can collide on the
    # same triple with different caveat bindings once merged into one shared
    # SpiceDB graph -- condition-data-types' own shape, at minimal scale.
    # zed validate's loader rejects the naive union outright ("found repeated
    # relationship") if both renderings are kept, so the generator must pick
    # one -- first-seen wins -- but per corpus-runs/README.md's
    # condition-data-types finding 3 and schema-mapping.md's "Multiple
    # isolated test fixtures colliding in one converted graph", the discarded
    # write must not vanish untraced: it has to be named in a
    # NOTE(spicedbmigration) comment (and logged), because the discarded
    # scenario's checks can still pass by coincidence rather than by
    # actually being verified.
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tests:
  - name: first block
    tuples:
      - user: bank:acme#customer
        relation: transfer_limit_policy
        object: bank:acme
        condition:
          name: policy_a
          context:
            transaction_limit: 100
    check: []
  - name: second block
    tuples:
      - user: bank:acme#customer
        relation: transfer_limit_policy
        object: bank:acme
        condition:
          name: policy_b
          context:
            transaction_limit: 200
    check: []
""")
    with caplog.at_level("WARNING"):
        out = generate_validation(store, identity_map(tmp_path), "schema.zed")
    relationships = yaml.safe_load(out)["relationships"]
    assert '[policy_a:{"transaction_limit":100}]' in relationships
    assert '[policy_b:{"transaction_limit":200}]' not in relationships
    # The drop must not vanish untraced: named in a NOTE(spicedbmigration)
    # comment (a YAML comment, invisible to yaml.safe_load -- check the raw
    # text) and logged.
    assert "NOTE(spicedbmigration)" in out and "policy_b" in out
    assert any("policy_b" in r.message for r in caplog.records)


def test_illegal_caveat_name_rejected(tmp_path):
    # A condition name is validated as an OpenFGA condition identifier at
    # model-definition time, which is looser than SpiceDB's caveat-name
    # grammar inside a relationship-string suffix
    # (^[a-z][a-z0-9_]{1,62}[a-z0-9]$). "C" fails both the lowercase-start
    # requirement and the length floor -- it must be rejected rather than
    # silently emitted (which would deploy as a legal `caveat` declaration
    # and then fail every relationship line referencing it) or silently
    # normalized (which would disagree with whatever name the schema side
    # gave the caveat).
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples:
  - user: user:anne
    relation: transfer_limit_policy
    object: bank:acme
    condition:
      name: C
      context:
        transaction_limit: 100
tests: []
""")
    with pytest.raises(InputError):
        generate_validation(store, identity_map(tmp_path), "schema.zed")


def test_invalid_object_id_rejected(tmp_path):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tuples:
  - user: user:anne
    relation: viewer
    object: 'document:my doc'
tests: []
""")
    with pytest.raises(InputError):
        generate_validation(store, identity_map(tmp_path), "schema.zed")
