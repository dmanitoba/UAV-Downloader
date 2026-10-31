#!/usr/bin/env python
# coding: utf-8
"""Merge queued URLs into the UAV Browser's persisted download queue CSV, in
the same format the GUI itself reads/writes (see DownloadManager.load_csv/
save_csv in uav_downloader/apps/browse.py), so they show up in the GUI ready
to run via its own "Download All" button (and its own concurrency setting)
without needing the GUI open.

Two input modes:

  --from-csv file1.csv [file2.csv ...]
      Merge one or more of the small per-run CSVs javdl_jable -g writes to
      the CSV Backups folder (header optional; matches this script's own
      output format: 狀態,名稱,進度,速度,網址,目標).

  (no args) reads a JSON array from stdin instead:
      [{"url": "...", "title": "...", "dest": "..."}, ...]

Either way, any url already present in the live queue CSV — or repeated
across multiple input files — is skipped once (existing rows, including
in-progress or completed ones, are left untouched). Prints a one-line
summary to stdout.

The GUI only reads this file at startup, so run this before launching the
GUI (or before it's running) — writing to it while the GUI is already open
won't be picked up until the GUI restarts, and the GUI may overwrite it on
its own autosave anyway.
"""

import csv
import json
import os
import sys

from uav_downloader.core import config

# Matches DownloadManager.save_csv's columns (uav_downloader/apps/browse.py),
# including 字幕來源證據 added for local-subtitle rows -- keeping it here too
# so merging doesn't silently drop that column from existing queue rows.
FIELDNAMES = ['狀態', '名稱', '進度', '速度', '網址', '目標', '字幕來源證據']


def _rows_from_csv_files(paths):
    for path in paths:
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = (row.get('網址') or '').strip()
                if not url:
                    continue
                yield {
                    'url': url,
                    'title': row.get('名稱') or '',
                    'dest': row.get('目標') or '',
                }


def _rows_from_stdin_json():
    try:
        items = json.load(sys.stdin)
    except Exception as exc:
        print(f"error: couldn't parse stdin as JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(items, list):
        print("error: expected a JSON array on stdin", file=sys.stderr)
        raise SystemExit(1)
    for item in items:
        url = (item.get('url') or '').strip()
        if not url:
            continue
        yield {'url': url, 'title': item.get('title') or '', 'dest': item.get('dest') or ''}


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] != '--from-csv' or len(sys.argv) < 3:
            print("usage: queue_to_gui.py --from-csv file1.csv [file2.csv ...]", file=sys.stderr)
            print("       (or pipe a JSON array to stdin with no args)", file=sys.stderr)
            return 2
        items = list(_rows_from_csv_files(sys.argv[2:]))
    else:
        items = list(_rows_from_stdin_json())

    path = config.queue_csv_path()

    existing_rows = []
    existing_urls = set()
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                existing_rows.append(row)
                if row.get('網址'):
                    existing_urls.add(row['網址'])

    added = 0
    skipped = 0
    new_rows = []
    for item in items:
        url = item['url']
        if url in existing_urls:
            skipped += 1
            continue
        new_rows.append({
            '狀態': '',
            '名稱': item['title'],
            '進度': '0%',
            '速度': '',
            '網址': url,
            '目標': item['dest'] or 'download',
        })
        existing_urls.add(url)
        added += 1

    if new_rows:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            for row in existing_rows:
                w.writerow({k: row.get(k, '') for k in FIELDNAMES})
            for row in new_rows:
                w.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    print(f"added {added}, skipped {skipped} (already in queue) -> {path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
