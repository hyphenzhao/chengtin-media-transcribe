import json
import httpx
import pytest
from backend import ai,db
from backend.extraction import groups


def job_for(project,texts):
    pid=project['id']
    with db.transaction() as c:
        for i,text in enumerate(texts):
            db.insert(c,'block',{'text':text,'raw':text,'start':i*60,'end':(i+1)*60,'state':'final'},pid)
        return db.new_job(c,pid,'extract',{'provider_id':'unused','template':'会议纪要','source_revision':0})


def test_deepseek_thinking_and_output_are_independent_from_context():
    p={'base_url':'https://api.deepseek.com/v1','model':'deepseek-flash'}
    assert ai.completion_options(p,10000)=={'thinking':{'type':'disabled'},'max_tokens':10000}
    assert ai.completion_options({**p,'thinking_mode':'enabled'},5000)['max_tokens']==32768
    assert ai.completion_options({**p,'max_output_tokens':64000},5000)['max_tokens']==64000
    assert ai.completion_options({**p,'base_url':'https://other.example/v1'},5000)=={'max_tokens':5000}
    assert 'thinking' not in ai.completion_options({**p,'thinking_mode':'provider'},5000)


@pytest.mark.parametrize('streaming',[True,False])
def test_length_keeps_last_content_and_usage(project,monkeypatch,streaming):
    requests=[]
    usage={'prompt_tokens':500,'completion_tokens':10000,'completion_tokens_details':{'reasoning_tokens':9000}}
    def handler(request):
        requests.append(json.loads(request.content))
        if not streaming:
            return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'保留最后一句。','reasoning_content':'hidden reasoning'}}],'usage':usage})
        payload={'choices':[{'delta':{'content':'保留最后一句。','reasoning_content':'hidden reasoning'},'finish_reason':'length'}],'usage':usage}
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text='data: '+json.dumps(payload)+'\n\ndata: [DONE]\n\n')
    original=ai.httpx.Client
    monkeypatch.setattr(ai.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with db.transaction() as c:
        provider=db.insert(c,'provider',{'base_url':'https://api.deepseek.com/v1','model':'deepseek-flash','secret':ai.encrypt('private-key')})
        job=db.new_job(c,project['id'],'extract',{})
    output=[]
    with pytest.raises(ai.OutputLimit):
        for part in ai.stream_completion(provider['id'],[{'role':'user','content':'素材'}],job['id'],10000):output.append(part)
    assert ''.join(output)=='保留最后一句。'
    record=db.get(job['id'])['data']['api_calls'][-1]
    assert record['usage']==usage and record['finish_reason']=='length'
    assert 'hidden reasoning' not in json.dumps(record) and 'private-key' not in json.dumps(record)
    assert requests[0]['thinking']=={'type':'disabled'}


def test_generation_options_preserved_when_edit_omits_them(client):
    payload={'name':'DeepSeek','base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'private-key','max_output_tokens':32000,'thinking_mode':'enabled'}
    pid=client.post('/api/providers',json=payload).json()['id']
    original_secret=db.get(pid)['data']['secret']
    result=client.put('/api/providers/'+pid,json={'name':'修改名称','base_url':payload['base_url'],'model':payload['model'],'version':1})
    assert result.status_code==200
    p=db.get(pid)['data']
    assert p['max_output_tokens']==32000 and p['thinking_mode']=='enabled' and p['secret']==original_secret
    assert client.post('/api/providers',json={**payload,'max_output_tokens':-1}).status_code==422


def test_split_preserves_single_long_paragraph_and_anchors():
    body=''.join(f'第{i}条完整原文。' for i in range(2000))
    pieces=groups(['[00:00:30] 说话人\n'+body],1000)
    assert all(len(piece)<=1000 and piece.startswith('[00:00:30]') for piece in pieces)
    assert ''.join(piece.partition('\n')[2] for piece in pieces)==body


def test_completed_parts_reused_after_failure_and_partial_result_preserved(project,monkeypatch):
    job=job_for(project,['第一段。'*500,'第二段。'*500,'第三段。'*500])
    seen=[];failed=False
    def stream(provider,messages,*args,**kw):
        nonlocal failed
        prompt=messages[-1]['content'];seen.append(prompt)
        if '请仅整理本段原文' in prompt and '第二段。' in prompt and not failed:
            failed=True;yield '第二段的部分结果';raise RuntimeError('connection interrupted')
        yield '[00:00:00] 已整理关键要点。'
    monkeypatch.setattr(ai,'stream_completion',stream)
    with pytest.raises(RuntimeError):ai.extract(job)
    first=db.get(job['id']+'-extract');assert first['data']['state']=='failed'
    first_text=first['data']['text']
    calls_first=sum('第一段。' in s for s in seen)
    db.patch(job['id'],{'attempt':2})
    ai.extract(db.get(job['id']))
    assert sum('第一段。' in s for s in seen)==calls_first
    assert db.get(first['id'])['data']['text']==first_text
    latest=db.get(job['id']+'-extract-attempt-2')
    assert latest['data']['state']=='complete'
    assert db.get(job['id'])['data']['checkpoint']['extraction']['done']==3


def test_length_automatically_splits_large_part(project,monkeypatch):
    job=job_for(project,['甲。'*1600,'乙。'*1600]);splits=[]
    def stream(provider,messages,*args,**kw):
        prompt=messages[-1]['content']
        if '请仅整理本段原文' in prompt and len(prompt.partition('\n材料：\n')[2])>2000:
            splits.append(True);yield '被截断的草稿';raise ai.OutputLimit(5000)
        yield '[00:00:00] 完整要点。'
    monkeypatch.setattr(ai,'stream_completion',stream)
    ai.extract(job)
    assert splits and db.get(job['id']+'-extract')['data']['state']=='complete'
    assert any(p['data'].get('partial_text')=='被截断的草稿' for p in db.records('extraction_part',project['id']))


def test_final_limit_keeps_all_completed_sections_without_false_partial_success(project,monkeypatch):
    job=job_for(project,['甲。'*1600,'乙。'*1600])
    def stream(provider,messages,*args,**kw):
        if '请仅整理本段原文' in messages[-1]['content']:
            yield '[00:00:00] 已完成段落要点。'
        else:
            yield '未完成的总体汇总';raise ai.OutputLimit(12000)
    monkeypatch.setattr(ai,'stream_completion',stream)
    ai.extract(job)
    result=db.get(job['id']+'-extract')['data']
    assert result['state']=='complete' and result['text'].startswith('# 内容提取（分段版）')
    assert '第 1 部分' in result['text'] and '第 2 部分' in result['text']
    assert result['notice'] and result['merge_draft']=='未完成的总体汇总'
    assert len([j for j in db.records('job',project['id']) if j['data']['kind']=='diagram'])==1


def test_changed_transcript_invalidates_cached_parts(project,monkeypatch):
    job=job_for(project,['甲。'*1600,'乙。'*1600]);calls=[]
    def stream(provider,messages,*args,**kw):calls.append(messages[-1]['content']);yield '[00:00:00] 要点。'
    monkeypatch.setattr(ai,'stream_completion',stream);ai.extract(job)
    before=len(calls)
    block=db.records('block',project['id'])[0]
    db.patch(block['id'],{'text':'修改。'*1100})
    with db.transaction() as c:db.bump_transcript(c,project['id'])
    db.patch(job['id'],{'attempt':2});ai.extract(db.get(job['id']))
    assert len(calls)>before+1
    assert db.get(job['id']+'-extract-attempt-2')['data']['source_revision']==1


def test_missing_citations_keep_explicit_source_index(project,monkeypatch):
    job=job_for(project,['甲。'*1600,'乙。'*1600]);prompts=[]
    def stream(provider,messages,*args,**kw):
        prompts.append(messages[-1]['content']);yield '完整要点但没有逐项时间码。'
    monkeypatch.setattr(ai,'stream_completion',stream);ai.extract(job)
    assert '本部分原文段落起点：[00:00:00]' in prompts[-1]
    assert '本部分原文段落起点：[00:01:00]' in prompts[-1]
    result=db.get(job['id']+'-extract')['data']['text']
    assert '模型未标明逐项引用' in result and '[00:00:00]' in result and '[00:01:00]' in result


def test_graph_retries_with_fewer_nodes_after_length(project,monkeypatch):
    with db.transaction() as c:
        a=db.insert(c,'artifact',{'kind':'extract','state':'complete','text':'完整纪要','source_revision':0},project['id'])
        job=db.new_job(c,project['id'],'diagram',{'provider_id':'unused','extract_id':a['id']})
    calls=[]
    def complete(provider,messages,*args,**kw):
        calls.append(messages)
        if len(calls)==1:
            exc=ai.OutputLimit(10000);exc.partial='{"type":';raise exc
        return '{"type":"mindmap","nodes":[{"id":"a","label":"议题"}],"edges":[]}'
    monkeypatch.setattr(ai,'completion',complete);ai.diagram(job)
    assert len(calls)==2 and '最多12个节点' in calls[-1][-1]['content']
    assert db.get(job['id']+'-diagram')['data']['state']=='complete'
