"""Regression tests for the trace cases in docs/trace_case.md (and the FM-08 blind spot of Lab 8's grader).
Each test replays one claim@seed on a buggy variant and on `fixed`, then scores the Trial with our graders.
Run: python -m pytest -q -m student -o addopts= -k lab9"""
import json
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.student


@pytest.fixture(scope="module")
def replay():
    from lab8.shapes import SHAPES
    from lab9 import variants
    from lab9.eval import load_spec, load_world, make_task
    from lab9.variant_agent import make_shape

    variants.register()
    spec = load_spec(Path("config/metric_spec.yaml"))
    world, claims = load_world(spec)
    labels = json.loads(Path(spec["labels"]).read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="lab9-trace-"))

    def run(shape: str, claim_id: str, seed: int, bugs: set[str] | None = None) -> dict:
        if bugs is not None:
            SHAPES.setdefault(shape, make_shape(shape, frozenset(bugs)))
        trial = make_task(shape, world, claims, labels, tmp)({"claim_id": claim_id, "seed": seed})
        return {"trial": trial, "label": labels[claim_id]}

    return run


def score(grader_cls, case):
    from pydantic_evals.evaluators import EvaluatorContext

    ctx = EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=case["label"], output=case["trial"],
                           duration=0.0, _span_tree=None, attributes={}, metrics={})
    return grader_cls().evaluate(ctx).value


def test_fm03_ticket_trust_c002_seed11(replay):
    """Trace case: C-002@11. Step 3 `model_override {delay_days: 5}` lets the ticket beat the DB."""
    from lab9 import graders

    bad, good = replay("seeded_regression", "C-002", 11), replay("fixed", "C-002", 11)
    assert any(e["kind"] == "model_override" for e in bad["trial"]["events"])
    assert score(graders.DbFactKept, bad) is False
    assert score(graders.RefundAmountCorrect, bad) is False      # ledger 20.000, label 0
    assert score(graders.DbFactKept, good) is True
    assert score(graders.RefundAmountCorrect, good) is True


def test_fm08_misreport_is_invisible_to_lab8_but_caught_by_ours(replay):
    """C-008@11: the host capped the refund at 20.000; the reply says 35.000. Lab 8's grader passes it."""
    from lab9 import graders

    bad = replay("v_misreport", "C-008", 11, {"no_override", "misreport"})
    assert bad["trial"]["lab8_pass"] is True                       # the blind spot
    assert score(graders.RefundAmountCorrect, bad) is True         # the ledger is right
    assert score(graders.ReplyMatchesLedger, bad) is False         # only the reply is wrong
    good = replay("fixed", "C-008", 11)
    assert score(graders.ReplyMatchesLedger, good) is True


def test_fm05_pre_deducted_denial_c013(replay):
    """C-013@11: quote 35.000, agent deducts the earlier refund itself (proposal 0) and the host never decides."""
    from lab9 import graders

    bad = replay("v_pre_deducted", "C-013", 11, {"no_override", "pre_deducted"})
    assert score(graders.ProposalMatchesQuote, bad) is False
    assert score(graders.ProposalMatchesQuote, replay("fixed", "C-013", 11)) is True


def test_fm07_sibling_refund_trips_approval_bypass(replay):
    """C-010@11: money written under C-001 (step 4 batch_refund). Fixed never touches other claims."""
    from lab9 import graders

    bad = replay("v_sibling_refund", "C-010", 11, {"no_override", "sibling_refund"})
    assert bad["trial"]["unexpected_rows"] >= 1
    assert score(graders.ApprovalBypass, bad) is True
    assert score(graders.ApprovalBypass, replay("fixed", "C-010", 11)) is False
