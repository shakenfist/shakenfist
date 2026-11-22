# Copyright 2019 Michael Still and contributors

"""The responses swagger_helper() adds on the endpoints' behalf.

The decorators answer before any handler runs -- verify_token with a
401, caller_is_admin with a 401 or a 403 -- so those codes are part of
every guarded endpoint's contract whether or not its declaration says
so. swagger_helper() publishes them, and used to do it by appending to
the endpoint's own responses list, which silently overwrote an
endpoint's own 401 description (issue 4657).
"""

from shakenfist import exceptions
from shakenfist.external_api import base as api_base
from shakenfist.tests import base


def _helper(responses, **kwargs):
    return api_base.swagger_helper('tests', 'A test endpoint.', [],
                                   responses, **kwargs)


class SwaggerHelperResponsesTestCase(base.ShakenFistTestCase):
    def test_authenticated_endpoint_declares_401(self):
        out = _helper([(200, 'Fine.', None)])
        self.assertIn(401, out['responses'])
        self.assertIn('You must authenticate',
                      out['responses'][401]['description'])

    def test_unauthenticated_endpoint_declares_no_401(self):
        out = _helper([(200, 'Fine.', None)], requires_auth=False)
        self.assertNotIn(401, out['responses'])

    def test_an_endpoints_own_401_is_not_overwritten(self):
        out = _helper([(401, 'The caller is not a cluster administrator.',
                        None)],
                      requires_admin=True)
        description = out['responses'][401]['description']
        self.assertIn('The caller is not a cluster administrator.',
                      description)
        # The generic cause still appears, after the endpoint's words.
        self.assertIn('You must authenticate', description)
        self.assertLess(
            description.index('cluster administrator'),
            description.index('You must authenticate'))

    def test_admin_endpoint_declares_403(self):
        out = _helper([(200, 'Fine.', None)], requires_admin=True)
        self.assertIn(403, out['responses'])
        self.assertIn('not scoped for administrative operations',
                      out['responses'][403]['description'])
        self.assertIn('administrative scope', out['description'])

    def test_non_admin_endpoint_declares_no_403(self):
        out = _helper([(200, 'Fine.', None)])
        self.assertNotIn(403, out['responses'])

    def test_an_endpoints_own_403_is_extended_not_overwritten(self):
        out = _helper([(403, 'You cannot delete the system namespace.',
                        None)],
                      requires_admin=True)
        description = out['responses'][403]['description']
        self.assertIn('You cannot delete the system namespace.', description)
        self.assertIn('not scoped for administrative operations', description)

    def test_admin_without_auth_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration,
            _helper, [(200, 'Fine.', None)],
            requires_admin=True, requires_auth=False)

    def test_the_callers_responses_list_is_not_mutated(self):
        # The generic 401 used to be append()ed to the endpoint's own
        # list. These lists are module-level literals evaluated once at
        # import, so mutating one through a second call would double
        # its generic responses.
        responses = [(200, 'Fine.', None)]
        _helper(responses)
        self.assertEqual([(200, 'Fine.', None)], responses)


class SwaggerHelperResponseHeadersTestCase(base.ShakenFistTestCase):
    """The optional fourth element of a response declaration.

    A response was a (httpcode, description, sample) three-tuple and
    nothing more, so a header the response really carries could only be
    written in English in its description -- which is how `POST
    /instances` came to serve `Retry-After` while publishing it as prose
    (issue 4240). The parameter side of the same declaration had already
    grown an optional sixth element for constraints; this is the
    response side catching up.
    """

    def test_a_three_tuple_is_still_a_valid_response(self):
        out = _helper([(200, 'Fine.', None)])
        self.assertNotIn('headers', out['responses'][200])

    def test_a_headers_value_which_is_not_a_list_is_refused(self):
        # As InvalidAPIDeclaration naming the response, not as the
        # TypeError iterating None would raise.
        for headers in (None, 'Retry-After', 15):
            with self.assertRaises(
                    exceptions.InvalidAPIDeclaration) as ctx:
                _helper([(503, 'Later.', None, {'headers': headers})])
            self.assertIn('503', str(ctx.exception))

    def test_parameters_or_responses_which_are_not_lists_are_refused(self):
        self.assertRaises(exceptions.InvalidAPIDeclaration, _helper, None)
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, api_base.swagger_helper,
            'tests', 'A test endpoint.', None, [])

    def test_a_declared_header_is_published(self):
        out = _helper([
            (503, 'Try later.', None,
             {'headers': [('Retry-After', 'unsignedinteger', 'Seconds.')]})])

        self.assertEqual(
            {'Retry-After': {
                'type': 'integer', 'format': 'int64', 'minimum': 0,
                'description': 'Seconds.'}},
            out['responses'][503]['headers'])

    def test_a_response_declaring_no_headers_publishes_none(self):
        # An empty list is not an empty 'headers' object: OpenAPI 2.0
        # permits one, but publishing it says something about the
        # response that is not true.
        out = _helper([(200, 'Fine.', None, {'headers': []})])
        self.assertNotIn('headers', out['responses'][200])

    def test_several_headers_on_one_response(self):
        out = _helper([
            (200, 'Fine.', None,
             {'headers': [('X-One', 'string', 'One.'),
                          ('X-Two', 'integer', 'Two.')]})])
        self.assertEqual(
            ['X-One', 'X-Two'], sorted(out['responses'][200]['headers']))

    def test_the_type_vocabulary_is_shared_with_parameters(self):
        # Not a second mapping of tokens to types. ARGTYPES renders a
        # header exactly as it renders a primitive parameter, which is
        # the property that stops the two drifting.
        out = _helper([
            (200, 'Fine.', None,
             {'headers': [('X-When', 'unsignedinteger', 'When.')]})])
        rendered = dict(out['responses'][200]['headers']['X-When'])
        del rendered['description']
        self.assertEqual(api_base.ARGTYPES['unsignedinteger'], rendered)

    def test_the_vocabulary_is_not_mutated_by_rendering(self):
        # The parameter loop deep copies for this reason: a shallow copy
        # aliases a module-level mutable into every declaration in the
        # tree and into the specification flasgger re-reads per request.
        before = dict(api_base.ARGTYPES['unsignedinteger'])
        _helper([(200, 'Fine.', None,
                  {'headers': [('X-When', 'unsignedinteger', 'When.')]})])
        self.assertEqual(before, api_base.ARGTYPES['unsignedinteger'])

    # ------------------------------------------------------------------
    # Every malformed declaration arrives as InvalidAPIDeclaration, so
    # there is one exception type to catch and sf-api does not start.
    # ------------------------------------------------------------------
    def test_a_two_element_response_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper, [(200, 'Fine.')])

    def test_a_five_element_response_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, {}, 'extra')])

    def test_a_response_which_is_not_a_tuple_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper, ['200'])

    def test_extras_which_are_not_a_dictionary_are_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, [('X-One', 'string', 'One.')])])

    def test_an_unknown_extras_key_is_refused(self):
        # Rather than ignored, which is how a response would come to
        # declare a body schema that nothing published.
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, {'schema': {'type': 'object'}})])

    def test_a_header_which_is_not_a_triple_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, {'headers': [('X-One', 'string')]})])

    def test_a_header_with_no_name_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, {'headers': [('', 'string', 'One.')]})])

    def test_a_header_declared_twice_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None,
              {'headers': [('X-One', 'string', 'One.'),
                           ('X-One', 'integer', 'Again.')]})])

    def test_an_unknown_type_token_is_refused(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, {'headers': [('X-One', 'wat', 'One.')]})])

    def test_the_bearer_token_is_not_a_declarable_header_type(self):
        # swagger_helper() injects the Authorization parameter itself,
        # so 'bearer' is not a token an endpoint may declare -- the same
        # rule the parameter loop applies.
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None,
              {'headers': [('Authorization', 'bearer', 'One.')]})])

    def test_a_structured_type_is_refused_on_a_header(self):
        # A header carries a primitive. There is no schema object to
        # nest a structure in, so this would render an invalid
        # specification.
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, {'headers': [('X-One', 'dict', 'One.')]})])

    def test_an_array_of_objects_is_refused_on_a_header(self):
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None,
              {'headers': [('X-One', 'arrayofdict', 'One.')]})])

    def test_the_any_token_is_refused_on_a_header(self):
        # Named rather than inspected: 'any' is the one token with no
        # 'type' key at all, so it passes both structural checks.
        self.assertRaises(
            exceptions.InvalidAPIDeclaration, _helper,
            [(200, 'Fine.', None, {'headers': [('X-One', 'any', 'One.')]})])
