# Copyright 2019 Michael Still and contributors
import ast
import importlib.util
import os
import shutil
import tempfile

from shakenfist import exceptions
from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.tests import base


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

# Handlers deliberately absent from the published API. These three are
# the unauthenticated health probes a load balancer polls, listed in
# api_base.HEALTH_PROBE_PATHS: they are part of the deployment contract
# rather than of the tenant-facing API, and documenting them would
# invite callers to treat them as such.
UNDOCUMENTED_BY_DESIGN = {
    ('Root', 'get'),
    ('Livez', 'get'),
    ('Readyz', 'get'),
}


def _endpoints():
    """Yield (class name, method name, method node) per handler."""
    for _, _, cls, fn in declarations.handlers():
        yield cls.name, fn.name, fn


class ParameterDeclarationTestCase(base.ShakenFistTestCase):
    """Endpoint parameter declarations must describe the real API.

    These declarations were documentation-only for years and drifted
    accordingly: every parameter appearing in a URL path but three was
    declared as query or body, five parameters were named something the
    handler could not receive, and twenty were accepted but never
    documented. Phase 3 of PLAN-api-input-validation compiles them into
    request validation schemas, at which point drift stops being a
    documentation bug and starts rejecting valid requests.

    Structural assertions against the AST and the route table, so they
    describe the contract rather than the source text.
    """

    def test_declared_locations_are_derivable(self):
        """The declared location must be where the value really arrives.

        The whole rule, not part of it: a name in a mounted route is in
        the path, a name in a webargs query schema or read from
        flask.request.args is in the query, and everything else is in
        the body via the log_request merge.

        Asserting only some directions leaves a hole the size of the
        untested one. An earlier version checked path-to-path and
        query-source-to-query and would have passed with `event_type`
        on the blob events endpoint declared `query`, which nothing
        reads from the query string -- exactly the drift phase 3 would
        compile into a live query-string fallback under decision D6.

        This is the same derivation tools/fix-api-parameter-locations.py
        applies, imported rather than reimplemented, so what the script
        would rewrite is what this test fails on.
        """
        drifted, _, problems = declarations.audit()

        # Unreadable input first. A route the derivation cannot read
        # makes every parameter of that class look like a body
        # parameter, so asserting the drift first reports "blob_uuid is
        # declared path but arrives in the body" -- which would send a
        # reader to change a correct declaration. The cause has to be
        # named before the symptom.
        self.assertEqual(
            [], problems,
            'the derivation could not read some of its input, so any '
            'locations derived below came from an incomplete picture')
        self.assertEqual(
            [], ['%s.%s declares %r in %r, but it arrives in the %s'
                 % (d.cls, d.method, d.name, d.location, want)
                 for d, want in drifted],
            'run tools/fix-api-parameter-locations.py --apply to correct '
            'these, or correct the handler if the declaration is right')

    def test_declared_names_are_real_parameters(self):
        """A declared name must be something the handler can receive."""
        for cls, method, fn in _endpoints():
            declared = declarations.declarations(fn)
            if not declared:
                continue
            accepted = declarations.handler_kwargs(fn)
            for parameter in declared:
                if parameter.name == api_base.RAW_BODY_PARAMETER:
                    # Documents the raw request body, read from
                    # flask.request rather than passed as a kwarg.
                    continue
                self.assertIn(
                    parameter.name, accepted,
                    '%s.%s declares parameter %r, which it cannot receive. '
                    'Its parameters are: %s'
                    % (cls, method, parameter.name, ', '.join(sorted(accepted))))

    def test_accepted_parameters_are_declared(self):
        """A parameter a caller can send must appear in the published API.

        Gated on carrying a declaration, not on declaring parameters.
        Skipping a handler whose declaration list is empty exempts the
        one shape this assertion exists to catch: a `swag_from` whose
        parameters have been emptied while the handler still accepts
        them. The three UNDOCUMENTED_BY_DESIGN handlers take no kwargs,
        so this loop is a no-op for them.
        """
        for cls, method, fn in _endpoints():
            if not declarations.documented(fn):
                continue
            names = {d.name for d in declarations.declarations(fn)}
            for kwarg in declarations.handler_kwargs(fn):
                if (cls, method, kwarg) in UNDECLARED_BY_DESIGN:
                    continue
                self.assertIn(
                    kwarg, names,
                    '%s.%s accepts %r but does not declare it, so it is '
                    'invisible in the published API. Declare it, stop '
                    'accepting it, or add it to UNDECLARED_BY_DESIGN with a '
                    'reason.' % (cls, method, kwarg))

    def test_declared_locations_are_valid(self):
        """swagger_helper enforces this at import time; pin it here too, so
        the reason a location is rejected is visible in a test failure
        rather than only in an import traceback."""
        for cls, method, fn in _endpoints():
            for parameter in declarations.declarations(fn):
                self.assertIn(
                    parameter.location, api_base.SWAGGER_PARAMETER_LOCATIONS,
                    '%s.%s declares %r in %r, which is not an OpenAPI 2.0 '
                    'parameter location'
                    % (cls, method, parameter.name, parameter.location))

    def test_path_parameters_are_required(self):
        """OpenAPI 2.0 requires it: the route cannot match without them.

        swagger_helper() enforces this at import time; asserted here so a
        failure names the endpoint rather than arriving as an import
        error during test collection.
        """
        for cls, method, fn in _endpoints():
            for parameter in declarations.declarations(fn):
                if parameter.location != 'path':
                    continue
                self.assertIs(
                    True, parameter.required,
                    '%s.%s declares path parameter %r as required=%r; a path '
                    'parameter must be required or the specification is '
                    'invalid' % (cls, method, parameter.name,
                                 parameter.required))

    def test_declarations_are_statically_readable(self):
        """Every declaration must be one this test can actually check.

        The assertions above are only worth anything if they see every
        declaration. A tuple that cannot be evaluated statically would
        otherwise slip past all of them, and so would a parameter list
        which is not a literal list of tuples.
        """
        for cls, method, fn in _endpoints():
            for parameter in declarations.declarations(fn):
                self.assertIsNotNone(
                    parameter.name,
                    '%s.%s has a parameter declaration whose name is not a '
                    'literal or an api_base constant' % (cls, method))
                self.assertIsNotNone(
                    parameter.location,
                    '%s.%s parameter %r has a location which is not a literal'
                    % (cls, method, parameter.name))

    def test_no_handler_takes_a_kwarg_named_body(self):
        """RAW_BODY_PARAMETER is matched by value, so a handler kwarg of
        the same name would be silently skipped by
        test_declared_names_are_real_parameters."""
        for cls, method, fn in _endpoints():
            self.assertNotIn(
                api_base.RAW_BODY_PARAMETER, declarations.handler_kwargs(fn),
                '%s.%s takes a kwarg named %r, which collides with the '
                'raw-body sentinel'
                % (cls, method, api_base.RAW_BODY_PARAMETER))

    def test_injected_objects_are_not_declared(self):
        """The decorators' database objects are not part of the API."""
        for cls, method, fn in _endpoints():
            for parameter in declarations.declarations(fn):
                self.assertFalse(
                    parameter.name and parameter.name.endswith(
                        declarations.INJECTED_SUFFIX),
                    '%s.%s declares %r, which is injected by a decorator '
                    'rather than sent by a caller'
                    % (cls, method, parameter.name))

    def test_every_endpoint_is_documented(self):
        """A handler with no swag_from is absent from the published API.

        Taking no parameters is not a reason to be undocumented: the
        endpoint whose absence this phase discovered by accident,
        InstanceSnapshotEndpoint.get, takes none either. Checking only
        the handlers with parameters would catch half the class of bug
        that found it.

        Carrying a declaration is the question, not declaring any
        parameters: eight endpoints correctly declare an empty list
        because they accept nothing.
        """
        undocumented = []
        for cls, method, fn in _endpoints():
            if declarations.documented(fn):
                continue
            if (cls, method) in UNDOCUMENTED_BY_DESIGN:
                continue
            undocumented.append('%s.%s' % (cls, method))

        self.assertEqual(
            [], undocumented,
            'these handlers are missing from the published API: %s. '
            'Declare them, or add them to UNDOCUMENTED_BY_DESIGN with a '
            'reason.' % ', '.join(undocumented))


class DerivationTestCase(base.ShakenFistTestCase):
    """The derivation itself, on sources rather than on the tree.

    test_declared_locations_are_derivable is only as good as the four
    sources it composes, and each of those was added in response to a
    defect: the route regex missed Werkzeug converters, the webargs
    scan was class-scoped and ignored the use_kwargs location, and
    flask.request.args reads were not consulted at all. The tree cannot
    exercise the cases which are not in it.
    """

    def test_route_parameters_handle_converters(self):
        routes = declarations.route_parameters()

        # <path:label_name>, which a bare <([a-z_]+)> regex missed.
        self.assertIn('label_name', routes['LabelEndpoint'])
        self.assertIn('artifact_ref', routes['ArtifactEndpoint'])

    def test_query_derivation_does_not_cross_classes(self):
        """One class's schema is not another's.

        The scopes searched for a schema name used to be unioned, and
        the module was always one of them, so every `get_args` in a
        file contributed to every handler in it. The consequence was
        not merely a wrong test result: the fixer would have rewritten
        a correct `body` declaration to `query`, and phase 3 would have
        compiled a query-string fallback for a parameter that never
        arrives from the query string.
        """
        source = '''
class A(api_base.Resource):
    get_args = {'alpha': None}

    @use_kwargs(get_args, location='query')
    def get(self, alpha=None):
        pass


class B(api_base.Resource):
    get_args = {'beta': None}

    @use_kwargs(get_args, location='query')
    def get(self, beta=None):
        pass
'''
        tree = ast.parse(source)
        a, b = tree.body[0], tree.body[1]

        self.assertEqual(
            {'alpha'},
            declarations.query_parameters(a.body[-1], [a, tree]))
        self.assertEqual(
            {'beta'},
            declarations.query_parameters(b.body[-1], [b, tree]))

    def test_module_level_schema_is_found(self):
        """Falling back to the module scope still works, and picks up
        only the module's own assignments."""
        source = '''
get_args = {'alpha': None}


class A(api_base.Resource):
    @use_kwargs(get_args, location='query')
    def get(self, alpha=None):
        pass
'''
        tree = ast.parse(source)
        cls = tree.body[1]

        self.assertEqual(
            {'alpha'},
            declarations.query_parameters(cls.body[0], [cls, tree]))

    def test_query_derivation_reads_the_use_kwargs_location(self):
        """A schema bound at a location other than query is not a query
        schema, and a schema on one handler is not on its siblings."""
        source = '''
class Thing:
    get_args = {'all': None}

    @use_kwargs(get_args, location='query')
    def get(self, all=None):
        pass

    @use_kwargs(get_args, location='json')
    def post(self, all=None):
        pass

    def delete(self, all=None):
        pass
'''
        cls = self._parse_class(source)
        handlers = {fn.name: fn for fn in cls.body
                    if hasattr(fn, 'decorator_list')}

        self.assertEqual(
            {'all'}, declarations.query_parameters(handlers['get'], [cls]))
        self.assertEqual(
            set(), declarations.query_parameters(handlers['post'], [cls]))
        self.assertEqual(
            set(), declarations.query_parameters(handlers['delete'], [cls]))

    def test_request_args_reads_are_found(self):
        source = '''
class Thing:
    def get(self, a=None, b=None, c=None):
        a = flask.request.args.get('a')
        b = request.args['b']
        c = something.else_.args.get('c')
'''
        cls = self._parse_class(source)
        fn = cls.body[0]

        self.assertEqual(
            {'a', 'b'}, declarations.request_args_parameters(fn))

    def test_unreadable_declaration_is_reported_not_skipped(self):
        """A declaration this module cannot destructure must fail the
        audit rather than escape every assertion in it."""
        source = '''
class Thing:
    @swag_from(api_base.swagger_helper(*ARGS))
    def get(self):
        pass
'''
        cls = self._parse_class(source)
        declared = declarations.declarations(cls.body[0])

        self.assertEqual(1, len(declared))
        self.assertIsNone(declared[0].name)
        self.assertIsNone(declared[0].location)

    def test_wrong_arity_declaration_is_reported(self):
        """swagger_helper() destructures five elements, so a tuple of
        any other length is malformed however readable its parts are."""
        source = '''
class Thing(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'things', 'A thing.',
        [('name', 'body', 'string', 'A name.')],
        []))
    def get(self, name=None):
        pass
'''
        cls = self._parse_class(source)
        declared = declarations.declarations(cls.body[0])

        self.assertEqual(1, len(declared))
        self.assertIsNone(declared[0].name)

    def test_only_resource_subclasses_are_endpoints(self):
        """A helper class with a get() accessor is not an endpoint, and
        must not be asked to document itself."""
        source = '''
class Helper:
    def get(self, thing):
        return self.things[thing]
'''
        self._write('helper.py', source)

        problems = []
        self.assertEqual(
            [], list(declarations.handlers(self.tempdir, problems)))
        self.assertEqual([], problems)

    def test_unreadable_input_is_reported_not_skipped(self):
        """Every source here answers "not found" and "cannot tell" with
        the same empty set, so a skipped input is a wrong answer rather
        than a missing one.

        A route it cannot read empties the class's path set, which
        derives every parameter to `body` -- and the fixer, trusting the
        derivation, would rewrite a correct `path` declaration.
        """
        self._write('app.py', '''
api.add_resource(FakeEndpoint, *ROUTES)
''')
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('fake_ref', 'path', 'uuid', 'A ref.', True)],
        []))
    def get(self, fake_ref=None):
        pass


class InheritedEndpoint(FakeEndpoint):
    def post(self, fake_ref=None):
        pass
''')

        drifted, _, problems = declarations.audit(self.tempdir)

        reported = '\n'.join(problems)
        self.assertEqual(
            2, len(problems), 'expected the route and the subclass: %s'
            % reported)
        self.assertIn('route this cannot read', reported)
        self.assertIn('subclasses an endpoint', reported)

        # And the wrong answer the problems are protecting against.
        self.assertEqual(
            [('fake_ref', 'path', 'body')],
            [(d.name, d.location, want) for d, want in drifted])

    def test_unresolvable_query_schema_is_reported(self):
        source = '''
class Thing(api_base.Resource):
    @use_kwargs({'alpha': None}, location='query')
    def get(self, alpha=None):
        pass
'''
        cls = self._parse_class(source)
        problems = []

        self.assertEqual(
            set(),
            declarations.query_parameters(cls.body[0], [cls], problems))
        self.assertEqual(1, len(problems))
        self.assertIn('cannot resolve', problems[0])

    def test_raw_body_sentinel_resolves(self):
        """RAW_BODY_PARAMETER is referenced rather than spelled out, and
        is read from base.py's source rather than imported."""
        self.assertEqual(
            api_base.RAW_BODY_PARAMETER,
            declarations.CONSTANTS['RAW_BODY_PARAMETER'])

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir)

    def _parse_class(self, source):
        return ast.parse(source).body[0]

    def _write(self, name, source):
        with open(os.path.join(self.tempdir, name), 'w') as f:
            f.write(source)


class FixerTestCase(base.ShakenFistTestCase):
    """The rewrite path of tools/fix-api-parameter-locations.py.

    test_declared_locations_are_derivable tells a reader to run that
    script, and the script only ever runs against a clean tree in CI, so
    a regression in the parts which live only in it -- the byte-offset
    splice, the bottom-up ordering that keeps not-yet-applied edits
    valid, the guards -- would not be noticed until someone needed it.
    """

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir)

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(declarations.__file__)))),
            'tools', 'fix-api-parameter-locations.py')
        spec = importlib.util.spec_from_file_location('fixer', path)
        self.fixer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixer)

    def _write(self, name, source):
        with open(os.path.join(self.tempdir, name), 'w') as f:
            f.write(source)

    def test_rewrites_a_wrong_location(self):
        # Two declarations on one physical line, which is what the
        # reverse ordering exists for and which no declaration in the
        # tree currently exhibits.
        self._write('app.py', '''
api.add_resource(FakeEndpoint, '/fakes/<fake_ref>/parts/<part_ref>')
''')
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('fake_ref', 'query', 'uuid', 'A ref.', True), ('part_ref', 'body', 'uuid', 'A part.', True)],
        []))
    def get(self, fake_ref=None, part_ref=None):
        pass
''')

        self.assertEqual(1, self.fixer.main(False, self.tempdir))
        self.assertEqual(0, self.fixer.main(True, self.tempdir))

        with open(os.path.join(self.tempdir, 'fake.py')) as f:
            rewritten = f.read()
        self.assertIn(
            "[('fake_ref', 'path', 'uuid', 'A ref.', True), "
            "('part_ref', 'path', 'uuid', 'A part.', True)]",
            rewritten)

        # Idempotent, and the only thing that changed is the two tokens.
        self.assertEqual(0, self.fixer.main(False, self.tempdir))

    def test_leaves_a_correct_tree_alone(self):
        self._write('app.py', "api.add_resource(FakeEndpoint, '/fakes')\n")
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('name', 'body', 'string', 'A name.', False)],
        []))
    def post(self, name=None):
        pass
''')
        with open(os.path.join(self.tempdir, 'fake.py')) as f:
            before = f.read()

        self.assertEqual(0, self.fixer.main(True, self.tempdir))

        with open(os.path.join(self.tempdir, 'fake.py')) as f:
            self.assertEqual(before, f.read())


class SwaggerHelperValidationTestCase(base.ShakenFistTestCase):
    """swagger_helper() rejects a malformed declaration at import time.

    The assertions above read declarations out of the AST and never
    execute swagger_helper(), so they say nothing about the enforcement
    itself -- which is the mechanism the rest of the plan relies on, and
    whose failure mode is that sf-api will not start. Call it directly.
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

    def test_wrong_arity_is_rejected(self):
        """The one malformed declaration that used to arrive as a bare
        ValueError from the five-element unpack."""
        for parameters in ([('thing', 'body', 'string', 'A thing.')],
                           [('thing', 'body', 'string', 'A thing.', False, 1)],
                           [()]):
            self.assertRaises(
                exceptions.InvalidAPIDeclaration, self._helper, parameters)

    def test_bearer_is_not_declarable(self):
        """swagger_helper injects the Authorization header itself; an
        endpoint declaring a parameter of that type is confused."""
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._helper,
            [('thing', 'header', 'bearer', 'A thing.', False)])

    def test_optional_path_parameter_is_rejected(self):
        """A path parameter must be required, or the specification is
        invalid and client generators reject it."""
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._helper,
            [('thing', 'path', 'string', 'A thing.', False)])

    def test_rejection_names_the_section_and_parameter(self):
        """A malformed declaration aborts sf-api startup, so the message
        has to narrow down which one.

        The section, not the endpoint: swagger_helper() is called as an
        argument expression before the decorator is applied, so it never
        learns the class or method it belongs to. The traceback's file
        and line are what locate it exactly.
        """
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
