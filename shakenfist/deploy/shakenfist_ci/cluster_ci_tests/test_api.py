import requests
from shakenfist_ci import base


class TestOpenAPIJS(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'openapijs'
        super().__init__(*args, **kwargs)

    def test_ui_js(self):
        # Ensure we can fetch the UI Javascript
        r = requests.get(
            f'{self.test_client.base_url}/flasgger_static/swagger-ui.css')
        self.assertEqual(200, r.status_code)


class TestOutstandingOperationsBodyAll(base.BaseNamespacedTestCase):
    """Body-supplied ``all`` on the outstanding-operations endpoints.

    Issue 3629: these endpoints bound ``all`` to the query string only,
    but the shipped client serialises every request -- including GETs --
    to a JSON body, so ``all=True`` sent the only way the client can
    send it was silently dropped and completed operations never appeared
    in the response. The client's wrapper for the network endpoint sends
    ``all`` in a JSON body, which is exactly the path that was broken, so
    reading through the wrapper is the test. The query-string form stays
    a raw ``_request_url`` because the client never sends it.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'outstandingops'
        super().__init__(*args, **kwargs)

    def test_network_outstanding_operations_body_all(self):
        net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([net['uuid']])
        self._await_network_operations_complete(net['uuid'])

        url = '/networks/%s/clusteroperations' % net['uuid']

        # Creating the network enqueued at least one cluster operation
        # and every one of them has completed, so a body-supplied
        # all=True must reveal completed operations. Before the fix the
        # body value was dropped and this answered outstanding-only.
        every = self.test_client.get_network_clusteroperations(
            net['uuid'], all=True)
        completed = [op['uuid'] for op in every
                     if op['state'] in ('complete', 'deleted', 'abort')]
        self.assertNotEqual(
            [], completed,
            'all=True in the request body did not reveal any completed '
            'operations: %s' % every)
        for op in every:
            self.assertIn('observational', op)

        # The query string form must keep working too.
        every_by_query = self.test_client._request_url(
            'GET', url + '?all=true').json()
        self.assertNotEqual(
            [], [op for op in every_by_query
                 if op['state'] in ('complete', 'deleted', 'abort')])

        # And without all, terminal operations stay hidden. A fresh
        # operation from a background sweep may legitimately appear
        # here, so assert only that the completed ones are absent.
        outstanding = self.test_client.get_network_clusteroperations(
            net['uuid'])
        for op in outstanding:
            self.assertNotIn(op['uuid'], completed)
