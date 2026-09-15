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
  which accepts IPv6 too. It renders as a plain string, and the
  validator ``_FORMATS`` attaches to it calls ``ip_network()`` rather
  than matching a regex, so the compiled check is exactly as wide as
  the handler's.
* ``uuidorname``, ``namespace`` and ``node`` carry prose formats which
  are documentation and nothing else. ``uuidorname`` is ambiguous by
  construction, and a ref decorator resolves the other two against the
  database and answers 404 -- a stronger check than a format one, and
  one which already runs.
* The raw request body renders as a body schema whose type is not
  ``object``, which is exactly the discriminator needed to leave
  upload bodies alone.

``format`` used to be documentation in every case. Phase 6's step 4
made five of the format strings mean something -- see ``_FORMATS``
below -- so ``type``, ``format``, ``pattern``, ``minimum`` and
``maximum`` are now the five keys which constrain anything. The
validators are keyed on the exact string ``ARGTYPES`` renders, so the
published specification and the enforced check are the same string by
construction (decision D33).
"""

import base64
import ipaddress
import math
import re
import urllib.parse
import uuid
from typing import Any
from typing import Callable
from typing import Optional

import marshmallow
from marshmallow import fields
from marshmallow import validate
from shakenfist_utilities import logs

from shakenfist import exceptions

LOG, _ = logs.setup(__name__)


# Parameter locations which are not request input this can validate.
# 'header' carries the JWT, which the authentication decorators own.
_IGNORED_LOCATIONS = frozenset(['header', 'formData'])

# How the 'any' type token (D15) renders: a `format` annotation and no
# `type` at all, so the published schema constrains nothing while still
# telling a reader what the parameter is.
#
# It lives here rather than in base.py, where the rest of the type
# vocabulary lives, because base.py imports this module and the reverse
# would be an import cycle. ARGTYPES['any'] is built from this constant,
# so the rendering and the compiler's recognition of it are the same
# string by construction: a token that renders as one thing and compiles
# as another is precisely the drift the "compile from the rendered
# specification" design of this module exists to prevent.
ANY_VALUE_FORMAT = 'any JSON value'


class _ExactInteger(fields.Integer):
    """fields.Integer, except that 1.5 is not an integer (decision D46).

    marshmallow's Integer accepts any value int() will take, so 1.5
    passes because int(1.5) is 1. In a *deserialising* layer that is
    merely lax: the handler would receive the 1 the field produced.
    This layer is check-only by design (decision D14) --
    validate_request() runs schema.validate() for its findings and
    throws the deserialised result away, so the handler always sees the
    body the caller sent. A field which accepts a value it would have
    had to truncate is therefore not lax here, it is a false statement:
    it says the value is a valid integer about a value which is not one
    and which nothing downstream will convert.

    Measured before the fix (phase 7 finding F8): POST /instances with
    "cpus": 1.5 passed validation, reached InstanceData unchanged and
    raised a pydantic ValidationError -- a recorded 500 for a caller's
    mistake.

    **This is not marshmallow's `strict=True`, which D46 asked for and
    which cannot be used here.** `strict=True` refuses anything that is
    not already a Python int, and two kinds of caller legitimately send
    something else:

    * A query parameter is a string on the wire. base.py hands
      `flask.request.args.to_dict()` to check(), so `offset=10` arrives
      as `'10'` and `strict=True` answers `400 offset: Not a valid
      integer.` on a request the server has always served. Every
      integer query parameter in the tree is affected;
      test_blob_data_bounds caught it within a minute of the change.
    * A body integer sent as a JSON string reaches a handler which
      coerces it. `{"cpus": "8"}` is accepted today because pydantic's
      lax mode converts it, so refusing it here would be a validator
      narrower than its handler -- the breaking-change-dressed-as-a-fix
      that phase 6's width rule (and the _FORMATS comment above) exists
      to prevent.

    So the check is written against the defect rather than against the
    Python type: a JSON number with a fractional part is refused, and
    everything int() converts faithfully is left alone. An integral
    float is faithful and is accepted, for exactly the reason
    fields.Float is left alone below -- JSON has one numeric type, so
    8.0 and 8 are the same JSON number and reading one as the integer 8
    invents nothing. Infinity is left to the base class, which has a
    better message for it ("Number too large.") than this would.

    A subclass rather than a keyword at the construction site, because
    the construction site is the _SCALARS lookup below and D46 asks for
    this at every nesting depth. Binding it to the mapping from
    rendered type to field class means a branch of _field() which one
    day builds an integer some other way cannot forget it.
    """

    def _validated(self, value: Any) -> int:
        if (isinstance(value, float) and math.isfinite(value)
                and not value.is_integer()):
            # make_error rather than a hand-written message, so a
            # truncating value reads exactly like every other bad
            # integer: "Not a valid integer."
            raise self.make_error('invalid', input=value)
        return super()._validated(value)


_SCALARS: dict[str, type[fields.Field[Any]]] = {
    'string': fields.String,
    'integer': _ExactInteger,
    'number': fields.Float,
    'boolean': fields.Boolean,
    'object': fields.Dict,
}


# ---------------------------------------------------------------------
# Semantic formats (phase 6 step 4, decision D33, issue 3269).
#
# Keyed on the exact `format` string ARGTYPES renders, so the promise
# the published OpenAPI makes and the check the server performs are the
# same string by construction -- the argument ANY_VALUE_FORMAT already
# makes in its own comment, generalised. A parallel table keyed on the
# type token would be a second interpretation of the token in one
# process, which is the class of drift this module exists to prevent.
#
# Every validator obeys three rules:
#
# * a None is returned untouched. Every compiled field is
#   allow_none=True, and marshmallow does not run validators on a None
#   anyway, but an *optional* declared parameter legitimately arrives
#   as one -- the shipped client sends `"source_url": null` and
#   `"nvram_template": null` on every upload and every instance create
#   -- so a validator which refused it would refuse the API's dominant
#   caller. Required-ness is step 3's business and is decided before
#   any of this runs.
# * only marshmallow.ValidationError escapes. _schema_findings() has a
#   broad except for exactly this failure, but a validator which
#   reached it would report *no* findings for the whole schema, which
#   would silently disable every other check on the same request.
# * the check is written to be no narrower than the handler already is.
#   That is the whole risk of this step: a validator stricter than its
#   handler is a breaking change dressed as a correctness fix, and unit
#   tests will not show it. Each comment below records what was read to
#   establish the width.


def _invalid(what: str) -> marshmallow.ValidationError:
    return marshmallow.ValidationError('Not %s.' % what)


def _format_byte(value: Any) -> Any:
    """base64, strictly, once whitespace has been taken out.

    Issue 3269: `user_data` is the only declaration carrying this
    format, and its only consumer is the `base64.b64decode()` in
    Instance._make_config_drive_openstack_disk(), which runs on the
    hypervisor long after the API has answered 200 and the instance has
    been scheduled and placed. A caller who pasted raw cloud-config
    instead of encoding it got a binascii.Error in a daemon log and an
    instance which never booted.

    Two properties are wanted at once, and the order of the two
    operations here is what gets both.

    Wrapped base64 has to be accepted. `base64 somefile` emits 76
    column lines, and `sf-client instance create -U "$(base64
    user-data.yaml)"` sends them newlines and all, so a check which
    refuses embedded whitespace refuses input the config drive decodes
    happily today.

    Raw cloud-config has to be refused, which is the whole of #3269.
    b64decode's *default* discards every character outside the
    alphabet, so it does not merely tolerate the newlines: it throws
    away the colons, hashes and spaces in a raw payload too and then
    decodes whatever alphabet characters are left. Whether that raises
    is a coin flip on the filtered length modulo 4 -- of five realistic
    raw cloud-config samples, two decoded without complaint and passed.
    An earlier revision of this validator argued for `validate=False`
    on the strength of the wrapping alone; that reasoning was wrong,
    because it read the default's whitespace tolerance as the only
    thing the default does.

    So strip the whitespace first and then decode with `validate=True`.
    Wrapped base64 survives the strip and decodes; raw cloud-config
    reaches a strict decoder with its non-alphabet characters intact
    and is refused deterministically. The only input this is narrower
    than the config drive on is one carrying non-alphabet junk which
    happens to survive the filter -- which is the defect, not a use.
    """
    if value is None:
        return value
    if not isinstance(value, str):
        raise _invalid('a valid base64 string')
    try:
        # binascii.Error subclasses ValueError.
        base64.b64decode(''.join(value.split()), validate=True)
    except Exception as e:
        raise _invalid('a valid base64 string') from e
    return value


def _format_netblock(value: Any) -> Any:
    """A CIDR netblock, parsed the way NetworksEndpoint.post() parses it.

    The handler already calls ipaddress.ip_network() and answers 400 on
    a ValueError, so this is the same function on the same input and
    cannot be narrower. It is worth compiling anyway: ARGTYPES
    documents at length why netblock carries no pattern, and this is
    what it carries instead.

    ip_network() accepts IPv6 and refuses a netblock with host bits
    set, both of which are the handler's behaviour today. The handler's
    additional "below the minimum size of /29" check stays where it is;
    that is a policy about how small a network may be, not a statement
    about what parses, and D34 keeps handler guards in place regardless.
    """
    if value is None:
        return value
    if not isinstance(value, str):
        raise _invalid('a valid CIDR netblock')
    try:
        ipaddress.ip_network(value)
    except Exception as e:
        raise _invalid('a valid CIDR netblock') from e
    return value


def _format_ip_address(value: Any) -> Any:
    """An IP address.

    The sole declaration is NetworkDNSAddressEndpoint.post()'s `value`,
    which the handler does not check at all: it goes into the network's
    hosteddns attribute and is rendered straight into dnsmasq's hosts
    file (`{{value}} {{name}}` in dnshosts.tmpl). Anything which is not
    an address is a malformed hosts file on a network node.

    ip_address() rather than IPv4Address, even though the published
    format says IPv4: a hosts file takes an IPv6 address perfectly
    well, so refusing one would be this layer inventing a restriction
    the server does not have. Publishing narrower than the server
    accepts is a documentation wart; enforcing narrower than the server
    accepts is a broken API.
    """
    if value is None:
        return value
    if not isinstance(value, str):
        raise _invalid('a valid IP address')
    try:
        ipaddress.ip_address(value)
    except Exception as e:
        raise _invalid('a valid IP address') from e
    return value


def _format_url(value: Any) -> Any:
    """A URL, in the widest sense the five declarations share.

    This one is almost all comment, because the obvious validator is
    wrong. Demanding a scheme, or an http/https scheme, would refuse
    input every one of the five handlers accepts today:

    * ArtifactsEndpoint.post's `url` takes an image *shortcut* --
      `cirros`, `cirros:0.4.0`, `ubuntu:20.04` -- which
      images._resolve_image() expands into a download URL. The
      functional suite's `artifact cache cirros` sends exactly that,
      and `cirros` has no scheme at all.
    * InstancesEndpoint.post's `nvram_template` takes `label:<name>` or
      `sf://blob/<uuid>`, and passes anything else through to
      Blob.from_db() unchanged.
    * ArtifactUploadEndpoint.post's `source_url` is "the URL the
      artifact should claim to be downloaded from", and its own default
      is `sf://upload/<namespace>/<name>`.
    * only jwks_uri on the two issuer endpoints is a real web URL, and
      _validate_issuer_arguments() already refuses anything which is
      not https there -- in the handler, so it holds in every
      API_VALIDATION_MODE.

    So the check is the widest one which is still a check: the string
    must be something urllib.parse can parse. In practice that refuses
    an unbalanced bracket in the authority (`http://[::1`) and nothing
    else, which is the stdlib's own definition of "this is not a URL"
    and is malformed under RFC 3986 by any reading. Nothing in the
    server parses these strings today, so this is about the format
    meaning *something* rather than about a crash it prevents.
    """
    if value is None:
        return value
    if not isinstance(value, str):
        raise _invalid('a valid URL')
    try:
        urllib.parse.urlparse(value)
    except Exception as e:
        raise _invalid('a valid URL') from e
    return value


def _format_uuid(value: Any) -> Any:
    """A UUID, in any spelling uuid.UUID() accepts.

    The five declarations are blob_uuid on the label, artifact upload
    and agent put routes, upload_uuid on the artifact upload route, and
    target_uuid on the cluster operations query. Every uuid the server
    can answer with is `str(uuid.uuid4())`, and uuid.UUID() takes that
    plus the undashed, braced and urn:uuid: spellings -- so this is
    wider than anything the API hands out.

    Three of the five go straight into a from_db() whose miss is a 404,
    so for those this converts a 404 into a 400 for a string which
    could never have named an object. LabelEndpoint.post is the one
    which checks nothing at all: it hands blob_uuid to
    Artifact.add_index() and answers 200, leaving a label version
    pointing at a blob which does not exist.

    target_uuid is safe for a reason worth recording, because it is the
    one which could have been a name: every ObjectType a cluster
    operation can target comes from a `target_fields` declaration in
    schema/operations/, and all of them are ARTIFACT, INSTANCE, BLOB,
    NETWORK, INTERFACE or AGENTOPERATION. The two object types keyed by
    name rather than uuid -- namespace and node -- are not among them,
    so no caller can be naming one here.
    """
    if value is None:
        return value
    if not isinstance(value, str):
        raise _invalid('a valid UUID')
    try:
        uuid.UUID(value)
    except Exception as e:
        raise _invalid('a valid UUID') from e
    return value


# The table itself. Keys are the `format` strings ARGTYPES renders, not
# the type tokens which render them. test_format_validation.py holds
# every key to a format ARGTYPES actually renders -- retyping one there
# would otherwise turn a validator off silently -- and
# test_validation_compiler.py names the thirteen declarations covered.
#
# `a MAC address` is absent on purpose: macaddr validates through the
# anchored pattern ARGTYPES gives it (PR #4183), which _field() already
# compiles, and a second check would be a second definition of the same
# format.
_FORMATS: dict[str, Callable[[Any], Any]] = {
    'byte': _format_byte,
    'a CIDR netblock': _format_netblock,
    'an IPv4 address as a string': _format_ip_address,
    'url': _format_url,
    'uuid': _format_uuid,
}


class CompiledEndpoint:
    """The schemas one handler's declarations compile to.

    ``required_names`` is recorded here and enforced by
    ``validate_request`` in ``shakenfist/external_api/base.py`` --
    phase 6's step 3 turned that on, once step 2 had corrected every
    declaration a handler's own defaults had quietly made optional.

    Enforcement is a property of the decorator, not of this compiler:
    a declaration can still be more required than its handler is.
    ``command_line`` on ``InstanceAgentExecuteEndpoint.post`` is one --
    declared required, but an omission has always reached a handler
    which queues an ``execute`` operation carrying a null
    ``commandline`` (the comment at the declaration, and
    ``instance.py:2097``). Enforcement changes what such an omission
    answers -- 400, naming the parameter, instead of a 200 the guest
    agent can only fail on -- without touching the handler, which still
    behaves the old way under ``warn`` and ``off`` (decision D34).
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


def _field(spec: dict[str, Any], required: bool = False) -> fields.Field[Any]:
    """One rendered parameter or property as a marshmallow field.

    Every field is nullable, and every *parameter* is optional. Nullable
    because a JSON null reaches the handler as None today and several
    handlers treat that as "not supplied" -- rejecting it would be a
    behaviour change invented by the compiler rather than described by
    a declaration, and in warn-only it would fill the log with findings
    that are artefacts of this module. Optional because at the top
    level required-ness is metadata here (see CompiledEndpoint), which
    phase 6 enforces itself so that it can say which parameter is
    missing in its own words.

    `required` is the one exception, and only the object branch below
    passes it: a property inside a rendered object fragment has no
    CompiledEndpoint to carry its required-ness as metadata, so a
    `required` list on the fragment is honoured by marshmallow
    directly. That is still a check and never a coercion -- a missing
    property becomes a finding naming its path, exactly as a
    wrong-typed one does.
    """
    kwargs: dict[str, Any] = {'required': required, 'allow_none': True}

    validators: list[Any] = []
    minimum, maximum = spec.get('minimum'), spec.get('maximum')
    if minimum is not None or maximum is not None:
        validators.append(validate.Range(min=minimum, max=maximum))
    pattern = spec.get('pattern')
    if pattern is not None:
        # fullmatch rather than validate.Regexp's re.match: Python's $
        # also matches before a trailing newline, ECMA-262's does not,
        # so re.match('^...$', 'value\n') accepts what the published
        # JSON Schema pattern refuses. fullmatch requires the pattern
        # to consume the whole string, which is the strict reading both
        # dialects share for a ^...$-anchored pattern -- and anchoring
        # is required at import time.
        compiled_pattern = re.compile(pattern)

        def _fullmatch(value: Any,
                       _re: 're.Pattern[str]' = compiled_pattern) -> Any:
            if not isinstance(value, str) or _re.fullmatch(value) is None:
                raise marshmallow.ValidationError(
                    'String does not match expected pattern.')
            return value

        validators.append(_fullmatch)

    # An `enum` in the rendered fragment becomes a membership check,
    # for the same reason a `pattern` does and under the same rule: the
    # published document and the enforced check are the same structure
    # by construction, so a vocabulary entry cannot say "one of these
    # four" to a client generator and mean nothing at all to the
    # server. Phase 7's structured tokens (base.py's DISKSPEC_SCHEMA
    # and VIDEOSPEC_SCHEMA) are the first fragments in the tree to
    # publish one; decision D43 is the rule which decides that a key
    # gets an enum, and it is deliberately met by only three of them.
    #
    # marshmallow runs no validator on a null, so an enum does not
    # fire on the explicit nulls the shipped clients send for a disk's
    # bus and type -- which is the property that lets D43 publish an
    # enum on a key whose dominant value is null.
    #
    # Dropped again by the object, `any` and unrecognised-type branches
    # below along with the rest of the validator list, for the reason
    # each of them records: those compile to fields which do not
    # coerce, so a validator would meet a value of whatever Python type
    # the caller happened to send.
    enum = spec.get('enum')
    if isinstance(enum, list):
        validators.append(validate.OneOf(enum))

    declared = spec.get('type')
    if not isinstance(declared, str):
        declared = ''

    # The semantic formats (D33). Gated on the rendered type being
    # `string` because every one of them is a check on a string, and a
    # token which one day renders the same format on a different type
    # would otherwise get a validator written for text. The `any`
    # sentinel and the unrecognised-type fallback below both drop the
    # validator list wholesale for the same reason Range is dropped
    # there: fields.Raw does not coerce, so a validator would meet a
    # value of whatever Python type the caller sent.
    if declared == 'string':
        published_format = spec.get('format')
        if isinstance(published_format, str):
            semantic = _FORMATS.get(published_format)
            if semantic is not None:
                validators.append(semantic)

    if validators:
        kwargs['validate'] = validators

    if declared == 'array':
        # items is always present: swagger_helper() renders the array
        # tokens with it, and OpenAPI 2.0 requires it.
        return fields.List(_field(spec.get('items', {})), **kwargs)

    element_properties = spec.get('properties')
    if isinstance(element_properties, dict):
        # A structured object fragment (phase 7): compile its
        # properties into a nested schema so a value one level down is
        # checked the way a top level one is.
        #
        # Keyed on the presence of `properties` rather than on the type
        # being `object`, which is the whole of decision D47.
        # ARGTYPES['dict'] renders a bare {'type': 'object'} and two
        # live parameters need that to keep meaning "any mapping at
        # all": `metadata` on POST /instances, whose keys are the
        # caller's and which deliberately stores both a dict and a list
        # as the *value* under one caller-chosen key (sfcbr's k3s
        # traffic does exactly that), and `bound_claims` on the mapping
        # rule endpoints, which is guarded by hand in federation.py
        # with messages better than a schema could produce. A branch
        # keyed on the type would have refused both.
        #
        # Reading the rendered fragment rather than knowing anything
        # about type tokens is the module's standing rule (see the
        # module docstring and _FORMATS): the published specification
        # and the enforced check are the same structure by
        # construction, so a token cannot render one shape and compile
        # as another.
        required_properties = spec.get('required') or []
        nested = marshmallow.Schema.from_dict(
            {name: _field(prop, required=name in required_properties)
             for name, prop in element_properties.items()})

        # marshmallow's own default for a schema is RAISE, so the
        # EXCLUDE arm has to be written out: without it every
        # structured token would refuse unknown keys whether it
        # published additionalProperties: false or not, and the
        # published specification would be saying something the server
        # does not do. `is False` rather than a truthiness test because
        # JSON Schema also spells additionalProperties as a *schema*,
        # and only the literal false means "no other keys".
        #
        # Annotated because mypy widens marshmallow's two Literal
        # constants to str across a conditional, and Schema's own
        # parameter is the Literal.
        unknown: marshmallow.types.UnknownOption = (
            marshmallow.RAISE
            if spec.get('additionalProperties') is False
            else marshmallow.EXCLUDE)

        # Bounds and formats are dropped, for the same reason the `any`
        # and unrecognised-type branches below drop them: a
        # validate.Range meeting a mapping raises TypeError rather than
        # ValidationError, straight out through schema.validate() and
        # into _schema_findings' broad except, which reports *no*
        # findings for the whole request and so silently disables every
        # other check on it. _validated_constraints() already refuses a
        # minimum, maximum or pattern on anything but a numeric or
        # string type at import time, so none can arrive here today;
        # this keeps that true if a future token renders one itself.
        kwargs.pop('validate', None)
        return fields.Nested(nested(unknown=unknown), **kwargs)

    if 'type' not in spec and spec.get('format') == ANY_VALUE_FORMAT:
        # The 'any' token, which is typeless deliberately rather than
        # by omission. Without this branch it fell into the fallback
        # below and logged "unrecognised type" once per site at every
        # sf-api start -- fourteen warnings about a token the
        # vocabulary defines, which is noise, and worse, it made a
        # genuinely unrecognised token indistinguishable from expected
        # noise. That warning is the only signal the fallback produces,
        # so anything expected has to be kept out of it.
        #
        # Keyed on the absence of the `type` key rather than on
        # `declared` being empty: the normalisation above flattens an
        # unreadable type to '' too, and a schema whose type is present
        # but not a string should still reach the warning. Absent and
        # unreadable are different facts, and only the first is
        # deliberate.
        #
        # Bounds are dropped for the same reason the fallback drops
        # them: Raw does not coerce, so a Range or Regexp validator
        # would raise TypeError from inside schema.validate() the
        # moment it met a value of the wrong Python type.
        # `_validated_constraints` already refuses a bound on a
        # typeless token at import time, so 'any' carries none today;
        # this keeps the next typeless token from depending on that.
        kwargs.pop('validate', None)
        return fields.Raw(**kwargs)

    field_class = _SCALARS.get(declared)
    if field_class is None:
        # A type this does not know is documented rather than enforced.
        # Raw accepts anything, which keeps an unrecognised token from
        # silently becoming a rejection -- the failure mode phase 2's
        # netblock reasoning is about. Its bounds are dropped too: Raw
        # deserialises without coercing, so a Range or Regexp validator
        # would raise TypeError from inside schema.validate() the
        # moment it met a value of the wrong Python type, and an
        # unrecognised type cannot meaningfully carry a bound anyway.
        LOG.with_fields({'type': declared}).warning(
            'Unrecognised parameter type in the published specification; '
            'compiled as unvalidated')
        kwargs.pop('validate', None)
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
    compiled_classes: dict[str, type] = {}
    for view in app.view_functions.values():
        cls = getattr(view, 'view_class', None)
        if cls is None:
            continue
        # The key is the bare class name, because that is all the
        # validating decorator can reconstruct at dispatch. Two
        # endpoint classes sharing a name in different modules would
        # therefore silently validate one endpoint's requests against
        # the other's schema, and the completeness test collapses the
        # duplicate on both sides of its comparison -- so the collision
        # is refused here, loudly, at mount time.
        seen = compiled_classes.get(cls.__name__)
        if seen is not None and seen is not cls:
            raise exceptions.InvalidAPIDeclaration(
                'two endpoint classes named %s are mounted (%s and %s); '
                'the validation registry is keyed by class name and '
                'cannot tell their requests apart'
                % (cls.__name__, seen.__module__, cls.__module__))
        compiled_classes[cls.__name__] = cls
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
# Checking.
#
# Nothing below rejects anything: check() is pure and returns findings,
# and validate_request in base.py is the only place which decides what
# to do with them. The findings are recorded on flask.g and emitted
# once the response status is known, because "what did this request
# return anyway" is what separates a rejection enforcement introduced
# from a status code it merely changed -- and at validation time that
# is not yet known.

# The request-scoped hand-offs, named like base.py's
# _RECORDED_EXCEPTION_FIELDS because they are the same pattern.
# PARSED_BODY carries the body log_request parsed and merged, so the
# validator reports on exactly what the handler will receive rather
# than on a re-read of the request -- and so a body which is not JSON
# is not paid for twice.
VALIDATION_FINDINGS = 'sf_validation_findings'
BODY_PATH_COLLISIONS = 'sf_body_path_collisions'
PARSED_BODY = 'sf_parsed_body'

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

# The most type-mismatch findings one schema check can produce. A
# container's elements are each reported separately (see
# _flatten_messages), so a single declared parameter carrying a large
# enough array puts one log line per bad element into centralised
# logging -- exactly the property MAX_UNKNOWN_PARAMETER_FINDINGS exists
# to bound, arriving by a different route once phase 4 taught the
# flattener to name elements. Nothing bounds a request body's size, so
# a thousand-element disk list is a thousand findings without this.
# The overflow is summarised in one finding carrying the count, in the
# same shape as the unknown-parameter overflow above.
MAX_TYPE_MISMATCH_FINDINGS = 20


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
        # Truncated *and* stripped of non-printables: the length bound
        # alone would still let a newline in a key forge extra fields
        # in a log line or, in enforce mode, in the response. The JSON
        # formatter escapes these anyway, so this is defence in depth
        # rather than the only wall.
        self.parameter = ''.join(
            c for c in parameter if c.isprintable())[:MAX_PARAMETER_NAME]
        self.detail = detail
        self.value_type = type(value).__name__ if value is not None else None

    def fields(self) -> dict[str, Any]:
        return {
            'validation-reason': self.reason,
            'validation-parameter': self.parameter,
            'validation-detail': self.detail,
            'validation-value-type': self.value_type,
        }


def _sort_key(key: Any) -> tuple[int, int, str]:
    """Order marshmallow's error keys the way a caller reads them.

    Sequence indices are integers and must stay in numeric order:
    sorting them as strings gives 0, 1, 10, 11, 2, and since
    enforcement answers with the first finding only, a caller whose
    third and eleventh disk specifications are both malformed would be
    told about `disk[10]`. Integers sort ahead of sub-field names,
    which cannot collide with them because marshmallow keys a mapping
    error by field name and a sequence error by index.
    """
    if isinstance(key, int):
        return (0, key, '')
    return (1, 0, str(key))


def _flatten_messages(parameter: str, messages: Any,
                      path: tuple[Any, ...] = ()
                      ) -> list[tuple[str, str, tuple[Any, ...]]]:
    """Flatten marshmallow's error structure into (name, detail, path) leaves.

    validate() answers a list of strings when a field itself is wrong,
    but a dict keyed by index or sub-field when a *container's elements*
    are wrong -- `{0: ['Not a valid mapping type.']}` for a bad entry in
    an arrayofdict. Rendering that with str() puts a Python dict repr in
    the detail, which the enforce flip of phase 4 turned from a log line
    into the caller's error message: `disk: {0: ['Not a valid mapping
    type.']}`. Naming the offending element instead gives
    `disk[0]: Not a valid mapping type.`

    Integer keys index a sequence and render as `name[0]`; anything else
    is a sub-field and renders as `name.key`. The recursion terminates
    because marshmallow's leaves are always lists of strings, and the
    non-list, non-dict branch keeps a malformed leaf from raising inside
    the validator -- which _schema_findings' caller must never do.

    Each leaf also carries the sequence of keys walked to reach it, so
    the caller can index back into the supplied value and report the
    *element's* type rather than the container's. That path is the
    only reason a finding built from a leaf can say anything true
    about the value at all, since the parameter name has by then been
    rewritten into a display form which cannot be looked up.
    """
    if isinstance(messages, dict):
        flattened = []
        for key, nested in sorted(messages.items(),
                                  key=lambda kv: _sort_key(kv[0])):
            if isinstance(key, int):
                name = '%s[%d]' % (parameter, key)
            else:
                name = '%s.%s' % (parameter, key)
            flattened.extend(_flatten_messages(name, nested, path + (key,)))
        return flattened
    if isinstance(messages, list):
        return [(parameter, '; '.join(str(m) for m in messages), path)]
    return [(parameter, str(messages), path)]


def _element_value(container: Any, path: tuple[Any, ...]) -> Any:
    """The value at `path` inside `container`, or the container itself.

    A finding carries the offending value's type and never its value
    (decision D5), so this exists purely to make that type describe
    what actually failed: `disk[0]` reported as a `list` -- the type of
    the whole array -- is the telemetry being confidently wrong about
    the one case element naming was added for.

    Falls back to the container when the path does not resolve, which
    is the honest answer rather than None: marshmallow can key an error
    by a name the supplied value has no entry for (a required sub-field
    that is absent), and "the container's type" is still true of
    something the caller sent.
    """
    value = container
    for key in path:
        try:
            value = value[key]
        except (TypeError, KeyError, IndexError):
            return container
    return value


def _schema_findings(schema: Optional[marshmallow.Schema],
                     supplied: dict[str, Any]) -> list[Finding]:
    if schema is None:
        return []
    known = {name: value for name, value in supplied.items()
             if name in schema.fields}
    try:
        mismatches = schema.validate(known)
    except Exception:
        # A warn-only layer must never raise from inside itself, and
        # marshmallow does not catch everything a validator can throw
        # (a Range validator meeting a value of an uncoercible Python
        # type raises TypeError straight through validate()). No
        # findings is the honest answer when the check itself failed;
        # the log line is what stops a compiler defect hiding forever.
        LOG.with_fields({
            'parameters': sorted(known)}).exception(
            'Request validation raised internally; no findings reported')
        return []
    leaves = []
    for parameter, messages in mismatches.items():
        for name, detail, path in _flatten_messages(str(parameter), messages):
            leaves.append((name, detail, parameter, path))

    # Sliced before the values are resolved, so the elements past the
    # bound cost a tuple each and nothing more. marshmallow has already
    # built its own entry per bad element by this point, so the leaves
    # add no order of magnitude to what a large body costs; what the
    # bound is protecting is the log stream, where each finding is a
    # line.
    findings = [Finding(TYPE_MISMATCH, name, detail,
                        _element_value(known.get(parameter), path))
                for name, detail, parameter, path
                in leaves[:MAX_TYPE_MISMATCH_FINDINGS]]
    if len(leaves) > MAX_TYPE_MISMATCH_FINDINGS:
        findings.append(Finding(
            TYPE_MISMATCH, '(overflow)',
            '%d further mismatching values not reported individually'
            % (len(leaves) - MAX_TYPE_MISMATCH_FINDINGS)))
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
    # handler is variadic, so Python raises TypeError. Counting these
    # is how phase 4 chose between webargs' EXCLUDE and RAISE
    # (decision D10). Until phase 5 that TypeError was caught by a
    # broad except in handle_authorization_exceptions and returned as
    # a 400 carrying interpreter text; that arm is gone (decision
    # D23), so in 'warn' and 'off' -- the only modes where such a key
    # still reaches a handler -- it is now a recorded 500 (D25).
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

    # An explicit JSON null counts as missing too, for a required
    # parameter only: every compiled field is allow_none=True (see
    # _field()), so a null reaches the schema check indistinguishable
    # from a value that just happens to be absent, and a caller who
    # sent `{"key": null}` gets exactly the same answer as one who
    # sent no `key` at all -- one reason code, one message, for what
    # is one fact from the handler's side: it was not given a key.
    supplied = dict(body)
    supplied.update(query)
    for name in sorted(compiled.required_names):
        if name in compiled.path_names:
            continue
        if name not in supplied or supplied[name] is None:
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
