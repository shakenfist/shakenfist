# Copyright 2019 Michael Still and contributors

"""The five semantic format validators, and the issue they close.

Step 4 of PLAN-api-input-validation-phase-06-required.md. Finding F3 of
that plan says that of the nine semantic type tokens the API publishes,
exactly one -- `macaddr` -- validated anything, and `macaddr` is
declared nowhere. The other eight rendered a `format` string into the
OpenAPI document and compiled to a bare `fields.String`, so the
published specification promised a base64 body, a CIDR netblock or a
UUID and the server accepted any string at all.

`validation._FORMATS` gives five of those format strings a validator
apiece, keyed on the exact string `ARGTYPES` renders (decision D33).
This module holds each of them to the three properties the step
requires: it accepts something, it refuses something, and it returns a
None untouched.

The risk the step carries is the opposite of the obvious one. A
validator which is *stricter* than its handler is a breaking change
dressed up as a correctness fix, and no unit test of the validator
alone would show it -- so the acceptance cases here are chosen to be
the awkward real values each handler takes today (a `cirros` shortcut
with no scheme at all, a `label:` NVRAM template, base64 wrapped at 76
columns by the `base64` command), not tidy examples.

InstanceUserDataBase64TestCase at the bottom is definition-of-done item
6: it drives a real authenticated POST /instances and pins that the
`base64.b64decode()` in Instance._make_config_drive_openstack_disk() --
the traceback issue 3269 was filed about, which happens on the
hypervisor long after the API has answered 200 -- is never reached with
a body the API can tell is not base64.
"""

import base64
import binascii
import json
from unittest import mock

import marshmallow

from shakenfist.external_api import base as api_base
from shakenfist.external_api import instance as instance_api
from shakenfist.external_api import validation
from shakenfist.tests import base
from shakenfist.tests.external_api.test_instance_create_validation import (
    VALID_REMAINDER)
from shakenfist.tests.external_api.test_request_validation import (
    AuthenticatedStackTestCase)


class FormatValidatorTestCase(base.ShakenFistTestCase):
    """Each validator on its own, with no request around it."""

    def assertAccepts(self, validator, value, why):
        try:
            self.assertEqual(value, validator(value), why)
        except marshmallow.ValidationError as e:
            self.fail('%s: %s refused %r (%s)'
                      % (why, validator.__name__, value, e))

    def assertRefuses(self, validator, value):
        self.assertRaises(marshmallow.ValidationError, validator, value)

    def test_the_table_is_keyed_on_what_argtypes_renders(self):
        """D33, asserted rather than assumed.

        The entire design of this table is that the string the OpenAPI
        document publishes and the string the server dispatches its
        check on are the same string in the same process. A typo in a
        key would leave the published format and the compiled check
        silently disagreeing, which is the exact defect the "compile
        from the rendered specification" design of validation.py
        exists to prevent -- so the keys are checked against the
        vocabulary they came from.
        """
        rendered = {spec.get('format') for spec in
                    api_base.ARGTYPES.values()}
        self.assertEqual(set(), set(validation._FORMATS) - rendered)
        self.assertEqual(
            {'byte', 'a CIDR netblock', 'an IPv4 address as a string',
             'url', 'uuid'},
            set(validation._FORMATS))

    def test_every_validator_passes_none_through_untouched(self):
        """The rule the shipped client depends on.

        Every compiled field is allow_none=True, and marshmallow does
        not run a validator on a None at all -- but the shipped client
        sends `"source_url": null` on every artifact upload and
        `"nvram_template": null` on every instance create, so a
        validator which refused a None would refuse the API's dominant
        caller the moment anything ever handed it one. Required-ness
        is decided before any of this runs and is not this table's
        business.
        """
        for name, validator in sorted(validation._FORMATS.items()):
            self.assertIsNone(
                validator(None),
                '%s did not pass None through untouched' % name)

    def test_byte_takes_what_the_config_drive_decodes(self):
        """base64, including the wrapped kind.

        The second value is the first with a newline in the middle,
        which is what `base64 user-data.yaml` emits (it wraps at 76
        columns) and therefore what `-U "$(base64 user-data.yaml)"`
        sends. b64decode's default accepts it and the config drive
        decodes it today, so this validator must too -- which is why
        it does not pass validate=True.
        """
        encoded = str(base64.b64encode(b'#cloud-config\n'), 'utf-8')
        self.assertAccepts(validation._format_byte, encoded,
                           'plain base64 must be accepted')
        self.assertAccepts(
            validation._format_byte, 'I2Nsb3VkLWNv\nbmZpZwo=',
            'base64 wrapped by the base64(1) command must be accepted')
        self.assertAccepts(validation._format_byte, '',
                           'an empty string decodes to empty bytes today')

    def test_byte_refuses_what_the_config_drive_cannot_decode(self):
        """Issue 3269's input, at the validator.

        Raw cloud-config pasted in place of the base64 of it. The
        assertion that b64decode itself raises on the same value is
        the load bearing half: it is what makes this the value the
        hypervisor would have died on rather than merely one this
        validator happens to dislike.
        """
        raw = '#cloud-config\nruncmd:\n  - echo hello\n'
        self.assertRaises(binascii.Error, base64.b64decode, raw)
        self.assertRefuses(validation._format_byte, raw)

        # And the two other shapes b64decode refuses: a length which
        # cannot be base64, and one character of padding too few.
        self.assertRefuses(validation._format_byte, 'a')
        self.assertRefuses(validation._format_byte, 'aGVsbG8')

    def test_netblock_takes_what_ip_network_takes(self):
        """The same function the handler already calls.

        NetworksEndpoint.post() parses with ipaddress.ip_network() and
        answers 400 on a ValueError, so the widths cannot differ. IPv6
        is included deliberately: it is why the token has no pattern.
        """
        for value in ('10.0.0.0/24', '192.168.1.0/29', '10.0.0.1/32',
                      'fd00::/64',
                      # A bare address is a /32 to ip_network(), so the
                      # handler takes it and then refuses it for being
                      # below the minimum size of /29 -- a policy about
                      # how small a network may be, which stays in the
                      # handler. Refusing it here would answer a
                      # different 400 than the one the API publishes.
                      '10.0.0.0'):
            self.assertAccepts(validation._format_netblock, value,
                               'ip_network() parses this today')

    def test_netblock_refuses_what_ip_network_refuses(self):
        for value in ('banana', '10.0.0.1/24', '999.999.999.999/99', ''):
            self.assertRefuses(validation._format_netblock, value)

    def test_ipv4_takes_an_address(self):
        """Including an IPv6 one, on purpose.

        The published format says IPv4, but the value's only consumer
        is dnsmasq's hosts file, which takes either. Refusing a v6
        address would be this layer inventing a restriction the server
        does not have.
        """
        self.assertAccepts(validation._format_ip_address, '11.22.33.44',
                           'the functional suite sends exactly this')
        self.assertAccepts(validation._format_ip_address, 'fd00::1',
                           'a hosts file takes an IPv6 address')

    def test_ipv4_refuses_what_is_not_an_address(self):
        for value in ('banana', '11.22.33', '11.22.33.44/24',
                      '300.1.1.1', ''):
            self.assertRefuses(validation._format_ip_address, value)

    def test_url_takes_every_shape_the_five_handlers_take(self):
        """The acceptance list which makes this validator honest.

        Not one of these has an http scheme, and three of them have no
        scheme at all. A validator which demanded one would refuse
        `artifact cache cirros`, which is a line in the functional
        suite.
        """
        for value in ('cirros', 'cirros:0.4.0', 'ubuntu:20.04',
                      'label:mylabel', 'sf://blob/a-uuid',
                      'sf://upload/system/thing',
                      'https://example.com/.well-known/jwks.json',
                      ''):
            self.assertAccepts(validation._format_url, value,
                               'a handler accepts this today')

    def test_url_refuses_what_urlparse_cannot_parse(self):
        """The only thing it refuses, which is the point.

        An unbalanced bracket in the authority is malformed under RFC
        3986 and is what urllib.parse itself raises ValueError on. The
        validator is deliberately no narrower than that; see its
        docstring for why a scheme cannot be required.
        """
        self.assertRefuses(validation._format_url, 'http://[::1')
        self.assertRefuses(validation._format_url, 'http://]bad[/')

    def test_uuid_takes_every_spelling_uuid_takes(self):
        """Wider than anything the API hands out.

        Every uuid the server can answer with is `str(uuid.uuid4())`,
        the dashed form. The others are accepted because uuid.UUID()
        accepts them and narrowing to the dashed form would be a
        restriction the server has never had.
        """
        for value in ('6a9b0f4e-0b2f-4a5e-bd0e-000000000000',
                      '6a9b0f4e0b2f4a5ebd0e000000000000',
                      '{6a9b0f4e-0b2f-4a5e-bd0e-000000000000}',
                      'urn:uuid:6a9b0f4e-0b2f-4a5e-bd0e-000000000000'):
            self.assertAccepts(validation._format_uuid, value,
                               'uuid.UUID() parses this')

    def test_uuid_refuses_what_is_not_a_uuid(self):
        for value in ('banana', '', '6a9b0f4e-0b2f-4a5e-bd0e',
                      'sf://blob/6a9b0f4e-0b2f-4a5e-bd0e-000000000000'):
            self.assertRefuses(validation._format_uuid, value)

    def test_no_validator_lets_a_library_exception_escape(self):
        """A validator which raised anything else would be worse than
        none at all.

        _schema_findings() catches a validator blowing up and reports
        *no* findings for the whole schema, so one exception from one
        format would silently switch off every other check on the same
        request. The values here are the ones which reach a library
        function's own TypeError rather than its ValueError.
        """
        for name, validator in sorted(validation._FORMATS.items()):
            for value in (5, 5.0, True, [], {}, b'bytes', object()):
                try:
                    validator(value)
                except marshmallow.ValidationError:
                    continue
                except Exception as e:
                    self.fail('%s(%r) raised %s'
                              % (name, value, type(e).__name__))
                self.fail('%s(%r) was accepted' % (name, value))


class InstanceUserDataBase64TestCase(AuthenticatedStackTestCase):
    """Definition-of-done item 6: issue 3269, closed end to end.

    A real authenticated POST /instances through the whole decorator
    stack, for the reason AuthenticatedStackTestCase documents.
    """

    mode = 'enforce'

    #: Raw cloud-config, which is what a caller sends when they forget
    #: to encode it. This is the input of issue 3269.
    UNENCODED = '#cloud-config\nruncmd:\n  - echo hello\n'

    def _post(self, body):
        """POST /instances, with Instance.new spied on.

        SCHEDULER is reset for the reason
        test_instance_create_validation._post documents: the handler
        caches one in a module global and a request which reaches
        placement would leave a real one behind for every later test
        in the same worker.
        """
        with mock.patch.object(instance_api, 'SCHEDULER', None), \
                mock.patch.object(
                    instance_api.instance.Instance, 'new',
                    side_effect=AssertionError(
                        'the request reached instance creation')) as new:
            response = self.client.post(
                '/instances', data=json.dumps(body),
                content_type='application/json',
                headers={'Authorization': self.token})
        return response, new

    def test_unencoded_user_data_is_refused_before_the_hypervisor(self):
        """The whole of issue 3269 in one test.

        Three assertions, and all three are needed:

        * `base64.b64decode()` raises on this value. That is the
          expression in Instance._make_config_drive_openstack_disk(),
          and it is what makes this the input the issue was filed
          about rather than one this test invented.
        * the API answers 400 naming user_data, so the caller is told
          which field is wrong while they can still fix it.
        * Instance.new() was never called. No instance object exists,
          so nothing was scheduled, nothing was placed, and no
          hypervisor will ever be asked to build a config drive from
          this value. That is the "never reached" half, and asserting
          it on the creation call rather than on the decode is what
          makes it true of the deployed system rather than of this
          process -- the decode happens in a different daemon on a
          different machine, and no unit test can watch it.
        """
        self.assertRaises(binascii.Error, base64.b64decode, self.UNENCODED)

        body = dict(VALID_REMAINDER)
        body['name'] = 'userdatatest'
        body['user_data'] = self.UNENCODED
        response, new = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'user_data: Not a valid base64 string.',
             'status': 400},
            response.get_json())
        new.assert_not_called()

    def test_wrapped_base64_user_data_still_reaches_placement(self):
        """And the half which proves the fix did not narrow the API.

        `base64 user-data.yaml` wraps its output at 76 columns, so a
        caller doing `-U "$(base64 user-data.yaml)"` sends a value
        with newlines in it. The config drive decodes that today. A
        507 from the scheduler is proof the request got past
        validation and all the way to placement -- a single node
        cluster with no hypervisor cannot place an instance, which is
        the same landing point test_a_valid_name_gets_past_the_guard
        uses.
        """
        wrapped = '\n'.join(
            [str(base64.b64encode(b'#cloud-config\n' * 8), 'utf-8')[:76],
             str(base64.b64encode(b'#cloud-config\n' * 8), 'utf-8')[76:]])
        self.assertIn('\n', wrapped)
        self.assertEqual(b'#cloud-config\n' * 8, base64.b64decode(wrapped))

        body = dict(VALID_REMAINDER)
        body['name'] = 'userdatawrapped'
        body['user_data'] = wrapped
        with mock.patch.object(instance_api, 'SCHEDULER', None):
            response = self.client.post(
                '/instances', data=json.dumps(body),
                content_type='application/json',
                headers={'Authorization': self.token})

        self.assertEqual(507, response.status_code, response.get_json())
