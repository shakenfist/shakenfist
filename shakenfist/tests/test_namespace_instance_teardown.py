# Copyright 2019 Michael Still and contributors
#
# Issue 4847: an instance is reachable only through its namespace, so a
# namespace deleted over the top of one leaves a row with no namespace,
# no owner and nothing which will ever sweep it. The REST delete handler
# refuses a namespace which still holds a live instance (see
# shakenfist/tests/external_api/test_auth.py), and
# Namespace.hard_delete() is the backstop for whatever arrives anyway.
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.instance import Instance
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class HardDeleteSweepsInstancesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for name in ['system', 'doomed', 'bystander']:
            self.mock_mariadb.create_namespace(name, 'key1', 'secret')

    def _hard_delete_doomed(self):
        with mock.patch.object(Instance, 'enqueue_delete',
                               autospec=True) as ed:
            Namespace.from_db('doomed').hard_delete()
        return [str(c.args[0].uuid) for c in ed.call_args_list]

    def test_errored_instance_is_deleted(self):
        # The state the orphans found on sfcbr were in. Hard deleting
        # the row here would release the ledger capacity but leave
        # whatever is on the hypervisor unexplained, so the delete the
        # instance never got is enqueued instead.
        inst = self.mock_mariadb.create_instance(
            name='orphan', namespace='doomed', set_state=dbo.STATE_ERROR)

        self.assertEqual([str(inst.uuid)], self._hard_delete_doomed())
        # The sweep is one attempt alongside the namespace's own rows,
        # not a reason to hold them back.
        self.assertIsNone(Namespace.from_db('doomed'))

    def test_live_instance_is_deleted(self):
        # A create which raced the namespace delete.
        inst = self.mock_mariadb.create_instance(
            name='racer', namespace='doomed', set_state=dbo.STATE_CREATED)

        self.assertEqual([str(inst.uuid)], self._hard_delete_doomed())

    def test_deleted_instance_is_left_alone(self):
        # It has released everything it held, and the deleted object
        # sweep hard deletes its row by state rather than by namespace.
        self.mock_mariadb.create_instance(
            name='gone', namespace='doomed', set_state=dbo.STATE_DELETED)

        self.assertEqual([], self._hard_delete_doomed())

    def test_another_namespaces_instance_is_left_alone(self):
        self.mock_mariadb.create_instance(
            name='theirs', namespace='bystander',
            set_state=dbo.STATE_ERROR)

        self.assertEqual([], self._hard_delete_doomed())

    def test_an_empty_namespace_is_not_an_error(self):
        self.assertEqual([], self._hard_delete_doomed())

    def test_the_instance_records_why_it_was_deleted(self):
        self.mock_mariadb.create_instance(
            name='orphan', namespace='doomed', set_state=dbo.STATE_ERROR)

        with mock.patch.object(Instance, 'enqueue_delete', autospec=True):
            with mock.patch.object(Instance, 'add_event') as ae:
                Namespace.from_db('doomed').hard_delete()

        messages = [c.args[1] for c in ae.call_args_list]
        self.assertIn(
            'attempting deletion from namespace hard delete, '
            'instance outlived its namespace', messages)

    def test_system_does_not_sweep_the_cluster(self):
        # The object iterator reads a namespace of 'system' as "every
        # namespace", so without the guard this would enqueue a delete
        # for both of these.
        self.mock_mariadb.create_instance(
            name='theirs', namespace='bystander', set_state=dbo.STATE_ERROR)
        self.mock_mariadb.create_instance(
            name='ours', namespace='system', set_state=dbo.STATE_CREATED)

        with mock.patch.object(Instance, 'enqueue_delete',
                               autospec=True) as ed:
            Namespace.from_db('system').hard_delete()
        self.assertEqual([], ed.call_args_list)

    def test_a_failed_enqueue_does_not_defer_trust_revocation(self):
        # enqueue_delete() raises when it cannot read the instance's
        # outstanding operations. That aborts the pass, which is fine --
        # the namespace rows survive for the sweep's next one -- but the
        # trust revocation must already have happened.
        Namespace.from_db('bystander').add_trust('doomed')
        self.mock_mariadb.create_instance(
            name='orphan', namespace='doomed', set_state=dbo.STATE_ERROR)

        with mock.patch.object(
                Instance, 'enqueue_delete', autospec=True,
                side_effect=exceptions.DatabaseUnavailable('no')) as ed:
            self.assertRaises(exceptions.DatabaseUnavailable,
                              Namespace.from_db('doomed').hard_delete)
        ed.assert_called_once()
        self.assertNotIn('doomed', Namespace.from_db('bystander').trust)
