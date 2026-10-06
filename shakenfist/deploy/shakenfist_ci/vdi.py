# Copyright 2026 Michael Still and contributors
"""What the VDI console tests share.

test_vdi_console_file.py, test_vdi_tokens.py and
test_vdi_kerbside_exchange.py all create a minimal SPICE instance, parse
virt-viewer (.vv) files, or decide whether a Kerbside console proxy is
there to test. Those live here, once, on a base class the three test
classes derive from. It has no test methods, and it is outside
cluster-ci.conf's test path, so stestr never runs it on its own.
"""

import configparser
import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


# Shaken Fist allocates every console port of an instance from this range,
# inclusive (Instance._allocate_console_port() in shakenfist/instance.py). A
# port in it is a hypervisor's port, so a .vv which claims to name a proxy
# must not carry one.
CONSOLE_PORT_MIN = 30000
CONSOLE_PORT_MAX = 50000


class BaseVDIConsoleTestCase(base.BaseNamespacedTestCase):
    def _require_vdi_console_proxy(self):
        """Skip unless the cluster advertises a VDI console proxy.

        A cluster without Kerbside legitimately runs the feature off, so the
        test skips there -- unless the deploy profile said Kerbside was
        expected (SF_CI_EXPECT_VDI_CONSOLE_PROXY=1, see
        base.kerbside_expected()), in which case a missing capability is a
        broken deploy and the test fails.
        """
        if self.test_client.check_capability('vdi-console-proxy'):
            return
        if base.kerbside_expected():
            self.fail(
                'SF_CI_EXPECT_VDI_CONSOLE_PROXY=1 says this cluster was '
                'deployed with Kerbside, but it does not advertise the '
                'vdi-console-proxy capability')
        self.skipTest(
            'The cluster does not advertise the vdi-console-proxy '
            'capability (Kerbside is not configured); skipping')

    def _create_spice_instance(self, client, namespace):
        """Create a minimal SPICE instance and wait for it to be created.

        A minimal diskless instance: no base image is downloaded so nothing
        boots to an OS, but the VM still reaches the created state, which is
        all the mint endpoint requires and the only state Kerbside scrapes.
        video is left unset so the server applies its default SPICE console.
        """
        minimal_disk = [{'size': 1, 'type': 'disk'}]
        inst = self.create_instance(
            'vdi-%s' % self._uniquifier(), 1, 128, None, minimal_disk,
            None, None, client=client, namespace=namespace)
        self.addDetail(
            'instance',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_create(inst['uuid'])
        return inst['uuid']

    def _mint_vdi_console_proxy(self, instance_uuid):
        """Mint a console token, failing the test if the mint is refused.

        Only for a cluster which advertises the capability: there the
        feature is on and the mint must work, so a 404 means the endpoint is
        not there despite the advertisement, and a 500 means no signing key
        is provisioned. Both are failures.
        """
        try:
            return self.test_client.get_vdi_console_proxy(instance_uuid)
        except apiclient.ResourceNotFoundException:
            self.fail(
                'vdiconsoleproxy returned 404 although the vdi-console-proxy '
                'capability is advertised: the endpoint is not configured '
                '(KERBSIDE_URL unset?) or the instance was not found')
        except apiclient.InternalServerError:
            self.fail(
                'vdiconsoleproxy returned 500 although the vdi-console-proxy '
                'capability is advertised: no signing key is provisioned '
                '(sf-ctl ensure-kerbside-signing-key has not run?)')

    def _parse_vv(self, vv_text):
        """Parse a .vv file and return its [virt-viewer] section.

        The file must parse as an INI with a [virt-viewer] section. Values
        are taken literally: no interpolation, and only '=' delimits, since
        the ca and host-subject values carry ':' and ','.
        """
        cp = configparser.ConfigParser(delimiters=('=',), interpolation=None)
        cp.read_string(vv_text)
        self.assertIn('virt-viewer', cp.sections())
        return cp['virt-viewer']

    def _fetch_direct_vv(self, instance_uuid, detail_name='vv_file'):
        """Fetch and parse an instance's direct-to-hypervisor .vv file."""
        vv_text = self.test_client._request_url(
            'GET', '/instances/%s/vdiconsolehelper' % instance_uuid).text
        self.addDetail(detail_name, content.text_content(vv_text))
        return self._parse_vv(vv_text)
