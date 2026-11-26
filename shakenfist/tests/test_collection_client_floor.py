# Copyright 2026 Michael Still and contributors

"""The collection declares a client floor, and says the same one everywhere.

The native modules under the collection's plugins/modules/ call the
shakenfist_client SDK, and they pass keyword arguments which arrived in
particular releases. requirements.txt used to ask for a bare
`shakenfist-client`, so a control node with an old client failed with a
TypeError or an AttributeError from inside a module -- which reads like a
module bug rather than a version problem (issue #3310).

The floor was derived by walking every `client.<verb>(...)` call in the
four non-sf_claim modules and checking each verb and keyword argument
against every client-python release tag. `allocate_network(provide_dns=)`
is the binding one: it arrived in 0.7.5, and sf_network passes it whenever
the operator asks for DNS.

Two things are worth guarding, and neither is "is 0.7.5 still the right
number" -- that question needs client-python's history, which is a
different repository and not available here.

The first is that a floor is declared at all. It went missing once by
being written as a bare name, and a bare name looks deliberate.

The second is that the three places which state it do not drift. The
number lives in requirements.txt; the collection README quotes it for
operators and the sf_instance capability check reasons about where it
sits relative to 0.8.4, so a bump which moved one and not the others
would leave the documentation lying about what pip will accept.
"""

import io
import os
import re

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from shakenfist.tests import base


COLLECTION = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'deploy', 'collection')

REQUIREMENTS = os.path.join(COLLECTION, 'requirements.txt')
README = os.path.join(COLLECTION, 'README.md')

# The client release which added create_instance(timeout=).
# sf_instance._create_accepts_timeout() exists because the floor is below
# this, so if the floor ever reaches it that capability check becomes
# dead code and should be reconsidered rather than left to rot.
TIMEOUT_ARRIVED_IN = Version('0.8.4')


def _requirements():
    """Every requirement in the collection's requirements.txt."""
    parsed = {}
    with io.open(REQUIREMENTS) as f:
        for line in f:
            line = line.split('#')[0].strip()
            if not line:
                continue
            requirement = Requirement(line)
            parsed[requirement.name] = requirement
    return parsed


def _floor(specifier):
    """The lower bound a specifier set declares, or None."""
    for clause in specifier:
        if clause.operator in ('>=', '=='):
            return Version(clause.version)
    return None


class CollectionClientFloorTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.requirements = _requirements()

    def test_the_file_parses_to_something(self):
        """A vacuous pass is a failure.

        Every assertion below reads self.requirements, so a file which
        parsed to nothing -- renamed, moved, or commented out entirely --
        would pass them all.
        """
        self.assertIn(
            'shakenfist-client', self.requirements,
            'the collection requirements no longer name shakenfist-client; '
            'if the SDK requirement moved, move this test with it')

    def test_the_client_requirement_declares_a_floor(self):
        requirement = self.requirements['shakenfist-client']
        self.assertNotEqual(
            SpecifierSet(), requirement.specifier,
            'shakenfist-client is declared without a version, so a '
            'control node with a client older than the modules need '
            'fails inside a module rather than in pip (issue #3310)')
        self.assertIsNotNone(
            _floor(requirement.specifier),
            'shakenfist-client is constrained but has no lower bound: %s'
            % requirement.specifier)

    def test_the_floor_is_not_capped(self):
        """No upper bound, deliberately.

        A cap would make every new client release an incompatibility by
        declaration rather than by evidence. If one is ever added it
        should be because a release actually broke a module, and this
        assertion is where to say so.
        """
        capped = [str(clause) for clause in
                  self.requirements['shakenfist-client'].specifier
                  if clause.operator in ('<', '<=')]
        self.assertEqual(
            [], capped,
            'shakenfist-client now has an upper bound (%s). If a client '
            'release really did break a module, say which here.'
            % ', '.join(capped))

    def test_the_readme_quotes_the_declared_floor(self):
        floor = _floor(self.requirements['shakenfist-client'].specifier)
        with io.open(README) as f:
            readme = f.read()
        self.assertIn(
            'shakenfist-client>=%s' % floor, readme,
            'the collection README does not quote the floor '
            'requirements.txt declares (%s), so one of them is lying to '
            'operators about what pip will accept' % floor)

    def test_the_timeout_capability_check_is_still_needed(self):
        """The floor is below the release which added the argument.

        _create_accepts_timeout() feature-detects create_instance(timeout=)
        instead of relying on a version. That is only the right shape while
        the floor sits below the release which added it; at or above it the
        check is dead code and the reasoning in its docstring is wrong.
        """
        floor = _floor(self.requirements['shakenfist-client'].specifier)
        self.assertLess(
            floor, TIMEOUT_ARRIVED_IN,
            'the client floor (%s) has reached %s, where '
            'create_instance() gained its timeout argument. '
            'sf_instance._create_accepts_timeout() and its docstring are '
            'now reasoning about a case the floor rules out' % (
                floor, TIMEOUT_ARRIVED_IN))

    def test_sf_instance_does_not_claim_the_client_is_unpinned(self):
        """The docstring that the floor made wrong.

        _create_accepts_timeout() justified itself with "the collection
        requires shakenfist-client unpinned". That stopped being true
        when the floor was declared, and the real reason -- that
        ansible-galaxy does not read requirements.txt, so nothing
        enforces it -- is a different argument that happens to reach the
        same conclusion.
        """
        path = os.path.join(
            COLLECTION, 'plugins', 'modules', 'sf_instance.py')
        with io.open(path) as f:
            source = f.read()
        self.assertIsNone(
            re.search(r'shakenfist-client unpinned', source),
            'sf_instance.py still says the collection requires '
            'shakenfist-client unpinned, which requirements.txt '
            'contradicts')
