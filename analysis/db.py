"""Shared connection helper for the local homelab Postgres (marketdb-db-1, port 5433).

Only used by the homelab-only steps of the pipeline (story clustering, merge,
briefing, calibration, reader, FinBERT scoring, dq metrics, lab, eval export) --
GitHub Actions never imports this since it has no route to the homelab DB.

psycopg is imported lazily inside connect() (not at module level) so that importing
this module -- or a module that imports it -- doesn't require psycopg to be installed
when only the non-DB parts of the caller are being used (e.g. under `tests/`, which
runs without the analysis venv).
"""
from pathlib import Path


def connect(**kwargs):
    import psycopg
    from dotenv import dotenv_values
    env = dotenv_values(Path.home() / "services" / "marketdb" / ".env")
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="marketintel", user="postgres",
                            password=env["POSTGRES_PASSWORD"], **kwargs)
