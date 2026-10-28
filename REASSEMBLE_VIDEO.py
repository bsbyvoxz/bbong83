#!/usr/bin/env python3
"""Rebuild the original footage after extracting all four ZIPs into one directory."""
from pathlib import Path
import hashlib
ROOT = Path(__file__).resolve().parent
VIDEO = ROOT / 'references' / 'gameplay_camera_motion.mp4'
PARTS = [VIDEO.with_name(VIDEO.name + '.part01'), VIDEO.with_name(VIDEO.name + '.part02')]
EXPECTED_SHA256 = 'c542faf10c575abc1351be70dfc2e1ed307c0d592a35825ba1c21cf08dd5aeb5'
if not all(p.is_file() for p in PARTS):
    print('Missing video pieces:', [p.name for p in PARTS if not p.is_file()])
    raise SystemExit(1)
VIDEO.parent.mkdir(parents=True, exist_ok=True)
sha = hashlib.sha256()
with VIDEO.open('wb') as output:
    for p in PARTS:
        with p.open('rb') as source:
            while True:
                block = source.read(1024 * 1024)
                if not block: break
                sha.update(block)
                output.write(block)
if sha.hexdigest() != EXPECTED_SHA256:
    VIDEO.unlink(missing_ok=True)
    raise SystemExit('SHA256 mismatch; re-download video parts')
print('Video reconstructed and verified:', VIDEO)
