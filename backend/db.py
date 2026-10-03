"""Durable records and ordered events. All writes use short SQLite transactions."""
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Column, Float, Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MEDIA_DATA", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DATA / 'app.sqlite3'}", connect_args={"timeout": 30})
Base = declarative_base()


class Record(Base):
    __tablename__ = "records"
    id = Column(String, primary_key=True)
    kind = Column(String, nullable=False, index=True)
    project_id = Column(String, index=True)
    version = Column(Integer, nullable=False, default=1)
    data = Column(Text, nullable=False)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, index=True)
    name = Column(String, nullable=False)
    data = Column(Text, nullable=False)
    created_at = Column(Float, nullable=False)


@event.listens_for(engine, "connect")
def pragmas(conn, _):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")


Base.metadata.create_all(engine)


class Conflict(Exception):
    pass


def unpack(row):
    if row is None:
        return None
    d = dict(row._mapping)
    d["data"] = json.loads(d["data"])
    return d


def get(rid, conn=None):
    if conn is None:
        with engine.connect() as c:
            return get(rid, c)
    return unpack(conn.execute(select(Record.__table__).where(Record.id == rid)).first())


def records(kind, project_id=None, conn=None):
    if conn is None:
        with engine.connect() as c:
            return records(kind, project_id, c)
    q = select(Record.__table__).where(Record.kind == kind)
    if project_id is not None:
        q = q.where(Record.project_id == project_id)
    return [unpack(r) for r in conn.execute(q.order_by(Record.created_at, Record.id))]


@contextmanager
def transaction():
    with engine.connect() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise


def emit(conn, project_id, name, data):
    conn.execute(Event.__table__.insert().values(project_id=project_id, name=name,
                 data=json.dumps(data, ensure_ascii=False), created_at=time.time()))


def insert(conn, kind, data, project_id=None, rid=None):
    rid = rid or uuid.uuid4().hex
    now = time.time()
    conn.execute(Record.__table__.insert().values(id=rid, kind=kind, project_id=project_id,
                 version=1, data=json.dumps(data, ensure_ascii=False), created_at=now, updated_at=now))
    emit(conn, project_id, "changed", {"kind": kind, "id": rid})
    return get(rid, conn)


def update(conn, rid, changes, expected=None):
    rec = get(rid, conn)
    if not rec:
        raise KeyError(rid)
    if expected is not None and rec["version"] != expected:
        raise Conflict("内容已在其他页面或后台更新，请刷新后重试；你的输入已保留。")
    rec["data"].update(changes)
    conn.execute(Record.__table__.update().where(Record.id == rid).values(
        data=json.dumps(rec["data"], ensure_ascii=False), version=rec["version"]+1, updated_at=time.time()))
    emit(conn, rec["project_id"], "changed", {"kind": rec["kind"], "id": rid})
    return get(rid, conn)


def patch(rid, changes, expected=None):
    with transaction() as c:
        return update(c, rid, changes, expected)


def bump_transcript(conn, project_id):
    p = get(project_id, conn)
    return update(conn, project_id, {"transcript_revision": p["data"].get("transcript_revision", 0)+1})


def new_job(conn, project_id, kind, payload=None, unique=None):
    for j in records("job", project_id, conn):
        if j["data"]["kind"] == kind and j["data"]["state"] in ("queued", "running") and j["data"].get("unique") == unique:
            return j
    return insert(conn, "job", {"kind": kind, "state": "queued", "progress": 0,
        "stage": "等待处理", "payload": payload or {}, "unique": unique, "cancel": False,
        "attempt": 0, "error": "", "checkpoint": {}}, project_id)


def events_after(after, project_id=None):
    with engine.connect() as c:
        q = select(Event.__table__).where(Event.id > after)
        if project_id:
            q = q.where((Event.project_id == project_id) | (Event.project_id.is_(None)))
        return [unpack(r) for r in c.execute(q.order_by(Event.id).limit(250))]
