import concurrent.futures
import difflib
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import threading
import time
import wave
from pathlib import Path

from . import db


class Cancelled(Exception):
    pass


def check_cancel(jid):
    j=db.get(jid)
    if not j or j["data"].get("cancel"):
        raise Cancelled("任务已停止，已完成内容已保存")


def run(args,jid=None,timeout=3600,progress_file=None,duration=0):
    import tempfile
    with tempfile.TemporaryFile() as error:
        proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=error,start_new_session=True)
        started=time.monotonic()
        last_progress=0
        try:
            while True:
                try:
                    out,_=proc.communicate(timeout=.5)
                    break
                except subprocess.TimeoutExpired:
                    if jid:
                        check_cancel(jid)
                    if jid and progress_file and duration and time.monotonic()-last_progress>2:
                        last_progress=time.monotonic()
                        if Path(progress_file).exists():
                            values=re.findall(r'out_time_ms=(\d+)',Path(progress_file).read_text(errors='replace'))
                            if values: db.patch(jid,{'progress':min(.99,int(values[-1])/1e6/duration)})
                    if time.monotonic()-started>timeout:
                        raise TimeoutError("媒体处理超时")
            if proc.returncode:
                error.seek(0)
                raise RuntimeError(error.read().decode(errors="replace")[-1800:])
            return out
        except BaseException:
            if proc.poll() is None: os.killpg(proc.pid,signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL)
                proc.wait()
            raise


def probe(path):
    if Path(path).suffix.lower() in (".jpg",".jpeg",".png",".webp",".gif"):
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return {"type":"image","duration":0,"has_audio":False,"has_video":False}
    result=json.loads(run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(path)],timeout=120))
    streams=result.get("streams",[])
    audio=next((s for s in streams if s["codec_type"]=="audio"),None)
    video=next((s for s in streams if s["codec_type"]=="video" and not s.get("disposition",{}).get("attached_pic")),None)
    if not audio and not video:
        raise ValueError("没有可用的音视频轨道")
    return {"type":"video" if video else "audio","has_video":bool(video),"has_audio":bool(audio),
        "duration":float(result.get("format",{}).get("duration",0)),
        "video_codec":(video or {}).get("codec_name"),"audio_codec":(audio or {}).get("codec_name"),
        "width":(video or {}).get("width",0),"height":(video or {}).get("height",0)}


def prepare(job):
    aid=job["data"]["payload"]["asset_id"]
    a=db.get(aid)["data"]
    path=Path(a["path"])
    compatible=(a["type"]=="video" and path.suffix.lower() in (".mp4",".webm") and a.get("video_codec") in ("h264","vp8","vp9","av1") and (not a["has_audio"] or a.get("audio_codec") in ("aac","opus","vorbis"))) or (a["type"]=="audio" and path.suffix.lower() in (".mp3",".wav",".m4a",".ogg",".flac"))
    if compatible:
        db.patch(aid,{"state":"ready"})
        return
    proxy=path.parent/("preview.mp4" if a["type"]=="video" else "preview.m4a")
    args=["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-y","-i",str(path)]
    if a["type"]=="video" and a.get("video_codec")=="h264" and (not a["has_audio"] or a.get("audio_codec")=="aac"):
        args += ["-map","0:v:0","-map","0:a:0?","-c","copy","-movflags","+faststart",str(proxy)]
        db.patch(job["id"],{"stage":"正在封装浏览器播放版本"})
        run(args,job["id"],timeout=24*3600)
        db.patch(aid,{"state":"ready","proxy_path":str(proxy)})
        return
    if a["type"]=="video":
        args += ["-map","0:v:0","-map","0:a:0?","-vf","scale='min(1280,iw)':-2,fps=24","-c:v","libx264","-preset","ultrafast","-crf","25","-threads","16","-pix_fmt","yuv420p"]
    else:
        args += ["-vn"]
    progress=path.parent/'preview.progress'
    progress.unlink(missing_ok=True)
    args += ["-c:a","aac","-b:a","128k","-movflags","+faststart","-progress",str(progress),str(proxy)]
    db.patch(job["id"],{"stage":"正在生成浏览器播放版本"})
    run(args,job["id"],timeout=24*3600,progress_file=progress,duration=a['duration'])
    db.patch(aid,{"state":"ready","proxy_path":str(proxy)})


class ASR:
    def __init__(self,kind):
        self.kind=kind
        self.proc=None
        self.lock=threading.Lock()
        self.log=None
        # Linux PDEATHSIG follows the creating thread. Keep that thread alive
        # across per-recording pools so cached GPU workers survive between jobs.
        self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix=kind+'-rpc')

    def start(self):
        env=os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"]="1" if self.kind=="whisper" else "0"
        env["OMP_NUM_THREADS"]="8"
        env["HF_HUB_OFFLINE"]="1"
        env["MODELSCOPE_OFFLINE"]="1"
        python=os.environ.get("WHISPER_PYTHON" if self.kind=="whisper" else "SENSEVOICE_PYTHON",
            str(db.ROOT/(".venv-whisper" if self.kind=="whisper" else ".venv-sensevoice")/"bin/python"))
        if self.kind=="whisper":
            libs=Path(python).parent.parent/"lib/python3.10/site-packages/nvidia"
            env["LD_LIBRARY_PATH"]=":".join(str(libs/x/"lib") for x in ("cublas","cudnn"))+":"+env.get("LD_LIBRARY_PATH","")
        (db.ROOT/"logs").mkdir(exist_ok=True)
        self.log=open(db.ROOT/"logs"/(self.kind+".log"),"a")
        self.proc=subprocess.Popen([python,"-u",str(db.ROOT/"backend/infer.py"),self.kind],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1,env=env,start_new_session=True)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid,signal.SIGTERM)
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid,signal.SIGKILL); self.proc.wait()
        self.proc=None
        if self.log:
            self.log.close(); self.log=None

    def call(self,request,jid=None):
        return self.executor.submit(self._call,request,jid).result()

    def _call(self,request,jid=None):
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self.stop(); self.start()
            for attempt in range(2):
                try:
                    self.proc.stdin.write(json.dumps(request,ensure_ascii=False)+"\n")
                    self.proc.stdin.flush()
                    break
                except (BrokenPipeError,OSError):
                    self.stop()
                    if attempt:raise RuntimeError(f'{self.kind} 识别进程未能启动，请重试')
                    self.start()
            selector=selectors.DefaultSelector()
            selector.register(self.proc.stdout,selectors.EVENT_READ)
            start=time.monotonic()
            try:
                while time.monotonic()-start<900:
                    if jid: check_cancel(jid)
                    for _,_ in selector.select(.5):
                        line=self.proc.stdout.readline()
                        if not line:
                            raise RuntimeError(f"{self.kind} 识别进程退出，请检查日志后重试")
                        if line.startswith("__RESULT__"):
                            result=json.loads(line[10:])
                            if not result["ok"]: raise RuntimeError(result["error"])
                            return result["result"]
                raise TimeoutError(f"{self.kind} 识别超时")
            except BaseException:
                self.stop()
                raise
            finally:
                selector.close()


whisper=ASR("whisper")
sensevoice=ASR("sensevoice")


def build_chunks(duration,regions):
    """Every timestamp is covered; VAD only improves boundaries, never deletes gaps."""
    ends=sorted(r["end"] for r in regions)
    chunks=[]
    t=0.
    while t<duration-.05:
        limit=min(t+28,duration)
        candidates=[v for v in ends if t+8<=v<=limit]
        end=candidates[-1] if candidates and limit<duration else limit
        chunks.append((round(t,3),round(end,3)))
        if end>=duration: break
        t=max(t+.1,end-.8)
    return chunks


def deduplicate(previous,current):
    if not previous or not current: return current
    # Only remove a literal suffix/prefix overlap, never repetitions elsewhere.
    for n in range(min(len(previous),len(current),100),3,-1):
        if previous[-n:]==current[:n]:
            return current[n:].lstrip()
    return current


def choose_text(sv,wh):
    text=sv.get("text","").strip()
    segments=wh.get("segments",[])
    english=[s for s in segments if s["lang"]=="en"]
    generic=re.sub(r"[^a-z]","",wh.get("text","").lower())
    if sv.get("lang")=="nospeech" and generic in ("thankyou","thanksforwatching","thankyouforwatching") and all(s.get("no_speech",0)>.6 for s in segments):
        return "","nospeech",True
    a=re.sub(r"\W","",text).lower()
    b=re.sub(r"\W","",wh.get("text","")).lower()
    disagree=bool(a and b and difflib.SequenceMatcher(None,a,b).ratio()<.4)
    # Whisper occasionally translates Chinese into English despite transcribe mode.
    # Trust the independent speech-language vote before selecting an English result.
    has_chinese=sum('\u4e00'<=ch<='\u9fff' for ch in text)>=5
    if segments and len(english)==len(segments) and not has_chinese:
        if text and generic in ("thankyou","thanksforwatching","thankyouforwatching") and len(text)>len(wh["text"])*3:
            return text,sv.get("lang","en"),True
        return wh["text"],"en",disagree
    if text:
        a=re.sub(r"\W","",text)
        b=re.sub(r"\W","",wh.get("text",""))
        disagree=bool(a and b and difflib.SequenceMatcher(None,a,b).ratio()<.4)
        return text,sv.get("lang","zh"),disagree
    return wh.get("text",""),wh.get("lang","auto"),bool(wh.get("text"))


def align_text(sv,wh,req,jid=None):
    start=req['start']
    text,lang,uncertain=choose_text(sv,wh)
    ws=wh.get("segments",[])
    mixed=(any(s["lang"]=="en" for s in ws) and any(s["lang"]=="zh" for s in ws)) or (sv.get("lang")=="en" and sum('\u4e00'<=ch<='\u9fff' for ch in sv.get('text',''))>=5)
    if mixed:
        pieces=[]
        for s in ws:
            if s["end"]-s["start"]>.3:
                r=sensevoice.call({**req,"start":start+s["start"],"end":start+s["end"],"language":"auto"},jid)
                selected,_,conflict=choose_text(r,{"text":s["text"],"lang":s["lang"],"segments":[s]})
                pieces.append(selected)
                uncertain=uncertain or conflict
            else: pieces.append(s["text"])
        text=" ".join(pieces);lang="mixed"
    return text,lang,uncertain


_ocr=None
def ocr_engine():
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR, OCRVersion, ModelType
        modeldir=db.ROOT/"models/ocr"
        _ocr=RapidOCR(params={"Det.model_path":str(modeldir/"det.onnx"),
            "Rec.model_path":str(modeldir/"rec.onnx"),"Cls.model_path":str(modeldir/"cls.onnx"),
            "Det.ocr_version":OCRVersion.PPOCRV5,"Det.model_type":ModelType.MOBILE,
            "Rec.ocr_version":OCRVersion.PPOCRV5,"Rec.model_type":ModelType.SERVER,
            "EngineConfig.onnxruntime.intra_op_num_threads":8,"EngineConfig.onnxruntime.inter_op_num_threads":2})
    return _ocr


def frame_ocr(path,pid,start,end,folder,jid):
    import numpy as np
    from PIL import Image
    check_cancel(jid)
    folder.mkdir(exist_ok=True,parents=True)
    # Five-second sampling plus scene-change frames in this ASR window.
    output=folder/"frame-%04d.jpg"
    for old in folder.glob("frame-*.jpg"): old.unlink()
    filt="select='isnan(prev_selected_t)+gte(t-prev_selected_t,5)+gt(scene,0.30)',scale='min(1600,iw)':-2,showinfo"
    args=["ffmpeg","-nostdin","-hide_banner","-loglevel","info","-ss",str(start),"-t",str(max(.1,end-start)),"-i",str(path),
          "-vf",filt,"-vsync","vfr","-threads","4","-q:v","3",str(output)]
    # Capture showinfo separately for accurate selected-frame timestamps.
    log=folder/"frames.log"
    with open(log,"w") as f:
        proc=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=f,start_new_session=True)
        began=time.monotonic()
        try:
            while proc.poll() is None:
                check_cancel(jid)
                if time.monotonic()-began>240: raise TimeoutError("视频抓帧超时")
                time.sleep(.2)
        except BaseException:
            os.killpg(proc.pid,signal.SIGTERM);proc.wait();raise
    if proc.returncode:
        raise RuntimeError("视频抓帧失败："+log.read_text(errors="replace")[-300:])
    times=[float(t) for t in re.findall(r"pts_time:([0-9.]+)",log.read_text(errors="replace"))]
    result=[]
    last_hash=None
    for idx,frame in enumerate(sorted(folder.glob("frame-*.jpg"))):
        check_cancel(jid)
        with Image.open(frame) as image:
            arr=np.asarray(image.convert("L").resize((17,16)))
            h=(arr[:,1:]>arr[:,:-1]).reshape(-1)
        if last_hash is not None and np.count_nonzero(h!=last_hash)<8:
            frame.unlink();continue
        last_hash=h
        output=ocr_engine()(str(frame))
        texts=list(output.txts) if output.txts is not None else []
        scores=[float(x) for x in output.scores] if output.scores is not None else []
        kept=[(t,s) for t,s in zip(texts,scores) if s>=.5 and t.strip()]
        if not kept:
            frame.unlink();continue
        at=start+(times[idx] if idx<len(times) else min(idx*5,end-start))
        eid=hashlib.sha256(f"{pid}:frame:{at:.2f}".encode()).hexdigest()[:32]
        with db.transaction() as c:
            if not db.get(eid,c):
                db.insert(c,"evidence",{"time":at,"end":end,"text":"\n".join(t for t,_ in kept),
                    "confidence":sum(s for _,s in kept)/len(kept),"path":str(frame),"source":"视频画面 OCR"},pid,eid)
        result.append(eid)
    return result


def apply_speakers(c,pid,turns):
    changed=False
    for block in db.records('block',pid,c):
        d=block['data']
        if d.get('speaker_manual'):continue
        within=[t for t in turns if t['end']>d['start'] and t['start']<d['end']]
        overlaps={}
        for t in within:
            seconds=max(0,min(d['end'],t['end'])-max(d['start'],t['start']))
            overlaps[t['speaker_id']]=overlaps.get(t['speaker_id'],0)+seconds
        overlaps={s:v for s,v in overlaps.items() if v>.1}
        if not overlaps:continue
        overlap=any(a['speaker_id']!=b['speaker_id'] and min(a['end'],b['end'])-max(a['start'],b['start'])>.1 for i,a in enumerate(within) for b in within[i+1:])
        data={'speaker_id':max(overlaps,key=overlaps.get),'speaker_mixed':len(overlaps)>1,
            'speaker_overlap':overlap,'uncertain':d.get('uncertain',False) or overlap,'speaker_turns':within}
        if any(d.get(k)!=v for k,v in data.items()):
            db.update(c,block['id'],data);changed=True
    if changed:db.bump_transcript(c,pid)


def diarize(wav,pid,jid,threshold=.8):
    import sys
    check_cancel(jid)
    cached=Path(wav).with_suffix(f'.speakers-{threshold:.2f}.json')
    if cached.exists():result=json.loads(cached.read_text())
    else:
        progress=Path(wav).with_suffix('.speakers.progress')
        progress.unlink(missing_ok=True)
        output=run([sys.executable,'-m','backend.diarize',str(wav),str(progress),str(threshold)],jid,24*3600,progress_file=progress,duration=1)
        result=json.loads(output)
        temp=cached.with_suffix('.tmp');temp.write_text(json.dumps(result));os.replace(temp,cached)
    check_cancel(jid)
    mapped={};turns=[]
    with db.transaction() as c:
        for segment in result:
            key=int(segment['speaker'])
            if key not in mapped:
                sid=f'{pid}-speaker-{len(mapped)+1}';mapped[key]=sid
                if not db.get(sid,c):db.insert(c,'speaker',{'name':f'说话人{len(mapped)}','manual':False},pid,sid)
            turns.append({'start':segment['start'],'end':segment['end'],'speaker_id':mapped[key]})
        db.update(c,pid,{'speaker_turns':turns})
        apply_speakers(c,pid,turns)
        from sqlalchemy import delete
        used=set(mapped.values())|{b['data'].get('speaker_id') for b in db.records('block',pid,c)}
        for old in db.records('speaker',pid,c):
            if old['id'] not in used and not old['data'].get('manual'):
                c.execute(delete(db.Record).where(db.Record.id==old['id']))
    return turns


def speaker_job(job):
    db.patch(job['id'],{'stage':'正在区分说话人'})
    diarize(job['data']['payload']['wav'],job['project_id'],job['id'],job['data']['payload'].get('threshold',.8))


def seal_block(conn,block,settings):
    bid=block["id"]
    d=block["data"]
    provider=settings.get("provider_id")
    has_ai=settings.get("ai_enabled") and provider and db.get(provider,conn)
    block=db.update(conn,bid,{"state":"queued" if has_ai else "final","text":d["raw"],"ai_draft":""})
    db.bump_transcript(conn,block["project_id"])
    if has_ai:
        db.new_job(conn,block["project_id"],"revise",{"block_id":bid,"provider_id":provider,
            "input_version":block["version"],"suggestion":False,"settings":settings},bid)


def append_chunk(pid,job,idx,start,end,text,lang,uncertain,sv,wh,evidence,settings):
    sid=f"{job['id']}-segment-{idx}"
    with db.transaction() as c:
        if db.get(sid,c): return
        segments=db.records("segment",pid,c)
        prev=segments[-1]["data"]["text"] if segments else ""
        text=deduplicate(prev,text)
        db.insert(c,"segment",{"start":start,"end":end,"text":text,"language":lang,"uncertain":uncertain,
            "whisper":wh,"sensevoice":sv,"chunk":idx,"evidence_ids":evidence},pid,sid)
        if text:
            blocks=db.records("block",pid,c)
            block=next((b for b in reversed(blocks) if b["data"]["state"]=="accumulating"),None)
            if block:
                raw=block["data"]["raw"]+(" " if lang=="en" else "")+text
                block=db.update(c,block["id"],{"raw":raw,"end":end,
                    "segment_ids":block["data"]["segment_ids"]+[sid],
                    "evidence_ids":list(dict.fromkeys(block["data"].get("evidence_ids",[])+evidence)),
                    "uncertain":uncertain or block["data"].get("uncertain",False)})
            else:
                block=db.insert(c,"block",{"raw":text,"text":"","start":start,"end":end,"state":"accumulating",
                    "segment_ids":[sid],"evidence_ids":evidence,"uncertain":uncertain,"manual":False,
                    "speaker_id":None,"speaker_manual":False,"ai_draft":""},pid)
            if len(re.sub(r"\s","",block["data"]["raw"]))>=settings.get("threshold",300) and re.search(r"[。！？.!?][\"'”’）)]?$",text):
                seal_block(c,block,settings)
        turns=db.get(pid,c)['data'].get('speaker_turns')
        if turns:apply_speakers(c,pid,turns)
        current=db.get(job["id"],c)["data"].get("checkpoint",{})
        current["chunk"]=idx+1
        db.update(c,job["id"],{"checkpoint":current,"progress":min(.94,end/max(job["data"].get("duration",end),1)*.94),"stage":f"已识别 {int(end//60)}分{int(end%60):02}秒"})


def transcribe(job):
    jid=job["id"];pid=job["project_id"]
    asset=db.get(job["data"]["payload"]["asset_id"])["data"]
    settings=job["data"]["payload"]["settings"]
    folder=db.DATA/"projects"/pid/"processing"
    folder.mkdir(parents=True,exist_ok=True)
    duration=asset["duration"]
    db.patch(jid,{"stage":"准备音频","duration":duration})
    job=db.get(jid)
    wav=folder/"audio.wav"
    if asset["has_audio"] and not wav.exists():
        temp=folder/"audio.tmp.wav"
        run(["ffmpeg","-nostdin","-v","error","-y","-i",asset["path"],"-map","0:a:0","-vn","-ac","1","-ar","16000",
            "-af","highpass=f=70,dynaudnorm=f=500:g=31:p=0.9:m=20","-c:a","pcm_s16le",str(temp)],jid,24*3600)
        os.replace(temp,wav)
    if asset['has_audio'] and settings.get('diarization',True):
        with db.transaction() as c:
            if not any(j['data']['kind']=='diarize' for j in db.records('job',pid,c)):
                db.new_job(c,pid,'diarize',{'wav':str(wav),'threshold':settings.get('speaker_threshold',.8)},job['data']['payload']['asset_id'])
    chunksfile=folder/"chunks.json"
    if chunksfile.exists():
        chunks=json.loads(chunksfile.read_text())
    elif asset["has_audio"]:
        db.patch(jid,{"stage":"检测语音边界并建立完整覆盖"})
        with wave.open(str(wav)) as w: duration=w.getnframes()/w.getframerate()
        regions=whisper.call({"method":"vad","path":str(wav)},jid)
        chunks=build_chunks(duration,regions)
        chunksfile.write_text(json.dumps(chunks))
    else:
        chunks=build_chunks(duration,[])
        if not settings.get("ocr"):
            raise RuntimeError("视频没有音轨，请开启本地画面 OCR 后重试")
    begin=job["data"].get("checkpoint",{}).get("chunk",0)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for idx in range(begin,len(chunks)):
            check_cancel(jid)
            start,end=chunks[idx]
            ocr_future=None
            if asset["has_video"] and settings.get("ocr",True):
                ocr_future=pool.submit(frame_ocr,asset["path"],pid,start,end,folder/f"frames-{idx:05}",jid)
            if asset["has_audio"]:
                req={"path":str(wav),"start":start,"end":end,"language":settings.get("language","auto")}
                wf=pool.submit(whisper.call,req,jid)
                sf=pool.submit(sensevoice.call,req,jid)
                wh=wf.result();sv=sf.result()
                text,lang,uncertain=align_text(sv,wh,req,jid)
            else:
                wh={};sv={};text="";lang="zh";uncertain=False
            evidence=ocr_future.result() if ocr_future else []
            if not asset["has_audio"]:
                text="\n".join(f"[画面文字 {int(db.get(e)['data']['time'])}秒] {db.get(e)['data']['text']}" for e in evidence)
                uncertain=True
            append_chunk(pid,job,idx,start,end,text,lang,uncertain,sv,wh,evidence,settings)
    with db.transaction() as c:
        for b in db.records("block",pid,c):
            if b["data"]["state"]=="accumulating": seal_block(c,b,settings)
    db.patch(jid,{"coverage_seconds":duration,"chunk_count":len(chunks)})
