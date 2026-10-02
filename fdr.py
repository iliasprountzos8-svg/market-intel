"""Benjamini-Hochberg false-discovery-rate correction.

Used to correct for multiple-testing across a family of related hypothesis tests
(e.g. several strategies x horizons in the walk-forward lab, or several
model x metric x horizon x benchmark combinations in the news signal-eval).
Bonferroni is too conservative for this kind of exploratory research; BH-FDR
controls the expected proportion of false discoveries among the rejected
hypotheses at level q, which is the standard choice here.
"""
from __future__ import annotations


def bh_fdr(pvalues, q: float = 0.10):
    """Benjamini-Hochberg procedure.

    Args:
        pvalues: sequence of raw p-values (may contain None/NaN; those are
            treated as never-rejected and passed through unchanged).
        q: target FDR level (default 0.10).

    Returns:
        (rejected, thresholds) where `rejected` is a list of bool, same
        order/length as `pvalues` (True = survives FDR correction at level q),
        and `thresholds` is the BH critical value line (i/m * q) recorded for
        transparency, aligned to the sorted order internally but returned in
        the original order as the critical value each p-value was compared to.
    """
    n = len(pvalues)
    rejected = [False] * n
    crit = [None] * n
    # index, p pairs for valid (non-null) p-values
    valid = [(i, p) for i, p in enumerate(pvalues) if p is not None and p == p]  # p==p filters NaN
    m = len(valid)
    if m == 0:
        return rejected, crit
    valid.sort(key=lambda ip: ip[1])
    # BH: find the largest k such that p_(k) <= (k/m) * q; reject all p_(1..k)
    largest_k = 0
    for rank, (_, p) in enumerate(valid, start=1):
        threshold = (rank / m) * q
        if p <= threshold:
            largest_k = rank
    for rank, (orig_i, p) in enumerate(valid, start=1):
        crit[orig_i] = (rank / m) * q
        if rank <= largest_k:
            rejected[orig_i] = True
    return rejected, crit
