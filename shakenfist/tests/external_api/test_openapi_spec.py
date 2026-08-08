# Copyright 2019 Michael Still and contributors

from openapi_spec_validator import OpenAPIV2SpecValidator

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.tests import base


# The number of body parameters rendered with type/format where Swagger
# 2.0 requires a schema. This is a ratchet with an exact count, not a
# ceiling: an endpoint change that adds another one fails this test
# instead of quietly raising the number, the same honesty rule the
# declaration audit applies. Phase 2 of PLAN-api-input-validation
# collapses each operation's body parameters into a single
# schema-carrying parameter, at which point this reaches zero and the
# ratchet machinery should be deleted, leaving only "the specification
# is valid".
KNOWN_UNSCHEMAD_BODY_PARAMETERS = 128


class OpenAPISpecificationTestCase(base.ShakenFistTestCase):
    """Validate the OpenAPI specification flasgger generates.

    The published specification is what client generators read, and it
    was invalid in three ways nothing measured (issue 3626): schemes
    rendered as a string, security as a bare object, and body
    parameters carrying type/format instead of a schema. This test is
    the measurement.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True

        # The before_request hook resolves config.NODE_UUID, hitting
        # the database if it is not already set. Serving the
        # specification has no database dependency, so pin the UUID to
        # keep this hermetic.
        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = 'test-node-uuid'
        self.addCleanup(self._restore_node_uuid)

        self.client = external_api.app.test_client()

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def _fetch_spec(self):
        # flasgger registers its specs route directly on the Flask app
        # rather than through api_base.Resource, so it is served
        # without authentication, like the health probes.
        resp = self.client.get('/apispec_1.json')
        self.assertEqual(200, resp.status_code)
        return resp.get_json()

    @staticmethod
    def _node_at(spec, path):
        node = spec
        for key in path:
            node = node[key]
        return node

    def _is_unschemad_body_parameter(self, spec, error):
        # The known error class: a parameter declared in: body which
        # carries type/format where Swagger 2.0 requires a schema.
        # Classified structurally -- by looking at what the error
        # points to -- rather than by matching message text, which is
        # jsonschema's and changes between releases.
        node = self._node_at(spec, error.absolute_path)
        return (isinstance(node, dict) and node.get('in') == 'body'
                and 'schema' not in node)

    def test_specification_valid_apart_from_known_classes(self):
        spec = self._fetch_spec()
        errors = list(OpenAPIV2SpecValidator(spec).iter_errors())

        unknown = []
        known = 0
        for error in errors:
            if self._is_unschemad_body_parameter(spec, error):
                known += 1
            else:
                unknown.append('%s: %s' % (
                    '/'.join(str(p) for p in error.absolute_path),
                    error.message[:200]))

        self.assertEqual(
            [], unknown,
            'The generated specification has validation errors outside '
            'the known unschemad-body-parameter class:\n' +
            '\n'.join(unknown))
        self.assertEqual(
            KNOWN_UNSCHEMAD_BODY_PARAMETERS, known,
            'The count of body parameters rendered without a schema '
            'changed. If it went down, lower the ratchet constant; if '
            'it went up, a change added an invalid body parameter the '
            'phase 2 renderer work would have to unwind.')

    def test_security_requirements_resolve(self):
        # openapi_spec_validator does not check that a security
        # requirement names a defined scheme, and this was wrong in the
        # tree: operations required 'bearerAuth' while the template
        # defined no securityDefinitions at all, so a generated client
        # had no way to learn how to authenticate.
        spec = self._fetch_spec()
        defined = set(spec.get('securityDefinitions', {}))
        self.assertIn('bearerAuth', defined)

        unresolved = []
        for path, methods in spec['paths'].items():
            for method, operation in methods.items():
                if not isinstance(operation, dict):
                    continue
                for requirement in operation.get('security', []):
                    for scheme in requirement:
                        if scheme not in defined:
                            unresolved.append(
                                '%s %s references undefined security '
                                'scheme %r' % (method, path, scheme))
        self.assertEqual([], unresolved, '\n'.join(unresolved))
