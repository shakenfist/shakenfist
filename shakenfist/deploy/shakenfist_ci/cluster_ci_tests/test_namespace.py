import copy
import json
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestNamespace(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'namespace_test'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_namespace_create_delete_and_list(self):
        self.system_client.create_namespace('a')
        self.system_client.create_namespace('b')
        self.system_client.create_namespace('c')
        self.system_client.create_namespace('d')
        self.system_client.delete_namespace('c')

        namespaces = base.namespace_names(self.system_client.get_namespaces())
        self.addDetail(
            'namespaces',
            content.text_content(json.dumps(namespaces, indent=4,
                                            sort_keys=True)))

        self.assertIn('a', namespaces)
        self.assertIn('b', namespaces)
        self.assertNotIn('c', namespaces)
        self.assertIn('d', namespaces)
        self.assertNotIn('e', namespaces)

    def test_namespace_create_rejects_invalid_names(self):
        # A namespace name is rendered verbatim into dnsmasq's
        # conf-file on the network node (domain=<namespace>.<zone>), so
        # creation refuses any name which is not inert there: a newline
        # would inject arbitrary dnsmasq directives (issue 4250), a dot
        # would silently add DNS domain levels, and 63 characters is
        # the DNS label limit.
        for name in ['evil\ndhcp-script=/tmp/pwn',
                     'dotted.name',
                     'a' * 64]:
            self.assertRaises(
                apiclient.RequestMalformedException,
                self.system_client.create_namespace, name)

    def _await_instance_state(self, instance_uuid, state, timeout=300):
        # Unlike base._await_instance_deleted(), error is not success
        # here: telling error from deleted is the point of the test.
        start_time = time.time()
        while True:
            try:
                i = self.system_client.get_instance(instance_uuid)
            except apiclient.ResourceNotFoundException:
                i = None
            if not i:
                # Hard deleted between polls. That is as gone as
                # deleted, and a failure for any other target.
                if state == 'deleted':
                    return None
                self.fail('Instance %s vanished while awaiting state %s'
                          % (instance_uuid, state))
            if i['state'] == state:
                return i
            # Deleted is the one state nothing leaves, so reaching it
            # while awaiting another cannot come right by waiting. Error
            # is not treated the same way, because an errored instance
            # which has just been asked to delete still reads error
            # until the delete is picked up.
            if i['state'] == 'deleted':
                break
            if time.time() - start_time > timeout:
                break
            time.sleep(5)

        self.addDetail(
            'instance', content.text_content(json.dumps(i, indent=4,
                                                        sort_keys=True)))
        self.fail('Instance %s did not reach state %s within %d seconds, it '
                  'is in state %s' % (instance_uuid, state, timeout,
                                      i['state']))

    def _delete_instance_if_present(self, instance_uuid, client=None):
        # Through system rather than the instance's own namespace: on
        # the passing path that namespace has been deleted by the time
        # this runs, and a deleted namespace's credential is refused
        # with a 401 however harmless the request. A caller which needs
        # a client that does not wait on operations passes its own.
        if client is None:
            client = self.system_client
        try:
            client.delete_instance(instance_uuid)
        except apiclient.ResourceNotFoundException:
            pass

    def test_namespace_delete_refuses_an_errored_instance(self):
        """Issue 4847: an errored instance blocks its namespace's delete.

        An instance in error was counted as already gone, so the
        namespace was deleted over the top of a live row which then had
        no namespace, no owner and nothing which would ever sweep it.

        The instance here reaches error through an image which cannot be
        fetched rather than through a delete which failed, because that
        is the path reachable from the API and the refusal reads the
        state rather than how it got there.
        """
        # Kept short: a namespace's name is its object_states key, which
        # is VARCHAR(36), so a name built on self.namespace (26
        # characters here) overflows it and the create fails with a 500
        # even though the API accepts names up to 63 characters.
        other = 'ci-errored-%s' % self._uniquifier()
        other_client = self._make_namespace(other, self._uniquifier())
        self.addCleanup(self._remove_namespace, other)

        inst = self.create_instance(
            'errored', 1, 1024, [],
            [
                {
                    'size': 8,
                    'base': 'https://nosuch.shakenfist.com/foo',
                    'type': 'disk'
                }
            ], None, None, client=other_client)
        # Registered after the namespace cleanup so that it runs first:
        # BaseNamespacedTestCase only tears down instances in
        # self.namespace, and with this one left behind the namespace
        # removal would be refused -- which is this test's subject --
        # and the instance and namespace would both leak. On the
        # passing path the test has already deleted both the instance
        # and its namespace, so the cleanup must not need either.
        self.addCleanup(self._delete_instance_if_present, inst['uuid'])
        self.addDetail('inst', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        # An unfetchable image is deferred and retried before the fetch
        # is declared dead (15, 30 and 60 seconds), and the instance
        # then passes through creating-error on its way to error, so
        # this wait is longer than the deleted one below.
        self._await_instance_state(inst['uuid'], 'error', timeout=600)

        e = self.assertRaises(
            apiclient.RequestMalformedException,
            self.system_client.delete_namespace, other)
        self.assertIn('you cannot delete a namespace with instances',
                      str(e.text))
        self.assertIn(other,
                      base.namespace_names(self.system_client.get_namespaces()))

        # Clearing the instance is what makes the namespace deletable,
        # and a deleted instance does not block: it has released
        # everything it held and is hard deleted by state rather than
        # through its namespace.
        other_client.delete_instance(inst['uuid'])
        self._await_instance_state(inst['uuid'], 'deleted')

        self.system_client.delete_namespace(other)
        self.assertNotIn(
            other, base.namespace_names(self.system_client.get_namespaces()))

    def _await_network_state(self, network_uuid, state, timeout=300):
        start_time = time.time()
        while True:
            n = self.system_client.get_network(network_uuid)
            if n['state'] == state:
                return n
            # Deleted is the one state nothing leaves, so reaching it
            # while awaiting another cannot come right by waiting.
            if n['state'] == 'deleted' and state != 'deleted':
                break
            if time.time() - start_time > timeout:
                break
            time.sleep(5)

        self.addDetail(
            'network', content.text_content(json.dumps(n, indent=4,
                                                       sort_keys=True)))
        self.fail('Network %s did not reach state %s within %d seconds, it '
                  'is in state %s' % (network_uuid, state, timeout,
                                      n['state']))

    def _delete_network_if_present(self, client, network_uuid):
        try:
            client.delete_network(network_uuid)
        except (apiclient.ResourceNotFoundException,
                apiclient.ResourceStateConflictException):
            pass

    def test_namespace_delete_refuses_a_draining_network(self):
        """Issue 4863: a network mid-delete blocks its namespace's delete.

        A network in delete-wait or error was counted as already gone,
        because the handler asked Network.is_dead(). Neither state is in
        FINAL_OBJECT_STATES, so nothing would ever reap the network, and
        the namespace delete left it with no namespace and no owner --
        still holding its vxlan, its dnsmasq, its NAT rules and its IPAM.

        delete-wait is the state this test can reach from the API: a
        network deleted while an interface is still attached parks there
        while the delete op waits for the interface to drain. The error
        state is only reachable by exhausting the floating network, so it
        is covered by unit tests rather than here.
        """
        # Kept short for the same VARCHAR(36) reason as the errored
        # instance test above.
        other = 'ci-draining-%s' % self._uniquifier()
        other_client = self._make_namespace(other, self._uniquifier())
        self.addCleanup(self._remove_namespace, other)

        # The network delete below deliberately leaves a cluster
        # operation in flight, so every delete here is issued through an
        # admin client which does not wait on an object's operations.
        # Admin rather than the namespace's own client because the
        # cleanups registered below run after the namespace has been
        # deleted on the passing path, and a key in a deleted namespace
        # no longer authenticates.
        nb_client = apiclient.Client(async_strategy=apiclient.ASYNC_CONTINUE)

        net = other_client.allocate_network(
            '192.168.243.0/24', True, True, 'draining-net')
        self.addCleanup(self._delete_network_if_present, nb_client,
                        net['uuid'])
        self.addDetail('net_draining', content.text_content(json.dumps(
            net, indent=4, sort_keys=True)))
        self._await_networks_ready([net['uuid']])

        # The instance holding the interface lives in the system
        # namespace, not in the namespace under test. The delete handler
        # refuses on instances before it looks at networks, so an
        # instance of the namespace's own would always be what the
        # refusal names, and the network check this test is about would
        # never be reached. An admin may attach an instance to any
        # namespace's network, so this keeps the network draining while
        # leaving it as the only thing which blocks the delete.
        inst = self.create_instance(
            'draining', 1, 1024,
            [
                {
                    'network_uuid': net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None, client=self.system_client)
        # Registered after the network and namespace cleanups so that it
        # runs before them: the network cannot finish draining while the
        # instance holds an interface on it, and the namespace cannot be
        # removed while the network is live.
        self.addCleanup(self._delete_instance_if_present, inst['uuid'],
                        client=nb_client)
        self.addDetail('inst', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        # Ready rather than merely created, because the refusal is about
        # a network whose interface has not drained -- and an instance
        # which is still being created may not have attached one yet.
        self._await_instance_ready(inst['uuid'])

        nb_client.delete_network(net['uuid'])
        self._await_network_state(net['uuid'], 'delete-wait', timeout=120)

        e = self.assertRaises(
            apiclient.RequestMalformedException,
            self.system_client.delete_namespace, other)
        self.assertIn('you cannot delete a namespace with networks',
                      str(e.text))
        self.assertIn(other,
                      base.namespace_names(self.system_client.get_namespaces()))

        # Draining the interface is what lets the delete finish, and a
        # deleted network does not block: it has released everything it
        # held and is hard deleted by state rather than through its
        # namespace.
        nb_client.delete_instance(inst['uuid'])
        self._await_instance_deleted(inst['uuid'])
        self._await_network_state(net['uuid'], 'deleted', timeout=600)

        self.system_client.delete_namespace(other)
        self.assertNotIn(
            other, base.namespace_names(self.system_client.get_namespaces()))

    def test_namespace_clean(self):
        """Check that instances and networks are cleaned from namespace

        The "clean namespace" command line functionality uses
        delete_all_instances() and delete_all_networks() in quick succession to
        delete all components in a namespace. This test replicates this calling
        pattern.
        """

        NUM_INSTANCES = 6
        LONG_WAIT_MINS = 5
        SHORT_WAIT_MINS = 2

        inst_uuids = set()
        for i in range(NUM_INSTANCES):
            new_inst = self.create_instance(
                'test-%s' % i, 1, 1024,
                [
                    {
                        'network_uuid': self.net['uuid']
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
                'new_inst_%s' % i,
                content.text_content(json.dumps(new_inst, indent=4,
                                                sort_keys=True)))
            inst_uuids.add(new_inst['uuid'])

        # Wait for all instances to start
        for uuid in inst_uuids:
            self._await_instance_ready(uuid)

        # Run the test
        self.test_client.delete_all_instances(self.namespace)
        self.test_client.delete_all_networks(self.namespace, clean_wait=True)

        # Wait for instances to be deleted
        start_time = time.time()
        while inst_uuids:
            for uuid in copy.copy(inst_uuids):
                i = self.system_client.get_instance(uuid)
                if i['state'] in ['deleted']:
                    inst_uuids.remove(uuid)
            if time.time() - start_time > LONG_WAIT_MINS * 60:
                break
            time.sleep(5)

        self.assertEqual(0, len(inst_uuids),
                         'Instances not deleted: %s' % inst_uuids)

        start_time = time.time()
        while time.time() - start_time < SHORT_WAIT_MINS * 60:
            test_net = self.test_client.get_network(self.net['uuid'])
            if test_net['state'] in ['deleted', 'error']:
                break
            time.sleep(5)
        self.addDetail(
            'test_net',
            content.text_content(json.dumps(test_net, indent=4, sort_keys=True)))

        self.assertEqual('deleted', test_net['state'],
                         'Network not deleted by delete_all_networks()')
