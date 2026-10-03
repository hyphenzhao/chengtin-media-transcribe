import io
import json
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

import pytest
from backend import ai,db,media


def add_block(pid,text='今天讨论项目方案。',start=0,end=20,state='final'):
    with db.transaction() as c:
        return db.insert(c,'block',{'raw':text,'text':text,'start':start,'end':end,'state':state,
            'segment_ids':[],'evidence_ids':[],'manual':False,'edit_revision':0},pid)


def test_project_sequence_not_reused(client):
    a=client.post('/api/projects').json();client.delete('/api/projects/'+a['id'])
    b=client.post('/api/projects').json()
    assert a['data']['title']=='新建1' and b['data']['title']=='新建2'
    assert len(client.get('/api/projects?trash=true').json())==1
    client.post('/api/projects/'+a['id']+'/restore')
    assert len(client.get('/api/projects').json())==2


def test_edit_conflict_and_separate_note_version(client,project):
    pid=project['id']
    assert client.patch('/api/projects/'+pid,json={'version':1,'data':{'title':'手工标题'}}).status_code==200
    assert client.patch('/api/projects/'+pid,json={'version':1,'data':{'title':'旧页面'}}).status_code==409
    body={'version':0,'data':{'notes':{'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'不可发给AI的笔记'}]}]}}}
    assert client.patch(f'/api/projects/{pid}/notes',json=body).status_code==200
    assert client.patch(f'/api/projects/{pid}/notes',json=body).status_code==409


def test_external_media_rejected(client,project):
    r=client.patch(f"/api/projects/{project['id']}/notes",json={'version':0,'data':{'notes':{'type':'doc','content':[{'type':'image','attrs':{'src':'https://example.com/track'}}]}}})
    assert r.status_code==422


def test_cors_and_size_validation(client,project):
    assert client.post('/api/projects',headers={'Origin':'https://attacker.example'}).status_code==403
    assert client.post(f"/api/projects/{project['id']}/uploads",json={'name':'x.mp4','size':21*1024**3}).status_code==422
    assert client.post(f"/api/projects/{project['id']}/uploads",json={'name':'x.html','size':10}).status_code==422


def test_upload_resume_integrity_play_range(client,project):
    file=io.BytesIO()
    with wave.open(file,'wb') as f:
        f.setparams((1,2,16000,16000,'NONE','not compressed'));f.writeframes(b'\x00\x00'*16000)
    content=file.getvalue();pid=project['id']
    body={'name':'静音.wav','size':len(content),'role':'primary','fingerprint':'test:one'}
    u=client.post(f'/api/projects/{pid}/uploads',json=body).json()
    assert client.post(f'/api/projects/{pid}/uploads',json=body).json()['id']==u['id']
    assert client.post(f"/api/uploads/{u['id']}/complete").status_code==409
    assert client.put(f"/api/uploads/{u['id']}/chunks/0",content=content).status_code==200
    assert client.put(f"/api/uploads/{u['id']}/chunks/0",content=content).status_code==200
    bad=bytearray(content);bad[-1]=1
    assert client.put(f"/api/uploads/{u['id']}/chunks/0",content=bytes(bad)).status_code==409
    a=client.post(f"/api/uploads/{u['id']}/complete").json()
    assert client.post(f"/api/uploads/{u['id']}/complete").json()['id']==a['id']
    ranged=client.get(f"/api/assets/{a['id']}/media",headers={'Range':'bytes=0-31'})
    assert ranged.status_code==206 and len(ranged.content)==32
    first=client.post(f'/api/projects/{pid}/transcribe').json()
    assert client.post(f'/api/projects/{pid}/transcribe').json()['id']==first['id']


@pytest.mark.parametrize('duration,regions',[(71*60+20,[]),(200,[{'start':20,'end':40},{'start':100,'end':128}]),(.4,[]),(28,[])])
def test_complete_time_coverage(duration,regions):
    chunks=media.build_chunks(duration,regions)
    assert chunks[0][0]==0 and abs(chunks[-1][1]-duration)<.01
    end=0
    for a,b in chunks:
        assert a<=end+.01 and 0<b-a<=28.001
        end=b


def test_dedup_only_boundary():
    assert media.deduplicate('this is a sentence','sentence and another')=='and another'
    assert media.deduplicate('不同的话','真的真的很重要')=='真的真的很重要'


def test_atomic_chunk_commit_and_tail(project):
    pid=project['id']
    with db.transaction() as c: job=db.new_job(c,pid,'transcribe',{})
    job['data']['duration']=28
    args=(pid,job,0,0,28,'一句短话。','zh',False,{'text':'一句短话。'},{'text':'一句短话。'},[],{'threshold':300})
    media.append_chunk(*args);media.append_chunk(*args)
    assert len(db.records('segment',pid))==1 and len(db.records('block',pid))==1
    with db.transaction() as c: media.seal_block(c,db.records('block',pid,c)[0],{'ai_enabled':False})
    assert db.records('block',pid)[0]['data']['state']=='final'
    assert db.get(job['id'])['data']['checkpoint']['chunk']==1


def test_graph_validation_and_escaping():
    graph=ai.parse_graph(json.dumps({'type':'mindmap','nodes':[{'id':'root','label':'会议<script>'},{'id':'child','label':'决定'}],'edges':[{'source':'root','target':'child'}]}))
    assert '<script>' not in ai.graph_source(graph)
    for data in [{'type':'flowchart','nodes':[{'id':'bad;click','label':'攻击'}],'edges':[]},
        {'type':'mindmap','nodes':[{'id':'a','label':'A'},{'id':'b','label':'B'}],'edges':[{'source':'a','target':'b'},{'source':'b','target':'a'}]}]:
        with pytest.raises(ValueError):ai.parse_graph(json.dumps(data))


def test_chinese_not_replaced_by_english_translation():
    sv={'text':'然后请回答下一个问题。','lang':'zh'}
    wh={'text':'Next please answer the question.','lang':'en','segments':[{'text':'Next please answer the question.','lang':'en'}]}
    text,lang,uncertain=media.choose_text(sv,wh)
    assert text==sv['text'] and lang=='zh' and uncertain
    sv={'text':'Distinguished professors, I will introduce myself.','lang':'en'}
    wh={'text':'Thank you.','segments':[{'text':'Thank you.','lang':'en'}]}
    assert media.choose_text(sv,wh)[0]==sv['text']


def test_mixed_language_alignment_uses_absolute_offsets(monkeypatch):
    requests=[]
    def recognize(req,jid):
        requests.append(req)
        return {'text':'然后回答下一道问题。','lang':'zh'}
    monkeypatch.setattr(media.sensevoice,'call',recognize)
    sv={'text':'Thank you.然后回答下一道问题。','lang':'en'}
    wh={'text':'Next question.','segments':[{'text':'Next question.','lang':'en','start':2,'end':5}]}
    text,lang,uncertain=media.align_text(sv,wh,{'path':'sample.wav','start':100,'end':128})
    assert text=='然后回答下一道问题。' and lang=='mixed'
    assert requests[0]['start']==102 and requests[0]['end']==105


def test_empty_human_edit_preserved_in_export_and_ai(client,project):
    b=add_block(project['id'])
    assert client.patch(f"/api/blocks/{b['id']}",json={'version':b['version'],'data':{'text':''}}).status_code==200
    assert '今天讨论项目方案' not in client.get(f"/api/projects/{project['id']}/export?format=srt").text
    assert '今天讨论项目方案' not in '\n'.join(ai.transcript(project['id']))


def test_generating_artifact_rejects_manual_edit(client,project):
    with db.transaction() as c:
        a=db.insert(c,'artifact',{'kind':'extract','state':'generating','text':'尚未完成'},project['id'])
    assert client.patch(f"/api/artifacts/{a['id']}",json={'version':a['version'],'data':{'text':'编辑'}}).status_code==409


def test_notes_media_validation_cannot_be_bypassed(client,project):
    bad={'type':'doc','content':[{'type':'image','attrs':{'src':'https://example.com/track.png'}}]}
    assert client.patch(f"/api/projects/{project['id']}/notes",json={'version':0,'data':{'notes':bad}}).status_code==422
    response=client.patch(f"/api/projects/{project['id']}",json={'version':project['version'],'data':{'notes':bad}})
    assert not response.json()['data']['notes']


def test_media_command_error_preserves_original_failure():
    with pytest.raises(RuntimeError,match='meaningful reason'):
        media.run(['python3','-c','import sys;sys.stderr.write("meaningful reason");sys.exit(1)'])


def test_graph_file_export_validates_svg_and_serves_attachment(client,project):
    with db.transaction() as c:
        a=db.insert(c,'artifact',{'kind':'diagram','state':'complete'},project['id'])
    svg='<svg xmlns="http://www.w3.org/2000/svg"><defs><filter id="f"><feDropShadow dx="1"/></filter></defs><text>图谱</text></svg>'
    path=f"/api/artifacts/{a['id']}/rendered?version=1&format=svg"
    response=client.post(path,content=svg.encode())
    assert response.status_code==200
    download=client.get(response.json()['url'])
    assert download.status_code==200 and 'attachment' in download.headers['content-disposition']
    assert '<text>图谱</text>' in download.text
    assert client.post(path,content=b'<svg><script>alert(1)</script></svg>').status_code==422
    assert client.post(path,content=b'<svg><use href="https://example.com/track"/></svg>').status_code==422


def test_edit_never_overwritten_by_late_ai(client,project,monkeypatch):
    pid=project['id'];b=add_block(pid)
    with db.transaction() as c:
        p=db.insert(c,'provider',{'secret':ai.encrypt('test'),'base_url':'http://unused','model':'fake'})
        job=db.new_job(c,pid,'revise',{'block_id':b['id'],'provider_id':p['id']},b['id'])
    def stream(*args,**kwargs):
        current=db.get(b['id'])
        response=client.patch(f"/api/blocks/{b['id']}",json={'version':current['version'],'data':{'text':'用户明确保留的文字'}})
        assert response.status_code==200
        yield '迟到的 AI 文字'
    monkeypatch.setattr(ai,'stream_completion',stream)
    ai.revise(job)
    d=db.get(b['id'])['data']
    assert d['text']=='用户明确保留的文字' and d['suggestion']=='迟到的 AI 文字'


def test_only_transcript_and_ocr_sent_to_ai(project,monkeypatch):
    pid=project['id'];b=add_block(pid)
    db.patch(pid,{'notes':{'private':'NEVER_SEND_THIS_SECRET'},'synopsis':'PRIVATE_SYNOPSIS'})
    with db.transaction() as c:
        p=db.insert(c,'provider',{'secret':ai.encrypt('x'),'base_url':'http://unused','model':'fake'})
        db.insert(c,'asset',{'role':'supplement','path':'PRIVATE_ATTACHMENT','name':'PRIVATE_ATTACHMENT'},pid)
        db.insert(c,'evidence',{'time':1,'end':20,'confidence':.8,'text':'PPT重要信息','path':'PRIVATE_IMAGE_PATH'},pid)
        job=db.new_job(c,pid,'revise',{'block_id':b['id'],'provider_id':p['id']},b['id'])
    captured=[]
    def stream(provider,messages,*args,**kwargs):captured.extend(messages);yield '今天讨论项目方案。'
    monkeypatch.setattr(ai,'stream_completion',stream)
    ai.revise(job)
    text=json.dumps(captured,ensure_ascii=False)
    assert 'PPT重要信息' in text
    assert all(s not in text for s in ['NEVER_SEND_THIS_SECRET','PRIVATE_ATTACHMENT','PRIVATE_IMAGE_PATH','PRIVATE_SYNOPSIS'])


def test_retry_revision_uses_latest_manual_text(project,monkeypatch):
    pid=project['id'];b=add_block(pid)
    db.patch(b['id'],{'manual':True,'text':'这是用户最新修改，不应丢弃。'})
    with db.transaction() as c:
        p=db.insert(c,'provider',{'secret':ai.encrypt('x'),'base_url':'http://unused','model':'fake'})
        job=db.new_job(c,pid,'revise',{'block_id':b['id'],'provider_id':p['id'],'suggestion':True})
    captured=[]
    def stream(provider,messages,*args,**kwargs):captured.extend(messages);yield '保留为建议。'
    monkeypatch.setattr(ai,'stream_completion',stream)
    ai.revise(job)
    assert '这是用户最新修改' in json.dumps(captured,ensure_ascii=False)
    assert db.get(b['id'])['data']['text']=='这是用户最新修改，不应丢弃。'


def test_interrupted_ai_keeps_raw_and_partial_output(project,monkeypatch):
    b=add_block(project['id'])
    with db.transaction() as c:
        job=db.new_job(c,project['id'],'revise',{'block_id':b['id'],'provider_id':'unused'})
    def stream(*args,**kwargs):
        yield '未完成的修订'
        raise RuntimeError('connection timeout')
    monkeypatch.setattr(ai,'stream_completion',stream)
    with pytest.raises(RuntimeError):ai.revise(job)
    d=db.get(b['id'])['data']
    assert d['state']=='ai_failed' and d['raw']==b['data']['raw'] and d['ai_draft']=='未完成的修订'


def test_provider_key_encrypted_and_redacted(client):
    result=client.post('/api/providers',json={'name':'接口','base_url':'https://example.com/v1','model':'m','api_key':'sensitive-test-value'})
    assert result.status_code==200
    assert 'sensitive-test-value' not in client.get('/api/providers').text
    assert 'sensitive-test-value' not in db.get(result.json()['id'])['data']['secret']


def test_invented_ocr_reference_rejected():
    with pytest.raises(ValueError):
        ai.validate_revision('会议结束。【画面补充 02:34:56：下次讨论】',[])
    evidence=[{'data':{'time':30,'text':'真实资料'}}]
    ai.validate_revision('会议结束。【画面补充 00:00:30：真实资料】',evidence)
    with pytest.raises(ValueError):
        ai.validate_revision('会议结束。【画面补充 00:00:31：其他资料】',evidence)


def test_extract_keeps_markdown_as_document_and_schedules_graph(project,monkeypatch):
    pid=project['id'];add_block(pid)
    with db.transaction() as c:
        job=db.new_job(c,pid,'extract',{'provider_id':'unused','template':'面试记录'})
    def stream(*args,**kwargs):
        yield '```markdown\n# 问题记录\n'
        yield '\n原文未提供回答\n```'
    monkeypatch.setattr(ai,'stream_completion',stream)
    ai.extract(job)
    result=db.get(job['id']+'-extract')['data']
    assert result['text']=='# 问题记录\n\n原文未提供回答'
    assert result['state']=='complete'
    assert any(j['data']['kind']=='diagram' for j in db.records('job',pid))


def test_extract_does_not_present_invented_time_as_a_source():
    text='[00:00:00] 问题。 [00:00:08] 未回答。 00:00:15 也是推测。'
    result=ai.source_timestamps(text,['[00:00:00] 说话人1\n问题。'])
    assert result=='[00:00:00] 问题。 [时间码待核对] 未回答。 [时间码待核对] 也是推测。'


def test_exports_and_revision(client,project):
    b=add_block(project['id'],start=61,end=89)
    result=client.get(f"/api/projects/{project['id']}/export?format=srt")
    assert '00:01:01,000 --> 00:01:29,000' in result.text
    assert '今天讨论项目方案' in result.text
    assert client.patch(f"/api/blocks/{b['id']}",json={'version':b['version'],'data':{'text':'修改'}}).status_code==200
    assert db.get(project['id'])['data']['transcript_revision']==1


def test_streaming_compatible_api(project):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            for text in ['真实','流式','协议']:
                self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':text}}]})+'\n\n').encode());self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with db.transaction() as c:
            p=db.insert(c,'provider',{'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'protocol-test','secret':ai.encrypt('test')})
        assert list(ai.stream_completion(p['id'],[{'role':'user','content':'只用于协议测试'}]))==['真实','流式','协议']
        assert received[0]['stream'] is True
    finally:server.shutdown();server.server_close()
