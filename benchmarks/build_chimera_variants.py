"""Build two new chimera variants for decomposition analysis:
  - author-only swap (year unchanged)
  - year-only shift (authors unchanged)
Both use the same 60 base DOIs as the combined version, for direct comparison.
"""
import json, random
random.seed(42)

with open('benchmarks/data/hf_export/retracted_papers.jsonl') as f:
    cases = [json.loads(l) for l in f]
real = [c for c in cases if c['category'] == 'real_control' and c.get('doi') and c.get('title')]


def authors_list(c):
    a = c.get('authors', '')
    if isinstance(a, str):
        try:
            return json.loads(a)
        except:
            return []
    return a or []


real = [c for c in real if authors_list(c)]
random.shuffle(real)
n = 60
bases = real[:n]
donors = real[n:2 * n]

author_only = []
year_only = []
for i, (base, donor) in enumerate(zip(bases, donors)):
    base_authors = authors_list(base)
    donor_authors = authors_list(donor)
    if not donor_authors:
        continue
    try:
        base_year = int(base.get('year', '2020'))
    except Exception:
        base_year = 2020

    # author-only: swap authors, keep year
    author_only.append({
        'id': f'idchim_a_{i:03d}',
        'category': 'chimera',
        'doi': base['doi'],
        'title': base['title'],
        'authors': json.dumps(donor_authors),
        'year': base.get('year', ''),
        'venue': base.get('venue', ''),
        'expected_found': True, 'expected_retracted': False, 'expected_risk': 'high',
    })
    # year-only: keep authors, shift year by 7
    year_only.append({
        'id': f'idchim_y_{i:03d}',
        'category': 'chimera',
        'doi': base['doi'],
        'title': base['title'],
        'authors': base.get('authors'),
        'year': str(base_year - 7),
        'venue': base.get('venue', ''),
        'expected_found': True, 'expected_retracted': False, 'expected_risk': 'high',
    })

# Same controls as before
controls = []
for i, base in enumerate(bases):
    controls.append({
        'id': f'idreal_{i:03d}',
        'category': 'real_control',
        'doi': base['doi'],
        'title': base['title'],
        'authors': base.get('authors'),
        'year': base.get('year'),
        'venue': base.get('venue', ''),
        'expected_found': True, 'expected_retracted': False, 'expected_risk': 'low',
    })

with open('benchmarks/data/idchim_author_only.jsonl', 'w') as f:
    for r in author_only + controls:
        f.write(json.dumps(r) + '\n')
with open('benchmarks/data/idchim_year_only.jsonl', 'w') as f:
    for r in year_only + controls:
        f.write(json.dumps(r) + '\n')
print(f'Built author-only: {len(author_only)} chimeras + {len(controls)} controls')
print(f'Built year-only:   {len(year_only)} chimeras + {len(controls)} controls')
