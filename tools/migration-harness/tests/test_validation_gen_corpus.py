import pathlib
import shutil
import subprocess

import pytest

from migration_harness.idmap import IdMap
from migration_harness.spicedb_val import load_spicedb_assertions
from migration_harness.validation_gen import generate_validation

from corpus_support import (
    _JUDGMENT_SKIPS,
    _select_store_file,
    apply_caveat_param_renames,
)

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus-runs"
STORES = sorted(p.name for p in CORPUS.iterdir() if (p / "validation.yaml").exists())


@pytest.mark.parametrize("store", STORES)
def test_generated_assertions_match_committed(store, tmp_path):
    """The generator must reproduce the assertion set a human-followed pack produced."""
    if store in _JUDGMENT_SKIPS:
        pytest.skip(f"{store}: {_JUDGMENT_SKIPS[store]}")

    run = CORPUS / store
    src = CORPUS.parent / "corpus" / "sample-stores" / "stores" / store
    store_file = _select_store_file(src)
    if store_file is None:
        matches = sorted(p.name for p in src.glob("*.fga.yaml"))
        pytest.skip(
            f"{store} ships no unambiguous .fga.yaml store file "
            f"(no store.fga.yaml, {len(matches)} candidate(s): {matches})"
        )

    idmap = IdMap.load(run / "migration-map.json")
    generated = tmp_path / "generated.yaml"
    generated.write_text(generate_validation(store_file, idmap, "schema.zed"))

    want, _ = load_spicedb_assertions(run / "validation.yaml")
    got, _ = load_spicedb_assertions(generated)
    assert set(got) == set(want), f"{store}: assertion sets differ"


@pytest.mark.skipif(shutil.which("zed") is None, reason="zed not installed")
@pytest.mark.parametrize("store", STORES)
def test_generated_file_passes_zed_validate(store):
    """The relationships block must be writable against the store's own schema.

    Catches the failure the assertion-set test is blind to: a write aimed at a
    permission instead of its generated __direct relation, which zed rejects
    with `cannot write a relationship to permission ...`.
    """
    if store in _JUDGMENT_SKIPS:
        pytest.skip(f"{store}: {_JUDGMENT_SKIPS[store]}")

    run = CORPUS / store
    src = CORPUS.parent / "corpus" / "sample-stores" / "stores" / store
    store_file = _select_store_file(src)
    if store_file is None:
        matches = sorted(p.name for p in src.glob("*.fga.yaml"))
        pytest.skip(
            f"{store} ships no unambiguous .fga.yaml store file "
            f"(no store.fga.yaml, {len(matches)} candidate(s): {matches})"
        )

    idmap = IdMap.load(run / "migration-map.json")
    # zed resolves schemaFile relative to the validation file, so generate in place.
    generated = run / ".generated-for-test.yaml"
    try:
        text = generate_validation(store_file, idmap, "schema.zed")
        # See CAVEAT_PARAM_RENAMES: the generator passes a tuple's condition
        # context keys through unchanged from the OpenFGA source, so a store
        # whose own schema.zed renamed those keys needs the same translation
        # applied here before the generated text can validate against it.
        text = apply_caveat_param_renames(store, text)
        generated.write_text(text)
        proc = subprocess.run(
            ["zed", "validate", "--fail-on-warn", generated.name],
            cwd=run, capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"{store}: {proc.stdout}{proc.stderr}"
    finally:
        generated.unlink(missing_ok=True)
