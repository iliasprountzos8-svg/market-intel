"""Backfill/refresh the category column for any transaction that doesn't have one yet.
Rule-based (see categories.py) -- rerun any time the rule table changes to re-tag old rows
by clearing category first (`update money_transactions set category = null`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from categories import categorize  # noqa: E402


def main():
    import dbconn as db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, description from money_transactions where category is null")
        rows = cur.fetchall()
        for txn_id, description in rows:
            cur.execute("update money_transactions set category = %s where id = %s",
                        (categorize(description), txn_id))
        conn.commit()
    print(f"Categorized {len(rows)} transaction(s).")


if __name__ == "__main__":
    main()
