"""Deal Radar scoring: is a used-item listing worth flipping, and what is the most to pay?

Pure Python, no dependencies. Research/decision aid, not a guarantee of profit.

Profit model (all EUR):
    net_before_buy = resale * condition_factor * (1 - fee_pct) - shipping - extra_cost
    risk_cost      = p_fail * (1 - recovery) * buy
    profit         = net_before_buy - buy - risk_cost
A deal needs profit >= max(min_profit, min_roi * buy). Both limits are linear in `buy`, so the
maximum price to pay has a closed form (see max_buy).

Usage:
    python score.py --model apple_watch_se1_44 --ask 100 --condition good --price-cut --channel fb_local
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

CFG_PATH = Path(__file__).with_name("config.json")


def load_config(path=CFG_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def net_before_buy(cfg, model, condition, channel, extra_cost=0.0, resale=None):
    m = cfg["models"][model]
    ch = cfg["channels"][channel]
    base = m["resale_median"] if resale is None else resale
    return base * cfg["condition_factor"][condition] * (1 - ch["fee_pct"]) - ch["shipping"] - extra_cost


def risk_rate(cfg, proof, remote=False):
    key = "remote" if remote else ("proof" if proof else "no_proof")
    return cfg["fail_probability"][key] * (1 - cfg["rules"]["recovery_on_failure"])


def profit_at(cfg, model, buy, condition, channel, proof, extra_cost=0.0, resale=None, remote=False):
    net = net_before_buy(cfg, model, condition, channel, extra_cost, resale)
    return net - buy - risk_rate(cfg, proof, remote) * buy


def max_buy(cfg, model, condition, channel, proof, extra_cost=0.0, resale=None, remote=False):
    """Highest price that still meets both the minimum profit and the minimum ROI."""
    r = cfg["rules"]
    net = net_before_buy(cfg, model, condition, channel, extra_cost, resale)
    k = risk_rate(cfg, proof, remote)
    by_profit = (net - r["min_profit_eur"]) / (1 + k)
    by_roi = net / (1 + k + r["min_roi"])
    return max(0.0, min(by_profit, by_roi))


def battery_factor(cfg, battery):
    """Resale multiplier for a battery health percentage (None = unknown = no adjustment)."""
    if battery is None:
        return 1.0
    for floor, factor in cfg["battery_factor"]["steps"]:
        if battery >= floor:
            return factor
    return cfg["battery_factor"]["steps"][-1][1]


def score(cfg, model, ask, condition="good", channel="fb_local", proof=False, price_cut=False,
          days_listed=0, extra_cost=0.0, today=None, remote=False, battery=None):
    r = cfg["rules"]
    if remote:
        extra_cost += r["remote_extra_cost"]
    m = cfg["models"][model]
    today = today or date.today()
    warnings = []

    n = m.get("comps_n", 0)
    if n < r["comps_min_n"]:
        warnings.append(f"low confidence: only {n} comparable sale(s) recorded (want {r['comps_min_n']}+)")
    age = (today - datetime.strptime(m["comps_updated"], "%Y-%m-%d").date()).days
    if age > r["comps_stale_days"]:
        warnings.append(f"stale comps: {age} days old")

    bf = battery_factor(cfg, battery)
    base, low = m["resale_median"] * bf, m["resale_low"] * bf
    if battery is not None and bf < 1.0:
        warnings.append(f"battery {battery}%: resale priced at x{bf}; say so in your own listing, and remember a battery service costs money and time")
    mb = max_buy(cfg, model, condition, channel, proof, extra_cost, resale=base, remote=remote)
    mb_proven = max_buy(cfg, model, condition, channel, True, extra_cost, resale=base, remote=remote)
    p_ask = profit_at(cfg, model, ask, condition, channel, proof, extra_cost, resale=base, remote=remote)
    p_ask_cons = profit_at(cfg, model, ask, condition, channel, proof, extra_cost, resale=low, remote=remote)
    if remote:
        warnings.append("remote listing: you cannot check Activation Lock before paying; only with cash on delivery where you may open and test it, or a payment method with buyer protection")
    opening = round(mb * r["opening_offer_ratio"])

    stale_listing = price_cut or days_listed >= 14
    gap_limit = r["max_gap_stale_listing"] if stale_listing else r["max_gap_normal"]
    gap = 1 - mb / ask if ask > 0 else 1.0

    if ask < r["too_good_ratio"] * m["resale_median"]:
        warnings.append("too good to be true: far below market, expect a scam, a locked device or stolen goods; no prepayment, verify in person")

    if ask <= mb:
        verdict = "BUY" if p_ask_cons >= 0 else "BUY (thin: loses money at the low resale price)"
    elif gap <= gap_limit:
        verdict = "NEGOTIATE"
    elif stale_listing and gap <= r["max_gap_stale_listing"]:
        verdict = "NEGOTIATE"
    else:
        verdict = "PASS"

    if verdict == "NEGOTIATE" and gap > r["max_gap_normal"]:
        warnings.append("big gap: only a lowball has a chance; walk away if the seller will not move")

    return {
        "model": m["label"], "ask": ask, "verdict": verdict,
        "max_buy": round(mb, 1), "max_buy_if_proven": round(mb_proven, 1), "opening_offer": opening,
        "profit_at_ask": round(p_ask, 1), "profit_at_ask_conservative": round(p_ask_cons, 1),
        "profit_at_max_buy": round(profit_at(cfg, model, mb, condition, channel, proof, extra_cost, resale=base, remote=remote), 1),
        "gap_to_max_buy": round(gap, 2), "warnings": warnings, "checklist": m.get("must_check", []),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--ask", type=float, required=True)
    ap.add_argument("--condition", default="good", choices=["mint", "good", "fair", "parts"])
    ap.add_argument("--channel", default="fb_local", help="where you would RESELL")
    ap.add_argument("--proof", action="store_true", help="seller already proved it works and is unlocked")
    ap.add_argument("--price-cut", action="store_true", help="listing has been discounted")
    ap.add_argument("--days-listed", type=int, default=0)
    ap.add_argument("--extra-cost", type=float, default=0.0, help="repair or parts you would need to add")
    ap.add_argument("--battery", type=float, default=None, help="battery health percentage the seller states")
    ap.add_argument("--config", default=str(CFG_PATH))
    a = ap.parse_args(argv)
    res = score(load_config(a.config), a.model, a.ask, a.condition, a.channel, a.proof, a.price_cut, a.days_listed, a.extra_cost,
                battery=a.battery)
    print(f"{res['model']}  asking EUR {res['ask']:.0f}  ->  {res['verdict']}")
    print(f"  max to pay EUR {res['max_buy']} (EUR {res['max_buy_if_proven']} if the seller proves it works) | opening offer EUR {res['opening_offer']}")
    print(f"  profit at asking price EUR {res['profit_at_ask']} (EUR {res['profit_at_ask_conservative']} if it sells at the low comp) | at max price EUR {res['profit_at_max_buy']}")
    for w in res["warnings"]:
        print("  ! " + w)
    print("  Before paying:")
    for c in res["checklist"]:
        print("   - " + c)
    return res


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
