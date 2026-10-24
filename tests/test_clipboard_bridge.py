"""Regression test for the jav-queue-bridge auto-queue path (see
browse.py's BRIDGE_CLIPBOARD_MARKER). Upstream's "reviewed queue
workflows" change turned every clipboard-detected URL into a mere
_dl_url_var hint requiring a manual Add click - this silently broke the
Stash plugin's "+ Queue" button, which relies on an immediate, unattended
add+enqueue. The marker-gated branch added to restore that must keep
calling BOTH add_item() and enqueue() (not just add_item(), the way the
pre-review-workflow code did), since enqueue() is what now actually
starts/schedules a task.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
import test_persistence as _bootstrap  # noqa: F401  (stubs runtime deps)

from uav_downloader.apps import browse as gui_modern
from uav_downloader.apps.browse import BRIDGE_CLIPBOARD_MARKER


class FakeManager:
    def __init__(self, existing_urls=()):
        self.calls = []
        self._existing = list(existing_urls)

    def get_items(self):
        return [types.SimpleNamespace(url=u) for u in self._existing]

    def add_item(self, url, **kwargs):
        self.calls.append(('add_item', url, kwargs))

    def enqueue(self, url, dest, not_before=None):
        self.calls.append(('enqueue', url, dest, not_before))


def _app(existing_urls=(), preflight_ok=True):
    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    app._dlmgr = FakeManager(existing_urls)
    app._dest_var = types.SimpleNamespace(get=lambda: 'download')
    app._preflight_download_destination = lambda: preflight_ok
    return app


def test_queue_bridge_urls_adds_and_enqueues_valid_new_url():
    app = _app()
    app._queue_bridge_urls(['https://supjav.com/419986.html'])

    kinds = [call[0] for call in app._dlmgr.calls]
    assert kinds == ['add_item', 'enqueue']
    _, url, kwargs = app._dlmgr.calls[0]
    assert url == 'https://supjav.com/419986.html'
    assert kwargs['dest'] == 'download'
    _, url, dest, _not_before = app._dlmgr.calls[1]
    assert (url, dest) == ('https://supjav.com/419986.html', 'download')


def test_queue_bridge_urls_skips_already_queued_url():
    app = _app(existing_urls=['https://supjav.com/419986.html'])
    app._queue_bridge_urls(['https://supjav.com/419986.html'])

    assert app._dlmgr.calls == []


def test_queue_bridge_urls_skips_invalid_url():
    app = _app()
    app._queue_bridge_urls(['https://example.com/not-a-video'])

    assert app._dlmgr.calls == []


def test_queue_bridge_urls_noop_when_preflight_fails():
    app = _app(preflight_ok=False)
    app._queue_bridge_urls(['https://supjav.com/419986.html'])

    assert app._dlmgr.calls == []


def test_clipboard_poll_routes_marker_prefixed_text_to_bridge_queue():
    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    app._is_closing = False
    app._rebuilding = False
    app._clp_text = ''
    app.clipboard_get = lambda: BRIDGE_CLIPBOARD_MARKER + 'https://supjav.com/419986.html'
    app.after = lambda *a, **kw: None
    seen = []
    app._queue_bridge_urls = lambda urls: seen.append(list(urls))
    app._dl_url_var = types.SimpleNamespace(set=lambda v: seen.append(('hint', v)))

    app._clipboard_poll()

    assert seen == [['https://supjav.com/419986.html']]


def test_clipboard_poll_still_only_hints_for_unmarked_clipboard_text():
    app = gui_modern.ModernApp.__new__(gui_modern.ModernApp)
    app._is_closing = False
    app._rebuilding = False
    app._clp_text = ''
    app.clipboard_get = lambda: 'https://supjav.com/419986.html'
    app.after = lambda *a, **kw: None
    bridge_calls = []
    app._queue_bridge_urls = lambda urls: bridge_calls.append(list(urls))
    hints = []
    app._dl_url_var = types.SimpleNamespace(set=lambda v: hints.append(v))

    app._clipboard_poll()

    assert bridge_calls == []
    assert hints == ['https://supjav.com/419986.html']
