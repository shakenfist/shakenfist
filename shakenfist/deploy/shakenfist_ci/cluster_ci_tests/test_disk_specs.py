import json
import uuid

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestDiskSpecifications(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'diskspecs'
        super().__init__(*args, **kwargs)

    def test_default(self):
        inst = self.create_instance(
            'test-default-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        results = self._await_command(inst['uuid'], 'df -h')
        self.addDetail(
            'results',
            content.text_content(json.dumps(results, indent=4, sort_keys=True)))
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue('vda' in results['stdout'])

    def test_unfetchable_base(self):
        """A base no fetch could resolve is refused at create time.

        Issue 4557: each of these used to be accepted, scheduled, and
        then failed on the hypervisor with a requests exception,
        leaving the instance in error with no error message at all.
        The bare uuid is the mistake which prompted the issue -- it is
        the one string every artifact listing shows -- so its refusal
        is also read, to check it names the form which would have
        worked. Whether the uuid names a real artifact does not matter
        to the guard, and a literal one keeps this test independent of
        what the cloud happens to hold.

        A unit test proves the handler answers 400. Only a cluster
        proves that the shipped client and the whole decorator stack
        in front of that handler do not turn it into something else
        first.
        """
        bare_uuid = str(uuid.uuid4())
        bad_bases = [
            'ova:///srv/x.ova/disk.vmdk',
            'sf://instance/%s/vda' % bare_uuid,
            'not-an-image',
            bare_uuid,
        ]

        for bad_base in bad_bases:
            try:
                # raw-create: asserts the 400 an unfetchable disk base
                # gets, which is raised validating the request long
                # before the scheduler runs.
                self.test_client.create_instance(
                    'test-unfetchable-base', 1, 1024, None,
                    [
                        {
                            'size': 8,
                            'base': bad_base,
                            'type': 'disk'
                        }
                    ], None, None)
                self.fail('Unfetchable disk base %s was accepted' % bad_base)
            except apiclient.RequestMalformedException as e:
                self.addDetail(
                    'response for %s' % bad_base,
                    content.text_content(str(e.text)))
                self.assertEqual(400, e.status_code)
                body = json.loads(e.text)
                self.assertIn('cannot be fetched', body['error'])

                if bad_base == bare_uuid:
                    self.assertIn('sf://artifact/%s' % bare_uuid,
                                  body['error'])

    def test_bad_bus(self):
        self.assertRaises(
            apiclient.RequestMalformedException,
            # raw-create: asserts the 400 a malformed disk bus gets, which is
            # raised validating the request long before the scheduler runs.
            self.test_client.create_instance,
            'test-bad-bus-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk',
                    'bus': 'banana'
                }
            ], None, None)
