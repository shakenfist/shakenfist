from shakenfist_client import apiclient

from shakenfist_ci import base


class TestNodes(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'nodes'
        super().__init__(*args, **kwargs)

    def test_get_node(self):
        # I know this is a bit weird and is just testing if both calls return
        # the same name, but what its _really_ doing is ensuring the get_nodes()
        # call returns at all.
        nodes = self.system_client.get_nodes()
        n = self.system_client.get_node(nodes[0]['name'])
        self.assertEqual(nodes[0]['name'], n['name'])

    def test_get_missing_node(self):
        self.assertRaises(
            apiclient.ResourceNotFoundException, self.system_client.get_node,
            'banana')
