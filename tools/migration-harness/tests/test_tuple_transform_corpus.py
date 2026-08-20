import pathlib
import shutil
import subprocess

import pytest
import yaml

from migration_harness.idmap import IdMap
from migration_harness.tuple_transform import transform_tuple
from migration_harness.validation_gen import all_tuple_entries, dedupe_tuple_lines

from corpus_support import (
    _JUDGMENT_SKIPS,
    _select_store_file,
    apply_caveat_param_renames,
)

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus-runs"
STORES = sorted(p.name for p in CORPUS.iterdir() if (p / "schema.zed").exists())


@pytest.mark.skipif(shutil.which("zed") is None, reason="zed not installed")
@pytest.mark.parametrize("store", STORES)
def test_transformed_tuples_are_writable(store):
    """Every transformed tuple in the deduped write set must be a legal
    relationship string for its own schema.

    "Deduped write set", not "every transformed tuple", because
    `dedupe_tuple_lines` below can drop a line: `condition-data-types` has
    two `tests:` blocks that each render correctly on their own but collide
    on the same (resource, relation, subject) triple, and the discarded one
    is never handed to `zed validate` by this test. See the dedup comment
    further down for why that's the right call here.

    Reuses `_select_store_file`/`_JUDGMENT_SKIPS` (`corpus_support.py`,
    shared with `test_validation_gen_corpus.py` rather than one test module
    importing the other's private names) instead of a bare `next(glob())`,
    and `validation_gen.all_tuple_entries` instead of reading only
    `doc.get("tuples")`: the latter silently drops any tuple nested inside a
    `tests:` block, which previously lost 18 writes on `condition-data-types`
    (see that function's docstring for why). The two stores `corpus_support`
    already skips for hand-verified judgment reasons -- `abac-with-rebac`
    (mutually exclusive scenarios needing per-scenario file splitting) and
    `modeling-guide` (no unified store file) -- are skipped here too, for the
    same reasons: this test's naive union of every `tests:` block's tuples
    into one graph carries the identical risk `_JUDGMENT_SKIPS` documents,
    even though this test only asks about legality (does it parse and target
    a real relation), not about whether the merged graph reproduces either
    scenario's assertions.
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

    doc = yaml.safe_load(store_file.read_text()) or {}
    tuples = all_tuple_entries(doc)
    if not tuples:
        pytest.skip(f"{store} has no tuples")

    idmap = IdMap.load(run / "migration-map.json")
    rels = [transform_tuple(t, idmap) for t in tuples]

    # A store's tuples: entries can carry two different tests: blocks' writes
    # to the identical (resource, relation, subject) triple -- SpiceDB has no
    # notion of an isolated per-test: dataset, so folding everything into one
    # graph means the second write for the same triple is a same-relationship
    # re-write (TOUCH semantics: the actual live-load step overwrites, never
    # errors), not a second distinct fact. zed validate's static loader is
    # stricter than that -- it rejects the raw union outright as "found
    # repeated relationship" the moment two lines share a triple, even when
    # the only difference is the caveat suffix (condition-data-types: two
    # tests: blocks both bind a caveat to `datatype_test:one#is_valid@user:int`,
    # one with context, one without). So the list fed to zed validate has to
    # be deduplicated the same way generate_validation's own output already
    # is -- reusing its `dedupe_tuple_lines` here keeps this test's notion of
    # "the tuples that get written" identical to phase 5's.
    #
    # This matches phase 5's production path, not phase 3's -- the two are
    # not interchangeable here. Real phase 3 migration reads a live OpenFGA
    # store, which holds one row per (object, relation, user) triple, so this
    # collision cannot arise there; it is purely an artifact of `.fga.yaml`'s
    # multiple isolated tests: blocks being folded into one shared SpiceDB
    # graph by this corpus fixture format. The reuse is safe (each colliding
    # line renders correctly on its own -- verified via Counter over every
    # rendered line for condition-data-types, no two lines were textually
    # identical) and does not mask a transform bug; it just means this line
    # doesn't double as evidence about phase 3's real input shape.
    rels, _ = dedupe_tuple_lines(rels)

    # zed validate is the cheapest oracle that parses relationship strings
    # against a real schema and rejects illegal ones.
    #
    # Not tmp_path + an absolute schemaFile, despite that being the brief's
    # literal transcription: zed validate resolves schemaFile relative to
    # the validation file's own location, and separately rejects an
    # absolute path to the validation file itself ("schema filepath ... must
    # be local to where the command was invoked") even when invoked from the
    # right directory. So the validation file has to live inside `run`,
    # referenced by a bare relative filename, with cwd set to `run` --
    # matching test_generated_file_passes_zed_validate's existing pattern in
    # test_validation_gen_corpus.py. Written under a name that cannot
    # collide with a real corpus artifact and removed in `finally` so a
    # failing assertion still leaves corpus-runs/ clean.
    val = run / ".generated-tuple-validate.yaml"
    try:
        content = (
            "schemaFile: schema.zed\n"
            + "relationships: |\n"
            + "".join(f"  {r}\n" for r in rels)
            + "assertions:\n  assertTrue: []\n  assertFalse: []\n"
        )
        # See CAVEAT_PARAM_RENAMES: transform_tuple passes a tuple's condition
        # context keys through unchanged from the OpenFGA source, so a store
        # whose own schema.zed renamed those keys needs the same translation
        # applied here before the generated text can validate against it.
        content = apply_caveat_param_renames(store, content)
        val.write_text(content)
        proc = subprocess.run(
            ["zed", "validate", val.name], cwd=run, capture_output=True, text=True
        )
        assert proc.returncode == 0, f"{store}: {proc.stdout}{proc.stderr}"
    finally:
        val.unlink(missing_ok=True)
