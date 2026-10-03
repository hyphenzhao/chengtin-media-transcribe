"""Exercise real local models with Python Internet sockets denied, isolated data."""
import json
import os
import sys
import tempfile
from pathlib import Path

root=Path(__file__).resolve().parent.parent
qa=Path(tempfile.mkdtemp(prefix='chengtin-offline-'))
guard=qa/'guard';guard.mkdir()
(guard/'sitecustomize.py').write_text('''import socket
original_connect=socket.socket.connect
def offline_connect(self,address):
    if self.family in (socket.AF_INET,socket.AF_INET6):
        raise RuntimeError("OFFLINE VERIFICATION: Internet socket denied")
    return original_connect(self,address)
socket.socket.connect=offline_connect
''')
os.environ['PYTHONPATH']=str(guard)
sys.path.insert(0,str(guard));import sitecustomize
os.environ['MEDIA_DATA']=str(qa/'data')
os.environ['MEDIA_KEY_FILE']=str(qa/'key')
sys.path.insert(0,str(root))
from backend import db,media
from backend.api import create_project

report={'network':'Python IPv4/IPv6 outbound connects denied in parent and model subprocesses','checks':{}}
if os.environ.get('VERIFY_NETWORK_NAMESPACE'):
    report['network']='Linux isolated network namespace with no interfaces or routes; other services unaffected'
    report['routes']=Path('/proc/net/route').read_text()
source=root/'data/projects/2d31436827dd47a59e63d3a89962787c/processing/audio.wav'
speech=qa/'speech.wav'
media.run(['ffmpeg','-v','error','-y','-ss','370','-t','55','-i',str(source),str(speech)])
silent=qa/'silent.wav'
media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','8',str(silent)])
try:
    for engine in (media.whisper,media.sensevoice):
        result=engine.call({'path':str(speech),'start':0,'end':25,'language':'auto'})
        assert result['text'];report['checks'][engine.kind]=result['text']
        quiet=engine.call({'path':str(silent),'start':0,'end':8,'language':'auto'})
        assert not quiet['text'];report['checks'][engine.kind+'_silence']='no fabricated text'
    p=create_project();pid=p['id']
    with db.transaction() as c:
        j=db.new_job(c,pid,'transcribe',{})
        db.insert(c,'block',{'start':0,'end':55,'raw':'验证','text':'验证','state':'final'},pid)
    turns=media.diarize(str(speech),pid,j['id'])
    assert turns;report['checks']['speaker_turns']=turns
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(1280,720),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',60)
    draw.text((80,280),'OFFLINE TEST 3401',fill='black',font=font)
    image.save(qa/'slide.png')
    video=qa/'no-audio.mp4'
    media.run(['ffmpeg','-v','error','-y','-loop','1','-i',str(qa/'slide.png'),'-t','6','-c:v','libx264','-pix_fmt','yuv420p',str(video)])
    meta=media.probe(video);assert not meta['has_audio']
    with db.transaction() as c:
        asset=db.insert(c,'asset',{'path':str(video),**meta},pid)
        db.update(c,j['id'],{'payload':{'asset_id':asset['id'],'settings':{'ocr':True,'diarization':True,'ai_enabled':False,'threshold':300}}})
    media.transcribe(db.get(j['id']))
    evidence=db.records('evidence',pid)
    assert any('OFFLINE TEST' in e['data']['text'] for e in evidence)
    assert any('[画面文字' in b['data']['raw'] for b in db.records('block',pid))
    report['checks']['video_without_audio']=[e['data']['text'] for e in evidence]
    from fastapi.testclient import TestClient
    from backend.api import app
    with TestClient(app) as client:
        p=client.post('/api/projects').json();pid=p['id']
        content=speech.read_bytes()
        u=client.post(f'/api/projects/{pid}/uploads',json={'name':'speech.wav','size':len(content),'fingerprint':'offline-verification'}).json()
        uid=u['id']
        assert client.put(f'/api/uploads/{uid}/chunks/0',content=content).status_code==200
        a=client.post(f'/api/uploads/{uid}/complete').json()
        play=client.get(f'/api/assets/{a["id"]}/media',headers={'Range':'bytes=0-127'})
        assert play.status_code==206 and len(play.content)==128
        with db.transaction() as c:
            b=db.insert(c,'block',{'raw':'离线保存','text':'离线保存','start':0,'end':10,'state':'final'},pid)
        assert client.patch(f'/api/blocks/{b["id"]}',json={'version':b['version'],'data':{'text':'离线编辑已保存'}}).status_code==200
        assert '离线编辑已保存' in client.get(f'/api/projects/{pid}/export?format=md').text
        report['checks']['offline_api']=['upload','Range playback','versioned editing','Markdown export']
    report['ok']=True
finally:
    media.whisper.stop();media.sensevoice.stop()
(root/'logs/local-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False),flush=True)
