import json
import sys
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_ci import process


def _exec(cmd, env=None):
    # mask_secrets() on the way to stderr is a backstop, not the
    # protection: _exec_client() hands the namespace key to sf-client
    # through env rather than building it into cmd, so there is nothing
    # here to mask. See process.SECRET_FLAG_RE.
    sys.stderr.write('\n----- Exec: %s -----\n' % process.mask_secrets(cmd))
    out, err = process.execute(cmd, shell=True, env=env)
    for line in out.split('\n'):
        sys.stderr.write('out: %s\n' % line)
    sys.stderr.write('\n')
    for line in err.split('\n'):
        sys.stderr.write('err: %s\n' % line)
    sys.stderr.write('\n----- End: %s -----\n' % process.mask_secrets(cmd))
    return out


class TestNetworkCommandLine(base.BaseNamespacedTestCase):
    """Make sure the command line client works."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'network-commandline'
        super().__init__(*args, **kwargs)

    def _exec_client(self, cmd):
        # sf-client has read SHAKENFIST_API_URL, SHAKENFIST_NAMESPACE and
        # SHAKENFIST_KEY since v0.2.5. Using them keeps the namespace key
        # out of the command line, which _exec() writes to stderr on every
        # invocation and which the harness prints again on failure.
        return _exec('sf-client %s' % cmd,
                     env={
                         'SHAKENFIST_API_URL': self.test_client.base_url,
                         'SHAKENFIST_NAMESPACE': self.namespace,
                         'SHAKENFIST_KEY': self.namespace_key,
                     })

    def test_network_commands(self):
        # An invalid netblock
        self.assertRaises(
            process.ProcessExecutionError, self._exec_client,
            'network create %s-net 192.168.1.2/24' % self.namespace)

        # Create
        self.assertRegex(
            self._exec_client('network create %s-net 192.168.1.0/24'
                              % self.namespace),
            '.*uuid .*')

        # List
        self.assertRegex(
            self._exec_client('network list'), '.*192.168.1.0/24.*')
        out = json.loads(self._exec_client('--json network list'))
        self.addDetail(
            'network list out',
            content.text_content(json.dumps(out, indent=4, sort_keys=True)))
        net_uuid = out[0]['uuid']
        self.assertRegex(
            self._exec_client('--simple network list'),
            '.*%s,.*' % net_uuid)

        # Show
        self.assertRegex(
            self._exec_client('network show %s' % net_uuid),
            '.*provide dhcp.*')
        self.assertRegex(
            self._exec_client('--simple network show %s' % net_uuid),
            '.*%s.*' % net_uuid)
        json.loads(self._exec_client('--json network show %s' % net_uuid))

        # Metadata
        self.assertNotRegex(
            self._exec_client('network show %s' % net_uuid),
            '.*gibbon.*')
        self._exec_client('network set-metadata %s funky gibbon' % net_uuid)
        self.assertRegex(
            self._exec_client('network show %s' % net_uuid),
            '.*gibbon.*')
        self._exec_client('network delete-metadata %s funky' % net_uuid)
        self.assertNotRegex(
            self._exec_client('network show %s' % net_uuid),
            '.*gibbon.*')

        # Sleep for a bit and then make sure events are reasonable
        time.sleep(240)
        self._exec_client('--simple network events %s' % net_uuid)

        # UPDATE_DHCP_RE = re.compile('.*update dhcp.*finish.*')
        # TODO(mikal): finish this!
