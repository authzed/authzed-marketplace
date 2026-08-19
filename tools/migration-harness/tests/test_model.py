import pytest

from migration_harness.model import Assertion, parse_object_ref


def test_parse_plain_object_ref():
    assert parse_object_ref("user:anne") == ("user", "anne", "")


def test_parse_object_ref_with_subject_relation():
    assert parse_object_ref("organization:openfga#member") == (
        "organization",
        "openfga",
        "member",
    )


def test_parse_object_ref_allows_slash_in_id():
    # Real sample-stores data: repo:openfga/openfga, team:openfga/core
    assert parse_object_ref("repo:openfga/openfga") == ("repo", "openfga/openfga", "")


def test_parse_object_ref_allows_wildcard():
    assert parse_object_ref("user:*") == ("user", "*", "")


def test_parse_object_ref_rejects_missing_colon():
    with pytest.raises(ValueError, match="not a valid object reference"):
        parse_object_ref("anne")


def test_assertion_is_hashable():
    a = Assertion("user","anne","","view","doc","1",True,"")
    b = Assertion("user","anne","","view","doc","1",True,"")
    assert a == b
    assert len({a, b}) == 1


def test_assertion_orders_by_field_sequence():
    anne = Assertion("user","anne","","view","doc","1",True,"")
    bob  = Assertion("user","bob", "","view","doc","1",True,"")
    assert anne < bob
    assert sorted([bob, anne])[0] == anne
