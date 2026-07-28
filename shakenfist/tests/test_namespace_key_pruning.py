import time

from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NamespaceKeyPruningTestCase(base.ShakenFistTestCase):
    """Expired service keys must not accumulate where reads can see them.

    get_api_token() mints a short-lived _service_key_* every few
    minutes per daemon. When keys lived in the namespace_attributes
    keys JSON blob, filtering them only on read let that one row grow
    without bound until it crossed gRPC's maximum message size and
    namespace reads failed cluster-wide (issue #3521). The fix then
    was to purge expired entries on every write.

    Keys are now rows in namespace_keys rather than entries in a
    single JSON document, so the original failure mode is gone: no
    one value grows, and the expiry filter is applied in SQL so an
    expired key never reaches a caller. These tests pin that
    property. Physical removal is now the cluster daemon's expiry
    sweep, covered by test_daemon_cluster_scheduled_tasks.py.

    One behavioural difference from the JSON-blob fix is worth
    recording: removal now depends on the cluster daemon running,
    where purging on write did not. If it never runs the consequence
    is bounded table growth rather than a cluster-wide read failure.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _visible_keys(self, namespace):
        """The key names a caller authenticating would actually see."""
        ns = Namespace.from_db(namespace)
        return set(ns.keys.get('nonced_keys', {}))

    def test_expired_keys_are_invisible_to_readers(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        # The expired key still has a row, but no reader can see it,
        # so it can neither authenticate nor bloat a read.
        self.assertEqual({'permanent'}, self._visible_keys('banana'))

    def test_churning_service_keys_does_not_grow_the_visible_set(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')

        # Several rounds of get_api_token() minting a fresh
        # short-lived service key which then lapses.
        for unique in ('aaaaa', 'bbbbb', 'ccccc', 'ddddd'):
            ns.add_key(f'_service_key_{unique}', 'stale',
                       expiry=time.time() - 10)

        ns.add_key('_service_key_eeeee', 'fresh', expiry=time.time() + 300)

        # Only the permanent key and the one live service key are
        # visible, however many have churned through.
        self.assertEqual(
            {'permanent', '_service_key_eeeee'},
            self._visible_keys('banana'))

    def test_removing_a_key_does_not_disturb_the_others(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('doomed', 'bacon')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        ns.remove_key('doomed')

        self.assertEqual({'permanent'}, self._visible_keys('banana'))

    def test_removing_an_absent_key_is_not_an_error(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        # This path previously existed so that a remove could trigger
        # a prune. It must still be a no-op rather than a failure.
        ns.remove_key('no-such-key')

        self.assertEqual({'permanent'}, self._visible_keys('banana'))

    def test_keys_property_filters_expired_keys(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        self.assertEqual({'permanent'}, set(ns.keys['nonced_keys']))
