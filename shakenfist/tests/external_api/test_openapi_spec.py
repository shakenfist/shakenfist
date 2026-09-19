# Copyright 2019 Michael Still and contributors

import json

from openapi_spec_validator import OpenAPIV2SpecValidator

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import auth as api_auth
from shakenfist.external_api import base as api_base
from shakenfist.tests import base
from shakenfist.util import network as util_network


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
        # Unstructured on purpose, and the only object in this table
        # which is: _validate_instance_metadata() is key dependent and
        # accepts any non-empty JSON value under a key of the caller's
        # choosing, so there is no properties block to write (D47).
        ('/instances', 'post', 'metadata', {'type': 'object'}),
        # A videospec. Nothing is required, because the handler
        # defaults the whole spec when it is absent
        # (external_api/instance.py:833). model carries no enum because
        # instance.py:2153 renders it into the domain XML, so the set
        # which works is the hypervisor's rather than this API's; it
        # does carry the character-class pattern which keeps the value
        # inert in that XML (issue #4242), backed at every validation
        # mode by the escaping in instance._xml_attribute_escape().
        # vdi does carry an enum because Shaken Fist itself branches on
        # the value in three places (instance.py:1715, instance.py:2161
        # and libvirt.tmpl:156) and nothing else refuses a value
        # outside it. memory is typed integer and deliberately
        # unbounded: no handler refuses a zero or a negative one.
        ('/instances', 'post', 'video',
         {'type': 'object',
          'additionalProperties': False,
          'properties': {
              'model': {'type': 'string',
                        'pattern': api_base.DEVICE_MODEL_PATTERN},
              'memory': {'type': 'integer', 'format': 'int64'},
              'vdi': {'type': 'string',
                      'enum': ['vnc', 'spice', 'spiceconcurrent',
                               'spicedebug']},
          }}),
        # A list of diskspecs. size's floor is 0 rather than 1 because
        # a sizeless or zero sized disk means "the size of the base
        # image" to scheduler.py:471, scheduler.py:577,
        # mariadb.disk_spec_virtual_gb and util_image.create_cow alike,
        # while a negative one corrupts the capacity ledger. bus
        # publishes the keys of the bases dictionary in
        # instance._get_disk_device(), which is a refusal the handler
        # already makes; type publishes the deliberate narrowing of
        # D50, since only 'cdrom' is special cased and every other
        # value is handed to libvirt as a device name. base carries no
        # format because it is prefix dispatched and may be a URL, an
        # sf:// reference, a label: reference or a bare artifact name.
        ('/instances', 'post', 'disk',
         {'type': 'array',
          'items': {
              'type': 'object',
              'additionalProperties': False,
              'properties': {
                  'size': {'type': 'integer', 'format': 'int64',
                           'minimum': 0},
                  'base': {'type': 'string'},
                  'bus': {'type': 'string',
                          'enum': ['sata', 'scsi', 'usb', 'virtio', 'nvme']},
                  'type': {'type': 'string', 'enum': ['disk', 'cdrom']},
              }}}),
        # A list of networkspecs, the same shape the interface hotplug
        # endpoint below takes one of. network_uuid is the only
        # required key in this whole vocabulary because
        # _netdesc_safety_checks() already refuses a netdesc without
        # one -- and it is typed 'string' with no uuid format because
        # the handler passes it to Network.from_db_by_ref(), which
        # resolves a network *name* as readily as a UUID. macaddress
        # publishes util_network's one MAC pattern, which
        # valid_macaddr() enforces at instance.py:347. address carries
        # no ipv4 format because the literal string 'none' is a
        # documented value meaning "no address on this interface".
        # model carries no enum for the reason video's model does not,
        # and the same inert-in-the-XML pattern (issue #4242).
        ('/instances', 'post', 'network',
         {'type': 'array',
          'items': {
              'type': 'object',
              'additionalProperties': False,
              'required': ['network_uuid'],
              'properties': {
                  'network_uuid': {'type': 'string'},
                  'macaddress': {'type': 'string',
                                 'format': 'a MAC address',
                                 'pattern': util_network.MACADDR_PATTERN},
                  'address': {'type': 'string'},
                  'model': {'type': 'string',
                            'pattern': api_base.DEVICE_MODEL_PATTERN},
                  'float': {'type': 'boolean'},
              }}}),
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
        # One networkspec rather than a list of them, and asserted
        # separately from the create endpoint's array precisely because
        # a table entry is the audit: the two render from one Python
        # constant, and these two entries are what would fail if they
        # ever stopped doing so.
        ('/instances/{instance_ref}/interfaces', 'post', 'network',
         {'type': 'object',
          'additionalProperties': False,
          'required': ['network_uuid'],
          'properties': {
              'network_uuid': {'type': 'string'},
              'macaddress': {'type': 'string',
                             'format': 'a MAC address',
                             'pattern': util_network.MACADDR_PATTERN},
              'address': {'type': 'string'},
              'model': {'type': 'string',
                        'pattern': api_base.DEVICE_MODEL_PATTERN},
              'float': {'type': 'boolean'},
          }}),
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
        # A namespace name being created, as opposed to one being
        # resolved: every other namespace parameter is answered 404 by
        # a database lookup, but on create nothing resolves anything,
        # and the name is rendered verbatim into dnsmasq's conf-file on
        # the network node (issue #4250). The pattern is backed at
        # every validation mode by the handler guard in
        # AuthNamespacesEndpoint.post().
        ('/auth/namespaces', 'post', 'namespace',
         {'type': 'string', 'pattern': api_auth.NAMESPACE_NAME_PATTERN}),
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
        # The fourteen metadata `value` declarations (D15): 'any'
        # renders with no 'type' key at all, so a typeless schema is
        # exactly as much this table's business as an object or an
        # array -- see the typeless branch of
        # test_every_published_structure_or_bound_is_registered.
        # Do not add network.py's DNS record `value` here: it is a
        # different parameter, declared 'ipv4', and correctly typed.
        ('/artifacts/{artifact_ref}/metadata', 'post', 'value',
         {'format': 'any JSON value'}),
        ('/artifacts/{artifact_ref}/metadata/{key}', 'put', 'value',
         {'format': 'any JSON value'}),
        ('/auth/namespaces/{namespace}/metadata', 'post', 'value',
         {'format': 'any JSON value'}),
        ('/auth/namespaces/{namespace}/metadata/{key}', 'put', 'value',
         {'format': 'any JSON value'}),
        ('/blobs/{blob_uuid}/metadata', 'post', 'value',
         {'format': 'any JSON value'}),
        ('/blobs/{blob_uuid}/metadata/{key}', 'put', 'value',
         {'format': 'any JSON value'}),
        ('/instances/{instance_ref}/metadata', 'post', 'value',
         {'format': 'any JSON value'}),
        ('/instances/{instance_ref}/metadata/{key}', 'put', 'value',
         {'format': 'any JSON value'}),
        ('/interfaces/{interface_uuid}/metadata', 'post', 'value',
         {'format': 'any JSON value'}),
        ('/interfaces/{interface_uuid}/metadata/{key}', 'put', 'value',
         {'format': 'any JSON value'}),
        ('/networks/{network_ref}/metadata', 'post', 'value',
         {'format': 'any JSON value'}),
        ('/networks/{network_ref}/metadata/{key}', 'put', 'value',
         {'format': 'any JSON value'}),
        ('/nodes/{node}/metadata', 'post', 'value',
         {'format': 'any JSON value'}),
        ('/nodes/{node}/metadata/{key}', 'put', 'value',
         {'format': 'any JSON value'}),
    ]

    # A published parameter is this table's business if it carries a
    # structure or a bound. Everything else is a plain scalar whose
    # type token says all there is to say. A schema with no 'type' key
    # at all counts too: 'any' (D15) is the one token that renders
    # this way, and it constrains nothing, so a declaration changing to
    # it must be exactly as visible here as one gaining an object or an
    # array -- see the typeless check in
    # test_every_published_structure_or_bound_is_registered().
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

    @staticmethod
    def _without_descriptions(schema):
        """The published schema with every 'description' removed, recursively.

        An entry above describes the shape it expects in full, because
        a constraint nobody asked for is the failure mode this table
        exists to catch. Prose is the one part of a published schema
        that is not a contract, and the structured specs carry a
        paragraph of it on every property, so comparing it would put
        those paragraphs in two files and would fail this test on a
        wording fix. Everything else -- type, format, enum, minimum,
        pattern, required, additionalProperties, the property names
        themselves -- is compared exactly.
        """
        if isinstance(schema, dict):
            return {
                key: OpenAPISpecificationTestCase._without_descriptions(value)
                for (key, value) in schema.items() if key != 'description'
            }
        if isinstance(schema, list):
            return [OpenAPISpecificationTestCase._without_descriptions(value)
                    for value in schema]
        return schema

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

            published_schema = self._without_descriptions(published[name])
            for (key, value) in expected.items():
                if published_schema.get(key) != value:
                    wrong.append(
                        '%s %s %s: %s is %r, expected %r'
                        % (method, path, name, key,
                           published_schema.get(key), value))

            # A bound nobody asked for is the failure mode that shipped
            # twice, so an entry describes the published shape in full:
            # a constraint key it does not list must not be published.
            # Every constraint key, not just minimum -- console length
            # is listed precisely to assert that nothing bounds it, and
            # a maximum narrowing it would be the same defect.
            for key in sorted(api_base.CONSTRAINT_KEYS | {'items'}):
                if key not in expected and key in published_schema:
                    wrong.append(
                        '%s %s %s publishes %s %r, which this table does '
                        'not expect -- if the handler really enforces it, '
                        'add it here'
                        % (method, path, name, key, published_schema[key]))

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
                    # A schema with no 'type' key at all is 'any'
                    # (D15): unconstrained rather than structured in
                    # the object/array sense, but exactly as wide, so
                    # it must register here too -- otherwise the
                    # widest token in the vocabulary is the one type
                    # change this table would never catch.
                    structured = ('type' not in schema
                                  or schema.get('type') in self.STRUCTURE_TYPES)
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
                                or k in ('type', 'items', 'format')}))

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

    def test_post_instances_507_describes_the_transient_contract(self):
        # Phase 4 of PLAN-transient-capacity-refusals-phase-04-retry-after
        # (D32): the three-tuple response declaration format has no way
        # to express a response header or a body schema, so the
        # ``Retry-After`` header and the ``stage``/``transient`` body
        # fields are published in prose rather than structurally. This
        # is what keeps that prose from silently drifting away from the
        # helper it describes.
        spec = self._fetch_spec()
        response = spec['paths']['/instances']['post']['responses']['507']

        description = response['description']
        self.assertIn('Retry-After', description)
        self.assertIn('transient', description)

        # The sample published alongside it is a real body, not None,
        # so the shape is discoverable machine-readably even though the
        # header is not.
        example = response['examples']['application/json']
        decoded = example if isinstance(example, dict) else json.loads(example)
        self.assertEqual(
            {'error', 'status', 'stage', 'transient'}, set(decoded))
        self.assertIs(True, decoded['transient'])
