"""Keep bulk post-processing from starving the rest of the machine.

Local fork addition, not upstream.

Finishing a download spawns long batch children - an ``ffmpeg -c copy`` remux,
and for subtitles an audio extraction plus an ASR pass that will use every core
it is given. A child inherits the parent's scheduling priority, so by default
all of that competes with interactive work on equal terms. Nothing here tries
to make those jobs *slower*; the point is only that they should lose, rather
than win, a tie against whatever the user is actually doing.

Why there is no page-cache pacing here
--------------------------------------
An earlier version of this module also flushed and dropped the remux output's
page cache behind the write head (``sync_file_range`` + ``POSIX_FADV_DONTNEED``
on a growing file), on the theory that bounding the dirty backlog would stop a
multi-GB write from stalling unrelated processes in
``balance_dirty_pages()``. Measured against a 1.5 GiB write on this hardware,
with an unrelated 4 KiB+fsync writer sampling latency throughout, it made
things clearly worse:

                      victim fsync p50      p99/max
    buffered (kernel default)   13-16 ms    15-38 s
    flushed and dropped        4.2-13.7 s   14-18 s

Forcing writeback continuously keeps the device queue permanently busy, so
every unrelated fsync now queues behind it - trading an occasional long stall
for sustained sluggishness, which is the worse failure mode. The tail improved
somewhat, but nowhere near enough to pay for a ~1000x worse median.

The honest conclusion is that the dirty backlog is the kernel's job: it
throttles the process doing the dirtying, whereas saturating the device from
userspace penalises everyone equally. Capping it belongs in
``vm.dirty_background_bytes`` / ``vm.dirty_bytes``, not here. What this module
*can* usefully do is reduce how much we write in the first place (see
``_MOOV_RESERVE_BYTES_PER_SECOND`` in ``sites/base.py``, which halves the
remux's write volume) and make sure what we do write runs at low priority.
"""

import ctypes
import os

__all__ = ['deprioritize']

_DISABLED = os.environ.get('UAV_NO_IO_PACING', '').strip().lower() in (
    '1', 'true', 'yes', 'on')

# ioprio_set(2): IOPRIO_WHO_PROCESS, and class 3 (idle) in the high bits of
# the 16-bit value.
#
# This only has teeth under an I/O scheduler that implements priority, i.e.
# BFQ. It is a no-op on the `none` scheduler NVMe uses and on mq-deadline, so
# on an all-NVMe box it buys nothing - it is set anyway because it costs one
# syscall, needs no scheduler probing, and does help a destination that happens
# to sit on a SATA disk under BFQ.
_IOPRIO_WHO_PROCESS = 1
_IOPRIO_CLASS_IDLE = 3
_IOPRIO_CLASS_SHIFT = 13

_BATCH_NICE = 10

_libc = None


def _libc_handle():
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL('libc.so.6', use_errno=True)
        except OSError:
            _libc = False
    return _libc or None


def _call(name, *args):
    """Best-effort raw libc call; False when unavailable or failed.

    Every caller here is an optimisation whose failure must never break the
    job it was meant to help, so a missing symbol (musl, a non-glibc libc, a
    kernel without the syscall) is deliberately indistinguishable from
    "did nothing".
    """
    libc = _libc_handle()
    if libc is None:
        return False
    try:
        fn = getattr(libc, name)
    except AttributeError:
        return False
    try:
        return fn(*args) == 0
    except (OSError, ValueError, ctypes.ArgumentError):
        return False


def deprioritize(target=0, nice=_BATCH_NICE):
    """Drop a child's CPU and disk priority so it yields to interactive work.

    `target` is a pid, or any object carrying one as `.pid` (a Popen), so a
    caller never has to reach into a process handle that might not have one.
    Lowering priority is a pure optimisation, so anything unexpected here -
    including a target that turns out not to identify a process at all - is
    swallowed rather than allowed to fail the job it was meant to speed up.

    Applied from the parent against an already-spawned pid rather than through
    ``Popen(preexec_fn=...)``, which is documented as unsafe in a threaded
    process - and this runs with the download worker pool alive. The child
    therefore runs at normal priority for the few milliseconds before this
    lands, which does not matter for a job that takes minutes.
    """
    if _DISABLED:
        return
    pid = getattr(target, 'pid', target)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
        return
    try:
        os.setpriority(os.PRIO_PROCESS, pid, nice)
    except (OSError, AttributeError, ValueError, OverflowError):
        pass
    try:
        _call('ioprio_set', ctypes.c_int(_IOPRIO_WHO_PROCESS),
              ctypes.c_int(pid),
              ctypes.c_int((_IOPRIO_CLASS_IDLE << _IOPRIO_CLASS_SHIFT)))
    except (OverflowError, ValueError):
        pass
