"""Real long-recording retrieval through a local model, using an isolated test database."""
import json
import os
import sys
import tempfile
from pathlib import Path
import httpx

root=Path(__file__).resolve().parent.parent
qa=Path(tempfile.mkdtemp(prefix='chengtin-chat-check-'))
os.environ['MEDIA_DATA']=str(qa/'data')
os.environ['MEDIA_KEY_FILE']=str(qa/'key')
sys.path.insert(0,str(root))
from backend import ai,db

with httpx.Client(base_url='http://127.0.0.1:3401',trust_env=False) as client:
    projects=client.get('/api/projects').json()
    project=next(p for p in projects if p['data']['title']=='验收 · IMG_4639.MOV')
    source=client.get('/api/projects/'+project['id']).json()
rows=[f"[{ai.stamp(b['data']['start'])}] 说话人\n{b['data'].get('text',b['data']['raw'])}"
      for b in sorted(source['blocks'],key=lambda b:b['data']['start'])]
assert sum(map(len,rows))>14000
with db.transaction() as c:
    provider=db.insert(c,'provider',{'model':'qwen2.5:7b','base_url':'http://127.0.0.1:11434/v1','secret':ai.encrypt('')})
    job=db.new_job(c,'isolated-test','chat',{})
evidence=ai.question_evidence(provider['id'],rows,'有没有谈到抗压能力？请找出原话。',[],job['id'])
assert evidence and any('抗压' in row for row in evidence),'Local model did not retrieve the known passage'
assert all(any(row.split('] ',1)[0]+']'==original.split('] ',1)[0]+']' and row.split('] ',1)[1] in original for original in rows) for row in evidence)
report={'ok':True,'source':'Full actual IMG_4639.MOV transcript; production projects unchanged',
    'characters':sum(map(len,rows)),'provider':'local qwen2.5:7b; no external transfer',
    'question':'有没有谈到抗压能力？请找出原话。','verified_evidence':evidence}
(root/'logs/chat-long-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'ok':True,'characters':report['characters'],'quotes':len(evidence)},ensure_ascii=False),flush=True)
