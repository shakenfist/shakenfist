# Copyright 2019 Michael Still and contributors
import ast
import importlib.util
import os
import re
import shutil
import tempfile

from shakenfist import exceptions
from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.tests import base


# Everything in this module audits the working tree: declarations reads
# the external_api sources adjacent to its own file, and FixerTestCase
# loads the fixer out of tools/. tox installs the package as a wheel
# (isolated_build, no usedevelop), so these tests see the checkout only
# because unittest discovery puts the repository root at the front of
# sys.path and that copy wins the import. If that ever shifts, the
# audit would pass against a stale installed copy -- so the invariant
# is asserted (test_the_audit_reads_this_checkout) rather than assumed.
REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..'))


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

# Deferred to phase 4 for the same reason: InstanceSnapshotEndpoint.post
# treats an explicit `thin: false` as unset. The official client has
# always transmitted the key (`--thin/--flatten` defaults to False and
# apiclient sends it unconditionally), so honouring false today would
# make SNAPSHOTS_DEFAULT_TO_THIN inert for every shipped client. The
# absent-versus-false distinction needs the schema layer plus a client
# release that omits the key when unset.

# Declarations exempt from location derivation. `header` and `formData`
# cannot be derived from the code, so a declaration using one bypasses
# the audit entirely -- the fixer prints it and moves on, and nothing
# fails. That makes this list the boundary of the enforcement this
# phase builds: a new entry must be a deliberate, reviewed act, not a
# side effect of choosing an exotic location. (cls, method, name).
UNDERIVABLE_BY_DESIGN = set()

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

    def test_the_audit_reads_this_checkout(self):
        """Every other test here is meaningless if this fails.

        See the REPO_ROOT comment: the package is installed as a wheel
        into the tox venv, and the audit reads the tree adjacent to
        wherever declarations was imported from. If import resolution
        ever preferred the installed copy, the audit would confidently
        pass against stale sources and the mutation harness would
        mutate files nothing reads.

        Two assertions because either alone holds for the wrong
        reason. The relative check catches *divergence* (test from the
        checkout, module from the wheel) but passes when both resolve
        to the installed copy -- REPO_ROOT is then just site-packages,
        and the module is under it. So the second assertion requires a
        marker which the wheel does not ship: tools/ is not in the
        package, so its presence under REPO_ROOT proves REPO_ROOT is a
        checkout and not an install.
        """
        self.assertTrue(
            os.path.abspath(declarations.API_DIR).startswith(
                REPO_ROOT + os.sep),
            'declarations was imported from %s, which is not under this '
            'checkout (%s): the audit is running against an installed '
            'copy, not the working tree' % (declarations.API_DIR, REPO_ROOT))
        self.assertTrue(
            os.path.exists(os.path.join(
                REPO_ROOT, 'tools', 'fix-api-parameter-locations.py')),
            '%s does not contain tools/fix-api-parameter-locations.py, so '
            'it is an installed copy rather than a checkout, and the audit '
            'is not reading the working tree' % REPO_ROOT)

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

    def test_underivable_locations_are_allowlisted(self):
        """`header` and `formData` are an opt-out, so gate the opting.

        A declaration using either location bypasses derivation: the
        fixer prints it and exits zero, and the drift assertion never
        sees it. Without this canary the opt-out grows silently --
        declaring a parameter `header` would exempt it from the entire
        audit without failing anything, which is the silent-skip shape
        this suite exists to close. Both directions are asserted so an
        allowlist entry cannot outlive the declaration it exempts.
        """
        _, underivable, _ = declarations.audit()
        found = {(d.cls, d.method, d.name) for d, _ in underivable}

        self.assertEqual(
            set(), found - UNDERIVABLE_BY_DESIGN,
            'these declarations use a location the audit cannot derive '
            '(header or formData), which exempts them from location '
            'checking entirely; add them to UNDERIVABLE_BY_DESIGN with '
            'a reason if that is intended')
        self.assertEqual(
            set(), UNDERIVABLE_BY_DESIGN - found,
            'these UNDERIVABLE_BY_DESIGN entries no longer match an '
            'underivable declaration; remove them')

    def test_route_parameters_are_declared(self):
        """Every route segment is declared as a path parameter, by
        every handler of the mounted class.

        The drift assertion checks each *declared* parameter against
        its derived location, so a route segment that is never declared
        at all is compared against nothing -- and renders a path
        template with an undefined variable, the largest single error
        class in the specification this branch fixed. Today the
        property holds transitively (flask_restful passes segments as
        kwargs, and accepted kwargs must be declared), but that chain
        has exemption lists in it; this states the property directly.

        Per handler rather than per class: an earlier version pooled
        every handler's path declarations into one set per class, so a
        class whose post declared the segment covered for a get which
        neither declared nor accepted it -- and flask_restful passes
        the segment to every method on the class regardless.
        """
        declared_path: dict = {}
        for cls, method, fn in _endpoints():
            declared_path[(cls, method)] = {
                parameter.name
                for parameter in declarations.declarations(fn)
                if parameter.location == 'path'}

        problems: list = []
        routes = declarations.route_parameters(problems=problems)
        for (cls, method), declared in declared_path.items():
            for name in routes.get(cls, set()):
                self.assertIn(
                    name, declared,
                    '%s is mounted on a route carrying %r, but %s.%s does '
                    'not declare that path parameter; the published path '
                    'template would reference an undefined variable, and '
                    'flask_restful passes the segment to every method on '
                    'the class' % (cls, name, cls, method))
        self.assertEqual([], problems)

    def test_every_mounted_class_is_an_endpoint_and_vice_versa(self):
        """route_parameters() and handlers() must agree on what an
        endpoint is.

        handlers() recognises an endpoint by a base name ending in
        'Resource' -- a heuristic, because AST cannot resolve what a
        base actually is. A Resource subclass reached through an
        intermediate base named anything else would be skipped with an
        empty problems list, exempting it from every assertion in this
        module. This symmetry check is what makes that silent skip
        loud: a class mounted by add_resource() but not recognised as
        an endpoint fails one direction, and a recognised endpoint
        never mounted (dead code, or a typo in app.py) fails the
        other.
        """
        problems: list = []
        mounted = set(declarations.route_parameters(problems=problems))
        recognised = {cls for cls, _, _ in _endpoints()}
        self.assertEqual([], problems)

        self.assertEqual(
            set(), mounted - recognised,
            'mounted by add_resource() but not recognised as an endpoint, '
            'so exempt from every assertion in this module')
        self.assertEqual(
            set(), recognised - mounted,
            'recognised as an endpoint but never mounted in app.py')

    def test_declared_names_are_real_parameters(self):
        """A declared name must be something the handler can receive.

        An empty declaration list is legitimate -- eight endpoints
        accept nothing -- and needs no guard, because the loop below is
        already a no-op for one. The sibling assertion's version of that
        guard was load-bearing and wrong, so it is deliberately absent
        here rather than kept for symmetry.
        """
        for cls, method, fn in _endpoints():
            problems = []
            accepted = declarations.handler_kwargs(fn, problems)
            self.assertEqual(
                [], problems,
                '%s.%s: the accepted parameter list could not be '
                'enumerated: %s' % (cls, method, '; '.join(problems)))
            for parameter in declarations.declarations(fn):
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
            # A variadic handler would make this loop pass vacuously --
            # **kwargs accepts arbitrary undeclared names while the
            # enumeration below stays near-empty (issue 3642) -- so an
            # unreadable parameter list is a failure, not an absence.
            problems = []
            kwargs = declarations.handler_kwargs(fn, problems)
            self.assertEqual(
                [], problems,
                '%s.%s: the accepted parameter list could not be '
                'enumerated, so this assertion cannot hold: %s'
                % (cls, method, '; '.join(problems)))
            for kwarg in kwargs:
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

    def test_exemptions_still_describe_real_handlers(self):
        """An allowlist entry that no longer matches anything is a
        silent exemption waiting for a name to be reused.

        Both lists exempt a handler from an assertion, so a stale entry
        is invisible until the day something is called `Root.get`
        again. Every entry must name a handler that exists, and an
        UNDECLARED_BY_DESIGN entry must name a kwarg that handler still
        accepts -- which is also what makes the deferred `value` on the
        metadata deletes fail loudly once it is removed, rather than
        leaving a line here nobody revisits.
        """
        handlers = {(cls, method): fn for cls, method, fn in _endpoints()}

        for cls, method in UNDOCUMENTED_BY_DESIGN:
            self.assertIn(
                (cls, method), handlers,
                '%s.%s is in UNDOCUMENTED_BY_DESIGN but is not an endpoint '
                'handler; remove the entry' % (cls, method))

        for cls, method, kwarg in UNDECLARED_BY_DESIGN:
            self.assertIn(
                (cls, method), handlers,
                '%s.%s is in UNDECLARED_BY_DESIGN but is not an endpoint '
                'handler; remove the entry' % (cls, method))
            self.assertIn(
                kwarg, declarations.handler_kwargs(handlers[(cls, method)]),
                '%s.%s no longer accepts %r, so the UNDECLARED_BY_DESIGN '
                'entry exempting it is stale; remove it'
                % (cls, method, kwarg))

    def test_undocumented_by_design_is_exactly_the_health_probes(self):
        """The exemption list and the deployment contract cannot drift.

        The comment above UNDOCUMENTED_BY_DESIGN claims its entries are
        exactly the health probes in api_base.HEALTH_PROBE_PATHS, but
        the two lists live in different files and nothing else ties
        them together: an exempt class quietly gaining a tenant-facing
        route, or a probe path moving to a documented class, would
        falsify the comment without failing a test.
        """
        exempt = {cls for cls, _ in UNDOCUMENTED_BY_DESIGN}
        # Explicit encoding, like every other read in this module:
        # Python source is UTF-8 by definition (PEP 3120), while a bare
        # open() consults the caller's locale (issue 3643).
        with open(os.path.join(declarations.API_DIR, 'app.py'),
                  encoding='utf-8') as f:
            tree = ast.parse(f.read())

        routes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func).split('.')[-1] != 'add_resource':
                continue
            if not node.args:
                continue
            if ast.unparse(node.args[0]).split('.')[-1] not in exempt:
                continue
            for arg in node.args[1:]:
                routes.add(declarations.literal(arg))

        self.assertEqual(
            set(api_base.HEALTH_PROBE_PATHS), routes,
            'The routes mounted on the UNDOCUMENTED_BY_DESIGN classes are '
            'not exactly api_base.HEALTH_PROBE_PATHS; update whichever of '
            'the two lists is wrong')

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

    def test_empty_inner_schema_does_not_fall_through(self):
        """The first scope to *define* the name wins, even when empty.

        An earlier version took the first scope to yield a key, so a
        class-level `get_args = {}` fell through to a same-named
        module-level dict -- the cross-scope leak with an extra step,
        and confidently wrong rather than empty. An empty literal dict
        is readable and legitimately binds nothing, so it is not a
        problem either.
        """
        source = '''
get_args = {'leak': None}


class Thing(api_base.Resource):
    get_args = {}

    @use_kwargs(get_args, location='query')
    def get(self):
        pass
'''
        tree = ast.parse(source)
        cls = tree.body[1]
        problems = []

        self.assertEqual(
            set(),
            declarations.query_parameters(cls.body[-1], [cls, tree],
                                          problems))
        self.assertEqual([], problems)

    def test_unreadable_inner_schema_is_reported_not_fallen_through(self):
        """A defining scope this cannot read is a problem, not a miss.

        If the class scope assigns the name something other than a
        dict literal, falling through to the module scope answers with
        another handler's keys, and 'cannot read this' must never wear
        the same face as 'not found'.
        """
        source = '''
get_args = {'leak': None}


class Thing(api_base.Resource):
    get_args = build_schema()

    @use_kwargs(get_args, location='query')
    def get(self):
        pass
'''
        tree = ast.parse(source)
        cls = tree.body[1]
        problems = []

        self.assertEqual(
            set(),
            declarations.query_parameters(cls.body[-1], [cls, tree],
                                          problems))
        self.assertEqual(1, len(problems))
        self.assertIn('cannot read', problems[0])

    def test_partially_readable_schema_keeps_what_it_can(self):
        """A non-literal key is reported, and its siblings still count."""
        source = '''
class Thing(api_base.Resource):
    get_args = {'alpha': None, KEY: None}

    @use_kwargs(get_args, location='query')
    def get(self, alpha=None):
        pass
'''
        cls = self._parse_class(source)
        problems = []

        self.assertEqual(
            {'alpha'},
            declarations.query_parameters(cls.body[-1], [cls], problems))
        self.assertEqual(1, len(problems))
        self.assertIn('cannot read', problems[0])

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

    def test_query_derivation_reads_a_tuple_of_locations(self):
        """webargs accepts a tuple of locations, and one containing
        'query' is a query binding.

        No site uses this today. It is the shape a fix for issue 3629,
        and decision D6's fallback, would introduce -- and reading it
        as "not query" would send the fixer to rewrite the very
        declarations that fix had just made true. An unreadable
        location is a third answer and must not wear the same face as
        an absent one, which means webargs' json default.
        """
        source = '''
class Thing(api_base.Resource):
    get_args = {'all': None}

    @use_kwargs(get_args, location=('query', 'json'))
    def get(self, all=None):
        pass

    @use_kwargs(get_args, location=['json', 'query'])
    def post(self, all=None):
        pass

    @use_kwargs(get_args, location=('json', 'form'))
    def put(self, all=None):
        pass

    @use_kwargs(get_args, location=SOMETHING_UNREADABLE)
    def delete(self, all=None):
        pass

    @use_kwargs(get_args)
    def patch(self, all=None):
        pass
'''
        cls = self._parse_class(source)
        handlers = {fn.name: fn for fn in cls.body
                    if isinstance(fn, ast.FunctionDef)}

        for method in ('get', 'post'):
            self.assertEqual(
                {'all'},
                declarations.query_parameters(handlers[method], [cls]),
                '%s binds a tuple containing query' % method)

        # A tuple without query, and no location at all (webargs
        # defaults to json), are both correctly not query bindings.
        for method in ('put', 'patch'):
            problems = []
            self.assertEqual(
                set(),
                declarations.query_parameters(
                    handlers[method], [cls], problems))
            self.assertEqual([], problems)

        problems = []
        self.assertEqual(
            set(),
            declarations.query_parameters(
                handlers['delete'], [cls], problems))
        self.assertEqual(1, len(problems))
        self.assertIn('cannot read', problems[0])

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

    def test_unreadable_request_args_reads_are_reported(self):
        """Every way of touching request.args that the walk cannot name
        must land in problems, not silently derive to body.

        Four earlier review rounds each found a derivation source
        returning the same empty answer for 'nothing there' and 'could
        not read this'; this was the last source without the
        treatment.
        """
        source = '''
class Thing:
    def get(self, a=None, b=None, c=None, d=None):
        a = flask.request.args.get(TARGET_KEY)
        b = request.args[key_for('b')]
        c = flask.request.args.getlist('c')
        for d in flask.request.args:
            pass
'''
        cls = self._parse_class(source)
        fn = cls.body[0]

        problems = []
        self.assertEqual(
            set(), declarations.request_args_parameters(fn, problems))
        self.assertEqual(4, len(problems))
        self.assertEqual(
            2, len([p for p in problems if 'not a literal' in p]),
            problems)
        self.assertEqual(
            2, len([p for p in problems if 'other than via' in p]),
            problems)

        # And without a problems list, still no confident wrong answer
        # in the return value.
        self.assertEqual(set(), declarations.request_args_parameters(fn))

    def test_variadic_handler_is_a_problem(self):
        """A handler taking *args or **kwargs accepts names no
        enumeration can produce -- log_request merges the whole JSON
        body into kwargs -- so handler_kwargs() must report it rather
        than return a near-empty list an assertion iterates vacuously
        (issue 3642). By definition the shape is not in the tree, so it
        is pinned on constructed source.
        """
        source = '''
class Thing:
    def post(self, thing_ref=None, **kwargs):
        pass

    def put(self, thing_ref=None, *extras):
        pass
'''
        cls = self._parse_class(source)

        problems = []
        accepted = declarations.handler_kwargs(cls.body[0], problems)
        self.assertEqual(['thing_ref'], accepted)
        self.assertEqual(1, len(problems), problems)
        self.assertIn('**kwargs', problems[0])

        problems = []
        declarations.handler_kwargs(cls.body[1], problems)
        self.assertEqual(1, len(problems), problems)
        self.assertIn('*extras', problems[0])

        # And without the problems channel the answer is unchanged --
        # the channel reports, it does not filter.
        self.assertEqual(
            ['thing_ref'], declarations.handler_kwargs(cls.body[0]))

    def test_underivable_request_args_read_is_a_problem(self):
        """The reviewer's demonstration case: a handler reading
        flask.request.args.get(TARGET_KEY) derives to body -- which is
        the best answer available -- but must say so in problems, so
        the consumers refuse to trust it."""
        source = '''
class Thing:
    def get(self, a=None):
        a = flask.request.args.get(TARGET_KEY)
'''
        tree = ast.parse(source)
        cls = tree.body[0]
        fn = cls.body[0]

        problems = []
        self.assertEqual(
            'body',
            declarations.derived_location('a', fn, tree, cls, {}, problems))
        self.assertEqual(1, len(problems))
        self.assertIn('not a literal', problems[0])

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
        """swagger_helper() destructures five elements plus an optional
        constraints dictionary, so a tuple of any other length is
        malformed however readable its parts are."""
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

    def test_constrained_declaration_parses(self):
        """The six element form is legal, and the constraints element
        does not disturb the five values the audit reads. Without a
        positive test the arity widening is covered only by the tree
        happening to contain a constrained declaration today."""
        source = '''
class Thing(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'things', 'A thing.',
        [('limit', 'body', 'integer', 'How many.', False,
          {'minimum': 1, 'maximum': 1000})],
        []))
    def get(self, limit=None):
        pass
'''
        cls = self._parse_class(source)
        declared = declarations.declarations(cls.body[0])

        self.assertEqual(1, len(declared))
        self.assertEqual('limit', declared[0].name)
        self.assertEqual('body', declared[0].location)

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

    def test_colliding_class_names_are_reported(self):
        """Routes are looked up by bare class name, so two endpoints
        sharing one is a third failure case.

        Not "not found" and not "cannot read this", but a confident
        wrong answer: each class derives the other's URL segments as
        `path`, and the fixer would rewrite correct declarations to
        match. No collision exists in the tree, so only a constructed
        source can reach this.
        """
        self._write('app.py', '''
api.add_resource(api_one.FakeEndpoint, '/ones/<one_ref>')
api.add_resource(api_two.FakeEndpoint, '/twos/<two_ref>')
''')
        self._write('one.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'ones', 'A one.',
        [('one_ref', 'path', 'uuid', 'A ref.', True)],
        []))
    def get(self, one_ref=None):
        pass
''')
        self._write('two.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'twos', 'A two.',
        [('two_ref', 'path', 'uuid', 'A ref.', True)],
        []))
    def get(self, two_ref=None):
        pass
''')

        drifted, _, problems = declarations.audit(self.tempdir)

        reported = '\n'.join(problems)
        self.assertIn('mounted from two modules', reported)
        self.assertIn('is defined twice', reported)

        # Both declarations are correct, and the merged route set hides
        # that from the drift check -- which is the whole reason the
        # collision has to be reported rather than derived through.
        self.assertEqual([], [(d.name, want) for d, want in drifted])

    def test_unreadable_resource_argument_is_reported(self):
        """The class being mounted has to be readable too.

        An unreadable *route* was already reported; an unreadable
        *resource* silently empties some class's route set in exactly
        the same way, and the empty-args case used to raise IndexError
        out of the audit rather than report anything.
        """
        for app in ("api.add_resource()\n",
                    'api.add_resource(*everything)\n',
                    "api.add_resource(REGISTRY['Fake'], '/fakes/<x>')\n",
                    "api.add_resource(make(), '/fakes/<x>')\n"):
            self._write('app.py', app)
            self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    def get(self):
        pass
''')

            _, _, problems = declarations.audit(self.tempdir)

            self.assertEqual(
                1, len(problems), 'expected one problem for %r: %s'
                % (app, problems))
            self.assertIn('cannot read', problems[0])

    def test_differing_routes_for_one_class_are_reported(self):
        """Routes are merged per class, so the collection-plus-item
        shape would give the collection handler a path parameter it
        never receives -- and the fixer would rewrite a correct
        declaration to match. Only Readyz is mounted twice today, on
        two parameter-free routes, so this needs constructed sources.
        """
        self._write('app.py', '''
api.add_resource(FakeEndpoint, '/fakes')
api.add_resource(FakeEndpoint, '/fakes/<fake_ref>')
''')
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('name', 'body', 'string', 'A name.', False)],
        []))
    def post(self, name=None):
        pass
''')

        _, _, problems = declarations.audit(self.tempdir)

        self.assertEqual(1, len(problems), problems)
        self.assertIn('routes with different parameters', problems[0])

    def test_unreadable_declaration_reaches_problems(self):
        """The module docstring promises both consumers fail on
        anything unreadable, and the fixer is the consumer that only
        looks at problems.

        Skipping these silently had the fixer -- and so the pre-commit
        hook -- answer "0 locations would change" for a tree carrying a
        declaration it could not parse.
        """
        self._write('app.py', "api.add_resource(FakeEndpoint, '/fakes')\n")
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [(NAME, 'body', 'string', 'A name.', False)],
        []))
    def post(self, name=None):
        pass
''')

        _, _, problems = declarations.audit(self.tempdir)

        self.assertEqual(1, len(problems), problems)
        self.assertIn('declaration this cannot read', problems[0])
        self.assertIn('FakeEndpoint.post', problems[0])

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

    def test_variadic_handler_reaches_problems(self):
        """The audit has to refuse to proceed, not merely be able to.

        handler_kwargs() is not otherwise part of the derivation, so
        the report only reaches the fixer and the pre-commit hook if
        audit() asks for it. Without that, a tree carrying a variadic
        handler is reported as clean.
        """
        self._write('app.py', "api.add_resource(FakeEndpoint, '/fakes')\n")
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('name', 'body', 'string', 'A name.', False)],
        []))
    def post(self, name=None, **kwargs):
        pass
''')

        drifted, _, problems = declarations.audit(self.tempdir)

        self.assertEqual([], drifted)
        self.assertEqual(1, len(problems), problems)
        self.assertIn('cannot be enumerated', problems[0])

    def test_raw_body_sentinel_resolves(self):
        """RAW_BODY_PARAMETER is referenced rather than spelled out, and
        is read from base.py's source rather than imported."""
        self.assertEqual(
            api_base.RAW_BODY_PARAMETER,
            declarations.base_constants()['RAW_BODY_PARAMETER'])

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir)

    def _parse_class(self, source):
        return ast.parse(source).body[0]

    def _write(self, name, source):
        with open(os.path.join(self.tempdir, name), 'w',
                  encoding='utf-8') as f:
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

        # Anchored on this test file's own location rather than on
        # declarations.__file__: tools/ is not part of the installed
        # package, so resolving through an installed copy of the module
        # would be a FileNotFoundError here while the audit silently
        # read the wrong tree. test_the_audit_reads_this_checkout
        # asserts the module side of the same invariant.
        path = os.path.join(REPO_ROOT, 'tools', 'fix-api-parameter-locations.py')
        spec = importlib.util.spec_from_file_location('fixer', path)
        self.fixer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixer)

    def _write(self, name, source):
        # Explicit encoding because the byte-offset test writes real
        # non-ASCII, and this must not depend on the caller's locale.
        with open(os.path.join(self.tempdir, name), 'w',
                  encoding='utf-8') as f:
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

        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            rewritten = f.read()
        self.assertIn(
            "[('fake_ref', 'path', 'uuid', 'A ref.', True), "
            "('part_ref', 'path', 'uuid', 'A part.', True)]",
            rewritten)

        # Idempotent, and the only thing that changed is the two tokens.
        self.assertEqual(0, self.fixer.main(False, self.tempdir))

    def test_splices_at_byte_offsets_not_character_offsets(self):
        """AST column offsets count UTF-8 bytes, not characters.

        The two models agree on ASCII, so the character model works
        until a non-ASCII character lands ahead of a location literal
        on the same physical line -- the em dash in the first tuple's
        description shifts the second tuple's byte offsets past its
        character offsets. The old character-based splice failed the
        slice guard there: closed, but refusing a rewrite it should
        have made.
        """
        self._write('app.py', '''
api.add_resource(FakeEndpoint, '/fakes/<fake_ref>/parts/<part_ref>')
''')
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('fake_ref', 'query', 'uuid', 'A ref — truly.', True), ('part_ref', 'body', 'uuid', 'A part.', True)],
        []))
    def get(self, fake_ref=None, part_ref=None):
        pass
''')

        self.assertEqual(1, self.fixer.main(False, self.tempdir))
        self.assertEqual(0, self.fixer.main(True, self.tempdir))

        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            rewritten = f.read()
        self.assertIn(
            "[('fake_ref', 'path', 'uuid', 'A ref — truly.', True), "
            "('part_ref', 'path', 'uuid', 'A part.', True)]",
            rewritten)
        self.assertEqual(0, self.fixer.main(False, self.tempdir))

    def test_refuses_to_rewrite_from_unreadable_input(self):
        """A derivation with holes in it must not be written to disk.

        The guard exists because an unreadable route empties a class's
        path set, which derives every one of its parameters to `body`:
        rewriting on that basis corrupts declarations which were right
        to begin with. Asserting the file is untouched is the part that
        matters -- SystemExit alone would be satisfied by a script that
        exited after writing.
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
''')
        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            before = f.read()

        self.assertRaises(
            SystemExit, self.fixer.main, True, self.tempdir)

        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            self.assertEqual(before, f.read())

    def test_refuses_a_multiline_location_literal(self):
        """The splice edits one physical line, and must say so rather
        than corrupt source it cannot edit.

        Implicit string concatenation across a line break parses to a
        single Constant spanning two lines, which the derivation reads
        happily -- so the guard in the rewrite path is the only thing
        standing between that shape and a mangled file.
        """
        self._write('app.py', '''
api.add_resource(FakeEndpoint, '/fakes/<fake_ref>')
''')
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('fake_ref', 'que'
                      'ry', 'uuid', 'A ref.', True)],
        []))
    def get(self, fake_ref=None):
        pass
''')
        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            before = f.read()

        self.assertRaisesRegex(
            SystemExit, 'multi-line location literal',
            self.fixer.main, True, self.tempdir)

        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            self.assertEqual(before, f.read())

    def test_refuses_an_offset_that_does_not_hold_the_literal(self):
        """The splice checks the bytes it is about to replace.

        A double-quoted location has the same value but not the same
        repr, so the slice comparison fails -- standing in for any
        drift between the AST offsets and the file, which is the
        failure that turns a targeted edit into corruption.
        """
        self._write('app.py', '''
api.add_resource(FakeEndpoint, '/fakes/<fake_ref>')
''')
        self._write('fake.py', '''
class FakeEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('fake_ref', "query", 'uuid', 'A ref.', True)],
        []))
    def get(self, fake_ref=None):
        pass
''')
        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            before = f.read()

        self.assertRaisesRegex(
            SystemExit, 'does not hold',
            self.fixer.main, True, self.tempdir)

        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            self.assertEqual(before, f.read())

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
        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
            before = f.read()

        self.assertEqual(0, self.fixer.main(True, self.tempdir))

        with open(os.path.join(self.tempdir, 'fake.py'),
                  encoding='utf-8') as f:
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
        out = self._helper([('thing', 'query', 'string', 'A thing.', False)])

        declared = [p for p in out['parameters'] if p['name'] == 'thing']
        self.assertEqual(1, len(declared))
        self.assertEqual('query', declared[0]['in'])
        self.assertFalse(declared[0]['required'])

    def _body_parameters(self, out):
        return [p for p in out['parameters'] if p['in'] == 'body']

    def test_body_parameters_collapse_to_one_schema(self):
        # Swagger 2.0 permits at most one body parameter per operation,
        # carrying a schema. Declarations stay one tuple per parameter;
        # the renderer collapses them.
        out = self._helper([
            ('first', 'body', 'string', 'The first thing.', True),
            ('sneaky', 'query', 'string', 'Not a body thing.', False),
            ('second', 'body', 'integer', 'The second thing.', False)])

        bodies = self._body_parameters(out)
        self.assertEqual(1, len(bodies))
        body = bodies[0]
        self.assertEqual('body', body['name'])
        self.assertTrue(body['required'])
        self.assertEqual(
            {'first', 'second'}, set(body['schema']['properties']))
        self.assertEqual(
            'The first thing.',
            body['schema']['properties']['first']['description'])
        self.assertEqual(
            'integer', body['schema']['properties']['second']['type'])
        self.assertEqual(['first'], body['schema']['required'])

        # The query parameter is untouched by the collapse.
        self.assertEqual(
            1, len([p for p in out['parameters'] if p['name'] == 'sneaky']))

    def test_single_body_parameter_still_collapses(self):
        # One body parameter carrying type/format instead of a schema
        # is just as invalid as three of them.
        out = self._helper([('thing', 'body', 'string', 'A thing.', False)])

        bodies = self._body_parameters(out)
        self.assertEqual(1, len(bodies))
        self.assertIn('thing', bodies[0]['schema']['properties'])
        self.assertNotIn('type', bodies[0])

    def test_all_optional_body_omits_required_array(self):
        # In a schema object 'required' is an array of property names,
        # and an *empty* required array is itself invalid JSON Schema,
        # so it must be absent rather than empty.
        out = self._helper([
            ('one', 'body', 'string', 'One.', False),
            ('two', 'body', 'string', 'Two.', False)])

        body = self._body_parameters(out)[0]
        self.assertNotIn('required', body['schema'])
        self.assertFalse(body['required'])

    def test_no_body_declarations_no_body_parameter(self):
        out = self._helper([('thing', 'query', 'string', 'A thing.', False)])
        self.assertEqual([], self._body_parameters(out))

    def test_raw_body_renders_as_schema(self):
        out = self._helper([
            (api_base.RAW_BODY_PARAMETER, 'body', 'binary',
             'Binary data.', True)])

        bodies = self._body_parameters(out)
        self.assertEqual(1, len(bodies))
        self.assertEqual('string', bodies[0]['schema']['type'])
        self.assertNotIn('type', bodies[0])
        self.assertNotIn('properties', bodies[0]['schema'])

    def test_raw_and_named_body_rejected(self):
        # Raw bytes and named JSON keys cannot share a request body, so
        # declaring both is a contradiction caught at import time.
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._helper,
            [(api_base.RAW_BODY_PARAMETER, 'body', 'binary', 'Bytes.', True),
             ('thing', 'body', 'string', 'A thing.', False)])

    def test_parameter_named_body_is_not_the_raw_marker(self):
        # A named parameter which happens to be called 'body' with a
        # non-binary type is an ordinary schema property. There is no
        # collision with the generated wrapper: its name lives at
        # parameter level, properties live inside the schema.
        out = self._helper([('body', 'body', 'string', 'A thing.', False)])

        bodies = self._body_parameters(out)
        self.assertEqual(1, len(bodies))
        self.assertIn('body', bodies[0]['schema']['properties'])

    def test_security_and_authorization_travel_together(self):
        """An unauthenticated operation publishes neither the security
        requirement nor the Authorization parameter; an authenticated
        one publishes both.

        The requirement used to be emitted unconditionally, which
        described /auth/federated -- deliberately unauthenticated,
        the identity token is the credential -- as demanding a bearer
        token, and a client generated from the specification would
        have insisted on a credential for the one endpoint that must
        not take one. Making the requirement a spec-valid array made
        that misstatement invisible to a linter, so only a test like
        this can keep the two coupled.
        """
        authed = api_base.swagger_helper(
            'test', 'A test endpoint.', [], [(200, 'No return value', '')])
        self.assertEqual([{'bearerAuth': []}], authed['security'])
        self.assertEqual(
            1, len([p for p in authed['parameters']
                    if p['name'] == 'Authorization']))

        public = api_base.swagger_helper(
            'test', 'A test endpoint.', [], [(200, 'No return value', '')],
            requires_auth=False)
        self.assertNotIn('security', public)
        self.assertEqual(
            [], [p for p in public['parameters']
                 if p['name'] == 'Authorization'])

    def test_unknown_location_is_rejected(self):
        for location in ('qeury', 'post', 'BODY', ''):
            self.assertRaises(
                exceptions.InvalidAPIDeclaration, self._helper,
                [('thing', location, 'string', 'A thing.', False)])

    def test_unknown_type_is_rejected(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._helper,
            [('thing', 'body', 'stringy', 'A thing.', False)])

    def test_constraints_render_on_a_query_parameter(self):
        # minimum, maximum and pattern are valid Swagger 2.0 parameter
        # keywords, so a bound renders into the published OpenAPI
        # rather than living only in code -- the property that kept the
        # events limit cap invisible to callers for years.
        out = self._helper([
            ('limit', 'query', 'integer', 'A limit.', False,
             {'minimum': 1, 'maximum': 1000})])

        declared = [p for p in out['parameters'] if p['name'] == 'limit'][0]
        self.assertEqual(1, declared['minimum'])
        self.assertEqual(1000, declared['maximum'])

    def test_constraints_render_into_body_properties(self):
        out = self._helper([
            ('limit', 'body', 'integer', 'A limit.', False,
             {'minimum': 1, 'maximum': 1000})])

        prop = [p for p in out['parameters'] if p['in'] == 'body'][0][
            'schema']['properties']['limit']
        self.assertEqual(1, prop['minimum'])
        self.assertEqual(1000, prop['maximum'])

    def test_malformed_constraints_are_rejected(self):
        # Every defect arrives as InvalidAPIDeclaration so phase 3's
        # compiler catches one exception type, in the established
        # import-time style: sf-api does not start with one of these
        # in the tree.
        for parameters in (
                # A sixth element which is not a dictionary.
                [('thing', 'body', 'integer', 'A thing.', False, 'nope')],
                # An unknown constraint key.
                [('thing', 'body', 'integer', 'A thing.', False,
                  {'maximim': 3})],
                # A bound which is not a number.
                [('thing', 'body', 'integer', 'A thing.', False,
                  {'minimum': True})],
                # A bound on a non-numeric type.
                [('thing', 'body', 'string', 'A thing.', False,
                  {'minimum': 1})],
                # Contradictory bounds.
                [('thing', 'body', 'integer', 'A thing.', False,
                  {'minimum': 10, 'maximum': 1})],
                # A constraint restating a key the token already
                # renders: unsignedinteger defines its own minimum.
                [('thing', 'body', 'unsignedinteger', 'A thing.', False,
                  {'minimum': 3})],
                # A pattern on a non-string type.
                [('thing', 'body', 'integer', 'A thing.', False,
                  {'pattern': '^a$'})],
                # A pattern which is not a string.
                [('thing', 'body', 'string', 'A thing.', False,
                  {'pattern': 7})],
                # A pattern which does not compile.
                [('thing', 'body', 'string', 'A thing.', False,
                  {'pattern': '('})],
                # Patterns which are not ^...$ anchored. JSON Schema
                # pattern is an unanchored search while the compiled
                # validator requires the whole value to match, and full
                # anchoring is the only form the two read identically.
                [('thing', 'body', 'string', 'A thing.', False,
                  {'pattern': 'a+'})],
                [('thing', 'body', 'string', 'A thing.', False,
                  {'pattern': '^a+'})],
                [('thing', 'body', 'string', 'A thing.', False,
                  {'pattern': 'a+$'})],
                # A top-level alternation escapes the anchors: ^a|b$ is
                # (^a)|(b$), anchored on neither branch. A grouped
                # alternation like ^(a|b)$ is fine.
                [('thing', 'body', 'string', 'A thing.', False,
                  {'pattern': '^a|b$'})],
                # A sixth element which is not a dictionary, in the
                # shape that used to be a wrong-arity case.
                [('thing', 'body', 'string', 'A thing.', False, 1)],
                # A fractional bound on an integer type.
                [('thing', 'body', 'integer', 'A thing.', False,
                  {'minimum': 1.5})],
                # Seven elements.
                [('thing', 'body', 'string', 'A thing.', False, {}, 8)]):
            with self.subTest(parameters=parameters):
                self.assertRaises(
                    exceptions.InvalidAPIDeclaration, self._helper, parameters)

    def test_new_tokens_render_their_bounds(self):
        # The D9 vocabulary: bounds and formats phase 3 will compile,
        # published in the specification in the meantime.
        out = self._helper([
            ('count', 'query', 'unsignedinteger', 'A count.', False),
            ('mac', 'body', 'macaddr', 'A MAC.', False),
            ('data', 'body', 'base64', 'Some data.', False),
            ('block', 'body', 'netblock', 'A netblock.', False)])

        count = [p for p in out['parameters'] if p['name'] == 'count'][0]
        self.assertEqual(0, count['minimum'])

        props = [p for p in out['parameters'] if p['in'] == 'body'][0][
            'schema']['properties']
        # byte is Swagger 2.0's standard format for base64 content.
        self.assertEqual('byte', props['data']['format'])
        self.assertTrue(
            re.match(props['mac']['pattern'], '02:00:00:73:18:66'))
        self.assertFalse(
            re.match(props['mac']['pattern'], '02:00:00:73:18:zz'))
        # netblock is deliberately format-only. An IPv4 CIDR pattern
        # would publish the API as narrower than ip_network() actually
        # accepts, which phase 4 would then compile into a 400 for
        # input that works today.
        self.assertEqual('a CIDR netblock', props['block']['format'])
        self.assertNotIn('pattern', props['block'])

    def test_objects_outside_the_body_are_rejected(self):
        """Outside a body there is no schema object to nest a structure
        in, so the specification would be invalid. Refused at import
        time like every other declaration defect, rather than left for
        test_openapi_spec.py to find after sf-api has started serving
        it."""
        for argtype in ('arrayofdict', 'dict'):
            for location in ('query', 'path', 'header', 'formData'):
                with self.subTest(argtype=argtype, location=location):
                    self.assertRaises(
                        exceptions.InvalidAPIDeclaration, self._helper,
                        [('thing', location, argtype, 'A thing.', True)])

        # An array of strings is fine anywhere: its items are primitive.
        out = self._helper(
            [('scopes', 'query', 'arrayofstring', 'Scopes.', False)])
        scopes = [p for p in out['parameters'] if p['name'] == 'scopes'][0]
        self.assertEqual('array', scopes['type'])

    def test_dicts_render_as_objects(self):
        """instance create metadata is a dictionary on the wire -- the
        handler answers 400 to anything else -- so it must not be
        published as an array. It was declared arrayofdict, which was
        inert prose until the array tokens became machine readable."""
        out = self._helper([
            ('metadata', 'body', 'dict', 'Metadata.', False)])

        props = [p for p in out['parameters'] if p['in'] == 'body'][0][
            'schema']['properties']
        self.assertEqual('object', props['metadata']['type'])
        self.assertNotIn('items', props['metadata'])

    def test_arrays_render_as_arrays(self):
        # These were prose-formatted strings before the D9 work; now
        # that body parameters render through schema objects, a real
        # array type is legal and generators produce list-typed
        # bindings from it.
        out = self._helper([
            ('scopes', 'body', 'arrayofstring', 'Scopes.', False),
            ('disk', 'body', 'arrayofdict', 'Disks.', False)])

        props = [p for p in out['parameters'] if p['in'] == 'body'][0][
            'schema']['properties']
        self.assertEqual('array', props['scopes']['type'])
        self.assertEqual({'type': 'string'}, props['scopes']['items'])
        self.assertEqual({'type': 'object'}, props['disk']['items'])

    def test_wrong_arity_is_rejected(self):
        """The one malformed declaration that used to arrive as a bare
        ValueError from the five-element unpack.

        The unsized cases matter for the same reason: len() raises
        TypeError on them, which is the other way a malformed
        declaration escapes the single exception type that phase 3's
        compiler catches.

        Six elements is now legal arity, so a bad constraints element
        is a constraints defect and is tested as one in
        test_malformed_constraints_are_rejected.
        """
        for parameters in ([('thing', 'body', 'string', 'A thing.')],
                           [()],
                           [None],
                           [42],
                           [(x for x in range(5))],
                           ['a five character string']):
            with self.subTest(parameters=parameters):
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
