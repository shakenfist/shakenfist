# Copyright 2026 Michael Still and contributors

"""Tests that the functional suite renders SPICE host subjects as node.py does.

A SPICE client compares the host-subject it is given with the subject of the
certificate the server presents. ``shakenfist.node._spice_host_subject_from_cert()``
renders a hypervisor's. The functional suite has its own renderer,
``shakenfist/deploy/shakenfist_ci/spice_subject.py``, which checks that the
host-subject Kerbside hands out matches the certificate its proxy presents,
because the suite does not depend on the server package. These tests render
the same generated certificates both ways and require the same answer,
including where both must refuse, so that a difference between them fails here
rather than as a functional test failing on a correct proxy.

The kerbside role of the shakenfist.kerbside collection has a third copy of
the rules, which Kerbside tests against the same renderings.
"""

import datetime
import importlib.util
import os
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from shakenfist import node as node_module
from shakenfist.tests import base


SPICE_SUBJECT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'shakenfist_ci',
    'spice_subject.py'))


def _load_by_path(name, path):
    # The functional suite (shakenfist_ci) is not a package of this
    # repository's test environment, so it is not importable from here.
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
# implementations at once still fails here. Kerbside holds the role's copy of
# the rules to these same renderings.
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


class SpiceSubjectTestCase(base.ShakenFistTestCase):
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
            node_module._SPICE_SUBJECT_SHORT_NAMES, spice_subject.SHORT_NAMES)

    def test_renders_as_node_does(self):
        for name, attrs, expected in RENDERABLE:
            cert = _make_cert(attrs)
            self.assertEqual(
                expected, node_module._spice_host_subject_from_cert(cert),
                f'{name}: node.py rendered an unexpected subject')
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
            self.assertIsNone(
                spice_subject.host_subject(_der(cert)),
                f'{name}: shakenfist_ci/spice_subject.py rendered a subject '
                f'it should refuse')
