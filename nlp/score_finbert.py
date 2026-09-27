"""Local FinBERT scoring for every article, on the homelab CPU (no API cost).

Reads unscored articles (newest first) straight from the local Postgres, scores
title + start of the summary with ProsusAI/finbert, and stores the result in `article_scores`
(model='finbert', score = P(positive) - P(negative)). Time-boxed so it never starves the cycle;
the backlog drains over successive runs.

Run: python score_finbert.py [--max-minutes 8] [--days 30] [--batch 16]
"""
import argparse
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # model is cached locally after the first download
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import sys  # noqa: E402
import torch  # noqa: E402
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: E402

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
from db import connect  # noqa: E402
MODEL = "ProsusAI/finbert"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=8)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--threads", type=int, default=3)
    args = ap.parse_args()
    t0 = time.time()
    torch.set_num_threads(args.threads)
    conn = connect(autocommit=False)
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL).eval()
    id2 = {v.lower(): k for k, v in model.config.id2label.items()}
    ipos, ineg = id2["positive"], id2["negative"]
    deadline = t0 + args.max_minutes * 60
    done = 0
    while time.time() < deadline:
        with conn.cursor() as cur:
            cur.execute(
                """select a.id, a.title, left(coalesce(a.summary_raw, ''), 400)
                   from articles a left join article_scores s on s.article_id = a.id and s.model = 'finbert'
                   where s.article_id is null and a.published_at > now() - make_interval(days => %s) and a.title !~ '[Α-Ωα-ωά-ώ]'
                   order by a.published_at desc limit 256""", (args.days,))
            rows = cur.fetchall()
        if not rows:
            break
        rows.sort(key=lambda r: len(r[1] or "") + len(r[2] or ""))  # similar lengths per batch = less padding
        out = []
        with torch.inference_mode():
            for i in range(0, len(rows), args.batch):
                if time.time() > deadline:
                    break
                chunk = rows[i:i + args.batch]
                texts = [f"{(r[1] or '').strip()}. {(r[2] or '').strip()}" for r in chunk]
                enc = tok(texts, padding=True, truncation=True, max_length=128, return_tensors="pt")
                p = torch.softmax(model(**enc).logits, dim=-1)
                for r, pr in zip(chunk, p):
                    pp, pn = float(pr[ipos]), float(pr[ineg])
                    out.append((r[0], "finbert", pp - pn, pp, pn))
        if out:
            with conn.cursor() as cur:
                cur.executemany("insert into article_scores (article_id, model, score, p_pos, p_neg) values (%s,%s,%s,%s,%s) on conflict do nothing", out)
            conn.commit()
            done += len(out)
        else:
            break
    with conn.cursor() as cur:
        cur.execute("select count(*) from articles a left join article_scores s on s.article_id=a.id and s.model='finbert' where s.article_id is null and a.published_at > now() - make_interval(days => %s) and a.title !~ '[Α-Ωα-ωά-ώ]'", (args.days,))
        remaining = cur.fetchone()[0]
    el = time.time() - t0
    print(f"finbert: scored {done} in {el:.0f}s ({done / max(el, 1):.1f}/s) | backlog remaining {remaining}")


if __name__ == "__main__":
    main()
