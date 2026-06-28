"""Smoke test: download 3 distinct Exhibits from M12205 and zip them.
Run:  .venv\Scripts\python test_scraper.py
"""
import os
os.environ["HEADLESS"] = "false"
os.environ["DOWNLOAD_DIR"] = "downloads"
os.environ["MAX_DOCS"] = "3"

from scraper import fetch_matter

result = fetch_matter("M12205", "Exhibits")

print("\n=== RESULT ===")
print(f"  Matter:   {result.matter}")
print(f"  Title:    {result.title}")
print(f"  Counts:   {result.counts}")
print(f"  Files:    {len(result.downloaded_files)}")
import pathlib, hashlib
seen_hashes = set()
all_distinct = True
for f in result.downloaded_files:
    p = pathlib.Path(f)
    if p.exists():
        h = hashlib.md5(p.read_bytes()).hexdigest()[:12]
        dup = h in seen_hashes
        seen_hashes.add(h)
        print(f"            {p.name}  ({p.stat().st_size:,} bytes)  md5={h}  {'[DUP!]' if dup else ''}")
        if dup: all_distinct = False
    else:
        print(f"            {p.name}  [MISSING]")
print(f"  All distinct: {all_distinct}")
print(f"  ZIP:      {result.zip_path}")
if result.error:
    print(f"  ERROR:    {result.error}")
