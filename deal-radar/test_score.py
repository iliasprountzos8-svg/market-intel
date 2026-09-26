"""Run: python test_score.py   (no dependencies)"""
import json
from datetime import date

import score

CFG = score.load_config()
TODAY = date(2026, 9, 26)
M = "apple_watch_se1_44"


def s(**kw):
    kw.setdefault("today", TODAY)
    return score.score(CFG, M, **kw)


def test_watch_at_asking_price_is_a_lowball_at_best():
    r = s(ask=100, condition="good", price_cut=True)
    assert r["verdict"] == "NEGOTIATE", r
    assert r["profit_at_ask"] < 0
    assert 55 <= r["max_buy"] <= 62, r["max_buy"]
    assert 45 <= r["opening_offer"] <= 52, r["opening_offer"]
    assert any("big gap" in w for w in r["warnings"])
    assert any("low confidence" in w for w in r["warnings"])


def test_price_at_or_below_max_is_buy():
    ceiling = score.max_buy(CFG, M, "good", "fb_local", False)
    assert s(ask=55, condition="good")["verdict"] == "BUY"
    assert s(ask=ceiling - 0.1, condition="good")["verdict"] == "BUY"
    assert s(ask=ceiling + 1, condition="good")["verdict"] != "BUY"


def test_max_buy_actually_meets_both_thresholds():
    for cond in ("mint", "good", "fair"):
        for ch in ("fb_local", "vinted", "ebay"):
            for proof in (False, True):
                mb = score.max_buy(CFG, M, cond, ch, proof)
                if mb <= 0:
                    continue
                p = score.profit_at(CFG, M, mb, cond, ch, proof)
                need = max(CFG["rules"]["min_profit_eur"], CFG["rules"]["min_roi"] * mb)
                assert p >= need - 0.01, (cond, ch, proof, mb, p, need)
                over = score.profit_at(CFG, M, mb + 2, cond, ch, proof)
                need_over = max(CFG["rules"]["min_profit_eur"], CFG["rules"]["min_roi"] * (mb + 2))
                assert over < need_over, "paying 2 EUR more should break a threshold"


def test_ebay_fees_lower_the_ceiling():
    assert score.max_buy(CFG, M, "good", "ebay", False) < score.max_buy(CFG, M, "good", "fb_local", False) - 8


def test_proof_raises_the_ceiling_a_little():
    assert score.max_buy(CFG, M, "good", "fb_local", True) > score.max_buy(CFG, M, "good", "fb_local", False)


def test_condition_matters():
    assert score.max_buy(CFG, M, "fair", "fb_local", False) < score.max_buy(CFG, M, "good", "fb_local", False)


def test_far_above_ceiling_is_pass():
    assert s(ask=140, condition="good")["verdict"] == "PASS"
    assert s(ask=100, condition="good")["verdict"] == "PASS"  # not discounted, gap 40% > 30%


def test_too_good_to_be_true_warns():
    r = s(ask=25)
    assert r["verdict"] == "BUY"
    assert any("too good" in w for w in r["warnings"])


def test_stale_comps_warn():
    r = score.score(CFG, M, 60, today=date(2026, 12, 1))
    assert any("stale comps" in w for w in r["warnings"])


def test_repair_cost_reduces_ceiling_one_for_one_ish():
    a = score.max_buy(CFG, M, "good", "fb_local", False)
    b = score.max_buy(CFG, M, "good", "fb_local", False, extra_cost=15)
    assert 12 < a - b < 16


def test_battery_factor_steps():
    assert score.battery_factor(CFG, None) == 1.0
    assert score.battery_factor(CFG, 95) == 1.0
    assert score.battery_factor(CFG, 90) == 1.0
    assert score.battery_factor(CFG, 87) == 0.95
    assert score.battery_factor(CFG, 81) == 0.88
    assert score.battery_factor(CFG, 70) == 0.78


def test_low_battery_lowers_the_ceiling_and_warns():
    m = "apple_watch_se2"
    full = score.score(CFG, m, 90, today=TODAY)
    weak = score.score(CFG, m, 90, today=TODAY, battery=81)
    assert weak["max_buy"] < full["max_buy"] - 8
    assert weak["profit_at_ask"] < full["profit_at_ask"]
    assert any("battery 81%" in w for w in weak["warnings"])
    assert not any("battery" in w for w in full["warnings"])


def test_unknown_battery_changes_nothing():
    a = score.score(CFG, "apple_watch_se2", 90, today=TODAY)
    b = score.score(CFG, "apple_watch_se2", 90, today=TODAY, battery=None)
    assert a == b


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print("ok   ", name)
        except AssertionError as e:
            failed += 1; print("FAIL ", name, "->", e)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
