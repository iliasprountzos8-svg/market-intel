import subprocess, sys

calls = [
    dict(id="4b7ee663-c79a-408a-98ce-f002d4d3e0b8", sentiment="bullish", relevance=75,
         tickers="NVDA,GOOGL,OPENAI", action="watch", risk="low", confidence=70,
         summary="Trump-Xi summit (Thu) puts AI safety on the agenda (US-China AI dialogue channel discussed by Bessent), but neither side is slowing the AI race - Washington keeps restricting Nvidia's advanced chips to China and accuses Chinese firms of model distillation. Net: US AI leaders (NVDA, GOOGL, MSFT via OpenAI ties) stay the preferred way to play the race; chip export controls remain a live overhang, not a resolved risk."),
    dict(id="13a0b4b0-1717-4e2b-af37-ebad78f24038", sentiment="bullish", relevance=75,
         tickers="NVDA,GOOGL,AAPL,AMZN,MSFT", action="watch", risk="low", confidence=65,
         summary="Guardian coverage of the same Trump-Xi summit: Jensen Huang, Sundar Pichai, Sam Altman, Tim Cook, Bezos, Zuckerberg all attending a Thursday state dinner. Low expectations for concrete deliverables (per CSIS analyst Bonnie Glaser) beyond a possible AI-safety framing. Confirms the summit is a sentiment event for AI mega-caps, not a policy shift yet - no action needed beyond watching for surprise chip-export announcements."),
    dict(id="9687e6b0-1722-48fd-88ca-5fe17a3308af", sentiment="bearish", relevance=55,
         tickers="NVDA,OPENAI", action="watch", risk="low", confidence=55,
         summary="Cross-country POLITICO/Public First poll: pluralities in US/Canada/UK/France/Spain/Germany want to pause AI development and fear job destruction over creation. Political-backlash risk for AI regulation is building, but this is a slow-moving sentiment/regulatory risk, not a near-term price catalyst for NVDA/AI capex names."),
    dict(id="d7a44181-68cb-4810-a51c-f587ca5b9ce0", sentiment="mixed", relevance=65,
         tickers="", action="watch", risk="medium", confidence=65,
         summary="Boston Fed's Collins (non-voter) says inflation is now more likely to stay 'notably' above 2% even after backing last week's quarter-point hike - a hawkish signal. CME FedWatch shows markets split roughly 53/47 on another 25bp hike in October. A more-restrictive-for-longer Fed is a modest headwind for the VWCE base (higher discount rate on growth/tech-heavy index) and raises reinvestment-rate risk if bond yields keep climbing alongside (see OECD bond-yield warning same day)."),
    dict(id="d50509e3-e468-4a86-95c3-6dcb3f9e9c5a", sentiment="bullish", relevance=50,
         tickers="TSM", action="no action", risk="none", confidence=60,
         summary="AI-linked wealth effects (Samsung/SK Hynix rally) are spilling into resilient luxury spending in South Korea/Japan even as AI stocks turned volatile - Morgan Stanley raised Korea private-consumption forecast. Soft positive read-through for the broader AI supply chain (TSMC exposure via VWCE), not directly actionable for the tracked sleeve."),
    dict(id="666127e9-26b4-45d9-aa76-5046f850558a", sentiment="bullish", relevance=45,
         tickers="", action="no action", risk="none", confidence=30,
         summary="FT opinion/analysis piece titled 'Reasons to be bullish on equities' - paywalled, only headline/teaser available, can't verify the actual argument. Logged for context only; low confidence given no real body text."),
]

for c in calls:
    cmd = [
        sys.executable, "cli.py", "mark-processed", c["id"],
        "--sentiment", c["sentiment"],
        "--relevance", str(c["relevance"]),
        "--action", c["action"],
        "--risk", c["risk"],
        "--confidence", str(c["confidence"]),
        "--summary", c["summary"],
    ]
    if c["tickers"]:
        cmd += ["--tickers", c["tickers"]]
    print("Marking", c["id"], c["sentiment"], c["relevance"])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("FAILED:", r.stderr)
    else:
        print("OK")
