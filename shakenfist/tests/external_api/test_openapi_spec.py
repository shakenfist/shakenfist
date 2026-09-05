# Copyright 2019 Michael Still and contributors

from openapi_spec_validator import OpenAPIV2SpecValidator

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
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
    # structured type or a bound, and
    # test_every_published_structure_or_bound_is_registered() fails
    # until it does -- the table is hand-written but its completeness
    # is derived, because a registry which can silently fall behind is
    # the same failure mode as the prose types it replaced. Read the
    # handler before adding an entry: the point is agreement with the
    # code, not with the declaration.
    STRUCTURED_PARAMETERS = [
        # (path, method, parameter, expected subset of its schema)
        ('/instances', 'post', 'metadata', {'type': 'object'}),
        ('/instances', 'post', 'video', {'type': 'object'}),
        # Lists of diskspecs and networkspecs, so an array of objects.
        ('/instances', 'post', 'disk',
         {'type': 'array', 'items': {'type': 'object'}}),
        ('/instances', 'post', 'network',
         {'type': 'array', 'items': {'type': 'object'}}),
        # Side channel names, so an array of strings.
        ('/instances', 'post', 'side_channels',
         {'type': 'array', 'items': {'type': 'string'}}),
        ('/instances', 'post', 'cpus', {'type': 'integer', 'minimum': 0}),
        ('/instances', 'post', 'memory', {'type': 'integer', 'minimum': 0}),
        ('/instances', 'post', 'user_data',
         {'type': 'string', 'format': 'byte'}),
        # -1 is a supported sentinel meaning the whole log, so this one
        # must NOT publish a bound of any kind.
        ('/instances/{instance_ref}/consoledata', 'get', 'length',
         {'type': 'integer'}),
        ('/instances/{instance_ref}/interfaces', 'post', 'network',
         {'type': 'object'}),
        ('/instances/{instance_ref}/events', 'get', 'limit',
         {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
        ('/instances/{instance_ref}/snapshot', 'post', 'max_versions',
         {'type': 'integer', 'minimum': 0}),
        # The agent operation timing parameters. Their minimum is 0
        # because a duration cannot be negative, and 0 itself is a
        # sentinel meaning "none" rather than a floor -- do not "tidy"
        # the minimum to 1. agent/execute publishes deadline_seconds
        # and deliberately no progress_timeout_seconds: no command it
        # builds reports progress, so the enforcement phase could never
        # consult one. api_base.agent_operation_timing() is what backs
        # these bounds, answering 400, rather than the coercion the
        # events limit cap above relies on. The maximum is
        # AGENT_OPERATION_MAX_DEADLINE (issue #4074), an operator
        # settable ceiling published at whatever the deployment sets it
        # to; 86400 here is its default, which is what an unconfigured
        # test process generates the specification with.
        ('/instances/{instance_ref}/agent/put', 'post', 'deadline_seconds',
         {'type': 'number', 'minimum': 0, 'maximum': 86400}),
        ('/instances/{instance_ref}/agent/put', 'post',
         'progress_timeout_seconds',
         {'type': 'number', 'minimum': 0, 'maximum': 86400}),
        ('/instances/{instance_ref}/agent/get', 'post', 'deadline_seconds',
         {'type': 'number', 'minimum': 0, 'maximum': 86400}),
        ('/instances/{instance_ref}/agent/get', 'post',
         'progress_timeout_seconds',
         {'type': 'number', 'minimum': 0, 'maximum': 86400}),
        ('/instances/{instance_ref}/agent/execute', 'post',
         'deadline_seconds',
         {'type': 'number', 'minimum': 0, 'maximum': 86400}),
        ('/networks', 'post', 'netblock', {'type': 'string'}),
        ('/networks/{network_ref}/events', 'get', 'limit',
         {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
        ('/artifacts/{artifact_ref}/versions', 'post', 'max_versions',
         {'type': 'integer', 'minimum': 0}),
        ('/artifacts/{artifact_ref}/events', 'get', 'limit',
         {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
        # A version index counts from zero upwards; there is no
        # sentinel here, unlike console length.
        ('/artifacts/{artifact_ref}/versions/{version_id}', 'delete',
         'version_id', {'type': 'integer', 'minimum': 0}),
        ('/label/{label_name}', 'post', 'max_versions',
         {'type': 'integer', 'minimum': 0}),
        # Byte offsets into a file and a byte count, so both are
        # non-negative; BlobDataEndpoint's webargs schema refuses a
        # negative before the response starts streaming.
        ('/blobs/{blob_uuid}/data', 'get', 'offset',
         {'type': 'integer', 'minimum': 0}),
        ('/blobs/{blob_uuid}/data', 'get', 'limit',
         {'type': 'integer', 'minimum': 0}),
        ('/blobs/{blob_uuid}/events', 'get', 'limit',
         {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
        ('/nodes/{node}/events', 'get', 'limit',
         {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
        ('/upload/{upload_uuid}/truncate/{offset}', 'post', 'offset',
         {'type': 'integer', 'minimum': 0}),
        ('/auth/namespaces/{namespace}/rules', 'post', 'scopes',
         {'type': 'array', 'items': {'type': 'string'}}),
        ('/auth/namespaces/{namespace}/rules', 'post', 'bound_claims',
         {'type': 'object'}),
        # validate_key_ttl() refuses zero as well as negatives, and
        # caps at MAX_KEY_TTL_SECONDS, so this is not unsignedinteger.
        ('/auth/namespaces/{namespace}/rules', 'post', 'key_ttl',
         {'type': 'integer', 'minimum': 1, 'maximum': 86400}),
        ('/auth/namespaces/{namespace}/rules/{rule_name}', 'put', 'scopes',
         {'type': 'array', 'items': {'type': 'string'}}),
        ('/auth/namespaces/{namespace}/rules/{rule_name}', 'put',
         'bound_claims', {'type': 'object'}),
        ('/auth/namespaces/{namespace}/rules/{rule_name}', 'put', 'key_ttl',
         {'type': 'integer', 'minimum': 1, 'maximum': 86400}),
        # Namespace capacity claims. The three limits are counts of
        # cpus, megabytes and gigabytes, and _claim_limit() answers 400
        # to a negative one, so unsignedinteger's published minimum of 0
        # is backed by the handler. Zero is deliberately accepted: a
        # claim of no capacity in a dimension is how an operator says
        # "this namespace places nothing here", and refusing it would be
        # a policy decision the server does not make. There is no
        # maximum: the cluster's own totals are the only ceiling, they
        # change as nodes join and leave, and the guarded transaction
        # refuses an over-large claim with a 507 -- a published bound
        # would be wrong on the next node to arrive.
        ('/auth/namespaces/{namespace}/claims', 'post', 'limit_cpus',
         {'type': 'integer', 'minimum': 0}),
        ('/auth/namespaces/{namespace}/claims', 'post', 'limit_memory_mb',
         {'type': 'integer', 'minimum': 0}),
        ('/auth/namespaces/{namespace}/claims', 'post', 'limit_disk_gb',
         {'type': 'integer', 'minimum': 0}),
        # A duration in seconds, and _claim_expiry() refuses zero as
        # well as negatives -- a claim already expired when it is
        # created holds capacity for nobody and cannot be grown -- so
        # this is not unsignedinteger. Nothing caps it, so no maximum is
        # published.
        ('/auth/namespaces/{namespace}/claims', 'post', 'expires_in_seconds',
         {'type': 'integer', 'minimum': 1}),
        ('/auth/namespaces/{namespace}/claims/{claim_ref}', 'put',
         'limit_cpus', {'type': 'integer', 'minimum': 0}),
        ('/auth/namespaces/{namespace}/claims/{claim_ref}', 'put',
         'limit_memory_mb', {'type': 'integer', 'minimum': 0}),
        ('/auth/namespaces/{namespace}/claims/{claim_ref}', 'put',
         'limit_disk_gb', {'type': 'integer', 'minimum': 0}),
        ('/auth/namespaces/{namespace}/claims/{claim_ref}', 'put',
         'expires_in_seconds', {'type': 'integer', 'minimum': 1}),
        # The two capacity events endpoints. Their limit shares the
        # bounds every other events endpoint publishes, and for the
        # same reason: the server coerces anything below 1 to the
        # default and caps at 1000, so both bounds are backed.
        ('/auth/namespaces/{namespace}/events', 'get', 'limit',
         {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
        ('/auth/namespaces/{namespace}/claims/{claim_ref}/events', 'get',
         'limit', {'type': 'integer', 'minimum': 1, 'maximum': 1000}),
    ]

    # A published parameter is this table's business if it carries a
    # structure or a bound. Everything else is a plain scalar whose
    # type token says all there is to say.
    STRUCTURE_TYPES = frozenset(['object', 'array'])

    def _published_parameters(self, operation):
        """Every parameter of an operation by name, body properties included.

        Built fresh rather than aliasing the fetched specification's
        own dictionaries: the callers below read the result, and the
        day somebody caches the spec across assertions an in-place
        update here would leak into another test's subject.

        A name which appears both as a body property and as a path or
        query parameter is returned in ``collisions`` rather than
        resolved by ordering, because the two would publish different
        shapes under one name and the loser would be invisible.
        """
        published = {}
        collisions = []

        for parameter in operation.get('parameters', []):
            if parameter.get('in') != 'body':
                candidates = [(parameter['name'], dict(parameter))]
            else:
                properties = parameter.get('schema', {}).get('properties')
                if properties is None:
                    # The raw request body: bytes rather than named
                    # JSON keys, so the schema is the parameter's own
                    # shape and there is nothing to walk into.
                    candidates = [(parameter['name'],
                                   dict(parameter.get('schema', {})))]
                else:
                    candidates = [(name, dict(prop))
                                  for (name, prop) in properties.items()]

            for (name, schema) in candidates:
                if name in published:
                    collisions.append(name)
                published[name] = schema

        return published, collisions

    def test_structured_parameters_publish_their_real_shape(self):
        # Pinned against the published specification rather than
        # against the declarations, because the specification is what a
        # client generator reads and what phase 3 will compile.
        spec = self._fetch_spec()
        wrong = []

        for (path, method, name, expected) in self.STRUCTURED_PARAMETERS:
            # Reported rather than left to a KeyError: an entry naming
            # a route which has been renamed or removed should say so,
            # not die part way through the table and hide every
            # mismatch after it.
            operation = spec['paths'].get(path, {}).get(method)
            if operation is None:
                wrong.append(
                    'the specification has no %s %s, so its entry for %r is '
                    'stale' % (method, path, name))
                continue

            published, collisions = self._published_parameters(operation)
            if collisions:
                wrong.append(
                    '%s %s publishes %s under both a body property and a '
                    'non-body parameter, so one of the two shapes is '
                    'invisible' % (method, path, ', '.join(sorted(collisions))))

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
            # twice, so an entry describes the published shape in full:
            # a constraint key it does not list must not be published.
            # Every constraint key, not just minimum -- console length
            # is listed precisely to assert that nothing bounds it, and
            # a maximum narrowing it would be the same defect.
            for key in sorted(api_base.CONSTRAINT_KEYS | {'items'}):
                if key not in expected and key in published[name]:
                    wrong.append(
                        '%s %s %s publishes %s %r, which this table does '
                        'not expect -- if the handler really enforces it, '
                        'add it here'
                        % (method, path, name, key, published[name][key]))

        self.assertEqual([], wrong, '\n'.join(wrong))

    def test_every_published_structure_or_bound_is_registered(self):
        # The table above is hand-maintained, so on its own it can fall
        # behind the tree without anything noticing -- which is exactly
        # the failure it exists to prevent, one level up. This derives
        # the set which ought to be in it from the specification, so a
        # new bound or structure fails CI until somebody has read the
        # handler and written down what it really accepts.
        spec = self._fetch_spec()
        registered = {(path, method, name)
                      for (path, method, name, _) in self.STRUCTURED_PARAMETERS}

        unregistered = []
        for path, methods in spec['paths'].items():
            for method, operation in methods.items():
                if not isinstance(operation, dict):
                    continue
                published, _ = self._published_parameters(operation)
                for (name, schema) in published.items():
                    structured = schema.get('type') in self.STRUCTURE_TYPES
                    bounded = bool(set(schema) & api_base.CONSTRAINT_KEYS)
                    if not structured and not bounded:
                        continue
                    if (path, method, name) not in registered:
                        unregistered.append(
                            '%s %s %s publishes %r but is not in '
                            'STRUCTURED_PARAMETERS'
                            % (method, path, name,
                               {k: v for (k, v) in schema.items()
                                if k in api_base.CONSTRAINT_KEYS
                                or k in ('type', 'items')}))

        self.assertEqual(
            [], unregistered,
            'Parameters publishing a structure or a bound which no entry in '
            'STRUCTURED_PARAMETERS describes. Read the handler, then add one '
            'saying what it really accepts:\n' + '\n'.join(unregistered))

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
