# Copyright 2019 Michael Still and contributors

"""Compile the published parameter declarations into request schemas.

Phase 3 of ``docs/plans/PLAN-api-input-validation.md``. Nothing here
rejects anything: this module turns declarations into marshmallow
schemas and mounts them in a registry. The decorator which consults
them, and the warn-only telemetry which reports what they would have
refused, are separate.

**Compiled from the rendered specification, not from the declaration
tuples.** ``swagger_helper()`` already resolves the tuples into the
OpenAPI an endpoint publishes -- collapsing an operation's body
declarations into one schema, and expanding each type token into
``type``/``format`` and any constraints. Reading that output rather
than re-interpreting the tokens means the compiled schema and the
published specification cannot disagree, because there is only one
interpretation of a token in the process. Two consecutive review
rounds in phase 2 found a type token that contradicted its handler,
and a second independent mapping here would be a third way for that
class of defect to arrive.

It also gets three rules from the plan for free rather than as special
cases:

* ``netblock`` is deliberately format-only, with no pattern, because
  ``NetworksEndpoint.post()`` parses with ``ipaddress.ip_network()``,
  which accepts IPv6 too. It renders as a plain string and so compiles
  to one.
* ``uuidorname``, ``namespace``, ``node``, ``url`` and ``ipv4`` carry
  prose formats which are documentation. They render as plain strings
  and compile to plain strings; turning them into semantic validators
  is phase 6, not something this can do by accident.
* The raw request body renders as a body schema whose type is not
  ``object``, which is exactly the discriminator needed to leave
  upload bodies alone.

``format`` is documentation in every case. Only ``type``, ``pattern``,
``minimum`` and ``maximum`` constrain anything.
"""

from typing import Any
from typing import Optional

import marshmallow
from marshmallow import fields
from marshmallow import validate
from shakenfist_utilities import logs

LOG, _ = logs.setup(__name__)


# Parameter locations which are not request input this can validate.
# 'header' carries the JWT, which the authentication decorators own.
_IGNORED_LOCATIONS = frozenset(['header', 'formData'])

_SCALARS: dict[str, type[fields.Field[Any]]] = {
    'string': fields.String,
    'integer': fields.Integer,
    'number': fields.Float,
    'boolean': fields.Boolean,
    'object': fields.Dict,
}


class CompiledEndpoint:
    """The schemas one handler's declarations compile to.

    ``required_names`` is recorded rather than enforced. The plan found
    ``mode`` on the agent-put endpoint declared required while omitting
    it has always been accepted, so enforcing required-ness would break
    working clients; phase 6 decides that, and warn-only exists to give
    it the numbers.
    """

    def __init__(self, body: Optional[marshmallow.Schema],
                 query: Optional[marshmallow.Schema],
                 path_names: set[str], required_names: set[str],
                 raw_body: bool):
        self.body = body
        self.query = query
        self.path_names = path_names
        self.required_names = required_names
        self.raw_body = raw_body

    @property
    def names(self) -> set[str]:
        """Every parameter name this endpoint declares, any location."""
        out = set(self.path_names)
        for schema in (self.body, self.query):
            if schema is not None:
                out |= set(schema.fields)
        return out


def _field(spec: dict[str, Any]) -> fields.Field[Any]:
    """One rendered parameter or property as a marshmallow field.

    Every field is optional and nullable. Optional because required-ness
    is metadata here (see CompiledEndpoint). Nullable because a JSON
    null reaches the handler as None today and several handlers treat
    that as "not supplied" -- rejecting it would be a behaviour change
    invented by the compiler rather than described by a declaration,
    and in warn-only it would fill the log with findings that are
    artefacts of this module.
    """
    kwargs: dict[str, Any] = {'required': False, 'allow_none': True}

    validators: list[Any] = []
    minimum, maximum = spec.get('minimum'), spec.get('maximum')
    if minimum is not None or maximum is not None:
        validators.append(validate.Range(min=minimum, max=maximum))
    pattern = spec.get('pattern')
    if pattern is not None:
        validators.append(validate.Regexp(pattern))
    if validators:
        kwargs['validate'] = validators

    declared = spec.get('type')
    if not isinstance(declared, str):
        declared = ''
    if declared == 'array':
        # items is always present: swagger_helper() renders the array
        # tokens with it, and OpenAPI 2.0 requires it.
        return fields.List(_field(spec.get('items', {})), **kwargs)

    field_class = _SCALARS.get(declared)
    if field_class is None:
        # A type this does not know is documented rather than enforced.
        # Raw accepts anything, which keeps an unrecognised token from
        # silently becoming a rejection -- the failure mode phase 2's
        # netblock reasoning is about.
        LOG.with_fields({'type': declared}).warning(
            'Unrecognised parameter type in the published specification; '
            'compiled as unvalidated')
        return fields.Raw(**kwargs)
    return field_class(**kwargs)


def _schema(properties: dict[str, dict[str, Any]]) -> marshmallow.Schema:
    return marshmallow.Schema.from_dict(
        {name: _field(spec) for name, spec in properties.items()})()


def compile_parameters(parameters: list[dict[str, Any]]) -> CompiledEndpoint:
    """Compile one handler's rendered OpenAPI parameters."""
    body_properties: dict[str, dict[str, Any]] = {}
    query_properties: dict[str, dict[str, Any]] = {}
    path_names: set[str] = set()
    required: set[str] = set()
    raw_body = False

    for parameter in parameters:
        location = parameter.get('in')
        name = parameter.get('name')
        if location in _IGNORED_LOCATIONS or name is None:
            continue

        if location == 'body':
            schema = parameter.get('schema', {})
            if schema.get('type') != 'object':
                # The raw body marker. Not JSON, so there is nothing to
                # parse and nothing to validate.
                raw_body = True
                continue
            body_properties.update(schema.get('properties', {}))
            required |= set(schema.get('required', []))
            continue

        if parameter.get('required'):
            required.add(name)
        if location == 'path':
            # Werkzeug has already matched these, and their values
            # arrive as URL segments rather than as JSON.
            path_names.add(name)
        elif location == 'query':
            query_properties[name] = parameter

    return CompiledEndpoint(
        body=_schema(body_properties) if body_properties else None,
        query=_schema(query_properties) if query_properties else None,
        path_names=path_names, required_names=required, raw_body=raw_body)


def build_registry(app: Any) -> dict[tuple[str, str], CompiledEndpoint]:
    """Compile every handler mounted on ``app``.

    Keyed by (endpoint class name, lowercased HTTP method), which is
    what the validating decorator can reconstruct at dispatch from
    ``type(self)`` and ``flask.request.method``.

    Built from the mounted routes rather than from a registration
    inside ``swagger_helper()``, which does not know which class or
    method it is decorating, and rather than from an attribute the
    decorator chain would have to propagate: ``base.py`` documents in
    two places that several of its decorators predate
    ``functools.wraps`` and drop attributes, which is why ``_sf_public``
    must be applied outermost. Reading the class off the mount avoids
    that question entirely.
    """
    out: dict[tuple[str, str], CompiledEndpoint] = {}
    for view in app.view_functions.values():
        cls = getattr(view, 'view_class', None)
        if cls is None:
            continue
        for method in ('get', 'post', 'put', 'delete', 'patch'):
            handler = getattr(cls, method, None)
            if handler is None:
                continue
            specs = getattr(handler, 'specs_dict', None)
            if specs is None:
                # Not documented, so nothing to compile. The three
                # handlers in this state are Root, Livez and Readyz, and
                # test_parameter_declarations.py holds that list closed;
                # a new undocumented endpoint fails there rather than
                # quietly arriving here.
                continue
            out[(cls.__name__, method)] = compile_parameters(
                specs.get('parameters', []))
    return out


# ---------------------------------------------------------------------
# Warn-only checking.
#
# Nothing below rejects anything while API_VALIDATION_MODE is 'warn',
# which is the default and is what phase 3 ships. The findings are
# recorded on flask.g and emitted once the response status is known,
# because "what did this request return anyway" is what separates a
# rejection enforcement would introduce from a status code it would
# merely change -- and at validation time that is not yet known.

# The request-scoped hand-off, named like base.py's
# _RECORDED_EXCEPTION_FIELDS because it is the same pattern.
VALIDATION_FINDINGS = 'sf_validation_findings'
BODY_PATH_COLLISIONS = 'sf_body_path_collisions'

# Reason codes. Counted separately because they answer different
# questions: see the table in the phase 3 plan.
UNKNOWN_PARAMETER = 'unknown-parameter'
TYPE_MISMATCH = 'type-mismatch'
MISSING_REQUIRED = 'missing-required'
BODY_PATH_COLLISION = 'body-path-collision'


# The longest parameter name a finding will carry. Names are client
# supplied, so without a bound one request could put megabytes -- or
# control characters -- into a log line and, in enforce mode, into the
# response. 64 comfortably covers every declared name in the API.
MAX_PARAMETER_NAME = 64

# The most unknown-parameter findings one request can produce. The
# other reasons are bounded by the declaration (declared fields, path
# names), but unknown body keys are bounded only by what a caller
# sends, and each finding is a log line shipped to centralised
# logging. The overflow is summarised in one finding carrying the
# count, so the measurement still learns the request happened.
MAX_UNKNOWN_PARAMETER_FINDINGS = 20


class Finding:
    """One thing validation would have refused.

    Carries the offending value's *type* and never its value: decision
    D5, and several of these routes carry credentials, which is why
    log_request drops the whole body on a credential-handling route
    rather than naming fields. The parameter *name* is also client
    supplied on the unknown-parameter path, so it is truncated rather
    than trusted.
    """

    def __init__(self, reason: str, parameter: str, detail: str,
                 value: Any = None):
        self.reason = reason
        self.parameter = parameter[:MAX_PARAMETER_NAME]
        self.detail = detail
        self.value_type = type(value).__name__ if value is not None else None

    def fields(self) -> dict[str, Any]:
        return {
            'validation-reason': self.reason,
            'validation-parameter': self.parameter,
            'validation-detail': self.detail,
            'validation-value-type': self.value_type,
        }


def _schema_findings(schema: Optional[marshmallow.Schema],
                     supplied: dict[str, Any]) -> list[Finding]:
    if schema is None:
        return []
    known = {name: value for name, value in supplied.items()
             if name in schema.fields}
    findings = []
    for parameter, messages in schema.validate(known).items():
        findings.append(Finding(
            TYPE_MISMATCH, str(parameter),
            '; '.join(messages) if isinstance(messages, list)
            else str(messages),
            known.get(parameter)))
    return findings


def check(compiled: CompiledEndpoint, body: Any,
          query: dict[str, Any], collisions: set[str]) -> list[Finding]:
    """Everything this request would have been refused for.

    Pure, so it is testable without a request context, and so the
    decorator is only responsible for deciding what to do with the
    answer.

    ``body`` is whatever the request body parsed to. A non-dict body
    is refused by log_request before validation runs, so a dict is
    what arrives in practice -- but a warn-only layer must never raise
    from inside itself, so anything else is treated as no body at all
    rather than iterated on trust.
    """
    if not isinstance(body, dict):
        body = {}

    findings: list[Finding] = []

    # An undeclared body key is already fatal when the request reaches
    # its handler -- log_request merges every key into kwargs and no
    # handler is variadic, so Python raises TypeError and the broad
    # except in handle_authorization_exceptions returns it as a 400
    # carrying interpreter text. Counting these is how phase 4 chooses
    # between webargs' EXCLUDE and RAISE (decision D10).
    if not compiled.raw_body:
        unknown = [name for name in body if name not in compiled.names]
        for name in unknown[:MAX_UNKNOWN_PARAMETER_FINDINGS]:
            findings.append(Finding(
                UNKNOWN_PARAMETER, name,
                'not declared by this endpoint', body[name]))
        if len(unknown) > MAX_UNKNOWN_PARAMETER_FINDINGS:
            findings.append(Finding(
                UNKNOWN_PARAMETER, '(overflow)',
                '%d further undeclared keys not reported individually'
                % (len(unknown) - MAX_UNKNOWN_PARAMETER_FINDINGS)))

    supplied = dict(body)
    supplied.update(query)
    for name in sorted(compiled.required_names):
        if name not in supplied and name not in compiled.path_names:
            findings.append(Finding(
                MISSING_REQUIRED, name, 'declared required but not supplied'))

    if not compiled.raw_body:
        findings.extend(_schema_findings(compiled.body, body))

    # Query-declared parameters are checked against the merged view the
    # json_or_query loader reads, body authoritative, mirroring
    # _load_json_or_query's precedence. The shipped client serialises
    # every request to a JSON body and never builds a query string, so
    # checking the query string alone would systematically miss type
    # mismatches from the API's dominant caller -- and a body-supplied
    # value for a query-declared name is not an unknown parameter
    # (names is location-agnostic), so nothing else reports it either.
    if compiled.query is not None:
        query_supplied = dict(query)
        if not compiled.raw_body:
            for name in compiled.query.fields:
                if name in body:
                    query_supplied[name] = body[name]
        findings.extend(_schema_findings(compiled.query, query_supplied))

    for name in sorted(collisions):
        findings.append(Finding(
            BODY_PATH_COLLISION, name,
            'a body key of this name overwrote the URL path parameter'))

    return findings


# Populated by install(), which app.py calls once every route is
# mounted. A module global rather than something base.py builds,
# because base.py is imported to define the endpoints and so cannot see
# the finished app.
REGISTRY: dict[tuple[str, str], CompiledEndpoint] = {}


def install(app: Any) -> None:
    """Compile every mounted handler. Call once, after the last route."""
    REGISTRY.clear()
    REGISTRY.update(build_registry(app))
    LOG.with_fields({'handlers': len(REGISTRY)}).info(
        'Compiled API parameter declarations')
