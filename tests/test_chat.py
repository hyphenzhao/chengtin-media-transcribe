import json
import pytest
from backend import ai, db


def ready_project(project):
    pid=project['id']
    with db.transaction() as c:
        provider=db.insert(c,'provider',{'secret':ai.encrypt('test'),'base_url':'http://unused','model':'fake'})
        db.update(c,pid,{'settings':{**project['data']['settings'],'provider_id':provider['id']},
            'notes':{'private':'PRIVATE_NOTE_NEVER_SEND'}})
        block=db.insert(c,'block',{'raw':'小王负责整理材料，周五完成。','text':'小王负责整理材料，周五完成。',
            'state':'final','start':30,'end':60,'manual':False},pid)
        extract=db.insert(c,'artifact',{'kind':'extract','state':'complete','text':'小王负责材料整理。','source_revision':0},pid)
        db.insert(c,'asset',{'role':'supplement','name':'PRIVATE_ATTACHMENT','path':'/private/not-sent.wav'},pid)
    return block,extract


def ask(client,pid,question='谁负责整理材料？',request_id='question-one'):
    return client.post(f'/api/projects/{pid}/chat',json={'question':question,'request_id':request_id})


def test_question_requires_finished_extraction_and_provider(client,project):
    assert ask(client,project['id']).status_code==409
    _,a=ready_project(project)
    db.patch(a['id'],{'state':'generating'})
    assert ask(client,project['id']).status_code==409
    db.patch(a['id'],{'state':'complete'})
    db.patch(project['id'],{'settings':{'provider_id':''}})
    assert ask(client,project['id']).status_code==422
    assert ask(client,project['id'],question='  ').status_code==422


def test_question_idempotency_and_one_active_turn(client,project):
    ready_project(project);pid=project['id']
    first=ask(client,pid);assert first.status_code==200
    assert ask(client,pid).json()['id']==first.json()['id']
    assert len(db.records('chat',pid))==1
    assert ask(client,pid,'不同的问题').status_code==409
    assert ask(client,pid,request_id='question-two').status_code==409
    snapshot=client.get(f'/api/projects/{pid}').json()
    assert len(snapshot['chats'])==1 and 'chat_contexts' not in snapshot


def test_chat_stream_snapshot_citations_and_private_notes(client,project,monkeypatch):
    block,extract=ready_project(project);pid=project['id']
    turn=ask(client,pid).json();job=db.get(turn['data']['job_id'])
    db.patch(block['id'],{'text':'后来改成小李负责。'})
    db.patch(extract['id'],{'text':'新的提取结果。'})
    captured=[]
    def stream(provider,messages,*args,**kwargs):
        captured.extend(messages)
        yield '小王负责整理材料 [00:00:30]。'
        yield '\n[00:00:31] 这是未经验证的时间。'
    monkeypatch.setattr(ai,'stream_completion',stream)
    ai.chat(job)
    result=db.get(turn['id'])['data']
    assert result['state']=='complete' and '[时间码待核对]' in result['text']
    assert result['citations']==[{'label':'00:00:30','time':30}]
    text=json.dumps(captured,ensure_ascii=False)
    assert '小王负责整理材料，周五完成。' in text and '后来改成小李' not in text
    assert all(s not in text for s in ('PRIVATE_NOTE_NEVER_SEND','PRIVATE_ATTACHMENT','/private/not-sent.wav'))
    assert db.get(block['id'])['data']['text']=='后来改成小李负责。'


def test_failed_answer_is_saved_and_retry_does_not_duplicate(client,project,monkeypatch):
    ready_project(project);pid=project['id']
    turn=ask(client,pid).json();job=db.get(turn['data']['job_id'])
    def interrupted(*args,**kwargs):
        yield '已经生成的部分'
        raise RuntimeError('AI timeout')
    monkeypatch.setattr(ai,'stream_completion',interrupted)
    with pytest.raises(RuntimeError):ai.chat(job)
    assert db.get(turn['id'])['data']['text']=='已经生成的部分'
    assert db.get(turn['id'])['data']['state']=='failed'
    db.patch(job['id'],{'state':'failed'})
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code==200
    monkeypatch.setattr(ai,'stream_completion',lambda *args,**kw: iter(['小王负责。[00:00:30]']))
    ai.chat(db.get(job['id']))
    assert len(db.records('chat',pid))==1 and db.get(turn['id'])['data']['state']=='complete'


def test_followup_uses_completed_history_only_for_current_source(client,project,monkeypatch):
    ready_project(project);pid=project['id']
    first=ask(client,pid).json()
    db.patch(first['id'],{'text':'小王负责。','state':'complete'})
    db.patch(first['data']['job_id'],{'state':'complete'})
    second=ask(client,pid,'他什么时候完成？','question-two').json()
    context=db.get(second['id']+'-context')['data']
    assert context['history']==[{'问题':'谁负责整理材料？','回答':'小王负责。'}]
    db.patch(second['data']['job_id'],{'state':'complete'})
    db.patch(second['id'],{'state':'complete','text':'周五完成。'})
    db.patch(pid,{'transcript_revision':1})
    third=ask(client,pid,'现在由谁负责？','question-three').json()
    assert db.get(third['id']+'-context')['data']['history']==[]
    assert db.get(third['id']+'-context')['data']['extract_stale']


def test_long_recording_uses_only_exact_original_quotes(project,monkeypatch):
    with db.transaction() as c:job=db.new_job(c,project['id'],'chat',{})
    rows=['[00:00:30] 说话人1\n'+'背景材料。'*3000+'小王负责整理材料。']
    monkeypatch.setattr(ai,'completion',lambda *args,**kw: json.dumps({'quotes':[
        {'time':'00:00:30','text':'小王负责整理材料。'},
        {'time':'00:00:30','text':'虚构的预算是五十万。'},
        {'time':'09:00:00','text':'背景材料。'}]},ensure_ascii=False))
    result=ai.question_evidence('unused',rows,'谁负责材料？',[],job['id'])
    assert result==['[00:00:30] 小王负责整理材料。']


def test_project_cannot_read_another_project_conversation(client,project):
    ready_project(project);ask(client,project['id'])
    other=client.post('/api/projects').json()
    assert client.get(f"/api/projects/{other['id']}").json()['chats']==[]


def test_empty_answer_remains_retryable(client,project,monkeypatch):
    ready_project(project)
    turn=ask(client,project['id']).json()
    monkeypatch.setattr(ai,'stream_completion',lambda *args,**kw: iter([]))
    with pytest.raises(RuntimeError,match='未返回回答'):ai.chat(db.get(turn['data']['job_id']))
    assert db.get(turn['id'])['data']['state']=='failed'
