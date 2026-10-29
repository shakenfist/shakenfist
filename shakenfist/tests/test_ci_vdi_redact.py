# Copyright 2026 Michael Still and contributors
"""redact_vv must remove every password line _parse_vv would read.

The .vv files the CI tests attach to their logs carry a live console
credential, and the logs are public, so the redaction has to match every
spelling configparser accepts for the key, not only the one today's
producers emit.

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

    def test_tab_before_the_delimiter(self):
        text = VV.replace('password=s3cret-one', 'password\t=s3cret-tab')
        self.assertEqual('s3cret-tab', self._password_of(text))
        out = vdi.redact_vv(text)
        self.assertNotIn('s3cret-tab', out)
        self.assertEqual('<redacted>', self._password_of(out))

    def test_mixed_case_key(self):
        # configparser lowercases keys, so these are the password too.
        for line in ('Password=s3cret-three', 'PASSWORD = s3cret-four'):
            text = VV.replace('password=s3cret-one', line)
            self.assertEqual(line.split('=')[1].strip(),
                             self._password_of(text))
            out = vdi.redact_vv(text)
            self.assertNotIn('s3cret-', out)
            self.assertEqual('<redacted>', self._password_of(out))

    def test_every_password_line_is_redacted(self):
        text = VV + '[other]\npassword=s3cret-five\n'
        cp = configparser.ConfigParser(delimiters=('=',), interpolation=None)
        cp.read_string(text)
        self.assertEqual('s3cret-five', cp['other']['password'])
        out = vdi.redact_vv(text)
        self.assertNotIn('s3cret-', out)
        cp = configparser.ConfigParser(delimiters=('=',), interpolation=None)
        cp.read_string(out)
        self.assertEqual('<redacted>', cp['virt-viewer']['password'])
        self.assertEqual('<redacted>', cp['other']['password'])

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
                            'ca=-----BEGIN CERTIFICATE-----\n'
                            '  password=notakey\n')
        self.assertRaises(KeyError, self._password_of, text)
        self.assertEqual(text, vdi.redact_vv(text))

    def test_newline_before_the_delimiter_is_not_a_key(self):
        # Whitespace before the '=' is horizontal only: a bare key on one
        # line and its '=' on the next is a parse error to configparser,
        # not a password, so the two lines are not folded into one.
        text = VV.replace('password=s3cret-one', 'password\n=s3cret-six')
        self.assertRaises(configparser.ParsingError, self._password_of, text)
        self.assertEqual(text, vdi.redact_vv(text))

    def test_no_password_line_is_unchanged(self):
        text = VV.replace('password=s3cret-one\n', '')
        self.assertEqual(text, vdi.redact_vv(text))
