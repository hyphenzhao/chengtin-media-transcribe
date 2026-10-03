"""Provision public model files once; runtime always uses explicit local paths."""
import concurrent.futures
import hashlib
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path
import rapidocr
import yaml

ROOT=Path(__file__).resolve().parent.parent
MODELS=ROOT/'models'
manifest=[]


def fetch(url,dest,expected=None):
    dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():
        print('Downloading',dest.name,flush=True)
        request=urllib.request.Request(url,headers={'User-Agent':'media-transcribe/1.0'})
        with urllib.request.urlopen(request,timeout=120) as r,open(str(dest)+'.part','wb') as f:
            shutil.copyfileobj(r,f)
        Path(str(dest)+'.part').replace(dest)
    digest=hashlib.sha256(dest.read_bytes()).hexdigest()
    if expected and digest!=expected:
        raise RuntimeError(f'Checksum mismatch: {dest.name}')
    manifest.append({'file':str(dest.relative_to(ROOT)),'url':url,'sha256':digest})
    print('Ready',dest.name,flush=True)
    return dest


config=yaml.safe_load((Path(rapidocr.__file__).parent/'default_models.yaml').read_text())['onnxruntime']
tasks=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    for version,kind,name in [('PP-OCRv5','det','ch_PP-OCRv5_det_mobile'),('PP-OCRv5','rec','ch_PP-OCRv5_rec_server'),('PP-OCRv4','cls','ch_ppocr_mobile_v2.0_cls_mobile')]:
        m=config[version][kind][name]
        tasks.append(pool.submit(fetch,m['model_dir'],MODELS/'ocr'/f'{kind}.onnx',m['SHA256']))
    tasks.append(pool.submit(fetch,'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx',MODELS/'diarization/embedding.onnx'))
    archive=fetch('https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2',MODELS/'diarization/segmentation.tar.bz2')
    with tarfile.open(archive) as tar:
        member=next(m for m in tar.getmembers() if m.name.endswith('/model.onnx') and m.isfile())
        with tar.extractfile(member) as src,open(MODELS/'diarization/segmentation.onnx','wb') as dst:
            shutil.copyfileobj(src,dst)
    for task in tasks: task.result()
(MODELS/'manifest.json').write_text(json.dumps(manifest,indent=2))
print('All models ready',flush=True)
