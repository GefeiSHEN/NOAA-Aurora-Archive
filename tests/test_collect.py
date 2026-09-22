import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

from scripts import collect

UTC = timezone.utc
NOW = datetime(2026, 9, 22, 1, tzinfo=UTC)


def fixture(observation='2026-09-22T00:55:00Z', forecast='2026-09-22T01:35:00Z'):
    return {'Observation Time': observation, 'Forecast Time': forecast,
            'Data Format': '[Longitude, Latitude, Aurora]', 'type': 'MultiPoint',
            'coordinates': [[lon, lat, 0] for lon in range(360) for lat in range(-90, 91)]}


def raw(data=None):
    return json.dumps(data if data is not None else fixture()).encode()


class ValidationTests(unittest.TestCase):
    def test_complete_grid_and_utc_path(self):
        info = collect.validate(raw())
        self.assertEqual(info['observation_time'], '2026-09-22T00:55:00Z')
        self.assertEqual(collect.snapshot_path(info['forecast_time']),
                         'OVATION/2026/09/22/20260922T013500Z.json')
        data = fixture('2026-09-22T00:55:00+02:00', '2026-09-22T01:35:00+02:00')
        self.assertEqual(collect.snapshot_path(collect.validate(raw(data))['forecast_time']),
                         'OVATION/2026/09/21/20260921T233500Z.json')

    def test_reject_bad_timestamps(self):
        for value in ('bad', '2026-09-22', '2026-09-22T00:55:00',
                      '2026-02-30T00:00:00Z', '2026-09-22T00:55:00.5Z'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                collect.validate(raw(fixture(observation=value)))
        with self.assertRaises(ValueError):
            collect.validate(raw(fixture(forecast='2026-09-21T00:00:00Z')))

    def test_reject_malformed_incomplete_or_non_numeric(self):
        mutations = [lambda d: d.update(type='Point'), lambda d: d.pop('Forecast Time'),
                     lambda d: d['coordinates'].pop(),
                     lambda d: d.update(coordinates=[c for c in d['coordinates'] if c[1] >= 0]),
                     lambda d: d['coordinates'].__setitem__(0, d['coordinates'][1]),
                     lambda d: d['coordinates'].__setitem__(0, [0, -90]),
                     lambda d: d['coordinates'].__setitem__(0, [0, -90, True]),
                     lambda d: d['coordinates'].__setitem__(0, [0, -90, 101]),
                     lambda d: d['coordinates'].__setitem__(0, [360, -90, 0]),
                     lambda d: d['coordinates'].__setitem__(0, [0, -90, float('nan')]),
                     lambda d: d['coordinates'].__setitem__(0, [0, -90, 10 ** 400])]
        for mutate in mutations:
            data = fixture()
            mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                collect.validate(raw(data))
        for body in (b'{', b'[]', b'{"type":1,"type":2}'):
            with self.assertRaises(ValueError):
                collect.validate(body)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def save(self, body=None, now=NOW):
        return collect.archive(self.root, body or raw(),
                               collect.receipt(now, {'ETag': '"abc"', 'Last-Modified': 'date'}), now=now)

    def state(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_preserves_bytes_metadata_and_duplicate_noop(self):
        body = raw() + b'\n'
        self.assertTrue(self.save(body))
        entry = json.loads((self.root / 'OVATION/latest.json').read_text())['snapshot']
        self.assertEqual((self.root / entry['path']).read_bytes(), body)
        self.assertEqual(entry['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(entry['http_validators']['etag'], '"abc"')
        before = self.state()
        self.assertFalse(self.save(body, NOW + timedelta(minutes=5)))
        self.assertEqual(before, self.state())

    def test_revision_and_reverted_duplicate(self):
        self.save()
        original = self.state()
        data = fixture()
        data['coordinates'][0][2] = 10
        revised = raw(data)
        self.save(revised, NOW + timedelta(minutes=5))
        entry = json.loads((self.root / 'OVATION/latest.json').read_text())['snapshot']
        self.assertTrue(entry['path'].endswith('-' + hashlib.sha256(revised).hexdigest() + '.json'))
        self.assertEqual(original['OVATION/2026/09/22/20260922T013500Z.json'],
                         (self.root / 'OVATION/2026/09/22/20260922T013500Z.json').read_bytes())
        before = self.state()
        self.assertFalse(self.save(now=NOW + timedelta(minutes=10)))
        self.assertEqual(before, self.state())

    def test_latest_does_not_regress_and_recent_orders_by_collection(self):
        self.save()
        self.save(raw(fixture('2026-09-22T01:00:00Z', '2026-09-22T01:30:00Z')),
                  NOW + timedelta(minutes=1))
        latest = json.loads((self.root / 'OVATION/latest.json').read_text())['snapshot']
        recent = json.loads((self.root / 'OVATION/recent.json').read_text())['snapshots']
        self.assertEqual(latest['observation_time'], '2026-09-22T00:55:00Z')
        self.assertEqual(latest['forecast_time'], '2026-09-22T01:35:00Z')
        self.assertEqual(recent[0]['forecast_time'], '2026-09-22T01:30:00Z')

    def test_same_forecast_new_observation_is_a_revision(self):
        self.save()
        body = raw(fixture('2026-09-22T01:00:00Z'))
        self.save(body, NOW + timedelta(minutes=5))
        entry = json.loads((self.root / 'OVATION/latest.json').read_bytes())['snapshot']
        self.assertEqual(entry['path'], 'OVATION/2026/09/22/20260922T013500Z-' +
                         hashlib.sha256(body).hexdigest() + '.json')
        self.assertFalse((self.root / 'OVATION/2026/09/22/20260922T010000Z.json').exists())

    def test_metadata_day_uses_forecast_date(self):
        self.save(raw(fixture(forecast='2026-09-23T00:35:00Z')))
        self.assertTrue((self.root / 'OVATION/2026/09/23/metadata.json').exists())
        self.assertFalse((self.root / 'OVATION/metadata').exists())

    def test_recent_prunes_on_unchanged_response_without_refreshing_collection(self):
        self.save()
        self.assertTrue(self.save(now=NOW + timedelta(hours=24, seconds=1)))
        self.assertEqual(json.loads((self.root / 'OVATION/recent.json').read_text())['snapshots'], [])
        self.assertEqual(json.loads((self.root / 'OVATION/latest.json').read_text())['snapshot']['collected_at'],
                         '2026-09-22T01:00:00Z')

    def test_invalid_response_leaves_every_file_unchanged(self):
        self.save()
        before = self.state()
        with self.assertRaises(ValueError):
            self.save(b'{')
        self.assertEqual(before, self.state())

    def test_exhausted_invalid_download_does_not_touch_existing_archive(self):
        self.save()
        before = self.state()
        with self.assertRaises(RuntimeError):
            body, received = collect.download(fetch=lambda: (b'{', {}),
                                               sleep=lambda _: None, clock=lambda: NOW)
            collect.archive(self.root, body, received)
        self.assertEqual(before, self.state())

    def test_exact_recent_boundary_is_inclusive(self):
        self.save()
        self.assertFalse(self.save(now=NOW + timedelta(hours=24)))
        self.assertEqual(len(json.loads((self.root / 'OVATION/recent.json').read_text())['snapshots']), 1)

    def test_delayed_receipt_cannot_replace_newer_revision(self):
        data = fixture()
        data['coordinates'][0][2] = 5
        self.save(raw(data), NOW + timedelta(minutes=10))
        collect.archive(self.root, raw(), collect.receipt(NOW, {}), now=NOW + timedelta(minutes=11))
        latest = json.loads((self.root / 'OVATION/latest.json').read_text())['snapshot']
        self.assertEqual(latest['sha256'], hashlib.sha256(raw(data)).hexdigest())


class RetryTests(unittest.TestCase):
    def test_network_invalid_429_then_success(self):
        fetch = Mock(side_effect=[URLError('offline'), (b'{', {}),
                                 HTTPError('url', 429, 'busy', {'Retry-After': '99999'}, None),
                                 (raw(), {'ETag': 'ok'})])
        sleep = Mock()
        body, receipt = collect.download(fetch=fetch, sleep=sleep, clock=lambda: NOW)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [15, 30, 120])
        self.assertEqual(receipt['collected_at'], '2026-09-22T01:00:00Z')
        self.assertEqual(body, raw())

    def test_retry_after_date_and_invalid(self):
        self.assertEqual(collect.retry_after('Tue, 22 Sep 2026 01:01:00 GMT', NOW), 60)
        self.assertEqual(collect.retry_after('nonsense', NOW), 0)

    def test_exhaustion_and_no_retry_for_permanent_http_error(self):
        for failure, calls in ((URLError('offline'), 4),
                               (HTTPError('url', 503, 'busy', {}, None), 4),
                               (HTTPError('url', 404, 'missing', {}, None), 1)):
            fetch = Mock(side_effect=failure)
            with self.assertRaises(RuntimeError):
                collect.download(fetch=fetch, sleep=lambda _: None, clock=lambda: NOW)
            self.assertEqual(fetch.call_count, calls)


if __name__ == '__main__':
    unittest.main()
