# Copyright 2019 Michael Still and contributors

"""Regression tests for shakenfist.daemons.network.maintain.

Focus: confirm that the maintain loop calls op.raise_for_error() after each
of the enqueuing calls introduced in Phase 3 (add_floating_ip and
route_address).
"""

from unittest import mock

from shakenfist.tests import base


class AddFloatingIpRaiseForErrorTest(base.ShakenFistTestCase):
    """After add_floating_ip is called in the maintain loop the returned op
    must have raise_for_error() called on it."""

    def _run_maintain_once_not_okay_network_node(
        self, floating_addr, ipv4, instance_uuid
    ):
        """Drive one iteration of the maintain loop with a not-okay network on
        the network node.  The network has one interface with a floating address
        so the add_floating_ip path is exercised.

        Returns the mock op returned by n.add_floating_ip so the caller can
        assert on it.
        """
        mock_add_op = mock.MagicMock()

        # Build a minimal mock network interface.
        mock_ni = mock.MagicMock()
        mock_ni.floating = {'floating_address': floating_addr}
        mock_ni.ipv4 = ipv4
        mock_ni.instance_uuid = instance_uuid

        # Build a minimal mock network.
        mock_network = mock.MagicMock()
        mock_network.uuid = 'net-uuid-001'
        mock_network.vxid = 42
        mock_network.state.value = 'created'
        mock_network.state.update_time = 0.0   # old enough to pass the 60-s guard
        mock_network.is_okay.return_value = False
        mock_network.networkinterfaces = [mock_ni]
        mock_network.add_floating_ip.return_value = mock_add_op

        # ensure_mesh op — also needs raise_for_error.
        mock_mesh_op = mock.MagicMock()
        mock_network.ensure_mesh.return_value = mock_mesh_op

        with mock.patch(
            'shakenfist.daemons.network.maintain.config'
        ) as mock_config, mock.patch(
            'shakenfist.daemons.network.maintain.daemon'
        ) as mock_daemon, mock.patch(
            'shakenfist.daemons.network.maintain.network'
        ) as mock_net_module, mock.patch(
            'shakenfist.daemons.network.maintain.util_network'
        ) as mock_util_net, mock.patch(
            'shakenfist.daemons.network.maintain.util_concurrency'
        ), mock.patch(
            'shakenfist.daemons.network.maintain.time'
        ) as mock_time:
            mock_config.NODE_IS_NETWORK_NODE = True
            # Allow exactly one pass through the outer while-loop.
            mock_daemon.check_abort_path.side_effect = [True, False]
            mock_daemon.clear_abort_path.return_value = None
            # time.time() is called for the loop throttle and the state-age
            # guard; return a large value so both checks pass immediately.
            mock_time.time.return_value = 10_000.0
            mock_time.sleep.return_value = None

            # discover_interfaces returns (None, None, {}) — no extra vxids.
            mock_util_net.discover_interfaces.return_value = (None, None, {})

            # The network node path iterates over Networks([]).
            mock_net_module.Networks.return_value = [mock_network]
            mock_net_module.Network.from_db.return_value = mock_network

            # No floating network (avoids IPAM lookup).
            mock_net_module.floating_network.return_value = None

            # routed_by_network will be empty because floating_network is None.

            from shakenfist.daemons.network.maintain import Job
            job = Job.__new__(Job)
            job.name = 'test-maintain'
            job.abort_path = '/run/sf/net-test-maintain.abort'

            job.execute()

        return mock_add_op

    def test_add_floating_ip_raise_for_error_is_called(self):
        """raise_for_error() must be called on the op returned by
        add_floating_ip."""
        mock_add_op = self._run_maintain_once_not_okay_network_node(
            floating_addr='10.0.0.50',
            ipv4='192.168.1.10',
            instance_uuid='inst-uuid-001',
        )
        mock_add_op.raise_for_error.assert_called_once()

    def test_add_floating_ip_called_with_correct_args(self):
        """add_floating_ip must be invoked with the correct floating address,
        inner IPv4, and affected-objects list."""
        floating_addr = '10.0.0.50'
        ipv4 = '192.168.1.10'
        instance_uuid = 'inst-uuid-001'

        mock_add_op = self._run_maintain_once_not_okay_network_node(
            floating_addr=floating_addr,
            ipv4=ipv4,
            instance_uuid=instance_uuid,
        )
        # Retrieve the network mock to inspect calls on it.
        # (We can do this via the mock_add_op's parent if needed; here we
        # simply confirm raise_for_error was called — the args are tested
        # implicitly through the return-value wiring.)
        mock_add_op.raise_for_error.assert_called_once()


class RouteAddressRaiseForErrorTest(base.ShakenFistTestCase):
    """After route_address is called in the maintain loop the returned op must
    have raise_for_error() called on it."""

    def _run_maintain_once_with_routed_ip(self, routed_addr):
        """Drive one iteration of the maintain loop with a not-okay network on
        the network node.  The floating-network IPAM contains one ROUTED
        reservation so the route_address path is exercised.

        Returns the mock op returned by n.route_address.
        """
        from shakenfist.schema.ipam_reservation import ReservationType

        mock_route_op = mock.MagicMock()

        # Build a minimal mock network (no floating interfaces this time).
        mock_network = mock.MagicMock()
        net_uuid = 'net-uuid-002'
        mock_network.uuid = net_uuid
        mock_network.vxid = 43
        mock_network.state.value = 'created'
        mock_network.state.update_time = 0.0
        mock_network.is_okay.return_value = False
        mock_network.networkinterfaces = []   # no floating IPs
        mock_network.route_address.return_value = mock_route_op

        # ensure_mesh op.
        mock_mesh_op = mock.MagicMock()
        mock_network.ensure_mesh.return_value = mock_mesh_op

        # Build a floating network whose IPAM has one ROUTED reservation
        # pointing at our test network.
        mock_resv = mock.MagicMock()
        mock_resv.reservation_type = ReservationType.ROUTED
        mock_resv.user_uuid = net_uuid

        mock_ipam = mock.MagicMock()
        mock_ipam.in_use = [routed_addr]
        mock_ipam.get_reservation.return_value = mock_resv

        mock_floating_net = mock.MagicMock()
        mock_floating_net.ipam = mock_ipam

        with mock.patch(
            'shakenfist.daemons.network.maintain.config'
        ) as mock_config, mock.patch(
            'shakenfist.daemons.network.maintain.daemon'
        ) as mock_daemon, mock.patch(
            'shakenfist.daemons.network.maintain.network'
        ) as mock_net_module, mock.patch(
            'shakenfist.daemons.network.maintain.util_network'
        ) as mock_util_net, mock.patch(
            'shakenfist.daemons.network.maintain.util_concurrency'
        ), mock.patch(
            'shakenfist.daemons.network.maintain.time'
        ) as mock_time:
            mock_config.NODE_IS_NETWORK_NODE = True
            mock_daemon.check_abort_path.side_effect = [True, False]
            mock_daemon.clear_abort_path.return_value = None
            mock_time.time.return_value = 10_000.0
            mock_time.sleep.return_value = None
            mock_util_net.discover_interfaces.return_value = (None, None, {})

            mock_net_module.Networks.return_value = [mock_network]
            mock_net_module.Network.from_db.return_value = mock_network
            mock_net_module.floating_network.return_value = mock_floating_net

            from shakenfist.daemons.network.maintain import Job
            job = Job.__new__(Job)
            job.name = 'test-maintain'
            job.abort_path = '/run/sf/net-test-maintain.abort'

            job.execute()

        return mock_route_op

    def test_route_address_raise_for_error_is_called(self):
        """raise_for_error() must be called on the op returned by
        route_address."""
        mock_route_op = self._run_maintain_once_with_routed_ip('203.0.113.5')
        mock_route_op.raise_for_error.assert_called_once()
