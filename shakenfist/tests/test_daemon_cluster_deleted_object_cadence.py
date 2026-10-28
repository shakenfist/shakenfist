# Copyright 2026 Michael Still and contributors
#
# Tests for the cadence of the deleted-object sweep. The maintenance loop
# runs due jobs once per 60 seconds, and schedule sets a job's next run to
# its interval after the job finishes, so a sweep which runs for 20 seconds
# and is registered every minute misses every other pass.

import datetime
import types
from unittest import mock

import schedule

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.daemons.cluster import scheduled_tasks
from shakenfist.tests import base


class FakeClock:
    def __init__(self):
        self.start = datetime.datetime(2026, 1, 1, 0, 0, 0)
        self.current = self.start

    def advance(self, seconds):
        self.current += datetime.timedelta(seconds=seconds)

    def now(self, tz=None):
        return self.current


class DeletedObjectSweepCadenceTestCase(base.ShakenFistTestCase):
    def test_sweep_runs_on_consecutive_maintenance_passes(self):
        clock = FakeClock()
        now = mock.Mock(side_effect=clock.now)
        fake_datetime = types.SimpleNamespace(
            datetime=mock.Mock(now=now), time=datetime.time,
            timedelta=datetime.timedelta, date=datetime.date)

        runs = []

        def sweep():
            runs.append(clock.current)
            clock.advance(20)

        with mock.patch('schedule.datetime', fake_datetime), \
                mock.patch.object(scheduled_tasks, 'per_deleted_object_checks', sweep), \
                mock.patch.object(scheduled_tasks, 'publish_deleted_object_backlog'):
            sched = schedule.Scheduler()
            m = cluster_main.Monitor.__new__(cluster_main.Monitor)
            m._register_deleted_object_jobs(sched)

            for _ in range(3):
                # Passes fall at 60 second boundaries whatever the sweep took.
                elapsed = (clock.current - clock.start).total_seconds()
                clock.advance(60 - elapsed % 60)
                sched.run_pending()

        self.assertEqual(3, len(runs))
        # The fake clock is what schedule read. If schedule reads the time
        # some other way this fails, rather than the test passing or
        # failing against the real clock.
        self.assertTrue(now.called)
