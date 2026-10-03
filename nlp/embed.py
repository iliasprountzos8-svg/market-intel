"""Free, local sentence embeddings for headlines (all-MiniLM-L6-v2, 22M parameters, CPU only).

    from embed import Embedder
    vecs = Embedder().encode(["Nvidia beats estimates", "Nvidia tops Wall Street forecasts"])   # unit-length float32 rows
The model downloads once (about 90 MB) and is cached; afterwards it runs offline. Uses only torch + transformers, which the
FinBERT step already needs, so nothing new is installed.
"""
import os
import sys
from pathlib import Path

import numpy as np

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))


class Embedder:
    def __init__(self, threads=2, offline_if_cached=True):
        import torch
        from transformers import AutoModel, AutoTokenizer
        torch.set_num_threads(threads)
        self.torch = torch
        cache = Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + MODEL.replace("/", "--"))
        if offline_if_cached and cache.exists():
            os.environ["HF_HUB_OFFLINE"] = "1"
        self.tok = AutoTokenizer.from_pretrained(MODEL)
        self.model = AutoModel.from_pretrained(MODEL).eval()

    def encode(self, texts, batch=64):
        out = []
        with self.torch.inference_mode():
            for i in range(0, len(texts), batch):
                enc = self.tok(texts[i:i + batch], padding=True, truncation=True, max_length=48, return_tensors="pt")
                hidden = self.model(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).float()
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                out.append(self.torch.nn.functional.normalize(pooled, dim=1).numpy())
        return np.vstack(out).astype(np.float32) if out else np.zeros((0, 384), dtype=np.float32)


def clean(title):
    """Same normalisation the clustering uses (publisher tails, ticker tags, 'BREAKING:' prefixes removed)."""
    import story_logic as sl
    return sl.normalize_title(title)
