#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors

"""Print a certificate's subject as Kerbside's PROXY_HOST_SUBJECT.

Usage: kerbside-host-subject.py CERTIFICATE.pem

Kerbside embeds PROXY_HOST_SUBJECT in the console files it hands to SPICE
clients, and the client compares it with the subject of the certificate the
proxy presents. It must therefore be rendered exactly as Shaken Fist renders a
hypervisor's subject for the same comparison, which is
shakenfist.node._spice_host_subject_from_cert(): the attributes in certificate
(DER) order, each as SHORT=value with OpenSSL's short name, backslash and then
comma escaped in values, joined by a comma with no space.

This is a copy of that function's rules rather than an import of it, because
it runs with Kerbside's virtualenv on a host which need not have Shaken Fist
installed. shakenfist/tests/test_kerbside_host_subject.py compares the two
over generated certificates, so they cannot drift apart unnoticed.

Where node.py returns None (an attribute with no short name, a value which is
not a string, or an empty subject) this prints nothing and exits non-zero with
the reason on stderr: a proxy subject which cannot be rendered would make every
client refuse to connect, so the deploy must stop instead.

Do not be tempted to replace this with openssl x509 -subject: with -nameopt
RFC2253 it prints the attributes in reverse order.
"""

import sys

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


class SubjectError(Exception):
    """The subject cannot be rendered as a host subject."""


def host_subject(cert):
    """Render cert's subject as a SPICE host subject, or raise SubjectError."""
    parts = []
    for attr in cert.subject:
        short = SHORT_NAMES.get(attr.oid)
        if short is None:
            raise SubjectError(
                'the subject carries attribute %s, which has no SPICE host '
                'subject short name' % attr.oid.dotted_string)
        value = attr.value
        if not isinstance(value, str):
            raise SubjectError(
                'the subject attribute %s (%s) is not a string'
                % (short, attr.oid.dotted_string))
        escaped = value.replace('\\', '\\\\').replace(',', '\\,')
        parts.append('%s=%s' % (short, escaped))

    if not parts:
        raise SubjectError('the subject is empty')
    return ','.join(parts)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write('usage: %s CERTIFICATE.pem\n' % argv[0])
        return 2

    path = argv[1]
    try:
        with open(path, 'rb') as f:
            cert = x509.load_pem_x509_certificate(f.read())
        subject = host_subject(cert)
    except (OSError, ValueError, SubjectError) as e:
        sys.stderr.write(
            'Cannot render the subject of %s as Kerbside\'s '
            'PROXY_HOST_SUBJECT: %s\n' % (path, e))
        return 1

    # Written as UTF-8 whatever the locale, since a subject may carry any
    # Unicode text and ansible reads command output as UTF-8.
    sys.stdout.buffer.write(subject.encode('utf-8') + b'\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
