import json
import re
import sys
import traceback

import flask
import flask_restful
from flask_jwt_extended import decode_token
from flask_jwt_extended import get_jwt
from flask_jwt_extended import unset_jwt_cookies
from flask_jwt_extended import verify_jwt_in_request
from flask_jwt_extended.exceptions import CSRFError
from flask_jwt_extended.exceptions import FreshTokenRequired
from flask_jwt_extended.exceptions import InvalidHeaderError
from flask_jwt_extended.exceptions import JWTDecodeError
from flask_jwt_extended.exceptions import NoAuthorizationError
from flask_jwt_extended.exceptions import RevokedTokenError
from flask_jwt_extended.exceptions import WrongTokenError
from jwt.exceptions import PyJWTError
from jwt.exceptions import DecodeError
from jwt.exceptions import ExpiredSignatureError
import requests
from webargs.flaskparser import parser as webargs_parser
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import scopes as api_scopes
from shakenfist.instance import Instance
from shakenfist.namespace import get_api_token
from shakenfist.node import Node
from shakenfist.namespace import Namespace
from shakenfist.upload import Upload
from shakenfist.util.access_tokens import parse_jwt_identity
from shakenfist.util.access_tokens import request_namespace
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


# Unauthenticated paths hit by load balancer / orchestrator health probes.
# These short-circuit audit logging and are downgraded from INFO to DEBUG so
# that frequent probes do not swamp the logs or the eventlog server. Kept here
# (rather than in app.py) because base.py is imported by app.py, avoiding a
# circular import while letting both modules share the single tuple.
HEALTH_PROBE_PATHS = ('/', '/livez', '/readyz', '/healthz')

REDACTED_BODY = '...body not logged as this route handles credentials...'


# Request and response bodies are logged verbatim by app.py, and the
# parsed body is logged again as kwargs by log_request below. That is
# useful for debugging and unacceptable for the routes which carry
# credentials: POST /auth is sent a plaintext namespace key and answers
# with a JWT, the key management routes are sent key secrets, and
# POST /auth/federated is sent a third party identity token which is a
# bearer credential until it expires. Every such route lives under
# /auth, so bodies there are not logged at all. Redacting by field name
# instead was rejected because "key" means a metadata key name on most
# endpoints and a secret on only a few, so the check would have to know
# which route it was on anyway -- and would silently start leaking the
# day somebody adds a route it had not heard of, which is exactly how
# the federated exchange came to log its tokens.
#
# The URL is still logged, so an audit reader keeps the namespace and
# the key name. Only the credential itself is lost. Kept here alongside
# HEALTH_PROBE_PATHS so app.py and base.py cannot disagree about which
# routes are sensitive.
def handles_credentials():
    path = flask.request.path
    return path == '/auth' or path.startswith('/auth/')


# The parameter locations OpenAPI 2.0 defines. swagger_helper()
# rejects anything else, so a mistyped location is an import-time
# failure rather than a declaration silently ignored by the docs
# generator and wrong in the published API.
SWAGGER_PARAMETER_LOCATIONS = frozenset(
    ['query', 'header', 'path', 'formData', 'body'])


# The name an endpoint uses to declare that it consumes the raw
# request body rather than a named parameter, as
# ``(RAW_BODY_PARAMETER, 'body', 'binary', ...)``. It never appears
# in a handler signature -- the handler reads flask.request directly
# -- so schema compilation and the declaration audit both skip it.
# OpenAPI 2.0 permits at most one body parameter and conventionally
# names it 'body'.
RAW_BODY_PARAMETER = 'body'


# The shipped client serialises every request -- including GETs -- to a
# JSON body and never builds a query string, but webargs binds each
# schema to a single request location, so a schema bound to 'query'
# silently discards body-supplied values (issue 3629: the schema's
# load_default overwrote the body value log_request had merged into
# kwargs). This loader accepts a parameter from either place, named
# after webargs' built-in 'json_or_form'. Per decision D6 of
# docs/plans/PLAN-api-input-validation.md the JSON body is authoritative
# when a key arrives in both. Keys the schema does not name are dropped
# to match the unknown=EXCLUDE default webargs applies to the query
# location; without that, a stray query key would 422 the request. Not
# registered under a ('query', 'json') tuple even though webargs permits
# one: validation failures report as {location: messages} and a tuple
# key turns the 422 into a 500 when flask_restful JSON-serialises it.
# declarations.py derives a schema bound here as query parameters, so
# the published declarations stay 'query'.
@webargs_parser.location_loader('json_or_query')
def _load_json_or_query(req, schema):
    data = req.args.to_dict()
    json_body = req.get_json(force=True, silent=True)
    if isinstance(json_body, dict):
        data.update(json_body)
    return {key: value for key, value in data.items() if key in schema.fields}


def caller_is_admin(func):
    # Ensure only users in the 'system' namespace can call this method
    def wrapper(*args, **kwargs):
        if request_namespace() != 'system':
            return sf_api.error(401, 'unauthorized')

        # Being in the system namespace is necessary but no longer
        # sufficient. Without this a key scoped to, say, blob.read but
        # minted into system would reach every administrative endpoint
        # -- a scoped credential escalating to cluster administration.
        # Legacy unscoped keys carry the wildcard and are unaffected,
        # so existing admin automation keeps working.
        held = get_jwt().get('scopes')
        if not api_scopes.satisfies(held, api_scopes.ADMIN):
            LOG.with_fields({'held': held}).info(
                'Administrative request denied: token lacks the admin scope')
            return sf_api.error(
                403, 'token is not scoped for administrative operations')

        return func(*args, **kwargs)
    return wrapper


def caller_scopes():
    """The scopes the requesting token holds, or None for wildcard.

    None means "unrestricted" here, covering both a legacy token with
    no scopes claim and one explicitly carrying the wildcard. Callers
    deriving a new credential's scopes from the caller's should treat
    None as "no restriction to inherit".
    """
    held = get_jwt().get('scopes')
    if held is None or api_scopes.WILDCARD in held:
        return None
    return list(held)


def resolve_lookup_namespace(body_namespace, kind):
    # Decide which namespace a `*_ref` lookup should be scoped to.
    #
    # Why: `from_db_by_ref(name, namespace='system')` is a documented
    # "search everywhere" mode used for unqualified system lookups. The
    # decorators historically passed `request_namespace()` straight in,
    # which meant a system caller asking for a specific namespace via
    # the request body silently got cross-namespace resolution and
    # could find a same-named object in an unrelated namespace.
    #
    # Returns (lookup_namespace, error_response_or_None). A non-system
    # caller may not query a namespace other than their own — match
    # the 404 posture used by the requires_*_ownership decorators
    # rather than 403 to avoid leaking which namespaces exist.
    caller_ns = request_namespace()
    if body_namespace:
        if caller_ns != 'system' and body_namespace != caller_ns:
            return None, sf_api.error(404, f'{kind} not found')
        return body_namespace, None
    return caller_ns, None


# The keys a declaration's optional sixth element may carry. All three
# are valid Swagger 2.0 parameter keywords and valid JSON Schema, so a
# constraint renders into the published OpenAPI rather than living only
# in code -- which is the property that made the events limit cap
# invisible to callers for years.
CONSTRAINT_KEYS = frozenset(['minimum', 'maximum', 'pattern'])


# Type MUST be one of "string", "number", "integer", "boolean", "array" or "file".
ARGTYPES = {
    # Real array types rather than prose-formatted strings: body
    # parameters render through schema objects, where array is
    # legal JSON Schema. Every use in the tree is body-located, and
    # swagger_helper() refuses these outside a body at import time.
    'arrayofdict': {'type': 'array', 'items': {'type': 'object'}},
    'arrayofstring': {'type': 'array', 'items': {'type': 'string'}},
    # byte is Swagger 2.0's standard format token for base64
    # encoded content.
    'base64': {'type': 'string', 'format': 'byte'},
    'bearer': {'type': 'string', 'format': 'Bearer ...JWT...'},
    'binary': {'type': 'string', 'format': 'Binary data'},
    'boolean': {'type': 'boolean', 'format': 'boolean'},
    # Object valued parameters render as real objects for the
    # same reason the array tokens do: a body parameter's
    # schema is JSON Schema, where object is legal. As prose
    # formatted strings these described video, bound_claims
    # and instance metadata as strings while their neighbours
    # in the same request body were structures.
    'dict': {'type': 'object'},
    # The prose formats on the string types carry description-like
    # information a generator passes through; integer has standard
    # formats, and these are byte offsets and blob sizes, so int64.
    'integer': {'type': 'integer', 'format': 'int64'},
    'ipv4': {'type': 'string', 'format': 'an IPv4 address as a string'},
    'macaddr': {
        'type': 'string', 'format': 'a MAC address',
        'pattern': '^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$'},
    'namespace': {'type': 'string', 'format': 'the name of a namespace'},
    # Deliberately format-only, with no pattern. An IPv4 CIDR
    # pattern would describe the API as narrower than it is:
    # NetworksEndpoint.post() validates with ipaddress.ip_network(),
    # which parses IPv6 too. It would also be loose enough to admit
    # 999.999.999.999/99, so it earns nothing as validation while
    # setting phase 4 up to compile a documentation commit into a
    # 400 for input the API accepts today. ip_network() stays the
    # single source of truth for what parses.
    'netblock': {'type': 'string', 'format': 'a CIDR netblock'},
    'node': {'type': 'string', 'format': 'the name of a node'},
    'number': {'type': 'number', 'format': 'a floating point number'},
    'string': {'type': 'string', 'format': 'string'},
    # Negative values here are at best meaningless and at worst
    # silently destructive: a negative artifact max_versions
    # deletes the oldest version on every index add, because
    # delete_old_versions() slices [:-max].
    'unsignedinteger': {
        'type': 'integer', 'format': 'int64', 'minimum': 0},
    'url': {'type': 'string', 'format': 'url'},
    'uuid': {'type': 'string', 'format': 'uuid'},
    'uuidorname': {
        'type': 'string',
        'format': 'either a valid UUID or the unique name of an object'
        }
}


def _validated_constraints(section, name, rendered, constraints):
    """Check a declaration's constraints element, InvalidAPIDeclaration
    on any defect, so phase 3's compiler catches one exception type."""
    if not isinstance(constraints, dict):
        raise exceptions.InvalidAPIDeclaration(
            '%s parameter %s declares constraints which are not a '
            'dictionary: %r' % (section, name, constraints))

    unknown = set(constraints) - CONSTRAINT_KEYS
    if unknown:
        raise exceptions.InvalidAPIDeclaration(
            '%s parameter %s declares unknown constraint keys %s; the '
            'known keys are %s'
            % (section, name, ', '.join(sorted(unknown)),
               ', '.join(sorted(CONSTRAINT_KEYS))))

    # A constraint restating a key the type token already renders (a
    # second minimum on unsignedinteger) is a contradiction waiting for
    # the two values to disagree, so it is rejected rather than merged
    # silently.
    overlap = set(constraints) & set(rendered)
    if overlap:
        raise exceptions.InvalidAPIDeclaration(
            '%s parameter %s constrains %s, which its type already '
            'defines' % (section, name, ', '.join(sorted(overlap))))

    for key in ('minimum', 'maximum'):
        if key not in constraints:
            continue
        value = constraints[key]
        # bool is excluded explicitly because it subclasses int, and
        # minimum=True is a typo rather than a bound.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares %s=%r, which is not a number'
                % (section, name, key, value))
        if rendered.get('type') not in ('integer', 'number'):
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares %s on type %r, which is not '
                'numeric' % (section, name, key, rendered.get('type')))
        # JSON Schema would read minimum=1.5 on an integer as "at least
        # 2", but nobody means that on purpose. Refused for the same
        # reason as minimum=True above: the typo-shaped input is worth
        # more as an import-time error than as a valid-but-surprising
        # bound.
        if rendered['type'] == 'integer' and not isinstance(value, int):
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares %s=%r on an integer type; a '
                'fractional bound on an integer is a typo'
                % (section, name, key, value))

    if ('minimum' in constraints and 'maximum' in constraints
            and constraints['minimum'] > constraints['maximum']):
        raise exceptions.InvalidAPIDeclaration(
            '%s parameter %s declares minimum %r greater than maximum %r'
            % (section, name, constraints['minimum'],
               constraints['maximum']))

    if 'pattern' in constraints:
        pattern = constraints['pattern']
        if rendered.get('type') != 'string':
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares a pattern on type %r, which '
                'is not a string' % (section, name, rendered.get('type')))
        if not isinstance(pattern, str):
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares a pattern which is not a '
                'string: %r' % (section, name, pattern))
        try:
            re.compile(pattern)
        except re.error as e:
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares a pattern which does not '
                'compile: %r (%s)' % (section, name, pattern, e))

    return constraints


# https://swagger.io/specification/v2/ defines the schema for this dictionary
def swagger_helper(section, description, parameters, responses,
                   requires_admin=False, requires_auth=True):
    out = {
        'tags': [section],
        'parameters': [],
        'consumes': [
            'application/json'
        ],
        'produces': [
            'application/json'
        ],
        'deprecated': False,
        'description': description,
        'responses': {}
    }

    if requires_auth:
        out['parameters'].append({
            'name': 'Authorization',
            'in': 'header',
            'required': True,
            'description': 'JWT authorization header'
        })
        out['parameters'][-1].update(ARGTYPES['bearer'])
        # The security requirement and the Authorization parameter
        # travel together: an operation which does not demand the
        # header must not publish the requirement either. This used to
        # be emitted unconditionally, which described /auth/federated
        # -- the one deliberately unauthenticated endpoint, where the
        # identity token is the credential -- as requiring a bearer
        # token. A list, not a bare object: OpenAPI 2.0 defines
        # security as an array of requirement objects, and the object
        # form was the largest single class of specification-validity
        # errors.
        out['security'] = [{
            'bearerAuth': []
        }]

    declarable = set(ARGTYPES) - {'bearer'}

    # Swagger 2.0 permits at most one in: body parameter per operation,
    # and it must carry a schema rather than type/format. Declarations
    # stay one tuple per parameter -- that is the shape the audit reads
    # and phase 3 will compile -- but body-located declarations render
    # as properties of a single generated schema instead of as
    # parameters of their own, which used to be the largest class of
    # specification-validity errors (128 at its peak).
    body_properties = {}
    body_required = []
    raw_body = None

    for parameter in parameters:
        # Checked explicitly, and first, because every malformed
        # declaration has to arrive as an InvalidAPIDeclaration for
        # phase 3's compiler to catch one exception type. Destructuring
        # raises ValueError on a wrong-arity tuple and len() raises
        # TypeError on anything unsized, so neither is left to happen
        # by itself.
        if not isinstance(parameter, (tuple, list)) or len(parameter) not in (5, 6):
            raise exceptions.InvalidAPIDeclaration(
                '%s declares a parameter which is not a (name, location, '
                'type, description, required[, constraints]) tuple of five '
                'or six elements: %r' % (section, parameter))
        (name, location, argtype, argdescription, argrequired) = parameter[:5]

        # The location was never validated, so 'post' and 'qeury' both
        # survived in the tree until they were found by audit. Fail at
        # import time instead: these declarations are the input to
        # request validation, not just documentation.
        if location not in SWAGGER_PARAMETER_LOCATIONS:
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares location %r, which is not one of %s'
                % (section, name, location,
                   ', '.join(sorted(SWAGGER_PARAMETER_LOCATIONS))))

        # OpenAPI 2.0 requires that a path parameter be required, because
        # the route cannot match without it. A specification saying
        # otherwise fails validation in linters and client generators,
        # which are the readers this exists for.
        if location == 'path' and argrequired is not True:
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s is in the path, so it must be required'
                % (section, name))

        # An unknown type token used to surface as a bare KeyError from
        # the argtypes lookup below, naming the token but neither the
        # endpoint nor the parameter it came from. Report it the way a
        # bad location is reported, so every malformed declaration
        # raises one exception type that phase 3's compiler can catch
        # uniformly. 'bearer' describes the Authorization header this
        # function injects itself, so it is not a token an endpoint may
        # declare.
        if argtype not in declarable:
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares type %r, which is not one of %s'
                % (section, name, argtype, ', '.join(sorted(declarable))))

        # What this parameter renders as: the type token's keys, plus
        # any constraints from the optional sixth tuple element.
        # Validated whatever the location, and merged here rather than
        # in each branch below, so a constraint cannot be silently
        # dropped by the path it happens to render through.
        rendered = dict(ARGTYPES[argtype])
        if len(parameter) == 6:
            rendered.update(_validated_constraints(
                section, name, rendered, parameter[5]))

        if location != 'body':
            # Outside a body a parameter must be primitive: there is
            # no schema object to nest a structure in, so an object or
            # an array of objects on a query or path parameter renders
            # an invalid specification. test_openapi_spec.py would
            # catch it, but every other declaration defect is refused
            # at import time so sf-api does not start at all, and a
            # tree which fails CI should not be able to serve an
            # invalid specification in the meantime.
            if (rendered.get('type') == 'object'
                    or rendered.get('items', {}).get('type') == 'object'):
                raise exceptions.InvalidAPIDeclaration(
                    '%s parameter %s declares type %r in the %s, but an '
                    'object can only be declared in the body'
                    % (section, name, argtype, location))

            out['parameters'].append({
                'name': name,
                'in': location,
                'required': argrequired,
                'description': argdescription
            })
            out['parameters'][-1].update(rendered)
            continue

        # The raw request body marker: the handler reads bytes from
        # flask.request rather than receiving named JSON keys. A
        # parameter which merely happens to be called 'body' but has a
        # non-binary type is not the marker; it becomes a schema
        # property like any other named parameter, and there is no
        # collision because the generated wrapper's name lives at
        # parameter level while properties live inside the schema.
        if name == RAW_BODY_PARAMETER and argtype == 'binary':
            raw_body = (argdescription, argrequired, rendered)
            continue

        prop = dict(rendered)
        prop['description'] = argdescription
        body_properties[name] = prop
        if argrequired:
            body_required.append(name)

    if raw_body is not None and body_properties:
        raise exceptions.InvalidAPIDeclaration(
            '%s declares both the raw request body and named body '
            'parameters (%s); raw bytes and JSON keys cannot share a '
            'request body' % (section, ', '.join(sorted(body_properties))))

    if raw_body is not None:
        (argdescription, argrequired, rendered) = raw_body
        out['parameters'].append({
            'name': RAW_BODY_PARAMETER,
            'in': 'body',
            'required': argrequired,
            'description': argdescription,
            'schema': rendered
        })
    elif body_properties:
        schema = {
            'type': 'object',
            'properties': body_properties
        }
        # In a schema object 'required' is a JSON Schema array of
        # property names -- a different thing from the parameter-level
        # boolean -- and an empty array is itself invalid, so it is
        # only present when something is required.
        if body_required:
            schema['required'] = body_required
        out['parameters'].append({
            'name': RAW_BODY_PARAMETER,
            'in': 'body',
            'required': bool(body_required),
            'description': 'The JSON request body.',
            'schema': schema
        })

    if requires_auth:
        responses.append((
            401,
            'You must authenticate. See '
            'https://shakenfist.com/developer_guide/authentication/ for details.',
            None))
    for (httpcode, respdescription, sample) in responses:
        out['responses'][httpcode] = {
            'description': respdescription
        }
        if sample:
            out['responses'][httpcode]['examples'] = {
                'application/json': sample
            }

    access_notes = []
    if requires_admin:
        access_notes.append(
            'Requires authentication as a member of the system namespace.')

    if access_notes:
        out['description'] += \
            '<br/><br/><i>%s</i>' % '<br/>'.join(access_notes)

    return out


def verify_token(func):
    def wrapper(*args, **kwargs):
        # Ensure there is a valid JWT with a correct signature
        _, jwt_data = verify_jwt_in_request(
            False, False, False, ['headers'], True)

        # Perform SF specific safety checks
        try:
            ns_name, key_name = parse_jwt_identity()
        except (TypeError, ValueError):
            LOG.error('JWT token does not contain a namespace and key name in '
                      'the subject field')
            raise NoAuthorizationError()

        ns = Namespace.from_db(ns_name)
        if not ns:
            LOG.with_fields({'namespace', ns_name}).error(
                'JWT token is for non-existent namespace')
            raise NoAuthorizationError()
        if ns.state.value == dbo.STATE_DELETED:
            LOG.with_fields({'namespace', ns_name}).error(
                'JWT token is for deleted namespace')
            raise NoAuthorizationError()

        # NOTE(mikal): the exact name '_service_key' skips the nonce
        # check entirely. This predates nonced keys and some deployment
        # may still be holding a token of that shape, so it is
        # deliberately preserved here (phase 2 Decision 1 of the auth
        # federation plan); its retirement is recorded as phase 3 work.
        # Note that this is an exact match, not a prefix match -- the
        # '_service_key_<rand>' keys get_api_token() mints are checked
        # like any other key.
        if key_name != '_service_key':
            # One indexed point read of the named key, honouring
            # expiry. This must never be cached: the nonce is the
            # revocation handle for every token minted from the key, so
            # a stale cache would delay revocation, which is the entire
            # point of the mechanism.
            key = ns.lookup_key(key_name)
            if not key:
                LOG.with_fields({'namespace', ns_name}).error(
                    'JWT token uses non-existent key')
                raise NoAuthorizationError()

            nonce = key.nonce
            if 'nonce' not in jwt_data:
                LOG.with_fields({'namespace', ns_name}).error(
                    'JWT token lacks nonce')
                raise NoAuthorizationError()
            if jwt_data['nonce'] != nonce:
                LOG.with_fields({'namespace', ns_name}).error(
                    'JWT token has incorrect nonce')
                raise NoAuthorizationError()

        return func(*args, **kwargs)
    return wrapper


def log_token_use(func):
    def wrapper(*args, **kwargs):
        namespace, keyname = parse_jwt_identity()

        ns = Namespace.from_db(namespace)
        if not ns:
            return sf_api.error(401, 'authenticated namespace not known')

        # NOTE(mikal): the presented token must never appear in the
        # event. The key name identifies which credential was used,
        # which is what an audit reader actually needs; the token
        # itself would be replayable by anyone who can read the
        # namespace's events.
        ns.add_event(
            EVENT_TYPE_AUDIT, 'token used to authenticate request',
            extra={
                'keyname': keyname,
                'method': flask.request.environ['REQUEST_METHOD'],
                'path': flask.request.environ['PATH_INFO'],
                'remote-address': flask.request.remote_addr
            })

        return func(*args, **kwargs)
    return wrapper


def arg_is_instance_ref(func):
    def wrapper(*args, **kwargs):
        body_namespace = kwargs.pop('namespace', None)
        lookup_namespace, err = resolve_lookup_namespace(
            body_namespace, 'instance')
        if err:
            return err

        try:
            inst = Instance.from_db_by_ref(
                kwargs.get('instance_ref'), lookup_namespace)
        except exceptions.MultipleObjects as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        if not inst:
            LOG.with_fields({'instance': kwargs.get('instance_ref')}).info(
                'Instance not found, missing or deleted')
            return sf_api.error(404, 'instance not found')

        # UUID lookups bypass from_db_by_ref's namespace filter; if the
        # caller explicitly named a namespace, the resolved object must
        # live there or we reject it.
        if body_namespace and inst.namespace != body_namespace:
            LOG.with_fields({
                'instance': inst,
                'requested_namespace': body_namespace,
            }).info('Instance not in requested namespace')
            return sf_api.error(404, 'instance not found')

        kwargs['instance_from_db'] = inst
        return func(*args, **kwargs)
    return wrapper


def redirect_instance_request(func):
    # Redirect method to the hypervisor hosting the instance
    def wrapper(*args, **kwargs):
        i = kwargs.get('instance_from_db')
        if not i:
            return

        placement = i.placement
        if not placement:
            return
        if not placement.get('node'):
            return

        if not config.NODE_UUID:
            LOG.warning(
                'NODE_UUID is not set, cannot determine if '
                'request should be proxied')
            return sf_api.error(
                503, 'node UUID not resolved, cannot route request')

        if placement.get('node') != config.NODE_UUID:
            target_node = Node.from_db(placement['node'])
            if not target_node:
                return sf_api.error(404, 'placement node not found')
            target_ip = target_node.ip
            path = flask.request.environ['PATH_INFO']
            url = f'http://{target_ip}:13000{path}'
            api_token = get_api_token(
                f'http://{target_ip}:13000',
                namespace=request_namespace())
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=json.dumps(sf_api.flask_get_post_body()),
                headers={
                    'Authorization': api_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.with_fields({
                'method': flask.request.environ['REQUEST_METHOD'],
                'url': url,
                'status_code': r.status_code,
                'body_bytes': len(r.content)
            }).info('Returning proxied request')
            resp = flask.Response(
                r.content,
                mimetype=r.headers.get('Content-Type', 'application/json'))
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def requires_instance_ownership(func):
    # Requires that @arg_is_instance_ref has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('instance_from_db'):
            LOG.with_fields({'instance': kwargs['instance_ref']}).info(
                'Instance not found, kwarg missing')
            return sf_api.error(404, 'instance not found')

        i = kwargs['instance_from_db']
        if request_namespace() not in [i.namespace, 'system']:
            LOG.with_fields({'instance': i}).info(
                'Instance not found, ownership test in decorator')
            return sf_api.error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def requires_instance_active(func):
    # Requires that @arg_is_instance_ref has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('instance_from_db'):
            LOG.with_fields({'instance': kwargs['instance_ref']}).info(
                'Instance not found, kwarg missing')
            return sf_api.error(404, 'instance not found')

        i = kwargs['instance_from_db']
        if i.state.value != Instance.STATE_CREATED:
            LOG.with_fields({'instance': i}).info(
                'Instance not ready (%s)' % i.state.value)
            return sf_api.error(406, f'instance {i.uuid} is not ready ({i.state.value})')

        return func(*args, **kwargs)
    return wrapper


def arg_is_network_ref(func):
    # Method uses the network from the db
    def wrapper(*args, **kwargs):
        body_namespace = kwargs.pop('namespace', None)
        lookup_namespace, err = resolve_lookup_namespace(
            body_namespace, 'network')
        if err:
            return err

        try:
            n = network.Network.from_db_by_ref(
                kwargs.get('network_ref'), lookup_namespace)
        except exceptions.MultipleObjects as e:
            return sf_api.error(400, str(e), suppress_traceback=True)

        if not n:
            LOG.with_fields({'network': kwargs.get('network_ref')}).info(
                'Network not found, missing or deleted')
            return sf_api.error(404, 'network not found')

        # UUID lookups bypass from_db_by_ref's namespace filter; if the
        # caller explicitly named a namespace, the resolved object must
        # live there or we reject it. The floating network has
        # namespace=None and is therefore never "in" any namespace from
        # this check's perspective — admins must omit `namespace` to
        # reach it.
        if body_namespace and n.namespace != body_namespace:
            LOG.with_fields({
                'network': n,
                'requested_namespace': body_namespace,
            }).info('Network not in requested namespace')
            return sf_api.error(404, 'network not found')

        kwargs['network_from_db'] = n
        return func(*args, **kwargs)
    return wrapper


def redirect_to_network_node(func):
    # Redirect method to the network node
    def wrapper(*args, **kwargs):
        if not config.NODE_IS_NETWORK_NODE:
            path = flask.request.environ['PATH_INFO']
            admin_token = get_api_token(
                f'http://{config.NETWORK_NODE_IP}:13000', namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                f'http://{config.NETWORK_NODE_IP}:13000{path}',
                data=flask.request.data,
                headers={
                    'Authorization': admin_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.with_fields({
                'method': flask.request.environ['REQUEST_METHOD'],
                'url': path,
                'status_code': r.status_code,
                'body_bytes': len(r.content)
            }).info('Returning proxied request')
            resp = flask.Response(
                r.content,
                mimetype=r.headers.get('Content-Type', 'application/json'))
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def requires_network_ownership(func):
    # Requires that @arg_is_network_ref has already run
    def wrapper(*args, **kwargs):
        log = LOG.with_fields({'network': kwargs['network_ref']})

        if not kwargs.get('network_from_db'):
            log.info('Network not found, kwarg missing')
            return sf_api.error(404, 'network not found')

        if request_namespace() not in [kwargs['network_from_db'].namespace, 'system']:
            log.info('Network not found, ownership test in decorator')
            return sf_api.error(404, 'network not found')

        return func(*args, **kwargs)
    return wrapper


def requires_network_active(func):
    # Requires that @arg_is_network_ref has already run
    def wrapper(*args, **kwargs):
        log = LOG.with_fields({'network': kwargs['network_ref']})

        if not kwargs.get('network_from_db'):
            log.info('Network not found, kwarg missing')
            return sf_api.error(404, 'network not found')

        state = kwargs['network_from_db'].state
        if state.value != dbo.STATE_CREATED:
            log.info('Network not ready (%s)' % state.value)
            return sf_api.error(406,
                                'network %s is not ready (%s)'
                                % (kwargs['network_from_db'].uuid, state.value))

        return func(*args, **kwargs)
    return wrapper


def requires_namespace_exist_if_specified(func):
    def wrapper(*args, **kwargs):
        if kwargs.get('namespace'):
            if not Namespace.from_db(kwargs['namespace']):
                LOG.with_fields({'namespace': kwargs['namespace']}).warning(
                    'Attempt to use non-existent namespace')
                return sf_api.error(404, 'namespace not found')

        return func(*args, **kwargs)
    return wrapper


def arg_is_upload_uuid(func):
    # Method uses the upload from the db
    def wrapper(*args, **kwargs):
        if 'upload_uuid' in kwargs:
            kwargs['upload_from_db'] = Upload.from_db(
                kwargs['upload_uuid'])
        if not kwargs.get('upload_from_db'):
            LOG.with_fields({'upload': kwargs['upload_uuid']}).info(
                'Upload not found, genuinely missing')
            return sf_api.error(404, 'upload not found')

        return func(*args, **kwargs)
    return wrapper


def redirect_upload_request(func):
    # Redirect method to the hypervisor hosting the upload
    def wrapper(*args, **kwargs):
        u = kwargs.get('upload_from_db')
        if not u:
            return

        if not u.node:
            return

        if u.node != config.NODE_NAME:
            path = flask.request.environ['PATH_INFO']
            url = f'http://{u.node}:13000{path}'
            api_token = get_api_token(
                f'http://{u.node}:13000', namespace=request_namespace())
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=flask.request.get_data(cache=False, as_text=False,
                                            parse_form_data=False),
                headers={
                    'Authorization': api_token,
                    'User-Agent': util_general.get_user_agent(),
                    'X-Request-ID': flask.request.headers.get('X-Request-ID')
                })

            LOG.with_fields({
                'method': flask.request.environ['REQUEST_METHOD'],
                'url': url,
                'status_code': r.status_code,
                'body_bytes': len(r.content)
            }).info('Returning proxied request')
            resp = flask.Response(
                r.content,
                mimetype=r.headers.get('Content-Type', 'application/json'))
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def log_request(func):
    def wrapper(*args, **kwargs):
        j = sf_api.flask_get_post_body()

        if j:
            for key in j:
                if key == 'uuid':
                    destkey = 'passed_uuid'
                else:
                    destkey = key
                kwargs[destkey] = j[key]

        formatted_headers = []
        for header in flask.request.headers:
            formatted_headers.append(str(header))

        # The body has just been merged into kwargs, so on a credential
        # carrying route kwargs now holds the credential. Drop the lot
        # rather than naming fields, for the reasons on
        # handles_credentials.
        if handles_credentials():
            kwargs_log = {'body': REDACTED_BODY}
        else:
            # Ensure key does not appear in logs
            kwargs_log = kwargs.copy()
            if 'key' in kwargs_log:
                kwargs_log['key'] = '*****'

            # Redact a password if any
            if 'password' in kwargs_log:
                kwargs_log['password'] = '*****'

        # Redact the JWT auth token in headers as well
        headers_log = dict(flask.request.headers)
        if 'Authorization' in headers_log:
            headers_log['Authorization'] = 'Bearer *****'

        # Attempt to lookup the identity from JWT token. This doesn't use
        # the usual get_jwt_identity() because that requires that the
        # require_jwt() decorator has been run, and that is not the case
        # for all paths this wrapper covers. Its ok for there to be no
        # identity here, for example unprotected paths.
        identity = None
        try:
            auth = flask.request.headers.get('Authorization')
            if auth:
                token = auth.split(' ')[1]
                dt = decode_token(token)
                identity = dt.get('identity')
        except Exception:
            pass

        log = LOG.with_fields({
            'request-id': flask.request.environ.get('FLASK_REQUEST_ID', 'none'),
            'identity': identity,
            'method': flask.request.method,
            'url': flask.request.url,
            'path': flask.request.path,
            'args': args,
            'kwargs': kwargs_log,
            'headers': headers_log
        })
        if flask.request.path in HEALTH_PROBE_PATHS:
            # This is likely a load balancer or orchestrator health check
            log.debug('API request parsed')
        else:
            log.info('API request parsed')

        return func(*args, **kwargs)

    return wrapper


def handle_authorization_exceptions(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)

        except TypeError as e:
            return sf_api.error(400, str(e), suppress_traceback=False)

        except DecodeError:
            # Send a more informative message than 'Not enough segments'. If this
            # is a web browser, redirect them back to the root URL. Otherwise just
            # return a 401.
            if flask.request.headers.get('Accept', 'text/html').find('text/html') != -1:
                resp = flask.redirect('/', code=302)
                unset_jwt_cookies(resp)
                return resp
            return sf_api.error(401, 'invalid JWT in Authorization header',
                                suppress_traceback=True)

        except ExpiredSignatureError as e:
            # The JWT looked valid, except it has expired. If this is a web
            # browser, redirect them back to the root URL. Otherwise just return
            # a 401.
            if flask.request.headers.get('Accept', 'text/html').find('text/html') != -1:
                resp = flask.redirect('/', code=302)
                unset_jwt_cookies(resp)
                return resp
            return sf_api.error(401, str(e), suppress_traceback=True)

        except (JWTDecodeError,
                NoAuthorizationError,
                InvalidHeaderError,
                WrongTokenError,
                RevokedTokenError,
                FreshTokenRequired,
                CSRFError,
                PyJWTError,
                ) as e:
            return sf_api.error(401, str(e), suppress_traceback=True)

    return wrapper


def handle_database_unavailable(func):
    # An unreachable or failing database must surface to clients as a
    # 503, never as an authentication or "object not found" failure:
    # the auth path treating an unreadable namespace key set as a 401
    # 'JWT token uses non-existent key' sent clients into
    # re-authentication loops and misdirected diagnosis towards key
    # rotation (issue 3522, the auth-path variant of issue 3373). This
    # sits below record_exception in the decorator stack so a database
    # outage answers each request with a clean 503 rather than
    # recording a server exception per request for the duration.
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except exceptions.DatabaseUnavailable as e:
            LOG.with_fields({
                'method': flask.request.method,
                'path': flask.request.path,
                'error': str(e)}).error(
                'Database unavailable while handling API request')
            return sf_api.error(503, 'database unavailable, please retry',
                                suppress_traceback=True)

    return wrapper


# The flask.g key carrying the correlation fields for a recorded
# exception from the record_exception decorator out to
# suppress_exceptions_to_client, which sits immediately outside it in
# Resource.method_decorators and emits the single log line for the
# event. flask.g is request scoped, so there is no cross-request leak.
_RECORDED_EXCEPTION_FIELDS = 'sf_recorded_exception_fields'


def record_exception(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # suppress_exceptions_to_client wraps this decorator -- it
            # is last in Resource.method_decorators and therefore
            # outermost -- and is guaranteed to log the full detail of
            # anything we re-raise. Recording must therefore not log a
            # second entry for the same event: that pair is exactly the
            # duplicate signature issue 3590 describes, and fixing only
            # ignore_exception would have left the API tier emitting it.
            #
            # Stash the correlation fields so that single 'Server error'
            # line can carry them. Without this the only link from a log
            # line to /srv/shakenfist/exceptions/<hash>.json would be on
            # a DEBUG record, which centralised logging does not ship.
            fields = util_exceptions.record_exception(
                *sys.exc_info(), already_logged=True)
            if fields:
                try:
                    setattr(flask.g, _RECORDED_EXCEPTION_FIELDS, fields)
                except RuntimeError:
                    # No application context. This decorator is only
                    # reached through flask_restful dispatch, so it
                    # should not happen -- but losing the correlation
                    # fields is survivable and replacing the exception
                    # we are about to re-raise is not. That is the
                    # misattribution issue 3433 was about, and it is
                    # why record_exception itself never raises either.
                    pass
            raise e

    return wrapper


def suppress_exceptions_to_client(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Attach the exception class, traceback and request context as
            # explicit structured fields. The .exception() call also carries
            # exc_info for the formatter's exception_class / stack_trace
            # enrichment, but 'Server error' records have reached centralised
            # logging without that enrichment (issue 3433), leaving the events
            # unattributable. Explicit fields ride in extra_fields and so
            # survive independently of exc_info handling.
            fields = {
                'exception_class': type(e).__name__,
                'traceback': traceback.format_exc(),
                'method': flask.request.method,
                'path': flask.request.path,
            }

            # Correlation fields for the on-disk record, stashed by the
            # record_exception decorator immediately inside this one.
            # This is the only line shipped for the event, so it is the
            # only place they can ride out (issue 3590).
            fields.update(getattr(flask.g, _RECORDED_EXCEPTION_FIELDS, {}))

            LOG.with_fields(fields).exception('Server error')
            return sf_api.error(500, 'server error: %s' % repr(e),
                                suppress_traceback=True)

    return wrapper


def object_events_response(object_type, object_uuid, limit, event_type):
    """Build the per-object events REST response.

    Shared by the /{instance,artifact,network,node,blob}/<u>/events
    endpoints: each handler does authn / authz / object lookup, then
    delegates the read-and-shape step here so the wire-format change
    only happens in one place.
    """
    return [
        row.model_dump(mode='json')
        for row in mariadb.get_object_events(
            object_type, object_uuid,
            limit=limit, event_type=event_type)
    ]


def public(func):
    """Mark an endpoint method as deliberately unauthenticated.

    Authentication is applied to every resource method by
    Resource.method_decorators below, so this marker is the only way
    out of it, and every use of it needs justifying in review. There
    should only ever be a handful.

    Apply it as the outermost (topmost) decorator on the method. The
    marker is an attribute read off the bound method at dispatch time,
    and several decorators in this file predate functools.wraps and so
    do not propagate attributes -- being outermost means the marker
    cannot be swallowed by whatever is applied beneath it. The
    structural test in shakenfist/tests/external_api/test_auth_universal.py
    enumerates the routes and will fail if this is ever not true.
    """
    func._sf_public = True
    return func


def scope(family=None, verb=None, name=None):
    """Override the scope derived for an endpoint method.

    Scopes are normally derived from the resource class and the HTTP
    method, so most endpoints need nothing. This annotates the cases
    where that derivation misleads -- a POSTed power action is not
    really a write of the instance -- and it exists so those cases are
    visible at the decoration site and greppable in review.

    Apply it as the outermost decorator, for the same reason
    @public must be: the marker is read off the bound method at
    dispatch and several decorators in this file predate
    functools.wraps. tools/check-endpoint-authentication.sh enforces
    that placement.
    """
    def decorator(func):
        func._sf_scope = {'family': family, 'verb': verb, 'scope': name}
        return func
    return decorator


def _enforce_scope(func, resource_class, override):
    def wrapper(*args, **kwargs):
        required = api_scopes.required_scope(
            resource_class, flask.request.method, override)
        held = get_jwt().get('scopes')

        if not api_scopes.satisfies(held, required):
            resource_name = (resource_class.__name__
                             if resource_class else None)
            LOG.with_fields({
                'required': required,
                'held': held,
                'resource': resource_name
            }).info('Request denied by scope')
            return sf_api.error(
                403, 'token is not scoped for this operation')

        return func(*args, **kwargs)
    return wrapper


def _authenticate_unless_public(func):
    # The method_decorators entry which makes authentication the
    # default. flask_restful applies these to the bound method at
    # dispatch, so this wraps outside every per-method decorator --
    # which is what lets authentication precede the ownership checks
    # that assume an authenticated caller.
    if getattr(func, '_sf_public', False):
        return func

    # The resource instance is available because func is bound. The
    # HTTP method is read from the request rather than from
    # func.__name__, which is unreliable: the per-method decorators in
    # this file predate functools.wraps, so by the time we see it the
    # name is often 'wrapper'.
    instance = getattr(func, '__self__', None)
    resource_class = type(instance) if instance is not None else None
    override = getattr(func, '_sf_scope', None)

    # verify_token wraps the scope check, so the token is proven valid
    # before its claims are trusted to say what it may do.
    return verify_token(_enforce_scope(func, resource_class, override))


class Resource(flask_restful.Resource):
    # Remember that order here matters, the record_exception
    # wrapper deliberately reraises the exception so that
    # generic_wrapper can handle the response after logging.
    #
    # flask_restful applies these in list order with each wrapping the
    # previous, so the LAST entry ends up outermost and therefore runs
    # FIRST. Reading bottom to top gives execution order: exceptions
    # are suppressed and recorded, authorization errors are turned into
    # responses, the request is logged, and only then is the caller
    # authenticated. Authentication is last in that sequence but still
    # ahead of every per-method decorator, so an unauthenticated
    # request never reaches an ownership check.
    method_decorators = [
        _authenticate_unless_public,
        log_request,
        handle_authorization_exceptions,
        handle_database_unavailable,
        record_exception,
        suppress_exceptions_to_client
        ]
