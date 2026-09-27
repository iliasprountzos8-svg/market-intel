"""Connection helper for money-intel, sharing the same local homelab Postgres as market-intel
(marketdb-db-1, port 5433) but its own tables (money_transactions, money_recurring).
psycopg is imported lazily so unit tests that only exercise pure logic don't need it installed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))


def connect(**kwargs):
    import db as analysis_db  # analysis/db.py -- same DB, reuse its connection details
    return analysis_db.connect(**kwargs)
