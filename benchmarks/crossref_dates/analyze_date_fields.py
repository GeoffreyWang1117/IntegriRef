import collections, json, urllib.parse
from pathlib import Path

root=Path(__file__).resolve().parent
data=root.parent/'data'
fixture=sorted(p for p in data.glob('preprint_hard_negatives_2*.jsonl') if 'crossref' not in p.name)[-1]
cache=root/'cache'
rows=[json.loads(s) for s in fixture.read_text(encoding='utf-8').splitlines() if s]
fields=('published-print','published-online','issued','posted')
stats={f:{'available':0,'flagged_gt2':0,'tolerated_le2':0,'missing_field_success':0,'malformed_field':0,'unknown_fetch':0,'delta':collections.Counter(),'category':collections.Counter(),'by_delta':collections.defaultdict(lambda:{'available':0,'flagged_gt2':0})} for f in fields}
record_outcomes=[]
fetch_success=fetch_failed=cache_hits=0

for row in rows:
    doi=row['registered']['doi']
    p=cache/(urllib.parse.quote(doi,safe='')+'.json')
    if not p.exists():
        fetch_failed+=1
        for f in fields: stats[f]['unknown_fetch']+=1
        continue
    data=json.loads(p.read_text(encoding='utf-8'))
    cache_hits+=1
    if '_fetch_error' in data:
        fetch_failed+=1
        for f in fields: stats[f]['unknown_fetch']+=1
        continue
    fetch_success+=1
    message=data.get('message') or {}
    row_out={'case_id':row['case_id'],'delta':row['delta'],'category':row['category'],'arxiv_year':row['arxiv_year'],'journal_ref_year':row['registered']['year'],'crossref':{}}
    for f in fields:
        dp=message.get(f,{}).get('date-parts') if isinstance(message.get(f),dict) else None
        if not dp:
            stats[f]['missing_field_success']+=1
            row_out['crossref'][f]=None
            continue
        try: year=int(dp[0][0])
        except (IndexError,TypeError,ValueError):
            stats[f]['malformed_field']+=1
            row_out['crossref'][f]='MALFORMED'
            continue
        gap=abs(year-int(row['arxiv_year']))
        s=stats[f]
        s['available']+=1
        s['by_delta'][str(row['delta'])]['available']+=1
        if gap>2:
            s['flagged_gt2']+=1; s['delta'][str(row['delta'])]+=1; s['category'][row['category']]+=1; s['by_delta'][str(row['delta'])]['flagged_gt2']+=1
        else: s['tolerated_le2']+=1
        row_out['crossref'][f]={'year':year,'gap_from_arxiv_year':gap,'flagged_gt2':gap>2}
    record_outcomes.append(row_out)

print(json.dumps({'fixture_rows':len(rows),'successful_responses':fetch_success,'failed_or_unknown':fetch_failed,'cache_files_read':cache_hits,'field_stats':{f:{k:(dict(v) if isinstance(v,collections.Counter) else {d:dict(t) for d,t in v.items()} if isinstance(v,collections.defaultdict) else v) for k,v in s.items()} for f,s in stats.items()}},ensure_ascii=False))
