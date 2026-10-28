# -*- coding: utf-8 -*-
"""Polar v21. Windows/AHK v1.1 runtime, deterministic policies in control_core.
Filename retained for existing launchers. Offline tests never inject input.
"""
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
import time
import traceback

import cv2
import numpy as np
from bridge import FileBridge, atomic_text
from control_core import Controller, CoinMemory, MoveEvidence
from perception import Calibration, BuffIcons, read_status
from vision_engine import VisionEngine, Navigator

ROOT=Path(__file__).resolve().parent
TITLE='Trickster Don Cavalier'
CONFIG=dict(max_mp=8500,mp_restore=4000,wheel_mp=550,wheel_interval=10.25,
            buff_fallback_seconds=285,movement_default=True,leash_px=1100,
            vision_stale_seconds=2.5,stuck_timeout=3.6,min_move_progress=12.,
            move_interval=3.2,coin_suppress_seconds=180,loot_enabled=True)
try: CONFIG.update(json.loads((ROOT/'config.json').read_text(encoding='utf-8')))
except FileNotFoundError: pass
cv2.setNumThreads(2)

class VisionWorker:
    """Owns CV/navigation state. Main input loop never waits for a CV lock."""
    def __init__(self):
        self.jobs=queue.Queue(maxsize=1)
        self.commands=queue.SimpleQueue()
        self.snapshot=None
        self.quit=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True,name='polar-vision')
        self.thread.start()
    def submit(self, job):
        try:self.jobs.get_nowait()
        except queue.Empty:pass
        try:self.jobs.put_nowait(job)
        except queue.Full:pass
    def run(self):
        engine=VisionEngine();icons=BuffIcons();navigator=Navigator(CONFIG['leash_px'])
        generation=-1;sequence=0
        while not self.quit.is_set():
            try:job=self.jobs.get(timeout=.2)
            except queue.Empty:continue
            try:
                if generation!=job['generation']:
                    navigator.reset();generation=job['generation']
                while not self.commands.empty():
                    kind, args=self.commands.get_nowait()
                    if kind=='blocked':navigator.mark_blocked(*args)
                frame=job['frame'];anchor=job['anchor']
                navigator.tracker.update(frame,anchor)
                obs=engine.analyze(frame)
                flags,scores,reliable=icons.read(frame)
                if job['escape'] is not None:
                    target,why=navigator.escape_from_stuck(frame,anchor,job['escape'],obs) if navigator.tracker.confident else (None,'camera uncertain')
                elif job['move']:
                    target,why=navigator.choose(frame,anchor,obs)
                else:target,why=None,'movement off'
                if target is not None and np.linalg.norm(np.array(target)-anchor)>CONFIG.get('max_move_click_px',310):
                    target=None;why='waypoint exceeds configured click distance'
                sequence+=1
                self.snapshot=dict(sequence=sequence,generation=generation,captured=job['captured'],size=frame.shape[1::-1],
                    obs=obs,icons=flags,icon_scores=scores,hud_reliable=reliable,target=target,reason=why,
                    coins=engine.coin_candidates(frame,anchor),world=tuple(navigator.tracker.world),
                    confident=navigator.tracker.confident,valid=navigator.tracker.valid_updates,
                    escape=job['escape'],anchor=anchor)
            except Exception as exc:
                logging.exception('VISION worker failure')
                self.snapshot=None
    def close(self):
        self.quit.set()
        self.thread.join(timeout=2.)

def configure_log():
    logger=logging.getLogger();logger.setLevel(logging.INFO)
    fmt=logging.Formatter('[%(asctime)s] %(message)s',datefmt='%H:%M:%S')
    handler=RotatingFileHandler(ROOT/'status.txt',maxBytes=2*1024*1024,backupCount=3,encoding='utf-8')
    handler.setFormatter(fmt);logger.addHandler(handler)
    stream=logging.StreamHandler();stream.setFormatter(fmt);logger.addHandler(stream)

def user32():
    if os.name!='nt':raise RuntimeError('Live mode requires Windows + AutoHotkey v1.1. Use TEST_OFFLINE.py on Linux.')
    u=ctypes.windll.user32
    try:u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError,OSError):u.SetProcessDPIAware()
    u.GetForegroundWindow.restype=wintypes.HWND
    u.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    u.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    u.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
    return u

class App:
    def __init__(self):
        self.u=user32()
        self.controller=Controller(CONFIG)
        self.bridge=FileBridge(ROOT);self.bridge.stop()
        self.calibration=Calibration(ROOT/'gauge_points.json')
        self.coins=CoinMemory(CONFIG['coin_suppress_seconds'])
        self.worker=VisionWorker()
        self.center=None;self.anchor_size=None;self.cal_points=[];self.cal_size=None
        self.move=CONFIG['movement_default'];self.exiting=False
        self.generation=0;self.frame_id=0;self.prev={};self.escape=None
        self.escape_failures=0;self.next_move=0.;self.last_submit=0.;self.last_coins=-1
        self.snapshot=None;self.available_coins=[];self.last_notice=0.;self.vision_missing_at=None
        self.last_hp=None;self.last_mp=None;self.region=None;self.pending_move_context=None
        self.bad_reads=0
        self.load_state()
        logging.info('v21 | F9 anchor | Ctrl+F9 x4 gauges | F10 run/pause | F8 movement | Ctrl+F7 diagnostics | F11 STOP | F12 exit')
        logging.info('F7 alone belongs to game buffs. AHK SendEvent retained. Supervised use only.')
    def region_now(self):
        hwnd=self.u.GetForegroundWindow();title=ctypes.create_unicode_buffer(512)
        self.u.GetWindowTextW(hwnd,title,512)
        if TITLE.casefold() not in title.value.casefold():return None
        r=wintypes.RECT();p=wintypes.POINT(0,0)
        if not self.u.GetClientRect(hwnd,ctypes.byref(r)):return None
        if not self.u.ClientToScreen(hwnd,ctypes.byref(p)):return None
        w,h=r.right-r.left,r.bottom-r.top
        if w<750 or h<480:return None
        return dict(left=p.x,top=p.y,width=w,height=h)
    def cursor_local(self,region):
        p=wintypes.POINT();self.u.GetCursorPos(ctypes.byref(p))
        return p.x-region['left'],p.y-region['top']
    def load_state(self):
        try:
            saved=json.loads((ROOT/'buff_state.json').read_text(encoding='utf-8'))
            if saved.get('version')!=2:return
            for i,t in enumerate(saved['last_sent_epoch'][:4]):
                if t is not None and 0<=time.time()-t<300:
                    self.controller.buffs.last_sent[i]=time.monotonic()-(time.time()-t)
        except (OSError,ValueError,TypeError,KeyError):pass
    def save_state(self):
        times=[None if t is None else time.time()-(time.monotonic()-t) for t in self.controller.buffs.last_sent]
        atomic_text(ROOT/'buff_state.json',json.dumps(dict(version=2,last_sent_epoch=times),indent=2))
    def stop(self,reason):
        self.bridge.stop();self.controller.stop(reason)
        self.coins.invalidate();self.available_coins=[];self.escape=None;self.snapshot=None
        self.generation+=1;self.vision_missing_at=None;self.region=None
    def hotkeys(self,region):
        ctrl=bool(self.u.GetAsyncKeyState(0x11)&0x8000)
        for name,vk in [('F7',0x76),('F8',0x77),('F9',0x78),('F10',0x79),('F11',0x7a),('F12',0x7b)]:
            pressed=bool(self.u.GetAsyncKeyState(vk)&0x8000)
            old=self.prev.get(name,False);self.prev[name]=pressed
            if not pressed or old:continue
            if name=='F12':self.stop('exit');self.exiting=True;continue
            if name=='F11':self.stop('emergency F11');self.move=False;continue
            if region is None:continue
            size=(region['width'],region['height'])
            if name=='F9' and ctrl:
                self.stop('gauge calibration')
                if self.cal_size!=size:self.cal_points=[];self.cal_size=size
                self.cal_points.append(self.cursor_local(region))
                logging.info('Gauge calibration %s/4: %s',len(self.cal_points),self.cal_points[-1])
                if len(self.cal_points)==4:
                    try:self.calibration.save(self.cal_points,size);logging.info('Gauge calibration saved for %s',size)
                    except ValueError:logging.warning('Invalid bar endpoints; repeat Ctrl+F9 x4')
                    self.cal_points=[]
            elif name=='F9':
                self.stop('anchor changed')
                point=self.cursor_local(region)
                if not (48<point[0]<size[0]-48 and 100<point[1]<size[1]-48):
                    self.center=None;logging.warning('Anchor outside usable game area; place cursor on character and press F9');continue
                self.center=point;self.anchor_size=size
                logging.info('Anchor %s, size %s; approximate motion tracker reset',self.center,size)
            elif name=='F8':
                self.move=not self.move
                self.controller.move_evidence=None;self.escape=None
                self.generation+=1;self.coins.invalidate()
                logging.info('Movement %s',self.move)
            elif name=='F10':
                if self.controller.running:self.stop('pause')
                elif not self.calibration.valid_for(size):logging.warning('START BLOCKED: calibrate Ctrl+F9 x4 for this client size')
                elif self.center is None or self.anchor_size!=size:logging.warning('START BLOCKED: F9 anchor for this client size')
                elif not self.bridge.ready():logging.warning('START BLOCKED: AHK v1.1 bridge heartbeat missing; launch START.bat')
                else:
                    self.bridge.begin();self.controller.start(time.monotonic());self.vision_missing_at=time.monotonic()
                    logging.info('RUN; preserved buff queue %s; movement %s',self.controller.buffs.queue,self.move)
            elif name=='F7' and ctrl:
                self.save_diagnostic=True
    def flush_events(self):
        for event in self.controller.events:
            logging.info('%s',json.dumps(event,ensure_ascii=False))
        self.controller.events.clear()
    def consume_snapshot(self,now,region):
        s=self.worker.snapshot
        if s is None or s['generation']!=self.generation or s['size']!=(region['width'],region['height']) or now-s['captured']>CONFIG['vision_stale_seconds']:
            self.snapshot=None;self.available_coins=[]
            self.coins.invalidate()
            return
        self.snapshot=s;self.vision_missing_at=None
        if s['sequence']!=self.last_coins:
            self.available_coins=self.coins.update(s['coins'],s['world'],s['confident'],s['sequence'],now)
            self.last_coins=s['sequence']
            # A failed odometry sample could hide a shift of an already-picked coin.
            if not s['confident']:self.coins.invalidate()
    def check_movement(self,now):
        rec=self.controller.move_evidence
        if rec is None:return
        s=self.snapshot
        outcome=rec.outcome(now,s['world'] if s else rec.world,s['valid'] if s else rec.valid,
                            bool(s and s['confident']),CONFIG['stuck_timeout'],CONFIG['min_move_progress'])
        if outcome=='pending':return
        self.controller.move_evidence=None
        logging.info('MOVE_RESULT %s target=%s recovery=%s',outcome,rec.target,rec.recovery)
        if outcome=='interrupted':
            # Casts/loot/focus/pause stop or change movement; not a blocked path.
            self.next_move=now+.4
            # An interrupted recovery has not succeeded. Retry at most twice.
            if rec.recovery:
                self.escape_failures+=1
                self.escape=rec.target
        elif outcome=='moved':
            self.escape=None;self.escape_failures=0;self.next_move=now+.2
        elif outcome=='unknown':
            self.move=False;self.escape=None
            logging.warning('Movement OFF: cannot verify camera motion; F8 to retry after inspection')
        else:
            self.worker.commands.put(('blocked',(rec.target,self.center,now)))
            self.escape_failures+=1
            self.escape=rec.target;self.next_move=now+.6
        if self.escape_failures>=2:
            self.move=False;self.escape=None
            logging.warning('Movement OFF: recovery not verified after two attempts; no repeated blind clicks')
    def acknowledge(self,now):
        action=self.controller.pending
        if action is None:return
        receipt=self.bridge.poll(action.token)
        if receipt is None:return
        status,wall_sent=receipt
        sent=now-max(0.,min(2.5,time.time()-wall_sent))
        self.controller.acknowledge(action.token,status,sent)
        if status=='SENT':
            if action.kind=='BUFF':self.save_state()
            elif action.kind=='MOVE':
                context=self.pending_move_context
                if context:
                    self.controller.move_evidence=MoveEvidence(sent,context['world'],context['valid'],action.point,recovery=context['recovery'])
                self.next_move=sent+CONFIG['move_interval']
            elif action.kind=='PICK':
                # Attempt was already recorded on publication, including lost ACKs.
                self.available_coins=[]
        self.pending_move_context=None
    def dispatch(self,action,region,now):
        if action.kind=='MOVE':
            s=self.snapshot
            self.pending_move_context=dict(world=s['world'],valid=s['valid'],recovery=self.escape is not None)
        elif action.kind=='PICK':
            self.coins.attempted(action.index,now)
            self.available_coins=[c for c in self.available_coins if c[0]!=action.index]
        self.bridge.send(action,(region['left'],region['top']))
    def diagnostic(self,frame):
        s=self.snapshot
        if s is None:return
        from regression_runner import annotate
        cv2.imwrite(str(ROOT/'vision_check.png'),annotate(frame,s['obs'],self.center,s['target'],s['reason']))
        (ROOT/'vision_scores.json').write_text(json.dumps(s,indent=2),encoding='utf-8')
        logging.info('Diagnostic saved (latest asynchronous result, age %.2fs)',time.monotonic()-s['captured'])
    def run(self):
        import mss
        self.save_diagnostic=False
        last_capture=0.;last_heartbeat=0.
        try:
            with mss.mss() as cap:
                while not self.exiting:
                    now=time.monotonic();region=self.region_now();self.hotkeys(region)
                    if not self.controller.running:
                        self.flush_events();time.sleep(.025);continue
                    if region is None:
                        self.stop('game focus lost; F10 required to resume');continue
                    size=(region['width'],region['height'])
                    if not self.calibration.valid_for(size) or self.anchor_size!=size:
                        self.stop('client resized; recalibrate gauges and F9 anchor');continue
                    # A moved window keeps local calibration but invalidates queued screen coordinates.
                    if self.region and (region['left'],region['top'])!=(self.region['left'],self.region['top']):
                        self.region=region;self.stop('window moved; F10 to resume with new screen offset');continue
                    self.region=region
                    if now-last_heartbeat>=.2:self.bridge.heartbeat();last_heartbeat=now
                    self.acknowledge(now)
                    if not self.controller.running:self.bridge.stop();continue
                    if now-last_capture<.10:time.sleep(.01);continue
                    last_capture=now
                    frame=np.array(cap.grab(region))[:,:,:3].copy();self.frame_id+=1
                    hp,mp=read_status(frame,self.calibration.points)
                    self.last_hp,self.last_mp=hp,mp
                    if hp is None or mp is None:
                        self.bad_reads+=1
                        if self.bad_reads>=5:self.stop('gauges unreadable; inspect calibration')
                    else:self.bad_reads=0
                    if now-self.last_submit>=.45:
                        self.worker.submit(dict(frame=frame,anchor=self.center,generation=self.generation,
                            captured=now,move=self.move,escape=self.escape))
                        self.last_submit=now
                    self.consume_snapshot(now,region)
                    s=self.snapshot
                    self.controller.observe(now,hp,mp,self.frame_id,s['icons'] if s else None,s['sequence'] if s else -1)
                    self.check_movement(now)
                    if s is None:
                        if self.vision_missing_at is None:self.vision_missing_at=now
                        if now-self.vision_missing_at>8.:
                            self.stop('vision unavailable/stale for 8 seconds');continue
                    attack=bool(s and s['obs']['monsters'])
                    # Stop blind repeated F3 on empty/stale screens; buffs and health still managed.
                    target=None
                    if self.move and s and s['confident'] and now>=self.next_move and not self.controller.move_evidence:
                        if s['escape']==self.escape:target=s['target']
                        if self.escape is not None and target is None:
                            self.move=False;self.escape=None
                            logging.warning('Movement OFF: no fully checked escape route')
                    coin=None
                    if CONFIG['loot_enabled'] and self.available_coins and self.escape is None:
                        coin=self.available_coins[0]
                    action=self.controller.choose(now,self.center,attack,coin,target)
                    if action:self.dispatch(action,region,now)
                    if not self.controller.running:self.bridge.stop()
                    if self.save_diagnostic and s:
                        # I/O only; CV and path planning remain in worker.
                        self.diagnostic(frame);self.save_diagnostic=False
                    if now-self.last_notice>=10.:
                        logging.info('HUD HP=%s MP=%s mobs=%s vision_age=%s move=%s reason=%s',hp,mp,
                            len(s['obs']['monsters']) if s else 'unknown',round(now-s['captured'],2) if s else None,
                            self.move,s['reason'] if s else 'stale/unknown')
                        self.last_notice=now
                    self.flush_events()
        finally:
            self.stop('runtime closed');self.worker.close();self.flush_events()

if __name__=='__main__':
    configure_log()
    from instance_lock import InstanceLock
    # A rejected second instance must never revoke the first one's lease.
    try: instance=InstanceLock(ROOT/'runtime.lock').acquire()
    except (RuntimeError,OSError) as exc:
        logging.error('%s',exc)
        raise SystemExit(1)
    try:
        App().run()
    except KeyboardInterrupt:logging.info('Keyboard stop')
    except Exception:
        FileBridge(ROOT).stop()
        logging.error('Runtime failed\n%s',traceback.format_exc())
        raise SystemExit(1)
    finally:instance.close()
