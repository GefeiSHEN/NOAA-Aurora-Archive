#!/usr/bin/env python3
"""Start the data branch on a fresh, parentless commit once a month so its history stops growing.

Nothing is lost. Before the reset, every day folder ever removed from the branch must be listed in
OVATION/archive.json with its asset present on the release, and the branch's whole history is saved
as a Git bundle on release ovation-history, downloaded back and fetched into an empty repository.
The new commit holds exactly the old head's files, so the live archive and its URLs do not change.
Only a separate data branch is ever rewritten; the default branch keeps its code history.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

try:
    from . import archive_days
except ImportError:
    import archive_days

git = archive_days.git
TAG = 'ovation-history'
REF = 'refs/ovation/history'
MAX_ASSET = 2_000_000_000  # GitHub's per-asset limit is 2 GiB.
BOT = ['-c', 'user.name=github-actions[bot]',
       '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com']
NOTES = ('Git bundles of the OVATION data branch, one set per monthly history reset. A "part" '
         'bundle continues the one uploaded before it. Restore with: git fetch <file> '
         f"'{REF}:{REF}' (oldest first).")


def fetch(repo, branch):
    """Fetch the branch with its full history and return its head."""
    local = 'refs/remotes/origin/' + branch
    # A shallow clone would hide the start of the branch and leave holes in the bundle.
    deepen = ['--unshallow'] if git(repo, 'rev-parse', '--is-shallow-repository') == 'true' else []
    git(repo, 'fetch', '--no-tags', *deepen, 'origin', f'+refs/heads/{branch}:{local}',
        timeout=1800)
    return git(repo, 'rev-parse', local)


def subtree(repo, rev, path):
    """Object id of a top-level entry at rev, or None."""
    line = git(repo, 'ls-tree', rev, path)
    return line.split()[2] if line else None


def check_archived(repo, head, releases):
    """Refuse unless every day folder the branch's history dropped is a verified release asset."""
    try:
        index = json.loads(git(repo, 'show', f'{head}:{archive_days.INDEX}'))
    except RuntimeError:
        index = {'days': []}
    archived = {e['date']: e for e in index.get('days', [])}
    present = set(git(repo, 'ls-tree', '-r', '--name-only', head, 'OVATION').splitlines())
    deleted = git(repo, 'log', '--no-renames', '--diff-filter=D', '--name-only', '--format=',
                  head, '--', 'OVATION', timeout=1800).splitlines()
    missing = set()
    for path in deleted:
        match = archive_days.DAY.match(path)
        if match and path not in present and '-'.join(match.groups()) not in archived:
            missing.add('-'.join(match.groups()))
    if missing:
        raise RuntimeError('history holds files from days not in the archive index: '
                           + ', '.join(sorted(missing)))
    by_tag = {}
    for entry in archived.values():
        by_tag.setdefault(entry['release'], []).append(entry)
    for tag, entries in sorted(by_tag.items()):
        assets = releases.assets(tag)
        for entry in entries:
            if assets.get(entry['asset']) != entry['bytes']:
                raise RuntimeError(f'{entry["asset"]} is missing from release {tag} or has the '
                                   'wrong size')
    print(f'{len(archived)} archived day(s) present on their releases')
    return len(archived)


def retire_default(repo, branch, default_branch, head, dry_run, attempts=3, sleep=time.sleep):
    """Remove the frozen OVATION/ copy from the default branch once the data branch started from it."""
    for attempt in range(attempts):
        base = fetch(repo, default_branch)
        frozen = subtree(repo, base, 'OVATION')
        if frozen is None:
            return False
        roots = git(repo, 'rev-list', '--max-parents=0', head).splitlines()
        if frozen not in {subtree(repo, root, 'OVATION') for root in roots}:
            raise RuntimeError(f'OVATION/ on {default_branch} differs from where {branch} started; '
                               f'check it against {branch} and remove it by hand')
        if dry_run:
            print(f'would remove OVATION/ from {default_branch}; {branch} started from it')
            return True
        entries = [line for line in git(repo, 'ls-tree', base).splitlines()
                   if line.split('\t', 1)[1] != 'OVATION']
        tree = git(repo, 'mktree', input=('\n'.join(entries) + '\n').encode())
        commit = git(repo, *BOT, 'commit-tree', tree, '-p', base, '-m',
                     f'data(ovation): remove the frozen archive; it lives on the {branch} branch')
        try:
            git(repo, 'push', 'origin', f'{commit}:refs/heads/{default_branch}', timeout=300)
            print(f'removed OVATION/ from {default_branch}')
            return True
        except RuntimeError as exc:
            if attempt == attempts - 1:
                raise
            print(f'retire attempt {attempt + 1} failed: {exc}; retrying', file=sys.stderr)
            sleep(10)


def save_bundle(repo, tip, base, verify_repo, releases, temp, today, dry_run):
    """Bundle base..tip (or all of tip), prove it restores tip, and upload it unless dry_run."""
    part = '' if base is None else '-part'
    name = f'ovation-data-{today:%Y-%m-%d}-{tip[:12]}{part}.bundle'
    path = Path(temp) / name
    git(repo, 'update-ref', REF, tip)
    git(repo, 'bundle', 'create', str(path), REF, *([] if base is None else ['^' + base]),
        timeout=3600)
    size = path.stat().st_size
    if size > MAX_ASSET:
        raise RuntimeError(f'{name} is {size} bytes, over the release asset limit')
    # Fetching checks every object and that the history is complete back to base.
    git(verify_repo, '-c', 'transfer.fsckObjects=true', 'fetch', '--no-tags', str(path),
        f'{REF}:{REF}', timeout=3600)
    if git(verify_repo, 'rev-parse', REF) != tip:
        raise RuntimeError(f'{name} does not restore {tip}')
    if dry_run:
        print(f'would upload {name} ({size} bytes)')
        return name
    releases.upload(TAG, path, title='OVATION data history', notes=NOTES)
    check = Path(temp) / 'check'
    check.mkdir(exist_ok=True)
    uploaded = releases.download(TAG, name, check)
    if uploaded is None or archive_days.sha256(uploaded) != archive_days.sha256(path):
        raise RuntimeError(f'uploaded {name} does not match the local copy')
    print(f'uploaded and verified {name} ({size} bytes)')
    return name


def reset(repo, branch, head, releases, today, dry_run, attempts=5, sleep=time.sleep,
          before_push=None):
    if git(repo, 'rev-list', '--count', head) == '1':
        print(f'{branch} already starts at {head}; nothing to reset')
        return None
    tip, base, saved, unsaved = head, None, [], True
    with tempfile.TemporaryDirectory(prefix='ovation-history-') as temp:
        verify_repo = Path(temp) / 'verify.git'
        git(Path(temp), 'init', '-q', '--bare', str(verify_repo))
        for attempt in range(attempts):
            if unsaved:
                saved.append(save_bundle(repo, tip, base, verify_repo, releases, temp, today,
                                         dry_run))
                unsaved = False
            message = (f'data(ovation): start a fresh history for {branch}\n\n'
                       f'The previous history ended at {tip}. It is saved on release {TAG} as '
                       + ', '.join(saved) + '.')
            commit = git(repo, *BOT, 'commit-tree', f'{tip}^{{tree}}', '-m', message)
            if dry_run:
                print(f'would replace {branch} at {tip} with {commit}, same files')
                return commit
            if before_push is not None:
                before_push(attempt)
            try:
                # The lease refuses the push if a collector commit landed since tip.
                git(repo, 'push', f'--force-with-lease=refs/heads/{branch}:{tip}', 'origin',
                    f'{commit}:refs/heads/{branch}', timeout=300)
                print(f'{branch} now starts at {commit}; history to {tip} is in {TAG}')
                return commit
            except RuntimeError as exc:
                if attempt == attempts - 1:
                    raise
                print(f'reset attempt {attempt + 1} failed: {exc}; retrying', file=sys.stderr)
            newer = fetch(repo, branch)
            if newer != tip:
                try:
                    git(repo, 'merge-base', '--is-ancestor', tip, newer)
                except RuntimeError:
                    raise RuntimeError(f'{branch} was rewritten by someone else; not resetting')
                # Save only the commits that landed meanwhile, then try again from the new head.
                tip, base, unsaved = newer, tip, True
            sleep(5 * (attempt + 1))


def run(repo, branch, default_branch, releases, today=None, dry_run=False, sleep=time.sleep,
        before_push=None):
    if branch == default_branch and not dry_run:
        raise ValueError(f'only a separate data branch is reset, never {default_branch}; '
                         'set the OVATION_DATA_BRANCH variable first')
    git(repo, 'check-ref-format', 'refs/heads/' + branch)
    today = today or datetime.now(timezone.utc).date()
    head = fetch(repo, branch)
    check_archived(repo, head, releases)
    if branch != default_branch:
        retire_default(repo, branch, default_branch, head, dry_run, sleep=sleep)
    return reset(repo, branch, head, releases, today, dry_run, sleep=sleep, before_push=before_push)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--branch', required=True, help='data branch to reset')
    parser.add_argument('--default-branch', required=True)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'),
                        help='owner/name for release assets (default: $GITHUB_REPOSITORY)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.repository:
        parser.error('--repository or GITHUB_REPOSITORY is required')
    run(args.repo.resolve(), args.branch, args.default_branch,
        archive_days.GitHubReleases(args.repository), dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.exit(f'ERROR: {exc}')
