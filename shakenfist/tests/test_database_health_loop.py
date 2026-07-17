# Copyright 2019 Michael Still and contributors

from unittest import mock

from grpc_health.v1 import health_pb2

from shakenfist.daemons.database import main as database_main
from shakenfist.tests import base


class _RecordingHealthServicer:
    """Minimal stand-in for grpc_health's HealthServicer.

    Records every set() call so tests can assert on the reported status
    without standing up a real gRPC server.
    """

    def __init__(self):
        self.calls = []

    def set(self, service, status):
        self.calls.append((service, status))


def _make_monitor(health_servicer):
    """Build a Monitor without running its heavy __init__.

    The real __init__ registers Prometheus metrics globally and starts an
    HTTP metrics server, neither of which is relevant to the health-update
    logic under test. We construct via __new__ and set only the attributes
    _update_health touches.
    """
    m = database_main.Monitor.__new__(database_main.Monitor)
    m.health_servicer = health_servicer
    m._last_health_status = None
    return m


class DatabaseHealthLoopTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.check_reachable', return_value=True)
    def test_reachable_reports_serving(self, _mock_reachable):
        servicer = _RecordingHealthServicer()
        m = _make_monitor(servicer)

        m._update_health()

        self.assertEqual(
            servicer.calls,
            [('', health_pb2.HealthCheckResponse.SERVING)])
        self.assertEqual(
            m._last_health_status,
            health_pb2.HealthCheckResponse.SERVING)

    @mock.patch('shakenfist.mariadb.check_reachable', return_value=False)
    def test_unreachable_reports_not_serving(self, _mock_reachable):
        servicer = _RecordingHealthServicer()
        m = _make_monitor(servicer)

        m._update_health()

        self.assertEqual(
            servicer.calls,
            [('', health_pb2.HealthCheckResponse.NOT_SERVING)])
        self.assertEqual(
            m._last_health_status,
            health_pb2.HealthCheckResponse.NOT_SERVING)

    @mock.patch('shakenfist.mariadb.check_reachable', return_value=True)
    def test_steady_state_sets_every_tick_logs_once(self, _mock_reachable):
        servicer = _RecordingHealthServicer()
        m = _make_monitor(servicer)

        with mock.patch.object(database_main, 'LOG') as mock_log:
            m._update_health()
            m._update_health()

            # set('', SERVING) is called on every tick so external Check
            # callers always see a fresh value...
            self.assertEqual(
                servicer.calls,
                [('', health_pb2.HealthCheckResponse.SERVING),
                 ('', health_pb2.HealthCheckResponse.SERVING)])

            # ...but the transition is only logged once.
            self.assertEqual(mock_log.info.call_count, 1)
            self.assertEqual(mock_log.warning.call_count, 0)

    def test_transition_logs_on_each_change(self):
        servicer = _RecordingHealthServicer()
        m = _make_monitor(servicer)

        with mock.patch.object(database_main, 'LOG') as mock_log:
            with mock.patch('shakenfist.mariadb.check_reachable',
                            return_value=True):
                m._update_health()
            with mock.patch('shakenfist.mariadb.check_reachable',
                            return_value=False):
                m._update_health()
            with mock.patch('shakenfist.mariadb.check_reachable',
                            return_value=True):
                m._update_health()

            # SERVING -> NOT_SERVING -> SERVING is two info logs (became
            # reachable) and one warning (became unreachable).
            self.assertEqual(mock_log.info.call_count, 2)
            self.assertEqual(mock_log.warning.call_count, 1)
            self.assertEqual(
                m._last_health_status,
                health_pb2.HealthCheckResponse.SERVING)

    def test_none_servicer_is_noop(self):
        m = _make_monitor(None)

        with mock.patch('shakenfist.mariadb.check_reachable') as mock_reach:
            # Must not raise and must not even probe reachability.
            m._update_health()
            mock_reach.assert_not_called()

        self.assertIsNone(m._last_health_status)


class DatabaseDrainTestCase(base.ShakenFistTestCase):
    def test_health_flips_to_not_serving_before_stop(self):
        # The ordering is the invariant: NOT_SERVING must be reported
        # before server.stop() starts refusing new RPCs, so external
        # Check-based monitoring (and the deploy's gateway-health roll
        # gate) sees the drain. A shared parent mock records the two
        # collaborators' calls in a single sequence.
        parent = mock.Mock()
        server = parent.server
        health_servicer = parent.health_servicer

        database_main.drain_and_stop(server, health_servicer)

        self.assertEqual(
            parent.mock_calls[0],
            mock.call.health_servicer.set(
                '', health_pb2.HealthCheckResponse.NOT_SERVING))
        self.assertEqual(
            parent.mock_calls[1],
            mock.call.server.stop(
                database_main.config.DATABASE_DRAIN_GRACE))

        # And the drain is waited on, not fire-and-forget.
        server.stop.return_value.wait.assert_called_once_with()
