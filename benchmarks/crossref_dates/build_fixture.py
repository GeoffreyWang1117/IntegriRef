import datetime, json, urllib.parse
from pathlib import Path
import sys

root=Path(__file__).resolve().parent
data=root.parent/'data'
src=sorted(p for p in data.glob('preprint_hard_negatives_2*.jsonl') if 'crossref' not in p.name)[-1]
cache=root/'cache'
fields=('published-print','published-online','issued','posted')
rows=[json.loads(s) for s in src.read_text(encoding='utf-8').splitlines() if s]
out=[]
for row in rows:
    doi=row['registered']['doi']
    p=cache/(urllib.parse.quote(doi,safe='')+'.json')
    if not p.exists():
        error={'reason':'cache_file_missing; response status unknown'}
        dates={f:{'status':'fetch_failed','date-parts':None,'error':error} for f in fields}
    else:
        data=json.loads(p.read_text(encoding='utf-8'))
        if '_fetch_error' in data:
            error=data['_fetch_error']
            dates={f:{'status':'fetch_failed','date-parts':None,'error':error} for f in fields}
        else:
            msg=data.get('message') or {}
            dates={}
            for f in fields:
                value=msg.get(f)
                dp=value.get('date-parts') if isinstance(value,dict) else None
                if dp:
                    try: year=int(dp[0][0])
                    except (IndexError,TypeError,ValueError):
                        dates[f]={'status':'malformed','date-parts':dp}
                    else: dates[f]={'status':'present','date-parts':dp,'year':year}
                else: dates[f]={'status':'field_absent','date-parts':None}
    row['crossref_dates']=dates
    row['crossref_source']={'endpoint':'/works/{doi}','cache_file':p.name}
    out.append(json.dumps(row,ensure_ascii=False,separators=(',',':')))
start=int(sys.argv[1]) if len(sys.argv)>1 else 0
limit=int(sys.argv[2]) if len(sys.argv)>2 else len(out)
stamp=sys.argv[3] if len(sys.argv)>3 else datetime.datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')
name=f'preprint_hard_negatives_crossref_{stamp}.jsonl'
print(json.dumps({'stamp':stamp,'fixture':name,'rows':len(out),'start':start,'limit':limit}))
print('\n'.join(out[start:start+limit]))
