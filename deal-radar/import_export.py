"""Convert a Marketplace export (xlsx from your own browser extension) into the listings CSV.

Expected columns (header names are ignored, position matters):
    0 item URL, 1 image URL, 2 price, 3 title, 4 location, 5 previous price
Greek text and the euro sign may arrive as replacement characters; only Latin titles, digits and the
trailing region letter are used. Facebook lists local results first and "outside your search" results after, and many local cards show no
location at all. So: rows are local until the first row whose region letter is not 'B' (Central Macedonia);
from that row on everything is REMOTE.

Usage: python import_export.py path/to/export.xlsx listings_fb_export.csv
"""
import csv
import re
import sys

import pandas as pd

LOCAL_REGION = "B"
ACCESSORY_WORDS = re.compile(r"\b(bands?|loops?|straps?|bracelet|chargers?|cable|protector|glass|stand|adapter)\b", re.I)
NOT_A_WATCH = re.compile(r"iphone|i phone|airpods|ipad|macbook|galaxy|huawei|xiaomi|garmin|samsung|amazfit|oppo|swatch", re.I)


def digits(s):
    d = re.sub(r"[^0-9]", "", str(s)) if s is not None and str(s) != "nan" else ""
    return int(d) if d else None


def size_of(t):
    m = re.search(r"\b(38|40|41|42|44|45|46|49)\s?m", t, re.I)
    return int(m.group(1)) if m else None


def model_of(title, ask=None):
    t = re.sub(r"\s+", " ", title.lower())
    if re.search(r"iphone|i phone|airpods|ipad", t):
        return "bundle" if "watch" in t else "other"
    if NOT_A_WATCH.search(t) and "apple watch" not in t.replace("+", " "):
        return "other"
    if ACCESSORY_WORDS.search(t) and (ask is None or ask < 60):
        return "accessory"
    if "ultra" in t:
        return "ultra"
    if re.search(r"\bse\b|\bse\d|\bse\s?(series|gen)", t):
        if re.search(r"se ?3\b|se 2025|3rd|3η", t):
            return "se3"
        if re.search(r"series 1|gen ?1\b|gen1|1st|first|a235[1-4]|\(2021\)|se 2020", t):
            return "se1"
        if re.search(r"se ?2\b|2nd|gen ?2\b|gen2|second|2022|2023|2024|2 44|2 40", t):
            return "se2"
        return "se_unk"
    m = re.search(r"series ?(\d{1,2})\b", t) or re.search(r"watch ?(\d{1,2})\b", t) or re.search(r"\bs(\d{1,2})\b", t)
    if m and 1 <= int(m.group(1)) <= 11:
        return "s" + m.group(1)
    if re.search(r"watch", t):
        return "unknown"
    return "other"


def main(src, dst):
    df = pd.read_excel(src, header=0, dtype=str)
    df = df.iloc[:, :6]
    df.columns = ["url", "img", "price", "title", "loc", "was"]
    rows, seen = [], set()
    for i, r in df.iterrows():
        url = str(r["url"])
        m = re.search(r"/item/(\d+)", url)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        ask, was = digits(r["price"]), digits(r["was"])
        title = re.sub(r"[^\x20-\x7e]+", " ", str(r["title"])).strip()
        title = re.sub(r"\s+", " ", title)
        loc = str(r["loc"])
        region = loc.strip()[-1:] if "," in loc else ""
        key = model_of(title, ask)
        size = size_of(title)
        note = []
        if key == "s7" and size in (40, 42, 44):
            note.append("Series 7 only comes in 41/45mm: wrong model or scam risk")
        if key == "se1" and size == 44:
            key = "se1_44"
        if ask is not None and ask < 30 and key not in ("accessory", "other"):
            note.append("under 30 EUR: parts, broken or scam")
        rows.append({"id": len(rows) + 1, "model_key": key, "size_mm": size or "", "ask": ask if ask is not None else "",
                     "was": was or "", "location": region,
                     "title_short": title[:60], "note": "; ".join(note), "url": url.split("?")[0]})
    boundary = next((i for i, r in enumerate(rows) if r["location"] not in ("", LOCAL_REGION)), len(rows))
    for i, r in enumerate(rows):
        r["location"] = "Local" if i < boundary else f"REMOTE {r['location'] or '?'}"
    print(f"local/remote boundary at row {boundary + 1}")
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    c = Counter(r["model_key"] for r in rows)
    loc = Counter(r["location"] == "Local" for r in rows)
    print(f"{len(df)} rows read, {len(rows)} unique listings, {loc[True]} local, {loc[False]} remote")
    print("by model:", dict(c.most_common()))
    return rows


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    main(sys.argv[1], sys.argv[2])
