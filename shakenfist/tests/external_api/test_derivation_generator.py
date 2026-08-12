# Copyright 2019 Michael Still and contributors

"""Generate the derivation's input space rather than sampling it.

Phase 1 of PLAN-api-input-validation took five review rounds, and four
of them found a defect in the machinery added by the round before: the
Werkzeug converter regex, the ``flask.request.args`` fallback, the
webargs scope leak, and an emptied parameter list. Every one was
``declarations.py`` misreading source, and every one was a shape which
did not occur anywhere in the tree -- so no amount of testing against
the tree could have found them, and neither could mutating it, which
only permutes shapes already present.

While the declarations are documentation a misread costs a wrong line
in the published API. Once phase 3 compiles them, the same misread
rejects a valid request: a ``path`` parameter derived as ``body``
produces a schema hunting the JSON body for a URL segment, and D6's
query-string fallback is granted only to parameters derived as
``query``. That is why this is a precondition for compiling rather
than a step in it.

The oracle is free because each case is *constructed* knowing where
its parameter comes from, which is what makes this different from
mutating declarations in the tree: ``audit()`` derives the truth and
compares, so flipping a declared location and asserting drift tests the
comparison. Every real defect was on the other side of that comparison,
in the derivation.

Three axes are crossed rather than sampled, because the defects were in
how the sources interact -- one axis reporting a problem must not stop
another from deriving, and one axis deriving must not stop another from
reporting. The fourth axis in the plan, declaration well-formedness, is
about reading the declaration rather than deriving the location and is
covered case-by-case in test_parameter_declarations.py.
"""

import itertools
import os
import shutil
import tempfile

from shakenfist.external_api import declarations
from shakenfist.tests import base


# Each axis value is (label, fragments, derives, problem).
#
# ``derives`` is what this value alone contributes to the derivation:
# 'path', 'query' or None. ``problem`` is a substring of the report
# ``audit()`` must produce for it, or None when the value is readable.
# The expected outcome of a case is composed from the three, which is
# the whole point -- an axis is not allowed to mask another.

ROUTES = [
    ('no path parameter',
     "api.add_resource(FakeEndpoint, '/fakes')",
     None, None),
    ('bare name',
     "api.add_resource(FakeEndpoint, '/fakes/<alpha>')",
     'path', None),
    # The converter forms are here because a bare <([a-z_]+)> regex
    # missed them, silently skipping three LabelEndpoint declarations.
    ('path converter',
     "api.add_resource(FakeEndpoint, '/fakes/<path:alpha>')",
     'path', None),
    ('int converter with arguments',
     "api.add_resource(FakeEndpoint, '/fakes/<int(min=1):alpha>')",
     'path', None),
    # An unreadable route must not merely be skipped: skipping empties
    # the class's route set, which derives every one of its parameters
    # to 'body' and sends the fixer to rewrite a correct 'path'.
    ('non-literal route',
     "ROUTE = '/fakes/<alpha>'\napi.add_resource(FakeEndpoint, ROUTE)",
     None, 'mounted on a route this cannot read'),
]

WEBARGS = [
    ('no use_kwargs', '', '', None, None),
    ('schema on the class',
     "    get_args = {'alpha': None}\n",
     "    @use_kwargs(get_args, location='query')\n",
     'query', None),
    ('schema on the module',
     '', "    @use_kwargs(get_args, location='query')\n",
     'query', None),
    # An inline dict is readable as a dict but is not a name any scope
    # defines, so the keys cannot be attributed to a scope; reported
    # rather than resolved to nothing.
    ('inline dict schema',
     '', "    @use_kwargs({'alpha': None}, location='query')\n",
     None, 'schema this cannot resolve'),
    # Bound at json rather than query: not a query parameter, and the
    # earlier class-wide scan derived it as one anyway.
    ('schema bound at json',
     "    get_args = {'alpha': None}\n",
     "    @use_kwargs(get_args, location='json')\n",
     None, None),
    # D6's fallback location. A query schema too -- reading it as "not
    # query" would send the fixer to rewrite the very declarations
    # issue 3629's fix made true.
    ('schema bound at json_or_query',
     "    get_args = {'alpha': None}\n",
     "    @use_kwargs(get_args, location='json_or_query')\n",
     'query', None),
    # The next three are the scope-resolution shapes. The module always
    # defines get_args = {'alpha': None}, so each of these asks whether
    # the class-level definition really wins. Under the original
    # cross-scope leak -- and under a first-definition-to-yield-a-key
    # rule, which is the same leak with an extra step -- alpha would be
    # derived 'query' from the module in all three.
    ('empty schema on the class',
     '    get_args = {}\n',
     "    @use_kwargs(get_args, location='query')\n",
     None, None),
    ('class schema shadowing the module',
     "    get_args = {'beta': None}\n",
     "    @use_kwargs(get_args, location='query')\n",
     None, None),
    # Defined but not as a dict literal: reported by name rather than
    # falling through to the module, because 'not found' and 'cannot
    # read this' must not share an answer.
    ('unreadable schema on the class',
     '    get_args = OTHER\n',
     "    @use_kwargs(get_args, location='query')\n",
     None, 'is assigned something this cannot read'),
]

REQUEST_ARGS = [
    ('no request.args read', '        pass\n', None, None),
    ('.get() with a literal',
     "        flask.request.args.get('alpha')\n", 'query', None),
    ('literal subscript',
     "        flask.request.args['alpha']\n", 'query', None),
    # .args on something which is not the request. Contributes nothing
    # and is not a problem: the walk must not claim every attribute
    # named args.
    ('args on a non-request object',
     "        helper.args.get('alpha')\n", None, None),
    # A key the walk cannot name. Reported, because deriving 'body'
    # from it is the confident wrong answer this module refuses.
    ('.get() with a non-literal key',
     '        flask.request.args.get(KEY)\n',
     None, 'key which is not a literal'),
]

MODULE = '''import flask
from flasgger import swag_from
from webargs.flaskparser import use_kwargs

from shakenfist.external_api import base as api_base

KEY = 'alpha'
OTHER = {'alpha': None}
get_args = {'alpha': None}


class FakeEndpoint(api_base.Resource):
%(class_level)s
    @swag_from(api_base.swagger_helper(
        'fakes', 'A fake.',
        [('alpha', '%(location)s', 'string', 'A parameter.', %(required)s)],
        []))
%(decorator)sdef get(self, alpha=None):
%(body)s'''


class DerivationGeneratorTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir)

    def _write(self, name, source):
        with open(os.path.join(self.tempdir, name), 'w',
                  encoding='utf-8') as f:
            f.write(source)

    def _build(self, route, webargs, request_args, location):
        _, mount, _, _ = route
        _, class_level, decorator, _, _ = webargs
        _, body, _, _ = request_args

        self._write('app.py', mount + '\n')
        self._write('fake.py', MODULE % {
            'class_level': class_level,
            'decorator': decorator + '    ' if decorator else '    ',
            'location': location,
            # Phase 1's rule: a path parameter is always required.
            'required': location == 'path',
            'body': body,
        })

    @staticmethod
    def _expected(route, webargs, request_args):
        derives = [axis[-2] for axis in (route, webargs, request_args)]
        if 'path' in derives:
            location = 'path'
        elif 'query' in derives:
            location = 'query'
        else:
            location = 'body'
        problems = {axis[-1] for axis in (route, webargs, request_args)}
        return location, problems - {None}

    def test_every_combination_derives_what_it_was_built_to_mean(self):
        """The cross product, with the declaration written to match.

        Each case declares its parameter at the location the case was
        constructed to produce, so an empty ``drifted`` means the
        derivation agreed with the construction. The problems assertion
        is exact in both directions: a missing report is a source read
        with false confidence, and an extra one is a source the audit
        would refuse to derive from for no reason -- which, because the
        fixer and the pre-commit hook both stop on any problem, would
        block a clean tree.
        """
        cases = list(itertools.product(ROUTES, WEBARGS, REQUEST_ARGS))
        # Pinned so that dropping an axis value is a failure rather than
        # a quietly smaller cross product.
        self.assertEqual(
            len(ROUTES) * len(WEBARGS) * len(REQUEST_ARGS), len(cases))
        self.assertEqual(225, len(cases))

        for route, webargs, request_args in cases:
            label = '%s + %s + %s' % (route[0], webargs[0], request_args[0])
            with self.subTest(label):
                location, want_problems = self._expected(
                    route, webargs, request_args)
                self._build(route, webargs, request_args, location)

                drifted, underivable, problems = declarations.audit(
                    self.tempdir)

                self.assertEqual(
                    [], [(d.name, d.location, want) for d, want in drifted],
                    '%s: declared %s, which is what this case was built to '
                    'mean, but the derivation disagreed' % (label, location))
                self.assertEqual([], underivable, label)

                for fragment in want_problems:
                    self.assertTrue(
                        any(fragment in p for p in problems),
                        '%s: expected a problem containing %r, got %s'
                        % (label, fragment, problems))
                self.assertEqual(
                    len(want_problems), len(problems),
                    '%s: expected %d problem(s), got %s'
                    % (label, len(want_problems), problems))

    def test_a_wrong_declaration_drifts_in_every_combination(self):
        """The comparison, which is the other side of the assertion above.

        Written as its own pass rather than folded into the one above:
        that one holds ``drifted`` empty, so on its own it cannot
        distinguish a derivation which is right from one which returns
        the declared value. Declaring the one location the case cannot
        mean must always drift.
        """
        for route, webargs, request_args in itertools.product(
                ROUTES, WEBARGS, REQUEST_ARGS):
            label = '%s + %s + %s' % (route[0], webargs[0], request_args[0])
            with self.subTest(label):
                location, _ = self._expected(route, webargs, request_args)
                wrong = 'query' if location != 'query' else 'body'
                self._build(route, webargs, request_args, wrong)

                drifted, _, _ = declarations.audit(self.tempdir)

                self.assertEqual(
                    [(wrong, location)],
                    [(d.location, want) for d, want in drifted],
                    '%s: declared %s where the case means %s, and the audit '
                    'did not report drift' % (label, wrong, location))
