#!/usr/bin/env python3
"""Move forecast days older than the window from main into verified GitHub release assets.

Each old day folder becomes OVATION-YYYY-MM-DD.tar.xz on release ovation-YYYY-MM. A folder is
removed from main only after its asset is uploaded, downloaded back, and every file inside it
matches the Git blob it came from. OVATION/archive.json records each asset and the last commit
that still holds the folder, so every byte stays reachable. Nothing is deleted from Git history.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time

try:
    from . import collect
except ImportError:
    import collect

INDEX = 'OVATION/archive.json'
DAY = re.compile(r'OVATION/(\d{4})/(\d{2})/(\d{2})')


def git(repo, *args, timeout=600, stdout=subprocess.PIPE, input=None):
    result = subprocess.run(['git', '-C', str(repo), *args], stdout=stdout, stderr=subprocess.PIPE,
                            timeout=timeout, input=input)
    if result.returncode:
        # Do not echo remotes/credentials from Git's diagnostic output.
        raise RuntimeError(f'git {args[0]} failed (exit {result.returncode})')
    return result.stdout.decode().strip() if stdout is subprocess.PIPE else None


def day_folders(repo, rev):
    """{date: tree oid} for every OVATION/YYYY/MM/DD folder at rev."""
    folders = {}
    for line in git(repo, 'ls-tree', '-r', '-t', rev, 'OVATION').splitlines():
        meta, path = line.split('\t', 1)
        match = DAY.fullmatch(path)
        if match and meta.split()[1] == 'tree':
            folders[date(*map(int, match.groups()))] = meta.split()[2]
    return folders


def day_path(day):
    return f'OVATION/{day:%Y/%m/%d}'


def asset_name(day):
    return f'OVATION-{day:%Y-%m-%d}.tar.xz'


def release_tag(day):
    return f'ovation-{day:%Y-%m}'


def blob_oid(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def expected_blobs(repo, rev, day):
    blobs = {}
    for line in git(repo, 'ls-tree', '-r', rev, day_path(day)).splitlines():
        meta, path = line.split('\t', 1)
        blobs[path] = meta.split()[2]
    return blobs


def verify(archive, blobs):
    """True only if the archive holds exactly these paths with exactly these blob contents."""
    try:
        found = {}
        with tarfile.open(archive, 'r:xz') as tar:
            for member in tar:
                if member.isdir():
                    continue
                if not member.isfile():
                    return False
                found[member.name] = blob_oid(tar.extractfile(member).read())
        return found == blobs
    except (tarfile.TarError, OSError, EOFError):
        return False


def materialize(repo, rev, day, temp):
    """Fetch the day's blobs in one batch, even in a blobless partial clone."""
    work = Path(temp) / 'materialize'
    git(repo, 'worktree', 'add', '--no-checkout', '--detach', str(work), rev)
    try:
        git(work, 'sparse-checkout', 'set', '--no-cone', f'/{day_path(day)}/')
        git(work, 'read-tree', '-mu', 'HEAD')
    finally:
        git(repo, 'worktree', 'remove', '--force', str(work))


def build(repo, tree, day, out):
    """Deterministic tarball: archiving the folder's tree with a fixed mtime gives the same bytes."""
    mtime = f'{day + timedelta(days=1):%Y-%m-%d}T00:00:00Z'
    with open(out, 'wb') as f:
        archive = subprocess.Popen(['git', '-C', str(repo), 'archive', '--format=tar',
                                    f'--mtime={mtime}', f'--prefix={day_path(day)}/', tree],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        xz = subprocess.run(['xz', '-9', '-T2', '-c'], stdin=archive.stdout, stdout=f,
                            stderr=subprocess.DEVNULL, timeout=1800)
        archive.stdout.close()
        if archive.wait(timeout=60) or xz.returncode:
            raise RuntimeError(f'could not build {out.name}')


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class GitHubReleases:
    """Release assets through the gh CLI and the job's built-in token."""

    def __init__(self, repository):
        self.repository = repository

    def gh(self, *args, check=True):
        result = subprocess.run(['gh', *args, '--repo', self.repository], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=600, text=True)
        if check and result.returncode:
            raise RuntimeError(f'gh {" ".join(args[:2])} failed (exit {result.returncode})')
        return result

    def url(self, tag, name):
        return f'https://github.com/{self.repository}/releases/download/{tag}/{name}'

    def download(self, tag, name, directory):
        """Path of the downloaded asset, or None if the release or asset does not exist."""
        result = self.gh('release', 'download', tag, '--pattern', name, '--dir', str(directory),
                         '--clobber', check=False)
        path = Path(directory) / name
        return path if result.returncode == 0 and path.exists() else None

    def upload(self, tag, path):
        if self.gh('release', 'view', tag, check=False).returncode:
            self.gh('release', 'create', tag, '--title', f'OVATION archive {tag[8:]}', '--notes',
                    'Forecast days moved out of main. Each asset is one OVATION/YYYY/MM/DD folder '
                    'as tar.xz; OVATION/archive.json on main lists them with SHA-256 digests.')
        self.gh('release', 'upload', tag, str(path), '--clobber')


def archive_day(repo, rev, day, tree, releases, temp, dry_run=False):
    """Upload (or reuse) a verified asset for one day and return its index entry."""
    blobs = expected_blobs(repo, rev, day)
    tag, name = release_tag(day), asset_name(day)
    existing_dir = Path(temp) / 'existing'
    existing_dir.mkdir(exist_ok=True)
    existing = None if dry_run else releases.download(tag, name, existing_dir)
    if existing is not None and verify(existing, blobs):
        local = existing
        print(f'{day}: reusing verified asset {name}')
    else:
        materialize(repo, rev, day, temp)
        local = Path(temp) / name
        build(repo, tree, day, local)
        if not verify(local, blobs):
            raise RuntimeError(f'{name} does not match {day_path(day)}')
        if dry_run:
            print(f'{day}: would upload {name} ({local.stat().st_size} bytes, {len(blobs)} files)')
        else:
            releases.upload(tag, local)
            check_dir = Path(temp) / 'check'
            check_dir.mkdir(exist_ok=True)
            uploaded = releases.download(tag, name, check_dir)
            if uploaded is None or sha256(uploaded) != sha256(local):
                raise RuntimeError(f'uploaded {name} does not match the local copy')
            print(f'{day}: uploaded and verified {name}')
    return {'date': day.isoformat(), 'path': day_path(day), 'asset': name, 'release': tag,
            'url': releases.url(tag, name), 'sha256': sha256(local), 'bytes': local.stat().st_size,
            'files': len(blobs), 'tree': tree, 'commit': rev}


def merge_index(old, entries):
    days = {e['date']: e for e in old.get('days', [])}
    days.update({e['date']: e for e in entries})
    return {'schema_version': 1, 'days': [days[d] for d in sorted(days, reverse=True)]}


def remove_days(repo, branch, entries, attempts=5, sleep=time.sleep):
    """One commit on the newest remote head: drop archived folders, update the index."""
    target = 'refs/heads/' + branch
    last_error = None
    for attempt in range(attempts):
        try:
            git(repo, 'fetch', '--no-tags', '--depth=1', 'origin', target, timeout=120)
            head = git(repo, 'rev-parse', 'FETCH_HEAD')
            folders = day_folders(repo, head)
            # Only remove a folder that is still exactly what was archived.
            ready = [e for e in entries if folders.get(date.fromisoformat(e['date'])) == e['tree']]
            for e in entries:
                if e not in ready and date.fromisoformat(e['date']) in folders:
                    print(f'{e["date"]}: folder changed since archiving; kept on {branch}')
            if not ready:
                return False
            with tempfile.TemporaryDirectory(prefix='ovation-archive-') as temp:
                work = Path(temp) / 'work'
                git(repo, 'worktree', 'add', '--no-checkout', '--detach', str(work), head)
                try:
                    git(work, 'sparse-checkout', 'set', '--no-cone', '/.gitattributes', '/' + INDEX)
                    git(work, 'read-tree', '-mu', 'HEAD')
                    git(work, 'rm', '-r', '-q', '--cached', '--sparse', '--',
                        *[e['path'] for e in ready])
                    index = work / INDEX
                    old = collect.read_json(index, {'schema_version': 1, 'days': []})
                    collect.atomic_write(index, collect.encode(merge_index(old, ready)))
                    git(work, 'add', '--sparse', INDEX)
                    git(work, '-c', 'user.name=github-actions[bot]', '-c',
                        'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                        'commit', '-q', '-m',
                        f'data(ovation): move {len(ready)} archived day(s) to release assets')
                    git(work, 'push', 'origin', 'HEAD:' + target, timeout=120)
                    print(f'removed {", ".join(e["date"] for e in ready)} from {branch}')
                    return True
                finally:
                    git(repo, 'worktree', 'remove', '--force', str(work))
        except RuntimeError as exc:
            last_error = exc
        if attempt < attempts - 1:
            print(f'removal attempt {attempt + 1} failed; retrying from remote head', file=sys.stderr)
            sleep(10 * (attempt + 1))
    raise RuntimeError(f'removal failed after {attempts} attempts: {last_error}')


def run(repo, branch, releases, keep_days=5, today=None, limit=31, dry_run=False,
        sleep=time.sleep):
    if keep_days < 4:
        raise ValueError('keep at least 4 days so the last 72 hours stay on main')
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=keep_days - 1)
    git(repo, 'fetch', '--no-tags', '--depth=1', 'origin', 'refs/heads/' + branch, timeout=120)
    rev = git(repo, 'rev-parse', 'FETCH_HEAD')
    folders = day_folders(repo, rev)
    old = sorted(d for d in folders if d < cutoff)[:limit]
    print(f'keeping days from {cutoff} on; {len(old)} older day(s) to archive')
    entries = []
    for day in old:
        with tempfile.TemporaryDirectory(prefix='ovation-day-') as temp:
            entries.append(archive_day(repo, rev, day, folders[day], releases, temp, dry_run))
    if dry_run or not entries:
        return entries
    remove_days(repo, branch, entries, sleep=sleep)
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--branch', required=True)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'),
                        help='owner/name for release assets (default: $GITHUB_REPOSITORY)')
    parser.add_argument('--keep-days', type=int, default=5)
    parser.add_argument('--limit', type=int, default=31, help='most days to archive in one run')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.repository:
        parser.error('--repository or GITHUB_REPOSITORY is required')
    run(args.repo.resolve(), args.branch, GitHubReleases(args.repository), args.keep_days,
        limit=args.limit, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.exit(f'ERROR: {exc}')
