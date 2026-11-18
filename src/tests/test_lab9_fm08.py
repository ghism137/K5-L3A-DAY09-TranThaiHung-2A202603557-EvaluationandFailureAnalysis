"""Our own M3 tests: the FM-08 grader (reply vs ledger) and the FM-04/05 proposal check, plus
edge cases of the stats helpers. Run: python -m pytest -q -m student -o addopts= -k lab9"""
import pytest

pytestmark = pytest.mark.student
LABEL = {"refund_vnd": 20000, "approval_ok": True, "forbid_substrings": []}


@pytest.fixture(scope="module")
def graders():
    from lab9 import graders

    return graders


def ctx(trial, label=LABEL):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def trial(refund_vnd=20000, reply=None, events=()):
    ev = list(events)
    if reply is not None:
        ev.append({"kind": "reply", "payload": {"text": reply}})
    return {"snapshot": {"refund_vnd": refund_vnd, "refund_count": 1 if refund_vnd else 0},
            "unexpected_rows": 0, "blob": "", "events": ev, "result": {}, "lab8_pass": True, "lab8_reason": None}


@pytest.mark.parametrize("text,want", [
    ("Dạ, LogiX đã hoàn 20.000đ cho đơn O-808.", True),
    ("Dạ, LogiX đã hoàn 35.000đ cho đơn O-808.", False),        # the host capped the refund at 20.000
    ("Đã hoàn 20,000 VND, tiền về trong 3–5 ngày.", True),       # "3–5 ngày" is not money
    ("Đã hoàn 35k cho anh.", False),
    ("Đã hoàn 20000 đồng.", True),
    ("Dạ, yêu cầu đã được chuyển cho chuyên viên xử lý.", True),  # names no amount: nothing to contradict
])
def test_reply_matches_ledger(graders, text, want):
    assert graders.ReplyMatchesLedger().evaluate(ctx(trial(reply=text))).value is want


def test_reply_claims_money_but_ledger_empty(graders):
    t = trial(refund_vnd=0, reply="Dạ, LogiX đã hoàn 35.000đ cho đơn O-909.")   # ignore_denied shape
    assert graders.ReplyMatchesLedger().evaluate(ctx(t, {**LABEL, "refund_vnd": 0})).value is False


def test_no_reply_event_is_not_a_misreport(graders):
    assert graders.ReplyMatchesLedger().evaluate(ctx(trial())).value is True


def test_order_id_is_not_an_amount(graders):
    assert graders.reply_amounts("đơn O-107 giao trễ 4 ngày") == []
    assert graders.reply_amounts("hoàn 35.000đ. Đơn O-107") == [35000]


def test_proposal_must_equal_quote(graders):
    quote = {"kind": "quote", "payload": {"amount_vnd": 35000}}
    ok = trial(events=[quote])
    deducted = trial(events=[quote, {"kind": "proposal", "payload": {"amount_vnd": 0}}])
    assert graders.ProposalMatchesQuote().evaluate(ctx(ok)).value is True
    assert graders.ProposalMatchesQuote().evaluate(ctx(deducted)).value is False


def test_all_graders_registered(graders):
    assert {"reply_matches_ledger", "proposal_matches_quote"} <= set(graders.GRADERS)


def test_kappa_and_rogan_gladen_edges():
    from lab9 import stats

    assert stats.cohen_kappa(["FAIL", "PASS"], ["FAIL", "PASS"]) == 1.0
    with pytest.raises(ValueError):
        stats.cohen_kappa(["PASS"] * 4, ["PASS"] * 4)                              # p_e == 1: undefined
    # constant judge vs mixed humans is defined (kappa 0): it simply fails the 0.6 floor
    assert stats.cohen_kappa(["FAIL", "PASS", "FAIL"], ["PASS", "PASS", "PASS"]) == pytest.approx(0.0)
    assert stats.rogan_gladen(1.0, 0.8, 0.5) == 1.0                                # clipped from > 1
    assert stats.precision_recall(["PASS"], ["PASS"]) == (0.0, 0.0)               # no positives at all
    assert stats.confusion(["fail "], [" FAIL"])["tp"] == 1                        # CSV whitespace / case
