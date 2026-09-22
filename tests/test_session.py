from datetime import datetime, timedelta, timezone
import unittest

from scripts import session


class Clock:
    def __init__(self, now):
        self.now = now
        self.elapsed = 0

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.elapsed += seconds


class SessionTests(unittest.TestCase):
    def test_utc_slots_and_rollover(self):
        for value, expected in (
            ('2026-09-22T23:58:00+00:00', '2026-09-23T00:00:00+00:00'),
            ('2026-09-22T17:41:59+00:00', '2026-09-22T17:42:00+00:00'),
            ('2026-09-22T12:42:01-05:00', '2026-09-22T17:44:00+00:00'),
        ):
            self.assertEqual(session.next_slot(datetime.fromisoformat(value)).isoformat(), expected)

    def run_session(self, clock, collect, minutes=12):
        return session.run(collect, minutes=minutes, clock=lambda: clock.now,
                           monotonic=lambda: clock.elapsed, sleep=clock.sleep)

    def test_delayed_start_collects_now_then_on_utc_boundaries(self):
        clock = Clock(datetime(2026, 9, 22, 17, 43, tzinfo=timezone.utc))
        calls = []
        self.run_session(clock, lambda _: calls.append(clock.now))
        self.assertEqual([d.strftime('%H:%M') for d in calls],
                         ['17:43', '17:44', '17:46', '17:48', '17:50', '17:52', '17:54'])
        self.assertLessEqual(clock.elapsed, 12 * 60)

    def test_slow_fetch_skips_missed_slots_without_catchup_frames(self):
        clock = Clock(datetime(2026, 9, 22, 17, 42, tzinfo=timezone.utc))
        calls = []
        def collect(_):
            calls.append(clock.now)
            clock.sleep(7 * 60)
        self.run_session(clock, collect, minutes=15)
        self.assertEqual([d.strftime('%H:%M') for d in calls], ['17:42', '17:50'])

    def test_noop_continues_and_failure_stops_visibly(self):
        clock = Clock(datetime(2026, 9, 22, 17, 42, tzinfo=timezone.utc))
        calls = []
        def collect(_):
            calls.append(clock.now)
            if len(calls) == 2:
                raise RuntimeError('retries exhausted')
            return False
        with self.assertRaisesRegex(RuntimeError, 'retries exhausted'):
            self.run_session(clock, collect)
        self.assertEqual(len(calls), 2)

    def test_rejects_unbounded_session(self):
        for minutes in (0, -1, 341):
            with self.assertRaises(ValueError):
                session.run(lambda _: None, minutes=minutes)

    def test_clock_rollback_cannot_extend_monotonic_deadline(self):
        clock = Clock(datetime(2026, 9, 22, 17, 43, tzinfo=timezone.utc))
        calls = []
        def collect(_):
            calls.append(clock.now)
            clock.now -= timedelta(hours=1)
        self.run_session(clock, collect, minutes=1)
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(clock.elapsed, 60)

    def test_shared_cycle_deadline_clips_segment_and_collection_budget(self):
        clock = Clock(datetime(2026, 9, 22, 17, 42, tzinfo=timezone.utc))
        budgets = []
        session.run(budgets.append, minutes=300, until=clock.now.timestamp() + 150,
                    clock=lambda: clock.now, monotonic=lambda: clock.elapsed, sleep=clock.sleep)
        self.assertEqual(budgets, [150, 30])
        self.assertEqual(clock.elapsed, 150)

    def test_expired_cycle_does_not_fetch(self):
        clock = Clock(datetime(2026, 9, 22, 17, 42, tzinfo=timezone.utc))
        calls = []
        session.run(calls.append, until=clock.now.timestamp() - 1,
                    clock=lambda: clock.now, monotonic=lambda: clock.elapsed, sleep=clock.sleep)
        self.assertEqual(calls, [])
