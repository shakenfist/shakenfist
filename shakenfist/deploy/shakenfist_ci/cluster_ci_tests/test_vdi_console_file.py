import configparser
import json
import re

from testtools import content

from shakenfist_ci import base


UUID_RE = re.compile(
    '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


class TestVDIConsoleFile(base.BaseNamespacedTestCase):
    """Fetch the direct-to-hypervisor .vv file and parse its content.

    Issue 4009: the file from /instances/<ref>/vdiconsolehelper carried
    the placement node UUID as the host, Shaken Fist's internal VDI enum
    (spiceconcurrent) as the type, and no host-subject -- and nothing in
    CI ever parsed a .vv, which is how all three shipped. Unlike the
    proxy mint path (test_vdi_tokens.py), the direct path has no
    Kerbside precondition, so this test always runs.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'vvfile'
        super().__init__(*args, **kwargs)

    def _fetch_vv(self, video=None):
        """Create a minimal instance and return its parsed direct .vv.

        A minimal diskless instance: nothing boots to an OS, but the VM
        reaches the created state with its console ports allocated,
        which is all the .vv generator reads.
        """
        minimal_disk = [{'size': 1, 'type': 'disk'}]
        inst = self.test_client.create_instance(
            'vvfile-%s' % self._uniquifier(), 1, 128, None, minimal_disk,
            None, None, namespace=self.namespace, video=video)
        self._await_instance_create(inst['uuid'])

        vv_text = self.test_client._request_url(
            'GET', '/instances/%s/vdiconsolehelper' % inst['uuid']).text
        self.addDetail('vv_file', content.text_content(vv_text))

        # The file must parse as an INI with a [virt-viewer] section.
        cp = configparser.ConfigParser(delimiters=('=',), interpolation=None)
        cp.read_string(vv_text)
        self.assertIn('virt-viewer', cp.sections())
        return cp['virt-viewer']

    def test_direct_vv_file_is_valid(self):
        # video is left unset so the server applies its default SPICE
        # console.
        vv = self._fetch_vv()

        # virt-viewer accepts exactly these types; anything else (like the
        # internal spiceconcurrent enum) is "Unsupported graphic type".
        #
        # Note that this assertion alone does not hold the type half of
        # issue 4009 in place: an instance created with video unset gets
        # vdi 'spice' from the server's default, so the value is already
        # 'spice' before instance.py's spice* collapse ever runs.
        # test_spiceconcurrent_vv_type_is_collapsed below is what
        # actually exercises the collapse.
        self.assertIn(vv['type'], ('spice', 'vnc'))

        # The host must be a connectable address for a cluster node, not
        # the placement node's UUID.
        nodes = self._get_cluster_nodes()
        self.addDetail(
            'nodes',
            content.text_content(json.dumps(nodes, indent=4, sort_keys=True)))
        self.assertIsNone(
            UUID_RE.match(vv['host']),
            'the .vv host is a UUID, which resolves nowhere: %s' % vv['host'])
        known_addresses = set()
        for n in nodes:
            known_addresses.add(n['ip'])
            known_addresses.add(n['name'])
        self.assertIn(vv['host'], known_addresses)

        # At least one connection port, and every port numeric.
        self.assertTrue(
            'port' in vv or 'tls-port' in vv,
            'the .vv carries no port at all')
        for port_key in ('port', 'tls-port'):
            if port_key in vv:
                self.assertTrue(
                    vv[port_key].isdigit(),
                    '%s is not numeric: %s' % (port_key, vv[port_key]))

        # The CA travels on one line with literal \n escapes and must
        # round-trip to a PEM certificate.
        self.assertIn('ca', vv)
        pem = vv['ca'].replace('\\n', '\n')
        self.assertTrue(
            pem.startswith('-----BEGIN CERTIFICATE-----'),
            'the ca value does not round-trip to a PEM certificate')
        self.assertIn('-----END CERTIFICATE-----', pem)

        # With a TLS port the node's certificate subject must be pinned:
        # every hypervisor is signed by the same cluster CA, so without a
        # host-subject a viewer would accept any node as this endpoint.
        if 'tls-port' in vv:
            self.assertIn('host-subject', vv)
            self.assertTrue(vv['host-subject'])

    def test_spiceconcurrent_vv_type_is_collapsed(self):
        """The internal VDI enum must never reach the viewer.

        'spiceconcurrent' is a documented, user-settable vdi value (see
        docs/user_guide/consoles.md) which selects server-side policy --
        multiple clients on one console -- that virt-viewer knows
        nothing about. Issue 4009 shipped it into the .vv verbatim,
        where it is "Unsupported graphic type". The endpoint collapses
        any spice* value to 'spice'; with video unset the server's own
        default is already 'spice', so only a request which asks for a
        variant reaches that code at all.
        """
        vv = self._fetch_vv(
            video={'model': 'cirrus', 'memory': 16384,
                   'vdi': 'spiceconcurrent'})
        self.assertEqual('spice', vv['type'])
