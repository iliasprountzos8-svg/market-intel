"""Import a bank-exported CSV into money_transactions.

Bank CSV layouts vary wildly (column names, date formats, one amount column vs separate
debit/credit columns, decimal comma vs dot). Rather than hardcode one bank's format, this
auto-detects likely columns and lets you override via bank_config.json when the guess is wrong.

Usage:
    python import_csv.py path/to/statement.csv --sniff   # show detected columns, import nothing
    python import_csv.py path/to/statement.csv           # actually import

Re-running on the same file is safe: rows are deduped by (date, amount, normalized description).
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from categories import normalize_merchant  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent / "bank_config.json"
DATE_CANDIDATES = ["date", "transaction date", "value date", "booking date", "ημερομηνία"]
DESC_CANDIDATES = ["description", "details", "narrative", "merchant", "αιτιολογία", "περιγραφή"]
AMOUNT_CANDIDATES = ["amount", "value", "ποσό"]
DEBIT_CANDIDATES = ["debit", "withdrawal", "χρέωση"]
CREDIT_CANDIDATES = ["credit", "deposit", "πίστωση"]


def _find(header, candidates):
    low = {h.lower().strip(): h for h in header}
    for c in candidates:
        if c in low:
            return low[c]
    return None


def default_config_template():
    return {
        "_comment": "Fill in if auto-detect (see --sniff) guesses wrong. date_format uses "
                    "strptime codes, e.g. %d/%m/%Y. Set amount_col OR both debit_col/credit_col.",
        "date_col": None, "date_format": None, "desc_col": None,
        "amount_col": None, "debit_col": None, "credit_col": None,
        "decimal_comma": False,
    }


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    CONFIG_PATH.write_text(json.dumps(default_config_template(), indent=2))
    return default_config_template()


def resolve_columns(header, cfg):
    date_col = cfg.get("date_col") or _find(header, DATE_CANDIDATES)
    desc_col = cfg.get("desc_col") or _find(header, DESC_CANDIDATES)
    amount_col = cfg.get("amount_col") or _find(header, AMOUNT_CANDIDATES)
    debit_col = cfg.get("debit_col") or _find(header, DEBIT_CANDIDATES)
    credit_col = cfg.get("credit_col") or _find(header, CREDIT_CANDIDATES)
    return date_col, desc_col, amount_col, debit_col, credit_col


def parse_amount(raw: str, decimal_comma: bool) -> float:
    s = (raw or "").strip().replace("€", "").replace(" ", "")
    if decimal_comma:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    return float(s) if s not in ("", "-") else 0.0


def parse_date(raw: str, fmt: str | None) -> str:
    from datetime import datetime
    raw = raw.strip()
    fmts = [fmt] if fmt else ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d.%m.%Y"]
    for f in fmts:
        try:
            return datetime.strptime(raw, f).date().isoformat()
        except (ValueError, TypeError):
            continue
    raise ValueError(f"Could not parse date {raw!r}; set date_format in {CONFIG_PATH.name}")


def read_rows(csv_path: Path, cfg: dict):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        date_col, desc_col, amount_col, debit_col, credit_col = resolve_columns(header, cfg)
        missing = [n for n, v in [("date", date_col), ("description", desc_col)] if not v]
        if missing or (not amount_col and not (debit_col or credit_col)):
            raise SystemExit(
                f"Could not confidently map columns. Header was: {header}\n"
                f"Detected: date={date_col} desc={desc_col} amount={amount_col} "
                f"debit={debit_col} credit={credit_col}\n"
                f"Edit {CONFIG_PATH} with the right column names and re-run."
            )
        for row in reader:
            txn_date = parse_date(row[date_col], cfg.get("date_format"))
            description = (row[desc_col] or "").strip()
            if amount_col:
                amount = parse_amount(row[amount_col], cfg.get("decimal_comma", False))
            else:
                debit = parse_amount(row.get(debit_col, ""), cfg.get("decimal_comma", False)) if debit_col else 0.0
                credit = parse_amount(row.get(credit_col, ""), cfg.get("decimal_comma", False)) if credit_col else 0.0
                amount = credit - debit
            yield txn_date, description, amount


def dedup_key(txn_date: str, description: str, amount: float) -> str:
    h = hashlib.sha256(f"{txn_date}|{normalize_merchant(description)}|{amount:.2f}".encode()).hexdigest()
    return h[:32]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--sniff", action="store_true", help="Show detected columns and a few parsed rows, import nothing.")
    args = ap.parse_args()

    cfg = load_config()
    header = next(csv.reader(open(args.csv_path, newline="", encoding="utf-8-sig")))
    date_col, desc_col, amount_col, debit_col, credit_col = resolve_columns(header, cfg)
    print(f"Header: {header}")
    print(f"Detected -> date={date_col} desc={desc_col} amount={amount_col} debit={debit_col} credit={credit_col}")

    rows = list(read_rows(args.csv_path, cfg))
    print(f"Parsed {len(rows)} row(s). First 3:")
    for r in rows[:3]:
        print(" ", r)
    if args.sniff:
        return

    import dbconn as db
    inserted = skipped = 0
    with db.connect() as conn, conn.cursor() as cur:
        for txn_date, description, amount in rows:
            key = dedup_key(txn_date, description, amount)
            merchant = normalize_merchant(description)
            cur.execute(
                "insert into money_transactions (txn_date, description, merchant_norm, amount, source_file, dedup_key) "
                "values (%s, %s, %s, %s, %s, %s) on conflict (dedup_key) do nothing",
                (txn_date, description, merchant, amount, args.csv_path.name, key),
            )
            inserted += cur.rowcount
            skipped += 1 - cur.rowcount
        conn.commit()
    print(f"Imported {inserted} new transaction(s), skipped {skipped} already-seen row(s).")


if __name__ == "__main__":
    main()
