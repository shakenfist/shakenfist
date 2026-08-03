# Copyright 2019 Michael Still and contributors
import ast
import collections
import glob
import os
import re

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
    """The (name, location) pairs a handler's swagger_helper declares."""
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
                continue
            try:
                location = ast.literal_eval(item.elts[1])
            except ValueError:
                continue
            try:
                name = ast.literal_eval(item.elts[0])
            except ValueError:
                # A constant reference such as api_base.RAW_BODY_PARAMETER.
                name = getattr(api_base, ast.unparse(item.elts[0]).split('.')[-1],
                               None)
            out.append((name, location))
    return out


def _handler_kwargs(fn):
    return [a.arg for a in fn.args.args
            if a.arg != 'self' and not a.arg.endswith(INJECTED_SUFFIX)]


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
                out[cls] |= set(re.findall(r'<([a-z_]+)>',
                                           ast.literal_eval(route)))
            except ValueError:
                pass
    return out


def _endpoints():
    """Yield (class name, method name, FunctionDef) for every handler."""
    for path in sorted(glob.glob(os.path.join(API_DIR, '*.py'))):
        tree = ast.parse(open(path).read())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                if fn.name in HANDLER_METHODS:
                    yield cls.name, fn.name, fn


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
        for cls, method, fn in _endpoints():
            declared = _declared_parameters(fn)
            if not declared:
                continue
            accepted = _handler_kwargs(fn)
            for name, _ in declared:
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
        for cls, method, fn in _endpoints():
            declared = _declared_parameters(fn)
            if not declared:
                continue
            names = {n for n, _ in declared}
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
        for cls, method, fn in _endpoints():
            for name, location in _declared_parameters(fn):
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
        for cls, method, fn in _endpoints():
            for name, location in _declared_parameters(fn):
                self.assertIn(
                    location, api_base.SWAGGER_PARAMETER_LOCATIONS,
                    '%s.%s declares %r in %r, which is not an OpenAPI 2.0 '
                    'parameter location' % (cls, method, name, location))

    def test_injected_objects_are_not_declared(self):
        """The decorators' database objects are not part of the API."""
        for cls, method, fn in _endpoints():
            for name, _ in _declared_parameters(fn):
                self.assertFalse(
                    name and name.endswith(INJECTED_SUFFIX),
                    '%s.%s declares %r, which is injected by a decorator '
                    'rather than sent by a caller' % (cls, method, name))

    def test_every_endpoint_declares_its_parameters(self):
        """A handler taking parameters must document them at all."""
        undocumented = []
        for cls, method, fn in _endpoints():
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
