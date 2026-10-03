"""One durable media lane and one ordered AI lane, supervised independently of HTTP."""
import fcntl
import signal
import threading
import time
import traceback

from . import ai, db, media

stop=threading.Event()


def claim(kinds):
    with db.transaction() as c:
        for job in db.records("job",conn=c):
            d=job["data"]
            if d["state"]!="queued" or d["kind"] not in kinds: continue
            project=db.get(job["project_id"],c)
            if d.get("cancel") or not project or project["data"].get("deleted"):
                db.update(c,job["id"],{"state":"cancelled","stage":"已停止"});continue
            return db.update(c,job["id"],{"state":"running","stage":"开始处理","attempt":d.get("attempt",0)+1,"started_at":time.time()})


def lane(kinds):
    while not stop.is_set():
        job=claim(kinds)
        if not job:
            stop.wait(.5);continue
        kind=job["data"]["kind"]
        try:
            {"prepare":media.prepare,"transcribe":media.transcribe,"diarize":media.speaker_job,"revise":ai.revise,
             "extract":ai.extract,"diagram":ai.diagram,"title":ai.metadata,"synopsis":ai.metadata}[kind](job)
            media.check_cancel(job["id"])
            db.patch(job["id"],{"state":"complete","stage":"处理完成","progress":1,"finished_at":time.time()})
        except media.Cancelled:
            db.patch(job["id"],{"state":"cancelled","stage":"已停止，已完成内容已保存"})
        except Exception as e:
            traceback.print_exc()
            db.patch(job["id"],{"state":"failed","stage":"处理失败，可重试","error":str(e)[-1200:]})
            if kind=="prepare":
                db.patch(job["data"]["payload"]["asset_id"],{"state":"failed"})


def main():
    lock=open(db.DATA/"worker.lock","w")
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit("worker already running")
    with db.transaction() as c:
        for job in db.records("job",conn=c):
            if job["data"]["state"]=="running":
                db.update(c,job["id"],{"state":"queued","stage":"服务恢复，等待续传","error":""})
        if not db.get("worker-heartbeat",c): db.insert(c,"system",{},rid="worker-heartbeat")
    def shutdown(*_):
        stop.set()
        with db.transaction() as c:
            for j in db.records("job",conn=c):
                if j["data"]["state"]=="running": db.update(c,j["id"],{"cancel":True,"restart_pending":True})
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    workers=[threading.Thread(target=lane,args=(k,),daemon=True) for k in
        [{"transcribe"},{"prepare"},{"diarize"},{"diarize"},{"revise","extract","diagram","title","synopsis"}]]
    for t in workers:t.start()
    try:
        while not stop.wait(5): db.patch("worker-heartbeat",{"time":time.time()})
    finally:
        for t in workers:t.join(timeout=15)
        media.whisper.stop();media.sensevoice.stop()
        with db.transaction() as c:
            for j in db.records("job",conn=c):
                if j["data"].get("restart_pending"):
                    db.update(c,j["id"],{"state":"queued","cancel":False,"restart_pending":False})


if __name__=="__main__": main()
