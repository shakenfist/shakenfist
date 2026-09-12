# Copyright 2019 Michael Still and contributors
#
# Issue 2074: a trust names a namespace by name, not by uuid, and
# namespace names are reusable once the cluster maintainer hard deletes
# the static row. A trust which outlives the namespace it names is
# therefore inherited by whoever creates that name next.
from unittest import mock

from shakenfist.namespace import Namespace
from shakenfist.namespace import revoke_inbound_trust
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class RevokeInboundTrustTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for name in ['alpha', 'beta', 'gamma', 'doomed']:
            self.mock_mariadb.create_namespace(name, 'key1', 'secret')

    def test_revokes_from_every_truster(self):
        Namespace.from_db('alpha').add_trust('doomed')
        Namespace.from_db('beta').add_trust('doomed')

        revoked = revoke_inbound_trust('doomed', 'unit test')

        self.assertEqual(['alpha', 'beta'], sorted(revoked))
        self.assertNotIn('doomed', Namespace.from_db('alpha').trust)
        self.assertNotIn('doomed', Namespace.from_db('beta').trust)

    def test_leaves_other_trusts_alone(self):
        alpha = Namespace.from_db('alpha')
        alpha.add_trust('doomed')
        alpha.add_trust('gamma')

        revoke_inbound_trust('doomed', 'unit test')

        # Only the doomed namespace loses its trust. 'system' is granted
        # to every namespace at creation and must survive too, or the
        # cluster admin stops being able to see the namespace.
        self.assertEqual(['system', 'gamma'],
                         Namespace.from_db('alpha').trust)

    def test_untrusting_namespaces_are_untouched(self):
        Namespace.from_db('alpha').add_trust('doomed')

        revoked = revoke_inbound_trust('doomed', 'unit test')

        self.assertEqual(['alpha'], revoked)
        self.assertEqual(['system'], Namespace.from_db('beta').trust)
        self.assertEqual(['system'], Namespace.from_db('gamma').trust)

    def test_no_trusts_is_not_an_error(self):
        self.assertEqual([], revoke_inbound_trust('doomed', 'unit test'))

    def test_self_trust_is_skipped(self):
        # A namespace always trusts itself implicitly
        # (namespace_is_trusted short circuits), but if a name somehow
        # appears in its own list we must not trip over it while
        # deleting that very namespace.
        doomed = Namespace.from_db('doomed')
        doomed.add_trust('doomed')

        revoked = revoke_inbound_trust('doomed', 'unit test')

        self.assertEqual([], revoked)

    def test_audit_event_recorded_on_the_truster(self):
        Namespace.from_db('alpha').add_trust('doomed')

        with mock.patch('shakenfist.namespace.Namespace.add_event') as ae:
            revoke_inbound_trust('doomed', 'namespace deleted')

        messages = [c.args[1] for c in ae.call_args_list]
        self.assertIn('trust revoked, trusted namespace is gone', messages)


class HardDeleteRevokesTrustTestCase(base.ShakenFistTestCase):
    """hard_delete() is the backstop behind the REST delete handler."""

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for name in ['alpha', 'doomed']:
            self.mock_mariadb.create_namespace(name, 'key1', 'secret')

    def test_hard_delete_revokes_inbound_trust(self):
        Namespace.from_db('alpha').add_trust('doomed')
        self.assertIn('doomed', Namespace.from_db('alpha').trust)

        Namespace.from_db('doomed').hard_delete()

        # Without this the name 'doomed' is now free to be recreated by
        # anyone, and whoever does inherits alpha's trust.
        self.assertNotIn('doomed', Namespace.from_db('alpha').trust)
