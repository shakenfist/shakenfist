# Copyright 2019 Michael Still and contributors

"""What POST /instances answers for a name it cannot use.

Phase 5 of PLAN-api-input-validation deleted the `except TypeError` arm
from handle_authorization_exceptions (decision D23), which had been
answering every handler-internal TypeError as a 400 carrying the
interpreter's own words. That arm was load bearing for one input on the
default path: `name` is declared required, and at the time required-ness
was deliberately not enforced (decision D17) while the compiled field
was nullable, so both an omitted name and an explicit JSON null reached
the handler as None. `validators.hostname(None, ...)` *returns* a falsy
ValidationError rather than raising, so execution used to reach
`'.' in name`, raise TypeError, and be answered as a 400 by the arm that
is now gone -- turning a plain caller mistake into a recorded 500 with a
file under /srv/shakenfist/exceptions/ and an ERROR `Server error` log
line.

Phase 6's step 3 resolved D17: `enforce`, the default, now refuses an
omitted or null `name` at the validation layer, before the handler with
the TypeError bug ever sees it. That story is only still true in `warn`
and `off`, which InstanceCreateNameRollbackTestCase below pins.

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
        """The default path, and no longer the regression phase 5
        introduced.

        `name` is declared required, and phase 6's step 3 deleted the
        filter that used to leave a missing-required finding recorded
        and unenforced (decision D17, resolved). The omission is now
        refused by the validation layer itself, before the handler
        whose TypeError the module docstring describes ever sees it --
        that story is only reachable in 'warn' or 'off' now, which
        InstanceCreateNameRollbackTestCase overrides this test to pin.
        """
        response, recorded = self._post(dict(VALID_REMAINDER))

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'name: declared required but not supplied',
             'status': 400},
            response.get_json())
        self.assertNoInterpreterText(response)
        recorded.assert_not_called()

    def test_an_explicitly_null_name_is_a_bad_request(self):
        """The same refusal by the other route.

        Every compiled field is allow_none=True (see
        validation._field's docstring), so without phase 6's step 3 an
        explicit null would slip past both the required check (the key
        is present) and the schema check (null is accepted) and reach
        the handler as None -- exactly the omission's old path. The
        required check now treats a required parameter's null the same
        as its absence, so this answers identically to the omission
        above instead of separately reaching the handler's guard.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = None
        response, recorded = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'name: declared required but not supplied',
             'status': 400},
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

    def test_an_omitted_name_is_a_bad_request(self):
        """Overridden: 'warn' does not enforce required-ness, so this
        is the mode where the omission still reaches the handler and
        its own guard answers -- the exact property the base class
        used to pin for every mode before phase 6's step 3 turned
        enforcement on. The TypeError story the module docstring
        describes lives here now.
        """
        response, recorded = self._post(dict(VALID_REMAINDER))

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'instance name must be specified', 'status': 400},
            response.get_json())
        self.assertNoInterpreterText(response)
        recorded.assert_not_called()

    def test_an_explicitly_null_name_is_a_bad_request(self):
        """Overridden for the same reason: an explicit null and an
        omission both still reach the handler unchanged under 'warn',
        by two different branches of the compiler -- a guard keyed on
        falsiness rather than on None would pass one of these two and
        not the other.
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


class InstanceCreateBooleanSpellingTestCase(AuthenticatedStackTestCase):
    """The declared booleans, read the way the schema publishes them.

    `uefi` and `secure_boot` are published as booleans, and
    marshmallow's Boolean accepts a set of *string* spellings --
    'false', 'no', 'off' and '0' are all valid and all mean False. The
    compiled path is check-only (decision D14), so the handler is
    handed the raw body, and until issue 4253's fix it read both keys
    with a bare truthiness test. The stored value happened to survive
    that -- InstanceData's `uefi: bool` is a lax pydantic field which
    coerces the same spellings -- but the guard between the handler
    and that rescue did not: {"secure_boot": "false"} was refused with
    `secure boot requires UEFI be enabled`, and {"secure_boot":
    "true", "uefi": "false"} sailed past the guard to store secure
    boot without UEFI, the exact combination it exists to refuse.
    validation.declared_boolean() is now the one reading at the API
    boundary, exactly as it already was for the networkspec's `float`
    (issue 4223's review finding, the first instance of this class),
    so the contract no longer depends on two libraries' spelling sets
    happening to agree.

    The reading under test is the handler's own, so it holds in every
    API_VALIDATION_MODE; the rollback subclass at the bottom re-runs
    every test at 'warn' to pin that.
    """

    mode = 'enforce'

    def _post_spying_on_new(self, body):
        """POST /instances, capturing what reaches Instance.new().

        The spy raises rather than returning: everything these tests
        assert is in the call arguments, and a create which continued
        past Instance.new() would need a scheduler and a placement to
        succeed. The resulting 500 is the sentinel's, not the code
        under test's, so no test here asserts on the response of a
        request which was expected to reach the spy. SCHEDULER is
        reset and record_exception stubbed for the reasons
        InstanceCreateNameTestCase._post documents.
        """
        with mock.patch.object(instance_api, 'SCHEDULER', None), \
                mock.patch.object(
                    api_base.util_exceptions, 'record_exception',
                    return_value={'exception-record': 'test'}), \
                mock.patch.object(
                    instance_api.instance.Instance, 'new',
                    side_effect=AssertionError(
                        'halted at instance creation by the test spy')) as new:
            response = self.client.post(
                '/instances', data=json.dumps(body),
                content_type='application/json',
                headers={'Authorization': self.token})
        return response, new

    def test_uefi_string_false_means_bios(self):
        """Issue 4253's companion case, at the unit level.

        The cluster CI half
        (test_api_validation.TestStringSpelledBooleanBootsBIOS) shows a
        real cluster storing False; this half asserts on the exact
        value handed to Instance.new(). Before the fix that value was
        the string 'false', and only pydantic's lax coercion inside
        the persistence model turned it into the False the caller
        meant -- a rescue by accident, one strict=True away from a
        recorded 500.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = 'uefistringfalse'
        body['uefi'] = 'false'
        _, new = self._post_spying_on_new(body)

        new.assert_called_once()
        self.assertIs(False, new.call_args.kwargs['uefi'])

    def test_uefi_string_true_means_uefi(self):
        """The other spelling, so the fix is a reading rather than a
        constant."""
        body = dict(VALID_REMAINDER)
        body['name'] = 'uefistringtrue'
        body['uefi'] = 'true'
        _, new = self._post_spying_on_new(body)

        new.assert_called_once()
        self.assertIs(True, new.call_args.kwargs['uefi'])

    def test_json_booleans_are_unchanged(self):
        """The dominant callers send real JSON booleans -- the shipped
        CLI and the ansible collection both do -- and their values must
        come through untouched."""
        body = dict(VALID_REMAINDER)
        body['name'] = 'uefijsonbool'
        body['uefi'] = True
        body['secure_boot'] = False
        _, new = self._post_spying_on_new(body)

        new.assert_called_once()
        self.assertIs(True, new.call_args.kwargs['uefi'])
        self.assertIs(False, new.call_args.kwargs['secure_boot'])

    def test_secure_boot_string_false_does_not_demand_uefi(self):
        """The sharpest consequence of the truthiness read.

        Before the fix, {"secure_boot": "false"} with no uefi at all
        was refused with `secure boot requires UEFI be enabled` -- a
        caller explicitly declining secure boot was told to turn UEFI
        on. The guard now sees the value the caller meant.
        """
        body = dict(VALID_REMAINDER)
        body['name'] = 'securebootoff'
        body['secure_boot'] = 'false'
        _, new = self._post_spying_on_new(body)

        new.assert_called_once()
        self.assertIs(False, new.call_args.kwargs['secure_boot'])

    def test_secure_boot_guard_reads_the_spellings(self):
        """And the guard itself now speaks for the decoded values:
        secure boot genuinely requested, UEFI genuinely declined, is
        still the refusal it has always been."""
        body = dict(VALID_REMAINDER)
        body['name'] = 'securebootguard'
        body['secure_boot'] = 'true'
        body['uefi'] = 'false'
        response, new = self._post_spying_on_new(body)

        self.assertEqual(400, response.status_code, response.get_json())
        self.assertEqual(
            {'error': 'secure boot requires UEFI be enabled', 'status': 400},
            response.get_json())
        new.assert_not_called()


class InstanceCreateBooleanSpellingRollbackTestCase(
        InstanceCreateBooleanSpellingTestCase):
    """The same properties at 'warn'.

    declared_boolean() is a handler read, not a validation-layer
    refusal, so the rollback must change nothing here -- these
    spellings pass the compiled boolean field at enforce anyway, which
    is exactly why the handler has to do its own reading (decision D42:
    warn and off must not hand back a newly unguarded API).
    """

    mode = 'warn'
