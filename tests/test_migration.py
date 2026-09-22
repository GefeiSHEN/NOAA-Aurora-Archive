import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta

from agents import migrate_ovation
from scripts import collect
from test_collect import NOW, raw, fixture


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = raw() + b'\n'
        self.path = '2026/09/22/20260922T005500Z.json'
        self.new_path = 'OVATION/2026/09/22/20260922T013500Z.json'
        payload = self.root / self.path
        payload.parent.mkdir(parents=True)
        payload.write_bytes(self.body)
        self.entry = {**collect.validate(self.body), **collect.receipt(NOW, {}),
                      'path': self.path, 'sha256': hashlib.sha256(self.body).hexdigest()}
        self.metadata = self.root / 'metadata/2026/09/22.json'
        self.metadata.parent.mkdir(parents=True)
        self.metadata.write_bytes(collect.encode({'schema_version': 1, 'snapshots': [self.entry]}))
        (self.root / 'recent.json').write_bytes(collect.encode(
            {'schema_version': 1, 'window_hours': 24, 'snapshots': [self.entry]}))
        (self.root / 'latest.json').write_bytes(collect.encode(
            {'schema_version': 1, 'snapshot': self.entry}))

    def state(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_bytes_references_and_duplicate_behavior_survive_migration(self):
        self.assertEqual(migrate_ovation.migrate(self.root, now=NOW), 1)
        self.assertEqual((self.root / self.new_path).read_bytes(), self.body)
        self.assertFalse((self.root / '2026').exists())
        self.assertFalse((self.root / 'metadata').exists())
        for name in ('latest.json', 'recent.json', '2026/09/22/metadata.json'):
            index = json.loads((self.root / 'OVATION' / name).read_bytes())
            entry = index.get('snapshot') or index['snapshots'][0]
            self.assertEqual(entry, {**self.entry, 'path': self.new_path})
        before = self.state()
        self.assertFalse(collect.archive(self.root, self.body, collect.receipt(NOW, {}), now=NOW))
        self.assertEqual(migrate_ovation.migrate(self.root, now=NOW), 0)
        self.assertEqual(before, self.state())

    def test_conflict_is_rejected_before_any_files_move(self):
        target = self.root / self.new_path
        target.parent.mkdir(parents=True)
        target.write_bytes(b'other data')
        before = self.state()
        with self.assertRaises(FileExistsError):
            migrate_ovation.migrate(self.root, now=NOW)
        self.assertEqual(before, self.state())

    def test_bad_hash_and_dangling_index_leave_all_files_untouched(self):
        for change in ({'sha256': '0' * 64}, {'path': '../outside.json'}):
            with self.subTest(change=change):
                self.metadata.write_bytes(collect.encode(
                    {'schema_version': 1, 'snapshots': [{**self.entry, **change}]}))
                before = self.state()
                with self.assertRaises(ValueError):
                    migrate_ovation.migrate(self.root, now=NOW)
                self.assertEqual(before, self.state())

    def test_unindexed_payload_is_not_silently_moved(self):
        (self.root / '2026/09/22/20260922T010000Z.json').write_bytes(raw())
        before = self.state()
        with self.assertRaises(ValueError):
            migrate_ovation.migrate(self.root, now=NOW)
        self.assertEqual(before, self.state())

    def test_same_forecast_from_two_observations_preserves_both_as_revisions(self):
        body = raw(fixture(observation='2026-09-22T01:00:00Z'))
        old_path = '2026/09/22/20260922T010000Z.json'
        (self.root / old_path).write_bytes(body)
        entry = {**collect.validate(body), **collect.receipt(NOW + timedelta(minutes=1), {}),
                 'path': old_path, 'sha256': hashlib.sha256(body).hexdigest()}
        self.metadata.write_bytes(collect.encode({'schema_version': 1, 'snapshots': [self.entry, entry]}))
        self.assertEqual(migrate_ovation.migrate(self.root, now=NOW + timedelta(minutes=2)), 2)
        revised_path = self.new_path[:-5] + '-' + entry['sha256'] + '.json'
        self.assertEqual((self.root / self.new_path).read_bytes(), self.body)
        self.assertEqual((self.root / revised_path).read_bytes(), body)
        latest = json.loads((self.root / 'OVATION/latest.json').read_bytes())['snapshot']
        self.assertEqual(latest['path'], revised_path)
