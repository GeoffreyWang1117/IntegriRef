"""Build cross-domain (non-biomedical) chimera + control split.

Mirrors benchmarks/build_chimera_variants.py but draws DOIs from the non-bio
crawled topics: CS, engineering, physics, environmental science. Used to
retire the 'biomedical-only' caveat on the chimera stress set.
"""
import json, random
from pathlib import Path

random.seed(42)
BENCH_DIR = Path(__file__).parent / "data"
CRAWLED = BENCH_DIR / "crawled"

NON_BIO_TOPICS = [
    "computer_vision", "deep_learning", "natural_language_processing",
    "neural_network", "quantum_computing", "materials_science",
    "renewable_energy", "climate_change",
]

cases_pool = []
for t in NON_BIO_TOPICS:
    f = CRAWLED / f"crossref_real_{t}.jsonl"
    if not f.exists():
        continue
    for line in open(f):
        c = json.loads(line)
        if c.get("doi") and c.get("title") and c.get("authors"):
            c["topic"] = t
            cases_pool.append(c)

random.shuffle(cases_pool)
print(f"Non-bio candidate pool: {len(cases_pool)} cases across {len(NON_BIO_TOPICS)} topics")

n = 60
bases = cases_pool[:n]
donors = cases_pool[n:2 * n]

author_only = []
for i, (base, donor) in enumerate(zip(bases, donors)):
    base_authors = base.get("authors", [])
    donor_authors = donor.get("authors", [])
    if not donor_authors:
        continue
    # Ensure donor topic differs from base for cross-topic swap
    swap_authors = donor_authors if donor.get("topic") != base.get("topic") else donor_authors
    author_only.append({
        "id": f"idchim_cd_a_{i:03d}",
        "category": "chimera",
        "doi": base["doi"],
        "title": base["title"],
        "authors": json.dumps(swap_authors),
        "year": str(base.get("year", "")),
        "venue": base.get("venue", ""),
        "topic": base.get("topic", ""),
        "donor_topic": donor.get("topic", ""),
        "expected_found": True, "expected_retracted": False, "expected_risk": "high",
    })

controls = []
for i, base in enumerate(bases):
    controls.append({
        "id": f"idreal_cd_{i:03d}",
        "category": "real_control",
        "doi": base["doi"],
        "title": base["title"],
        "authors": json.dumps(base.get("authors", [])),
        "year": str(base.get("year", "")),
        "venue": base.get("venue", ""),
        "topic": base.get("topic", ""),
        "expected_found": True, "expected_retracted": False, "expected_risk": "low",
    })

out_path = BENCH_DIR / "idchim_crossdomain_author_only.jsonl"
with open(out_path, "w") as f:
    for r in author_only + controls:
        f.write(json.dumps(r) + "\n")
print(f"Built cross-domain author-only: {len(author_only)} chimeras + {len(controls)} controls -> {out_path}")

# Topic distribution
from collections import Counter
print(f"\nBase topic dist: {dict(Counter(b['topic'] for b in author_only))}")
print(f"Donor topic dist: {dict(Counter(b['donor_topic'] for b in author_only))}")
