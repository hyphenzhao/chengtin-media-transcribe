"""Checkpointed map/reduce extraction with bounded output and adaptive splitting."""
import hashlib
import json
import re
import time

from . import ai, db
from .media import check_cancel

VERSION=2
CHARS=4000
INSTRUCTIONS={
    '会议纪要':'包含概览、议题与讨论、确定结论、待确认事项和行动清单；负责人、日期无依据就写未指定。',
    '内容摘要':'归纳主题、核心要点、关键数字和仍有分歧的事项。',
    '面试记录':'只整理实际出现的自我介绍、问题、回答和追问；无回答写原文未提供回答，不替受访者补答或评分。',
    '行动清单':'逐项列出明确任务、负责人、截止时间与原始时间码；未确认任务另列。',
}


def split_text(text,limit):
    """Split at nearby sentence boundaries; never omit any source characters."""
    parts=[]
    while len(text)>limit:
        candidates=list(re.finditer(r'[\n。！？.!?]',text[limit//2:limit]))
        end=limit//2+candidates[-1].end() if candidates else limit
        parts.append(text[:end]);text=text[end:]
    if text:parts.append(text)
    return parts


def groups(rows,limit=CHARS):
    parts=[];buffer=''
    for row in rows:
        heading,sep,body=row.partition('\n')
        prefix=heading+'\n' if sep and re.match(r'^\[\d{2}:\d{2}:\d{2}\]',heading) else ''
        for piece in split_text(body if prefix else row,max(100,limit-len(prefix))):
            part=prefix+piece
            if buffer and len(buffer)+len(part)+2>limit:
                parts.append(buffer);buffer=''
            buffer+=('\n\n' if buffer else '')+part
    if buffer:parts.append(buffer)
    return parts


def clean(text,rows):
    wrapped=re.fullmatch(r'\s*```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*',text)
    return ai.source_timestamps(wrapped.group(1) if wrapped else text,rows).strip()


def source_index(text):
    return list(dict.fromkeys(re.findall(r'^\[(\d{2}:\d{2}:\d{2})\]',text,re.M)))


class Extraction:
    def __init__(self,job,artifact,rows):
        self.job=job;self.jid=job['id'];self.pid=job['project_id'];self.aid=artifact['id']
        self.payload=job['data']['payload'];self.provider=self.payload['provider_id'];self.rows=rows
        config=db.get(self.provider)
        identity={k:config['data'].get(k) for k in ('base_url','model')} if config else {}
        identity.update({'version':VERSION,'rows':rows,'template':self.payload.get('template'),
                         'instruction':self.payload.get('instruction')})
        self.identity=hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        self.completed=[];self.last=0

    def preview(self,current='',heading='正在整理',force=False):
        if not force and time.monotonic()-self.last<.25 and current:return
        sections=[f'### 第 {i+1} 部分\n\n{text}' for i,text in enumerate(self.completed)]
        if current:sections.append(f'### {heading}\n\n{current}')
        db.patch(self.aid,{'text':'# 分段提取 · 生成中\n\n'+'\n\n'.join(sections)})
        self.last=time.monotonic()

    def part(self,text,mode='part',depth=0):
        check_cancel(self.jid)
        key=self.jid+'-part-'+hashlib.sha256((self.identity+mode+text).encode()).hexdigest()
        prior=db.get(key)
        if prior and prior['data'].get('state')=='complete':return prior['data']['text']
        if prior and prior['data'].get('state')=='split' and prior['data'].get('children'):
            result='\n\n'.join(self.part(piece,mode,depth+1) for piece in prior['data']['children'])
            db.patch(key,{'state':'complete','text':result})
            return result
        with db.transaction() as c:
            data={'state':'generating','text':'','input_hash':self.identity,'job_id':self.jid,'mode':mode}
            if prior:db.update(c,key,data)
            else:db.insert(c,'extraction_part',data,self.pid,key)
        template=self.payload.get('template','会议纪要')
        target=min(1100,max(180,len(text)//3))
        instruction=(f'请仅整理本段原文，为最终的{template}提取要点。' if mode=='part' else '将以下已完成的分段要点合并去重，保持各部分信息。')
        instruction+=f'尽量控制在{target}字以内；保留决定、否定、关键数字、人名、负责人、日期、分歧与待确认项。'
        instruction+='每个主要要点后必须附对应原文段首[HH:MM:SS]引用；只复制已有时间，不推算时间，不输出整个文档的代码围栏。'+INSTRUCTIONS.get(template,'按用户要求提取。')
        messages=[{'role':'system','content':ai.GROUNDING},
                  {'role':'user','content':instruction+'\n用户要求：'+self.payload.get('instruction','')+'\n材料：\n'+text}]
        output=''
        try:
            for delta in ai.stream_completion(self.provider,messages,self.jid,max_tokens=5000):
                output+=delta;self.preview(output,'当前片段')
            if not output.strip():raise RuntimeError('AI 返回空的分段结果，请重试')
            result=clean(output,text.split('\n\n') if mode=='part' else self.rows)
        except ai.OutputLimit:
            db.patch(key,{'state':'split','partial_text':output})
            if depth>=4 or len(text)<500:
                raise RuntimeError('该片段仍达到单次输出上限，已保留其他分段；请提高 AI 输出上限或关闭思考模式后重试') from None
            db.patch(self.jid,{'stage':'当前片段输出受限，正在拆成更小片段'})
            children=groups(text.split('\n\n'),max(250,len(text)//2))
            db.patch(key,{'children':children})
            result='\n\n'.join(self.part(piece,mode,depth+1) for piece in children)
        except BaseException:
            db.patch(key,{'state':'failed','text':output})
            self.preview(output,'未完成片段（可重试）',force=True)
            raise
        check_cancel(self.jid)
        db.patch(key,{'state':'complete','text':result})
        return result

    def sections(self):
        return '# 内容提取（分段版）\n\n'+'\n\n'.join(f'## 第 {i+1} 部分\n\n{text}' for i,text in enumerate(self.completed))

    def run(self):
        pieces=groups(self.rows)
        db.patch(self.jid,{'stage':f'准备分段提取，共 {len(pieces)} 段','progress':.05})
        if len(pieces)>1:
            for i,piece in enumerate(pieces):
                db.patch(self.jid,{'stage':f'分段提取 {i+1}/{len(pieces)}','progress':.05+.6*i/len(pieces)})
                result=self.part(piece)
                anchors=' '.join(f'[{t}]' for t in source_index(piece))
                self.completed.append(result+'\n\n本部分原文段落起点：'+anchors)
                self.preview()
                with db.transaction() as c:
                    job=db.get(self.jid,c)
                    db.update(c,self.jid,{'checkpoint':{**job['data'].get('checkpoint',{}),
                        'extraction':{'input_hash':self.identity,'done':i+1,'total':len(pieces)}}})
            material='\n\n'.join(self.completed)
            for level in range(4):
                if len(material)<=8000:break
                db.patch(self.jid,{'stage':f'逐级汇总 · 第 {level+1} 层','progress':.7})
                material='\n\n'.join(self.part(part,'merge') for part in groups([material],6000))
            if len(material)>8000:
                return self.sections(),'合并结果过长，已完整保留各段提取结果。'
        else:material='\n\n'.join(self.rows)
        template=self.payload.get('template','会议纪要')
        instruction=INSTRUCTIONS.get(template,'按自定义要求提取。')
        for attempt in range(2):
            db.patch(self.jid,{'stage':'合并生成完整提取' if not attempt else '输出受限，正在生成精简汇总','progress':.8})
            prompt=f'根据材料生成{template}，使用 Markdown。{instruction}合并同一议题，避免重复，保留关键数字、决定及未确定事项。'
            prompt+=f'目标不超过{2500 if not attempt else 1000}字，不要逐句改写全文。每个主要议题与行动项后必须附对应材料的[HH:MM:SS]引用；只复制已有时间，不要推算时间。不要把整个文档放入代码围栏。'
            prompt+='\n用户要求：'+self.payload.get('instruction','')+'\n材料：\n'+material
            output='';last=0
            try:
                for delta in ai.stream_completion(self.provider,[{'role':'system','content':ai.GROUNDING},{'role':'user','content':prompt}],self.jid,max_tokens=12000):
                    output+=delta
                    if time.monotonic()-last>.25:
                        db.patch(self.aid,{'text':output});last=time.monotonic()
                if not output.strip():raise RuntimeError('AI 未返回提取内容，请重试')
                result=clean(output,self.rows)
                if not re.search(r'\[\d{2}:\d{2}:\d{2}\]',result):
                    # Keep navigation honest when a provider ignores the citation instruction.
                    anchors=' '.join(f'[{t}]' for t in source_index('\n\n'.join(self.rows)))
                    result+='\n\n## 原文来源索引\n\n模型未标明逐项引用；以下仅为原文段落起点：\n\n'+anchors
                return result,''
            except ai.OutputLimit:
                db.patch(self.aid,{'text':output,'merge_draft':output})
                if not self.completed:
                    # Even a short source may request a long answer; split and checkpoint it.
                    smaller=groups(self.rows,max(250,len(material)//2))
                    self.completed=[self.part(piece) for piece in smaller]
                    material='\n\n'.join(self.completed)
            except BaseException:
                db.patch(self.aid,{'text':output or (self.sections() if self.completed else '')})
                raise
        return self.sections(),'整体汇总达到单次输出上限；所有片段均已完成，已保留完整分段版。'


def extract(job):
    jid=job['id'];pid=job['project_id'];payload=job['data']['payload']
    artifact=ai.artifact(job,'extract')
    with db.transaction() as c:
        project=db.get(pid,c);revision=project['data']['transcript_revision']
        rows=ai.transcript(pid,conn=c)
        payload={**payload,'source_revision':revision}
        db.update(c,jid,{'payload':payload})
        db.update(c,artifact['id'],{'source_revision':revision,'pipeline_version':VERSION})
    job={**job,'data':{**job['data'],'payload':payload}}
    try:
        pipeline=Extraction(job,artifact,rows)
        output,notice=pipeline.run()
        check_cancel(jid)
        db.patch(artifact['id'],{'text':output,'state':'complete','notice':notice})
        with db.transaction() as c:
            db.new_job(c,pid,'diagram',{**payload,'extract_id':artifact['id']},artifact['id'])
    except BaseException:
        db.patch(artifact['id'],{'state':'failed'})
        raise
