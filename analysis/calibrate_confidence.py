"""Check if higher confidence calls actually have a higher hit rate.
Run: python calibrate_confidence.py
"""
import os
import sys
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY", file=sys.stderr)
        sys.exit(1)
    return create_client(url, key)

def get_bucket(conf):
    if conf is None:
        return "Unknown"
    if conf < 40:
        return "Low (0-39)"
    if conf < 70:
        return "Medium (40-69)"
    return "High (70-100)"

def main():
    client = get_client()
    res = (
        client.table("ai_calls_log")
        .select("confidence,outcome")
        .not_.is_("outcome", "null")
        .neq("outcome", "unclear")
        .execute()
    )
    
    rows = res.data
    if not rows:
        print("No decided calls found yet.")
        return
        
    buckets = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in rows:
        b = get_bucket(row.get("confidence"))
        buckets[b]["total"] += 1
        if row["outcome"] == "correct":
            buckets[b]["correct"] += 1
            
    print(f"{'Confidence Bucket':<20} | {'Hit Rate':<10} | {'Sample Size'}")
    print("-" * 50)
    
    for bucket in sorted(buckets.keys()):
        stats = buckets[bucket]
        if stats["total"] > 0:
            rate = (stats["correct"] / stats["total"]) * 100
            print(f"{bucket:<20} | {rate:5.1f}%     | {stats['total']}")

if __name__ == "__main__":
    main()
