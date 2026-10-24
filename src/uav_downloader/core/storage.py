"""Identify destination I/O failures separately from retryable transport errors."""

import errno
import re

# Only errno values that genuinely indicate the destination volume/device
# itself is the problem (full, dead, disconnected, read-only, or a network
# mount that dropped) escalate to StorageWriteError and stop every other
# in-flight job on that same destination. Anything else (a missing file from
# a benign race, a permission quirk, too many open files, ...) is job-
# specific, not evidence the whole destination is broken, and is left to
# propagate as a plain OSError so only the one job that hit it fails - one
# unrelated job's local hiccup must not cancel every other healthy transfer.
_FATAL_ERRNOS = frozenset(filter(None, (
    getattr(errno, name, None) for name in (
        'ENOSPC', 'EIO', 'ENODEV', 'EROFS', 'ESTALE', 'ESHUTDOWN',
        'ENOTCONN', 'ECONNRESET', 'ETIMEDOUT', 'EHOSTUNREACH',
        'ENETDOWN', 'ENETUNREACH',
    )
)))


def storage_failure_message(message):
    return bool(re.search(
        r'no space left|disk (?:is )?full|input/output error|i/o error|'
        r'device (?:is )?not ready|no such device|device not connected|'
        r'error (?:writing|closing)|failed to (?:write|flush)',
        str(message), re.IGNORECASE))


def is_destination_fatal(exc):
    """Whether this OSError indicates the destination itself is broken,
    rather than a job-specific/transient failure - see _FATAL_ERRNOS."""
    return getattr(exc, 'errno', None) in _FATAL_ERRNOS


class StorageWriteError(OSError):
    """Destination storage failed; stop writes and require an explicit retry."""

    def __init__(self, path, cause, operation='access'):
        self.path = str(path)
        self.cause = cause
        self.operation = operation
        code = getattr(cause, 'errno', None) or errno.EIO
        if code == errno.ENOENT:
            hint = ('The path is missing or unavailable; check the drive and '
                    'choose an existing writable download folder')
        elif code == errno.ENOSPC:
            hint = 'Free space on the selected download drive before retrying'
        elif code in (errno.EACCES, errno.EPERM):
            hint = ('Check folder permission and whether another application '
                    'has locked the file')
        elif code == errno.ENOTDIR:
            hint = 'A path component is not a directory; select a download folder'
        elif code == errno.EINVAL:
            hint = 'Check the destination path and drive; try a shorter writable path'
        else:
            hint = 'Check free space, drive availability and device health before retrying'
        detail = getattr(cause, 'strerror', None) or str(cause)
        super().__init__(
            code,
            f"Destination storage I/O failed [{operation}] at '{self.path}': "
            f'{hint}. Details: {detail}. Downloads stopped.', self.path)


class _StorageFile:
    def __init__(self, path, mode, *args, **kwargs):
        self.path = path
        try:
            self.file = open(path, mode, *args, **kwargs)
        except OSError as exc:
            # Unconditional, unlike _io below - failing to even open the
            # destination at all is inherently about the destination
            # (most commonly: the download directory itself is gone),
            # not a per-operation hiccup is_destination_fatal's narrower
            # errno set is meant to filter out. In particular ENOENT here
            # is NOT the "missing file from a benign race" _FATAL_ERRNOS'
            # own comment carves out - there's no existing file to race
            # on during an open() call, so it means the destination
            # folder itself doesn't exist.
            operation = ('open for writing' if any(flag in mode for flag in 'wax+')
                         else 'open for reading')
            raise StorageWriteError(path, exc, operation=operation) from exc

    def _io(self, method, *args, **kwargs):
        try:
            return getattr(self.file, method)(*args, **kwargs)
        except OSError as exc:
            # Gated, unlike __init__ above - this runs against an
            # ALREADY-open handle, where a transient/job-specific error
            # (the benign race _FATAL_ERRNOS' own comment describes) is
            # plausible, so only genuinely destination-level errno
            # values escalate; anything else propagates as a plain
            # OSError so one job's hiccup doesn't cancel every other
            # in-flight transfer on the same destination.
            if is_destination_fatal(exc):
                raise StorageWriteError(self.path, exc, operation=method) from exc
            raise

    def write(self, data):
        written = self._io('write', data)
        if written != len(data):
            raise StorageWriteError(
                self.path, OSError(errno.EIO, 'short write'), operation='write')
        return written

    def read(self, *args):
        return self._io('read', *args)

    def seek(self, *args):
        return self._io('seek', *args)

    def truncate(self, *args):
        return self._io('truncate', *args)

    def flush(self):
        return self._io('flush')

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self._io('close')
        except StorageWriteError:
            if kind is None:
                raise


def storage_open(path, mode, *args, **kwargs):
    return _StorageFile(path, mode, *args, **kwargs)
