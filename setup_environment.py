"""Build a local environment; archive broken environments instead of deleting."""
import os
from pathlib import Path
import struct
import subprocess
import sys
import uuid
import venv

ROOT = Path(__file__).resolve().parent


def supported_runtime(version, bits):
    return (3, 10) <= tuple(version[:2]) < (3, 14) and bits == 64


def environment_python(directory):
    return Path(directory) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def environment_works(python):
    if not python.is_file():
        return False
    code = ('import struct, sys; sys.exit(0 if '
            '(3,10) <= sys.version_info[:2] < (3,14) and '
            'struct.calcsize("P") * 8 == 64 else 1)')
    try:
        return subprocess.run([str(python), '-c', code], capture_output=True,
                              timeout=15, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prepare_environment(directory):
    directory = Path(directory)
    python = environment_python(directory)
    if environment_works(python):
        return python
    if directory.exists():
        backup = directory.with_name(directory.name + '.previous-' + uuid.uuid4().hex[:12])
        directory.rename(backup)
        print('Previous environment preserved at:', backup.name, flush=True)
    venv.EnvBuilder(with_pip=True).create(directory)
    if not environment_works(python):
        raise RuntimeError('Virtual environment is unusable; no package installation attempted')
    return python


def main():
    if not supported_runtime(sys.version_info, struct.calcsize('P') * 8):
        print('Use 64-bit Python 3.10-3.13 (3.12 recommended).', file=sys.stderr)
        return 1
    print('Using Python:', sys.executable, flush=True)
    try:
        python = prepare_environment(ROOT / '.venv')
        for args in (['-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')],
                     ['-m', 'unittest', 'discover', '-s', 'tests', '-v']):
            result = subprocess.run([str(python)] + args, cwd=ROOT, check=False)
            if result.returncode:
                return result.returncode
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('Setup failed:', exc, file=sys.stderr)
        return 1
    print('Setup complete. Install AutoHotkey v1.1.37.02 and run START.bat.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
