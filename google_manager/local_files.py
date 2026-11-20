"""Local file and process checks shared by the desktop variants."""

import errno
import os
import stat
from pathlib import Path


def open_local_fd(path, flags=os.O_RDONLY, mode=0o600):
    if os.name == "nt":
        try:
            original = Path(path).lstat()
        except FileNotFoundError:
            original = None
        if original is not None and (
            not stat.S_ISREG(original.st_mode)
            or original.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise OSError(errno.ELOOP, "Expected a regular local file")
        fd = os.open(path, flags | os.O_BINARY, mode)
        if original is not None and not os.path.samestat(original, os.fstat(fd)):
            os.close(fd)
            raise OSError(errno.EAGAIN, "The file changed while opening it")
        return fd
    return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode)


def owned(path, info, *, private):
    if os.name != "nt":
        return info.st_uid == os.getuid() and (not private or not info.st_mode & 0o077)
    from .windows_security import verify_access

    return not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT and verify_access(path, private)


def ensure_process_running(pid):
    if os.name == "nt":
        from .windows_security import ensure_process_running as running

        running(pid)
    else:
        os.kill(pid, 0)
