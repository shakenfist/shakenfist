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
