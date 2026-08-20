"""Shared helpers for tests parametrized over `corpus-runs/`'s 39 stores.

Not itself a `test_*.py` module, so pytest never collects it -- it exists
purely so `test_validation_gen_corpus.py` and `test_tuple_transform_corpus.py`
(both walk the same 39-store corpus and need the same store-file-selection
and judgment-skip logic) share one definition instead of one importing the
other's private names. A test module reaching into another test module's
`_`-prefixed symbols only works by accident of pytest's rootless import mode
and reads, to anyone skimming the corpus tests, as a real module boundary
that isn't actually enforced -- so the second caller moved both here rather
than importing across `test_validation_gen_corpus.py`.
"""

import pathlib

# Stores whose committed validation.yaml reflects judgment `generate_validation`
# cannot mechanically reproduce -- see corpus-runs/README.md for each store's own
# finding. Every entry here must carry the specific reason in its skip message;
# a bare skip is indistinguishable from a passing test.
#
# Both corpus test modules (test_validation_gen_corpus.py, which produces and
# checks a whole validation.yaml, and test_tuple_transform_corpus.py, which
# only checks that a store's transformed tuples are legal relationship
# strings) skip the same two stores for the same underlying reasons: a naive
# union of every tests: block's tuples into one SpiceDB graph carries the
# identical risk this dict documents, whether the thing being checked
# afterward is a full assertion set or just write-legality.
_JUDGMENT_SKIPS = {
    "abac-with-rebac": (
        "needed per-scenario file splitting: its two tests: blocks encode two "
        "mutually exclusive scenarios (draft vs. published) under the same object "
        "ID via *different relations*, not just different caveat context -- "
        "merging both tests: blocks' tuples into one converted graph silently "
        "flips can_edit@bob and can_view@anne both to true, contradicting whichever "
        "scenario is not 'active' (corpus-runs/README.md, abac-with-rebac finding "
        "1). A first-seen-wins tuple merge cannot recover the per-scenario negative "
        "coverage a human obtained by splitting the store into separate runs; "
        "generate_validation converts one store file into one validation file and "
        "has no way to reproduce that split."
    ),
    "modeling-guide": (
        "ships no unified store.fga.yaml -- only ten step-N-*.fga.yaml files, each "
        "a cumulative tutorial checkpoint. The committed validation.yaml was hand-"
        "assembled from content spanning all ten step files; generate_validation "
        "takes exactly one store file, so no single glob match can reproduce a "
        "validation file assembled from ten."
    ),
}


# Caveat *write-time* parameter names that this corpus's own committed
# artifacts (corpus-runs/<store>/schema.zed and friends) use in place of the
# upstream OpenFGA store's own `condition:` field names, keyed by store.
# `validation_gen.tuple_relationship` renders a tuple's `condition: context:`
# block straight through to the generated caveat suffix by design -- its own
# docstring: "idmap has no condition/caveat namespace, so the name passes
# through unchanged" -- so the mechanically-regenerated text for these
# stores still carries the *source*'s field names even though the schema it
# gets validated against no longer does. `apply_caveat_param_renames` below
# is the corresponding translation, applied only in test code (never in
# `src/migration_harness/`, which stays a faithful, un-opinionated passthrough)
# so a generated-and-validated round trip still exercises the real thing:
# does this store's transformed data still write legally against its own
# schema.
CAVEAT_PARAM_RENAMES: dict[str, dict[str, str]] = {
    "temporal-access": {"grant_time": "issued_at", "grant_duration": "valid_for"},
    "superadmin": {"grant_time": "issued_at", "grant_duration": "valid_for"},
    "advanced-entitlements": {
        "collaborator_limit": "collaborator_max",
        "row_sync_limit": "row_sync_max",
        "page_history_days_limit": "page_history_days_max",
    },
    "ip-based-access": {"cidr": "ip_range"},
    "groups-resource-attributes": {"allowed_statuses": "permitted_states"},
    "banking": {"transaction_limit": "transfer_cap"},
}


def apply_caveat_param_renames(store: str, text: str) -> str:
    """Rewrite a mechanically-generated caveat-context JSON key to match
    this store's renamed schema.zed.

    A plain ``"old":`` -> ``"new":`` substring replacement on the serialized
    JSON key (not a general identifier match) is safe here: every string this
    is called on is caveat-context JSON produced by `json.dumps` inside
    `validation_gen`/`tuple_transform` (a fixed ``{"key":value,...}`` shape,
    no whitespace variants to miss), and each renamed key is chosen to be
    unique within its own store's generated output, so nothing else in a
    relationship string or assertion line can collide with it.
    """
    for old, new in CAVEAT_PARAM_RENAMES.get(store, {}).items():
        text = text.replace(f'"{old}":', f'"{new}":')
    return text


def _select_store_file(src: pathlib.Path) -> pathlib.Path | None:
    """Pick the one `.fga.yaml` file that represents a store's full oracle.

    Not `next(src.glob("*.fga.yaml"), None)`: `Path.glob` order is
    filesystem-dependent, not alphabetical, so with more than one match it
    picks arbitrarily. Derived mechanically across all 39 corpus stores
    (`ls corpus/sample-stores/stores/*/*.fga.yaml`): 38 of 39 ship a file
    literally named `store.fga.yaml` -- the sample-stores repo's own
    naming convention for "the" combined test file -- and for 37 of those
    38 it is the *only* `.fga.yaml` present, so `next(glob())` already
    happened to return it by accident. Preferring the literal name over
    glob order needs no per-store knowledge and resolves the one exception
    that has more than one candidate: `modular` ships `store.fga.yaml`
    alongside three per-module files that use an external `tuple_file:`
    reference this generator does not resolve. Verified: pointed at
    `store.fga.yaml`, `generate_validation` reproduces `modular`'s
    committed `validation.yaml` exactly and the result passes `zed
    validate --fail-on-warn`.

    The other exception, `modeling-guide`, ships no `store.fga.yaml` at
    all -- only ten `step-N-*.fga.yaml` tutorial checkpoints, no single one
    of which is the whole store -- so this returns `None` for it exactly
    as `next(glob())` would have for a store with zero matches, and the
    caller's existing `store_file is None` skip covers it unchanged.
    """
    matches = sorted(src.glob("*.fga.yaml"))
    canonical = src / "store.fga.yaml"
    if canonical in matches:
        return canonical
    if len(matches) == 1:
        return matches[0]
    return None
