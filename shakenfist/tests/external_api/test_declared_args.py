# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The webargs schemas built from an endpoint's own declaration list.

`@use_kwargs` is the only mechanism by which a query-string parameter
reaches a handler at all, so six endpoints hand-authored a marshmallow
schema beside the `swagger_helper()` declaration describing the same
parameters -- two statements of one fact, and the decision which
deferred this noted at the time that the endpoints which hand-authored
a schema were exactly the ones whose documentation had drifted (issue
4098).

`api_base.use_declared_args()` builds the schema from the declaration
list instead. Two things are asserted here: that it builds the right
fields, and that each call site passes the same list it publishes and
the defaults its handler's signature gives -- the parts the decorator
cannot check for itself.
"""

import ast
from unittest import mock

import marshmallow
from marshmallow import fields

from shakenfist import exceptions
from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.external_api import validation
from shakenfist.tests import base


def _use_declared_args_sites(unreadable=None):
    """(class, method, declaration list name, defaults) per call site.

    A site whose declaration list or defaults this cannot read -- an
    argument missing, or defaults which are not a literal dict -- is
    appended to ``unreadable`` as 'Class.method' rather than raising
    from here, so that the test asserting there are none can say which.
    """
    out = []
    for path, tree, cls, fn in declarations.handlers():
        for dec in fn.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if ast.unparse(dec.func).split('.')[-1] != 'use_declared_args':
                continue
            # Bound by name, as Python binds them: positionally in
            # use_declared_args()'s parameter order, then by keyword.
            arguments = dict(zip(('parameters', 'defaults'), dec.args))
            arguments.update((keyword.arg, keyword.value)
                             for keyword in dec.keywords)
            try:
                defaults = ast.literal_eval(arguments['defaults'])
            except (KeyError, ValueError, TypeError, SyntaxError):
                defaults = None
            if not isinstance(defaults, dict) or (
                    'parameters' not in arguments):
                if unreadable is not None:
                    unreadable.append('%s.%s' % (cls.name, fn.name))
                continue
            out.append((cls.name, fn.name,
                        ast.unparse(arguments['parameters']), defaults, fn))
    return out


def _swagger_parameter_argument(fn):
    """What a handler passes swagger_helper() as its parameter list."""
    for dec in fn.decorator_list:
        if not (isinstance(dec, ast.Call)
                and 'swagger_helper' in ast.unparse(dec)):
            continue
        call = dec.args[0] if dec.args else None
        if isinstance(call, ast.Call) and len(call.args) >= 3:
            return ast.unparse(call.args[2])
    return None


def _signature_defaults(fn):
    args = fn.args
    positional = args.posonlyargs + args.args
    out = {}
    for name, default in zip(positional[len(positional) - len(args.defaults):],
                             args.defaults):
        out[name.arg] = ast.literal_eval(default)
    for name, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is not None:
            out[name.arg] = ast.literal_eval(default)
    return out


class UseDeclaredArgsCallSiteTestCase(base.ShakenFistTestCase):
    """What each call site passes, which the decorator cannot see."""

    def test_the_sites_are_still_found(self):
        # Every assertion below passes vacuously if the AST walk stops
        # finding the decorator.
        unreadable = []
        sites = _use_declared_args_sites(unreadable)
        self.assertEqual(
            [], unreadable,
            'these use_declared_args() sites pass a declaration list and '
            'a defaults dict this test cannot read, so nothing below '
            'checks them: %s' % ', '.join(unreadable))
        self.assertEqual(
            6, len(sites),
            'expected the six endpoints which parse declared query '
            'parameters, found %s'
            % sorted((cls, method) for cls, method, _, _, _ in sites))

    def test_each_site_parses_the_list_it_publishes(self):
        """The whole point: one list, read twice.

        Passing a different list to each would rebuild the duplication
        this replaced, in a form which looks right.
        """
        for cls, method, parsed, _, fn in _use_declared_args_sites():
            self.assertEqual(
                _swagger_parameter_argument(fn), parsed,
                '%s.%s parses %s and publishes something else'
                % (cls, method, parsed))

    def test_each_default_is_the_one_the_handler_signature_gives(self):
        """Why the defaults are passed rather than read.

        use_declared_args() cannot read them: 24 of the 25 per-method
        decorators in base.py do not use functools.wraps, so by the
        time it runs inspect.signature() sees a wrapper's (*args,
        **kwargs). This is that guarantee, one call site away.
        """
        for cls, method, _, defaults, fn in _use_declared_args_sites():
            signature = _signature_defaults(fn)
            for name, value in defaults.items():
                self.assertIn(
                    name, signature,
                    '%s.%s passes a default for %r, which its handler '
                    'does not accept' % (cls, method, name))
                self.assertEqual(
                    value, signature[name],
                    '%s.%s parses %r with a default of %r and its handler '
                    'signature says %r; webargs always passes the schema\'s '
                    'value, so the signature default is the one that is '
                    'wrong to read'
                    % (cls, method, name, value, signature[name]))

    def test_no_endpoint_hand_authors_a_webargs_schema_any_more(self):
        # Not a style rule: a hand-authored schema is a second
        # statement of the parameters, and declarations.py derives a
        # query location from it, so the two can disagree about which
        # parameters exist.
        offenders = []
        for path, tree, cls, fn in declarations.handlers():
            for dec in fn.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if ast.unparse(dec.func).split('.')[-1] == 'use_kwargs':
                    offenders.append('%s.%s' % (cls.name, fn.name))
        self.assertEqual([], offenders, ', '.join(offenders))


class UseDeclaredArgsTestCase(base.ShakenFistTestCase):
    """What the decorator builds, and what it refuses."""

    PARAMETERS = [
        ('thing_ref', 'path', 'uuidorname', 'A thing.', True),
        ('namespace', 'body', 'namespace', 'A namespace.', False),
        ('all', 'query', 'boolean', 'Everything.', False),
        ('offset', 'query', 'unsignedinteger', 'From here.', False),
    ]

    def _schema(self, parameters=None, defaults=None):
        return api_base.declared_args_schema(
            self.PARAMETERS if parameters is None else parameters,
            {'all': False, 'offset': 0} if defaults is None else defaults)

    def test_only_the_query_declarations_are_bound(self):
        # Not the path parameter, which werkzeug has already matched and
        # webargs cannot see, and not the body one, which log_request
        # merges into kwargs.
        self.assertEqual(['all', 'offset'], sorted(self._schema()))

    def test_each_field_has_the_type_the_declaration_names(self):
        schema = self._schema()
        self.assertIsInstance(schema['all'], fields.Boolean)
        self.assertIsInstance(schema['offset'], fields.Integer)

    def test_each_field_carries_the_default_it_was_given(self):
        schema = self._schema(defaults={'all': True, 'offset': 7})
        self.assertIs(True, schema['all'].load_default)
        self.assertEqual(7, schema['offset'].load_default)

    def test_the_schema_parses_a_query_string_the_way_it_used_to(self):
        # The hand-authored schemas were fields.Int(load_default=0) and
        # fields.Boolean(load_default=False). This is the same parse:
        # strings off the wire coerce, and an absent key takes the
        # default.
        schema = marshmallow.Schema.from_dict(self._schema())()
        self.assertEqual(
            {'all': True, 'offset': 10},
            schema.load({'all': 'true', 'offset': '10'}))
        self.assertEqual({'all': False, 'offset': 0}, schema.load({}))

    def test_a_query_parameter_with_no_default_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration,
            self._schema, None, {'all': False})

    def test_a_default_for_something_undeclared_is_refused(self):
        # Most often a parameter whose location was changed to body and
        # whose default was left behind, which would otherwise bind
        # nothing and silently stop being parsed.
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._schema, None,
            {'all': False, 'offset': 0, 'namespace': None})

    def test_an_endpoint_declaring_no_query_parameters_is_refused(self):
        # The decorator exists to parse the query string. Applied to an
        # endpoint with nothing to parse it would bind an empty schema,
        # which is a mistake rather than a no-op.
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._schema,
            [('namespace', 'body', 'namespace', 'A namespace.', False)], {})

    def test_a_location_which_cannot_carry_a_query_parameter_is_refused(self):
        """The parameters are published as query whatever is passed.

        So parsing them from the JSON body alone, or a form, would read
        them from somewhere the specification and the audit do not say
        -- and nothing else would notice, because the audit reads the
        same list the specification publishes.
        """
        for location in ('json', 'form', 'headers'):
            self.assertRaises(
                exceptions.InvalidAPIDeclaration, api_base.use_declared_args,
                self.PARAMETERS, {'all': False, 'offset': 0},
                location=location)

    def test_each_location_which_can_carry_one_is_accepted(self):
        for location in api_base.DECLARED_ARGS_LOCATIONS:
            api_base.use_declared_args(
                self.PARAMETERS, {'all': False, 'offset': 0},
                location=location)

    def test_a_structured_type_in_the_query_string_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, self._schema,
            [('shape', 'query', 'dict', 'A shape.', False)],
            {'shape': None})

    def test_a_malformed_declaration_is_left_to_swagger_helper(self):
        # Reported from one place. Every endpoint using this decorator
        # passes the same list to swagger_helper(), which checks the
        # shape, the location and the type with a message naming the
        # endpoint.
        schema = self._schema(
            [('all', 'query'), ('offset', 'query', 'unsignedinteger',
                                'From here.', False)],
            {'offset': 0})
        self.assertEqual(['offset'], sorted(schema))


class DeclarationElementsTestCase(base.ShakenFistTestCase):
    """Resolving a parameter list which is a name rather than a literal.

    A decoration site carries nothing but the name, so the list is
    resolved from an index of every module level list in the API
    package -- which is how package_functions() resolves a decorator
    name, and carries the same ambiguity.
    """

    def _tuple(self, source):
        return ast.parse(source, mode='eval').body

    def test_a_literal_list_is_its_own_elements(self):
        node = self._tuple("[('all', 'query', 'boolean', 'd', False)]")
        elements = declarations.declaration_elements(node)
        self.assertEqual(1, len(elements))

    def test_an_empty_literal_list_is_not_unreadable(self):
        # An endpoint legitimately declaring no parameters answers the
        # empty tuple; only something this cannot read answers None.
        self.assertEqual(
            (), declarations.declaration_elements(self._tuple('[]')))

    def test_a_name_the_package_defines_resolves(self):
        node = self._tuple('BLOB_DATA_PARAMETERS')
        elements = declarations.declaration_elements(node)
        self.assertEqual(3, len(elements))

    def test_a_name_nothing_defines_is_unreadable(self):
        node = self._tuple('NO_SUCH_PARAMETERS')
        self.assertIsNone(declarations.declaration_elements(node))

    def test_a_name_two_modules_define_is_unreadable(self):
        """Ambiguity is reported rather than resolved by file order.

        The index is keyed on the bare name across the package, so two
        modules defining PARAMETERS differently would otherwise hand
        one module's declarations to the other module's audit -- a
        confidently wrong answer, which is worse than no answer.
        """
        both = {'PARAMETERS': [
            tuple(self._tuple("[('a', 'query', 'boolean', 'd', False)]").elts),
            tuple(self._tuple("[('b', 'query', 'boolean', 'd', False)]").elts),
        ]}
        with mock.patch.object(declarations, 'package_lists',
                               return_value=both):
            self.assertIsNone(
                declarations.declaration_elements(self._tuple('PARAMETERS')))
            # And that the probe would have resolved a unique one, so
            # the assertion above is about the ambiguity and not about
            # the patch.
            both['PARAMETERS'] = both['PARAMETERS'][:1]
            self.assertEqual(
                1, len(declarations.declaration_elements(
                    self._tuple('PARAMETERS'))))

    def test_something_which_is_neither_is_unreadable(self):
        self.assertIsNone(
            declarations.declaration_elements(self._tuple('build_them()')))


class CoercingFieldTestCase(base.ShakenFistTestCase):
    """The field a declared query parameter is parsed with.

    Coercion without validation, deliberately: the declared bound is
    enforced by the compiled validation path, which answers 400, and by
    the handler for the modes where that path only warns. A validator
    here would be another copy of the bound, one which ignored
    API_VALIDATION_MODE.
    """

    def _field(self, token, load_default=marshmallow.missing):
        return validation.coercing_field(
            api_base.ARGTYPES[token], load_default, 'a test parameter')

    def test_a_boolean_token_builds_a_boolean_field(self):
        self.assertIsInstance(self._field('boolean'), fields.Boolean)

    def test_a_string_token_builds_a_string_field(self):
        self.assertIsInstance(self._field('string'), fields.String)

    def test_an_integer_token_builds_an_integer_field(self):
        self.assertIsInstance(self._field('unsignedinteger'), fields.Integer)

    def test_a_formatted_string_token_builds_a_string_field(self):
        # Every string-shaped token -- uuid, namespace, url -- renders
        # type 'string' with a prose format, and the format is a
        # description rather than something to parse with.
        for token in ('uuid', 'uuidorname', 'namespace', 'url', 'netblock'):
            self.assertIsInstance(self._field(token), fields.String, token)

    def test_the_declared_bound_does_not_become_a_validator(self):
        # unsignedinteger renders minimum 0. A Range validator here
        # would refuse a negative offset even under API_VALIDATION_MODE
        # off, where the declared bounds are meant to stop applying and
        # the handler's own check is the refusal.
        field = self._field('unsignedinteger')
        self.assertEqual([], list(field.validators))

    def test_the_default_is_what_the_field_answers_when_nothing_arrives(self):
        field = self._field('unsignedinteger', 0)
        self.assertEqual(0, field.load_default)

    def test_a_structured_token_is_refused(self):
        for token in ('dict', 'arrayofstring', 'diskspec', 'any'):
            self.assertRaises(
                exceptions.InvalidAPIDeclaration, self._field, token)

    def test_an_unknown_token_renders_nothing_and_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration,
            validation.coercing_field, {}, None, 'a test parameter')
