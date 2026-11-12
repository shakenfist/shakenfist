# Copyright 2019 Michael Still and contributors
"""Behaviour of the namespace ownership predicate and its one blind spot.

`api_base.namespace_owns()` is the single implementation of the test
`docs/developer_guide/coding_rules.md` requires every mutation guard to
call, and `shakenfist/tests/test_namespace_ownership_predicate.py` is
the guard which stops it being restated. Neither of those says what the
predicate *answers*, so the truth table is pinned here: own namespace
yes, `system` yes, anybody else no, and a trust or a shared flag is not
consulted because neither hands out a delete button.

The second class covers `api_util.safe_get_network_interface()`, which
is the one converted site with no behavioural coverage of its own. It is
a helper rather than a decorator, it is the only site which runs the
predicate twice -- once against the interface's network and once against
the instance the interface is attached to -- and every test which
reaches it through `InterfaceEndpoint` mocks it out, so its two refusals
were asserted nowhere. Both are exercised directly, each with a control
showing the same call succeeding when the namespace under test is the
only thing changed, because a refusal test without a control cannot
tell a working guard from a broken lookup.
"""

from unittest import mock

from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import util as api_util
from shakenfist.tests import base


class NamespaceOwnsTestCase(base.ShakenFistTestCase):
    def test_the_owning_namespace_owns(self):
        self.assertTrue(api_base.namespace_owns('tenant', 'tenant'))

    def test_system_owns_everything(self):
        self.assertTrue(api_base.namespace_owns('tenant', 'system'))

    def test_a_stranger_owns_nothing(self):
        self.assertFalse(api_base.namespace_owns('tenant', 'stranger'))

    def test_a_system_owned_object_is_not_owned_by_a_tenant(self):
        """The arguments are not interchangeable.

        Both are namespace names and a transposed call is therefore
        silent, so this pins the direction: the caller being `system` is
        what grants, not the object living there.
        """
        self.assertFalse(api_base.namespace_owns('system', 'tenant'))

    def test_a_namespaceless_object_is_owned_only_by_system(self):
        """The floating network has namespace None.

        It reaches `requires_network_ownership` like any other network,
        so None must not match a caller -- and a namespace name can
        never be None, so only `system` gets in.
        """
        self.assertTrue(api_base.namespace_owns(None, 'system'))
        self.assertFalse(api_base.namespace_owns(None, 'tenant'))


class SafeGetNetworkInterfaceOwnershipTestCase(base.ShakenFistTestCase):
    """The two ownership refusals in api_util.safe_get_network_interface."""

    INTERFACE_UUID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'

    def setUp(self):
        super().setUp()

        self.interface = mock.MagicMock()
        self.interface.uuid = self.INTERFACE_UUID
        self.interface.network_uuid = 'bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        self.interface.instance_uuid = 'cccccccc-bbbb-4ccc-8ddd-eeeeeeeeeeee'

        self.network = mock.MagicMock()
        self.network.namespace = 'owner'

        self.instance = mock.MagicMock()
        self.instance.namespace = 'owner'

        for target, value in (
                ('shakenfist.external_api.util.NetworkInterface.from_db',
                 self.interface),
                ('shakenfist.external_api.util.network.Network.from_db',
                 self.network),
                ('shakenfist.external_api.util.Instance.from_db',
                 self.instance)):
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fetch(self, caller_namespace):
        """Call the helper as `caller_namespace` would.

        A flask request context is needed because the refusal builds a
        response, and request_namespace() is patched rather than
        authenticated because the predicate is what is under test here
        and not the token parsing in front of it.
        """
        with external_api.app.test_request_context():
            with mock.patch(
                    'shakenfist.external_api.util.request_namespace',
                    return_value=caller_namespace):
                return api_util.safe_get_network_interface(
                    self.INTERFACE_UUID)

    def test_the_owner_may_fetch_an_interface(self):
        # The control for both refusals below.
        ni, n, err = self._fetch('owner')
        self.assertIsNone(err)
        self.assertEqual(self.interface, ni)
        self.assertEqual(self.network, n)

    def test_system_may_fetch_an_interface(self):
        ni, n, err = self._fetch('system')
        self.assertIsNone(err)
        self.assertEqual(self.interface, ni)

    def test_a_stranger_is_refused_by_the_network_test(self):
        ni, n, err = self._fetch('stranger')
        self.assertIsNone(ni)
        self.assertIsNone(n)
        self.assertEqual(404, err.status_code)

    def test_my_network_but_your_instance_is_refused(self):
        """The second test is not redundant with the first.

        An interface on a network the caller owns may be attached to
        somebody else's instance, and this is the only converted site
        which asks the question twice, for exactly that reason. Here the
        caller passes the network test and is refused by the instance
        one; if the two calls were ever collapsed into one, this test is
        what notices.
        """
        self.network.namespace = 'caller'
        self.instance.namespace = 'somebody-else'
        ni, n, err = self._fetch('caller')
        self.assertIsNone(ni)
        self.assertEqual(404, err.status_code)

    def test_a_refusal_does_not_distinguish_the_two_tests(self):
        """Both refusals answer 404 with the same message.

        A caller which could tell which of the two tests refused it
        would learn whose instance the interface is attached to, which
        is exactly what the 404 is buying.
        """
        self.network.namespace = 'stranger'
        _, _, by_network = self._fetch('owner-of-nothing')

        self.network.namespace = 'owner-of-nothing'
        self.instance.namespace = 'somebody-else'
        _, _, by_instance = self._fetch('owner-of-nothing')

        self.assertEqual(by_network.status_code, by_instance.status_code)
        self.assertEqual(by_network.get_json(), by_instance.get_json())
