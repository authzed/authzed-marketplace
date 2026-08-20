import json
import pathlib
import random

from migration_harness.idmap import IdMap
from migration_harness.model import Assertion
from migration_harness.parity import compare


def identity_map(tmp_path: pathlib.Path) -> IdMap:
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
    }))
    return IdMap.load(p)


A = Assertion("user", "anne", "", "view", "doc", "1", True, "")
A_FALSE = Assertion("user", "anne", "", "view", "doc", "1", False, "")
B = Assertion("user", "bob", "", "view", "doc", "1", False, "")


def test_identical_sets_are_ok(tmp_path):
    r = compare([A, B], [A, B], identity_map(tmp_path), [])
    assert r.ok
    assert r.missing == [] and r.extra == [] and r.contradictions == []


def test_missing_assertion_is_reported(tmp_path):
    r = compare([A, B], [A], identity_map(tmp_path), [])
    assert not r.ok
    assert r.missing == [B]


def test_extra_assertion_is_reported(tmp_path):
    r = compare([A], [A, B], identity_map(tmp_path), [])
    assert not r.ok
    assert r.extra == [B]


def test_opposite_answer_is_a_contradiction_not_a_missing_pair(tmp_path):
    r = compare([A], [A_FALSE], identity_map(tmp_path), [])
    assert not r.ok
    assert r.contradictions == [(A, A_FALSE)]
    assert r.missing == [] and r.extra == []


def test_idmap_is_applied_to_the_fga_side(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "types": {"doc": "document"},
        "permissions": {"doc": {"view": "read"}},
        "id_encoding": {"mode": "none", "types": []},
    }))
    mapped = Assertion("user", "anne", "", "read", "document", "1", True, "")
    r = compare([A], [mapped], IdMap.load(p), [])
    assert r.ok


def test_caveated_entries_are_carried_but_do_not_fail_parity(tmp_path):
    r = compare([A], [A], identity_map(tmp_path), ["doc:1#view@user:sarah"])
    assert r.ok
    assert r.caveated == ["doc:1#view@user:sarah"]


def test_fga_side_duplicate_with_conflicting_answers_is_ambiguous(tmp_path):
    r = compare([A, A_FALSE], [A], identity_map(tmp_path), [])
    assert not r.ok
    assert r.ambiguous == [(A, A_FALSE)]
    assert r.missing == [] and r.extra == [] and r.contradictions == []


def test_spicedb_side_duplicate_with_conflicting_answers_is_ambiguous(tmp_path):
    r = compare([A], [A, A_FALSE], identity_map(tmp_path), [])
    assert not r.ok
    assert r.ambiguous == [(A, A_FALSE)]
    assert r.missing == [] and r.extra == [] and r.contradictions == []


def test_same_side_duplicate_with_same_answer_is_harmless(tmp_path):
    r = compare([A, A], [A], identity_map(tmp_path), [])
    assert r.ok
    assert r.ambiguous == []


def test_ambiguous_question_isolated_from_unrelated_assertions(tmp_path):
    r = compare([A, A_FALSE, B], [A, B], identity_map(tmp_path), [])
    assert not r.ok
    assert r.ambiguous == [(A, A_FALSE)]
    assert r.missing == [] and r.extra == [] and r.contradictions == []


def test_ambiguous_ordering_is_deterministic(tmp_path):
    c_true = Assertion("user", "carol", "", "view", "doc", "1", True, "")
    c_false = Assertion("user", "carol", "", "view", "doc", "1", False, "")
    d_true = Assertion("user", "dave", "", "view", "doc", "1", True, "")
    d_false = Assertion("user", "dave", "", "view", "doc", "1", False, "")
    fga = [d_true, d_false, A, A_FALSE, c_true, c_false]
    random.shuffle(fga)
    r = compare(fga, [], identity_map(tmp_path), [])
    assert len(r.ambiguous) == 3
    assert r.ambiguous == sorted(r.ambiguous)


# --- zero compared assertions is not parity (S2) -----------------------------
#
# Every other field this report carries is an *absence*, and absences are
# vacuously satisfied by an empty input. A store whose oracle is entirely
# `list_objects` / `list_users` -- or one with an empty `tests:` -- otherwise
# reaches `ok` having examined no evidence at all. Across the shipped corpus
# the harness sees 82.9% of source assertions (97/117) and as little as 50%
# on `ip-based-access`, so the zero case is the endpoint of a real gradient,
# not a hypothetical.


def test_two_empty_sides_are_not_ok(tmp_path):
    r = compare([], [], identity_map(tmp_path), [])
    assert r.compared == 0
    assert not r.ok


def test_caveated_only_run_compares_nothing(tmp_path):
    # assertCaveated is carried through unexamined, so it is not evidence.
    r = compare([], [], identity_map(tmp_path), ["doc:1#view@user:anne"])
    assert r.compared == 0
    assert not r.ok


def test_compared_counts_questions_both_sides_answered(tmp_path):
    r = compare([A, B], [A, B], identity_map(tmp_path), [])
    assert r.compared == 2
    assert r.ok


def test_compared_excludes_questions_only_one_side_asks(tmp_path):
    r = compare([A, B], [A], identity_map(tmp_path), [])
    assert r.compared == 1  # B was never compared, only reported missing
    assert not r.ok


def test_compared_excludes_ambiguous_questions(tmp_path):
    # An ambiguous key is dropped from both sides before comparison, so it
    # must not be counted as evidence either.
    r = compare([A, A_FALSE], [A], identity_map(tmp_path), [])
    assert r.compared == 0
    assert not r.ok
