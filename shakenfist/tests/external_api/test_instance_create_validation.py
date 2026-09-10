# Copyright 2019 Michael Still and contributors

"""What POST /instances answers for a name it cannot use.

Phase 5 of PLAN-api-input-validation deleted the `except TypeError` arm
from handle_authorization_exceptions (decision D23), which had been
answering every handler-internal TypeError as a 400 carrying the
interpreter's own words. That arm was load bearing for one input on the
default path: `name` is declared required, required-ness is deliberately
not enforced (decision D17) and the compiled field is nullable, so both
an omitted name and an explicit JSON null reach the handler as None.
`validators.hostname(None, ...)` *returns* a falsy ValidationError
rather than raising, so execution used to reach `'.' in name`, raise
TypeError, and be answered as a 400 by the arm that is now gone --
turning a plain caller mistake into a recorded 500 with a file under
/srv/shakenfist/exceptions/ and an ERROR `Server error` log line.

These drive real authenticated requests through the whole decorator
stack, for the reason AuthenticatedStackTestCase documents: phase 3's
review found a handler tested in isolation and the deployed behaviour
giving different answers, because suppress_exceptions_to_client sits
outside everything a unit test sees.
"""

import json
from unittest import mock

from shakenfist.external_api import base as api_base
from shakenfist.external_api import instance as instance_api
from shakenfist.tests.external_api.test_request_validation import (
    AuthenticatedStackTestCase)
from shakenfist.tests.external_api.test_request_validation import (
    INTERPRETER_TEXT)


# Enough of a body to get past every guard which precedes the name
# check, so a test about `name` fails for the reason it says it does.
# `disk` in particular is refused before the instance object exists, and
# a body without it would answer 400 whatever the name was.
VALID_REMAINDER = {
    'cpus': 1,
    'memory': 1024,
    'disk': [{'size': 8}]
}


class InstanceCreateNameTestCase(AuthenticatedStackTestCase):
    """The name guard, at both validation modes that reach the handler."""

    mode = 'enforce'

    def _post(self, body):
        """POST /instances, with exception recording spied on rather than
        performed.

        The spy is the point rather than incidental: a 500 here writes a
        file under /srv/shakenfist/exceptions/ for what is a caller's
        mistake, and "no record was written" is the half of this defect
        which a status code assertion alone would not catch.

        InstancesEndpoint.post caches its Scheduler in a module global
        and only builds one if that global is falsy, so a request which
        reaches placement leaves a real Scheduler behind for every
        later test in the same worker process -- including the ones in
        test_external_api.py which patch shakenfist.scheduler.Scheduler
        with a fake in setUp and then never get asked for it, and so
        fail with the scheduler refusal of whatever cluster this
        fixture built. patch.object restores the attribute it saved
        even though the handler reassigns it, which is exactly the
        containment wanted here.
        """
        with mock.patch.object(instance_api, 'SCHEDULER', None), \
                mock.patch.object(
                    api_base.util_exceptions, 'record_exception',
                    return_value={'exception-record': 'test'}) as recorded:
            response = self.client.post(
                '/instances', data=json.dumps(body),
                content_type='application/json',
                headers={'Authorization': self.token})
        return response, recorded

    def assertNoInterpreterText(self, response):
        """The absence, asserted positively.

        Against the whole response body rather than the error string, so
        a leak into any other field is caught too. INTERPRETER_TEXT is
        imported rather than restated because a marker added there must
        apply here as well.
        """
        body = response.get_data(as_text=True)
        for fragment in INTERPRETER_TEXT:
            self.assertNotIn(
                fragment, body,
                'the refusal leaked interpreter text: %s' % body)

    def test_an_omitted_name_is_a_bad_request(self):
        """The default path, and the regression phase 5 introduced.

        `name` is declared required and D17 says the validation layer
        does not enforce that, so this body produces a
        missing-required finding, is not refused for it, and reaches
        the handler with name=None. Before the guard this was the
        TypeError described in the module docstring; the property
        pinned here is that a caller who forgot a name is told so
        rather than being told the cluster broke.
        """
        response, recorded = self._post(dict(VALID_REMAINDER))

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'instance name must be specified', 'status': 400},
            response.get_json())
        self.assertNoInterpreterText(response)
        recorded.assert_not_called()

    def test_an_explicitly_null_name_is_a_bad_request(self):
        """The same defect by the other route.

        The compiled field is allow_none=True (see validation._field:
        a JSON null reaches the handler as None today and several
        handlers read that as "not supplied"), so an explicit null is
        not a type mismatch and is not refused by the layer either. It
        is worth its own test because it arrives through a different
        branch of the compiler than an omitted key does, and a guard
        keyed on falsiness rather than on None would pass one of these
        two and not the other.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = None
        response, recorded = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'instance name must be specified', 'status': 400},
            response.get_json())
        self.assertNoInterpreterText(response)
        recorded.assert_not_called()

    def test_a_non_string_name_is_refused_by_the_validation_layer(self):
        """Enforcement answers first, and its message is unchanged.

        The handler's isinstance guard exists for the rollback modes,
        not for this one. Pinning which of the two answers here means a
        later change that moved the refusal from the layer into the
        handler would be visible rather than silent -- the two produce
        different messages and the published behaviour is the layer's.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = 5
        response, recorded = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'name: Not a valid string.', 'status': 400},
            response.get_json())
        self.assertNoInterpreterText(response)
        recorded.assert_not_called()

    def test_a_valid_name_gets_past_the_guard(self):
        """The guard refuses nothing that used to work.

        A single node cluster with no hypervisor cannot place an
        instance, so the request lands on the scheduler's 507. That is
        the point: the name check is behind it, so a 507 is proof the
        request reached the placement stage rather than being turned
        away by a guard which is too eager. Asserting the 507 rather
        than a 200 keeps this from needing a whole fake hypervisor to
        make an assertion about a name.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = 'validname'
        response, recorded = self._post(body)

        self.assertEqual(507, response.status_code, response.get_json())
        recorded.assert_not_called()

    def test_a_name_containing_a_dot_still_gets_the_host_name_refusal(self):
        """The existing 400, unchanged.

        A dotted name is a string, so it falls through both new arms to
        the DNS host name explanation it has always received. The
        message is asserted in full because the whole shape of the fix
        is "add two arms above this one and change nothing about it",
        and a paraphrase would not detect the guard swallowing this
        case.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = 'not.a.hostname'
        response, recorded = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'instance name not.a.hostname is not useable as a DNS '
                      'and Linux host name. That is, less than 63 characters '
                      'and in the character set: a-z, A-Z, 0-9, or hyphen '
                      '(-).',
             'status': 400},
            response.get_json())
        recorded.assert_not_called()

    def test_an_over_long_name_still_gets_the_host_name_refusal(self):
        """The other pre-existing 400, for the same reason.

        64 characters is one past the 63 the handler allows, so this
        exercises the `len(name) > 63` clause specifically -- the one
        arm of the original condition that a None would have reached
        only if the two before it had somehow passed.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = 'a' * 64
        response, recorded = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertIn(
            'is not useable as a DNS and Linux host name',
            response.get_json()['error'])
        recorded.assert_not_called()


class InstanceCreateNameRollbackTestCase(InstanceCreateNameTestCase):
    """The same properties with enforcement rolled back to 'warn'.

    'warn' promises that no request behaves differently than it does
    with the layer switched off, so it is the mode in which a
    non-string name actually reaches the handler -- which is the only
    mode where the isinstance arm of the guard is reachable at all. An
    arm no test can reach is an arm that rots, and this class is what
    stops the rollback path being the one where a caller's mistake is
    still a recorded 500.
    """

    mode = 'warn'

    def test_a_non_string_name_is_refused_by_the_validation_layer(self):
        """Overridden: in 'warn' the layer stands aside and the
        handler's own guard is what answers, with its own message.

        This is the arm the enforce-mode test above cannot reach. It
        matters because the whole purpose of 'warn' is to be an
        operator's escape hatch from enforcement, and an escape hatch
        which turns a bad request into a 500 is not one.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = 5
        response, recorded = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'instance name must be a string', 'status': 400},
            response.get_json())
        self.assertNoInterpreterText(response)
        recorded.assert_not_called()
