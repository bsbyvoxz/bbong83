import unittest
import cv2
import numpy as np
from control_core import Controller, CombatPresence
from perception import read_gauge_info
from vision_engine import Navigator, segment_hits_zone


def hud(mp=.28,hp=.8):
    frame=np.full((600,800,3),30,np.uint8)
    # The calibrated y=58 lies just below TWO always-blue outline rows.
    frame[56:58,20:420]=(220,60,20)
    frame[58:66,20:420]=(80,30,20)  # dark blue unfilled portion, not mana
    frame[58:66,20:20+round(400*mp)]=(220,100,30)
    frame[32:41,20:420]=(35,25,20)
    frame[32:41,20:20+round(400*hp)]=(15,20,230)
    return frame


class GaugeRegressionTests(unittest.TestCase):
    def test_two_full_border_rows_do_not_make_mp_full(self):
        info=read_gauge_info(hud(),[[20,57],[420,58]],'mp')
        self.assertAlmostEqual(info['value'],.28,delta=.01)
        self.assertGreaterEqual(info['rows'],3)
        self.assertGreaterEqual(info['row'],58)
    def test_dark_blue_background_is_not_filled_mp(self):
        info=read_gauge_info(hud(.2),[[20,61],[420,61]],'mp')
        self.assertAlmostEqual(info['value'],.2,delta=.01)
    def test_hp_does_not_mix_text_border_and_interior(self):
        self.assertAlmostEqual(read_gauge_info(hud(),[[20,37],[420,34]],'hp')['value'],.8,delta=.01)
    def test_confirmed_28_percent_mp_requests_f2(self):
        c=Controller({});c.start(100.)
        for seq in (1,2):
            frame=hud()
            hp=read_gauge_info(frame,[[20,37],[420,34]],'hp')['value']
            mp=read_gauge_info(frame,[[20,57],[420,58]],'mp')['value']
            c.observe(100.+seq*.1,hp,mp,seq)
        a=c.choose(100.3,(300,300),False)
        self.assertEqual((a.kind,a.key),('POTION','F2'))
        self.assertLess(a.baseline,.4)
    def test_wheel_cost_is_visible_even_with_unchanged_blue_outline(self):
        c=Controller({});c.start(100.);c.next_wheel=100.
        before=read_gauge_info(hud(.7),[[20,57],[420,58]],'mp')['value']
        c.observe(100.,.8,before,1);a=c.choose(100.,(300,300),True)
        c.acknowledge(a.token,'SENT',100.5)
        after=read_gauge_info(hud(.7-550/8500),[[20,57],[420,58]],'mp')['value']
        c.observe(101.,.8,after,2)
        self.assertEqual(c.unconfirmed_wheels,0)
        self.assertTrue(any(e.get('result')=='mp_drop_consistent' for e in c.events))
    def test_empty_mp_requires_real_outline_and_dark_interior(self):
        info=read_gauge_info(hud(0.),[[20,57],[420,58]],'mp')
        self.assertEqual(info['value'],0.)
        self.assertIsNone(read_gauge_info(np.zeros((600,800,3),np.uint8),[[20,57],[420,58]],'mp')['value'])

    def test_replay_uses_real_gauge_reader_for_mp_refill_buffs_and_wheel(self):
        c=Controller({});c.start(100.);memory=CombatPresence()
        mana=.28;pending=None;sent=[]
        for index in range(500):
            now=100.+index*.1
            if pending and now>=pending[0]:
                a=pending[1];pending=None
                if a.kind=='WHEEL':mana-=550/8500
                elif a.kind=='BUFF':mana-=[160,160,300,650][a.index]/8500
                elif a.kind=='POTION':mana=min(1.,mana+4000/8500)
                c.acknowledge(a.token,'SENT',now);sent.append(a.kind)
            frame=hud(mana)
            mp=read_gauge_info(frame,[[20,57],[420,58]],'mp')['value']
            c.observe(now,.8,mp,index)
            if index%15==0 and not (150<=index<190):memory.observe(index,[{}],now)
            action=c.choose(now,(300,300),memory.active(now))
            if action:pending=(now+.7,action)
            self.assertTrue(c.running,c.pause_reason)
        self.assertIn('POTION',sent);self.assertEqual(sent.count('BUFF'),4)
        self.assertGreaterEqual(sent.count('WHEEL'),3)


class CombatMemoryTests(unittest.TestCase):
    def test_slow_unknown_frame_does_not_starve_due_wheel(self):
        memory=CombatPresence();memory.observe(1,[dict(kind='ghost')],100.)
        self.assertTrue(memory.active(106.))
        c=Controller({});c.start(100.);c.buffs.queue=[];c.next_wheel=105.
        c.observe(106.,.8,.8,1)
        self.assertEqual(c.choose(106.,(300,300),memory.active(106.)).kind,'WHEEL')
    def test_three_distinct_empty_frames_clear_combat(self):
        m=CombatPresence();m.observe(1,[{}],100.)
        for seq in (2,3,4):m.observe(seq,[],100.+seq)
        self.assertFalse(m.active(105.))
    def test_repeated_cached_empty_frame_is_not_three_confirmations(self):
        m=CombatPresence();m.observe(1,[{}],100.)
        for _ in range(100):m.observe(2,[],101.)
        self.assertTrue(m.active(102.))
    def test_unknown_does_not_keep_combat_forever(self):
        m=CombatPresence();m.observe(1,[{}],100.)
        self.assertFalse(m.active(109.))


class RouteGeometryTests(unittest.TestCase):
    def test_bbox_does_not_create_a_phantom_giant_circle(self):
        nav=Navigator();frame=np.full((600,800,3),(120,65,50),np.uint8)
        zone=dict(x=320,y=140,radius=300,box=[300,100,40,80],padding=18,geometry='rectangle',kind='skull')
        self.assertFalse(segment_hits_zone((200,300),(550,300),zone))
        self.assertTrue(nav.visible_floor(frame,(200,300),(550,300),[],[zone]))
    def test_actual_rectangle_crossing_remains_blocked(self):
        zone=dict(x=350,y=300,radius=50,box=[330,260,40,80],padding=18,geometry='rectangle',kind='pillar')
        self.assertTrue(segment_hits_zone((200,300),(550,300),zone))
    def test_lit_textured_stone_floor_is_not_rejected_for_losing_blue_hue(self):
        rng=np.random.default_rng(6)
        gray=rng.integers(75,120,(600,800),dtype=np.uint8)
        frame=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
        self.assertTrue(Navigator().visible_floor(frame,(200,300),(550,300),[],[]))
        frame[270:330,350:370]=0
        self.assertFalse(Navigator().visible_floor(frame,(200,300),(550,300),[],[]))


if __name__=='__main__':unittest.main()
