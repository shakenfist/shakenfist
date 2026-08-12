# Copyright 2019 Michael Still and contributors

"""Warn-only request validation.

Phase 3 PR 3. The property that matters most here is a negative one:
with API_VALIDATION_MODE at its default of 'warn', no request behaves
differently than it did before this layer existed. The findings are
recorded and logged; nothing acts on them until phase 4.
"""

from unittest import mock

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import validation
from shakenfist.tests import base


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

        No webargs error handler was registered before phase 3, so the
        four @use_kwargs sites answered a bad query parameter with
        webargs' default: 422, carrying {"json": {"field": [...]}} --
        the wrong status, and the only responses in the API which are
        not {"error": ..., "status": ...}. Nothing about *what* is
        rejected changes.
        """
        import werkzeug
        from marshmallow import ValidationError

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
