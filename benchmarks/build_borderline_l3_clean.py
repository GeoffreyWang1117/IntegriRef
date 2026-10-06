"""Build Borderline-L3 clean split.

Constructs 20 'clean bibliographies' (one per topic) by sampling top-cited
real Crossref papers from the same topic file. These act as controls for
the 22-case anomaly split: an L3 graph anomaly detector that fires on these
clean topic-coherent bibliographies is a false positive.
"""
import json, random
from pathlib import Path

random.seed(42)
BENCH_DIR = Path(__file__).parent / "data"
CRAWLED = BENCH_DIR / "crawled"

clean_bibs = []
for f in sorted(CRAWLED.glob("crossref_real_*.jsonl")):
    topic = f.stem.replace("crossref_real_", "")
    papers = [json.loads(l) for l in open(f)]
    if len(papers) < 6:
        continue
    # Sort by citation count, take top 7
    papers.sort(key=lambda p: int(p.get("citation_count", 0) or 0), reverse=True)
    top = papers[:7]
    dois = [p["doi"] for p in top if p.get("doi")]
    if len(dois) < 6:
        continue
    # Carry full metadata per DOI so the bench can pass proper refs to L0
    papers_meta = [{"doi": p["doi"], "title": p.get("title", ""),
                    "authors": p.get("authors", []), "year": str(p.get("year", "")),
                    "venue": p.get("venue", "")} for p in top if p.get("doi")]
    clean_bibs.append({
        "id": f"clean_bib_{topic}",
        "category": "clean_bibliography",
        "topic": topic,
        "description": f"Top {len(dois)} cited Crossref real papers on '{topic}' (topic-coherent clean bibliography)",
        "dois": dois,
        "papers": papers_meta,
        "expected_anomaly": False,
    })

out_path = BENCH_DIR / "borderline_l3_clean.jsonl"
with open(out_path, "w") as f:
    for b in clean_bibs:
        f.write(json.dumps(b) + "\n")
print(f"Built {len(clean_bibs)} clean bibliographies → {out_path}")
for b in clean_bibs[:5]:
    print(f"  {b['id']}: {len(b['dois'])} DOIs ({b['topic']})")
