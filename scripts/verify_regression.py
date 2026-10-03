"""Audit committed coverage and model provenance after the actual long-video jobs."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from backend import db

last={}
while True:
    projects=[p for p in db.records('project') if p['data']['title'].startswith('验收 · IMG_')]
    pending=False
    for p in projects:
        jobs=db.records('job',p['id'])
        active=[j for j in jobs if j['data']['state'] in ('queued','running')]
        pending=pending or bool(active)
        status=','.join(j['data']['kind']+':'+j['data']['state'] for j in jobs)
        if last.get(p['id'])!=status:print(p['data']['title'],status,flush=True);last[p['id']]=status
    if not pending:break
    if '--wait' not in sys.argv:raise SystemExit('Real regression jobs still running')
    time.sleep(10)

report={'ok':True,'videos':[],'note':'Coverage measures processed time, not recognition accuracy. No external AI was used on these full recordings.'}
for p in projects:
    pid=p['id'];a=db.get(p['data']['primary_id'])['data'];duration=a['duration']
    segments=sorted(db.records('segment',pid),key=lambda r:r['data']['chunk'])
    upto=0;gaps=[];indexes=[]
    for s in segments:
        d=s['data'];indexes.append(d['chunk'])
        if d['start']>upto+.05:gaps.append([upto,d['start']])
        upto=max(upto,d['end'])
        assert 'whisper' in d and 'sensevoice' in d
    if upto<duration-.1:gaps.append([upto,duration])
    assert not gaps and indexes==list(range(len(indexes)))
    jobs=db.records('job',pid)
    assert all(j['data']['state']=='complete' for j in jobs)
    blocks=db.records('block',pid)
    assert all(b['data']['state']=='final' for b in blocks)
    assert Path(a.get('proxy_path') or a['path']).exists()
    report['videos'].append({'name':a['name'],'duration':duration,'covered_to':upto,'chunks':len(segments),
        'blocks':len(blocks),'characters':sum(len(b['data']['text']) for b in blocks),'ocr_evidence':len(db.records('evidence',pid)),
        'speakers':len(db.records('speaker',pid)),'gaps':gaps,'attempts':{j['data']['kind']:j['data']['attempt'] for j in jobs}})
path=db.ROOT/'logs/regression-verification.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False),flush=True)
