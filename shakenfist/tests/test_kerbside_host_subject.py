# Copyright 2026 Michael Still and contributors

"""Tests for the kerbside role's host subject script and its defaults.

Kerbside embeds PROXY_HOST_SUBJECT in the console files it hands to SPICE
clients, and the client compares it with the subject of the certificate the
proxy presents. The kerbside role renders it with
``roles/kerbside/files/kerbside-host-subject.py``, a copy of the rules in
``shakenfist.node._spice_host_subject_from_cert()``, because it runs on a
Kerbside host which need not have Shaken Fist installed. A difference between
the two surfaces only as clients refusing to connect, never as a failed deploy,
so these tests render the same generated certificates both ways and require
the same answer, including where both must refuse.

The functional suite has a third renderer,
``shakenfist/deploy/shakenfist_ci/spice_subject.py``, which checks that the
host-subject Kerbside hands out matches the certificate its proxy presents. It
is held to node.py over the same certificates, so that a difference between
them fails here rather than as a functional test failing on a correct proxy.

The role's defaults are checked against its argument_specs here too, as
test_node_config_template.py does for the node role.
"""

import datetime
import importlib.util
import os
import tempfile
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
import yaml

from shakenfist import node as node_module
from shakenfist.tests import base


ROLE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles',
    'kerbside'))
SCRIPT_PATH = os.path.join(ROLE_PATH, 'files', 'kerbside-host-subject.py')
DEFAULTS_PATH = os.path.join(ROLE_PATH, 'defaults', 'main.yml')
SPICE_SUBJECT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'shakenfist_ci',
    'spice_subject.py'))
ARGUMENT_SPECS_PATH = os.path.join(ROLE_PATH, 'meta', 'argument_specs.yml')


def _load_by_path(name, path):
    # Neither file is importable from here: the script is a standalone file in
    # the role, and the functional suite (shakenfist_ci) is not a package of
    # this repository's test environment.
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_by_path('kerbside_host_subject', SCRIPT_PATH)
spice_subject = _load_by_path('ci_spice_subject', SPICE_SUBJECT_PATH)


def _der(cert):
    return cert.public_bytes(serialization.Encoding.DER)


def _make_cert(name_attrs):
    """A self-signed certificate whose subject is exactly name_attrs, in order."""
    key = ed25519.Ed25519PrivateKey.generate()
    subject = x509.Name(
        [x509.NameAttribute(oid, value) for oid, value in name_attrs])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2030, 1, 1))
        .sign(key, None))


# Subjects both implementations must render, and what they must render them as.
# The expected strings pin the rules themselves, so a change made to both
# implementations at once still fails here.
RENDERABLE = [
    # internal_ca's shape: O, then CN.
    ('internal_ca',
     [(NameOID.ORGANIZATION_NAME, 'Shaken Fist CA for sf'),
      (NameOID.COMMON_NAME, 'kerbside.example.com')],
     'O=Shaken Fist CA for sf,CN=kerbside.example.com'),
    ('comma',
     [(NameOID.ORGANIZATION_NAME, 'Acme, Inc'),
      (NameOID.COMMON_NAME, 'kerbside')],
     'O=Acme\\, Inc,CN=kerbside'),
    ('backslash',
     [(NameOID.ORGANIZATION_NAME, 'Acme\\Labs'),
      (NameOID.COMMON_NAME, 'kerbside')],
     'O=Acme\\\\Labs,CN=kerbside'),
    # A backslash before a comma. Escaping in the wrong order, comma first,
    # would then double the backslash the comma's escape introduced, giving
    # four backslashes and a bare comma rather than three and an escaped one.
    ('backslash_then_comma',
     [(NameOID.ORGANIZATION_NAME, 'a\\,b')],
     'O=a\\\\\\,b'),
    ('long',
     [(NameOID.COUNTRY_NAME, 'AU'),
      (NameOID.STATE_OR_PROVINCE_NAME, 'New South Wales'),
      (NameOID.LOCALITY_NAME, 'Sydney'),
      (NameOID.ORGANIZATION_NAME, 'Shaken Fist'),
      (NameOID.ORGANIZATIONAL_UNIT_NAME, 'Consoles'),
      (NameOID.DOMAIN_COMPONENT, 'example'),
      (NameOID.COMMON_NAME, 'kerbside.example.com'),
      (NameOID.EMAIL_ADDRESS, 'ops@example.com')],
     'C=AU,ST=New South Wales,L=Sydney,O=Shaken Fist,OU=Consoles,DC=example,'
     'CN=kerbside.example.com,emailAddress=ops@example.com'),
    # Certificate order, not the reverse RFC 2253 order openssl prints.
    ('certificate_order',
     [(NameOID.COMMON_NAME, 'kerbside'),
      (NameOID.ORGANIZATION_NAME, 'Shaken Fist')],
     'CN=kerbside,O=Shaken Fist'),
]

# Subjects both implementations must refuse.
UNRENDERABLE = [
    ('unknown_oid',
     [(NameOID.COMMON_NAME, 'kerbside'),
      (NameOID.SERIAL_NUMBER, '12345')]),
    ('empty', []),
]


class KerbsideHostSubjectTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # node.py's warn-once set is module state which outlives a test, and
        # its warnings are not what is under test here.
        node_module._SPICE_SUBJECT_WARNED.clear()
        self.addCleanup(node_module._SPICE_SUBJECT_WARNED.clear)
        patcher = mock.patch('shakenfist.node.LOG')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_short_names_match_node(self):
        self.assertEqual(
            node_module._SPICE_SUBJECT_SHORT_NAMES, script.SHORT_NAMES)
        self.assertEqual(
            node_module._SPICE_SUBJECT_SHORT_NAMES, spice_subject.SHORT_NAMES)

    def test_renders_as_node_does(self):
        for name, attrs, expected in RENDERABLE:
            cert = _make_cert(attrs)
            self.assertEqual(
                expected, node_module._spice_host_subject_from_cert(cert),
                f'{name}: node.py rendered an unexpected subject')
            self.assertEqual(
                expected, script.host_subject(cert),
                f'{name}: the script rendered differently from node.py')
            self.assertEqual(
                expected, spice_subject.host_subject(_der(cert)),
                f'{name}: shakenfist_ci/spice_subject.py rendered differently '
                f'from node.py')

    def test_refuses_where_node_does(self):
        for name, attrs in UNRENDERABLE:
            cert = _make_cert(attrs)
            self.assertIsNone(
                node_module._spice_host_subject_from_cert(cert),
                f'{name}: node.py rendered a subject it should refuse')
            self.assertRaises(
                script.SubjectError, script.host_subject, cert)
            self.assertIsNone(
                spice_subject.host_subject(_der(cert)),
                f'{name}: shakenfist_ci/spice_subject.py rendered a subject '
                f'it should refuse')

    def _run_main(self, attrs):
        cert = _make_cert(attrs)
        with tempfile.NamedTemporaryFile(suffix='.pem', delete=False) as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
            path = f.name
        self.addCleanup(os.unlink, path)

        stdout = mock.MagicMock()
        with mock.patch('sys.stdout', stdout), \
                mock.patch('sys.stderr') as stderr:
            rc = script.main(['kerbside-host-subject.py', path])
        written = b''.join(
            call.args[0] for call in stdout.buffer.write.call_args_list)
        errors = ''.join(
            call.args[0] for call in stderr.write.call_args_list)
        return rc, written, errors

    def test_main_prints_the_subject(self):
        _, attrs, expected = RENDERABLE[0]
        rc, written, errors = self._run_main(attrs)
        self.assertEqual(0, rc)
        self.assertEqual(expected.encode('utf-8') + b'\n', written)
        self.assertEqual('', errors)

    def test_main_fails_and_says_why(self):
        rc, written, errors = self._run_main(UNRENDERABLE[0][1])
        self.assertEqual(1, rc)
        self.assertEqual(b'', written)
        self.assertIn('2.5.4.5', errors)

    def test_main_fails_on_a_missing_file(self):
        with mock.patch('sys.stderr') as stderr:
            rc = script.main(['kerbside-host-subject.py', '/nonexistent.pem'])
        self.assertEqual(1, rc)
        stderr.write.assert_called_once()


class KerbsideRoleDefaultsTestCase(base.ShakenFistTestCase):
    def test_kerbside_defaults_are_documented(self):
        with open(DEFAULTS_PATH) as f:
            defaults = yaml.safe_load(f)
        with open(ARGUMENT_SPECS_PATH) as f:
            specs = yaml.safe_load(f)
        options = specs['argument_specs']['main']['options']
        for name in defaults:
            self.assertIn(
                name, options,
                f'role default {name} is not documented in argument_specs.yml')
