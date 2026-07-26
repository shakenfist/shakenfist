import time

from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NamespaceKeyPruningTestCase(base.ShakenFistTestCase):
    """Expired nonced keys must be pruned from storage on write.

    get_api_token() mints a short-lived _service_key_* every few
    minutes per daemon. Filtering expired keys only on read lets the
    stored blob grow without bound until it crosses gRPC's maximum
    message size and namespace reads fail cluster-wide (issue #3521).
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _stored_keys(self, namespace):
        return self.mock_mariadb.namespace_attributes[namespace].keys['nonced_keys']

    def test_add_key_prunes_expired_keys(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        # The expired key survives its own write...
        self.assertEqual(
            {'permanent', '_service_key_aaaaa'},
            set(self._stored_keys('banana')))

        # ... but the next write must purge it from storage, not just
        # filter it from the read.
        ns.add_key('_service_key_bbbbb', 'fresh', expiry=time.time() + 300)
        self.assertEqual(
            {'permanent', '_service_key_bbbbb'},
            set(self._stored_keys('banana')))

    def test_remove_key_prunes_expired_keys(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('doomed', 'bacon')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        ns.remove_key('doomed')
        self.assertEqual({'permanent'}, set(self._stored_keys('banana')))

    def test_remove_key_prunes_even_when_name_absent(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        ns.remove_key('no-such-key')
        self.assertEqual({'permanent'}, set(self._stored_keys('banana')))

    def test_keys_property_filters_expired_keys(self):
        ns = Namespace.new('banana')
        ns.add_key('permanent', 'cheese')
        ns.add_key('_service_key_aaaaa', 'stale', expiry=time.time() - 10)

        self.assertEqual({'permanent'}, set(ns.keys['nonced_keys']))
