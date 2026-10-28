"""Deterministic v21 policies. No Windows, capture, sleeps or file I/O here.

All clocks passed to this module are monotonic seconds. SENT means an AHK
SendEvent completed, never that the server accepted a skill.
"""
from dataclasses import dataclass, field
from math import hypot

BUFFS = [('F1', 160), ('F2', 160), ('F6', 300), ('F7', 650)]
PRIORITY = {'WHEEL': 0, 'POTION': 10, 'BUFF': 20, 'PICK': 40, 'MOVE': 50}

@dataclass
class Action:
    kind: str
    key: str = ''
    index: int = -1
    point: tuple | None = None
    baseline: float | None = None
    reason: str = ''
    token: str = ''
    issued: float = 0.

@dataclass
class GaugeGate:
    threshold: float
    inclusive: bool
    streak: int = 0
    value: float | None = None
    waiting: tuple | None = None
    blocked_until: float = 0.
    last_frame: int = -1
    def observe(self, value, frame, now):
        if frame == self.last_frame:
            return None
        self.last_frame = frame
        self.value = value
        if value is None:
            self.streak = 0
            return None
        low = value <= self.threshold if self.inclusive else value < self.threshold
        self.streak = min(3, self.streak + 1) if low else 0
        if self.waiting:
            at, before = self.waiting
            if value - before >= .025:
                self.waiting = None
                self.streak = 0  # demand two NEW frames after the recovery
                self.blocked_until = now + .3
                return 'recovery_observed'
            if now - at >= 2.:
                self.waiting = None
                self.blocked_until = now + 5.
                self.streak = 0
                return 'recovery_unconfirmed'
        return None
    def ready(self, now):
        return self.value is not None and self.streak >= 2 and not self.waiting and now >= self.blocked_until
    def sent(self, now, baseline):
        self.waiting = (now, baseline)
        self.streak = 0

class BuffPlan:
    def __init__(self, fallback=285.):
        self.fallback = max(285., min(300., fallback))
        self.last_sent = [None]*4
        self.not_before = [0.]*4
        self.queue = []
        self.seen = [False]*4
        self.present = [0]*4
        self.missing = [0]*4
        self.next_at = 0.
        self.frame = -1
        self.started = False
    def start(self, now):
        if not self.started:
            self.queue = [i for i, t in enumerate(self.last_sent) if t is None or now-t >= self.fallback]
            self.started = True
    def observe(self, flags, frame, now):
        if frame == self.frame:
            return
        self.frame = frame
        for i, flag in enumerate(flags):
            if flag is None:
                self.missing[i] = self.present[i] = 0
            elif flag:
                self.present[i] += 1
                self.missing[i] = 0
                if self.present[i] >= 2:
                    self.seen[i] = True
            else:
                self.present[i] = 0
                self.missing[i] += 1
        for i, at in enumerate(self.last_sent):
            if at is None or i in self.queue:
                continue
            age = now-at
            gone = self.seen[i] and self.missing[i] >= 8 and age >= 30.
            expired = age >= self.fallback
            if gone or expired:
                self.queue.append(i)
    def sent(self, i, now):
        if self.queue and self.queue[0] == i:
            self.queue.pop(0)
        self.last_sent[i] = now
        self.seen[i] = False
        self.present[i] = self.missing[i] = 0
        self.next_at = now + .8

class CoinMemory:
    """Approximate camera-relative identity; uncertainty suppresses pickup.

    Attempted records survive disappearance. After a tracking gap, all pickup is
    held until records expire; assigning a new ID would risk pressing Z twice.
    """
    def __init__(self, ttl=180.):
        self.ttl = ttl
        self.records = []
        self.next_id = 1
        self.frame = -1
        self.uncertain_until = 0.
    def update(self, coins, world, confident, frame, now):
        if frame == self.frame:
            return []
        self.frame = frame
        self.records = [r for r in self.records if now-r.get('last_seen_at',r['at']) < self.ttl]
        if not confident:
            self.invalidate()
            return []
        if now < self.uncertain_until:
            return []
        available = []
        matched = set()
        for coin in coins:
            point = (coin['x']+world[0], coin['y']+world[1])
            candidates = [(hypot(point[0]-r['point'][0], point[1]-r['point'][1]), r)
                          for r in self.records if r['id'] not in matched]
            distance, record = min(candidates, key=lambda p:p[0]) if candidates else (1e9, None)
            if distance > 48.:
                record = dict(id=self.next_id, point=point, at=now, last_seen_at=now, seen=0, last=-1, attempted=False)
                self.next_id += 1
                self.records.append(record)
            matched.add(record['id'])
            record['seen'] = record['seen']+1 if record['last'] == frame-1 else 1
            record['last'] = frame
            record['last_seen_at'] = now
            # Keep the original reference for attempted coins: do not drift its
            # identity toward a different adjacent coin via repeated updates.
            if not record['attempted']:
                record['point'] = point
                record['at'] = now
            if not record['attempted'] and record['seen'] >= 2 and coin['confidence'] >= .78:
                available.append((record['id'], coin))
        return available
    def attempted(self, ident, now):
        for r in self.records:
            if r['id'] == ident:
                r['attempted'] = True
                r['at'] = now
    def invalidate(self):
        self.uncertain_until = max([self.uncertain_until]+[r.get('last_seen_at',r['at'])+self.ttl for r in self.records if r['attempted']])

@dataclass
class MoveEvidence:
    at: float
    world: tuple
    valid: int
    target: tuple
    interrupted: bool = False
    recovery: bool = False
    motion_epoch: int | None = None
    def outcome(self, now, world, valid, confident, timeout=3.6, minimum=12., current_epoch=None):
        if now-self.at < timeout:
            return 'pending'
        if self.interrupted:
            return 'interrupted'
        if self.motion_epoch is not None and current_epoch is not None and self.motion_epoch!=current_epoch:
            return 'unknown'
        if not confident or valid-self.valid < 2:
            return 'unknown'
        return 'moved' if hypot(world[0]-self.world[0],world[1]-self.world[1]) >= minimum else 'stuck'

class Controller:
    def __init__(self, config):
        self.config = config
        self.buffs = BuffPlan(config.get('buff_fallback_seconds',285))
        self.hp = GaugeGate(.5, True)
        self.mp = GaugeGate(.4, False)
        self.pending = None
        self.next_wheel = None
        self.last_wheel = -1e9
        self.quiet_until = 0.
        self.running = False
        self.sequence = 0
        self.events = []
        self.effect = None
        self.last_mana_action = -1e9
        self.move_evidence = None
        self.unconfirmed_wheels = 0
        self.pause_reason = ''
    def event(self, event, **values):
        self.events.append(dict(event=event, **values))
    def start(self, now):
        self.running = True
        self.pause_reason = ''
        self.buffs.start(now)
        if self.next_wheel is None:
            self.next_wheel = now + (5.8 if self.buffs.queue else 0.)
    def stop(self, reason='paused'):
        self.running = False
        self.pause_reason = reason
        if self.pending and self.pending.kind == 'BUFF':
            self.buffs.not_before[self.pending.index] = self.pending.issued + 30.
            self.event('BUFF_UNCERTAIN', index=self.pending.index, retry_after=30.)
        self.pending = None
        self.move_evidence = None
        self.hp.streak = self.mp.streak = 0
        self.event('STOP', reason=reason)
    def observe(self, now, hp, mp, frame, flags=None, vision_frame=-1):
        for name, gate, value in [('HP', self.hp, hp), ('MP', self.mp, mp)]:
            result = gate.observe(value, frame, now)
            if result:
                self.event('POTION_EFFECT', kind=name, result=result, value=value)
                if result == 'recovery_unconfirmed':
                    self.stop(name+' potion recovery unconfirmed; inspect gauge/input')
        if flags is not None:
            self.buffs.observe(flags, vision_frame, now)
        if self.effect and mp is not None:
            e = self.effect
            if self.last_mana_action > e['sent']:
                self.event('WHEEL_EFFECT', result='unknown_mana_interference', id=e['id'])
                self.effect = None
            elif now-e['sent'] >= .25 and .035 <= e['mp']-mp <= .095:
                self.event('WHEEL_EFFECT', result='mp_drop_consistent', id=e['id'], delta=e['mp']-mp)
                self.unconfirmed_wheels = 0
                self.effect = None
            elif now-e['sent'] >= 1.6:
                self.event('WHEEL_EFFECT', result='unconfirmed_no_retry', id=e['id'], delta=e['mp']-mp)
                self.unconfirmed_wheels += 1
                self.effect = None
                if self.unconfirmed_wheels >= 3:
                    self.stop('Three F3 effects unconfirmed; inspect game/AHK/calibration')
    def issue(self, action, now):
        self.sequence += 1
        action.token = f'{self.sequence:08d}'
        action.issued = now
        self.pending = action
        self.event('INPUT_ATTEMPT', id=action.token, kind=action.kind, key=action.key, reason=action.reason, baseline=action.baseline)
        return action
    def acknowledge(self, token, status, now):
        action = self.pending
        if action is None or action.token != token:
            return
        self.pending = None
        self.event('BRIDGE_'+status, id=token, kind=action.kind)
        if status != 'SENT':
            if action.kind == 'BUFF': self.buffs.not_before[action.index] = now + 30.
            # Outcome of partial OS input may be ambiguous. Do not requeue it.
            self.stop('bridge '+status)
            return
        if action.kind == 'WHEEL':
            self.last_wheel = now
            # No early resend and no timer reset based on an effect guess.
            self.next_wheel = now + max(10.25, self.config.get('wheel_interval',10.25))
            self.effect = dict(id=token, sent=now, mp=action.baseline)
            self.quiet_until = now + .8
        elif action.kind == 'BUFF':
            self.buffs.sent(action.index, now)
            self.last_mana_action = now
            self.quiet_until = now + .1
        elif action.kind == 'POTION':
            gate = self.hp if action.key == 'F1' else self.mp
            gate.sent(now, action.baseline)
            if action.key == 'F2': self.last_mana_action = now
            self.quiet_until = now + .3
        elif action.kind == 'PICK':
            self.quiet_until = now + .05
        elif action.kind == 'MOVE':
            self.quiet_until = now + .15
        if self.move_evidence and action.kind in ('WHEEL','BUFF','POTION','PICK'):
            self.move_evidence.interrupted = True
    def choose(self, now, anchor=None, attack_allowed=True, coin=None, target=None):
        if not self.running:
            return None
        if self.pending:
            if now-self.pending.issued > 2.5:
                self.stop('bridge ACK timeout; no blind retransmission')
            return None
        if self.hp.value is None or self.mp.value is None or now < self.quiet_until:
            return None
        mp = self.mp.value
        wheel_due = self.next_wheel is not None and now >= self.next_wheel and attack_allowed
        if wheel_due:
            if anchor is None: return None
            if self.mp.waiting: return None
            if mp*self.config.get('max_mp',8500) < self.config.get('wheel_mp',550)+250:
                if self.mp.ready(now):
                    return self.issue(Action('POTION',key='F2',baseline=mp,reason='F3 mana; confirmed <40%'),now)
                return None
            return self.issue(Action('WHEEL',point=anchor,baseline=mp,reason='due priority'),now)
        if self.hp.ready(now):
            return self.issue(Action('POTION',key='F1',baseline=self.hp.value,reason='two fresh frames HP <=50%'),now)
        if self.mp.ready(now):
            return self.issue(Action('POTION',key='F2',baseline=mp,reason='two fresh frames MP <40%'),now)
        # Do not contaminate the attack's MP observation window with buff costs.
        if self.effect:
            return None
        until_due = (self.next_wheel-now) if attack_allowed and self.next_wheel is not None else 999.
        if self.buffs.queue and now >= self.buffs.next_at and until_due > .95:
            i = self.buffs.queue[0]
            if now < self.buffs.not_before[i]: return None
            if mp < .4 or self.mp.waiting:
                return None
            return self.issue(Action('BUFF',key=BUFFS[i][0],index=i,baseline=mp),now)
        if self.buffs.queue:
            return None
        if coin and until_due > 1.:
            return self.issue(Action('PICK',index=coin[0],reason='one attempt per tracked coin'),now)
        if target and until_due > 1.5 and now-self.last_wheel >= .8:
            return self.issue(Action('MOVE',point=target),now)
        return None
