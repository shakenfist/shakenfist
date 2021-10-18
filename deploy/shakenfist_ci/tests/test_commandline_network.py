import json
import sys
import time

from oslo_concurrency import processutils

from shakenfist_ci import base


def _exec(cmd):
    sys.stderr.write('\n----- Exec: %s -----\n' % cmd)
    out, err = processutils.execute(cmd, shell=True)
    for line in out.split('\n'):
        sys.stderr.write('out: %s\n' % line)
    sys.stderr.write('\n')
    for line in err.split('\n'):
        sys.stderr.write('err: %s\n' % line)
    sys.stderr.write('\n----- End: %s -----\n' % cmd)
    return out


class TestNetworkCommandLine(base.BaseNamespacedTestCase):
    """Make sure the command line client works."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'network-commandline'
        super(TestNetworkCommandLine, self).__init__(*args, **kwargs)

    def _exec_client(self, cmd):
        return _exec('sf-client --apiurl %s --namespace %s --key %s %s'
                     % (self.test_client.base_url, self.namespace,
                        self.namespace_key, cmd))

    def test_network_commands(self):
        # An invalid netblock
        self.assertRaises(
            processutils.ProcessExecutionError, self._exec_client,
            'network create %s-net 192.168.1.2/24' % self.namespace)

        # Create
        self.assertRegexpMatches(
            self._exec_client('network create %s-net 192.168.1.0/24'
                              % self.namespace),
            '.*uuid .*')

        # List
        self.assertRegexpMatches(
            self._exec_client('network list'), '.*192.168.1.0/24.*')
        out = json.loads(self._exec_client('--json network list'))
        net_uuid = out['networks'][0]['uuid']
        self.assertRegexpMatches(
            self._exec_client('--simple network list'),
            '.*%s,.*' % net_uuid)

        # Show
        self.assertRegexpMatches(
            self._exec_client('network show %s' % net_uuid),
            '.*provide dhcp.*')
        self.assertRegexpMatches(
            self._exec_client('--simple network show %s' % net_uuid),
            '.*%s.*' % net_uuid)
        json.loads(self._exec_client('--json network show %s' % net_uuid))

        # Metadata
        self.assertNotRegexpMatches(
            self._exec_client('network show %s' % net_uuid),
            '.*gibbon.*')
        self._exec_client('network set-metadata %s funky gibbon' % net_uuid)
        self.assertRegexpMatches(
            self._exec_client('network show %s' % net_uuid),
            '.*gibbon.*')
        self._exec_client('network delete-metadata %s funky' % net_uuid)
        self.assertNotRegexpMatches(
            self._exec_client('network show %s' % net_uuid),
            '.*gibbon.*')

        # Sleep for a bit and then make sure events are reasonable
        time.sleep(240)
        self._exec_client('--simple network events %s' % net_uuid)

        # UPDATE_DHCP_RE = re.compile('.*update dhcp.*finish.*')
        # TODO(mikal): finish this!
