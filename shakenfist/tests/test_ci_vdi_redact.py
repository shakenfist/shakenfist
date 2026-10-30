# Copyright 2026 Michael Still and contributors
"""redact_vv must leave no password _parse_vv would read, or attach nothing.

The .vv files the CI tests attach to their logs carry a live console
credential, and the logs are public, so the redaction has to cover every
spelling configparser accepts for the key, not only the one today's
producers emit, and withhold the file when it cannot. Each test asserts
what configparser reads from the input and then from the output, so the
tests are tied to the contract with _parse_vv rather than to the regex.

vdi.py imports ``shakenfist_ci.base``, which imports ``shakenfist_client``
and ``prettytable``, neither of which is installed in the unit test
environment, so it is imported here behind the same stubs
``test_ci_agentops_shared.py`` uses.
"""

import configparser
import logging
import os
import sys
import types

from shakenfist.tests import base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')


def _client_stubs():
    """A shakenfist_client which is only its exception taxonomy.

    ``base.py`` builds ``AGENT_OPERATION_FAILURES`` at import time from
    these exception classes, so the stub defines the taxonomy and nothing
    else.
    """
    client = types.ModuleType('shakenfist_client')
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    class APIException(Exception):
        def __init__(self, message='', method=None, url=None,
                     status_code=None, text=None):
            super().__init__(message)
            self.status_code = status_code
            self.text = text

    apiclient.APIException = APIException
    for name in ('InsufficientResourcesException',
                 'ResourceStateConflictException',
                 'ResourceNotFoundException',
                 'RequestMalformedException',
                 'ServiceUnavailableException',
                 'IncapableException',
                 'AgentCommandError',
                 'AgentOperationFailed',
                 'AgentAwaitTimeout'):
        setattr(apiclient, name, type(name, (APIException,), {}))

    apiclient.ASYNC_PAUSE = 'pause'
    apiclient.ASYNC_CONTINUE = 'continue'
    apiclient.Client = type(
        'Client', (), {'__init__': lambda self, *a, **kw: None})

    client.apiclient = apiclient
    return client, apiclient


def _load_vdi():
    """Import the functional suite's vdi.py from the tree, behind stubs.

    The suite is not a package the unit environment installs. sys.path,
    sys.modules and the root logger are put back afterwards, because
    stestr runs the whole unit suite in one process and a stubbed
    shakenfist_client left in sys.modules would be a trap for a later
    test.
    """
    names = ('prettytable', 'shakenfist_client',
             'shakenfist_client.apiclient', 'shakenfist_ci',
             'shakenfist_ci.base', 'shakenfist_ci.process',
             'shakenfist_ci.retries', 'shakenfist_ci.vdi')
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in names}
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level
    try:
        sys.path.insert(0, os.path.dirname(CI_SUITE))
        for name in names:
            sys.modules.pop(name, None)

        prettytable = types.ModuleType('prettytable')
        prettytable.PrettyTable = type('PrettyTable', (), {})
        sys.modules['prettytable'] = prettytable

        client, _ = _client_stubs()
        sys.modules['shakenfist_client'] = client
        sys.modules['shakenfist_client.apiclient'] = client.apiclient

        import shakenfist_ci.vdi as vdi
        return vdi
    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


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
    def _parse(self, text):
        cp = configparser.ConfigParser(delimiters=('=',), interpolation=None)
        cp.read_string(text)
        return cp

    def _password_of(self, text, section='virt-viewer'):
        return self._parse(text)[section]['password']

    def _assert_redacted(self, text, secret, expected=None):
        """Redact text and check configparser reads only the marker.

        The secret must be what configparser read beforehand and must be
        absent afterwards. With expected, the output is checked exactly,
        which pins everything the redaction must not touch.
        """
        self.assertEqual(secret, self._password_of(text))
        out = vdi.redact_vv(text)
        self.assertNotIn(secret, out)
        self.assertEqual('<redacted>', self._password_of(out))
        if expected is not None:
            self.assertEqual(expected, out)
        return out

    def _assert_withheld(self, text, secret):
        out = vdi.redact_vv(text)
        self.assertEqual(vdi.VV_WITHHELD, out)
        self.assertNotIn(secret, out)

    def test_plain_password_line(self):
        self._assert_redacted(
            VV, 's3cret-one', VV.replace('s3cret-one', '<redacted>'))

    def test_spaces_around_the_delimiter(self):
        text = VV.replace('password=s3cret-one', 'password = s3cret-two')
        self._assert_redacted(
            text, 's3cret-two', VV.replace('s3cret-one', '<redacted>'))

    def test_tab_before_the_delimiter(self):
        text = VV.replace('password=s3cret-one', 'password\t=s3cret-tab')
        self._assert_redacted(
            text, 's3cret-tab', VV.replace('s3cret-one', '<redacted>'))

    def test_mixed_case_key(self):
        # configparser lowercases keys, so these are the password too.
        for line in ('Password=s3cret-three', 'PASSWORD = s3cret-four'):
            text = VV.replace('password=s3cret-one', line)
            self._assert_redacted(
                text, line.split('=')[1].strip(),
                VV.replace('s3cret-one', '<redacted>'))

    def test_indented_key_after_the_header(self):
        # configparser only reads an indented line as a continuation when
        # an option is open above it; straight after the header there is
        # none, so this is the password key.
        text = VV.replace('[virt-viewer]\ntype=spice\nhost=10.0.0.5\n'
                          'password=s3cret-one\n',
                          '[virt-viewer]\n  password=s3cret-five\n'
                          'type=spice\nhost=10.0.0.5\n')
        self._assert_redacted(
            text, 's3cret-five', text.replace('s3cret-five', '<redacted>'))

    def test_indented_keys_throughout(self):
        # Keys which all share one indent are keys, not continuations: a
        # continuation has to be indented more than the option above it.
        text = ''.join(
            line if line.startswith('[') else '  ' + line
            for line in VV.splitlines(keepends=True))
        self.assertEqual('10.0.0.5', self._parse(text)['virt-viewer']['host'])
        self._assert_redacted(
            text, 's3cret-one', text.replace('s3cret-one', '<redacted>'))

    def test_every_password_line_is_redacted(self):
        text = VV + '[other]\npassword=s3cret-six\n'
        self.assertEqual('s3cret-six', self._password_of(text, 'other'))
        out = self._assert_redacted(
            text, 's3cret-one',
            VV.replace('s3cret-one', '<redacted>')
            + '[other]\npassword=<redacted>\n')
        self.assertEqual('<redacted>', self._password_of(out, 'other'))

    def test_password_in_the_default_section(self):
        # A [DEFAULT] password is what every section reads when it has
        # none of its own.
        text = '[DEFAULT]\npassword=s3cret-seven\n' + VV.replace(
            'password=s3cret-one\n', '')
        self._assert_redacted(
            text, 's3cret-seven', text.replace('s3cret-seven', '<redacted>'))

    def test_crlf_line_endings_are_kept(self):
        text = VV.replace('\n', '\r\n')
        self._assert_redacted(
            text, 's3cret-one', text.replace('s3cret-one', '<redacted>'))

    def test_continuation_line_which_looks_like_a_key(self):
        # An indented line below an open option continues its value, so
        # this is not the password key. It is redacted anyway, which is
        # harmless, and nothing else changes.
        text = VV.replace('password=s3cret-one\n', '')
        text = text.replace('ca=-----BEGIN CERTIFICATE-----\n',
                            'ca=-----BEGIN CERTIFICATE-----\n'
                            '  password=notakey\n')
        self.assertRaises(KeyError, self._password_of, text)
        out = vdi.redact_vv(text)
        self.assertEqual(text.replace('notakey', '<redacted>'), out)
        self.assertRaises(KeyError, self._password_of, out)

    def test_password_value_continued_on_the_next_line_is_withheld(self):
        # configparser joins an indented line onto the value above, so the
        # second line is part of the credential and no line match reaches
        # it. Reading the result back is what catches this.
        text = VV.replace('password=s3cret-one\n',
                          'password=s3cret-eight\n  s3cret-nine\n')
        self.assertEqual('s3cret-eight\ns3cret-nine', self._password_of(text))
        self._assert_withheld(text, 's3cret-nine')

    def test_continued_password_in_the_default_section_is_withheld(self):
        # With no section of its own to read it through, the [DEFAULT]
        # password has to be checked directly.
        text = '[DEFAULT]\npassword=s3cret-ten\n  s3cret-eleven\n'
        self.assertEqual('s3cret-ten\ns3cret-eleven',
                         self._password_of(text, 'DEFAULT'))
        self._assert_withheld(text, 's3cret-eleven')

    def test_unparseable_input_is_withheld(self):
        # A bare key on one line and its '=' on the next is a parse error
        # to configparser, so nothing can prove the credential is gone and
        # the file is not attached at all.
        text = VV.replace('password=s3cret-one', 'password\n=s3cret-twelve')
        self.assertRaises(configparser.ParsingError, self._password_of, text)
        self._assert_withheld(text, 's3cret-twelve')

    def test_no_password_line_is_unchanged(self):
        text = VV.replace('password=s3cret-one\n', '')
        self.assertEqual(text, vdi.redact_vv(text))
