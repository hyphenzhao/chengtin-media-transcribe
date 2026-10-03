"""End-to-end AI protocol test using the server's already installed local model."""
import json
import os
import sys
import tempfile
from pathlib import Path
root=Path(__file__).resolve().parent.parent
qa=Path(tempfile.mkdtemp(prefix='chengtin-ai-check-'))
os.environ['MEDIA_DATA']=str(qa/'data')
os.environ['MEDIA_KEY_FILE']=str(qa/'key')
sys.path.insert(0,str(root))
from backend import ai,db
from backend.api import create_project

p=create_project();pid=p['id']
with db.transaction() as c:
    provider=db.insert(c,'provider',{'name':'Local verification only','model':'qwen2.5:7b','base_url':'http://127.0.0.1:11434/v1','secret':ai.encrypt('')})
    block=db.insert(c,'block',{'raw':'今天我们讨论知识库项目。小王负责整理原始材料，周五前完成。小李负责界面设计，下周一提交初稿。数据库方案还没有决定，需下次会议讨论。','text':'','start':0,'end':40,'state':'queued','manual':False,'edit_revision':0,'segment_ids':[]},pid)
    job=db.new_job(c,pid,'revise',{'provider_id':provider['id'],'block_id':block['id']})
ai.revise(job)
print('REVISION',db.get(block['id'])['data']['text'],flush=True)
with db.transaction() as c:
    job=db.new_job(c,pid,'extract',{'provider_id':provider['id'],'template':'会议纪要','source_revision':db.get(pid,c)['data']['transcript_revision']})
ai.extract(job)
print('EXTRACTION_READY',flush=True)
job=next(j for j in db.records('job',pid) if j['data']['kind']=='diagram')
ai.diagram(job)
artifacts=db.records('artifact',pid)
assert all(a['data']['state']=='complete' for a in artifacts)
report={'ok':True,'provider':'local qwen2.5:7b','source':'synthetic verification material; no external transmission',
    'transcript':db.get(block['id'])['data']['text'],'artifacts':[a['data'] for a in artifacts]}
(root/'logs/ai-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('AI_END_TO_END_PASS',[(a['data']['kind'],a['data']['state']) for a in artifacts],flush=True)
