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

    # Every parameter which publishes a structure or a bound, and the
    # shape the handler actually accepts. A type token is not derived
    # from anything -- declarations.py reads a declaration's name and
    # location and never looks at its type -- so this table is the
    # audit. Two defects of exactly this class shipped in review
    # rounds of this PR: metadata declared as an array while the
    # handler answers 400 to anything but a mapping, and console
    # length declared unsigned while -1 means "the whole log".
    #
    # A new entry belongs here whenever a declaration gains a
    # structured type or a bound. Read the handler before adding one:
    # the point is agreement with the code, not with the declaration.
    STRUCTURED_PARAMETERS = [
        # (path, method, parameter, expected subset of its schema)
        ('/instances', 'post', 'metadata', {'type': 'object'}),
        ('/instances', 'post', 'video', {'type': 'object'}),
        ('/instances', 'post', 'disk', {'type': 'array'}),
        ('/instances', 'post', 'network', {'type': 'array'}),
        ('/instances', 'post', 'side_channels', {'type': 'array'}),
        ('/instances', 'post', 'cpus', {'type': 'integer', 'minimum': 0}),
        ('/instances', 'post', 'memory', {'type': 'integer', 'minimum': 0}),
        ('/instances', 'post', 'user_data',
         {'type': 'string', 'format': 'byte'}),
        # -1 is a supported sentinel meaning the whole log, so this one
        # must NOT publish a minimum.
        ('/instances/{instance_ref}/consoledata', 'get', 'length',
         {'type': 'integer'}),
        ('/instances/{instance_ref}/interfaces', 'post', 'network',
         {'type': 'object'}),
        ('/instances/{instance_ref}/events', 'get', 'limit',
         {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
        ('/networks', 'post', 'netblock', {'type': 'string'}),
        ('/artifacts/{artifact_ref}/versions', 'post', 'max_versions',
         {'type': 'integer', 'minimum': 0}),
        ('/auth/namespaces/{namespace}/rules', 'post', 'scopes',
         {'type': 'array'}),
        ('/auth/namespaces/{namespace}/rules', 'post', 'bound_claims',
         {'type': 'object'}),
        ('/auth/namespaces/{namespace}/rules', 'post', 'key_ttl',
         {'type': 'integer', 'minimum': 0}),
    ]

    def test_structured_parameters_publish_their_real_shape(self):
        # Pinned against the published specification rather than
        # against the declarations, because the specification is what a
        # client generator reads and what phase 3 will compile.
        spec = self._fetch_spec()
        wrong = []

        for (path, method, name, expected) in self.STRUCTURED_PARAMETERS:
            operation = spec['paths'][path][method]
            parameters = operation.get('parameters', [])
            bodies = [p for p in parameters if p.get('in') == 'body']
            if bodies:
                published = bodies[0]['schema'].get('properties', {})
            else:
                published = {}
            published.update(
                {p['name']: p for p in parameters if p.get('in') != 'body'})

            if name not in published:
                wrong.append('%s %s has no parameter %r' % (method, path, name))
                continue

            for (key, value) in expected.items():
                if published[name].get(key) != value:
                    wrong.append(
                        '%s %s %s: %s is %r, expected %r'
                        % (method, path, name, key,
                           published[name].get(key), value))

            # A bound nobody asked for is the failure mode that shipped
            # twice, so an entry which declares no minimum asserts the
            # absence of one.
            if 'minimum' not in expected and 'minimum' in published[name]:
                wrong.append(
                    '%s %s %s publishes minimum %r, which this table does '
                    'not expect -- if the handler really refuses values '
                    'below it, add it here'
                    % (method, path, name, published[name]['minimum']))

        self.assertEqual([], wrong, '\n'.join(wrong))

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
