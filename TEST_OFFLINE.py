"""Run unit tests then actual image/video regression. No live input."""
from pathlib import Path
import argparse
import sys
import unittest
from regression_runner import run

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--video');p.add_argument('--require-video',action='store_true');a=p.parse_args()
    root=Path(__file__).resolve().parent
    tests=unittest.defaultTestLoader.discover(str(root/'tests'))
    results=unittest.TextTestRunner(verbosity=2).run(tests)
    if not results.wasSuccessful():sys.exit(1)
    report=run(root/'regression_output',a.video,a.require_video)
    sys.exit(bool(report['failures']))
