"""Live perception pipeline: fast motion/route checks never wait on detection.

Semantic results retain their original capture timestamp. Old coordinates may be
projected only through a continuous, reliable camera-motion track. No timestamp
refresh or permissive random movement is used to hide a slow detector.
"""
import logging
import queue
import threading
import time
import numpy as np
from perception import BuffIcons
from vision_engine import Navigator, VisionEngine


def replace_latest(jobs, job):
    try: jobs.get_nowait()
    except queue.Empty: pass
    try: jobs.put_nowait(job)
    except queue.Full: pass


def project_observations(obs, shift, size):
    dx,dy=shift;w,h=size
    result={}
    for category in ('monsters','portals','obstacles'):
        items=[]
        for source in obs.get(category,[]):
            item=dict(source)
            item['x']=round(source['x']+dx);item['y']=round(source['y']+dy)
            if 'box' in source:
                x,y,bw,bh=source['box'];item['box']=[round(x+dx),round(y+dy),bw,bh]
            if category=='monsters' and not (0<=item['x']<w and 0<=item['y']<h):continue
            items.append(item)
        result[category]=items
    return result


class SemanticDetector:
    def __init__(self, engine_factory=VisionEngine):
        self.factory=engine_factory;self.jobs=queue.Queue(maxsize=1)
        self.result=None;self.status=dict(phase='initializing',started=time.monotonic(),error=None)
        self.quit=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True,name='polar-detector')
        self.thread.start()
    def submit(self, job):replace_latest(self.jobs,job)
    def run(self):
        try:engine=self.factory()
        except Exception as exc:
            self.status=dict(phase='failed',started=time.monotonic(),error=repr(exc))
            logging.exception('VISION detector initialization failed');return
        sequence=0;last_duration=None
        self.status=dict(phase='idle',started=time.monotonic(),error=None,duration=None)
        while not self.quit.is_set():
            try:job=self.jobs.get(timeout=.1)
            except queue.Empty:continue
            start=time.monotonic()
            self.status=dict(phase='analyzing',started=start,error=None,duration=last_duration)
            try:
                obs=engine.analyze(job['frame']);sequence+=1
                done=time.monotonic();last_duration=done-start
                self.result=dict(sequence=sequence,generation=job['generation'],captured=job['captured'],
                    completed=done,world=job['world'],motion_epoch=job['motion_epoch'],size=job['size'],obs=obs)
                self.status=dict(phase='idle',started=done,error=None,duration=last_duration)
            except Exception as exc:
                self.result=None
                self.status=dict(phase='error',started=time.monotonic(),error=repr(exc),duration=time.monotonic()-start)
                logging.exception('VISION detector analysis failed')
    def close(self):
        self.quit.set()
        # Let finite OpenCV calls finish before the interpreter tears down C++ state.
        self.thread.join()


class VisionWorker:
    def __init__(self, config, engine_factory=VisionEngine):
        self.config=config;self.jobs=queue.Queue(maxsize=1);self.commands=queue.SimpleQueue()
        self.snapshot=None;self.quit=threading.Event()
        self.status=dict(phase='initializing',started=time.monotonic(),error=None)
        self.detector=SemanticDetector(engine_factory)
        self.thread=threading.Thread(target=self.run,daemon=True,name='polar-motion')
        self.thread.start()
    def submit(self,job):replace_latest(self.jobs,job)
    def health(self):
        now=time.monotonic();s=self.snapshot;r=self.detector.result
        return dict(fast=dict(self.status,alive=self.thread.is_alive(),
                             capture_age=now-s['captured'] if s else None),
                    detector=dict(self.detector.status,alive=self.detector.thread.is_alive(),
                                  capture_age=now-r['captured'] if r else None))
    def run(self):
        try:
            navigator=Navigator(self.config.get('leash_px',1100));icons=BuffIcons()
            # Coin segmentation is cheap and independent of full-screen matching.
            coin_engine=VisionEngine()
        except Exception as exc:
            self.status=dict(phase='failed',started=time.monotonic(),error=repr(exc))
            logging.exception('VISION motion initialization failed');return
        generation=-1;sequence=0;motion_epoch=0;last_detector=-1e9
        planned=None;plan_world=None;plan_reason='waiting for detector';last_plan=-1e9;plan_mode=None
        while not self.quit.is_set():
            try:job=self.jobs.get(timeout=.1)
            except queue.Empty:continue
            started=time.monotonic()
            self.status=dict(phase='motion',started=started,error=None)
            try:
                if generation!=job['generation']:
                    navigator.reset();generation=job['generation'];motion_epoch+=1
                    last_detector=-1e9;planned=None;plan_world=None;last_plan=-1e9;plan_mode=None
                while not self.commands.empty():
                    kind,args=self.commands.get_nowait()
                    if kind=='blocked':navigator.mark_blocked(*args)
                frame=job['frame'];anchor=job['anchor'];size=frame.shape[1::-1]
                ground=(anchor[0],anchor[1]+self.config.get('player_feet_offset_px',43))
                tracker=navigator.tracker;previous=tracker.valid_updates;had_previous=tracker.prev is not None
                tracker.update(frame,anchor)
                if had_previous and tracker.valid_updates==previous:
                    motion_epoch+=1;tracker.world[:]=0
                    navigator.goal_world=None;navigator.last_pack_heading=None;navigator.search_world=[]
                    planned=None;plan_world=None;last_plan=-1e9
                world=tuple(tracker.world);now=time.monotonic()
                if now-last_detector>=self.config.get('detector_submit_seconds',.6):
                    self.detector.submit(dict(frame=frame,generation=generation,captured=job['captured'],
                        world=world,motion_epoch=motion_epoch,size=size))
                    last_detector=now
                raw=self.detector.result;obs=None;semantic_age=None
                if raw is None:
                    reason='waiting for full-screen detector'
                else:
                    semantic_age=now-raw['captured']
                    if raw['generation']!=generation or raw['size']!=size:reason='waiting for current window/anchor generation'
                    elif semantic_age>self.config.get('semantic_stale_seconds',4.):reason='detector too slow: semantic capture is stale'
                    elif raw['motion_epoch']!=motion_epoch:reason='camera track interrupted since detector capture'
                    else:
                        shift=np.array(raw['world'])-np.array(world)
                        obs=project_observations(raw['obs'],shift,size)
                        reason='camera motion uncertain' if not tracker.confident else 'ready'
                flags,scores,reliable=icons.read(frame)
                target=None
                if obs is not None and tracker.confident and job['move']:
                    mode=(job['escape'],bool(job['move']))
                    if now-last_plan>=.35 or mode!=plan_mode:
                        self.status=dict(phase='planning',started=time.monotonic(),error=None)
                        if job['escape'] is not None:
                            planned,plan_reason=navigator.escape_from_stuck(frame,ground,job['escape'],obs)
                        else:planned,plan_reason=navigator.choose(frame,ground,obs)
                        plan_world=np.array(world);last_plan=now;plan_mode=mode
                    if planned is not None:
                        projected=np.array(planned)+plan_world-np.array(world)
                        candidate=tuple(map(lambda x:int(round(x)),projected))
                        # Every reuse is checked against the CURRENT entire click corridor.
                        if (np.linalg.norm(np.array(candidate)-anchor)<=self.config.get('max_move_click_px',310)
                            and not navigator.is_blacklisted(candidate,ground)
                            and navigator.visible_floor(frame,ground,candidate,obs['portals'],obs['obstacles'])):
                            target=candidate
                        else:plan_reason='current-frame route veto; waiting for next plan'
                    reason=plan_reason
                else:
                    planned=None;plan_world=None;last_plan=-1e9
                    if not job['move'] and obs is not None:reason='movement off'
                sequence+=1;done=time.monotonic()
                self.snapshot=dict(sequence=sequence,generation=generation,captured=job['captured'],completed=done,size=size,
                    obs=obs,icons=flags,icon_scores=scores,hud_reliable=reliable,target=target,reason=reason,
                    coins=coin_engine.coin_candidates(frame,anchor),world=world,confident=tracker.confident,
                    valid=tracker.valid_updates,escape=job['escape'],anchor=anchor,semantic_age=semantic_age,
                    semantic_sequence=raw['sequence'] if raw is not None else None,health=self.health(),
                    motion_epoch=motion_epoch,ground_anchor=ground)
                self.status=dict(phase='idle',started=done,error=None,duration=done-started)
            except Exception as exc:
                self.snapshot=None;self.status=dict(phase='error',started=time.monotonic(),error=repr(exc))
                logging.exception('VISION motion/planning failure')
        self.status=dict(phase='stopped',started=time.monotonic(),error=None)
    def close(self):
        self.quit.set();self.detector.quit.set()
        self.thread.join();self.detector.close()
