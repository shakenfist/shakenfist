import copy
import functools
import json
import math
import re
import sys
import time
import traceback
from typing import Any
from typing import NoReturn
from typing import Optional

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
from werkzeug.exceptions import HTTPException
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
from shakenfist.external_api import validation
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


@webargs_parser.error_handler
def _webargs_error(error, req, schema, *, error_status_code, error_headers):
    """Answer a webargs parse failure in this API's error shape.

    webargs' default handler aborts 422 carrying its own
    {"json": {"field": ["message"]}} body -- but no client ever saw
    that. Nothing registered a handler before phase 3, and the abort's
    HTTPException was swallowed by suppress_exceptions_to_client's bare
    except Exception, so the four @use_kwargs sites answered a bad
    query parameter with a 500, a 'Server error' log line and an
    exception record on disk. This handler and the HTTPException
    carve-out in suppress_exceptions_to_client are the two halves of
    the fix: the abort below only reaches a client because that
    carve-out returns the response it carries.

    Decision D4 fixes the status and the shape without changing what
    is rejected -- every request webargs refused before is still
    refused, and one it accepted is still accepted.
    """
    # error.messages is keyed by location, then by field. The location
    # is not useful to a caller, and for the custom json_or_query
    # loader it would name a location no caller can address.
    parameters = []
    for by_field in error.messages.values():
        if isinstance(by_field, dict):
            for field, messages in by_field.items():
                detail = ('; '.join(messages)
                          if isinstance(messages, list) else str(messages))
                parameters.append('%s: %s' % (field, detail))
    flask.abort(sf_api.error(
        400, parameters[0] if parameters else 'invalid request parameter'))


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


def agent_operation_timing(deadline_seconds, progress_timeout_seconds,
                           progress_capable):
    """Turn an agent operation request's timing parameters into stored values.

    Returns ``((deadline, progress_timeout), None)``, or
    ``(None, <400 response>)`` if the caller sent something which
    cannot be honoured -- the same convention as
    ``resolve_lookup_namespace()`` above.

    Three values are possible for each parameter, and they are not the
    same three:

    * Omitted (``None``, never merely falsy) means the caller expressed
      no intent, so the server default applies. It is applied *here*
      rather than left as SQL NULL, because a deadline runs from the
      moment this request was received and only this node knows when
      that was. A NULL in the database means the row was written by an
      API server which predates deadlines, and the enforcement phase
      anchors those at dispatch time instead.
    * An explicit ``0`` means the caller asked for none. Streaming a
      very large file with no wall-clock deadline but a live progress
      timeout is a first-class use case, so this has to survive the
      API layer as ``0.0`` rather than being folded into "omitted".
    * Anything else is a count of seconds.

    ``progress_capable`` says whether any command in the operation can
    report progress. When it cannot -- the execute endpoint -- an
    omitted progress timeout stores ``0.0``, which is true of the
    operation, rather than a default which could never fire.
    """
    # Both parameters share one operator ceiling. It is what stops a
    # caller parking an instance's single executor slot with an
    # enormous budget, so the 0 sentinels are deliberately not folded
    # into it here: an operation with no budget at all is instead
    # bounded by the same ceiling at enforcement time (see
    # AgentOperation.effective_deadline() and issue #4074), while the
    # sentinel with a live progress timeout keeps meaning what it says.
    deadline, error = _timing_seconds(
        'deadline_seconds', deadline_seconds,
        maximum=config.AGENT_OPERATION_MAX_DEADLINE)
    if error:
        return None, error

    progress_timeout, error = _timing_seconds(
        'progress_timeout_seconds', progress_timeout_seconds,
        maximum=config.AGENT_OPERATION_MAX_DEADLINE)
    if error:
        return None, error

    if deadline is None:
        deadline = time.time() + config.AGENT_OPERATION_DEFAULT_DEADLINE
    elif deadline > 0:
        # A request deadline is relative to now; the stored one is
        # absolute. The explicit zero sentinel is not a duration and
        # must not be shifted onto the clock.
        deadline = time.time() + deadline

    if progress_timeout is None:
        if progress_capable:
            progress_timeout = float(
                config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT)
        else:
            progress_timeout = 0.0

    return (deadline, progress_timeout), None


def _timing_seconds(name, value, maximum=None):
    """Validate one timing parameter, returning (seconds_or_None, error).

    None comes back for an omitted parameter and is the caller's
    signal to apply a default. Every rejection is a 400: these are
    durations, and silently reinterpreting one a caller got wrong is
    how a timeout ends up meaning something nobody asked for.

    maximum is the operator ceiling published as the parameter's
    maximum in the API specification, which the rejection here is what
    backs. The 0 sentinel trivially passes it, which is intended --
    what it asks for is decided at enforcement time, not here.
    """
    if value is None:
        return None, None

    # isinstance(True, int) is true in Python, so a bool reaches
    # float() happily and arrives as 1.0. JSON true is not a number of
    # seconds, and accepting it as one second would be worse than
    # refusing it.
    if isinstance(value, bool):
        return None, sf_api.error(
            400, '%s must be a number of seconds, not a boolean' % name)

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None, sf_api.error(
            400, '%s must be a number of seconds' % name)

    # Neither infinity nor NaN is a duration, and both arrive easily:
    # the JSON string "inf" converts, and json.loads() accepts the
    # bare Infinity and NaN literals by default, so flask hands them
    # straight through. Refuse both here rather than downstream. NaN
    # in particular would slip past a bare "< 0" test, because it
    # fails every comparison including that one, and infinity would
    # produce an infinite absolute deadline -- which has the same
    # effect as the 0 sentinel but does not look like it, and which
    # the DOUBLE column cannot store anyway.
    if not math.isfinite(seconds):
        return None, sf_api.error(
            400, '%s must be a finite number of seconds' % name)

    if seconds < 0:
        return None, sf_api.error(
            400, '%s must not be negative' % name)

    if maximum is not None and seconds > maximum:
        return None, sf_api.error(
            400,
            '%s must not exceed this deployment\'s '
            'AGENT_OPERATION_MAX_DEADLINE of %d seconds' % (name, maximum))

    return seconds, None


# The keys a declaration's optional sixth element may carry. All three
# are valid Swagger 2.0 parameter keywords and valid JSON Schema, so a
# constraint renders into the published OpenAPI rather than living only
# in code -- which is the property that made the events limit cap
# invisible to callers for years.
CONSTRAINT_KEYS = frozenset(['minimum', 'maximum', 'pattern'])


# Type MUST be one of "string", "number", "integer", "boolean", "array" or "file".
ARGTYPES: dict[str, dict[str, Any]] = {
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


def _validated_constraints(section: str, name: str,
                           rendered: dict[str, Any],
                           constraints: Any) -> dict[str, Any]:
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
        # re.compile() answers "valid for CPython", but the consumers
        # of this pattern are JSON Schema validators and client
        # generators, where pattern is ECMA-262. The Python-only
        # constructs are refused explicitly rather than left to fail
        # in somebody else's toolchain. Note also that JSON Schema
        # pattern is an unanchored partial match, so a pattern which
        # means to match the whole value needs ^...$ as macaddr does.
        dialect = [c for c in ('(?P', '(?#', '\\A', '\\Z') if c in pattern]
        if dialect:
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares a pattern using the Python only '
                'construct(s) %s; OpenAPI patterns are ECMA-262'
                % (section, name, ', '.join(dialect)))
        # Anchoring is where the consumers genuinely disagree: JSON
        # Schema pattern is an unanchored search, while the compiled
        # validator requires the pattern to consume the whole value
        # (re.fullmatch -- see validation._field(), which uses it
        # precisely because Python's $ also matches before a trailing
        # newline and ECMA-262's does not). A declared ^...$ pattern is
        # the one form both read identically, so it is required rather
        # than documented -- an unanchored pattern would pass here and
        # then wrongly reject (or wrongly admit) requests once phase 4
        # enforces.
        if not (pattern.startswith('^') and pattern.endswith('$')):
            raise exceptions.InvalidAPIDeclaration(
                '%s parameter %s declares a pattern which is not '
                'anchored with ^...$: %r. JSON Schema searches and the '
                'compiled validator matches, and full anchoring is the '
                'only form they read identically'
                % (section, name, pattern))
        # A top-level alternation defeats the anchors the check above
        # just required: '^a|b$' means (^a)|(b$) and is anchored on
        # neither branch, so the string test alone would bless a
        # pattern the two consumers still read differently. Wrap the
        # alternation in a group instead.
        depth, escaped = 0, False
        for character in pattern:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '(':
                depth += 1
            elif character == ')':
                depth -= 1
            elif character == '|' and depth == 0:
                raise exceptions.InvalidAPIDeclaration(
                    '%s parameter %s declares a pattern with a top '
                    'level alternation, which escapes the ^...$ '
                    'anchors: %r. Wrap the alternation in a group'
                    % (section, name, pattern))

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
        # Deep, because the array tokens nest an items dict: a
        # shallow copy would alias one module-level mutable into every
        # declaration in the tree and into the specification flasgger
        # re-reads on every request, where a single in-place write
        # anywhere downstream would poison the vocabulary
        # process-wide.
        rendered = copy.deepcopy(ARGTYPES[argtype])
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

        # No second copy: rendered is already a fresh deep copy made
        # once per iteration and nothing else holds it, so handing it
        # on directly is what the non-body and raw-body branches above
        # both do.
        rendered['description'] = argdescription
        body_properties[name] = rendered
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


def _token_request_fields(ns_name: Optional[str] = None,
                          key_name: Optional[str] = None) -> dict[str, Any]:
    """The attribution a rejected token needs.

    The keyname, method, path and remote address log_token_use()
    records on the success path, plus the namespace, so that a
    rejection can be read the same way an acceptance is. The presented
    token and the key's nonce are never included: both are replayable
    by anyone who can read the log.
    """
    return {
        'namespace': ns_name,
        'keyname': key_name,
        'method': flask.request.environ.get('REQUEST_METHOD'),
        'path': flask.request.environ.get('PATH_INFO'),
        'remote-address': flask.request.remote_addr
    }


def _reject_token(message: str, ns_name: Optional[str] = None,
                  key_name: Optional[str] = None,
                  ns: Optional[Namespace] = None) -> NoReturn:
    """Record a rejected token and raise the 401.

    NOTE(mikal): these are logged at INFO, not ERROR. Every rejection
    reachable from here is caused by the credential the client
    presented -- most commonly a token minted before its key was
    rotated or deleted, which is an entirely expected client condition
    and is exactly what the nonce exists to cause. None of them are
    cluster faults, so none of them should be paging anyone (issue
    3606). The message alone was also unattributable, so the request
    context travels with it and, where the namespace still exists, is
    recorded as an audit event as well. Getting here requires a token
    signed by this cluster, so an unauthenticated caller cannot use
    that event write as an amplifier.
    """
    fields = _token_request_fields(ns_name, key_name)
    LOG.with_fields(fields).info(message)
    if ns:
        ns.add_event(EVENT_TYPE_AUDIT, message, extra=fields)
    raise NoAuthorizationError()


def verify_token(func):
    def wrapper(*args, **kwargs):
        # Ensure there is a valid JWT with a correct signature
        _, jwt_data = verify_jwt_in_request(
            False, False, False, ['headers'], True)

        # Perform SF specific safety checks
        try:
            ns_name, key_name = parse_jwt_identity()
        except (TypeError, ValueError):
            # Unlike the rejections below this one is ours, not the
            # client's: only this cluster can sign a token, so a valid
            # signature over an unparseable subject means we minted it
            # that way. That is worth an ERROR.
            LOG.with_fields(_token_request_fields()).error(
                'JWT token does not contain a namespace and key name in '
                'the subject field')
            raise NoAuthorizationError()

        ns = Namespace.from_db(ns_name)
        if not ns:
            _reject_token('JWT token is for non-existent namespace',
                          ns_name=ns_name, key_name=key_name)
        if ns.state.value == dbo.STATE_DELETED:
            _reject_token('JWT token is for deleted namespace',
                          ns_name=ns_name, key_name=key_name, ns=ns)

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
                _reject_token('JWT token uses non-existent key',
                              ns_name=ns_name, key_name=key_name, ns=ns)

            # lookup_key() returns the attributes model, so the nonce is
            # a SecretStr. It is unwrapped for the comparison because the
            # claim side is a plain string out of the token, and
            # SecretStr never compares equal to a str -- leaving it
            # wrapped would reject every request rather than fail open,
            # but it would still be wrong.
            nonce = key.nonce.get_secret_value()
            if 'nonce' not in jwt_data:
                _reject_token('JWT token lacks nonce', ns_name=ns_name,
                              key_name=key_name, ns=ns)
            if jwt_data['nonce'] != nonce:
                _reject_token('JWT token has incorrect nonce',
                              ns_name=ns_name, key_name=key_name, ns=ns)

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


def proxy_request_to_node(url, api_token, data, peer):
    """Proxy the current request to a peer node's API and relay its reply.

    A refused, reset, or otherwise failed connection to the peer is an
    infrastructure condition, not a fault in the API server which received
    the request, so it must not escape as an unqualified 500 (issue 3743,
    the node-to-node variant of issues 3373 and 3522). It surfaces as a
    503 naming the peer, with the peer and proxied URL as structured log
    fields so the failure is attributable without parsing a traceback.
    The commonest cause is a peer's API restarting during a rolling
    redeploy -- a handled, retryable condition -- so it is logged at
    WARNING, not ERROR (issue 3850).
    """
    method = flask.request.environ['REQUEST_METHOD']

    try:
        r = requests.request(
            method, url,
            data=data,
            headers={
                'Authorization': api_token,
                'User-Agent': util_general.get_user_agent(),
                'X-Request-ID': flask.request.headers.get('X-Request-ID')
            })
    except requests.exceptions.RequestException as e:
        LOG.with_fields({
            'method': method,
            'url': url,
            'peer': peer,
            'error': str(e)
        }).warning('Peer node API unreachable while proxying request')
        return sf_api.error(
            503, f'peer node {peer} did not answer the proxied request, please retry',
            suppress_traceback=True)

    LOG.with_fields({
        'method': method,
        'url': url,
        'peer': peer,
        'status_code': r.status_code,
        'body_bytes': len(r.content)
    }).info('Returning proxied request')
    resp = flask.Response(
        r.content,
        mimetype=r.headers.get('Content-Type', 'application/json'))
    resp.status_code = r.status_code
    return resp


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
            return proxy_request_to_node(
                url, api_token, json.dumps(sf_api.flask_get_post_body()),
                target_node.fqdn)

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
            return proxy_request_to_node(
                f'http://{config.NETWORK_NODE_IP}:13000{path}', admin_token,
                flask.request.data, config.NETWORK_NODE_IP)

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
            return proxy_request_to_node(
                url, api_token,
                flask.request.get_data(cache=False, as_text=False,
                                       parse_form_data=False),
                u.node)

        return func(*args, **kwargs)
    return wrapper


def log_request(func):
    def wrapper(*args, **kwargs):
        j = sf_api.flask_get_post_body()

        # Stashed for the validator, which runs two decorators later:
        # reading it back means validation reports on exactly the body
        # merged into the handler's kwargs below, and a body which
        # failed to parse as JSON is not re-parsed to find that out a
        # second time. Stashed unconditionally -- check() already
        # normalises a non-dict body -- so the fallback fetch in
        # validate_request only runs when log_request never ran. The
        # one body it cannot distinguish is a JSON null, which stashes
        # the same None as "unset"; the fallback re-fetch is answered
        # from flask's parse cache in that case.
        try:
            setattr(flask.g, validation.PARSED_BODY, j)
        except RuntimeError:
            pass

        if j:
            # Only a JSON object can merge into kwargs. Any other JSON
            # document -- a list, a string, a number -- has always been
            # refused as a 400 (previously by the per-key merge raising
            # TypeError on the lookup), and this guard keeps it that
            # way: dict.update would raise ValueError for most of them,
            # which nothing in the decorator chain catches, and would
            # silently merge a list of two-character strings as key
            # value pairs.
            if not isinstance(j, dict):
                raise TypeError('the request body must be a JSON object')

            # A body key with the same name as a URL path parameter
            # overwrites it. Recorded here rather than in the validator
            # because this decorator is applied outside it and so runs
            # first: by the time the validator sees kwargs the overwrite
            # has happened and is indistinguishable from a path
            # parameter which simply had that value (decision D12).
            #
            # The 'uuid' -> 'passed_uuid' remap which used to live here
            # was not the dodge decision D8 cites it as. 'passed_uuid'
            # occurred nowhere else in the tree, so no handler accepted
            # it and a body 'uuid' was a guaranteed 400 carrying
            # interpreter text on every endpoint in the API. Dropped, so
            # a body 'uuid' is now an undeclared parameter like any
            # other and is reported as one (decision D11).
            collisions = set(j) & set(kwargs)
            if collisions:
                try:
                    setattr(flask.g, validation.BODY_PATH_COLLISIONS,
                            collisions)
                except RuntimeError:
                    # No application context. Losing the record is
                    # survivable; replacing the request is not.
                    pass
            kwargs.update(j)

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


def redirect_to_root_clearing_jwt() -> flask.Response:
    # Send a browser back to the root URL with its now useless JWT
    # cookies cleared. flask.redirect() is declared as returning
    # werkzeug's Response, but unset_jwt_cookies() requires flask's
    # subclass of it. make_response() coerces the redirect to the
    # application's response class -- which is what a redirect built
    # inside a request context already is at runtime -- so the object
    # genuinely is the type the callee asks for rather than being cast
    # into shape.
    resp = flask.make_response(flask.redirect('/', code=302))
    unset_jwt_cookies(resp)
    return resp


def _authorization_failure_log(e: Exception):
    """A logger carrying the attribution a rejected request needs.

    The record emitted here is the only one saying *why* a request was
    rejected, and without the request-id it cannot be joined to the
    'API request parsed' and audit records which say *which* request it
    was (issue 4069). The exception class travels as its own field so
    an expired token, an unparseable one and a revoked one are
    distinguishable in a query rather than only by message text.
    """
    return LOG.with_fields({
        'request-id': flask.request.environ.get('FLASK_REQUEST_ID', 'none'),
        'method': flask.request.method,
        'path': flask.request.path,
        'remote-address': flask.request.remote_addr,
        'error-class': type(e).__name__,
        'error': str(e)
    })


def handle_authorization_exceptions(func):
    # NOTE(mikal): like _reject_token, these are logged at INFO. Every
    # rejection here is caused by the credential the client presented,
    # which is an expected client condition and not a cluster fault, so
    # none of them should be paging anyone (issue 3606).
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)

        except TypeError as e:
            _authorization_failure_log(e).info('API request rejected as malformed')
            return sf_api.error(400, str(e), suppress_traceback=False)

        except DecodeError as e:
            # Send a more informative message than 'Not enough segments'. If this
            # is a web browser, redirect them back to the root URL. Otherwise just
            # return a 401.
            log = _authorization_failure_log(e)
            if flask.request.headers.get('Accept', 'text/html').find('text/html') != -1:
                log.info('Undecodable JWT, redirecting browser to root')
                return redirect_to_root_clearing_jwt()
            log.info('API request rejected, undecodable JWT')
            return sf_api.error(401, 'invalid JWT in Authorization header',
                                suppress_traceback=True)

        except ExpiredSignatureError as e:
            # The JWT looked valid, except it has expired. If this is a web
            # browser, redirect them back to the root URL. Otherwise just return
            # a 401.
            log = _authorization_failure_log(e)
            if flask.request.headers.get('Accept', 'text/html').find('text/html') != -1:
                log.info('Expired JWT, redirecting browser to root')
                return redirect_to_root_clearing_jwt()
            log.info('API request rejected, expired JWT')
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
            _authorization_failure_log(e).info('API request rejected, JWT authorization failed')
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
            # An HTTPException carrying a crafted response is a
            # deliberate abort -- _webargs_error is the one source in
            # this tree -- not a server fault. Recording it would write
            # an exception record for every malformed query parameter.
            # Mirrors the carve-out on the got_request_exception
            # handler in app.py.
            if isinstance(e, HTTPException) and e.response is not None:
                raise

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
            # A deliberate abort carrying a crafted response is returned
            # as that response rather than suppressed into a 500.
            # HTTPException is an Exception subclass, so without this
            # the 400 _webargs_error aborts with would never reach a
            # client -- which is exactly what happened to webargs' own
            # 422 abort for as long as the @use_kwargs sites have
            # existed. Restricted to response-carrying exceptions so a
            # bare abort() somewhere cannot start answering werkzeug's
            # HTML error pages: every response in this API is
            # {"error": ..., "status": ...}, and an abort which wants
            # through this gate has to build one.
            if isinstance(e, HTTPException) and e.response is not None:
                return e.response

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


def validate_request(func):
    """Check a request against its published parameter declarations.

    Phase 3 of PLAN-api-input-validation. While API_VALIDATION_MODE is
    'warn' -- the default, and what phase 3 ships -- this changes
    nothing about any request: it records what it would have refused
    and calls through. app.py emits those records once the response
    status is known, because whether a finding represents a rejection
    enforcement would *introduce* or a status code it would merely
    *change* depends on what the request returned anyway.

    First in Resource.method_decorators and so innermost, which puts it
    after authentication (an unauthenticated caller cannot probe the
    schema) and before every per-method decorator. That ordering is
    also why enforcement is a contract change beyond the obvious: a
    request which is both malformed and refers to a missing object is
    answered here rather than by the 404 an arg_is_* decorator would
    have returned.

    Being innermost is also what makes func a bound method, so the
    endpoint class is readable from it without depending on attribute
    propagation through the decorators in this file which predate
    functools.wraps.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # The operator's safety valve: if the layer itself is the
        # problem -- log volume from a chatty caller being the
        # foreseeable case -- it can be turned off without a downgrade.
        if config.API_VALIDATION_MODE == 'off':
            return func(*args, **kwargs)

        resource = getattr(func, '__self__', None)
        if resource is None:
            return func(*args, **kwargs)

        compiled = validation.REGISTRY.get(
            (type(resource).__name__, flask.request.method.lower()))
        if compiled is None:
            # Undocumented by design: Root, Livez and Readyz. The list is
            # held closed by test_parameter_declarations.py, so this is
            # not a way for a new endpoint to opt out silently.
            return func(*args, **kwargs)

        # A raw body is bytes of arbitrary size which check() would
        # ignore anyway, so it is never fetched: flask_get_post_body()
        # attempts two full JSON parses of a body that is not JSON
        # before returning nothing, and the upload data path is the
        # API's bulk transfer route. Everything else reads the body
        # log_request already parsed and merged, so validation reports
        # on exactly what the handler receives; the fallback fetch only
        # runs if log_request somehow did not stash one.
        body: dict[str, Any] = {}
        if not compiled.raw_body:
            stashed = getattr(flask.g, validation.PARSED_BODY, None)
            body = (stashed if stashed is not None
                    else sf_api.flask_get_post_body() or {})
        findings = validation.check(
            compiled, body, flask.request.args.to_dict(),
            getattr(flask.g, validation.BODY_PATH_COLLISIONS, set()))
        if not findings:
            return func(*args, **kwargs)

        # Stashed before the enforce decision, so a rejected request
        # still emits its finding lines from the after_request hook --
        # carrying mode=enforce and the 400. Rejecting silently would
        # turn the measurement apparatus off at the exact moment phase
        # 4 flips the switch, which is when an operator most needs to
        # see which parameter a refused request was refused for.
        try:
            setattr(flask.g, validation.VALIDATION_FINDINGS, findings)
        except RuntimeError:
            pass

        if config.API_VALIDATION_MODE == 'enforce':
            # required is recorded and never enforced, even here:
            # several parameters are declared required while omitting
            # them has always worked (CompiledEndpoint's docstring has
            # the example), so a missing-required finding is telemetry
            # for phase 6's decision, not grounds for rejection.
            enforceable = [f for f in findings
                           if f.reason != validation.MISSING_REQUIRED]
            if enforceable:
                first = enforceable[0]
                return sf_api.error(
                    400, '%s: %s' % (first.parameter, first.detail))

        return func(*args, **kwargs)

    # Being first in method_decorators means every entry after this one
    # sees this wrapper rather than the bound method.
    # _authenticate_unless_public reads __self__ for the resource class
    # and _sf_public / _sf_scope for the policy markers, and this file
    # already documents that several of its decorators predate
    # functools.wraps and swallow attributes. functools.wraps above
    # carries the function __dict__, which is where _sf_public,
    # _sf_scope and flasgger's specs_dict live; __self__ is a bound
    # method attribute rather than a dict entry, so it is copied here.
    # Without both, every @public endpoint would start demanding a token
    # and every scope check would lose the class it is scoped to.
    wrapper.__self__ = getattr(func, '__self__', None)  # type: ignore[attr-defined]  # noqa: E501
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
        validate_request,
        _authenticate_unless_public,
        log_request,
        handle_authorization_exceptions,
        handle_database_unavailable,
        record_exception,
        suppress_exceptions_to_client
        ]
