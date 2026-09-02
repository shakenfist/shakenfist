# Copyright 2026 Michael Still and contributors
#
# Tests for the /instances/<ref>/vdiconsolehelper endpoint
# (InstanceVDIConsoleHelperEndpoint), which emits a virt-viewer .vv
# file. Issue 4009: the file used to carry the placement node UUID as
# the host, the internal VDI enum as the type, and no host-subject;
# nothing parsed the file in any test, which is how all three survived.

import builtins
import configparser
import io
import json
import logging
import os
import sys
from unittest import mock
from uuid import uuid4

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.node import Node
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB

CA_CERT_PATH = '/etc/pki/libvirt-spice/ca-cert.pem'
FAKE_PEM = ('-----BEGIN CERTIFICATE-----\n'
            'MIIEFfakefakefakefake\n'
            '-----END CERTIFICATE-----\n')


class InstanceVDIConsoleHelperEndpointTestCase(base.ShakenFistTestCase):
    """End to end tests for GET /instances/<ref>/vdiconsolehelper.

    Uses a real Flask test client and MockMariaDB, matching the harness
    in test_instance_vdiconsoleproxy.py. The instance is placed on the
    node this "API server" claims to be (config.NODE_UUID), so
    redirect_instance_request() runs the handler locally instead of
    proxying it, the same reason test_agent_operation_parameters.py
    does this.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=2)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = self.mock_mariadb.node_uuids['node1_net']
        self.addCleanup(self._restore_node_uuid)

        self.client = external_api.app.test_client()

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'foo', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.owner_token = 'Bearer %s' % resp.get_json()['access_token']

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def _make_instance(self, vdi_type, ports):
        instance_uuid = str(uuid4())
        inst = self.mock_mariadb.create_instance(
            'vdi-instance', uuid=instance_uuid, namespace='foo',
            video={'model': 'cirrus', 'memory': 16384, 'vdi': vdi_type},
            set_state=dbo.STATE_CREATED, place_on_node=config.NODE_UUID)
        inst.ports = ports
        return instance_uuid

    def _set_node_cert_subject(self, subject):
        n = Node.from_db(config.NODE_UUID)
        attrs = n._ensure_attributes()
        attrs.spice_server_cert_subject = subject
        n._save_attributes(fields=['spice_server_cert_subject'])

    def _get_vv(self, instance_uuid):
        resp = self.client.get(
            '/instances/%s/vdiconsolehelper' % instance_uuid,
            headers={'Authorization': self.owner_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/x-virt-viewer', resp.mimetype)

        # The file must parse as an INI with a [virt-viewer] section --
        # that is the contract remote-viewer and ryll both rely on.
        cp = configparser.ConfigParser(delimiters=('=',), interpolation=None)
        cp.read_string(resp.get_data(as_text=True))
        self.assertIn('virt-viewer', cp.sections())
        return cp['virt-viewer']

    def test_spiceconcurrent_emits_valid_vv_file(self):
        self._set_node_cert_subject('CN=node1_net')
        instance_uuid = self._make_instance(
            'spiceconcurrent', {'vdi_port': 31002, 'vdi_tls_port': 31003})

        vv = self._get_vv(instance_uuid)

        # The internal spiceconcurrent enum must not leak: virt-viewer
        # accepts only 'spice' and 'vnc' as the type.
        self.assertEqual('spice', vv['type'])

        # The host must be the node's connectable IP, not its UUID.
        self.assertEqual('10.0.0.1', vv['host'])
        self.assertNotEqual(config.NODE_UUID, vv['host'])

        self.assertEqual('31002', vv['port'])
        self.assertEqual('31003', vv['tls-port'])

        # With a TLS port, the node certificate subject is pinned so a
        # viewer cannot be redirected to another node signed by the
        # same cluster CA.
        self.assertEqual('CN=node1_net', vv['host-subject'])
        self.assertEqual('1', vv['delete-this-file'])

    def test_plain_spice_and_vnc_pass_through(self):
        for vdi_type in ('spice', 'vnc'):
            instance_uuid = self._make_instance(
                vdi_type, {'vdi_port': 31002})
            vv = self._get_vv(instance_uuid)
            self.assertEqual(vdi_type, vv['type'])

    def test_no_tls_port_omits_tls_and_subject(self):
        self._set_node_cert_subject('CN=node1_net')
        instance_uuid = self._make_instance('spice', {'vdi_port': 31002})

        vv = self._get_vv(instance_uuid)

        self.assertNotIn('tls-port', vv)
        self.assertNotIn('host-subject', vv)

    def test_unknown_cert_subject_omits_host_subject(self):
        instance_uuid = self._make_instance(
            'spice', {'vdi_port': 31002, 'vdi_tls_port': 31003})

        vv = self._get_vv(instance_uuid)

        self.assertEqual('31003', vv['tls-port'])
        self.assertNotIn('host-subject', vv)

    def test_ca_cert_escaping_round_trips(self):
        instance_uuid = self._make_instance(
            'spice', {'vdi_port': 31002, 'vdi_tls_port': 31003})

        real_exists = os.path.exists
        real_open = builtins.open

        def fake_exists(path):
            if path == CA_CERT_PATH:
                return True
            return real_exists(path)

        def fake_open(path, *args, **kwargs):
            if path == CA_CERT_PATH:
                return io.StringIO(FAKE_PEM)
            return real_open(path, *args, **kwargs)

        with mock.patch('os.path.exists', side_effect=fake_exists), \
                mock.patch('builtins.open', side_effect=fake_open):
            vv = self._get_vv(instance_uuid)

        # The PEM travels on one line with literal \n escapes, and must
        # round-trip back to the certificate it came from.
        self.assertNotIn('\n', vv['ca'])
        self.assertEqual(FAKE_PEM, vv['ca'].replace('\\n', '\n'))
