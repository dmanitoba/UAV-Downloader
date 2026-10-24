#!/usr/bin/env python
# coding: utf-8
"""Print the highest video resolution (height in pixels) and page title for a
supported source URL, without downloading any video/segment data.

Usage: probe_resolution.py <url>

Prints one line of JSON to stdout: {"resolution": <str>, "title": <str>}
  resolution is e.g. "1080" (tallest variant in the source's master m3u8),
  or "unknown" if the source has no master playlist (single-quality stream)
  or resolved to a direct-MP4 fallback (e.g. SupJav Streamtape) where height
  can't be read without downloading.
  title is the page's video title, or "" if unavailable — useful for sites
  (e.g. SupJav numeric-ID URLs) where the JAV code isn't in the URL itself.

Exit code is nonzero (with a message on stderr) if the URL is unsupported or
the source couldn't be reached at all.
"""

import os as _os, ssl as _ssl
try:
    import certifi as _certifi
    _ca = _certifi.where()
    if _ca and _os.path.exists(_ca):
        _os.environ.setdefault('SSL_CERT_FILE', _ca)
        _os.environ.setdefault('SSL_CERT_DIR', _os.path.dirname(_ca))
        try:
            _ssl.get_default_verify_paths()
        except (UnicodeDecodeError, SystemError):
            _dvp = _ssl.DefaultVerifyPaths(_ca, _os.path.dirname(_ca),
                                           'SSL_CERT_FILE', _ca,
                                           'SSL_CERT_DIR', _os.path.dirname(_ca))
            _ssl.get_default_verify_paths = lambda: _dvp
except Exception:
    pass

import json
import sys

from uav_downloader import sites as M3U8Sites
from uav_downloader.sites.base import _variant_height_bw


def main():
    if len(sys.argv) != 2:
        print("usage: probe_resolution.py <url>", file=sys.stderr)
        return 2

    url = sys.argv[1]
    # CreateSite's __init__ already calls get_url_infos() internally.
    site = M3U8Sites.CreateSite(url, silence=True)
    if site is None:
        print(f"unsupported URL: {url}", file=sys.stderr)
        return 1
    if not site.is_url_vaildate():
        print(f"no playable source found: {url}", file=sys.stderr)
        return 1

    title = getattr(site, '_targetName', None) or ''

    m3u8url = getattr(site, '_m3u8url', None)
    if not m3u8url:
        # e.g. SupJav resolved to a Streamtape direct MP4 with no m3u8 to inspect.
        print(json.dumps({"resolution": "unknown", "title": title}))
        return 0

    try:
        m3u8obj = site._load_m3u8(m3u8url)
    except Exception as exc:
        if getattr(site, '_direct_url', None):
            # The resolved HLS mirror is dead (e.g. SupJav sometimes only has
            # one working embed and it 502s), but a direct-MP4 fallback (e.g.
            # Streamtape) is still playable - same "no inspectable master
            # playlist" case as the `not m3u8url` branch above, not a real
            # failure. start_download() already falls back to this URL; this
            # probe has no download to retry, so it just reports unknown.
            print(json.dumps({"resolution": "unknown", "title": title}))
            return 0
        print(f"failed to load m3u8: {exc}", file=sys.stderr)
        return 1

    heights = [h for h, _bw in (_variant_height_bw(p) for p in (m3u8obj.playlists or [])) if h]
    resolution = str(max(heights)) if heights else "unknown"
    print(json.dumps({"resolution": resolution, "title": title}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
