from datetime import date
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import archive_days, collect, publish, reset_history
from test_archive_days import FakeReleases
from test_collect import NOW, fixture, raw

TODAY = date(2026, 11, 1)


def git(path, *args):
    return archive_days.git(path, *args)


class ResetHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.remote, self.repo, self.other = root / 'remote', root / 'repo', root / 'other'
        self.releases = FakeReleases(root / 'releases')
        subprocess.run(['git', 'init', '--bare', '-b', 'main', str(self.remote)],
                       check=True, capture_output=True)
        subprocess.run(['git', 'clone', '-q', str(self.remote), str(self.other)],
                       check=True, capture_output=True)
        for key, value in (('user.name', 'Test'), ('user.email', 'test@example.invalid')):
            git(self.other, 'config', key, value)
        git(self.other, 'checkout', '-q', '-b', 'main')
        (self.other / '.gitattributes').write_text('/OVATION/[0-9][0-9][0-9][0-9]/** -text\n')
        (self.other / 'README.md').write_text('code')
        for day in range(1, 11):  # 2026-10-01 .. 2026-10-10
            folder = self.other / f'OVATION/2026/10/{day:02d}'
            folder.mkdir(parents=True)
            (folder / f'202610{day:02d}T000000Z.json').write_bytes(b'{"day": %d}\r\n' % day)
        (self.other / 'OVATION/latest.json').write_text('{"schema_version": 1, "snapshot": null}\n')
        git(self.other, 'add', '.')
        git(self.other, 'commit', '-q', '-m', 'baseline')
        git(self.other, 'push', '-q', 'origin', 'main')
        subprocess.run(['git', 'clone', '-q', f'file://{self.remote}', str(self.repo)],
                       check=True, capture_output=True)

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, observed='2026-09-22T01:00:00Z', forecast='2026-09-22T01:35:00Z'):
        return publish.publish(self.repo, 'data', raw(fixture(observed, forecast)),
                               collect.receipt(NOW, {}), sleep=lambda _: None, seed_from='main')

    def files(self, rev):
        return git(self.remote, 'ls-tree', '-r', '--name-only', rev).splitlines()

    def collector_commit(self, name):
        git(self.other, 'fetch', '-q', 'origin', 'data')
        git(self.other, 'checkout', '-q', '-B', 'data', 'FETCH_HEAD')
        (self.other / 'OVATION/2026/10/10' / name).write_text('{}\n')
        git(self.other, 'add', '.')
        git(self.other, 'commit', '-q', '-m', 'collector')
        git(self.other, 'push', '-q', 'origin', 'data')

    def reset(self, **kwargs):
        return reset_history.run(self.repo, 'data', 'main', self.releases, today=TODAY,
                                 sleep=lambda _: None, **kwargs)

    def restored(self, names):
        """Head restored by fetching the uploaded bundles, oldest first, into an empty repo."""
        target = Path(self.temp.name) / 'restore.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(target)], check=True)
        for name in names:
            git(target, 'fetch', '-q', str(self.releases.root / reset_history.TAG / name),
                f'{reset_history.REF}:{reset_history.REF}')
        return git(target, 'rev-parse', reset_history.REF)

    def test_publisher_starts_the_data_branch_from_main(self):
        self.assertTrue(self.publish())
        root = git(self.remote, 'rev-list', '--max-parents=0', 'data')
        self.assertEqual(git(self.remote, 'ls-tree', '--name-only', root).splitlines(),
                         ['.gitattributes', 'OVATION'])
        self.assertEqual(git(self.remote, 'rev-parse', f'{root}:OVATION'),
                         git(self.remote, 'rev-parse', 'main:OVATION'))
        self.assertIn('OVATION/2026/09/22/20260922T013500Z.json', self.files('data'))
        self.assertNotIn('README.md', self.files('data'))
        # Once the branch exists, later snapshots just add to it.
        self.assertTrue(self.publish('2026-09-22T01:05:00Z', '2026-09-22T01:40:00Z'))
        self.assertEqual(git(self.remote, 'rev-list', '--count', 'data'), '3')

    def test_publisher_will_not_start_an_empty_data_branch(self):
        git(self.other, 'rm', '-r', '-q', 'OVATION')
        git(self.other, 'commit', '-q', '-m', 'gone')
        git(self.other, 'push', '-q', 'origin', 'main')
        with self.assertRaises(RuntimeError):
            self.publish()

    def test_saves_history_then_starts_data_afresh_and_retires_main_copy(self):
        self.publish()
        archive_days.run(self.repo, 'data', self.releases, today=date(2026, 10, 10),
                         sleep=lambda _: None)
        self.publish('2026-09-22T01:05:00Z', '2026-09-22T01:40:00Z')
        old = git(self.remote, 'rev-parse', 'data')
        self.assertGreater(int(git(self.remote, 'rev-list', '--count', old)), 2)
        commit = self.reset()
        self.assertEqual(git(self.remote, 'rev-parse', 'data'), commit)
        self.assertEqual(git(self.remote, 'rev-list', '--count', 'data'), '1')
        self.assertEqual(git(self.remote, 'rev-parse', 'data^{tree}'),
                         git(self.remote, 'rev-parse', old + '^{tree}'))
        self.assertIn(old, git(self.remote, 'log', '-1', '--format=%B', 'data'))
        bundles = sorted(self.releases.assets(reset_history.TAG))
        self.assertEqual(len(bundles), 1)
        self.assertEqual(self.restored(bundles), old)
        main = self.files('main')
        self.assertIn('README.md', main)
        self.assertFalse(any(f.startswith('OVATION/') for f in main))
        # Main kept its history: the retirement is an ordinary commit on top.
        self.assertEqual(git(self.remote, 'rev-list', '--count', 'main'), '2')
        # A second run has nothing to do.
        self.assertIsNone(self.reset())

    def test_keeps_commits_that_land_during_the_reset(self):
        self.publish()
        self.publish('2026-09-22T01:05:00Z', '2026-09-22T01:40:00Z')
        def collector(attempt):
            if attempt == 0:
                self.collector_commit('late.json')
        commit = self.reset(before_push=collector)
        self.assertIn('OVATION/2026/10/10/late.json', self.files(commit))
        self.assertEqual(git(self.remote, 'rev-list', '--count', 'data'), '1')
        bundles = sorted(self.releases.uploads)
        self.assertEqual(len(bundles), 2)
        full = [b for b in self.releases.uploads if not b.endswith('-part.bundle')]
        part = [b for b in self.releases.uploads if b.endswith('-part.bundle')]
        self.assertEqual(self.restored(full + part), git(self.other, 'rev-parse', 'HEAD'))

    def test_refuses_when_history_holds_an_unarchived_day(self):
        self.publish()
        git(self.other, 'fetch', '-q', 'origin', 'data')
        git(self.other, 'checkout', '-q', '-B', 'data', 'FETCH_HEAD')
        git(self.other, 'rm', '-r', '-q', 'OVATION/2026/10/02')
        git(self.other, 'commit', '-q', '-m', 'lost day')
        git(self.other, 'push', '-q', 'origin', 'data')
        head = git(self.remote, 'rev-parse', 'data')
        with self.assertRaisesRegex(RuntimeError, '2026-10-02'):
            self.reset()
        self.assertEqual(git(self.remote, 'rev-parse', 'data'), head)
        self.assertEqual(self.releases.uploads, [])

    def test_refuses_when_main_changed_after_the_data_branch_started(self):
        self.publish()
        self.publish('2026-09-22T01:05:00Z', '2026-09-22T01:40:00Z')
        git(self.other, 'pull', '-q', 'origin', 'main')
        (self.other / 'OVATION/2026/10/09/stray.json').write_text('{}\n')
        git(self.other, 'add', '.')
        git(self.other, 'commit', '-q', '-m', 'stray')
        git(self.other, 'push', '-q', 'origin', 'main')
        head = git(self.remote, 'rev-parse', 'data')
        with self.assertRaisesRegex(RuntimeError, 'by hand'):
            self.reset()
        self.assertEqual(git(self.remote, 'rev-parse', 'data'), head)
        self.assertIn('OVATION/2026/10/09/stray.json', self.files('main'))

    def test_dry_run_changes_nothing_and_main_is_never_rewritten(self):
        self.publish()
        self.publish('2026-09-22T01:05:00Z', '2026-09-22T01:40:00Z')
        (self.other / 'README.md').write_text('code change')
        git(self.other, 'commit', '-q', '-am', 'code change')
        git(self.other, 'push', '-q', 'origin', 'main')
        heads = {b: git(self.remote, 'rev-parse', b) for b in ('main', 'data')}
        self.assertIsNotNone(self.reset(dry_run=True))
        self.assertIsNotNone(reset_history.run(self.repo, 'main', 'main', self.releases,
                                               today=TODAY, dry_run=True))
        self.assertEqual({b: git(self.remote, 'rev-parse', b) for b in heads}, heads)
        self.assertEqual(self.releases.uploads, [])
        with self.assertRaises(ValueError):
            reset_history.run(self.repo, 'main', 'main', self.releases, today=TODAY)


if __name__ == '__main__':
    unittest.main()
