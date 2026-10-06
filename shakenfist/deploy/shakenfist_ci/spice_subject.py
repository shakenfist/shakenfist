# Copyright 2026 Michael Still and contributors
"""Render a certificate's subject as a SPICE host-subject string.

A SPICE client given a host-subject compares it with the subject of the
certificate the server presents. Shaken Fist renders a hypervisor's subject
for that comparison in shakenfist.node._spice_host_subject_from_cert(), and
the kerbside role renders the proxy's with a copy of the same rules
(roles/kerbside/files/kerbside-host-subject.py). The functional tests need a
third renderer, to check that the host-subject Kerbside hands out matches the
certificate its proxy actually presents: the attributes in certificate (DER)
order, each as SHORT=value with OpenSSL's short name, backslash and then comma
escaped in values, joined by a comma with no space.

This is a copy of those rules rather than an import of node.py, because the
functional suite does not depend on the server package. It imports nothing
from the rest of the suite either, so that
shakenfist/tests/test_kerbside_host_subject.py can load it by path and hold
it to node.py over the same certificates the role's script is held to.
"""

from cryptography import x509
from cryptography.x509.oid import NameOID


# Must equal shakenfist.node._SPICE_SUBJECT_SHORT_NAMES; the unit test checks.
SHORT_NAMES = {
    NameOID.COUNTRY_NAME: 'C',
    NameOID.STATE_OR_PROVINCE_NAME: 'ST',
    NameOID.LOCALITY_NAME: 'L',
    NameOID.ORGANIZATION_NAME: 'O',
    NameOID.ORGANIZATIONAL_UNIT_NAME: 'OU',
    NameOID.COMMON_NAME: 'CN',
    NameOID.DOMAIN_COMPONENT: 'DC',
    NameOID.EMAIL_ADDRESS: 'emailAddress',
}


def host_subject(der):
    """Render a DER certificate's subject as a SPICE host subject.

    Returns None wherever node.py does: for a subject which is empty, carries
    an attribute with no short name, or carries a value which is not a
    string. None of those has an exact rendering to compare against.
    """
    cert = x509.load_der_x509_certificate(der)
    parts = []
    for attr in cert.subject:
        short = SHORT_NAMES.get(attr.oid)
        if short is None:
            return None
        value = attr.value
        if not isinstance(value, str):
            return None
        escaped = value.replace('\\', '\\\\').replace(',', '\\,')
        parts.append('%s=%s' % (short, escaped))

    if not parts:
        return None
    return ','.join(parts)
