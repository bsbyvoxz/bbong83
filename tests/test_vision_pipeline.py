from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np

from control_core import Controller, CoinMemory, MoveEvidence
from polar_v18 import App
from vision_worker import VisionWorker, project_observations

CFG=dict(leash_px=1100,max_move_click_px=310,semantic_stale_seconds=4.,detector_submit_seconds=.2)


def scene():
    # Actual textured blue ground for real optical flow and corridor validation.
    rng=np.random.default_rng(23)
    return np.clip(np.array([120,65,50])+rng.integers(-25,26,(600,800,3)),0,255).astype('uint8')


def job(frame,generation=1,move=True):
    return dict(frame=frame,anchor=(300,300),generation=generation,captured=time.monotonic(),move=move,escape=None)


class ProjectionTests(unittest.TestCase):
    def test_camera_projection_moves_boxes_and_keeps_original(self):
        source=dict(monsters=[dict(x=100,y=100,box=[80,60,40,40],kind='ghost',confidence=.9)],
                    portals=[dict(x=-20,y=100,radius=60,box=[-40,60,40,40])],obstacles=[])
        projected=project_observations(source,(10,-5),(800,600))
        self.assertEqual(projected['monsters'][0]['box'],[90,55,40,40])
        self.assertEqual(source['monsters'][0]['box'],[80,60,40,40])
        self.assertEqual(len(projected['portals']),1)
    def test_offscreen_monsters_are_discarded(self):
        self.assertEqual(project_observations(dict(monsters=[dict(x=4,y=50)],portals=[],obstacles=[]),(-20,0),(800,600))['monsters'],[])


class LivePipelineTests(unittest.TestCase):
    def start_worker(self,factory):
        worker=VisionWorker(CFG,engine_factory=factory)
        self.addCleanup(worker.close)
        return worker
    def test_blocked_detector_does_not_block_motion_publication(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        class Slow:
            def analyze(self,frame):
                entered.set();release.wait(3.)
                return dict(monsters=[],portals=[],obstacles=[])
        worker=self.start_worker(Slow);frame=scene();worker.submit(job(frame))
        try:
            self.assertTrue(entered.wait(1.))
            deadline=time.monotonic()+1.5
            while time.monotonic()<deadline:
                worker.submit(job(frame));time.sleep(.04)
                if worker.snapshot and worker.snapshot['sequence']>=5:break
            self.assertIsNotNone(worker.snapshot)
            self.assertGreaterEqual(worker.snapshot['sequence'],5)
            self.assertIsNone(worker.snapshot['obs'])
            self.assertLess(time.monotonic()-worker.snapshot['captured'],.4)
            self.assertTrue(worker.snapshot['confident'])
            self.assertEqual(worker.health()['detector']['phase'],'analyzing')
        finally:release.set()
    def test_fresh_path_reaches_real_runtime_move_dispatch_after_slow_detection(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        class Slow:
            def analyze(self,frame):
                entered.set();release.wait(3.)
                return dict(monsters=[dict(x=x,y=y,kind='turtle',confidence=.9,box=[x-20,y-50,40,50])
                                      for x,y in [(610,240),(640,260),(660,280),(630,300)]],portals=[],obstacles=[])
        worker=self.start_worker(Slow);frame=scene()
        frame[275:325,275:325]=(10,180,240)  # avatar body is NOT floor
        worker.submit(job(frame))
        try:
            self.assertTrue(entered.wait(1.))
            for _ in range(6):worker.submit(job(frame));time.sleep(.05)
            release.set()
            deadline=time.monotonic()+2.
            while time.monotonic()<deadline:
                worker.submit(job(frame));time.sleep(.04)
                if worker.snapshot and worker.snapshot['target']:break
            self.assertIsNotNone(worker.snapshot['target'],worker.snapshot['reason'])
            app=App.__new__(App);app.worker=worker;app.generation=1
            app.snapshot=None;app.coins=CoinMemory();app.last_coins=-1;app.available_coins=[]
            app.move=True;app.next_move=0.;app.escape=None;app.center=(300,300)
            app.controller=Controller({});now=time.monotonic();app.controller.start(now)
            app.controller.buffs.queue=[];app.controller.next_wheel=now+10.
            app.controller.observe(now,.9,.9,1)
            app.consume_snapshot(now,dict(width=800,height=600))
            target=app.movement_target(now);self.assertIsNotNone(target)
            action=app.controller.choose(now,app.center,True,None,target)
            self.assertEqual(action.kind,'MOVE')
            sent=[]
            class Bridge:
                def send(self,action,offset):sent.append((action.kind,action.point,offset))
            app.bridge=Bridge();app.pending_move_context=None
            app.dispatch(action,dict(left=100,top=200),now)
            self.assertEqual(sent,[('MOVE',target,(100,200))])
            self.assertEqual(app.pending_move_context['world'],worker.snapshot['world'])
        finally:release.set()
    def test_detector_initialization_failure_is_reported(self):
        def broken():raise RuntimeError('fixture missing template dependency')
        with self.assertLogs(level='ERROR'):
            worker=self.start_worker(broken)
            worker.detector.thread.join(timeout=1.)
        self.assertEqual(worker.health()['detector']['phase'],'failed')
        self.assertIn('fixture missing',worker.health()['detector']['error'])
        self.assertFalse(worker.health()['detector']['alive'])
    def test_close_finishes_busy_opencv_worker_before_return(self):
        entered=threading.Event();release=threading.Event()
        class Slow:
            def analyze(self,frame):
                entered.set();release.wait(1.)
                return dict(monsters=[],portals=[],obstacles=[])
        worker=self.start_worker(Slow);worker.submit(job(scene()))
        self.assertTrue(entered.wait(1.))
        timer=threading.Timer(.1,release.set);timer.start()
        try:
            worker.close()
            self.assertFalse(worker.thread.is_alive())
            self.assertFalse(worker.detector.thread.is_alive())
        finally:release.set();timer.join()
    def test_old_semantics_are_not_made_fresh_by_new_motion_frames(self):
        class Detector:
            def analyze(self,frame):return dict(monsters=[],portals=[],obstacles=[])
        worker=self.start_worker(Detector)
        worker.detector.quit.set();worker.detector.thread.join(timeout=1.)
        # A stationary track starts in epoch 1; this is a truly old result.
        worker.detector.result=dict(sequence=1,generation=1,captured=time.monotonic()-5.,completed=time.monotonic(),
            world=(0,0),motion_epoch=1,size=(800,600),obs=dict(monsters=[],portals=[],obstacles=[]))
        worker.submit(job(scene()))
        deadline=time.monotonic()+1.
        while worker.snapshot is None and time.monotonic()<deadline:time.sleep(.02)
        self.assertIsNone(worker.snapshot['obs'])
        self.assertIn('stale',worker.snapshot['reason'])
        self.assertGreater(worker.snapshot['semantic_age'],4.)


class RuntimeHealthTests(unittest.TestCase):
    def app(self):
        app=App.__new__(App)
        class Worker:
            def health(self):return dict(fast=dict(alive=True),detector=dict(alive=True))
        app.worker=Worker();app.vision_missing_at=100.;app.vision_reason='waiting for detector'
        return app
    def test_fast_publications_do_not_reset_missing_semantic_timeout(self):
        app=self.app();app.snapshot=dict(obs=None)
        self.assertIsNone(app.vision_failure(104.))
        self.assertIn('8 seconds',app.vision_failure(109.))
    def test_valid_semantics_reset_timeout(self):
        app=self.app();app.snapshot=dict(obs=dict(monsters=[],portals=[],obstacles=[]))
        self.assertIsNone(app.vision_failure(109.));self.assertIsNone(app.vision_missing_at)
    def test_camera_gap_cannot_be_reported_as_successful_move(self):
        rec=MoveEvidence(100.,(0,0),1,(500,300),motion_epoch=2)
        self.assertEqual(rec.outcome(104.,(100,0),10,True,current_epoch=3),'unknown')
    def test_dead_worker_has_precise_failure(self):
        app=self.app()
        app.worker.health=lambda:dict(fast=dict(alive=False,error='bad capture'),detector=dict(alive=True))
        self.assertIn('fast stopped: bad capture',app.vision_failure(101.))
    def test_unknown_scene_cannot_issue_move(self):
        app=self.app();app.snapshot=dict(obs=None,confident=True,target=(500,300));app.move=True
        self.assertIsNone(app.movement_target(100.))

    def test_manual_resume_requires_new_perception_generation(self):
        app=self.app()
        class U:
            def GetAsyncKeyState(self,vk):return 0x8000 if vk==0x79 else 0
        class Calibration:
            def valid_for(self,size):return size==(800,600)
        class Bridge:
            def ready(self):return True
            def begin(self):pass
        app.u=U();app.prev={};app.move=True;app.generation=7;app.snapshot={}
        app.center=(300,300);app.anchor_size=(800,600);app.coins=CoinMemory()
        app.calibration=Calibration();app.bridge=Bridge();app.controller=Controller({})
        app.hotkeys(dict(width=800,height=600))
        self.assertTrue(app.controller.running);self.assertEqual(app.generation,8)
        self.assertIsNone(app.snapshot)
    def test_f8_does_not_discard_a_good_camera_track(self):
        app=self.app()
        class U:
            def GetAsyncKeyState(self,vk):return 0x8000 if vk==0x77 else 0
        app.u=U();app.prev={};app.move=False;app.generation=7
        app.controller=Controller({});app.escape=(400,300)
        app.hotkeys(dict(width=800,height=600))
        self.assertTrue(app.move);self.assertEqual(app.generation,7)
        self.assertIsNone(app.escape)


if __name__=='__main__':unittest.main()
