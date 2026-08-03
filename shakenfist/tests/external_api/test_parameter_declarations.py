# Copyright 2019 Michael Still and contributors
import ast
import collections
import glob
import os
import re

from shakenfist import exceptions
from shakenfist.external_api import base as api_base
from shakenfist.tests import base


API_DIR = os.path.dirname(
    os.path.abspath(api_base.__file__))
HANDLER_METHODS = ('get', 'post', 'put', 'delete', 'patch')

# Objects the decorators inject into the handler's kwargs. They are not
# request parameters and must never be declared or validated.
INJECTED_SUFFIX = '_from_db'

# Handler kwargs which are deliberately not part of the published API.
#
# The metadata delete endpoints accept `value` and none of them read it.
# It should be removed from the signatures rather than documented, but not
# until phase 4 of PLAN-api-input-validation: today, removing it means a
# caller who sends it gets `delete() got an unexpected keyword argument`
# as a 400, which is the leak that plan exists to remove. Once the schema
# layer rejects unknown parameters cleanly, this list should be empty.
UNDECLARED_BY_DESIGN = {
    ('ArtifactMetadataEndpoint', 'delete', 'value'),
    ('AuthMetadataEndpoint', 'delete', 'value'),
    ('BlobMetadataEndpoint', 'delete', 'value'),
    ('InterfaceMetadataEndpoint', 'delete', 'value'),
    ('NodeMetadataEndpoint', 'delete', 'value'),
}


def _declared_parameters(fn):
    """The (name, location, required) triples a handler declares.

    A declaration which cannot be evaluated statically is returned with
    None in the offending position rather than skipped, so that a
    declaration this test cannot read fails it instead of silently
    escaping every assertion below.
    """
    out = []
    for dec in fn.decorator_list:
        if 'swagger_helper' not in ast.unparse(dec):
            continue
        call = dec.args[0] if isinstance(dec, ast.Call) and dec.args else None
        if not (isinstance(call, ast.Call) and len(call.args) >= 3):
            continue
        if not isinstance(call.args[2], ast.List):
            continue
        for item in call.args[2].elts:
            if not (isinstance(item, ast.Tuple) and len(item.elts) >= 2):
                out.append((None, None, None))
                continue
            out.append((_literal(item.elts[0]), _literal(item.elts[1]),
                        _literal(item.elts[4]) if len(item.elts) > 4 else None))
    return out


def _literal(node):
    """A declaration element's value, or None if it is not static.

    api_base constants are resolved, since RAW_BODY_PARAMETER is
    referenced rather than spelled out.
    """
    try:
        return ast.literal_eval(node)
    except ValueError:
        return getattr(api_base, ast.unparse(node).split('.')[-1], None)


def _handler_kwargs(fn):
    """Every parameter a caller could populate, keyword-only included."""
    args = list(fn.args.args) + list(fn.args.kwonlyargs)
    return [a.arg for a in args
            if a.arg != 'self' and not a.arg.endswith(INJECTED_SUFFIX)]


def _query_schema_keys(cls_node):
    """Names a class parses from the query string with webargs."""
    out = set()
    for node in ast.walk(cls_node):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, 'id', '') == 'get_args' for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            for key in node.value.keys:
                value = _literal(key)
                if value is not None:
                    out.add(value)
    return out


def _request_args_keys(fn):
    """Names a handler reads straight out of flask.request.args."""
    out = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get'
                and _is_request_args(node.func.value)
                and node.args):
            key = _literal(node.args[0])
        elif isinstance(node, ast.Subscript) and _is_request_args(node.value):
            key = _literal(node.slice)
        else:
            continue
        if key is not None:
            out.add(key)
    return out


def _is_request_args(node):
    """Is this node ``request.args``, however ``request`` was imported?"""
    return (isinstance(node, ast.Attribute) and node.attr == 'args'
            and ast.unparse(node.value).split('.')[-1] == 'request')


def _route_parameters():
    """Path parameter names per endpoint class, from the mounted routes."""
    out = collections.defaultdict(set)
    tree = ast.parse(open(os.path.join(API_DIR, 'app.py')).read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, 'attr', '') != 'add_resource':
            continue
        cls = ast.unparse(node.args[0]).split('.')[-1]
        for route in node.args[1:]:
            try:
                # Routes may name a converter, as in <path:label_name>
                # or <int(min=1):x>; the parameter name follows the
                # last colon. An earlier version of this regex matched
                # only bare names and so silently skipped three
                # LabelEndpoint declarations.
                out[cls] |= {segment.split(':')[-1] for segment
                             in re.findall(r'<([^>]+)>',
                                           ast.literal_eval(route))}
            except ValueError:
                pass
    return out


def _endpoints():
    """Yield (class name, method name, FunctionDef, ClassDef) per handler."""
    for path in sorted(glob.glob(os.path.join(API_DIR, '*.py'))):
        tree = ast.parse(open(path).read())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                if fn.name in HANDLER_METHODS:
                    yield cls.name, fn.name, fn, cls


class ParameterDeclarationTestCase(base.ShakenFistTestCase):
    """Endpoint parameter declarations must describe the real API.

    These declarations were documentation-only for years and drifted
    accordingly: 116 path parameters declared as query or body, five
    parameters named something the handler could not receive, and seven
    accepted but never documented. Phase 3 of PLAN-api-input-validation
    compiles them into request validation schemas, at which point drift
    stops being a documentation bug and starts rejecting valid requests.

    Structural assertions against the AST and the route table, so they
    describe the contract rather than the source text.
    """

    def test_declared_names_are_real_parameters(self):
        """A declared name must be something the handler can receive."""
        for cls, method, fn, _ in _endpoints():
            declared = _declared_parameters(fn)
            if not declared:
                continue
            accepted = _handler_kwargs(fn)
            for name, _, _ in declared:
                if name == api_base.RAW_BODY_PARAMETER:
                    # Documents the raw request body, read from
                    # flask.request rather than passed as a kwarg.
                    continue
                self.assertIn(
                    name, accepted,
                    '%s.%s declares parameter %r, which it cannot receive. '
                    'Its parameters are: %s'
                    % (cls, method, name, ', '.join(sorted(accepted))))

    def test_accepted_parameters_are_declared(self):
        """A parameter a caller can send must appear in the published API."""
        for cls, method, fn, _ in _endpoints():
            declared = _declared_parameters(fn)
            if not declared:
                continue
            names = {n for n, _, _ in declared}
            for kwarg in _handler_kwargs(fn):
                if (cls, method, kwarg) in UNDECLARED_BY_DESIGN:
                    continue
                self.assertIn(
                    kwarg, names,
                    '%s.%s accepts %r but does not declare it, so it is '
                    'invisible in the published API. Declare it, stop '
                    'accepting it, or add it to UNDECLARED_BY_DESIGN with a '
                    'reason.' % (cls, method, kwarg))

    def test_path_parameters_are_declared_as_path(self):
        """Location decides where a parser looks for a value."""
        routes = _route_parameters()
        for cls, method, fn, _ in _endpoints():
            for name, location, _ in _declared_parameters(fn):
                if name not in routes.get(cls, set()):
                    continue
                self.assertEqual(
                    'path', location,
                    '%s.%s declares %r in %r, but it is a path parameter of '
                    'the route %s is mounted on'
                    % (cls, method, name, location, cls))

    def test_declared_locations_are_valid(self):
        """swagger_helper enforces this at import time; pin it here too, so
        the reason a location is rejected is visible in a test failure
        rather than only in an import traceback."""
        for cls, method, fn, _ in _endpoints():
            for name, location, _ in _declared_parameters(fn):
                self.assertIn(
                    location, api_base.SWAGGER_PARAMETER_LOCATIONS,
                    '%s.%s declares %r in %r, which is not an OpenAPI 2.0 '
                    'parameter location' % (cls, method, name, location))

    def test_query_parameters_are_declared_as_query(self):
        """Two ways of reading the query string, one declaration.

        Three endpoints carrying @use_kwargs(get_args, location='query')
        were given `all` declared as a body parameter. webargs updates
        kwargs from the query string after log_request has merged the
        body, so a caller following that documentation would have had
        their value silently overwritten by the default.

        ClusterOperationsEndpoint.get reads its target parameters from
        flask.request.args directly, as a fallback behind the body
        merge, and AGENTS.md documents the query-string form. Declaring
        those `body` cost nothing today but would have told phase 3 to
        drop a fallback the handler deliberately implements: decision D6
        grants a query-string fallback only to parameters declared
        `query`. Either way of reading the query string means the
        declaration says `query`.
        """
        for cls, method, fn, cls_node in _endpoints():
            query_keys = _query_schema_keys(cls_node) | _request_args_keys(fn)
            if not query_keys:
                continue
            for name, location, _ in _declared_parameters(fn):
                if name not in query_keys:
                    continue
                self.assertEqual(
                    'query', location,
                    '%s.%s reads %r from the query string but declares it '
                    'in %r' % (cls, method, name, location))

    def test_path_parameters_are_required(self):
        """OpenAPI 2.0 requires it: the route cannot match without them.

        swagger_helper() enforces this at import time; asserted here so a
        failure names the endpoint rather than arriving as an import
        error during test collection.
        """
        for cls, method, fn, _ in _endpoints():
            for name, location, required in _declared_parameters(fn):
                if location != 'path':
                    continue
                self.assertIs(
                    True, required,
                    '%s.%s declares path parameter %r as required=%r; a path '
                    'parameter must be required or the specification is '
                    'invalid' % (cls, method, name, required))

    def test_declarations_are_statically_readable(self):
        """Every declaration must be one this test can actually check.

        The assertions above are only worth anything if they see every
        declaration. A tuple that cannot be evaluated statically would
        otherwise slip past all of them.
        """
        for cls, method, fn, _ in _endpoints():
            for name, location, _ in _declared_parameters(fn):
                self.assertIsNotNone(
                    name, '%s.%s has a parameter declaration whose name is '
                    'not a literal or an api_base constant' % (cls, method))
                self.assertIsNotNone(
                    location, '%s.%s parameter %r has a location which is not '
                    'a literal' % (cls, method, name))

    def test_no_handler_takes_a_kwarg_named_body(self):
        """RAW_BODY_PARAMETER is matched by value, so a handler kwarg of
        the same name would be silently skipped by
        test_declared_names_are_real_parameters."""
        for cls, method, fn, _ in _endpoints():
            self.assertNotIn(
                api_base.RAW_BODY_PARAMETER, _handler_kwargs(fn),
                '%s.%s takes a kwarg named %r, which collides with the '
                'raw-body sentinel'
                % (cls, method, api_base.RAW_BODY_PARAMETER))

    def test_injected_objects_are_not_declared(self):
        """The decorators' database objects are not part of the API."""
        for cls, method, fn, _ in _endpoints():
            for name, _, _ in _declared_parameters(fn):
                self.assertFalse(
                    name and name.endswith(INJECTED_SUFFIX),
                    '%s.%s declares %r, which is injected by a decorator '
                    'rather than sent by a caller' % (cls, method, name))

    def test_every_endpoint_declares_its_parameters(self):
        """A handler taking parameters must document them at all."""
        undocumented = []
        for cls, method, fn, _ in _endpoints():
            if _declared_parameters(fn):
                continue
            if _handler_kwargs(fn):
                undocumented.append('%s.%s' % (cls, method))

        # Endpoints which take no parameters legitimately have no
        # declaration; ones which take parameters must have one, or the
        # compiled schema in phase 3 has nothing to work from.
        self.assertEqual(
            [], undocumented,
            'these handlers accept parameters but declare none: %s'
            % ', '.join(undocumented))


class SwaggerHelperValidationTestCase(base.ShakenFistTestCase):
    """swagger_helper() rejects a malformed declaration at import time.

    The tree-scanning assertions above cannot cover this: importing this
    module imports every endpoint, so a bad declaration anywhere aborts
    collection before any assertion runs. That makes those assertions a
    tautology with respect to the enforcement itself, which is the
    mechanism the whole plan relies on. Test it directly.
    """

    def _helper(self, parameters):
        return api_base.swagger_helper(
            'test', 'A test endpoint.', parameters,
            [(200, 'No return value', '')])

    def test_valid_declaration_renders(self):
        out = self._helper([('thing', 'body', 'string', 'A thing.', False)])

        declared = [p for p in out['parameters'] if p['name'] == 'thing']
        self.assertEqual(1, len(declared))
        self.assertEqual('body', declared[0]['in'])
        self.assertFalse(declared[0]['required'])

    def test_unknown_location_is_rejected(self):
        for location in ('qeury', 'post', 'BODY', ''):
            self.assertRaises(
                exceptions.InvalidAPIDeclaration, self._helper,
                [('thing', location, 'string', 'A thing.', False)])

    def test_unknown_type_is_rejected(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._helper,
            [('thing', 'body', 'stringy', 'A thing.', False)])

    def test_rejection_names_the_endpoint_and_parameter(self):
        """A malformed declaration aborts sf-api startup, so the message
        has to say which one without the reader walking the traceback."""
        for bad in (('thing', 'qeury', 'string', 'A thing.', False),
                    ('thing', 'body', 'stringy', 'A thing.', False),
                    ('thing', 'path', 'string', 'A thing.', False)):
            try:
                self._helper([bad])
            except exceptions.InvalidAPIDeclaration as e:
                self.assertIn('test', str(e))
                self.assertIn('thing', str(e))
            else:
                self.fail('%r was accepted' % (bad,))

    def test_optional_path_parameter_is_rejected(self):
        """A path parameter must be required, or the specification is
        invalid and client generators reject it."""
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._helper,
            [('thing', 'path', 'string', 'A thing.', False)])

        # The same declaration is fine when required.
        out = self._helper([('thing', 'path', 'string', 'A thing.', True)])
        self.assertEqual(
            ['path'], [p['in'] for p in out['parameters']
                       if p['name'] == 'thing'])
