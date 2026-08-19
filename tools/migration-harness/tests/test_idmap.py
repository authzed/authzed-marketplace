import base64
import json
import pathlib

import pytest

from migration_harness.idmap import IdMap, encode_id, normalize_name
from migration_harness.model import Assertion, InputError


def test_normalize_lowercases_and_replaces_separators():
    assert normalize_name("My-Doc") == "my_doc"
    assert normalize_name("a.b/c") == "a_b_c"


def test_normalize_pads_short_names_to_three_chars():
    # SpiceDB requires >= 3 characters
    assert len(normalize_name("u")) >= 3
    assert len(normalize_name("ab")) >= 3


def test_normalize_strips_leading_and_trailing_underscores():
    got = normalize_name("_internal_")
    assert not got.startswith("_")
    assert not got.endswith("_")


def test_normalize_truncates_long_names_with_hash_suffix():
    got = normalize_name("a" * 200)
    assert len(got) <= 64
    assert got != "a" * 64  # a hash suffix disambiguates


def test_normalize_output_matches_spicedb_regex():
    import re
    pattern = re.compile(r"^[a-z][a-z0-9_]{1,62}[a-z0-9]$")
    for raw in ["My-Doc", "u", "a.b/c", "_x_", "a" * 200, "9lives"]:
        assert pattern.match(normalize_name(raw)), raw


def test_encode_id_base64url_is_reversible_and_charset_safe():
    import re
    raw = "alice@corp.com"
    enc = encode_id(raw, "base64url")
    assert re.match(r"^[a-zA-Z0-9/_|\-=+]{1,1024}$", enc)
    assert base64.urlsafe_b64decode(enc.encode()).decode() == raw


def test_encode_id_none_is_identity():
    assert encode_id("alice", "none") == "alice"


def test_apply_maps_types_and_permissions(tmp_path):
    p = tmp_path / "migration-map.json"
    p.write_text(json.dumps({
        "types": {"repo": "repository"},
        "permissions": {"repo": {"reader": "read"}},
        "id_encoding": {"mode": "none", "types": []},
    }))
    m = IdMap.load(p)
    got = m.apply(Assertion("user", "anne", "", "reader", "repo", "r1", True, ""))
    assert got.resource_type == "repository"
    assert got.permission == "read"
    assert got.subject_type == "user"


def test_apply_encodes_only_listed_types(tmp_path):
    p = tmp_path / "migration-map.json"
    p.write_text(json.dumps({
        "types": {},
        "permissions": {},
        "id_encoding": {"mode": "base64url", "types": ["user"]},
    }))
    m = IdMap.load(p)
    got = m.apply(Assertion("user", "a@b.com", "", "view", "doc", "d/1", True, ""))
    assert got.subject_id == encode_id("a@b.com", "base64url")
    assert got.resource_id == "d/1"  # doc not listed, left alone


def test_apply_leaves_wildcard_subject_id_untouched(tmp_path):
    p = tmp_path / "migration-map.json"
    p.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "base64url", "types": ["user"]},
    }))
    m = IdMap.load(p)
    got = m.apply(Assertion("user", "*", "", "view", "doc", "d1", True, ""))
    assert got.subject_id == "*"


def test_load_rejects_unknown_encoding_mode(tmp_path):
    p = tmp_path / "migration-map.json"
    p.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "rot13", "types": []},
    }))
    with pytest.raises(ValueError, match="unknown id encoding mode"):
        IdMap.load(p)


# --- IdMap.build: collision-resistant registry (Fix round 1, Finding 1) ---


def test_build_disambiguates_relation_collision_on_same_type():
    m = IdMap.build(["doc"], {"doc": ["can-edit", "can_edit"]})
    got = {m.permissions["doc"]["can-edit"], m.permissions["doc"]["can_edit"]}
    assert len(got) == 2


def test_build_disambiguates_type_collision():
    m = IdMap.build(["MyDoc", "mydoc"], {})
    got = {m.types["MyDoc"], m.types["mydoc"]}
    assert len(got) == 2


def test_build_does_not_disambiguate_same_relation_across_different_types():
    m = IdMap.build(["repo", "doc"], {"repo": ["viewer"], "doc": ["viewer"]})
    assert m.permissions["repo"]["viewer"] == "viewer"
    assert m.permissions["doc"]["viewer"] == "viewer"


def test_build_disambiguated_names_still_match_spicedb_regex():
    import re
    pattern = re.compile(r"^[a-z][a-z0-9_]{1,62}[a-z0-9]$")
    m = IdMap.build(
        ["MyDoc", "mydoc", "MYDOC"],
        {"MyDoc": ["can-edit", "can_edit", "CAN_EDIT"]},
    )
    for name in m.types.values():
        assert pattern.match(name), name
    for name in m.permissions["MyDoc"].values():
        assert pattern.match(name), name


def test_build_is_deterministic():
    types = ["MyDoc", "mydoc"]
    relations = {"MyDoc": ["can-edit", "can_edit"], "mydoc": ["viewer"]}
    first = IdMap.build(types, relations)
    second = IdMap.build(types, relations)
    assert first.types == second.types
    assert first.permissions == second.permissions


def test_build_two_distinct_inputs_never_collide():
    # A broad batch of distinct-but-normalize-alike names, none of which may
    # collapse onto the same SpiceDB name.
    raw_types = [
        "MyDoc", "mydoc", "MYDOC", "my-doc", "my.doc", "my_doc",
        "a" * 200, "A" * 200, "_internal", "internal", "9lives", "u", "ab",
    ]
    m = IdMap.build(raw_types, {})
    normalized = list(m.types.values())
    assert len(normalized) == len(set(normalized))


# --- IdMap.apply: subject_relation remapping (Fix round 1, Finding 2) ---


def test_apply_remaps_subject_relation(tmp_path):
    p = tmp_path / "migration-map.json"
    p.write_text(json.dumps({
        "types": {},
        "permissions": {"team": {"member": "participant"}},
        "id_encoding": {"mode": "none", "types": []},
    }))
    m = IdMap.load(p)
    got = m.apply(Assertion("team", "eng", "member", "view", "doc", "d1", True, ""))
    assert got.subject_relation == "participant"


def test_apply_leaves_empty_subject_relation_untouched(tmp_path):
    p = tmp_path / "migration-map.json"
    p.write_text(json.dumps({
        "types": {},
        "permissions": {"team": {"member": "participant"}},
        "id_encoding": {"mode": "none", "types": []},
    }))
    m = IdMap.load(p)
    got = m.apply(Assertion("user", "anne", "", "view", "doc", "d1", True, ""))
    assert got.subject_relation == ""


# --- encode_id: object-id regex bounds (Fix round 1, Finding 3) ---


def test_encode_id_rejects_empty_value():
    with pytest.raises(ValueError, match="empty"):
        encode_id("", "none")
    with pytest.raises(ValueError, match="empty"):
        encode_id("", "base64url")


def test_encode_id_base64url_rejects_output_over_1024_chars():
    # 800 bytes base64url-encodes to well over 1024 characters.
    with pytest.raises(ValueError, match="1024"):
        encode_id("a" * 800, "base64url")


def test_encode_id_base64url_allows_max_size_output():
    # 768 bytes base64url-encodes to exactly 1024 characters, the max allowed.
    enc = encode_id("a" * 768, "base64url")
    assert len(enc) == 1024


# --- migration-map.json injectivity (S1) -------------------------------------
#
# `build` cannot be the only guard: the /spicedb-dev:migrate-schema agent has
# no Bash tool, so every map that reaches production is written by hand or by
# the model and enters through `load`. A map that merges two source names
# rewrites both onto one key, `parity._dedupe` collapses them as a duplicate,
# and the run reports PARITY OK having silently dropped one of the source's
# questions.


def _write_map(tmp_path: pathlib.Path, doc: dict) -> pathlib.Path:
    p = tmp_path / "migration-map.json"
    p.write_text(json.dumps(doc))
    return p


def test_load_rejects_two_permissions_merging_onto_one_name(tmp_path):
    path = _write_map(tmp_path, {
        "types": {},
        "permissions": {"doc": {"can-edit": "can_edit", "can_edit": "can_edit"}},
        "id_encoding": {"mode": "none", "types": []},
    })

    with pytest.raises(InputError) as exc:
        IdMap.load(path)

    assert "can_edit" in str(exc.value)
    assert "can-edit" in str(exc.value)


def test_load_rejects_two_types_merging_onto_one_name(tmp_path):
    path = _write_map(tmp_path, {
        "types": {"My-Doc": "my_doc", "my.doc": "my_doc"},
        "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
    })

    with pytest.raises(InputError):
        IdMap.load(path)


def test_load_allows_the_same_relation_name_on_two_types(tmp_path):
    # SpiceDB scopes relation/permission names to one definition, so `viewer`
    # on two types is not a collision -- rejecting it would corrupt both.
    path = _write_map(tmp_path, {
        "types": {"doc": "doc", "folder": "folder"},
        "permissions": {
            "doc": {"viewer": "viewer"},
            "folder": {"viewer": "viewer"},
        },
        "id_encoding": {"mode": "none", "types": []},
    })

    idmap = IdMap.load(path)
    assert idmap.permissions["doc"]["viewer"] == "viewer"
    assert idmap.permissions["folder"]["viewer"] == "viewer"


def test_load_allows_an_identity_mapping(tmp_path):
    # Every name mapping to itself is injective; the check must not fire on
    # the overwhelmingly common case.
    path = _write_map(tmp_path, {
        "types": {"doc": "doc"},
        "permissions": {"doc": {"viewer": "viewer", "editor": "editor"}},
        "id_encoding": {"mode": "none", "types": []},
    })
    assert IdMap.load(path).types == {"doc": "doc"}


def test_build_output_always_survives_loads_injectivity_check(tmp_path):
    # The two halves of the contract must agree: anything `build` produces
    # must be something `load` accepts.
    built = IdMap.build(
        ["My-Doc", "my.doc"],
        {"My-Doc": ["can-edit", "can_edit"], "my.doc": []},
    )
    path = _write_map(tmp_path, {
        "types": built.types,
        "permissions": built.permissions,
        "id_encoding": {"mode": built.encoding_mode, "types": []},
    })

    reloaded = IdMap.load(path)
    assert reloaded.types == built.types
    assert reloaded.permissions == built.permissions


# --- migration-map.json `relation_splits` (write-target names) --------------
#
# A split `define` (a `[...]` type list fused with an operator) produces two
# SpiceDB names: the permission keeps the source name (what `apply` and
# `permissions[type]` already carry, for the check surface), and a generated
# `__direct` relation the *write* path must use instead -- SpiceDB rejects a
# relationship write to a permission outright. Nothing machine-readable
# recorded that second name before; `relation_splits` does, and
# `write_relation` is the accessor that resolves it. `apply` must not change
# at all: every committed corpus-runs/<store>/migration-map.json still has no
# `relation_splits` key, and every one of them must keep loading and keep
# producing identical `apply` output.


def test_load_defaults_relation_splits_to_empty_when_key_absent(tmp_path):
    # Backward compatibility: every map committed before this key existed
    # has no `relation_splits` entry at all.
    path = _write_map(tmp_path, {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "member"}},
        "id_encoding": {"mode": "none", "types": []},
    })
    m = IdMap.load(path)
    assert m.relation_splits == {}


def test_write_relation_resolves_split_relation_to_write_target(tmp_path):
    path = _write_map(tmp_path, {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "member"}},
        "relation_splits": {
            "organization": {
                "member": {"relation": "member__direct", "permission": "member"}
            }
        },
        "id_encoding": {"mode": "none", "types": []},
    })
    m = IdMap.load(path)
    assert m.write_relation("organization", "member") == "member__direct"


def test_write_relation_resolves_non_split_relation_to_ordinary_mapped_name(
    tmp_path,
):
    # `owner` never split on this type: no `relation_splits` entry for it,
    # even though the type has other relations that did split.
    path = _write_map(tmp_path, {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "member", "owner": "owner"}},
        "relation_splits": {
            "organization": {
                "member": {"relation": "member__direct", "permission": "member"}
            }
        },
        "id_encoding": {"mode": "none", "types": []},
    })
    m = IdMap.load(path)
    assert m.write_relation("organization", "owner") == "owner"


def test_write_relation_falls_back_when_relation_splits_key_absent(tmp_path):
    path = _write_map(tmp_path, {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "participant"}},
        "id_encoding": {"mode": "none", "types": []},
    })
    m = IdMap.load(path)
    assert m.write_relation("organization", "member") == "participant"


def test_write_relation_leaves_empty_relation_untouched(tmp_path):
    path = _write_map(tmp_path, {
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
    })
    m = IdMap.load(path)
    assert m.write_relation("user", "") == ""


def test_load_rejects_two_split_relations_sharing_one_write_target(tmp_path):
    # `member` and `owner` both (erroneously) generate `member__direct` --
    # two distinct source relations must not share one SpiceDB name, exactly
    # the same rule `permissions` is already held to.
    path = _write_map(tmp_path, {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "member", "owner": "owner"}},
        "relation_splits": {
            "organization": {
                "member": {"relation": "member__direct", "permission": "member"},
                "owner": {"relation": "member__direct", "permission": "owner"},
            }
        },
        "id_encoding": {"mode": "none", "types": []},
    })
    with pytest.raises(InputError) as exc:
        IdMap.load(path)
    assert "member__direct" in str(exc.value)
    assert "member" in str(exc.value)
    assert "owner" in str(exc.value)


def test_load_rejects_split_write_target_colliding_with_unrelated_permission(
    tmp_path,
):
    # `viewer` splits and generates `viewer__direct` -- which collides with
    # an unrelated, real source relation on the same type that already
    # normalized to `viewer__direct`. schema-mapping.md calls this out by
    # name: the collision "must be caught, not assumed away."
    path = _write_map(tmp_path, {
        "types": {"doc": "doc"},
        "permissions": {
            "doc": {"viewer": "viewer", "viewer__direct": "viewer__direct"}
        },
        "relation_splits": {
            "doc": {"viewer": {"relation": "viewer__direct", "permission": "viewer"}}
        },
        "id_encoding": {"mode": "none", "types": []},
    })
    with pytest.raises(InputError) as exc:
        IdMap.load(path)
    assert "viewer__direct" in str(exc.value)


def test_load_rejects_malformed_relation_split_entry(tmp_path):
    path = _write_map(tmp_path, {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "member"}},
        "relation_splits": {"organization": {"member": {"relation": "member__direct"}}},
        "id_encoding": {"mode": "none", "types": []},
    })
    with pytest.raises(InputError):
        IdMap.load(path)


def test_load_allows_the_same_split_relation_name_on_two_types(tmp_path):
    # Mirrors test_load_allows_the_same_relation_name_on_two_types: relation
    # names -- split or not -- are scoped per definition, not global.
    path = _write_map(tmp_path, {
        "types": {"org": "org", "team": "team"},
        "permissions": {
            "org": {"member": "member"},
            "team": {"member": "member"},
        },
        "relation_splits": {
            "org": {"member": {"relation": "member__direct", "permission": "member"}},
            "team": {"member": {"relation": "member__direct", "permission": "member"}},
        },
        "id_encoding": {"mode": "none", "types": []},
    })
    m = IdMap.load(path)
    assert m.write_relation("org", "member") == "member__direct"
    assert m.write_relation("team", "member") == "member__direct"


def test_apply_output_unchanged_when_relation_splits_present(tmp_path):
    # The regression that matters most: `relation_splits` must be inert as
    # far as `apply` is concerned. Assertions check the permission, and the
    # permission keeps its original, unsuffixed name whether or not this map
    # also happens to record the write-target relation.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    without_splits = _write_map(tmp_path / "a", {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "member"}},
        "id_encoding": {"mode": "none", "types": []},
    })
    with_splits = _write_map(tmp_path / "b", {
        "types": {"organization": "organization"},
        "permissions": {"organization": {"member": "member"}},
        "relation_splits": {
            "organization": {
                "member": {"relation": "member__direct", "permission": "member"}
            }
        },
        "id_encoding": {"mode": "none", "types": []},
    })

    assertion = Assertion(
        "organization", "eng", "member", "member", "organization", "o1", True, ""
    )
    got_without = IdMap.load(without_splits).apply(assertion)
    got_with = IdMap.load(with_splits).apply(assertion)
    assert got_without == got_with
    assert got_with.permission == "member"
    assert got_with.subject_relation == "member"
