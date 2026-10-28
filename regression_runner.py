"""Offline, assertive image/video regression. Never sends input.

Video: decode ALL frames for integrity, analyze 1 fps and run background motion
at ~4 fps. This is sampling, not a full closed-loop gameplay test.
"""
from pathlib import Path
import argparse
import hashlib
import json
import time
import cv2
import numpy as np
from vision_engine import VisionEngine, Navigator, BackgroundTracker
from perception import BuffIcons

ROOT=Path(__file__).resolve().parent
EXPECTED_VIDEO='c542faf10c575abc1351be70dfc2e1ed307c0d592a35825ba1c21cf08dd5aeb5'

def annotate(frame,obs,anchor=None,target=None,reason=''):
    vis=frame.copy()
    for m in obs['monsters']:
        x,y,w,h=map(int,m['box']);color=(60,220,70) if m['kind']=='turtle' else (20,200,240)
        cv2.rectangle(vis,(x,y),(x+w,y+h),color,2)
        count=sum(np.hypot(z['x']-m['x'],z['y']-m['y'])<=185 for z in obs['monsters'])
        cv2.putText(vis,f"{m['kind']} {m['confidence']:.2f} pack={count}",(x,max(15,y-4)),0,.45,color,1)
    for z in obs['portals']+obs['obstacles']:
        if z.get('geometry')=='rectangle':
            x,y,w,h=z['box'];pad=z.get('padding',18)
            cv2.rectangle(vis,(x-pad,y-pad),(x+w+pad,y+h+pad),(0,40,255),2)
        else:cv2.circle(vis,(int(z['x']),int(z['y'])),int(z['radius']),(0,40,255),2)
        cv2.putText(vis,z['kind'],(int(z['x']),int(z['y'])),0,.5,(0,80,255),1)
    if anchor is not None:
        a=tuple(map(int,anchor));cv2.circle(vis,a,8,(255,255,0),2)
        if target:cv2.arrowedLine(vis,a,tuple(map(int,target)),(0,255,255),3)
    cv2.rectangle(vis,(0,0),(vis.shape[1],25),(0,0,0),-1)
    cv2.putText(vis,reason[:150],(5,18),0,.42,(255,255,255),1)
    return vis

def run(output,video=None,require_video=False):
    cv2.setNumThreads(2)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    engine=VisionEngine();icons=BuffIcons()
    labels=json.loads((ROOT/'regression/labels.json').read_text())
    report=dict(images=[],video=None,failures=[],notes=labels['notes'])
    for p in sorted((ROOT/'references').glob('*.png')):
        frame=cv2.imread(str(p));start=time.monotonic()
        if frame is None:report['failures'].append(p.name+': unreadable');continue
        # Detection assertions use the original context, avoiding a crop that
        # could trivially remove a known false detection.
        original=engine.analyze(frame)
        negatives=labels['negative_regions'].get(p.name,[])
        false=[]
        for m in original['monsters']:
            bx,by,bw,bh=m['box'];cx,cy=bx+bw/2,by+bh/2
            if any(x<=cx<x+w and y<=cy<y+h for x,y,w,h in negatives):false.append(m)
        if false:report['failures'].append(f'{p.name}: {len(false)} labelled false detections')
        for key,minimum in labels['positive_reference_checks'].get(p.name,{}).items():
            actual=len(original['monsters'] if key=='minimum_monsters' else original['portals'])
            if actual<minimum:report['failures'].append(f'{p.name}: {key} expected >= {minimum}, got {actual}')
        rect=labels['game_rectangles'].get(p.name,[0,0,frame.shape[1],frame.shape[0]])
        x,y,w,h=rect;client=frame[y:y+h,x:x+w]
        obs=engine.analyze(client) if rect[:2]!=[0,0] else original
        # Static screenshots cannot establish reliable camera motion. Score and
        # render detections, but do not fabricate confidence for a move click.
        nav=Navigator();anchor=(int(client.shape[1]*.52),int(client.shape[0]*.50))
        target,reason=nav.choose(client,anchor,obs)
        if target is not None:report['failures'].append(p.name+': moved without motion evidence')
        flags,scores,reliable=icons.read(client)
        annotated=annotate(client,obs,anchor,target,p.name+' | '+reason)
        cv2.imwrite(str(output/(p.stem+'.jpg')),annotated,[cv2.IMWRITE_JPEG_QUALITY,85])
        report['images'].append(dict(name=p.name,monsters=len(obs['monsters']),portals=len(obs['portals']),
            ghost_count=sum(m['kind']=='ghost' for m in obs['monsters']),negative_regions=len(negatives),
            negative_false_detections=len(false),original_detections=original['monsters'],
            icons=flags,icon_scores=scores,hud_reliable=reliable,seconds=round(time.monotonic()-start,3)))
    video=Path(video) if video else ROOT/'references/gameplay_camera_motion.mp4'
    if video.is_file():
        digest=hashlib.sha256(video.read_bytes()).hexdigest()
        if digest!=EXPECTED_VIDEO:report['failures'].append('video SHA256 mismatch')
        cap=cv2.VideoCapture(str(video));fps=cap.get(cv2.CAP_PROP_FPS);declared=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        tracker=BackgroundTracker();nav=Navigator();samples=[];decoded=0;motion_updates=0;route_checks=0;route_failures=0
        while True:
            ok,frame=cap.read()
            if not ok:break
            frame_no=decoded;decoded+=1
            if frame_no%max(1,round(fps/4))==0:
                tracker.update(frame,(800,450));motion_updates+=1
            if frame_no%max(1,round(fps))!=0:continue
            start=time.monotonic();obs=engine.analyze(frame)
            flags,scores,reliable=icons.read(frame)
            # The recorded player is not anchored at the viewport center.
            # Evaluate candidate routes as hypothetical geometry only.
            anchor=(800,450);nav.tracker=tracker;target,reason=nav.choose(frame,anchor,obs)
            if target:
                route_checks+=1
                if not nav.visible_floor(frame,anchor,target,obs['portals'],obs['obstacles']):
                    route_failures+=1
            row=dict(frame=frame_no,seconds=round(frame_no/fps,3),monsters=len(obs['monsters']),
                portals=len(obs['portals']),icons=flags,icon_scores=scores,hud_reliable=reliable,
                motion_confident=tracker.confident,world=list(tracker.world),target=target,reason=reason,
                analysis_seconds=round(time.monotonic()-start,3))
            samples.append(row)
            cv2.imwrite(str(output/f'video_{len(samples)-1:03d}.jpg'),annotate(frame,obs,anchor,target,
                f"t={row['seconds']}s | {reason}"),[cv2.IMWRITE_JPEG_QUALITY,80])
        cap.release()
        if decoded!=declared or decoded==0:report['failures'].append(f'video decoded {decoded}/{declared}')
        if route_failures:report['failures'].append(f'{route_failures} returned routes failed corridor recheck')
        # Positive/negative video evidence; not an accuracy metric without labels.
        if not any(s['monsters']>0 for s in samples if s['seconds']>=10):report['failures'].append('no monsters recognized in combat footage')
        report['video']=dict(sha256=digest,declared_frames=declared,decoded_frames=decoded,fps=fps,
            duration_seconds=decoded/fps,motion_samples=motion_updates,detection_samples=len(samples),
            route_checks=route_checks,route_failures=route_failures,samples=samples)
    elif require_video:report['failures'].append('required video missing')
    else:report['video']=dict(skipped=True,reason='Video not included in lightweight code ZIP; use --video PATH')
    (output/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(images=len(report['images']),video={k:v for k,v in (report['video'] or {}).items() if k!='samples'},failures=report['failures']),indent=2))
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'regression_output'))
    parser.add_argument('--video');parser.add_argument('--require-video',action='store_true');args=parser.parse_args()
    result=run(args.output,args.video,args.require_video)
    raise SystemExit(bool(result['failures']))
