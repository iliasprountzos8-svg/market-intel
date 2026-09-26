"""Pure logic for merging stories that report the same event in different words (needs numpy only).

Embedding similarity finds candidates; guards decide. A merge needs ALL of: cosine >= threshold, first publication within the
time window, compatible companies (feed tickers and named entities must overlap when both sides have them), and compatible
numbers (two headlines that both quote figures but share none are different events). Precision matters more than recall:
a missed merge costs a duplicate line, a wrong merge hides a real story.
"""
import re

import numpy as np

WINDOW_S = 72 * 3600
_FORWARD = re.compile(r"preview|ahead of|to report|what to expect|expected to|likely to|set to (?:report|announce)|will report|next week|upcoming|could\b|may\b", re.I)
_RESULT = re.compile(r"\bbeats?\b|\bmiss(?:es|ed)?\b|\bposts?\b|\breports?(?:ed)?\b|\btops\b|\bresults\b|\bslumps?\b|\bsurges?\b|\bplunges?\b|\bsoars?\b|\bfell\b|\brose\b|\bdeclares?\b", re.I)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def latin_share(title):
    """Share of alphabetic characters that are plain ASCII letters. The embedding model only understands English: Greek or Thai
    headlines all look alike to it (found by inspection: unrelated Greek stories scored 0.6-0.9), so they are never merged this way."""
    letters = [c for c in (title or "") if c.isalpha()]
    return sum(1 for c in letters if c.isascii()) / len(letters) if letters else 0.0


def numbers_of(title):
    """Significant figures in a headline: no small ordinals, no years."""
    out = set()
    for m in _NUM.findall(title or ""):
        v = m.replace(",", "")
        if "." not in v and (len(v) < 2 or (len(v) == 4 and 2019 <= int(v) <= 2031)):
            continue
        out.add(v.rstrip("0").rstrip(".") if "." in v else v)
    return out


def numbers_compatible(na, nb, tol=0.12):
    """Both headlines quote figures: they must share one, or have two within ~12% of each other (12 vs 11.6 billion is the same deal)."""
    if not na or not nb:
        return True
    if na & nb:
        return True
    fa, fb = [float(x) for x in na], [float(x) for x in nb]
    # Tolerance only for figures that can plausibly be rounded (decimals or 3+ digits); small integers such as dates must match exactly.
    return any(abs(x - y) <= tol * max(abs(x), abs(y)) and (x != int(x) or y != int(y) or max(x, y) >= 100) for x in fa for y in fb)


def gates(a, b):
    """a, b: dicts with title, tickers(set), ents(set), first_pub(seconds). Returns (ok, reason)."""
    if abs(a["first_pub"] - b["first_pub"]) > WINDOW_S:
        return False, "time"
    if a["tickers"] and b["tickers"] and not (a["tickers"] & b["tickers"]):
        return False, "tickers"  # strict on purpose: templated headlines for different companies score very high on similarity
    if a["ents"] and b["ents"] and not (a["ents"] & b["ents"]):
        return False, "entities"
    if not numbers_compatible(numbers_of(a["title"]), numbers_of(b["title"])):
        return False, "numbers"
    fa, fb = bool(_FORWARD.search(a["title"])), bool(_FORWARD.search(b["title"]))
    ra, rb = bool(_RESULT.search(a["title"])), bool(_RESULT.search(b["title"]))
    if (fa and not ra and rb and not fb) or (fb and not rb and ra and not fa):
        return False, "preview_vs_result"  # "X earnings preview" and "X beats estimates" are different developments
    return True, "ok"


def candidate_pairs(emb, min_cos, chunk=1024):
    """All index pairs i<j with cosine >= min_cos. emb is an (n, d) matrix of unit vectors."""
    n = emb.shape[0]
    out = []
    for s in range(0, n, chunk):
        sims = emb[s:s + chunk] @ emb.T
        rows, cols = np.nonzero(sims >= min_cos)
        for r, c in zip(rows, cols):
            i = s + int(r)
            if c > i:
                out.append((i, int(c), float(sims[r, c])))
    return out


class UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def merge_groups(stories, pairs, min_cos, max_cluster=12):
    """Greedy agglomeration, highest similarity first. Two clusters join only if EVERY cross pair passes the gates, so a story
    can never be chained into a group through a neighbour that lacks the information to veto it. stories: list of dicts (see
    gates) in embedding-row order. Returns [[indices...], ...] with len > 1, earliest first publication first (that one is kept)."""
    members = {i: [i] for i in range(len(stories))}
    uf = UnionFind(len(stories))
    cosmap = {(i, j): c for i, j, c in pairs}
    for i, j, cos in sorted(pairs, key=lambda p: -p[2]):
        if cos < min_cos:
            break
        ri, rj = uf.find(i), uf.find(j)
        if ri == rj:
            continue
        a, b = members[ri], members[rj]
        if len(a) + len(b) > max_cluster:
            continue
        # complete-link: EVERY cross pair must be similar enough (a pair missing from `pairs` is below the floor) and pass the guards,
        # so a group cannot drift from one development to a different one of the same topic through a chain of neighbours
        if all(cosmap.get((min(x, y), max(x, y)), 0.0) >= min_cos and gates(stories[x], stories[y])[0] for x in a for y in b):
            uf.union(ri, rj)
            root = uf.find(ri)
            members[root] = a + b
            for k in (ri, rj):
                if k != root:
                    members.pop(k, None)
    out = [sorted(g, key=lambda k: (stories[k]["first_pub"], stories[k].get("id", 0))) for g in members.values() if len(g) > 1]
    return out
