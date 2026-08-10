# Copyright 2019 Michael Still and contributors

from openapi_spec_validator import OpenAPIV2SpecValidator

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.tests import base


class OpenAPISpecificationTestCase(base.ShakenFistTestCase):
    """Validate the OpenAPI specification flasgger generates.

    The published specification is what client generators read, and it
    was invalid in three ways nothing measured (issue 3626): schemes
    rendered as a string, body parameters carrying type/format instead
    of a schema, and security requirements referencing an undefined
    scheme. All three are fixed; this test is what keeps them fixed.

    This began life as a ratchet holding the body-parameter error
    class to an exact count of 128 while the schemes fix landed ahead
    of the renderer collapse. The collapse took it to zero, so the
    classifier is gone and simple validity is the permanent assertion.
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

    def test_specification_is_valid(self):
        spec = self._fetch_spec()
        errors = [
            '%s: %s' % ('/'.join(str(p) for p in error.absolute_path),
                        error.message[:200])
            for error in OpenAPIV2SpecValidator(spec).iter_errors()]
        self.assertEqual(
            [], errors,
            'The generated specification is not valid OpenAPI 2.0:\n' +
            '\n'.join(errors))

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

    def test_structured_parameters_publish_their_real_shape(self):
        # instance create metadata is a dictionary on the wire: the
        # handler answers 400 to anything else, and the functional
        # suite posts a dict. It was declared arrayofdict, which was
        # inert while the token rendered as prose but became a positive
        # assertion of the wrong shape once array types became real --
        # and phase 3 would compile it into rejecting the only shape
        # that works. Pinned against the published specification rather
        # than the declaration, because the specification is what a
        # client generator reads.
        spec = self._fetch_spec()
        body = [p for p in spec['paths']['/instances']['post']['parameters']
                if p.get('in') == 'body'][0]
        properties = body['schema']['properties']

        self.assertEqual('object', properties['metadata']['type'])
        self.assertNotIn('items', properties['metadata'])
        self.assertEqual('object', properties['video']['type'])
        # Its neighbours which genuinely are arrays stay arrays.
        self.assertEqual('array', properties['disk']['type'])
        self.assertEqual('array', properties['network']['type'])

    def test_at_most_one_body_parameter_per_operation(self):
        # The validator catches an unschemad body parameter, but "at
        # most one body parameter" is checked here directly so a
        # regression names the operation rather than surfacing as a
        # oneOf mismatch deep in jsonschema output.
        spec = self._fetch_spec()
        offenders = []
        for path, methods in spec['paths'].items():
            for method, operation in methods.items():
                if not isinstance(operation, dict):
                    continue
                bodies = [p for p in operation.get('parameters', [])
                          if p.get('in') == 'body']
                if len(bodies) > 1:
                    offenders.append('%s %s has %d body parameters'
                                     % (method, path, len(bodies)))
                for body in bodies:
                    if 'schema' not in body:
                        offenders.append(
                            '%s %s body parameter %r has no schema'
                            % (method, path, body.get('name')))
        self.assertEqual([], offenders, '\n'.join(offenders))
