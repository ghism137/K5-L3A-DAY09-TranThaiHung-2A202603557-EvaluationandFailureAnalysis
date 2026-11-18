"""Our own M5/M7 tests: traps beyond the public ones (the instructor grades with hidden ones).
Run: python -m pytest -q -m student -o addopts= -k advanced"""
import copy

import pytest

pytestmark = pytest.mark.student


def _rows(items=4, seeds=(1, 2)):
    return [{"item_id": f"E-{i:03d}", "seed": s, "pass_rate": True, "slices": ["money_movement"] if i < 2 else []}
            for i in range(items) for s in seeds]


def _rep(rows=None, metrics=None, items=4, seeds=(1, 2)):
    return {"manifest": {"eval_set_sha": "x", "items": items, "seeds": list(seeds)},
            "rows": rows if rows is not None else _rows(items, seeds),
            "metrics": metrics if metrics is not None else {"pass_rate": 1.0}, "evaluator_failures": []}


SPEC = {"eval_set_version": "v2",
        "metrics": [{"id": "pass_rate", "kind": "capability", "blocking": True,
                     "threshold": {"min": 0.8, "no_significant_drop": True}}],
        "slices": [{"name": "money_movement", "metric": "pass_rate", "min": 0.9,
                    "no_significant_drop": True, "blocking": True}]}


@pytest.fixture(scope="module")
def gate():
    from lab9 import gate

    return gate


def test_clean_report_is_trusted(gate):
    base = _rep()
    assert gate.integrity_check(base, base, SPEC, SPEC) == []


def test_inf_is_flagged(gate):
    assert gate.integrity_check(_rep(metrics={"pass_rate": float("inf")}), _rep(), SPEC)


def test_evaluator_failures_are_flagged(gate):
    pr = _rep()
    pr["evaluator_failures"] = ["E-001@1:ApprovalBypass:boom"]
    assert gate.integrity_check(pr, _rep(), SPEC)


def test_none_value_in_a_row_is_flagged(gate):
    pr = _rep()
    pr["rows"][0]["pass_rate"] = None                  # crashed grader: silently leaves the mean
    assert gate.integrity_check(pr, _rep(), SPEC)


def test_duplicated_row_replaces_a_missing_one(gate):
    rows = _rows()
    rows[-1] = copy.deepcopy(rows[0])                  # right row count, wrong (item, seed) set
    assert gate.integrity_check(_rep(rows=rows), _rep(), SPEC)


def test_cherry_picked_seed_is_flagged(gate):
    pr = _rep(rows=[r for r in _rows() if r["seed"] == 1], seeds=(1,))
    assert gate.integrity_check(pr, _rep(), SPEC)


def test_hand_edited_metric_is_flagged(gate):
    rows = _rows()
    rows[0]["pass_rate"] = False
    pr = _rep(rows=rows, metrics={"pass_rate": 1.0})   # rows say 0.875, the report claims 1.0
    problems = gate.integrity_check(pr, _rep(), SPEC)
    assert any("hand-edited" in p for p in problems)


def test_hand_edited_pass_hat_is_flagged(gate):
    rows = _rows()
    rows[0]["pass_rate"] = False
    pr = _rep(rows=rows, metrics={"pass_rate": 0.875, "pass_hat_2": 1.0})
    assert any("pass_hat_2" in p for p in gate.integrity_check(pr, _rep(), SPEC))


def test_relabelling_an_item_out_of_a_slice_is_flagged(gate):
    rows = _rows()
    for r in rows:
        if r["item_id"] == "E-000":
            r["slices"] = []                           # the regression on E-000 would leave the slice gate
    assert any("slice tags" in p for p in gate.integrity_check(_rep(rows=rows), _rep(), SPEC))


def test_slice_tags_inconsistent_inside_one_report(gate):
    rows = _rows()
    rows[0]["slices"] = []
    assert gate.integrity_check(_rep(rows=rows), _rep(), SPEC)


@pytest.mark.parametrize("edit", [
    lambda s: s["metrics"][0]["threshold"].__setitem__("min", 0.5),
    lambda s: s["metrics"][0]["threshold"].pop("no_significant_drop"),
    lambda s: s["metrics"][0].__setitem__("blocking", False),
    lambda s: s["metrics"].clear(),
    lambda s: s["slices"][0].__setitem__("min", 0.5),
    lambda s: s["slices"][0].__setitem__("no_significant_drop", False),
    lambda s: s["slices"][0].__setitem__("blocking", False),
    lambda s: s["slices"].clear(),
])
def test_every_way_of_loosening_the_spec_is_flagged(gate, edit):
    loose = copy.deepcopy(SPEC)
    edit(loose)
    assert gate.integrity_check(_rep(), _rep(), loose, SPEC)


def test_stricter_spec_is_allowed(gate):
    strict = copy.deepcopy(SPEC)
    strict["metrics"][0]["threshold"]["min"] = 0.9
    strict["slices"][0]["min"] = 0.95
    assert gate.integrity_check(_rep(), _rep(), strict, SPEC) == []


def test_version_bump_without_new_eval_set_is_flagged(gate):
    bumped = dict(copy.deepcopy(SPEC), eval_set_version="v3")
    bumped["metrics"][0]["threshold"]["min"] = 0.1
    assert gate.integrity_check(_rep(), _rep(), bumped, SPEC)


def test_slice_with_no_rows_blocks_instead_of_passing(gate):
    rows = [dict(r, slices=[]) for r in _rows()]
    blocked, _ = gate.slice_check(_rep(rows=rows), _rep(), SPEC)
    assert blocked == ["slice:money_movement/pass_rate"]


def test_slice_ci_below_zero_blocks_even_above_floor(gate):
    base = _rep(rows=_rows(items=10), items=10)
    rows = _rows(items=10)
    for r in rows:
        if "money_movement" in r["slices"] and r["seed"] == 1:
            r["pass_rate"] = False
    spec = copy.deepcopy(SPEC)
    spec["slices"][0]["min"] = 0.0                     # floor off: only the paired CI can block
    blocked, _ = gate.slice_check(_rep(rows=rows, items=10), base, spec)
    assert blocked in ([], ["slice:money_movement/pass_rate"])   # 2 items x 1 flip: CI may touch 0
    rows2 = [dict(r, pass_rate=False) if "money_movement" in r["slices"] else r for r in _rows(items=10)]
    blocked2, _ = gate.slice_check(_rep(rows=rows2, items=10), base, spec)
    assert blocked2 == ["slice:money_movement/pass_rate"]
