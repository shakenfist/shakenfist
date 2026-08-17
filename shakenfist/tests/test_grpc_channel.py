# Copyright 2019 Michael Still and contributors

import json
from unittest import mock

from shakenfist.tests import base
from shakenfist.util import caller_identity
from shakenfist.util import grpc_channel
from shakenfist.util.grpc_channel import _DEFAULT_OPTIONS


class MakeDatabaseChannelReturnTypeTestCase(base.ShakenFistTestCase):
    """make_database_channel returns a grpc.Channel-typed object."""

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_returns_channel_object(self, mock_ic):
        import grpc
        fake_channel = mock.MagicMock(spec=grpc.Channel)
        mock_ic.return_value = fake_channel
        result = grpc_channel.make_database_channel(['10.0.0.1'], 13005)
        self.assertIsNotNone(result)
        mock_ic.assert_called_once()

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_does_not_raise_for_valid_args(self, mock_ic):
        mock_ic.return_value = mock.MagicMock()
        # Should not raise
        grpc_channel.make_database_channel(['10.0.0.1'], 13005)


class MakeDatabaseChannelEmptyHostsTestCase(base.ShakenFistTestCase):
    """make_database_channel raises ValueError when hosts is empty."""

    def test_raises_value_error_on_empty_hosts(self):
        with self.assertRaises(ValueError) as ctx:
            grpc_channel.make_database_channel([], 13005)
        self.assertIn('at least one host', str(ctx.exception))


class MakeDatabaseChannelTargetStringTestCase(base.ShakenFistTestCase):
    """Target string passed to grpc.insecure_channel has the correct form."""

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_single_host_target(self, mock_ic):
        mock_ic.return_value = mock.MagicMock()
        grpc_channel.make_database_channel(['10.0.0.1'], 13005)
        target = mock_ic.call_args[0][0]
        self.assertEqual(target, 'ipv4:10.0.0.1:13005')

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_multi_host_target(self, mock_ic):
        mock_ic.return_value = mock.MagicMock()
        grpc_channel.make_database_channel(
            ['10.0.0.1', '10.0.0.2', '10.0.0.3'], 13005)
        target = mock_ic.call_args[0][0]
        self.assertEqual(target, 'ipv4:10.0.0.1:13005,10.0.0.2:13005,10.0.0.3:13005')


class MakeDatabaseChannelOptionsTestCase(base.ShakenFistTestCase):
    """Options list passed to grpc.insecure_channel is built correctly."""

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_default_options_present_in_order(self, mock_ic):
        mock_ic.return_value = mock.MagicMock()
        grpc_channel.make_database_channel(['10.0.0.1'], 13005)
        options = mock_ic.call_args[1]['options']
        # Every default option appears in the options list.
        for opt in _DEFAULT_OPTIONS:
            self.assertIn(opt, options)
        # They appear in the correct order relative to one another.
        default_positions = [options.index(opt) for opt in _DEFAULT_OPTIONS]
        self.assertEqual(default_positions, sorted(default_positions))

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_extra_options_appended_after_defaults(self, mock_ic):
        mock_ic.return_value = mock.MagicMock()
        extra = [('grpc.keepalive_time_ms', 99999)]
        grpc_channel.make_database_channel(['10.0.0.1'], 13005, extra_options=extra)
        options = mock_ic.call_args[1]['options']
        # The default entry for keepalive_time_ms appears before the override.
        default_entry = ('grpc.keepalive_time_ms', 10000)
        override_entry = ('grpc.keepalive_time_ms', 99999)
        self.assertIn(default_entry, options)
        self.assertIn(override_entry, options)
        self.assertLess(
            options.index(default_entry),
            options.index(override_entry),
            'Duplicate-key extra option must appear after the default '
            'so gRPC last-key-wins picks up the override',
        )

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_no_extra_options_when_none_given(self, mock_ic):
        mock_ic.return_value = mock.MagicMock()
        grpc_channel.make_database_channel(['10.0.0.1'], 13005)
        options = mock_ic.call_args[1]['options']
        self.assertEqual(len(options), len(_DEFAULT_OPTIONS))

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_receive_cap_is_bounded_at_both_ends(self, mock_ic):
        """The receive cap is a band, not just a floor.

        GetObjectsByState and GetObjectEvents replies are unbounded by
        design, and at gRPC's 4MiB default the sfcbr cluster saw
        RESOURCE_EXHAUSTED on replies up to ~7.2MB (#3638), so the cap
        has to clear observed traffic with headroom. It must equally
        not be raised without limit: sf-database serialises whatever it
        sends, so a very generous client cap trades a fast client-side
        failure for memory pressure on the database tier. Assert the
        band rather than the constant, so a future adjustment inside
        the band is free and one outside it has to argue its case.
        """
        mock_ic.return_value = mock.MagicMock()
        grpc_channel.make_database_channel(['10.0.0.1'], 13005)
        options = dict(mock_ic.call_args[1]['options'])
        cap = options.get('grpc.max_receive_message_length')
        self.assertIsNotNone(cap)
        self.assertGreaterEqual(cap, 16 * 1024 * 1024)
        self.assertLessEqual(cap, 64 * 1024 * 1024)

    @mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel')
    def test_reconnect_backoff_capped_below_roll_settle(self, mock_ic):
        """The reconnect backoff cap must stay below the deploy settle.

        gRPC's default reconnect backoff grows to 120s, which left
        clients not redialling a recovered gateway until long after the
        database-tier roll had moved on to stop the next one -- the
        "connections to all backends failing" deploy storms of #3430.
        The node role's roll settle (sf_database_roll_settle_seconds,
        default 10s) only covers the reconnect window because this cap
        is shorter than it; keep it that way.
        """
        mock_ic.return_value = mock.MagicMock()
        grpc_channel.make_database_channel(['10.0.0.1'], 13005)
        options = dict(mock_ic.call_args[1]['options'])
        self.assertEqual(
            1000, options.get('grpc.initial_reconnect_backoff_ms'))
        self.assertEqual(
            5000, options.get('grpc.max_reconnect_backoff_ms'))
        self.assertLess(
            options['grpc.max_reconnect_backoff_ms'], 10000,
            'grpc.max_reconnect_backoff_ms must stay below the deploy '
            'roll settle (sf_database_roll_settle_seconds, default 10s) '
            'or the settle no longer guarantees clients have redialled '
            'a recovered gateway before the next one restarts')


class MakeDatabaseChannelServiceConfigTestCase(base.ShakenFistTestCase):
    """grpc.service_config option carries the expected LB config."""

    def _get_service_config(self, extra_options=None):
        with mock.patch('shakenfist.util.grpc_channel.grpc.insecure_channel') as mock_ic:
            mock_ic.return_value = mock.MagicMock()
            grpc_channel.make_database_channel(
                ['10.0.0.1'], 13005, extra_options=extra_options)
            options = mock_ic.call_args[1]['options']
        config_json = dict(options).get('grpc.service_config')
        self.assertIsNotNone(config_json, 'grpc.service_config must be present')
        return json.loads(config_json)

    def test_load_balancing_config_uses_round_robin(self):
        cfg = self._get_service_config()
        lb_configs = cfg.get('loadBalancingConfig', [])
        policies = [list(entry.keys())[0] for entry in lb_configs]
        self.assertIn('round_robin', policies)

    def test_health_check_config_absent(self):
        """Client-side health checking must stay disabled.

        healthCheckConfig opens a Health/Watch stream per subchannel,
        and the synchronous HealthServicer on sf-database deadlocks
        its server's single event-dispatch thread when a Watch open
        (initial response sent under the servicer lock) races a Watch
        close (close callback acquiring that lock inline on the event
        thread). See the _DEFAULT_OPTIONS comment in
        shakenfist/util/grpc_channel.py before re-enabling this.
        """
        cfg = self._get_service_config()
        self.assertNotIn(
            'healthCheckConfig', cfg,
            'healthCheckConfig re-enables Watch-based client-side health '
            'checking, which deadlocks the sync HealthServicer on '
            'sf-database. Do not re-add without an async or '
            'deadlock-safe server-side health implementation.',
        )


class CallerMetadataInterceptorTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        original = caller_identity.get_caller_daemon()
        self.addCleanup(caller_identity.set_caller_identity, original)

    def _details(self, metadata=None):
        return grpc_channel._ClientCallDetails(
            '/svc/Method', 30, metadata, None, None)

    def test_appends_caller_metadata_and_preserves_fields(self):
        caller_identity.set_caller_identity('queues')
        captured = {}

        def continuation(details, request):
            captured['metadata'] = list(details.metadata)
            captured['timeout'] = details.timeout
            captured['method'] = details.method
            return 'resp'

        interceptor = grpc_channel._CallerMetadataInterceptor()
        result = interceptor.intercept_unary_unary(
            continuation, self._details(), 'req')

        self.assertEqual('resp', result)
        md = dict(captured['metadata'])
        self.assertEqual('queues', md['caller-daemon'])
        self.assertIn('caller-node', md)
        # Non-metadata call details are carried through unchanged.
        self.assertEqual(30, captured['timeout'])
        self.assertEqual('/svc/Method', captured['method'])

    def test_preserves_existing_metadata(self):
        def continuation(details, request):
            return list(details.metadata)

        interceptor = grpc_channel._CallerMetadataInterceptor()
        md = dict(interceptor.intercept_unary_unary(
            continuation, self._details([('x', 'y')]), 'req'))

        self.assertEqual('y', md['x'])
        self.assertIn('caller-daemon', md)
