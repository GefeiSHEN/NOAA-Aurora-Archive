#!/usr/bin/env python3
"""Bounded collection session on a native GitHub-hosted runner."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

UTC = timezone.utc


def next_slot(now):
    """Strictly next UTC minute 2, 7, ..., 57, independent of job start time."""
    if now.tzinfo is None:
        raise ValueError('timezone required')
    epoch = now.timestamp()
    return datetime.fromtimestamp(((epoch - 120) // 300 + 1) * 300 + 120, UTC)


def run(collect, minutes=6, clock=lambda: datetime.now(UTC),
        monotonic=time.monotonic, sleep=time.sleep):
    if not 0 < minutes <= 340:
        raise ValueError('session must last between 1 and 340 minutes')
    deadline = monotonic() + minutes * 60
    while monotonic() < deadline:
        print(f'Collecting at {clock().isoformat()}', flush=True)
        collect()  # Exhausted retries stop the job visibly; successful no-ops continue.
        target = next_slot(clock())
        print(f'Next UTC collection: {target.isoformat()}', flush=True)
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return
            delay = (target - clock()).total_seconds()
            if delay <= 0:
                break
            sleep(min(delay, remaining, 60))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--minutes', type=int, default=6)
    args = parser.parse_args()
    publisher = Path(__file__).with_name('publish.py')
    def collect():
        # Each fetch+publish has its own upper bound inside the session/job limits.
        subprocess.run([sys.executable, '-u', str(publisher), '--branch', args.branch],
                       check=True, timeout=600)
    run(collect, minutes=args.minutes)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(f'ERROR: {exc}')
