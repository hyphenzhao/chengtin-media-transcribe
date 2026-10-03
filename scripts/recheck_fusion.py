"""Recheck only the deployment's regression projects after language-selection fixes."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from backend import db,media

pid=sys.argv[1]
assert db.get(pid)['data']['title'].startswith('验收 · IMG_')
segments=db.records('segment',pid)
changes=[];previous=''
try:
    for row in segments:
        d=row['data']
        req={'path':str(db.DATA/'projects'/pid/'processing/audio.wav'),'start':d['start'],'end':d['end'],'language':'auto'}
        text,lang,uncertain=media.align_text(d['sensevoice'],d['whisper'],req)
        text=media.deduplicate(previous,text);previous=text
        if text!=d['text'] or uncertain!=d['uncertain']:
            changes.append((row['id'],{'text':text,'language':lang,'uncertain':uncertain}))
    with db.transaction() as c:
        for rid,change in changes:db.update(c,rid,change)
        for b in db.records('block',pid,c):
            d=b['data'];parts=[db.get(s,c)['data'] for s in d['segment_ids']]
            raw=''
            for part in parts:raw+=(' ' if raw and part['language']=='en' else '')+part['text']
            updates={'raw':raw,'uncertain':any(p['uncertain'] for p in parts) or d.get('speaker_overlap',False)}
            if not d.get('manual') and not d.get('ai_revised'):updates['text']=raw
            if any(d.get(k)!=v for k,v in updates.items()):db.update(c,b['id'],updates)
        db.bump_transcript(c,pid)
    (db.ROOT/'logs/fusion-regression.json').write_text(json.dumps({'project':pid,'updated_segments':len(changes),
        'original_model_outputs_preserved':True,'manual_edits_preserved':True}))
    print('Current language fusion rules applied:',len(changes),'segments; raw model outputs and manual edits preserved.')
finally:media.sensevoice.stop()
