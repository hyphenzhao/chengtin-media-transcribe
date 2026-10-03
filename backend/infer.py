"""Line-delimited RPC workers run in the isolated ASR environments."""
import contextlib
import json
import os
import re
import sys
import wave
import numpy as np

# Avoid orphan GPU workers after an unexpected scheduler exit on Linux.
if sys.platform == "linux":
    import ctypes, signal
    parent=os.getppid()
    ctypes.CDLL(None).prctl(1,signal.SIGTERM)
    if os.getppid()!=parent:
        raise SystemExit(0)

kind=sys.argv[1]
os.environ.setdefault("OMP_NUM_THREADS","8")
os.environ.setdefault("HF_HUB_OFFLINE","1")
os.environ.setdefault("MODELSCOPE_OFFLINE","1")
SR=16000
model=None


def samples(path,start=0,end=None):
    with wave.open(path,"rb") as w:
        w.setpos(min(w.getnframes(),int(start*SR)))
        n=int((end-start)*SR) if end is not None else w.getnframes()-w.tell()
        return np.frombuffer(w.readframes(n),dtype=np.int16).astype(np.float32)/32768


def process(req):
    global model
    audio=samples(req["path"],req.get("start",0),req.get("end"))
    if req.get("method")=="vad":
        from faster_whisper.vad import VadOptions,get_speech_timestamps
        ts=get_speech_timestamps(audio,VadOptions(threshold=.15,min_speech_duration_ms=150,
            max_speech_duration_s=28,min_silence_duration_ms=300,speech_pad_ms=250))
        return [{"start":s["start"]/SR,"end":s["end"]/SR} for s in ts]
    if len(audio)<1600:
        return {"text":"","segments":[],"lang":"nospeech"}
    rms=float(np.sqrt(np.mean(audio*audio)))
    if rms<.00008:
        return {"text":"","segments":[],"lang":"nospeech","rms":rms}
    if kind=="whisper":
        from faster_whisper import WhisperModel
        if model is None:
            model=WhisperModel(os.environ.get("WHISPER_MODEL","/home/haifeng/asr_work/models/large-v3"),
                device="cuda",device_index=0,compute_type="float16",cpu_threads=8)
        language=req.get("language","auto")
        segs,info=model.transcribe(audio,beam_size=5,vad_filter=False,
            condition_on_previous_text=False,language=None if language=="auto" else language,
            multilingual=language=="auto",no_speech_threshold=.8,
            log_prob_threshold=-1.5,temperature=[0.,.2,.4,.6])
        out=[]
        for s in segs:
            text=s.text.strip()
            if not text or (s.no_speech_prob>.85 and s.avg_logprob<-1):
                continue
            zh=sum('\u4e00'<=c<='\u9fff' for c in text)
            letters=sum(c.isalpha() for c in text)
            out.append({"start":s.start,"end":min(s.end,len(audio)/SR),"text":text,
                "lang":"zh" if zh/max(letters,1)>.15 else "en", "no_speech":s.no_speech_prob,
                "logprob":s.avg_logprob})
        return {"segments":out,"text":" ".join(s["text"] for s in out),"lang":info.language,"rms":rms}
    from funasr import AutoModel
    if model is None:
        model=AutoModel(model=os.environ.get("SENSEVOICE_MODEL","/home/haifeng/.cache/modelscope/models/iic--SenseVoiceSmall/snapshots/master"),
            punc_model=os.environ.get("PUNC_MODEL","/home/haifeng/.cache/modelscope/models/iic--punc_ct-transformer_cn-en-common-vocab471067-large/snapshots/master"),
            device="cuda:0",disable_update=True,disable_pbar=True,hub="ms")
    res=model.generate(input=audio,language=req.get("language","auto"),use_itn=True,batch_size=1,disable_pbar=True)
    raw=res[0].get("text","") if res else ""
    tags=[re.sub(r"\s","",t).lower() for t in re.findall(r"<\s*\|([^|]*)\|\s*>",raw)]
    lang=next((t for t in tags if t in ("zh","en","yue","ja","ko","nospeech")),"auto")
    text=re.sub(r"<\s*\|[^|]*\|\s*>","",raw).strip()
    text=re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])","",text)
    text=re.sub(r"([，。、？！])\1+",r"\1",text)
    if lang=="nospeech" or not re.search(r"\w",text): text=""
    return {"text":text,"lang":lang,"rms":rms}


for line in sys.stdin:
    try:
        req=json.loads(line)
        with contextlib.redirect_stdout(sys.stderr):
            result=process(req)
        print("__RESULT__"+json.dumps({"ok":True,"result":result},ensure_ascii=False),flush=True)
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print("__RESULT__"+json.dumps({"ok":False,"error":str(e)},ensure_ascii=False),flush=True)
