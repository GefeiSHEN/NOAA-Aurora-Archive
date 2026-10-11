from datetime import date
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import archive_days, collect


class FakeReleases:
    def __init__(self, root, on_upload=None):
        self.root = Path(root)
        self.uploads = []
        self.on_upload = on_upload

    def url(self, tag, name):
        return f'https://example.invalid/{tag}/{name}'

    def download(self, tag, name, directory):
        source = self.root / tag / name
        if not source.exists():
            return None
        return Path(shutil.copy(source, Path(directory) / name))

    def upload(self, tag, path):
        (self.root / tag).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, self.root / tag / path.name)
        self.uploads.append(path.name)
        if self.on_upload:
            self.on_upload(path.name)


def git(path, *args):
    return archive_days.git(path, *args)


class ArchiveDaysTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.remote, self.repo, self.other = root / 'remote', root / 'repo', root / 'other'
        self.releases = FakeReleases(root / 'releases')
        subprocess.run(['git', 'init', '--bare', '-b', 'main', str(self.remote)],
                       check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(self.remote), str(self.other)],
                       check=True, capture_output=True)
        git(self.other, 'config', 'user.name', 'Test')
        git(self.other, 'config', 'user.email', 'test@example.invalid')
        git(self.other, 'checkout', '-q', '-b', 'main')
        (self.other / '.gitattributes').write_text('/OVATION/[0-9][0-9][0-9][0-9]/** -text\n')
        (self.other / 'README.md').write_text('baseline')
        for day in range(1, 11):  # 2026-10-01 .. 2026-10-10
            folder = self.other / f'OVATION/2026/10/{day:02d}'
            folder.mkdir(parents=True)
            (folder / f'202610{day:02d}T000000Z.json').write_bytes(b'{"day": %d}\r\n' % day)
            (folder / 'metadata.json').write_text('{"schema_version": 1, "snapshots": []}\n')
        (self.other / 'OVATION/latest.json').write_text('{}\n')
        git(self.other, 'add', '.')
        git(self.other, 'commit', '-q', '-m', 'baseline')
        git(self.other, 'push', '-q', 'origin', 'main')
        subprocess.run(['git', 'clone', '-q', '--depth=1', f'file://{self.remote}', str(self.repo)],
                       check=True, capture_output=True)

    def tearDown(self):
        self.temp.cleanup()

    def remote_files(self):
        return git(self.remote, 'ls-tree', '-r', '--name-only', 'main').splitlines()

    def run_archiver(self, **kwargs):
        return archive_days.run(self.repo, 'main', self.releases, today=date(2026, 10, 10),
                                sleep=lambda _: None, **kwargs)

    def test_moves_days_before_the_window_into_verified_assets(self):
        entries = self.run_archiver()
        self.assertEqual([e['date'] for e in entries],
                         ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05'])
        files = self.remote_files()
        self.assertFalse(any(f.startswith(('OVATION/2026/10/01/', 'OVATION/2026/10/05/'))
                             for f in files))
        for day in range(6, 11):
            self.assertIn(f'OVATION/2026/10/{day:02d}/metadata.json', files)
        self.assertIn('README.md', files)
        self.assertIn('OVATION/latest.json', files)
        index = collect.json.loads(git(self.remote, 'show', 'main:' + archive_days.INDEX))
        self.assertEqual(index['days'][0]['date'], '2026-10-05')
        for entry in index['days']:
            day = date.fromisoformat(entry['date'])
            asset = self.releases.root / entry['release'] / entry['asset']
            self.assertEqual(archive_days.sha256(asset), entry['sha256'])
            # The recorded commit still holds the folder, byte for byte (CRLF included).
            blobs = archive_days.expected_blobs(self.remote, entry['commit'], day)
            self.assertTrue(archive_days.verify(asset, blobs))

    def test_second_run_has_nothing_to_do(self):
        self.run_archiver()
        head = git(self.remote, 'rev-parse', 'main')
        self.assertEqual(self.run_archiver(), [])
        self.assertEqual(git(self.remote, 'rev-parse', 'main'), head)

    def test_reuses_a_good_asset_and_replaces_a_corrupt_one(self):
        self.run_archiver(dry_run=True)
        self.assertEqual(self.releases.uploads, [])
        self.run_archiver(limit=1)
        good = self.releases.root / 'ovation-2026-10' / 'OVATION-2026-10-01.tar.xz'
        # Pretend the removal push for 10-02 failed after a corrupt upload.
        (good.parent / 'OVATION-2026-10-02.tar.xz').write_bytes(b'not an archive')
        self.releases.uploads.clear()
        self.run_archiver()
        self.assertEqual(self.releases.uploads, ['OVATION-2026-10-02.tar.xz', 'OVATION-2026-10-03.tar.xz',
                                                 'OVATION-2026-10-04.tar.xz', 'OVATION-2026-10-05.tar.xz'])

    def test_survives_a_collector_commit_and_keeps_a_changed_folder(self):
        def collector(name):
            if name == 'OVATION-2026-10-05.tar.xz':
                git(self.other, 'pull', '-q', '--ff-only')
                (self.other / 'OVATION/2026/10/04/late.json').write_text('{}\n')
                (self.other / 'OVATION/2026/10/10/new.json').write_text('{}\n')
                git(self.other, 'add', '.')
                git(self.other, 'commit', '-q', '-m', 'collector')
                git(self.other, 'push', '-q', 'origin', 'main')
        self.releases.on_upload = collector
        self.run_archiver()
        files = self.remote_files()
        self.assertIn('OVATION/2026/10/10/new.json', files)
        self.assertIn('OVATION/2026/10/04/late.json', files)
        self.assertNotIn('OVATION/2026/10/03/metadata.json', files)
        index = collect.json.loads(git(self.remote, 'show', 'main:' + archive_days.INDEX))
        self.assertNotIn('2026-10-04', [e['date'] for e in index['days']])

    def test_refuses_a_window_shorter_than_72_hours(self):
        with self.assertRaises(ValueError):
            self.run_archiver(keep_days=3)


if __name__ == '__main__':
    unittest.main()
