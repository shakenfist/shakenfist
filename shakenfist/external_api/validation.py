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
