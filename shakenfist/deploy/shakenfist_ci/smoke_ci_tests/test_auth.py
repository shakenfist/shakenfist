from shakenfist_ci import base
from shakenfist_client import apiclient


class TestAuth(base.BaseTestCase):
    def test_namespaces(self):
        name = 'ci-auth-%s' % self._uniquifier()
        key = self._uniquifier()

        self.assertNotIn(
            name, base.namespace_names(self.system_client.get_namespaces()))
        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        self.assertIn(
            name, base.namespace_names(self.system_client.get_namespaces()))

        self.assertRaises(apiclient.ResourceNotFoundException,
                          self.system_client.delete_namespace_key, name, 'banana')
        self.assertIn(
            name, base.namespace_names(self.system_client.get_namespaces()))

        self.system_client.delete_namespace_key(name, 'test')
        self.assertIn(
            name, base.namespace_names(self.system_client.get_namespaces()))

        self.system_client.delete_namespace(name)
        self.assertNotIn(
            name, base.namespace_names(self.system_client.get_namespaces()))

    def test_deleting_a_namespace_revokes_trusts_naming_it(self):
        """Issue 2074.

        A trust names a namespace by name, and a name becomes reusable
        once the cluster maintainer hard deletes the static row of the
        namespace which held it. A trust left behind by a delete is
        therefore inherited by whoever creates that name next, without
        the granting namespace doing anything or being told.
        """
        truster = 'ci-truster-%s' % self._uniquifier()
        doomed = 'ci-doomed-%s' % self._uniquifier()

        for name in [truster, doomed]:
            self.system_client.create_namespace(name)
            self.addCleanup(self._delete_namespace_if_present, name)

        self.system_client.add_namespace_trust(truster, doomed)
        self.assertIn(
            doomed,
            self.system_client.get_namespace(truster)['trust']['full'])

        self.system_client.delete_namespace(doomed)

        # The trust is gone as soon as the namespace is, rather than
        # whenever the hard delete happens to catch up.
        self.assertNotIn(
            doomed,
            self.system_client.get_namespace(truster)['trust']['full'])

        # And the trust every namespace is created with survives, or
        # the cluster admin loses sight of the namespace.
        self.assertIn(
            'system',
            self.system_client.get_namespace(truster)['trust']['full'])

    def _delete_namespace_if_present(self, name):
        if name in base.namespace_names(self.system_client.get_namespaces()):
            self.system_client.delete_namespace(name)
