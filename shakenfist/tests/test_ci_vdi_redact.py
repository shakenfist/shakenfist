# Copyright 2026 Michael Still and contributors
"""redact_vv must remove every password line _parse_vv would read.

The .vv files the CI tests attach to their logs carry a live console
credential, and the logs are public, so the redaction has to match every
spelling configparser accepts for the key, not only the one today's
producers emit.
"""

import configparser
import os
import sys

from shakenfist.tests import base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')


def _load_vdi():
    """Import the functional suite's vdi.py from the tree.

    The suite is not a package the unit environment installs. sys.path
    and sys.modules are put back afterwards, because stestr runs the
    whole unit suite in one process.
    """
    names = ('shakenfist_ci', 'shakenfist_ci.base', 'shakenfist_ci.process',
             'shakenfist_ci.retries', 'shakenfist_ci.vdi')
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in names}
    try:
        sys.path.insert(0, os.path.dirname(CI_SUITE))
        for name in names:
            sys.modules.pop(name, None)
        import shakenfist_ci.vdi as vdi
        return vdi
    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


vdi = _load_vdi()


VV = (
    '[virt-viewer]\n'
    'type=spice\n'
    'host=10.0.0.5\n'
    'password=s3cret-one\n'
    'ca=-----BEGIN CERTIFICATE-----\n'
    'host-subject=C=AU,CN=sf-1\n'
)


class RedactVVTestCase(base.ShakenFistTestCase):
    def _password_of(self, text):
        cp = configparser.ConfigParser(delimiters=('=',), interpolation=None)
        cp.read_string(text)
        return cp['virt-viewer']['password']

    def test_plain_password_line(self):
        out = vdi.redact_vv(VV)
        self.assertNotIn('s3cret-one', out)
        self.assertEqual('<redacted>', self._password_of(out))
        # Nothing else is touched.
        self.assertIn('host=10.0.0.5\n', out)
        self.assertIn('host-subject=C=AU,CN=sf-1\n', out)

    def test_spaces_around_the_delimiter(self):
        text = VV.replace('password=s3cret-one', 'password = s3cret-two')
        self.assertEqual('s3cret-two', self._password_of(text))
        out = vdi.redact_vv(text)
        self.assertNotIn('s3cret-two', out)
        self.assertEqual('<redacted>', self._password_of(out))

    def test_crlf_line_endings(self):
        text = VV.replace('\n', '\r\n')
        out = vdi.redact_vv(text)
        self.assertNotIn('s3cret-one', out)
        self.assertEqual('<redacted>', self._password_of(out))

    def test_indented_line_is_a_continuation_not_a_key(self):
        # configparser reads an indented line as more of the value above,
        # so it is not the password key and need not be touched.
        text = VV.replace('password=s3cret-one\n', '')
        text = text.replace('ca=-----BEGIN CERTIFICATE-----\n',
                            'ca=-----BEGIN CERTIFICATE-----\n  password=notakey\n')
        self.assertRaises(KeyError, self._password_of, text)
        self.assertEqual(text, vdi.redact_vv(text))

    def test_no_password_line_is_unchanged(self):
        text = VV.replace('password=s3cret-one\n', '')
        self.assertEqual(text, vdi.redact_vv(text))
