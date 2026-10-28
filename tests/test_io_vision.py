import json
from pathlib import Path
import tempfile
import unittest
import cv2
import numpy as np
from bridge import FileBridge, atomic_text
from control_core import Action
from perception import Calibration, BuffIcons, read_status
from vision_engine import Navigator, BackgroundTracker, VisionEngine

ROOT=Path(__file__).resolve().parents[1]

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.b=FileBridge(self.temp.name,wall_clock=lambda:1000.);self.b.begin()
    def test_priority_filenames_put_wheel_before_other_inputs(self):
        for i,kind in enumerate(['MOVE','BUFF','PICK','POTION','WHEEL']):
            self.b.send(Action(kind,key='F1',token=str(i),point=(20,30)))
        kinds=[p.read_text().split()[3] for p in sorted(self.b.queue.glob('*.cmd'))]
        self.assertEqual(kinds,['WHEEL','POTION','BUFF','PICK','MOVE'])
    def test_atomic_composite_contains_slot_sensitive_action_and_expiry(self):
        self.b.send(Action('WHEEL',token='1',point=(20,30)),(100,200))
        parts=next(self.b.queue.glob('*.cmd')).read_text().split()
        self.assertEqual(parts,['1',self.b.session,'1001500','WHEEL','-','120','230'])
        self.assertEqual(list(self.b.queue.glob('*.tmp')),[])
    def test_stop_revokes_lease_and_discards_old_queue(self):
        self.b.send(Action('PICK',token='1'));old=self.b.session;self.b.stop()
        self.assertEqual((self.b.root/'bridge_lease.txt').read_text(),'STOP 0\n')
        self.assertEqual(list(self.b.queue.glob('*.cmd')),[])
        self.b.begin();self.assertNotEqual(self.b.session,old)
    def test_receipts_are_session_scoped(self):
        atomic_text(self.b.acks/'old_1.ack','1 SENT 1000000')
        self.assertIsNone(self.b.poll('1'))
        atomic_text(self.b.acks/f'{self.b.session}_1.ack','1 SENT 1000000')
        self.assertEqual(self.b.poll('1'),('SENT',1000.));self.assertIsNone(self.b.poll('1'))
    def test_no_bridge_heartbeat_is_not_ready(self):
        self.assertFalse(self.b.ready());atomic_text(self.b.root/'bridge_ready.txt','1000000');self.assertTrue(self.b.ready())
    def test_ahk_uses_supported_sender_and_has_no_double_click_or_queue_drain(self):
        # Static contract check only; this does not execute the AHK interpreter.
        text=(ROOT/'GAME_INPUT_AHKv1.ahk').read_text()
        self.assertIn('SendEvent,',text);self.assertNotIn('SendInput,',text)
        self.assertNotIn('CLICK2',text);self.assertIn('~F11::',text)
        self.assertIn('status := "SENT"',text);self.assertIn('EpochMs() < expires',text)
        self.assertNotIn('pyautogui',(ROOT/'polar_v18.py').read_text())

class CalibrationTests(unittest.TestCase):
    def test_persistence_and_resize_invalidation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'gauge_points.json';c=Calibration(p)
            c.save([[5,10],[205,10],[5,30],[205,30]],(800,600))
            restored=Calibration(p)
            self.assertTrue(restored.valid_for((800,600)));self.assertFalse(restored.valid_for((1024,768)))
    def test_legacy_without_client_size_is_not_silently_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'gauge_points.json';p.write_text('[[5,10],[205,10],[5,30],[205,30]]')
            self.assertFalse(Calibration(p).valid_for((800,600)))
    def test_invalid_geometry_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):Calibration(Path(d)/'x.json').save([[5,10],[10,10],[5,30],[205,30]],(800,600))
    def test_gauge_reads_actual_colored_fraction(self):
        frame=np.full((600,800,3),35,np.uint8)
        frame[8:13,5:105]=(0,0,220);frame[28:33,5:85]=(220,70,20)
        hp,mp=read_status(frame,[[5,10],[205,10],[5,30],[205,30]])
        self.assertAlmostEqual(hp,.5,delta=.015);self.assertAlmostEqual(mp,.4,delta=.015)
    def test_blank_capture_is_unknown(self):
        self.assertEqual(read_status(np.zeros((600,800,3),np.uint8),[[5,10],[205,10],[5,30],[205,30]]),(None,None))

class IconTests(unittest.TestCase):
    def test_empty_frame_is_unknown_not_four_missing(self):
        flags,_,reliable=BuffIcons().read(np.zeros((900,1600,3),np.uint8))
        self.assertEqual(flags,[None]*4);self.assertFalse(reliable)
    def test_provided_buff_row_is_recognized_at_different_offsets(self):
        crop=cv2.imread(str(ROOT/'references/buff_icons.png'))
        for y in (30,75):
            frame=np.zeros((900,1600,3),np.uint8);frame[y:y+crop.shape[0],5:5+crop.shape[1]]=crop
            flags,scores,reliable=BuffIcons().read(frame)
            self.assertEqual(flags,[True]*4,(scores,y));self.assertTrue(reliable)
    def test_window_width_does_not_scale_icons(self):
        crop=cv2.imread(str(ROOT/'references/buff_icons.png'))
        frame=np.zeros((900,1000,3),np.uint8);frame[50:110,5:144]=crop
        self.assertEqual(BuffIcons().read(frame)[0],[True]*4)

class PathTests(unittest.TestCase):
    def setUp(self):
        self.frame=np.full((600,800,3),(120,65,50),np.uint8);self.nav=Navigator()
    def test_known_floor_route_passes(self):
        self.assertTrue(self.nav.visible_floor(self.frame,(200,300),(550,300),[],[]))
    def test_narrow_void_near_start_vetoes_whole_path(self):
        self.frame[270:330,208:219]=0
        self.assertFalse(self.nav.visible_floor(self.frame,(200,300),(550,300),[],[]))
    def test_portal_on_segment_vetoes_even_with_safe_destination(self):
        portal=dict(x=350,y=300,radius=35)
        self.assertFalse(self.nav.visible_floor(self.frame,(200,300),(550,300),[portal],[]))
    def test_escape_cannot_use_unsafe_first_half(self):
        # A black start cannot be bypassed via the old last-half exception.
        self.frame[260:340,160:240]=0
        p,_=self.nav.escape_from_stuck(self.frame,(200,300),(400,300),{})
        self.assertIsNone(p)
    def test_unsupported_odometry_blocks_exploration(self):
        target,_=self.nav.choose(self.frame,(300,300),dict(monsters=[],portals=[],obstacles=[]))
        self.assertIsNone(target)
    def test_more_distant_pack_beats_near_two(self):
        self.nav.tracker.confident=True
        monsters=[dict(x=x,y=y,kind='turtle',confidence=.9) for x,y in [(290,295),(310,310),(610,240),(640,260),(660,270),(670,290),(650,300)]]
        target,reason=self.nav.choose(self.frame,(300,300),dict(monsters=monsters,portals=[],obstacles=[]))
        self.assertIsNotNone(target);self.assertIn('pack 5',reason)
    def test_all_dark_frame_cannot_produce_mobs(self):
        self.assertEqual(VisionEngine().analyze(np.zeros((600,800,3),np.uint8))['monsters'],[])
    def test_textured_translation_exercises_real_optical_flow(self):
        rng=np.random.default_rng(7);frame=rng.integers(40,220,(600,800,3),dtype=np.uint8)
        tracker=BackgroundTracker()
        for n in range(5):
            moved=cv2.warpAffine(frame,np.float32([[1,0,n*4],[0,1,n*2]]),(800,600))
            tracker.update(moved,(400,300))
        self.assertTrue(tracker.confident);self.assertAlmostEqual(tracker.world[0],-16.,delta=2.)
        tracker.update(np.zeros_like(frame),(400,300));self.assertFalse(tracker.confident)

if __name__=='__main__':unittest.main()
