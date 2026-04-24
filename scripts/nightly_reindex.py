#!/usr/bin/env python3
"""Nightly comp re-index for DATARADAR.

Rebuilds master_sales.json + historical_clean.json from the existing source
files under data/. Flask app auto-picks up the new historical_clean.json
via its mtime check in load_historical_clean().

Chain:
  1. consolidate_all.py  — merges ~11 source dumps into data/master_sales.json
  2. clean_historical.py — filters + normalizes to data/historical_clean.json

Railway cron setup (one-time):
  Railway project → Create New Service → Cron Job
  Root directory: .
  Command: python3 scripts/nightly_reindex.py
  Schedule: 0 3 * * *   (03:00 UTC daily = late evening Pacific)

To invalidate the running app's in-memory cache without restarting:
  curl https://your-app/api/admin/reindex-cache-bust   (if endpoint exists)
  — otherwise Flask picks up new data on next request via mtime check.

Manual run (local):
  cd /path/to/dataradar-listings
  python3 scripts/nightly_reindex.py
"""
import json
import os
import subprocess
import sys
import time

# Anchor to repo root regardless of where cron invokes us from
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)


def run(step, cmd):
    print(f'[reindex] {step}: {cmd}', flush=True)
    t0 = time.time()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    dt = time.time() - t0
    print(f'[reindex] {step} done in {dt:.1f}s (exit {result.returncode})', flush=True)
    if result.stdout.strip():
        print(f'[reindex] stdout (tail): {result.stdout.strip()[-500:]}', flush=True)
    if result.returncode != 0:
        print(f'[reindex] STDERR: {result.stderr[:800]}', flush=True)
        sys.exit(result.returncode)


def stat_result():
    paths = ['data/master_sales.json', 'data/historical_clean.json']
    for p in paths:
        if not os.path.exists(p):
            print(f'[reindex] {p}: MISSING')
            continue
        size = os.path.getsize(p)
        try:
            data = json.load(open(p))
            n = len(data) if isinstance(data, list) else len(data.keys())
        except Exception:
            n = '?'
        print(f'[reindex] {p}: {size/1024/1024:.1f} MB · {n} records')


if __name__ == '__main__':
    print(f'[reindex] start at {time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())}')
    run('step 1/2 consolidate_all', f'{sys.executable} consolidate_all.py')
    run('step 2/2 clean_historical', f'{sys.executable} clean_historical.py')
    stat_result()
    print('[reindex] complete', flush=True)
