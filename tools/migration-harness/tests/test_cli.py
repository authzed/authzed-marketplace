import json
import pathlib

import pytest

from migration_harness.cli import main, run_check

_VALID_STORE = """
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        object: doc:1
        assertions:
          reader: true
"""

_VALID_CONVERTED = """
schema: |
  definition user {}
assertions:
  assertTrue:
    - "doc:1#read@user:anne"
  assertFalse: []
"""

_VALID_MAPPING = json.dumps({
    "types": {}, "permissions": {"doc": {"reader": "read"}},
    "id_encoding": {"mode": "none", "types": []},
})


def test_run_check_reports_ok_for_a_faithful_conversion(tmp_path):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        object: doc:1
        assertions:
          reader: true
""")
    converted = tmp_path / "validation.yaml"
    converted.write_text("""
schema: |
  definition user {}
assertions:
  assertTrue:
    - "doc:1#read@user:anne"
  assertFalse: []
""")
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(json.dumps({
        "types": {}, "permissions": {"doc": {"reader": "read"}},
        "id_encoding": {"mode": "none", "types": []},
    }))

    report = run_check(store, converted, mapping)
    assert report.ok


def test_run_check_flags_a_dropped_assertion(tmp_path):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tests:
  - name: t1
    check:
      - users: [user:anne, user:bob]
        object: doc:1
        assertions:
          read: true
""")
    converted = tmp_path / "validation.yaml"
    converted.write_text("""
schema: |
  definition user {}
assertions:
  assertTrue:
    - "doc:1#read@user:anne"
""")
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
    }))

    report = run_check(store, converted, mapping)
    assert not report.ok
    assert [a.subject_id for a in report.missing] == ["bob"]


def test_main_returns_3_for_a_nonexistent_converted_path(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store.fga.yaml"
    store.write_text(_VALID_STORE)
    converted = tmp_path / "does-not-exist.yaml"  # never created
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(_VALID_MAPPING)

    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
        "--skip-zed",
    ])

    assert main() == 3
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(converted) in captured.err


def test_main_returns_3_for_malformed_json_map(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store.fga.yaml"
    store.write_text(_VALID_STORE)
    converted = tmp_path / "validation.yaml"
    converted.write_text(_VALID_CONVERTED)
    mapping = tmp_path / "migration-map.json"
    mapping.write_text("{not valid json")

    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
        "--skip-zed",
    ])

    assert main() == 3
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(mapping) in captured.err


def test_main_returns_3_for_malformed_yaml_store(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tests: [
  - broken
""")
    converted = tmp_path / "validation.yaml"
    converted.write_text(_VALID_CONVERTED)
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(_VALID_MAPPING)

    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
        "--skip-zed",
    ])

    assert main() == 3
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err


def test_main_returns_1_for_a_genuine_parity_failure(tmp_path, monkeypatch):
    store = tmp_path / "store.fga.yaml"
    store.write_text("""
name: T
tests:
  - name: t1
    check:
      - users: [user:anne, user:bob]
        object: doc:1
        assertions:
          read: true
""")
    converted = tmp_path / "validation.yaml"
    converted.write_text("""
schema: |
  definition user {}
assertions:
  assertTrue:
    - "doc:1#read@user:anne"
""")
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(json.dumps({
        "types": {}, "permissions": {},
        "id_encoding": {"mode": "none", "types": []},
    }))

    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
        "--skip-zed",
    ])

    assert main() == 1


def test_main_returns_0_for_a_clean_run(tmp_path, monkeypatch):
    store = tmp_path / "store.fga.yaml"
    store.write_text(_VALID_STORE)
    converted = tmp_path / "validation.yaml"
    converted.write_text(_VALID_CONVERTED)
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(_VALID_MAPPING)

    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
        "--skip-zed",
    ])

    assert main() == 0


# --- exit-code contract for a bad --converted (S4) ---------------------------
#
# `zed validate` ran before any input was opened, so a typo'd --converted
# came back as 2 -- the one code the corpus-hardening loop reads as "the
# conversion is wrong" -- instead of the documented 3. --store and --map were
# already correct because they are only touched after zed runs.


def test_main_returns_3_for_a_nonexistent_converted_path_without_skip_zed(
    tmp_path, monkeypatch, capsys
):
    store = tmp_path / "store.fga.yaml"
    store.write_text(_VALID_STORE)
    converted = tmp_path / "typo.yaml"  # never created
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(_VALID_MAPPING)

    called = []
    monkeypatch.setattr(
        "migration_harness.cli.run_zed_validate",
        lambda p: called.append(p) or (False, "should never run"),
    )
    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
    ])

    assert main() == 3
    assert called == []  # inputs are stat'd before zed is invoked
    assert str(converted) in capsys.readouterr().err


def test_main_returns_3_for_a_nonexistent_store_without_skip_zed(
    tmp_path, monkeypatch, capsys
):
    store = tmp_path / "typo.fga.yaml"  # never created
    converted = tmp_path / "validation.yaml"
    converted.write_text(_VALID_CONVERTED)
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(_VALID_MAPPING)

    monkeypatch.setattr(
        "migration_harness.cli.run_zed_validate",
        lambda p: (False, "should never run"),
    )
    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
    ])

    assert main() == 3
    assert str(store) in capsys.readouterr().err


# --- content-validation errors are input errors, not tracebacks (S5) ---------
#
# `_effective`, `parse_object_ref` and `parse_assertion_string` all raise on
# malformed *content* rather than malformed YAML. The CLI caught only OSError
# and yaml.YAMLError, so these printed a traceback and exited 1 -- which is
# also the code for a genuine parity failure.


def _run(tmp_path, monkeypatch, store_text, converted_text, map_text):
    store = tmp_path / "store.fga.yaml"
    store.write_text(store_text)
    converted = tmp_path / "validation.yaml"
    converted.write_text(converted_text)
    mapping = tmp_path / "migration-map.json"
    mapping.write_text(map_text)
    monkeypatch.setattr("sys.argv", [
        "migration-harness",
        "--store", str(store),
        "--converted", str(converted),
        "--map", str(mapping),
        "--skip-zed",
    ])
    return main(), store, converted, mapping


def test_main_returns_3_for_a_check_block_with_both_user_and_users(
    tmp_path, monkeypatch, capsys
):
    rc, store, _, _ = _run(tmp_path, monkeypatch, """
name: T
tests:
  - name: t1
    check:
      - user: user:anne
        users: [user:bob]
        object: doc:1
        assertions:
          reader: true
""", _VALID_CONVERTED, _VALID_MAPPING)

    assert rc == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "cannot contain both" in err
    assert str(store) in err


def test_main_returns_3_for_an_unparseable_object_reference(
    tmp_path, monkeypatch, capsys
):
    rc, store, _, _ = _run(tmp_path, monkeypatch, """
name: T
tests:
  - name: t1
    check:
      - user: anne
        object: doc:1
        assertions:
          reader: true
""", _VALID_CONVERTED, _VALID_MAPPING)

    assert rc == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(store) in err


def test_main_returns_3_for_an_unparseable_assertion_string(
    tmp_path, monkeypatch, capsys
):
    rc, _, converted, _ = _run(tmp_path, monkeypatch, _VALID_STORE, """
schema: |
  definition user {}
assertions:
  assertTrue:
    - "doc:1#read-with-no-subject"
""", _VALID_MAPPING)

    assert rc == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(converted) in err


def test_main_returns_3_for_a_map_that_merges_two_names(
    tmp_path, monkeypatch, capsys
):
    rc, _, _, mapping = _run(tmp_path, monkeypatch, _VALID_STORE, _VALID_CONVERTED,
        json.dumps({
            "types": {},
            "permissions": {"doc": {"reader": "read", "read": "read"}},
            "id_encoding": {"mode": "none", "types": []},
        }))

    assert rc == 3
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert str(mapping) in err


def test_main_still_raises_a_genuine_harness_bug_rather_than_reporting_3(
    tmp_path, monkeypatch
):
    # Narrowing the catch to InputError is the whole point: a ValueError from
    # anywhere else is a defect in the harness and must not be relabelled an
    # operator input error.
    def boom(_path):
        raise ValueError("harness defect")

    monkeypatch.setattr("migration_harness.cli.load_fga_assertions", boom)
    with pytest.raises(ValueError, match="harness defect"):
        _run(tmp_path, monkeypatch, _VALID_STORE, _VALID_CONVERTED, _VALID_MAPPING)


# --- a run that compares nothing is not a pass (S2) --------------------------


def test_main_returns_1_when_nothing_was_compared(tmp_path, monkeypatch, capsys):
    # A store whose only test block is a `list_users` oracle: the harness
    # drops it, compares nothing, and previously exited 0.
    rc, _, _, _ = _run(tmp_path, monkeypatch, """
name: T
tests:
  - name: t1
    list_users:
      - object: doc:1
        user_filter:
          - type: user
        assertions:
          reader:
            users: [user:anne]
""", """
schema: |
  definition user {}
assertions:
  assertTrue: []
  assertFalse: []
""", _VALID_MAPPING)

    assert rc == 1
    out = capsys.readouterr()
    assert "NOTHING COMPARED" in out.out
    assert "0 assertions compared" in out.err


def test_main_reports_the_compared_count_on_a_clean_run(
    tmp_path, monkeypatch, capsys
):
    rc, _, _, _ = _run(
        tmp_path, monkeypatch, _VALID_STORE, _VALID_CONVERTED, _VALID_MAPPING
    )
    assert rc == 0
    assert "PARITY OK (1 assertions compared)" in capsys.readouterr().err
