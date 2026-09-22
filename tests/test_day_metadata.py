from pathlib import Path
import tempfile
import unittest

from agents import move_day_metadata
from scripts import collect
from test_collect import NOW, raw


class DayMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        collect.archive(self.root, raw(), collect.receipt(NOW, {}), now=NOW)
        self.target = self.root / 'OVATION/2026/09/22/metadata.json'
        self.source = self.root / 'OVATION/metadata/2026/09/22.json'
        self.source.parent.mkdir(parents=True)
        self.target.rename(self.source)

    def state(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_only_metadata_location_changes_and_collector_reuses_it(self):
        before = self.state()
        self.assertEqual(move_day_metadata.migrate(self.root), 1)
        expected = dict(before)
        expected['OVATION/2026/09/22/metadata.json'] = expected.pop('OVATION/metadata/2026/09/22.json')
        self.assertEqual(self.state(), expected)
        self.assertFalse((self.root / 'OVATION/metadata').exists())
        self.assertFalse(collect.archive(self.root, raw(), collect.receipt(NOW, {}), now=NOW))
        self.assertEqual(move_day_metadata.migrate(self.root), 0)

    def test_conflict_leaves_existing_files_untouched(self):
        self.target.write_bytes(b'keep me')
        before = self.state()
        with self.assertRaises(FileExistsError):
            move_day_metadata.migrate(self.root)
        self.assertEqual(before, self.state())
