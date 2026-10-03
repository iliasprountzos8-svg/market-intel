import time, torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification
torch.set_num_threads(3)
t0 = time.time()
name = "ProsusAI/finbert"
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForSequenceClassification.from_pretrained(name).eval()
print("model loaded in", round(time.time() - t0, 1), "s | labels:", model.config.id2label)
texts = ["Nvidia beats earnings estimates and raises guidance, shares surge", "Microsoft faces antitrust probe as cloud growth slows",
         "Fed holds rates steady, says inflation remains elevated", "Micron warns of memory shortage lasting through next year"] * 16
t1 = time.time()
with torch.inference_mode():
    for i in range(0, len(texts), 16):
        b = tok(texts[i:i+16], padding=True, truncation=True, max_length=128, return_tensors="pt")
        p = torch.softmax(model(**b).logits, dim=-1)
dt = time.time() - t1
print(f"{len(texts)} texts in {dt:.1f}s = {len(texts)/dt:.1f}/s")
print("last probs (pos,neg,neu):", [round(float(x), 2) for x in p[0]])
