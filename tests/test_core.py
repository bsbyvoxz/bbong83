import unittest
from control_core import Controller, BuffPlan, GaugeGate, CoinMemory, MoveEvidence, Action

CONFIG={'max_mp':8500,'wheel_mp':550,'wheel_interval':10.25}

def ready(now=100.):
    c=Controller(CONFIG);c.start(now)
    c.observe(now,.9,.9,1)
    return c

class SchedulerTests(unittest.TestCase):
    def test_due_wheel_preempts_buff_potion_loot_and_move(self):
        c=ready();c.next_wheel=100.
        c.observe(100.1,.4,.9,2);c.observe(100.2,.4,.9,3)
        a=c.choose(100.2,(300,300),True,(1,{}),(500,300))
        self.assertEqual(a.kind,'WHEEL');self.assertEqual(c.buffs.queue,[0,1,2,3])
    def test_buff_queue_pops_only_after_sent(self):
        c=ready();a=c.choose(100.,(300,300))
        self.assertEqual(a.kind,'BUFF');self.assertEqual(c.buffs.queue,[0,1,2,3])
        c.acknowledge(a.token,'SENT',100.7)
        self.assertEqual(c.buffs.queue,[1,2,3])
    def test_startup_chain_order_and_pointer_after_wheel(self):
        c=ready();clock=100.
        for i in range(4):
            if i==2:
                c.next_wheel=clock
                a=c.choose(clock,(300,300));self.assertEqual(a.kind,'WHEEL')
                c.acknowledge(a.token,'SENT',clock+.5)
                c.observe(clock+.9,.9,.835,10)
                clock+=1.5
            a=c.choose(clock,(300,300))
            self.assertEqual((a.kind,a.index,a.key),('BUFF',i,['F1','F2','F6','F7'][i]))
            c.acknowledge(a.token,'SENT',clock+.7);clock+=1.6
        self.assertEqual(c.buffs.queue,[])
    def test_due_tick_never_issues_more_than_one_command(self):
        c=ready();c.next_wheel=100.
        a=c.choose(100.,(1,1));self.assertIsNotNone(a)
        self.assertIsNone(c.choose(100.1,(1,1)))
    def test_ack_timeout_stops_without_retry(self):
        c=ready();c.next_wheel=100.;c.choose(100.,(1,1))
        self.assertIsNone(c.choose(103.,(1,1)));self.assertFalse(c.running)
    def test_unknown_receipt_cannot_pop_buff(self):
        c=ready();a=c.choose(100.,(1,1));c.acknowledge('wrong','SENT',100.7)
        self.assertEqual(c.buffs.queue,[0,1,2,3]);self.assertIsNotNone(c.pending)
    def test_rejected_receipt_stops_and_retains_pointer(self):
        c=ready();a=c.choose(100.,(1,1));c.acknowledge(a.token,'STOPPED',100.7)
        self.assertFalse(c.running);self.assertEqual(c.buffs.queue,[0,1,2,3])
    def test_pause_resume_does_not_reset_cooldown_or_buffs(self):
        c=ready();c.next_wheel=100.;a=c.choose(100.,(1,1));c.acknowledge(a.token,'SENT',100.5)
        due=c.next_wheel;c.stop();c.start(102.)
        self.assertEqual(c.next_wheel,due);self.assertEqual(c.buffs.queue,[0,1,2,3])
    def test_pause_mid_buff_does_not_immediately_duplicate(self):
        c=ready();a=c.choose(100.,(1,1));c.stop();c.start(101.)
        c.observe(101.,.9,.9,2)
        self.assertIsNone(c.choose(101.,(1,1),False))
    def test_missing_mp_drop_does_not_retry_or_reset_due(self):
        c=ready();c.next_wheel=100.;a=c.choose(100.,(1,1));c.acknowledge(a.token,'SENT',100.5)
        due=c.next_wheel;c.observe(102.2,.9,.9,2)
        self.assertEqual(c.next_wheel,due)
        self.assertTrue(any(e.get('result')=='unconfirmed_no_retry' for e in c.events))
    def test_expected_mp_drop_is_consistency_evidence(self):
        c=ready();c.next_wheel=100.;a=c.choose(100.,(1,1));c.acknowledge(a.token,'SENT',100.5)
        c.observe(101.,.9,.835,2)
        self.assertTrue(any(e.get('result')=='mp_drop_consistent' for e in c.events))
    def test_mana_interference_is_unknown_not_failure(self):
        c=ready();c.next_wheel=100.;a=c.choose(100.,(1,1));c.acknowledge(a.token,'SENT',100.5)
        c.last_mana_action=100.8;c.observe(101.,.9,1.,2)
        self.assertTrue(any(e.get('result')=='unknown_mana_interference' for e in c.events))
        self.assertEqual(c.unconfirmed_wheels,0)
    def test_three_unconfirmed_wheels_stop(self):
        c=ready();c.buffs.queue=[]
        for tick in [100.,111.,122.]:
            c.next_wheel=tick;a=c.choose(tick,(1,1));self.assertEqual(a.kind,'WHEEL')
            c.acknowledge(a.token,'SENT',tick+.5);c.observe(tick+2.2,.9,.9,int(tick))
        self.assertFalse(c.running)
    def test_empty_scene_does_not_repeat_wheel(self):
        c=ready();c.next_wheel=100.;c.buffs.queue=[]
        self.assertIsNone(c.choose(200.,(1,1),False))
    def test_mana_refill_preserves_pending_buff(self):
        c=ready();c.observe(100.1,.9,.2,2);c.observe(100.2,.9,.2,3)
        a=c.choose(100.2,(1,1));self.assertEqual(a.kind,'POTION')
        c.acknowledge(a.token,'SENT',100.8);c.observe(101.2,.9,.67,4)
        self.assertEqual(c.buffs.queue,[0,1,2,3])
        a=c.choose(101.3,(1,1));self.assertEqual((a.kind,a.index),('BUFF',0))
    def test_mana_recovery_pending_holds_attack(self):
        c=ready();c.next_wheel=100.;c.mp.waiting=(99.8,.2)
        self.assertIsNone(c.choose(100.,(1,1)))
    def test_missing_gauge_blocks_every_action(self):
        c=ready();c.observe(100.1,None,.9,2);c.next_wheel=100.
        self.assertIsNone(c.choose(101.,(1,1)))

class BuffTests(unittest.TestCase):
    def cast_all(self):
        b=BuffPlan();b.start(100.)
        for i in range(4):b.sent(i,100.)
        return b
    def test_all_false_never_seen_does_not_spam(self):
        b=self.cast_all()
        for frame in range(400):b.observe([False]*4,frame,101+frame*.5)
        self.assertEqual(b.queue,[])
    def test_unknown_resets_disappearance_evidence(self):
        b=self.cast_all();b.observe([True]*4,1,110.);b.observe([True]*4,2,111.)
        for f in range(3,10):b.observe([False]*4,f,140+f)
        b.observe([None]*4,10,150.);b.observe([False]*4,11,151.)
        self.assertEqual(b.queue,[])
    def test_confirmed_loss_refreshes_only_missing(self):
        b=self.cast_all()
        b.observe([True]*4,1,101.);b.observe([True]*4,2,102.)
        for f in range(3,11):b.observe([True,False,True,True],f,140+f)
        self.assertEqual(b.queue,[1])
    def test_cached_frame_cannot_accumulate_false_evidence(self):
        b=self.cast_all();b.observe([True]*4,1,101.);b.observe([True]*4,2,102.)
        for _ in range(100):b.observe([False]*4,3,140.)
        self.assertEqual(b.queue,[])
    def test_unknown_falls_back_at_285_seconds(self):
        b=self.cast_all();b.observe([None]*4,1,384.9);self.assertEqual(b.queue,[])
        b.observe([None]*4,2,385.);self.assertEqual(b.queue,[0,1,2,3])
    def test_recent_seen_loss_does_not_recast_under_30_seconds(self):
        b=self.cast_all();b.observe([True]*4,1,101.);b.observe([True]*4,2,102.)
        for f in range(3,20):b.observe([False]*4,f,103+f)
        self.assertEqual(b.queue,[])
    def test_restored_recent_cast_is_not_startup_repeated(self):
        b=BuffPlan();b.last_sent=[90.,None,90.,90.];b.start(100.)
        self.assertEqual(b.queue,[1])

class GaugeTests(unittest.TestCase):
    def test_hp_inclusive_and_mp_strict_boundaries(self):
        hp=GaugeGate(.5,True);mp=GaugeGate(.4,False)
        for f in range(2):hp.observe(.5,f,100.);mp.observe(.4,f,100.)
        self.assertTrue(hp.ready(100.));self.assertFalse(mp.ready(100.))
    def test_one_low_frame_is_not_a_potion(self):
        g=GaugeGate(.4,False);g.observe(.39,1,100.);self.assertFalse(g.ready(100.))
    def test_same_frame_is_not_confirmation(self):
        g=GaugeGate(.4,False)
        for _ in range(10):g.observe(.2,1,100.)
        self.assertFalse(g.ready(100.))
    def test_recovery_clears_low_streak_no_extra_potion(self):
        g=GaugeGate(.4,False)
        for f in (1,2):g.observe(.2,f,100.)
        g.sent(100.,.2);self.assertFalse(g.ready(100.1))
        self.assertEqual(g.observe(.67,3,100.5),'recovery_observed')
        self.assertFalse(g.ready(101.))
    def test_unconfirmed_recovery_does_not_repeat_forever(self):
        g=GaugeGate(.4,False);g.sent(100.,.2)
        self.assertEqual(g.observe(.2,3,102.1),'recovery_unconfirmed');self.assertFalse(g.ready(102.2))
    def test_values_above_threshold_do_not_trigger(self):
        c=ready();c.buffs.queue=[]
        for f in (2,3,4):c.observe(100.1,.55,.49,f)
        self.assertIsNone(c.choose(100.2,(1,1),False))

class CoinTests(unittest.TestCase):
    def coin(self,x=300,y=400):return dict(x=x,y=y,confidence=.9)
    def test_one_attempt_survives_camera_translation(self):
        m=CoinMemory();self.assertEqual(m.update([self.coin()],(0,0),True,1,100.),[])
        found=m.update([self.coin()],(0,0),True,2,101.);self.assertEqual(len(found),1)
        m.attempted(found[0][0],101.)
        for i in range(3,30):
            self.assertEqual(m.update([self.coin(300-i*5)],(i*5,0),True,i,100+i),[])
    def test_disappearance_does_not_clear_attempt(self):
        m=CoinMemory();m.update([self.coin()],(0,0),True,1,100.)
        found=m.update([self.coin()],(0,0),True,2,101.);m.attempted(found[0][0],101.)
        m.update([],(0,0),True,3,102.)
        self.assertEqual(m.update([self.coin()],(0,0),True,4,103.),[])
        self.assertEqual(m.update([self.coin()],(0,0),True,5,104.),[])
    def test_tracking_gap_holds_reidentified_coin(self):
        m=CoinMemory();m.update([self.coin()],(0,0),True,1,100.)
        found=m.update([self.coin()],(0,0),True,2,101.);m.attempted(found[0][0],101.)
        m.update([],(0,0),False,3,102.)
        for f in (4,5):self.assertEqual(m.update([self.coin(100)],(0,0),True,f,103.),[])
    def test_cached_frame_cannot_confirm_coin(self):
        m=CoinMemory()
        for _ in range(10):self.assertEqual(m.update([self.coin()],(0,0),True,1,100.),[])
    def test_different_distant_coin_can_be_picked(self):
        m=CoinMemory();m.update([self.coin()],(0,0),True,1,100.)
        first=m.update([self.coin()],(0,0),True,2,101.);m.attempted(first[0][0],101.)
        m.update([self.coin(420)],(0,0),True,3,102.)
        self.assertEqual(len(m.update([self.coin(420)],(0,0),True,4,103.)),1)

class MovementTests(unittest.TestCase):
    def test_no_stuck_before_command_timeout(self):
        r=MoveEvidence(100.,(0,0),1,(300,300));self.assertEqual(r.outcome(102.,(0,0),4,True),'pending')
    def test_cast_interruption_is_not_stuck(self):
        r=MoveEvidence(100.,(0,0),1,(300,300),interrupted=True)
        self.assertEqual(r.outcome(104.,(0,0),4,True),'interrupted')
    def test_bad_motion_is_unknown(self):
        r=MoveEvidence(100.,(0,0),1,(300,300));self.assertEqual(r.outcome(104.,(0,0),1,False),'unknown')
    def test_recovery_must_show_displacement(self):
        r=MoveEvidence(100.,(0,0),1,(300,300),recovery=True)
        self.assertEqual(r.outcome(104.,(0,0),4,True),'stuck')
        self.assertEqual(r.outcome(104.,(20,0),4,True),'moved')

if __name__=='__main__':unittest.main()
