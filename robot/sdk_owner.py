"""Process lock shared by direct scripts and the bridge before SDK discovery."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import tempfile


@contextmanager
def sdk_owner(serial):
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9]{14}', serial):
        raise ValueError('Invalid rover serial')
    path = Path(tempfile.gettempdir()) / f'pavia-rover-{serial}.lock'
    with path.open('a+b') as file:
        if os.name == 'nt':
            import msvcrt
            file.seek(0)
            if not file.read(1):
                file.write(b'0')
                file.flush()
            file.seek(0)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError('Another local process owns the rover SDK') from exc
        else:
            import fcntl
            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError('Another local process owns the rover SDK') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
