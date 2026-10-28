"""Prevent two live controllers from competing for a single game window."""
import os
from pathlib import Path

class InstanceLock:
    def __init__(self,path):self.path=Path(path);self.handle=None;self.file=None
    def acquire(self):
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype=wintypes.HANDLE
            self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            self.kernel.ReleaseMutex.argtypes=[wintypes.HANDLE]
            handle=self.kernel.CreateMutexW(None,True,'Local\\PolarTricksterController-v21')
            if not handle:raise OSError('Cannot create live-controller mutex')
            if ctypes.get_last_error()==183:
                self.kernel.CloseHandle(handle)
                raise RuntimeError('Polar is already running. Close the previous controller first.')
            self.handle=handle
        else:
            import fcntl
            self.file=self.path.open('a+b')
            try:fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                self.file.close();self.file=None
                raise RuntimeError('Polar is already running')
        return self
    def close(self):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle);self.kernel.CloseHandle(self.handle);self.handle=None
        if self.file:self.file.close();self.file=None
