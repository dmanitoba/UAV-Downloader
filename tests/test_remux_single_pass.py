# coding: utf-8
"""Local fork additions that keep post-download work from starving the box:
a single-pass mp4 index (so the remux stops rewriting the whole file), child
deprioritisation, and reclaiming abandoned remux workdirs."""
import os
import sys
import time
import types
from pathlib import Path


def _stub(name, factory=None):
    try:
        __import__(name)
    except ImportError:
        sys.modules[name] = factory() if factory else types.ModuleType(name)


_stub('cloudscraper')
_stub('customtkinter')

from uav_downloader.core import iopacing
from uav_downloader.sites import base
from uav_downloader.sites.base import M3U8Crawler


# ── moov reservation ────────────────────────────────────────────────────

def _reserve(duration, payload=0):
    fake = types.SimpleNamespace(_total_duration=duration)
    return M3U8Crawler._moov_reserve_bytes(fake, payload)


def test_reservation_scales_with_duration():
    assert _reserve(7200) == 7200 * base._MOOV_RESERVE_BYTES_PER_SECOND


def test_reservation_covers_the_largest_moov_actually_observed():
    """A 4h video measured here carried an 18.5 MiB moov; the budget for the
    same duration must clear that with room to spare, because an overflow is
    only detected at the very end of the pass."""
    assert _reserve(4 * 3600) > int(18.5 * (1 << 20))


def test_reservation_falls_back_to_payload_size_without_durations():
    """A playlist with no usable EXTINF must still get a sane budget rather
    than the bare minimum."""
    payload = 8 << 30
    assert _reserve(0, payload) == payload // base._MOOV_RESERVE_SIZE_FRACTION


def test_reservation_is_clamped_both_ways():
    assert _reserve(0, 0) == base._MOOV_RESERVE_MIN
    assert _reserve(100 * 3600) == base._MOOV_RESERVE_MAX


def test_reservation_survives_a_junk_duration():
    fake = types.SimpleNamespace(_total_duration='not-a-number')
    assert M3U8Crawler._moov_reserve_bytes(fake, 1 << 30) > 0


# ── overflow must not be mistaken for a dead drive ──────────────────────

def test_moov_overflow_is_not_classified_as_a_storage_failure():
    """ffmpeg reports an under-sized reservation as 'Error writing trailer',
    which the destination-failure regex also matches. Were that to win, a
    too-small index budget would cancel every other download on the volume
    instead of simply retrying."""
    tail = ('[mp4 @ 0x5594cb811f00] reserved_moov_size is too small, '
            'needed 1995874 additional\n'
            '[out#0/mp4 @ 0x5594cb743980] Error writing trailer: '
            'Invalid argument\n')
    assert base._MOOV_OVERFLOW_RE.search(tail)
    from uav_downloader.core.storage import storage_failure_message
    assert storage_failure_message(tail), (
        'precondition: this text does look like a storage failure, which is '
        'exactly why the overflow check has to be made first')


# ── stale remux workdir reclamation ─────────────────────────────────────

def _crawler_in(tmp_path):
    return types.SimpleNamespace(_dest_folder=str(tmp_path))


def test_stale_remux_dir_is_reclaimed(tmp_path):
    stale = tmp_path / 'uav-remux-dead'
    stale.mkdir()
    (stale / 'out.mp4').write_bytes(b'x' * 1024)
    old = time.time() - base._STALE_REMUX_AGE_SECONDS - 60
    os.utime(stale, (old, old))
    M3U8Crawler._sweep_stale_remux_dirs(_crawler_in(tmp_path))
    assert not stale.exists()


def test_live_remux_dir_is_left_alone(tmp_path):
    """A workdir young enough to belong to a running job - including one from
    a second UAV instance - must never be pulled out from under it."""
    live = tmp_path / 'uav-remux-live'
    live.mkdir()
    (live / 'out.mp4').write_bytes(b'x' * 1024)
    M3U8Crawler._sweep_stale_remux_dirs(_crawler_in(tmp_path))
    assert live.exists()


def test_sweep_only_touches_our_own_prefix(tmp_path):
    for name in ('466434', 'Some Performer', '.hidden'):
        d = tmp_path / name
        d.mkdir()
        old = time.time() - base._STALE_REMUX_AGE_SECONDS - 60
        os.utime(d, (old, old))
    M3U8Crawler._sweep_stale_remux_dirs(_crawler_in(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        '.hidden', '466434', 'Some Performer']


def test_sweep_tolerates_a_missing_destination(tmp_path):
    M3U8Crawler._sweep_stale_remux_dirs(
        _crawler_in(tmp_path / 'does-not-exist'))
    M3U8Crawler._sweep_stale_remux_dirs(types.SimpleNamespace(_dest_folder=None))


# ── child deprioritisation ──────────────────────────────────────────────

def test_deprioritize_lowers_nice_of_the_calling_process():
    before = os.getpriority(os.PRIO_PROCESS, 0)
    try:
        iopacing.deprioritize(0, nice=before + 1)
        assert os.getpriority(os.PRIO_PROCESS, 0) == before + 1
    finally:
        try:
            os.setpriority(os.PRIO_PROCESS, 0, before)
        except OSError:
            pass            # cannot raise priority back without privileges


def test_deprioritize_never_raises_on_a_dead_pid():
    iopacing.deprioritize(2 ** 30)


def test_deprioritizing_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(iopacing, '_DISABLED', True)
    before = os.getpriority(os.PRIO_PROCESS, 0)
    iopacing.deprioritize(0, nice=before + 5)
    assert os.getpriority(os.PRIO_PROCESS, 0) == before


def test_no_page_cache_pacing_is_shipped():
    """Flushing and dropping the remux output's cache behind the write head
    was measured to push an unrelated writer's median fsync from ~15ms to
    multiple SECONDS on this hardware - sustained sluggishness in exchange
    for a slightly shorter tail. Bounding the dirty backlog is the kernel's
    job (vm.dirty_bytes), not something to force from userspace, so the
    mechanism must stay gone rather than get quietly reintroduced."""
    assert not hasattr(iopacing, 'FileCachePacer')
    assert iopacing.__all__ == ['deprioritize']
