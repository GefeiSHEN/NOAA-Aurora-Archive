#!/usr/bin/env python3
"""Bounded collection session on a native GitHub-hosted runner."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

UTC = timezone.utc


def next_slot(now):
    """Strictly next even UTC minute, independent of job start time."""
    if now.tzinfo is None:
        raise ValueError('timezone required')
    epoch = now.timestamp()
    return datetime.fromtimestamp((epoch // 120 + 1) * 120, UTC)


def run(collect, minutes=300, until=None, clock=lambda: datetime.now(UTC),
        monotonic=time.monotonic, sleep=time.sleep):
    if not 0 < minutes <= 340:
        raise ValueError('session must last between 1 and 340 minutes')
    started = monotonic()
    global_deadline = started + (until - clock().timestamp()) if until is not None else float('inf')
    deadline = min(started + minutes * 60, global_deadline)
    while monotonic() < deadline:
        print(f'Collecting at {clock().isoformat()}', flush=True)
        collect(min(600, global_deadline - monotonic()))
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


def run_command(command, timeout):
    """Stop the publisher and its Git children together at the cycle deadline."""
    process = subprocess.Popen(command, start_new_session=True)
    try:
        code = process.wait(timeout=timeout)
        if code:
            raise subprocess.CalledProcessError(code, command)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--seconds', type=int, default=18000)
    parser.add_argument('--until', type=float, help='shared workflow deadline as Unix seconds')
    args = parser.parse_args()
    publisher = Path(__file__).with_name('publish.py')
    def collect(timeout):
        try:
            run_command([sys.executable, '-u', str(publisher), '--branch', args.branch], timeout)
        except subprocess.TimeoutExpired:
            if args.until is None or datetime.now(UTC).timestamp() < args.until:
                raise
            print('Collection window ended; stopped the in-flight publisher.', flush=True)
    run(collect, minutes=args.seconds / 60, until=args.until)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(f'ERROR: {exc}')
