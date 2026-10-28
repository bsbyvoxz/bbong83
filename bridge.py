"""Atomic, leased, bounded file transport to AutoHotkey v1.1.

No keyboard injection in Python. Stale sessions can never be replayed.
"""
from pathlib import Path
import os
import time
import uuid
from control_core import PRIORITY

def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.tmp')
    tmp.write_text(text, encoding='ascii')
    os.replace(tmp, path)

class FileBridge:
    def __init__(self, root, wall_clock=time.time):
        self.root = Path(root)
        self.queue = self.root/'bridge_queue'
        self.acks = self.root/'bridge_acks'
        self.queue.mkdir(exist_ok=True)
        self.acks.mkdir(exist_ok=True)
        self.clock = wall_clock
        self.session = ''
        self.files = {}
    def begin(self):
        self.stop()
        self.session = uuid.uuid4().hex[:16]
        self.heartbeat()
    def heartbeat(self):
        if self.session:
            atomic_text(self.root/'bridge_lease.txt',f'{self.session} {int((self.clock()+.9)*1000)}\n')
    def ready(self):
        try:
            stamp = int((self.root/'bridge_ready.txt').read_text().strip())/1000
            return abs(self.clock()-stamp) < 2.
        except (OSError, ValueError):
            return False
    def send(self, action, offset=(0,0)):
        if not self.session:
            raise RuntimeError('bridge disabled')
        x,y = action.point or (0,0)
        x,y = round(x+offset[0]),round(y+offset[1])
        if action.kind not in PRIORITY:
            raise ValueError('unknown bridge action')
        expires = int((self.clock()+1.5)*1000)
        filename = f'{PRIORITY[action.kind]:02d}_{self.session}_{action.token}.cmd'
        path = self.queue/filename
        atomic_text(path,f'{action.token} {self.session} {expires} {action.kind} {action.key or "-"} {x} {y}\n')
        self.files[action.token] = path
    def poll(self, token):
        path = self.acks/f'{self.session}_{token}.ack'
        try:
            parts = path.read_text(encoding='ascii').split()
            path.unlink()
        except FileNotFoundError:
            return None
        if len(parts) != 3 or parts[0] != token or parts[1] not in ('SENT','EXPIRED','FOCUS','STOPPED','INVALID'):
            return 'INVALID', self.clock()
        self.files.pop(token,None)
        return parts[1],int(parts[2])/1000
    def stop(self):
        atomic_text(self.root/'bridge_lease.txt','STOP 0\n')
        for folder, pattern in ((self.queue,'*.cmd'),(self.acks,'*.ack')):
            for p in folder.glob(pattern):
                p.unlink(missing_ok=True)
        self.session = ''
        self.files.clear()
