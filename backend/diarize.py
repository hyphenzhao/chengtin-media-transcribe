"""CPU speaker analysis is isolated so long recordings cannot block scheduling."""
import json
import os
import sys
from pathlib import Path
import sherpa_onnx
import soundfile as sf

def main():
    wav,progress=sys.argv[1:3]
    threads=int(os.environ.get('DIAR_THREADS','8'))
    threshold=float(sys.argv[3] if len(sys.argv)>3 else os.environ.get('DIAR_THRESHOLD','.8'))
    modeldir=Path(__file__).resolve().parent.parent/'models/diarization'
    config=sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(modeldir/'segmentation.onnx')),num_threads=threads,provider='cpu'),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(modeldir/'embedding.onnx'),num_threads=threads,provider='cpu'),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1,threshold=threshold),min_duration_on=.3,min_duration_off=.5)
    if not config.validate():raise RuntimeError('说话人模型配置无效')
    samples,sr=sf.read(wav,dtype='float32')
    if sr!=16000:raise RuntimeError('说话人分析需要 16kHz 音频')
    def report(done,total):
        with open(progress,'a') as f:f.write(f'out_time_ms={int(done/max(total,1)*1e6)}\n')
        return 0
    sd=sherpa_onnx.OfflineSpeakerDiarization(config)
    result=sd.process(samples,callback=report).sort_by_start_time()
    print(json.dumps([{'speaker':int(s.speaker),'start':s.start,'end':s.end} for s in result]),flush=True)

if __name__=='__main__':main()
