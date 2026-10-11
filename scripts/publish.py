#!/usr/bin/env python3
"""Publish through disposable worktrees; never reset or force-push a user's checkout."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

try:
    from . import collect
except ImportError:
    import collect


def diagnostic(stderr):
    """Last line of Git's stderr with URLs and credentials removed."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ''
    line = re.sub(r'[a-z][a-z0-9+.-]*://\S+', '<url>', lines[-1], flags=re.I)
    line = re.sub(r'\S+@\S+', '<redacted>', line)
    line = re.sub(r'(?i)(token|password|authorization)\S*', '<redacted>', line)
    return line[:200]


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=60, text=True)
    if result.returncode:
        # Report only a redacted line so the cause is visible without echoing remotes.
        reason = diagnostic(result.stderr)
        raise RuntimeError(f'git {args[0]} failed (exit {result.returncode})'
                           + (f': {reason}' if reason else ''))
    return result.stdout.strip()


def publish(repo, branch, body, received, attempts=4, sleep=time.sleep, before_push=None):
    collect.validate(body)
    git(repo, 'check-ref-format', 'refs/heads/' + branch)
    target = 'refs/heads/' + branch
    last_error = None
    for attempt in range(attempts):
        try:
            git(repo, 'fetch', '--no-tags', '--depth=1', 'origin', target)
            head = git(repo, 'rev-parse', 'FETCH_HEAD')
            with tempfile.TemporaryDirectory(prefix='ovation-publish-') as temp:
                work = Path(temp) / 'work'
                git(repo, 'worktree', 'add', '--detach', str(work), head)
                try:
                    if not collect.archive(work, body, received):
                        print('unchanged: successful no-op')
                        return False
                    git(work, 'add', '--all')  # Only our archive writes exist in this fresh worktree.
                    git(work, '-c', 'user.name=github-actions[bot]', '-c',
                        'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                        'commit', '-m', 'data(ovation): archive NOAA snapshot')
                    if before_push is not None:
                        before_push(attempt)
                    git(work, 'push', 'origin', 'HEAD:' + target)
                    print('archive published')
                    return True
                finally:
                    git(repo, 'worktree', 'remove', '--force', str(work))
        except RuntimeError as exc:
            last_error = exc
        if attempt < attempts - 1:
            print(f'publication attempt {attempt + 1} failed: {last_error}; retrying from remote head',
                  file=sys.stderr)
            sleep(15 * (attempt + 1))  # About 90 seconds in total, inside the 600-second budget.
    raise RuntimeError(f'publication failed after {attempts} attempts: {last_error}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', required=True)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    args = parser.parse_args()
    body, received = collect.download()
    publish(args.repo.resolve(), args.branch, body, received)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        sys.exit(f'ERROR: {exc}')
