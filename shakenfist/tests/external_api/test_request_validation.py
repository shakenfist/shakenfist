# Copyright 2019 Michael Still and contributors

"""Warn-only request validation.

Phase 3 PR 3. The property that matters most here is a negative one:
with API_VALIDATION_MODE at its default of 'warn', no request behaves
differently than it did before this layer existed. The findings are
recorded and logged; nothing acts on them until phase 4.
"""

import json
from unittest import mock

from marshmallow import ValidationError
import shakenfist_utilities.api as sf_utils_api
import werkzeug

from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import validation
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class RequestValidationTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        external_api.TESTING = True
        external_api.app.testing = True

        self.saved_node_uuid = config.NODE_UUID
        self.saved_mode = config.API_VALIDATION_MODE
        config.NODE_UUID = 'test-node-uuid'
        self.addCleanup(self._restore)

        self.client = external_api.app.test_client()
        self.namespace = mock.MagicMock()
        self.namespace.uuid = 'ns-uuid'

    def _restore(self):
        config.NODE_UUID = self.saved_node_uuid
        config.API_VALIDATION_MODE = self.saved_mode

    def _post_auth(self, body):
        """POST /auth, which is @public and declares two body parameters.

        Public so no token is needed, and the namespace lookup is
        mocked so the request reaches its handler rather than being
        short-circuited into a 404 -- which is itself one of the things
        this phase measures.
        """
        findings = []
        real = validation.check

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            findings.extend(out)
            return out

        with mock.patch.object(validation, 'check', spy), \
                mock.patch('shakenfist.namespace.Namespace.from_db',
                           return_value=self.namespace):
            response = self.client.post('/auth', json=body)
        return response, findings

    def test_warn_mode_changes_no_response(self):
        """The whole point of the phase.

        A body carrying an undeclared key produces a finding, and the
        response is byte for byte what it was before validation
        existed: the 400 log_request's merge has always produced, with
        the interpreter's own text. Phase 4 replaces that message; phase
        3 must not.
        """
        response, findings = self._post_auth(
            {'namespace': 'sys', 'key': 'k', 'zzz': 1})

        self.assertEqual(
            [validation.UNKNOWN_PARAMETER], [f.reason for f in findings])
        self.assertEqual(400, response.status_code)
        self.assertIn(
            'unexpected keyword argument', response.get_json()['error'])

    def test_a_clean_request_produces_no_findings(self):
        """Otherwise every request is a finding and the log says nothing."""
        response, findings = self._post_auth({'namespace': 'sys', 'key': 'k'})

        self.assertEqual([], findings)
        # 401 because the mocked namespace has no matching key. What
        # matters is that validation did not intervene.
        self.assertEqual(401, response.status_code)

    def test_a_body_uuid_is_an_ordinary_undeclared_parameter(self):
        """Decision D11: the passed_uuid remap is gone.

        It renamed a body `uuid` to a kwarg no handler in the tree
        accepts and no declaration names, so `{"uuid": ...}` was a
        guaranteed 400 carrying interpreter text on every endpoint --
        not the collision dodge decision D8 cites it as. Reported as
        what it is instead.
        """
        _, findings = self._post_auth(
            {'namespace': 'sys', 'key': 'k', 'uuid': 'x'})

        self.assertEqual(
            [(validation.UNKNOWN_PARAMETER, 'uuid')],
            [(f.reason, f.parameter) for f in findings])

    def test_a_wrong_type_is_reported(self):
        response, findings = self._post_auth(
            {'namespace': 'sys', 'key': 5})

        self.assertEqual(
            [(validation.TYPE_MISMATCH, 'key')],
            [(f.reason, f.parameter) for f in findings])
        # Unchanged: the handler's own guard answered, not validation.
        self.assertEqual(400, response.status_code)

    def test_a_finding_records_the_type_and_never_the_value(self):
        """Decision D5. Several of these routes carry credentials, which
        is why log_request drops the whole body on one rather than
        naming fields."""
        _, findings = self._post_auth({'namespace': 'sys', 'key': 5})

        fields = findings[0].fields()
        self.assertEqual('int', fields['validation-value-type'])
        self.assertNotIn(
            '5', str(fields), 'a finding must not carry the value')

    def test_enforce_mode_answers_in_the_api_error_shape(self):
        """Phase 4's switch, present from the start so the flip is
        configuration rather than a code change. webargs' own 422 shape
        is not what this API returns."""
        config.API_VALIDATION_MODE = 'enforce'

        response, _ = self._post_auth(
            {'namespace': 'sys', 'key': 'k', 'zzz': 1})

        self.assertEqual(400, response.status_code)
        body = response.get_json()
        self.assertEqual(400, body['status'])
        self.assertTrue(body['error'].startswith('zzz: '), body['error'])
        self.assertNotIn('unexpected keyword argument', body['error'])

    def test_enforce_mode_never_rejects_missing_required(self):
        """required is recorded and never enforced -- even in enforce
        mode. Several parameters are declared required while omitting
        them has always worked, so a missing-required finding is
        telemetry for phase 6's decision, not grounds for rejection.
        The second review round proved the first cut of the enforce
        branch rejected on any finding, contradicting this three times
        over in the documentation.
        """
        config.API_VALIDATION_MODE = 'enforce'

        response, findings = self._post_auth({'namespace': 'sys'})

        self.assertIn(
            validation.MISSING_REQUIRED, [f.reason for f in findings])
        # The handler's own guard answered, in its own words --
        # validation did not preempt it.
        self.assertEqual(400, response.status_code)
        self.assertEqual(
            'missing key in request', response.get_json()['error'])

    def test_off_mode_disables_the_layer(self):
        """The operator's safety valve: if the layer itself becomes the
        problem -- log volume being the foreseeable case -- it can be
        turned off without a downgrade. Off means check() never runs,
        not merely that findings are discarded."""
        config.API_VALIDATION_MODE = 'off'

        response, findings = self._post_auth(
            {'namespace': 'sys', 'key': 'k', 'zzz': 1})

        self.assertEqual([], findings)
        # And the request itself behaved exactly as it always did.
        self.assertEqual(400, response.status_code)
        self.assertIn(
            'unexpected keyword argument', response.get_json()['error'])

    def test_the_validator_does_not_refetch_the_body(self):
        """The validator reads the body log_request stashed, so it
        reports on exactly what the handler receives and a body which
        is not JSON is not paid for twice."""
        real = sf_utils_api.flask_get_post_body
        with mock.patch.object(
                sf_utils_api, 'flask_get_post_body',
                mock.Mock(wraps=real)) as spy, \
                mock.patch('shakenfist.namespace.Namespace.from_db',
                           return_value=self.namespace):
            self.client.post(
                '/auth', json={'namespace': 'sys', 'key': 'k'})

        self.assertEqual(1, spy.call_count)

    def test_check_is_pure(self):
        """The decision is separable from the request context, which is
        what lets the interesting cases be tested without one."""
        compiled = validation.CompiledEndpoint(
            body=None, query=None, path_names={'thing_ref'},
            required_names={'thing_ref'}, raw_body=False)

        self.assertEqual([], validation.check(compiled, {}, {}, set()))

        findings = validation.check(compiled, {}, {}, {'thing_ref'})
        self.assertEqual(
            [(validation.BODY_PATH_COLLISION, 'thing_ref')],
            [(f.reason, f.parameter) for f in findings])

    def test_check_tolerates_a_non_dict_body(self):
        """A warn-only layer must never raise from inside itself.

        log_request refuses a non-object body before validation runs,
        so a dict is what arrives in practice -- but check() is pure
        and independently callable, and iterating a list body on trust
        raised from inside the validator.
        """
        compiled = validation.CompiledEndpoint(
            body=None, query=None, path_names=set(),
            required_names=set(), raw_body=False)

        for body in (['ab', 'cd'], 'abc', 5, None):
            with self.subTest(body=body):
                self.assertEqual(
                    [], validation.check(compiled, body, {}, set()))

    def test_unknown_parameter_findings_are_capped(self):
        """Unknown body keys are bounded only by what a caller sends,
        and each finding is a log line shipped to centralised logging.
        The overflow is one summarising finding carrying the count, so
        the measurement still learns the request happened."""
        compiled = validation.CompiledEndpoint(
            body=None, query=None, path_names=set(),
            required_names=set(), raw_body=False)
        body = {'key%03d' % i: i for i in range(50)}

        findings = validation.check(compiled, body, {}, set())

        self.assertEqual(
            validation.MAX_UNKNOWN_PARAMETER_FINDINGS + 1, len(findings))
        overflow = findings[-1]
        self.assertEqual(validation.UNKNOWN_PARAMETER, overflow.reason)
        self.assertEqual('(overflow)', overflow.parameter)
        self.assertIn(
            '%d further undeclared keys'
            % (50 - validation.MAX_UNKNOWN_PARAMETER_FINDINGS),
            overflow.detail)

    def test_a_parameter_name_is_truncated(self):
        """The name is as client-supplied as the value on the
        unknown-parameter path: without a bound one request could put
        megabytes into a log line and, in enforce mode, into the
        response."""
        finding = validation.Finding(
            validation.UNKNOWN_PARAMETER, 'x' * 500, 'detail')

        self.assertEqual(
            validation.MAX_PARAMETER_NAME, len(finding.parameter))

    def test_control_characters_are_stripped_from_names(self):
        """The length bound alone would still let a newline in a key
        forge extra fields in a log line or, in enforce mode, in the
        response."""
        finding = validation.Finding(
            validation.UNKNOWN_PARAMETER, 'a\nfake-field=x\tb', 'detail')

        self.assertEqual('afake-field=xb', finding.parameter)

    def test_an_unrecognised_type_drops_its_bounds(self):
        """A Range validator on a Raw field raises TypeError from
        inside schema.validate() the moment it meets a value of an
        uncoercible Python type -- out through check() and out of the
        warn-only layer. An unrecognised type cannot meaningfully
        carry a bound, so the bound is dropped with the type."""
        compiled = validation.compile_parameters([
            {'in': 'body', 'name': 'payload', 'schema': {
                'type': 'object', 'properties': {
                    'thing': {'type': 'wibble', 'minimum': 1,
                              'maximum': 10}}}}])

        self.assertEqual([], list(
            compiled.body.fields['thing'].validators))
        # And the whole path is exercised: a value no Range could
        # compare against produces no findings and no exception.
        self.assertEqual(
            [], validation.check(
                compiled, {'thing': 'abc'}, {}, set()))

    def test_check_never_raises_even_if_a_schema_does(self):
        """The class-closing guarantee behind the instance above: if
        schema.validate() itself raises, the layer logs and reports
        nothing rather than changing the response."""
        compiled = validation.REGISTRY[('AuthEndpoint', 'post')]

        with mock.patch.object(
                type(compiled.body), 'validate',
                side_effect=TypeError('validator exploded')):
            self.assertEqual(
                [], validation.check(
                    compiled, {'namespace': 'sys', 'key': 'k'}, {}, set()))

    def test_two_endpoint_classes_with_one_name_are_refused(self):
        """The registry is keyed by bare class name, so a collision
        would silently validate one endpoint's requests against the
        other's schema -- and the completeness test collapses the
        duplicate on both sides of its comparison. Refused loudly at
        mount time instead."""
        first = type('CollidingEndpoint', (), {})
        second = type('CollidingEndpoint', (), {})

        class FakeView:
            pass

        view_a, view_b = FakeView(), FakeView()
        view_a.view_class, view_b.view_class = first, second

        class FakeApp:
            view_functions = {'a': view_a, 'b': view_b}

        self.assertRaises(
            exceptions.InvalidAPIDeclaration,
            validation.build_registry, FakeApp())

    def test_a_pattern_requires_the_whole_value_to_match(self):
        """Python's $ also matches before a trailing newline; ECMA-262's
        does not. The compiled validator uses fullmatch so 'value\\n'
        fails it exactly as it fails the published JSON Schema
        pattern."""
        compiled = validation.compile_parameters([
            {'in': 'body', 'name': 'payload', 'schema': {
                'type': 'object', 'properties': {
                    'thing': {'type': 'string', 'pattern': '^a+$'}}}}])

        self.assertEqual(
            [], validation.check(compiled, {'thing': 'aaa'}, {}, set()))
        findings = validation.check(compiled, {'thing': 'aaa\n'}, {}, set())
        self.assertEqual(
            [(validation.TYPE_MISMATCH, 'thing')],
            [(f.reason, f.parameter) for f in findings])

    def test_a_body_supplied_query_parameter_is_type_checked(self):
        """The shipped client serialises every request to a JSON body
        and never builds a query string, so a query-declared parameter
        checked against the query string alone is never checked for
        the API's dominant caller. check() mirrors the json_or_query
        loader's merged, body-authoritative view instead."""
        compiled = validation.REGISTRY[
            ('InstanceOutstandingOperationsEndpoint', 'get')]
        self.assertIn('all', compiled.query.fields)

        findings = validation.check(
            compiled, {'all': 'banana'}, {}, set())

        self.assertEqual(
            [(validation.TYPE_MISMATCH, 'all')],
            [(f.reason, f.parameter) for f in findings])

    def test_a_raw_body_is_never_reported(self):
        """Upload bodies are bytes. Every key of a JSON body would
        otherwise be undeclared, so an upload would be one long finding
        in warn mode and rejected outright in enforce."""
        compiled = validation.REGISTRY[('UploadDataEndpoint', 'post')]

        self.assertTrue(compiled.raw_body)
        self.assertEqual(
            [], validation.check(compiled, {'anything': 1}, {}, set()))

    def test_undocumented_endpoints_are_not_validated(self):
        """Root, Livez and Readyz compile to nothing, and a request to
        one must pass through rather than fail a lookup."""
        self.assertNotIn(('Root', 'get'), validation.REGISTRY)

        self.assertEqual(200, self.client.get('/').status_code)

    def test_validation_is_innermost(self):
        """Restated here as well as in test_auth_universal, because this
        is the file which explains why: being innermost is what makes
        func a bound method, so the endpoint class is readable without
        depending on attribute propagation through decorators in
        base.py which predate functools.wraps."""
        self.assertEqual(
            api_base.validate_request,
            api_base.Resource.method_decorators[0])

    def test_webargs_failures_use_the_api_error_shape(self):
        """Decision D4, and a defect fixed on the way past.

        No webargs error handler was registered before phase 3, and
        webargs' default 422 abort was swallowed into a 500 by
        suppress_exceptions_to_client's bare except -- so the four
        @use_kwargs sites answered a bad query parameter with a server
        error, a traceback in the log and an exception record on disk.
        Nothing about *what* is rejected changes.

        This exercises the handler in isolation, which asserts the
        response it builds and nothing about what a client sees; the
        first review round proved those are different questions. The
        request-level assertion lives in
        AuthenticatedValidationTestCase.
        """
        with external_api.app.test_request_context('/'):
            with self.assertRaises(werkzeug.exceptions.HTTPException) as caught:
                api_base._webargs_error(
                    ValidationError({'query': {'limit': ['Not a valid integer.']}}),
                    None, None, error_status_code=422, error_headers=None)

        response = caught.exception.get_response()
        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {'error': 'limit: Not a valid integer.', 'status': 400},
            response.get_json())

    def test_a_non_object_body_is_still_a_400(self):
        """A JSON body which is not an object has always been a 400.

        The per-key merge this phase replaced raised TypeError for one,
        which handle_authorization_exceptions answers as 400.
        dict.update would instead raise ValueError for most of these
        (a 500, since nothing catches ValueError) -- and would silently
        merge a list of two-character strings as key/value pairs, which
        is an unintended input path into the kwargs merge. The explicit
        guard keeps all of them a 400.
        """
        for payload in (['a', 'b'], 'abc', ['ab', 'cd'], 5):
            with self.subTest(payload=payload):
                response = self.client.post(
                    '/auth', data=json.dumps(payload),
                    content_type='application/json')

                self.assertEqual(400, response.status_code)
                self.assertEqual(
                    'the request body must be a JSON object',
                    response.get_json()['error'])

    def test_findings_are_emitted_with_the_response_status(self):
        """The after_request hook is the deliverable: a finding line
        carrying what the request returned anyway is what separates a
        rejection enforcement would introduce from a status code it
        would merely change."""
        with mock.patch.object(external_api, 'LOG') as log:
            response, findings = self._post_auth(
                {'namespace': 'sys', 'key': 'k', 'zzz': 1})

        self.assertEqual(400, response.status_code)
        self.assertEqual(1, len(findings))

        emitted = [c.args[0] for c in log.with_fields.call_args_list
                   if 'validation-reason' in c.args[0]]
        self.assertEqual(1, len(emitted))
        self.assertEqual(
            validation.UNKNOWN_PARAMETER, emitted[0]['validation-reason'])
        self.assertEqual(400, emitted[0]['validation-response-status'])
        self.assertEqual('warn', emitted[0]['validation-mode'])

    def test_enforced_rejections_still_emit_their_findings(self):
        """An enforced rejection must not be silent in the log.

        The third review round caught the enforce branch returning
        before the findings were stashed, which would have turned the
        measurement apparatus off at the exact moment phase 4 flips
        the switch -- when an operator most needs to see which
        parameter a refused request was refused for.
        """
        config.API_VALIDATION_MODE = 'enforce'

        with mock.patch.object(external_api, 'LOG') as log:
            response, _ = self._post_auth(
                {'namespace': 'sys', 'key': 'k', 'zzz': 1})

        self.assertEqual(400, response.status_code)
        self.assertTrue(
            response.get_json()['error'].startswith('zzz: '))

        emitted = [c.args[0] for c in log.with_fields.call_args_list
                   if 'validation-reason' in c.args[0]]
        self.assertEqual(1, len(emitted))
        self.assertEqual('enforce', emitted[0]['validation-mode'])
        self.assertEqual(400, emitted[0]['validation-response-status'])

    def test_credential_routes_redact_the_parameter_name(self):
        """/auth drops everything body-derived from its logs, and the
        finding line is a third body-reading logger: a buggy caller
        can put secret-bearing material in a key position, so the
        parameter name is redacted where the other two loggers drop
        the whole body. The reason code -- what the measurement needs
        -- still rides out."""
        with mock.patch.object(external_api, 'LOG') as log:
            response, _ = self._post_auth(
                {'namespace': 'sys', 'key': 'k', 'zzz': 1})

        self.assertEqual(400, response.status_code)
        emitted = [c.args[0] for c in log.with_fields.call_args_list
                   if 'validation-reason' in c.args[0]]
        self.assertEqual(1, len(emitted))
        self.assertEqual(
            validation.UNKNOWN_PARAMETER, emitted[0]['validation-reason'])
        self.assertEqual('*****', emitted[0]['validation-parameter'])
        self.assertNotIn('zzz', str(emitted[0]))

    def test_findings_do_not_leak_between_requests(self):
        """flask.g is request scoped by contract; this pins that a
        clean request after a finding-producing one emits nothing."""
        _, findings = self._post_auth(
            {'namespace': 'sys', 'key': 'k', 'zzz': 1})
        self.assertEqual(1, len(findings))

        with mock.patch.object(external_api, 'LOG') as log:
            _, findings = self._post_auth({'namespace': 'sys', 'key': 'k'})

        self.assertEqual([], findings)
        emitted = [c.args[0] for c in log.with_fields.call_args_list
                   if 'validation-reason' in c.args[0]]
        self.assertEqual([], emitted)


class AuthenticatedValidationTestCase(base.ShakenFistTestCase):
    """Request-level properties which need the full decorator stack.

    The first review round found the deployed behaviour and the
    behaviour of a handler tested in isolation were different answers:
    _webargs_error built a perfect 400 which
    suppress_exceptions_to_client then swallowed into a 500. Everything
    here therefore drives a real authenticated request end to end and
    asserts only what a client sees or what actually reached a spy.
    """

    def setUp(self):
        super().setUp()
        external_api.TESTING = True
        external_api.app.testing = True

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')

        self.client = external_api.app.test_client()
        resp = self.client.post(
            '/auth',
            data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.token = 'Bearer %s' % resp.get_json()['access_token']

    def test_a_webargs_failure_answers_400_through_the_real_stack(self):
        """The whole journey: use_kwargs raises, _webargs_error aborts
        with a crafted response, record_exception declines to record
        it, and suppress_exceptions_to_client returns it instead of
        swallowing it into a 500."""
        with mock.patch.object(
                api_base.util_exceptions, 'record_exception') as recorded:
            response = self.client.get(
                '/blobs/00000000-0000-0000-0000-000000000000/data'
                '?limit=notanint',
                headers={'Authorization': self.token})

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {'error': 'limit: Not a valid integer.', 'status': 400},
            response.get_json())
        # A malformed query parameter is a client error, not something
        # to write under /srv/shakenfist/exceptions/ on every request.
        recorded.assert_not_called()

    def test_a_raw_body_is_not_parsed_as_json(self):
        """An upload body is arbitrary binary of arbitrary size, and
        flask_get_post_body() attempts two full JSON parses of a body
        which is not JSON. log_request pays that once today; the
        validator must not pay it again for a result check() would
        discard anyway."""
        real = sf_utils_api.flask_get_post_body
        with mock.patch.object(
                sf_utils_api, 'flask_get_post_body',
                mock.Mock(wraps=real)) as spy:
            self.client.post(
                '/upload/00000000-0000-0000-0000-000000000000',
                data=b'\x00\x01\x02 not json',
                headers={'Authorization': self.token})

        self.assertEqual(1, spy.call_count)

    def test_a_finding_on_a_successful_request_changes_nothing(self):
        """The phase's central promise, pinned on a 2xx.

        Every other warn-mode test lands on a request that was already
        failing, so none of them could catch the warn layer breaking
        traffic that works today -- and a finding on a 200 is exactly
        the population decision D10 is trying to size. A body key
        shadowing its own path parameter with the same value is a
        finding by construction and a no-op by construction, so the
        response must be byte for byte the response of the same
        request without the body.
        """
        findings = []
        real = validation.check

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            findings.extend(out)
            return out

        headers = {'Authorization': self.token}
        clean = self.client.get('/auth/namespaces/system', headers=headers)
        self.assertEqual(200, clean.status_code)

        with mock.patch.object(validation, 'check', spy):
            response = self.client.get(
                '/auth/namespaces/system',
                data=json.dumps({'namespace': 'system'}),
                content_type='application/json', headers=headers)

        self.assertEqual(
            [(validation.BODY_PATH_COLLISION, 'namespace')],
            [(f.reason, f.parameter) for f in findings])
        self.assertEqual(200, response.status_code)
        self.assertEqual(clean.get_data(), response.get_data())

    def test_a_body_path_collision_is_recorded_through_the_stack(self):
        """The log_request -> flask.g -> validate_request hand-off,
        driven by a real request rather than a hand-constructed
        CompiledEndpoint: a body key shadowing a path parameter is
        recorded where the overwrite happens and reported by the
        validator which runs after it."""
        findings = []
        real = validation.check

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            findings.extend(out)
            return out

        with mock.patch.object(validation, 'check', spy):
            response = self.client.delete(
                '/instances/nosuchinstance',
                data=json.dumps({'instance_ref': 'adifferentinstance'}),
                content_type='application/json',
                headers={'Authorization': self.token})

        self.assertIn(
            (validation.BODY_PATH_COLLISION, 'instance_ref'),
            [(f.reason, f.parameter) for f in findings])
        # And warn mode changed nothing: the handler's own 404 answered.
        self.assertEqual(404, response.status_code)
