from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
from control_core import Controller, CoinMemory, MoveEvidence
from polar_v18 import App, VisionWorker

CONFIG={'max_mp':8500,'wheel_mp':550,'wheel_interval':10.25}

class ReplayTests(unittest.TestCase):
    def test_ten_minute_replay_no_buff_storm_and_wheel_cadence(self):
        c=Controller(CONFIG);c.start(0.)
        mana=.95;clock=0.;sent=[];waiting=None
        for frame in range(6500):
            clock=frame/10
            if waiting and clock>=waiting[0]:
                _,a=waiting;waiting=None
                if a.kind=='WHEEL':mana-=550/8500
                elif a.kind=='BUFF':mana-=[160,160,300,650][a.index]/8500
                elif a.kind=='POTION':mana=min(1.,mana+4000/8500)
                c.acknowledge(a.token,'SENT',clock);sent.append((clock,a.kind,a.index))
            c.observe(clock,.9,mana,frame,[None]*4,frame//5)
            a=c.choose(clock,(800,450),True)
            if a:
                waiting=(clock+(.7 if a.kind=='BUFF' else .55),a)
            self.assertTrue(c.running,c.pause_reason)
        buffs=[row for row in sent if row[1]=='BUFF'];wheels=[row[0] for row in sent if row[1]=='WHEEL']
        self.assertEqual(len(buffs),12)
        self.assertGreater(len(wheels),55)
        self.assertTrue(all(10.25<=b-a<=11.5 for a,b in zip(wheels,wheels[1:])),wheels)
        for i in range(4):
            times=[t for t,kind,index in buffs if index==i]
            self.assertTrue(all(b-a>=285 for a,b in zip(times,times[1:])))
    def test_slow_cv_does_not_block_controller(self):
        entered=threading.Event();release=threading.Event()
        class Slow:
            def analyze(self,frame):
                entered.set();release.wait(2.)
                return dict(monsters=[],portals=[],obstacles=[])
            def coin_candidates(self,frame,anchor):return []
        with patch('polar_v18.VisionEngine',Slow):
            w=VisionWorker()
            try:
                w.submit(dict(frame=np.zeros((600,800,3),np.uint8),anchor=(400,300),generation=1,captured=time.monotonic(),move=False,escape=None))
                self.assertTrue(entered.wait(1.))
                c=Controller(CONFIG);c.start(100.);c.next_wheel=100.;c.observe(100.,.9,.9,1)
                start=time.monotonic();a=c.choose(100.,(400,300));elapsed=time.monotonic()-start
                self.assertEqual(a.kind,'WHEEL');self.assertLess(elapsed,.1)
            finally:release.set();w.close()
    def test_runtime_recovery_ack_is_not_success_without_motion(self):
        app=App.__new__(App);app.controller=Controller(CONFIG)
        app.controller.move_evidence=MoveEvidence(100.,(0,0),1,(400,300),recovery=True)
        app.snapshot=dict(world=(0,0),valid=5,confident=True)
        app.center=(300,300);app.escape_failures=1;app.move=True;app.escape=(400,300)
        import queue
        class Worker:commands=queue.SimpleQueue()
        app.worker=Worker();app.check_movement(104.)
        self.assertFalse(app.move);self.assertIsNone(app.escape)
    def test_runtime_unknown_motion_disables_movement(self):
        app=App.__new__(App);app.controller=Controller(CONFIG)
        app.controller.move_evidence=MoveEvidence(100.,(0,0),1,(400,300))
        app.snapshot=None;app.escape_failures=0;app.move=True;app.escape=None
        app.check_movement(104.);self.assertFalse(app.move)


class PersistentSafetyTests(unittest.TestCase):
    def test_visible_attempted_coin_never_expires_at_180_seconds(self):
        m=CoinMemory();coin=dict(x=300,y=400,confidence=.9)
        m.update([coin],(0,0),True,1,100.)
        found=m.update([coin],(0,0),True,2,101.);m.attempted(found[0][0],101.)
        for frame in range(3,600):
            self.assertEqual(m.update([coin],(0,0),True,frame,100.+frame),[])
        self.assertEqual(len(m.records),1)
    def test_missing_anchor_cannot_send_wheel_to_desktop_origin(self):
        c=Controller(CONFIG);c.start(100.);c.next_wheel=100.;c.observe(100.,.9,.9,1)
        self.assertIsNone(c.choose(100.,None))
    @unittest.skipIf(__import__('os').name=='nt','Windows uses an OS named mutex, requires a second process')
    def test_second_controller_cannot_take_runtime_lock(self):
        from instance_lock import InstanceLock
        with tempfile.TemporaryDirectory() as d:
            first=InstanceLock(Path(d)/'runtime.lock').acquire()
            try:
                with self.assertRaises(RuntimeError):InstanceLock(Path(d)/'runtime.lock').acquire()
            finally:first.close()

if __name__=='__main__':unittest.main()
