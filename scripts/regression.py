"""Run the actual media pipeline on the two source videos, keeping evidence local."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from backend import db
from backend.api import create_project
from backend.media import probe

source=Path('/Volumes/Workspace')/'面试过程记录'/'01_原始录像'
for name in ['IMG_4639.MOV','IMG_4640.MOV']:
    title='验收 · '+name
    found=next((p for p in db.records('project') if p['data']['title']==title),None)
    if found:
        print(name,found['id'],'exists',flush=True);continue
    p=create_project();pid=p['id']
    folder=db.DATA/'projects'/pid/'original';folder.mkdir(parents=True,exist_ok=True)
    dest=folder/name
    os.link(source/name,dest)
    metadata=probe(dest)
    with db.transaction() as c:
        a=db.insert(c,'asset',{'name':name,'size':dest.stat().st_size,'path':str(dest),'role':'primary','state':'preparing',**metadata},pid)
        db.update(c,pid,{'title':title,'primary_id':a['id'],'synopsis':'使用原始录像验证本地转写、低音量覆盖、视频 OCR 与说话人识别。未启用外部 AI。'})
        db.new_job(c,pid,'prepare',{'asset_id':a['id']},a['id'])
        job=db.new_job(c,pid,'transcribe',{'asset_id':a['id'],'settings':p['data']['settings']})
    print(name,pid,job['id'],flush=True)
