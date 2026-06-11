# Copyright 2019 Michael Still and contributors

import json
from unittest import mock

from shakenfist.tests import base
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
