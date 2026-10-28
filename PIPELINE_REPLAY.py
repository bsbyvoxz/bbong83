"""Paced replay through the live worker; no Windows calls or input injection.

Uses a hypothetical (800,450) anchor for this supplied 1600x900 recording.
This tests worker delivery and geometry, not gameplay success or true navigation.
"""
import argparse
import json
from pathlib import Path
import time
import cv2
from polar_v18 import CONFIG, VisionWorker

ROOT=Path(__file__).resolve().parent

def replay(video,output):
    cap=cv2.VideoCapture(str(video))
    if not cap.isOpened():raise RuntimeError('video unreadable')
    fps=cap.get(cv2.CAP_PROP_FPS);count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    worker=VisionWorker();rows=[];began=time.monotonic();decoded=0;seen=-1
    def record():
        nonlocal seen
        s=worker.snapshot
        if s is None or s['sequence']==seen:return
        seen=s['sequence'];now=time.monotonic()
        rows.append(dict(time=round(now-began,3),sequence=s['sequence'],
            frame_age=round(now-s['captured'],3),semantic_age=s['semantic_age'],
            ready=s['obs'] is not None,monsters=len(s['obs']['monsters']) if s['obs'] is not None else None,
            camera=s['confident'],target=s['target'],reason=s['reason'],health=worker.health()))
    try:
        while True:
            ok,frame=cap.read()
            if not ok:break
            i=decoded;decoded+=1
            if i%max(1,round(fps/10)):continue
            due=began+i/fps
            while time.monotonic()<due:
                record();time.sleep(min(.01,max(0.,due-time.monotonic())))
            worker.submit(dict(frame=frame,anchor=(800,450),generation=1,captured=time.monotonic(),move=True,escape=None))
            record()
        end=time.monotonic()+.3
        while time.monotonic()<end:record();time.sleep(.02)
    finally:worker.close();cap.release()
    result=dict(decoded=decoded,declared=count,samples=len(rows),ready=sum(r['ready'] for r in rows),
        camera_confident=sum(r['camera'] for r in rows),targets=sum(r['target'] is not None for r in rows),
        max_fast_frame_age=max((r['frame_age'] for r in rows),default=None),
        note='Hypothetical anchor; no game inputs, ground-truth movements or actual navigation tested.',rows=rows)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
    return decoded==count and len(rows)>0 and result['ready']>0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--video',default=str(ROOT/'references/gameplay_camera_motion.mp4'))
    p.add_argument('--output',default=str(ROOT/'regression_output/pipeline_v21_1.json'));a=p.parse_args()
    raise SystemExit(0 if replay(a.video,a.output) else 1)
