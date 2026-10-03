import subprocess, sys, json

summary = (
    "Broad risk-on tape continues into the Trump-Xi summit (Thursday, Maryland): AI mega-cap "
    "CEOs (Huang, Pichai, Altman, Cook, Bezos, Zuckerberg) attend a White House state dinner "
    "framed partly around AI-safety cooperation, but chip export controls on China stay in "
    "place and expectations for concrete deliverables are low - this is a sentiment tailwind "
    "for NVDA/GOOGL/the broader AI trade, not a resolved policy risk. Oil is on its longest "
    "losing streak in over a year (Brent ~$98) on US-Iran de-escalation hopes and a Saudi "
    "pipeline restart, which is the main macro tailwind behind this week's equity strength and "
    "VWCE's gains. Counterweight: Boston Fed's Collins flagged inflation staying 'notably' "
    "above 2% even after last week's hike, markets are ~53/47 split on another October hike, "
    "and the OECD separately warned on surging government bond yields - a hawkish-Fed / "
    "higher-yield combination is the main headwind to watch against the AI-driven rally, "
    "since it raises the discount rate on the growth-heavy names that dominate both VWCE and "
    "the tracked sleeve. Eurozone data held up (French composite PMI hit a 10-month high), "
    "a mild positive for VWCE's European weighting."
)

themes = "AI-driven risk-on ahead of Trump-Xi summit;Oil selloff on US-Iran de-escalation;Hawkish Fed commentary vs rising bond yields;Eurozone PMI resilience"

guidance = (
    "No action needed on the sleeve today. Two things worth actually watching over the next "
    "week: (1) any surprise on US chip-export policy toward China coming out of the Trump-Xi "
    "meeting - that's a real catalyst for NVDA specifically; (2) whether Fed rhetoric (Collins "
    "today) turns into an actual hawkish surprise at the October FOMC, since that's the risk "
    "that could reverse this week's rally across both VWCE and the sleeve at once given how "
    "concentrated both are in the same mega-cap tech names."
)

watchlist = {
    "NVDA": "Central to the Trump-Xi AI narrative this week; export-control headlines out of the summit are the real catalyst to watch, not the summit itself.",
    "GOOGL": "Along for the AI-mega-cap sentiment ride via the summit dinner; no company-specific news today.",
    "MSFT": "No direct news today; exposure is via the same AI-capex/Fed-rate macro themes as the rest of the sleeve.",
    "ASML": "No direct news today; watch for any read-through if US-China chip export rules move after the summit.",
}

cmd = [
    sys.executable, "cli.py", "write-digest",
    "--hours", "24",
    "--summary", summary,
    "--themes", themes,
    "--guidance", guidance,
    "--watchlist", json.dumps(watchlist),
]
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout)
if r.returncode != 0:
    print("FAILED:", r.stderr)
