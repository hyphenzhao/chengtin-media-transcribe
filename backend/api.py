import asyncio
import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, quote
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from . import db

app = FastAPI(title="澄听 · 本地多媒体工作台", version="1.0.0")
MAX_FILE = 20 * 1024**3
CHUNK = 8 * 1024**2
MEDIA = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@app.middleware("http")
async def same_origin(request, call_next):
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@app.exception_handler(db.Conflict)
async def conflict(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


def required(rid, kind=None):
    r = db.get(rid)
    if not r or (kind and r["kind"] != kind):
        raise HTTPException(404, "内容不存在")
    return r


def active_project(pid):
    p = required(pid, "project")
    if p["data"].get("deleted"):
        raise HTTPException(409, "项目在回收站，请先恢复")
    return p


class Revision(BaseModel):
    version: int
    data: dict


@app.get("/api/health")
def health():
    heartbeat = db.get("worker-heartbeat")
    return {"ok": True, "worker_alive": bool(heartbeat and time.time()-heartbeat["updated_at"] < 30),
            "disk_free": shutil.disk_usage(db.DATA).free, "max_file_size": MAX_FILE}


@app.get("/api/projects")
def projects(trash: bool = False):
    rows=[r for r in db.records("project") if bool(r["data"].get("deleted")) == trash]
    jobs=db.records("job")
    for r in rows:
        own=[j for j in jobs if j['project_id']==r['id']]
        current=next((j for j in own if j['data']['state']=='running'),None) or next((j for j in own if j['data']['state']=='queued'),None)
        r['data']['status']=current['data']['stage'] if current else ('需要处理失败任务' if any(j['data']['state']=='failed' for j in own) else '转录已完成' if any(j['data']['kind']=='transcribe' and j['data']['state']=='complete' for j in own) else '音视频项目' if r['data'].get('primary_id') else '待添加素材')
    return sorted(rows,key=lambda r:r['updated_at'],reverse=True)


@app.post("/api/projects")
def create_project():
    with db.transaction() as c:
        counter = db.get("project-counter", c)
        n = counter["data"]["n"]+1 if counter else 1
        if counter:
            db.update(c, counter["id"], {"n": n})
        else:
            db.insert(c, "counter", {"n": n}, rid="project-counter")
        pid = uuid.uuid4().hex
        return db.insert(c, "project", {"title": f"新建{n}", "synopsis": "", "notes": None, "notes_version": 0,
            "deleted": False, "primary_id": None, "transcript_revision": 0,
            "settings": {"provider_id": "", "ai_enabled": False, "threshold": 300, "language": "auto",
                "ocr": True, "diarization": True, "speaker_threshold": .8, "system_prompt": "忠实记录原话，纠正识别错误和断句。根据画面文字补充时注明来源和时间；不编造、不擅自纠正原话中的事实。"}}, pid, pid)


@app.get("/api/projects/{pid}")
def snapshot(pid: str):
    project = required(pid, "project")
    with db.engine.connect() as c:
        c.exec_driver_sql("BEGIN")
        result = {"project": db.get(pid, c)}
        for kind in ("asset", "block", "segment", "speaker", "evidence", "job", "artifact", "chat"):
            rows = db.records(kind, pid, c)
            if kind == "segment":
                # Keep both model outputs persisted; load on demand for the audit drawer.
                rows = [{**r, "data": {k:v for k,v in r["data"].items() if k not in ("whisper", "sensevoice")}} for r in rows]
            if kind == "asset":
                rows = [public_asset(r) for r in rows]
            result[kind+"s"] = rows
        result["event_cursor"] = c.execute(select(func.max(db.Event.id))).scalar() or 0
    return result


@app.patch("/api/projects/{pid}/notes")
def save_notes(pid: str, body: Revision):
    active_project(pid)
    value=body.data.get("notes")
    if len(json.dumps(value,ensure_ascii=False))>2_000_000:
        raise HTTPException(413,"笔记内容过大")
    def validate(node):
        if not isinstance(node,dict): return
        if node.get("type") in ("image","media"):
            src=node.get("attrs",{}).get("src","")
            match=re.fullmatch(r"/api/assets/([a-f0-9]{32})/media",src)
            asset=db.get(match.group(1)) if match else None
            if not asset or asset["kind"]!="asset" or asset["project_id"]!=pid:
                raise HTTPException(422,"笔记媒体必须是本项目上传的本地附件")
        for child in node.get("content",[]): validate(child)
    validate(value)
    with db.transaction() as c:
        p=db.get(pid,c)
        version=p["data"].get("notes_version",0)
        if version!=body.version:
            raise db.Conflict("笔记已在另一页面修改，你的草稿仍保留，请核对后再保存。")
        db.update(c,pid,{"notes":value,"notes_version":version+1})
    return {"version":version+1}


@app.patch("/api/projects/{pid}")
def edit_project(pid: str, body: Revision):
    active_project(pid)
    allowed = {k:v for k,v in body.data.items() if k in ("title", "synopsis", "settings")}
    for field in ('title','synopsis'):
        if field in allowed: allowed[field+'_suggestion']=''
    if "title" in allowed:
        allowed["title"] = str(allowed["title"]).strip()[:160]
        if not allowed["title"]:
            raise HTTPException(422, "标题不能为空")
    if len(json.dumps(allowed, ensure_ascii=False)) > 2_000_000:
        raise HTTPException(413, "内容过大")
    if "settings" in allowed:
        old = active_project(pid)["data"]["settings"].copy()
        old.setdefault('speaker_threshold',.8)
        old.update({k:v for k,v in allowed["settings"].items() if k in old})
        if not isinstance(old["threshold"], int) or not 100 <= old["threshold"] <= 1500:
            raise HTTPException(422, "分段阈值应为 100–1500 字")
        if old["language"] not in ("auto", "zh", "en"):
            raise HTTPException(422, "不支持的语言")
        if old['speaker_threshold'] not in (.5,.8,1.0):raise HTTPException(422,'说话人分组设置无效')
        allowed["settings"] = old
    return db.patch(pid, allowed, body.version)


@app.delete("/api/projects/{pid}")
def trash_project(pid: str):
    required(pid, "project")
    with db.transaction() as c:
        for j in db.records("job", pid, c):
            if j["data"]["state"] in ("queued", "running"):
                db.update(c, j["id"], {"cancel": True})
        return db.update(c, pid, {"deleted": True})


@app.post("/api/projects/{pid}/restore")
def restore_project(pid: str):
    required(pid, "project")
    return db.patch(pid, {"deleted": False})


@app.delete("/api/projects/{pid}/permanent")
def permanent_delete(pid: str):
    p = required(pid, "project")
    if not p["data"].get("deleted"):
        raise HTTPException(409, "请先将项目移入回收站")
    if any(j["data"]["state"] in ("queued", "running") for j in db.records("job", pid)):
        raise HTTPException(409, "任务正在停止，请稍后再清理")
    folder = (db.DATA / "projects" / pid).resolve()
    if folder.parent != (db.DATA / "projects").resolve():
        raise HTTPException(400, "路径无效")
    shutil.rmtree(folder, ignore_errors=True)
    with db.transaction() as c:
        c.execute(delete(db.Record).where(db.Record.project_id == pid))
        c.execute(delete(db.Event).where(db.Event.project_id == pid))
        db.emit(c, None, "deleted", {"id": pid})
    return {"ok": True}


class UploadStart(BaseModel):
    name: str = Field(max_length=240)
    size: int = Field(gt=0, le=MAX_FILE)
    role: str = "primary"
    fingerprint: str = Field(default="", max_length=200)


def public_asset(rec):
    return {**rec, "data": {k:v for k,v in rec["data"].items() if k not in ("path", "proxy_path")}}


@app.post("/api/projects/{pid}/uploads")
def start_upload(pid: str, body: UploadStart):
    p = active_project(pid)
    if body.role not in ("primary", "supplement"):
        raise HTTPException(422, "附件类型无效")
    suffix = Path(body.name).suffix.lower()
    if suffix not in (MEDIA if body.role == "primary" else MEDIA | IMAGES):
        raise HTTPException(422, "暂不支持此文件格式")
    if body.role == "primary" and p["data"].get("primary_id"):
        raise HTTPException(409, "项目已有主素材，请新建项目上传另一份")
    if shutil.disk_usage(db.DATA).free < body.size*2 + 1024**3:
        raise HTTPException(507, "磁盘空间不足")
    with db.transaction() as c:
        for u in db.records("upload", pid, c):
            d=u["data"]
            if d["state"] == "uploading" and body.fingerprint and d.get("fingerprint") == body.fingerprint and d["size"] == body.size and d["role"] == body.role and d["name"] == body.name:
                return u
        u = db.insert(c, "upload", {**body.model_dump(), "suffix": suffix, "chunk_size": CHUNK,
            "count": (body.size+CHUNK-1)//CHUNK, "received": {}, "state": "uploading"}, pid)
    (db.DATA/"projects"/pid/"uploads"/u["id"]).mkdir(parents=True, exist_ok=True)
    return u


@app.get("/api/uploads/{uid}")
def upload_status(uid: str):
    return required(uid, "upload")


@app.put("/api/uploads/{uid}/chunks/{index}")
async def upload_chunk(uid: str, index: int, request: Request):
    u = required(uid, "upload")
    active_project(u["project_id"])
    d = u["data"]
    if d["state"] != "uploading" or index < 0 or index >= d["count"]:
        raise HTTPException(409, "上传状态或分块编号无效")
    expected = min(CHUNK, d["size"] - index*CHUNK)
    folder = db.DATA/"projects"/u["project_id"]/"uploads"/uid
    temp = folder / f"{index}.{uuid.uuid4().hex}.tmp"
    count=0
    digest=hashlib.sha256()
    try:
        with open(temp, "wb") as f:
            async for chunk in request.stream():
                count += len(chunk)
                if count > expected:
                    raise HTTPException(413, "分块过大")
                f.write(chunk)
                digest.update(chunk)
            f.flush()
            os.fsync(f.fileno())
        if count != expected:
            raise HTTPException(422, "分块长度不完整，请重试")
        checksum = digest.hexdigest()
        with db.transaction() as c:
            latest = db.get(uid, c)["data"]
            if latest["state"] != "uploading":
                raise HTTPException(409, "上传已结束")
            received = latest["received"]
            if str(index) in received and received[str(index)] != checksum:
                raise HTTPException(409, "相同编号的分块内容不一致，请重新发起上传")
            os.replace(temp, folder/f"{index}.part")
            received[str(index)]=checksum
            db.update(c, uid, {"received": received})
        return {"index": index, "sha256": checksum}
    finally:
        temp.unlink(missing_ok=True)


@app.post("/api/uploads/{uid}/complete")
def complete_upload(uid: str):
    u=required(uid, "upload")
    active_project(u["project_id"])
    d=u["data"]
    if d["state"] == "complete":
        return public_asset(required(d["asset_id"], "asset"))
    if len(d["received"]) != d["count"]:
        raise HTTPException(409, "还有分块未上传")
    folder=db.DATA/"projects"/u["project_id"]/"uploads"/uid
    dest=folder/("original"+d["suffix"])
    # A single file lock also protects two tabs completing the same upload.
    import fcntl
    with open(folder/"assemble.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        d=required(uid, "upload")["data"]
        if d["state"] == "complete":
            return public_asset(required(d["asset_id"], "asset"))
        with open(str(dest)+".tmp", "wb") as out:
            for i in range(d["count"]):
                part=folder/f"{i}.part"
                if not part.exists():
                    raise HTTPException(409, f"分块 {i} 缺失，请重新上传")
                data=part.read_bytes()
                if hashlib.sha256(data).hexdigest() != d["received"][str(i)]:
                    raise HTTPException(409, "分块校验失败")
                out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(str(dest)+".tmp", dest)
        from .media import probe
        try:
            meta=probe(dest)
        except Exception:
            raise HTTPException(422, "文件不是可读取的音视频或图片")
        with db.transaction() as c:
            p=db.get(u["project_id"],c)
            if d["role"] == "primary" and p["data"].get("primary_id"):
                raise HTTPException(409, "项目已经上传主素材")
            a=db.insert(c,"asset", {"name":d["name"],"size":d["size"],"path":str(dest),
                "role":d["role"],"state":"ready" if meta["type"]=="image" else "preparing", **meta},u["project_id"])
            db.update(c,uid,{"state":"complete","asset_id":a["id"]})
            if d["role"] == "primary":
                db.update(c,u["project_id"],{"primary_id":a["id"]})
            if meta["type"] != "image":
                db.new_job(c,u["project_id"],"prepare",{"asset_id":a["id"]},a["id"])
        for part in folder.glob("*.part"):
            part.unlink()
        return public_asset(a)


@app.get("/api/assets/{aid}/media")
def media_file(aid: str, original: bool=False):
    a=required(aid,"asset")["data"]
    path=Path(a.get("proxy_path") or a["path"]) if not original else Path(a["path"])
    if not path.exists():
        raise HTTPException(404,"文件不存在")
    return FileResponse(path,media_type=mimetypes.guess_type(str(path))[0] or "application/octet-stream")


@app.get("/api/evidence/{eid}/image")
def evidence_image(eid: str):
    e=required(eid,"evidence")
    return FileResponse(e["data"]["path"],media_type="image/jpeg")


@app.get("/api/segments/{sid}")
def segment_detail(sid: str):
    return required(sid,"segment")


@app.get("/api/events")
async def events(request: Request, project_id: str="", after: int=0):
    try:
        cursor=max(after,int(request.headers.get("last-event-id", "0")))
    except ValueError:
        cursor=after
    async def stream():
        nonlocal cursor
        heartbeat=0
        while not await request.is_disconnected():
            rows=await asyncio.to_thread(db.events_after,cursor,project_id or None)
            for r in rows:
                cursor=r["id"]
                yield f"id: {cursor}\nevent: {r['name']}\ndata: {json.dumps(r['data'],ensure_ascii=False)}\n\n"
            heartbeat+=1
            if heartbeat%15==0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(stream(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


@app.post("/api/projects/{pid}/transcribe")
def transcribe(pid: str):
    p=active_project(pid)
    if not p["data"].get("primary_id"):
        raise HTTPException(422,"请先上传主素材")
    settings=p['data']['settings']
    if settings.get('ai_enabled') and not db.get(settings.get('provider_id') or ''):
        raise HTTPException(422,"请先选择 AI 接口，或关闭 AI 修订后使用本地转写")
    with db.transaction() as c:
        prior=[j for j in db.records("job",pid,c) if j["data"]["kind"]=="transcribe"]
        if prior:
            return prior[-1]
        return db.new_job(c,pid,"transcribe",{"asset_id":p["data"]["primary_id"],"settings":p["data"]["settings"]})


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    required(jid,"job")
    return db.patch(jid,{"cancel":True})


@app.post("/api/jobs/{jid}/retry")
def retry_job(jid: str):
    j=required(jid,"job")
    active_project(j["project_id"])
    if j["data"]["state"] not in ("failed","cancelled"):
        raise HTTPException(409,"当前任务不能重试")
    return db.patch(jid,{"state":"queued","cancel":False,"error":"","stage":"等待继续"})


@app.patch("/api/blocks/{bid}")
def edit_block(bid: str, body: Revision):
    b=required(bid,"block")
    active_project(b["project_id"])
    changes={k:v for k,v in body.data.items() if k in ("text","speaker_id")}
    if "text" in changes:
        changes.update({"manual":True,"state":"final"})
    if "speaker_id" in changes:
        s=required(changes["speaker_id"],"speaker") if changes["speaker_id"] else None
        if s and s["project_id"] != b["project_id"]:
            raise HTTPException(422,"说话人不属于此项目")
        changes["speaker_manual"]=True
    with db.transaction() as c:
        current=db.get(bid,c)
        if current["version"] != body.version:
            raise db.Conflict("正文已更新，你的编辑已保留，请重新核对后保存。")
        db.insert(c,"history",{"block_id":bid,"text":current["data"].get("text",current["data"]["raw"]),"reason":"manual"},b["project_id"])
        if "text" in changes:
            changes["edit_revision"]=current["data"].get("edit_revision",0)+1
        result=db.update(c,bid,changes,body.version)
        db.bump_transcript(c,b["project_id"])
        return result


@app.get("/api/blocks/{bid}/history")
def block_history(bid: str):
    b=required(bid,"block")
    return [h for h in db.records("history",b["project_id"]) if h["data"].get("block_id")==bid]


@app.post("/api/blocks/{bid}/adopt-raw")
def adopt_raw(bid: str, body: Revision):
    b=required(bid,"block")
    return edit_block(bid,Revision(version=body.version,data={"text":b["data"]["raw"]}))


@app.post("/api/blocks/{bid}/revise")
def revise_block(bid: str):
    b=required(bid,"block")
    p=active_project(b["project_id"])
    provider=p["data"]["settings"].get("provider_id")
    if not provider or not db.get(provider):raise HTTPException(422,'请先在项目中选择 AI 接口')
    with db.transaction() as c:
        return db.new_job(c,b["project_id"],"revise",{"block_id":bid,"provider_id":provider,"input_version":b["version"],"suggestion":bool(b["data"].get("manual"))},bid)


@app.post("/api/blocks/{bid}/adopt-suggestion")
def adopt_suggestion(bid: str, body: Revision):
    b=required(bid,"block")
    if not b["data"].get("suggestion"):
        raise HTTPException(409,"暂无修订建议")
    return edit_block(bid,Revision(version=body.version,data={"text":b["data"]["suggestion"]}))


@app.patch("/api/speakers/{sid}")
def rename_speaker(sid: str, body: Revision):
    speaker=required(sid,"speaker")
    active_project(speaker['project_id'])
    name=str(body.data.get("name","")).strip()[:80]
    if not name:
        raise HTTPException(422,"名字不能为空")
    with db.transaction() as c:
        result=db.update(c,sid,{"name":name,"manual":True},body.version)
        db.bump_transcript(c,speaker['project_id'])
        return result


class Generate(BaseModel):
    kind: str = "extract"
    template: str = "会议纪要"
    instruction: str = Field(default="",max_length=8000)
    diagram_type: str = "auto"


@app.post("/api/projects/{pid}/generate")
def generate(pid: str, body: Generate):
    p=active_project(pid)
    if body.kind not in ("extract","diagram","title","synopsis"):
        raise HTTPException(422,"生成类型无效")
    provider=p["data"]["settings"].get("provider_id")
    if not provider or not db.get(provider):
        raise HTTPException(422,"请先配置并选择 AI 接口")
    if not db.records("block",pid):
        raise HTTPException(422,"请先完成转录")
    transcription=[j for j in db.records('job',pid) if j['data']['kind']=='transcribe']
    if transcription and transcription[-1]['data']['state']!='complete':
        raise HTTPException(409,"转录尚未完整完成，请先继续或重试转录任务")
    if any(j["data"]["state"] in ("queued","running") and j["data"]["kind"] in ("transcribe","revise","diarize") for j in db.records("job",pid)):
        raise HTTPException(409,"请等待转录、说话人分析和修订任务完成")
    with db.transaction() as c:
        return db.new_job(c,pid,body.kind,{**body.model_dump(),"provider_id":provider,
            "source_revision":p["data"]["transcript_revision"],"project_version":p["version"]})


@app.patch("/api/artifacts/{aid}")
def edit_artifact(aid: str, body: Revision):
    a=required(aid,"artifact")
    if a["data"]["kind"] != "extract":
        raise HTTPException(422,"此结果不能直接编辑")
    active_project(a['project_id'])
    if a['data']['state']=='generating':
        raise HTTPException(409,"请等待生成完成后编辑")
    return db.patch(aid,{"text":str(body.data.get("text","")),"manual":True},body.version)


class ChatQuestion(BaseModel):
    question: str = Field(min_length=1,max_length=2000)
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{8,80}$')


@app.post('/api/projects/{pid}/chat')
def ask_recording(pid: str,body: ChatQuestion):
    from .ai import transcript
    active_project(pid)
    question=body.question.strip()
    if not question:raise HTTPException(422,'请输入问题')
    cid=hashlib.sha256(f'{pid}:chat:{body.request_id}'.encode()).hexdigest()
    with db.transaction() as c:
        # A repeated network request must not add a second question or charge twice.
        existing=db.get(cid,c)
        if existing:
            if existing['data']['question']!=question:raise HTTPException(409,'请求编号已使用，请重新发送问题')
            return existing
        p=db.get(pid,c)
        extracts=[a for a in db.records('artifact',pid,c) if a['data']['kind']=='extract' and a['data']['state']=='complete']
        if not extracts:raise HTTPException(409,'请先在正文区域完成一次内容提取，再使用内容问答')
        provider=p['data']['settings'].get('provider_id')
        config=db.get(provider or '',c)
        if not config or config['kind']!='provider':raise HTTPException(422,'请先配置并在项目中选择 AI 接口')
        jobs=db.records('job',pid,c)
        if any(j['data']['state'] in ('queued','running') and j['data']['kind'] in ('chat','transcribe','revise','diarize') for j in jobs):
            raise HTTPException(409,'请等待当前回答或正文处理完成后再提问')
        rows=transcript(pid,conn=c)
        if not rows:raise HTTPException(409,'当前项目没有可供问答的正文')
        source=extracts[-1]
        revision=p['data']['transcript_revision']
        history=[];length=0
        for turn in reversed(db.records('chat',pid,c)):
            d=turn['data']
            if d['state']!='complete' or d['source_revision']!=revision:continue
            item={'问题':d['question'],'回答':d['text']}
            length+=len(d['question'])+len(d['text'])
            if length>12000 or len(history)>=6:break
            history.insert(0,item)
        # Store an immutable text-only snapshot; never include project notes or attachments.
        db.insert(c,'chat_context',{'rows':rows,'extract':source['data']['text'],
            'extract_stale':source['data']['source_revision']!=revision,'history':history},pid,cid+'-context')
        job=db.new_job(c,pid,'chat',{'chat_id':cid,'provider_id':provider},cid)
        return db.insert(c,'chat',{'question':question,'text':'','state':'queued','job_id':job['id'],
            'source_revision':revision,'extract_id':source['id'],'extract_version':source['version'],
            'citations':[]},pid,cid)


@app.get("/api/providers")
def providers():
    return [{**p,"data":{k:v for k,v in p["data"].items() if k!="secret"}} for p in db.records("provider")]


def graph_export_path(a, version, format):
    if a['data']['kind']!='diagram' or format not in ('svg','png') or not 1<=version<=a['version']:
        raise HTTPException(422,'图表版本或格式无效')
    name=hashlib.sha256(a['id'].encode()).hexdigest()+f'-v{version}.{format}'
    return db.DATA/'projects'/a['project_id']/'exports'/name


@app.post('/api/artifacts/{aid}/rendered')
async def save_graph_render(aid: str,request: Request,version: int,format: str):
    a=required(aid,'artifact');active_project(a['project_id'])
    if a['version']!=version or a['data']['state']!='complete':
        raise HTTPException(409,'图表已更新，请等待完成后重新导出')
    path=graph_export_path(a,version,format)
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>12*1024*1024:raise HTTPException(413,'图表文件过大')
    try:
        if format=='png':
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                if im.format!='PNG' or im.width*im.height>64_000_000:raise ValueError()
                im.verify()
        else:
            from xml.etree import ElementTree as ET
            root=ET.fromstring(data)
            allowed={'svg','g','defs','style','marker','path','rect','polygon','polyline','line','circle','ellipse','text','tspan','title','desc','clipPath','use',
                'filter','feDropShadow','feGaussianBlur','feOffset','feColorMatrix','feMerge','feMergeNode','linearGradient','radialGradient','stop'}
            if root.tag.split('}')[-1]!='svg':raise ValueError()
            for el in root.iter():
                if el.tag.split('}')[-1] not in allowed:raise ValueError()
                for k,v in el.attrib.items():
                    if k.lower().startswith('on') or (k.split('}')[-1]=='href' and not v.startswith('#')):raise ValueError()
            if re.search(rb'@import|javascript:|url\(\s*[\x22\x27]?(?:https?:|//|data:)',data,re.I):raise ValueError()
    except Exception:
        raise HTTPException(422,'图表文件格式校验失败')
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(data);os.replace(temp,path)
    return {'url':f'/api/artifacts/{aid}/export?version={version}&format={format}'}


@app.get('/api/artifacts/{aid}/export')
def download_graph(aid: str,version: int,format: str):
    a=required(aid,'artifact');path=graph_export_path(a,version,format)
    if not path.exists():raise HTTPException(404,'请先在图谱页面生成导出文件')
    return FileResponse(path,filename=f'内容图谱.{format}',media_type='image/png' if format=='png' else 'image/svg+xml',
        headers={'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'"})


class ProviderIn(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1,max_length=200)
    api_key: str = Field(default="",max_length=4000)
    version: int | None = None
    max_output_tokens: int | None = Field(default=None,ge=128,le=393216)
    thinking_mode: Literal['auto','provider','enabled','disabled'] = 'auto'


def provider_data(body, existing=None):
    from .ai import encrypt
    url=body.base_url.strip().rstrip("/")
    parsed=urlparse(url)
    if parsed.scheme not in ("https","http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422,"请输入完整的 HTTP(S) 接口地址")
    secret=encrypt(body.api_key) if body.api_key else (existing or {}).get("secret",encrypt(""))
    return {"name":body.name,"base_url":url,"model":body.model,"secret":secret,"has_key":bool(body.api_key or (existing or {}).get("has_key")),
        'max_output_tokens':body.max_output_tokens if 'max_output_tokens' in body.model_fields_set else (existing or {}).get('max_output_tokens'),
        'thinking_mode':body.thinking_mode if 'thinking_mode' in body.model_fields_set else (existing or {}).get('thinking_mode','auto')}


@app.post("/api/providers")
def add_provider(body: ProviderIn):
    with db.transaction() as c:
        p=db.insert(c,"provider",provider_data(body))
    return {"id":p["id"]}


@app.put("/api/providers/{pid}")
def edit_provider(pid: str, body: ProviderIn):
    p=required(pid,"provider")
    db.patch(pid,provider_data(body,p["data"]),body.version)
    return {"id":pid}


@app.delete("/api/providers/{pid}")
def delete_provider(pid: str):
    required(pid,"provider")
    if any(j["data"]["state"] in ("queued","running") and (j["data"].get("payload",{}).get("provider_id")==pid or j['data'].get('payload',{}).get('settings',{}).get('provider_id')==pid) for j in db.records("job")):
        raise HTTPException(409,"接口正在使用，请等待任务结束")
    with db.transaction() as c:
        c.execute(delete(db.Record).where(db.Record.id==pid))
        db.emit(c,None,"changed",{"kind":"provider","id":pid})
    return {"ok":True}


@app.post("/api/providers/{pid}/test")
def test_provider(pid: str):
    required(pid,"provider")
    from .ai import completion
    try:
        result=completion(pid,[{"role":"user","content":"只回复：连接成功"}],max_tokens=32)
        return {"ok":True,"message":result[:100]}
    except Exception as e:
        raise HTTPException(502,str(e)[:300])


def timecode(sec, srt=False):
    ms=max(0,int(round(sec*1000)))
    h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02}"+(f",{ms:03}" if srt else "")


@app.get("/api/projects/{pid}/export")
def export(pid: str, format: str="md", artifact_id: str=""):
    p=required(pid,"project")
    if format not in ("md","txt","srt"):
        raise HTTPException(422,"不支持的导出格式")
    blocks=sorted(db.records("block",pid),key=lambda b:b["data"]["start"])
    speakers={s["id"]:s["data"]["name"] for s in db.records("speaker",pid)}
    if artifact_id:
        a=required(artifact_id,"artifact")
        if a["project_id"]!=pid:
            raise HTTPException(404,"结果不存在")
        text=a["data"].get("text","")
    elif format=="srt":
        text="\n\n".join(f"{i+1}\n{timecode(b['data']['start'],True)} --> {timecode(b['data']['end'],True)}\n{(b['data']['raw'] if b['data'].get('state')=='accumulating' else b['data'].get('text',b['data']['raw']))}" for i,b in enumerate(blocks))
    else:
        lines=[("# " if format=="md" else "")+p["data"]["title"],""]
        for b in blocks:
            d=b["data"]
            lines += [f"[{timecode(d['start'])}] {speakers.get(d.get('speaker_id'), '说话人待确认')}",(d["raw"] if d.get("state")=="accumulating" else d.get("text",d["raw"])),""]
        text="\n".join(lines)
    name=re.sub(r'[\\/:*?"<>|\r\n]','_',p["data"]["title"])+"."+format
    return Response(text,media_type="text/plain; charset=utf-8",headers={"Content-Disposition":f"attachment; filename*=UTF-8''{quote(name)}"})


dist=db.ROOT/"frontend"/"dist"
if (dist/"assets").exists():
    app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")


@app.get("/{path:path}")
def index(path: str):
    if path.startswith("api/"):
        raise HTTPException(404,"接口不存在")
    if not (dist/"index.html").exists():
        return Response("前端正在构建",status_code=503)
    return FileResponse(dist/"index.html",headers={"Cache-Control":"no-cache"})
