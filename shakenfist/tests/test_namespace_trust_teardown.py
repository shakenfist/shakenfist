# Copyright 2019 Michael Still and contributors
#
# Issue 2074: a trust names a namespace by name, not by uuid, and
# namespace names are reusable once the cluster maintainer hard deletes
# the static row. A trust which outlives the namespace it names is
# therefore inherited by whoever creates that name next.
import inspect
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.daemons.cluster import scheduled_tasks
from shakenfist.namespace import Namespace
from shakenfist.namespace import revoke_inbound_trust
from shakenfist.namespace import revoke_trusts_naming_missing_namespaces
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


class ReconcileMissingTrustTestCase(base.ShakenFistTestCase):
    """Issue 4829: the delete-time revocation had no backfill.

    revoke_inbound_trust() only runs when a namespace is deleted, so a
    trust granted before that code landed -- or granted for a name
    which never existed -- stays in the list forever. The sweep is the
    standing reconciliation behind it.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        # 'system' exists here, unlike the suites above, because every
        # namespace is created trusting it and the sweep has to see the
        # row behind that trust.
        for name in ['system', 'alpha', 'beta', 'gamma', 'doomed']:
            self.mock_mariadb.create_namespace(name, 'key1', 'secret')

    def _delete_without_revoking(self, name):
        """Delete a namespace the way the world before 09e6dcdb56 did."""
        with mock.patch('shakenfist.namespace.revoke_inbound_trust'):
            Namespace.from_db(name).hard_delete()

    def test_revokes_residue_from_before_the_delete_fix(self):
        Namespace.from_db('alpha').add_trust('doomed')
        Namespace.from_db('beta').add_trust('doomed')
        self._delete_without_revoking('doomed')
        self.assertIn('doomed', Namespace.from_db('alpha').trust)

        revoked = revoke_trusts_naming_missing_namespaces('unit test')

        self.assertEqual({'alpha': ['doomed'], 'beta': ['doomed']}, revoked)
        self.assertEqual(['system'], Namespace.from_db('alpha').trust)
        self.assertEqual(['system'], Namespace.from_db('beta').trust)

    def test_revokes_a_trust_naming_a_name_which_never_existed(self):
        Namespace.from_db('alpha').add_trust('never-was')

        revoked = revoke_trusts_naming_missing_namespaces('unit test')

        self.assertEqual({'alpha': ['never-was']}, revoked)
        self.assertEqual(['system'], Namespace.from_db('alpha').trust)

    def test_live_trusts_survive(self):
        alpha = Namespace.from_db('alpha')
        alpha.add_trust('gamma')
        alpha.add_trust('never-was')

        revoke_trusts_naming_missing_namespaces('unit test')

        # 'system' is granted at creation and losing it would blind the
        # cluster admin, and 'gamma' is a live namespace.
        self.assertEqual(['system', 'gamma'],
                         Namespace.from_db('alpha').trust)

    def test_a_deleted_namespace_keeps_its_inbound_trusts(self):
        # Its name is not available for reuse while the static row
        # exists, so there is nobody to inherit the trust, and
        # Namespace.hard_delete() revokes at the moment it becomes
        # available. Revoking here would instead break a trust whose
        # namespace an operator could still undelete-by-recreate.
        Namespace.from_db('alpha').add_trust('doomed')
        doomed = Namespace.from_db('doomed')
        doomed.state = dbo.STATE_DELETED

        revoked = revoke_trusts_naming_missing_namespaces('unit test')

        self.assertEqual({}, revoked)
        self.assertIn('doomed', Namespace.from_db('alpha').trust)

    def test_a_failed_namespace_read_revokes_nothing(self):
        # An empty namespace list cannot be true of a cluster we are
        # running inside, so it means the read failed. Revoking every
        # trust in the cluster is not an acceptable response to that.
        Namespace.from_db('alpha').add_trust('never-was')

        with mock.patch('shakenfist.mariadb.get_all_namespace_names',
                        return_value=[]):
            revoked = revoke_trusts_naming_missing_namespaces('unit test')

        self.assertEqual({}, revoked)
        self.assertIn('never-was', Namespace.from_db('alpha').trust)

    def test_audit_event_recorded_on_the_truster(self):
        Namespace.from_db('alpha').add_trust('never-was')

        with mock.patch('shakenfist.namespace.Namespace.add_event') as ae:
            revoke_trusts_naming_missing_namespaces('unit test')

        messages = [c.args[1] for c in ae.call_args_list]
        self.assertIn('trust revoked, trusted namespace is gone', messages)

    def test_the_system_trust_is_never_reported_revoked(self):
        # remove_trust() refuses to remove it, so a sweep which asked
        # would report a revocation that did not happen. Simulated by a
        # namespace listing which omits system, because a cluster where
        # that is true is one where the read is broken rather than one
        # where the admin should lose sight of every namespace.
        with mock.patch('shakenfist.mariadb.get_all_namespace_names',
                        return_value=['alpha', 'beta', 'gamma', 'doomed']):
            revoked = revoke_trusts_naming_missing_namespaces('unit test')

        self.assertEqual({}, revoked)
        self.assertIn('system', Namespace.from_db('alpha').trust)

    def test_a_namespace_missing_only_from_the_listing_survives(self):
        # _persist_pydantic_upgrade() deletes and recreates a
        # namespace's static row to change its version, so a listing
        # taken inside that window is missing a live namespace. The
        # per candidate confirmation read is what stops the sweep
        # revoking a live trust because of it.
        Namespace.from_db('alpha').add_trust('gamma')

        with mock.patch('shakenfist.mariadb.get_all_namespace_names',
                        return_value=['system', 'alpha', 'beta', 'doomed']):
            revoked = revoke_trusts_naming_missing_namespaces('unit test')

        self.assertEqual({}, revoked)
        self.assertIn('gamma', Namespace.from_db('alpha').trust)

    def test_no_trusts_is_not_an_error(self):
        self.assertEqual({}, revoke_trusts_naming_missing_namespaces('test'))


class ScheduledTaskTestCase(base.ShakenFistTestCase):
    """The sweep has to actually be wired into the cluster maintainer."""

    def test_task_registered_on_the_maintenance_schedule(self):
        source = inspect.getsource(cluster_main.Monitor._run_inner)
        self.assertIn('scheduled_tasks.reconcile_namespace_trust', source)

    def test_task_calls_the_sweep(self):
        with mock.patch('shakenfist.daemons.cluster.scheduled_tasks.'
                        'revoke_trusts_naming_missing_namespaces') as sweep:
            scheduled_tasks.reconcile_namespace_trust()

        sweep.assert_called_once()
