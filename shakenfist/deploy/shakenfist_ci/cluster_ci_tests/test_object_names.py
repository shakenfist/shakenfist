import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestObjectNames(base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'namespace_test'
        super().__init__(*args, **kwargs)

    def test_object_names(self):
        """Check instances and networks names

        Testing API create_instance() using network name and instance/network
        retrieval by name.
        """

        nets = {}
        for i in ['barry', 'dave', 'alice']:
            n = self.test_client.allocate_network(
                '192.168.242.0/24', True, True, i+'_net')
            self.addDetail(
                'net_%s' % i,
                content.text_content(json.dumps(n, indent=4, sort_keys=True)))
            nets[i+'_net'] = n['uuid']
        self.addDetail(
            'nets',
            content.text_content(json.dumps(nets, indent=4, sort_keys=True)))

        for name, uuid in nets.items():
            n = self.system_client.get_network(name)
            self.assertEqual(uuid, n['uuid'])

        self._await_networks_ready(['barry_net'])

        inst_uuids = {}
        for name in ['barry', 'dave', 'trouble-writing-tests']:
            new_inst = self.test_client.create_instance(
                name, 1, 1024,
                [
                    {
                        'network_uuid': 'barry_net'
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': base.CLUSTER_CI_IMAGE,
                        'type': 'disk'
                    }
                ], None, None, namespace=self.namespace)
            self.addDetail(
                'new_inst_%s' % name,
                content.text_content(json.dumps(new_inst, indent=4,
                                                sort_keys=True)))
            inst_uuids[name] = new_inst['uuid']
        self.addDetail(
            'inst_uuids',
            content.text_content(json.dumps(inst_uuids, indent=4,
                                            sort_keys=True)))

        # Get instance by name
        for name, uuid in inst_uuids.items():
            inst = self.system_client.get_instance(name)
            self.addDetail(
                'inst_%s' % name,
                content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
            self.assertEqual(uuid, inst['uuid'])


class TestSameNameLookup(base.BaseNamespacedTestCase):
    """Functional tests for same-name cross-namespace lookup (phase 3 SQL pushdown).

    Verifies that after the Instance and Network from_db_by_ref overrides land,
    each namespace client resolves its own object when two namespaces share an
    object name, and that the system client receives either a 400
    (MultipleObjects surfaced as RequestMalformedException) or a 200 returning
    one of the two known UUIDs — never a 404.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'samenametest'
        super().__init__(*args, **kwargs)

    def test_instance_same_name_different_namespace(self):
        """Two namespaces each own an instance called 'shared-name'.

        get_instance('shared-name') from each namespace's client must return
        the instance belonging to *that* namespace (verified by UUID). The
        system client query must not return 404 — a 400 (ambiguous) or 200
        (one UUID returned silently) are both acceptable outcomes.

        A minimal empty disk is provided so the server accepts the request,
        but no base image is specified so nothing is downloaded and the
        instance never boots. This keeps wall time minimal and avoids any
        dependency on image availability or hypervisor capacity.
        """
        inst_name = 'shared-name'
        # Minimal disk spec: empty 1 GB disk, no base image to download
        minimal_disk = [{'size': 1, 'type': 'disk'}]

        ns_b_name = self.namespace + '-b'
        ns_b_key = self._uniquifier()
        client_b = self._make_namespace(ns_b_name, ns_b_key)

        try:
            # Namespace A instance (self.namespace / self.test_client)
            inst_a = self.test_client.create_instance(
                inst_name, 1, 128, None, minimal_disk, None, None,
                namespace=self.namespace)
            self.addDetail(
                'inst_a',
                content.text_content(json.dumps(inst_a, indent=4, sort_keys=True)))

            # Namespace B instance
            inst_b = client_b.create_instance(
                inst_name, 1, 128, None, minimal_disk, None, None,
                namespace=ns_b_name)
            self.addDetail(
                'inst_b',
                content.text_content(json.dumps(inst_b, indent=4, sort_keys=True)))

            self.assertNotEqual(
                inst_a['uuid'], inst_b['uuid'],
                'Expected different UUIDs for same name in different namespaces')

            # Each namespace client must resolve its own instance by name
            resolved_a = self.test_client.get_instance(inst_name)
            self.addDetail(
                'resolved_a',
                content.text_content(json.dumps(resolved_a, indent=4, sort_keys=True)))
            self.assertEqual(
                inst_a['uuid'], resolved_a['uuid'],
                'Namespace A client should resolve to its own instance')

            resolved_b = client_b.get_instance(inst_name)
            self.addDetail(
                'resolved_b',
                content.text_content(json.dumps(resolved_b, indent=4, sort_keys=True)))
            self.assertEqual(
                inst_b['uuid'], resolved_b['uuid'],
                'Namespace B client should resolve to its own instance')

            # System client: ambiguous lookup — accept 400 or 200, never 404
            try:
                resolved_sys = self.system_client.get_instance(inst_name)
                self.addDetail(
                    'resolved_sys',
                    content.text_content(json.dumps(resolved_sys, indent=4, sort_keys=True)))
                self.assertIn(
                    resolved_sys['uuid'],
                    {inst_a['uuid'], inst_b['uuid']},
                    'System client resolved an unexpected instance UUID')
            except apiclient.RequestMalformedException:
                # Expected after phase 3: MultipleObjects surfaces as 400
                pass

        finally:
            # Delete instances immediately (they never booted, deletion is fast)
            for client, inst in [(self.test_client, inst_a), (client_b, inst_b)]:
                try:
                    client.delete_instance(inst['uuid'])
                except apiclient.ResourceNotFoundException:
                    pass
            try:
                self.system_client.delete_namespace(ns_b_name)
            except (apiclient.ResourceNotFoundException,
                    apiclient.RequestMalformedException):
                # RequestMalformedException if instances are still being
                # asynchronously deleted.
                pass

    def test_instance_system_creds_namespace_scoped(self):
        """System creds + explicit `namespace=` scope strictly.

        Regression test for the bug where a system caller passing
        ``namespace=A`` could receive a same-named instance from
        namespace B because the server collapsed system creds to
        "search every namespace" before applying the body's namespace
        filter. The fix is in `arg_is_instance_ref`.
        """
        inst_name = 'shared-name'
        minimal_disk = [{'size': 1, 'type': 'disk'}]

        ns_b_name = self.namespace + '-b'
        ns_b_key = self._uniquifier()
        client_b = self._make_namespace(ns_b_name, ns_b_key)

        inst_a = None
        inst_b = None
        try:
            inst_a = self.test_client.create_instance(
                inst_name, 1, 128, None, minimal_disk, None, None,
                namespace=self.namespace)
            inst_b = client_b.create_instance(
                inst_name, 1, 128, None, minimal_disk, None, None,
                namespace=ns_b_name)
            self.assertNotEqual(inst_a['uuid'], inst_b['uuid'])

            # System creds, explicit namespace=A → must be A's UUID.
            scoped_a = self.system_client.get_instance(
                inst_name, namespace=self.namespace)
            self.addDetail(
                'scoped_a',
                content.text_content(json.dumps(scoped_a, indent=4, sort_keys=True)))
            self.assertEqual(
                inst_a['uuid'], scoped_a['uuid'],
                'System client with namespace=A should resolve A, not B')

            # System creds, explicit namespace=B → must be B's UUID.
            scoped_b = self.system_client.get_instance(
                inst_name, namespace=ns_b_name)
            self.addDetail(
                'scoped_b',
                content.text_content(json.dumps(scoped_b, indent=4, sort_keys=True)))
            self.assertEqual(
                inst_b['uuid'], scoped_b['uuid'],
                'System client with namespace=B should resolve B, not A')

            # Tenant A attempting to query namespace B by name must fail.
            self.assertRaises(
                apiclient.ResourceNotFoundException,
                self.test_client.get_instance, inst_name,
                namespace=ns_b_name)
        finally:
            for client, inst in [
                    (self.test_client, inst_a),
                    (client_b, inst_b)]:
                if inst is None:
                    continue
                try:
                    client.delete_instance(inst['uuid'])
                except apiclient.ResourceNotFoundException:
                    pass
            try:
                self.system_client.delete_namespace(ns_b_name)
            except (apiclient.ResourceNotFoundException,
                    apiclient.RequestMalformedException):
                pass

    def test_network_same_name_different_namespace(self):
        """Two namespaces each own a network called 'shared-net'.

        get_network('shared-net') from each namespace's client must return
        the network belonging to *that* namespace (verified by UUID). The
        system client query must not return 404.
        """
        net_name = 'shared-net'

        ns_b_name = self.namespace + '-b'
        ns_b_key = self._uniquifier()
        client_b = self._make_namespace(ns_b_name, ns_b_key)

        net_a = None
        net_b = None
        try:
            # Namespace A network (self.namespace / self.test_client)
            net_a = self.test_client.allocate_network(
                '10.100.0.0/24', True, True, net_name)
            self.addDetail(
                'net_a',
                content.text_content(json.dumps(net_a, indent=4, sort_keys=True)))

            # Namespace B network — use a non-overlapping prefix so the
            # allocator does not reject it for address-space reasons.
            net_b = client_b.allocate_network(
                '10.101.0.0/24', True, True, net_name)
            self.addDetail(
                'net_b',
                content.text_content(json.dumps(net_b, indent=4, sort_keys=True)))

            self.assertNotEqual(
                net_a['uuid'], net_b['uuid'],
                'Expected different UUIDs for same name in different namespaces')

            # Each namespace client must resolve its own network by name
            resolved_a = self.test_client.get_network(net_name)
            self.addDetail(
                'resolved_a',
                content.text_content(json.dumps(resolved_a, indent=4, sort_keys=True)))
            self.assertEqual(
                net_a['uuid'], resolved_a['uuid'],
                'Namespace A client should resolve to its own network')

            resolved_b = client_b.get_network(net_name)
            self.addDetail(
                'resolved_b',
                content.text_content(json.dumps(resolved_b, indent=4, sort_keys=True)))
            self.assertEqual(
                net_b['uuid'], resolved_b['uuid'],
                'Namespace B client should resolve to its own network')

            # System client: ambiguous lookup — accept 400 or 200, never 404
            try:
                resolved_sys = self.system_client.get_network(net_name)
                self.addDetail(
                    'resolved_sys',
                    content.text_content(json.dumps(resolved_sys, indent=4, sort_keys=True)))
                self.assertIn(
                    resolved_sys['uuid'],
                    {net_a['uuid'], net_b['uuid']},
                    'System client resolved an unexpected network UUID')
            except apiclient.RequestMalformedException:
                # Expected after phase 3: MultipleObjects surfaces as 400
                pass

        finally:
            # Delete networks then namespace B
            for client, net in [
                    (self.test_client, net_a),
                    (client_b, net_b)]:
                if net is None:
                    continue
                try:
                    client.delete_network(net['uuid'])
                except (apiclient.ResourceNotFoundException,
                        apiclient.ResourceStateConflictException):
                    pass
            try:
                self.system_client.delete_namespace(ns_b_name)
            except (apiclient.ResourceNotFoundException,
                    apiclient.RequestMalformedException):
                # RequestMalformedException if networks are still being
                # asynchronously deleted.
                pass

    def test_network_system_creds_namespace_scoped(self):
        """System creds + explicit `namespace=` scope strictly (networks).

        Companion to test_instance_system_creds_namespace_scoped — same
        regression, network decorator.
        """
        net_name = 'shared-net'

        ns_b_name = self.namespace + '-b'
        ns_b_key = self._uniquifier()
        client_b = self._make_namespace(ns_b_name, ns_b_key)

        net_a = None
        net_b = None
        try:
            net_a = self.test_client.allocate_network(
                '10.110.0.0/24', True, True, net_name)
            net_b = client_b.allocate_network(
                '10.111.0.0/24', True, True, net_name)
            self.assertNotEqual(net_a['uuid'], net_b['uuid'])

            scoped_a = self.system_client.get_network(
                net_name, namespace=self.namespace)
            self.assertEqual(
                net_a['uuid'], scoped_a['uuid'],
                'System client with namespace=A should resolve A, not B')

            scoped_b = self.system_client.get_network(
                net_name, namespace=ns_b_name)
            self.assertEqual(
                net_b['uuid'], scoped_b['uuid'],
                'System client with namespace=B should resolve B, not A')

            self.assertRaises(
                apiclient.ResourceNotFoundException,
                self.test_client.get_network, net_name,
                namespace=ns_b_name)
        finally:
            for client, net in [
                    (self.test_client, net_a),
                    (client_b, net_b)]:
                if net is None:
                    continue
                try:
                    client.delete_network(net['uuid'])
                except (apiclient.ResourceNotFoundException,
                        apiclient.ResourceStateConflictException):
                    pass
            try:
                self.system_client.delete_namespace(ns_b_name)
            except (apiclient.ResourceNotFoundException,
                    apiclient.RequestMalformedException):
                pass
