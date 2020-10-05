from shakenfist_client import apiclient

from shakenfist_ci import base


class TestAuth(base.BaseTestCase):
    def test_namespaces(self):
        name = 'ci-auth-%s' % self._uniquifier()
        key = self._uniquifier()

        self.assertNotIn(name, self.system_client.get_namespaces())
        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        self.assertIn(name, self.system_client.get_namespaces())

        self.assertRaises(apiclient.ResourceNotFoundException,
                          self.system_client.delete_namespace_key, name, 'banana')
        self.assertIn(name, self.system_client.get_namespaces())

        self.system_client.delete_namespace_key(name, 'test')
        self.assertIn(name, self.system_client.get_namespaces())

        self.system_client.delete_namespace(name)
        self.assertNotIn(name, self.system_client.get_namespaces())
