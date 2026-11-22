# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Every response header the API sets, against the declarations.

`swagger_helper()` response declarations could not express a response
header at all until issue 4240, so `POST /instances` served
`Retry-After: 15` -- the part of a transient capacity refusal a client
is meant to read and act on -- while publishing it in English prose
that no generated client can discover.

This audits the working tree the way `test_parameter_declarations.py`
does: it finds every place the API layer sets a response header (in
the forms `_sites_in()` lists), and
fails unless that site appears in the table below naming the responses
which publish it. A new header-setting site therefore has to be
triaged into a declaration rather than being added silently, which is
the only part of this that the published specification cannot check
for itself.
"""

import ast
import glob
import os

from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.tests import base


# Where the API layer sets a response header, and which declared
# responses publish it. Keyed by (module, enclosing class, enclosing
# function, header) -- the class is None for a module-level helper --
# and valued by the (class, method, httpcode) declarations which must
# publish that header. The class is in the key because a module holds
# many classes with a ``post``: without it a second handler setting the
# same header would match an existing entry and be checked against
# the wrong declaration.
#
# A helper rather than a handler can be the setter -- capacity_error()
# is one, and _netdesc_allocate_address() is shared between two
# endpoints -- which is why this is a table and not a derivation: no
# static read of this tree says which endpoints reach a given helper.
# The audit's value is that the table cannot quietly fall behind the
# code, because a site missing from it fails.
HEADER_SITES: dict[tuple[str, str | None, str, str],
                   set[tuple[str, str, int]]] = {
    # The transient capacity refusal. Its two call sites are both in
    # InstancesEndpoint.post.
    ('base.py', None, 'capacity_error', 'Retry-After'): {
        ('InstancesEndpoint', 'post', 507),
    },
    # A database failure while recording a network interface, shared
    # between instance create and interface hotplug.
    ('instance.py', None, '_netdesc_allocate_address', 'Retry-After'): {
        ('InstancesEndpoint', 'post', 503),
        ('InstanceInterfacesEndpoint', 'post', 503),
    },
    # A database failure while recording the placement.
    ('instance.py', 'InstancesEndpoint', 'post', 'Retry-After'): {
        ('InstancesEndpoint', 'post', 503),
    },
}

# The methods which add to a headers mapping, as werkzeug's Headers
# spells them. ``get`` and friends read, and are not sites.
_HEADER_MUTATORS = {'add', 'add_header', 'set', 'setdefault', 'extend',
                    'update', '__setitem__'}

# The response constructors whose third positional argument, or
# ``headers=`` keyword, is the response's headers. A ``headers=`` on
# anything else -- requests.request() to a peer, say -- is the headers
# of an outgoing request and not a site.
_RESPONSE_CONSTRUCTORS = {'Response', 'make_response'}


def _headers_value(node):
    """Is this expression a ``.headers`` attribute?"""
    return isinstance(node, ast.Attribute) and node.attr == 'headers'


def _names_in(node):
    """The header names a dict literal sets, or [None] if not a literal.

    None is how a site this audit cannot read reaches the unreadable
    assertion, rather than being skipped.
    """
    if isinstance(node, ast.Dict) and all(
            key is not None for key in node.keys):
        names = [declarations.literal(key) for key in node.keys]
        return [name if isinstance(name, str) else None for name in names]
    return [None]


def _call_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _sites_in(node):
    """The header names one AST node sets on a response.

    Recognised: ``x.headers['Name'] = ...``; a mutator called on
    ``x.headers`` (``add``, ``set``, ``setdefault``, ``update`` and the
    rest of _HEADER_MUTATORS); and a ``headers=`` keyword or third
    positional argument to ``flask.Response`` or ``make_response``.
    Where the name or the mapping is not a literal the site is still
    reported, as None, so that it fails as unreadable.

    Not recognised: flask-restful's ``return body, code, headers``
    tuple. The API layer's helpers return three-tuples for unrelated
    reasons (util.py and network.py return (object, object, error)),
    so a tuple return cannot be told apart from a header site
    statically. No handler uses that form; one that starts to will not
    be caught here.
    """
    if isinstance(node, ast.Assign):
        return [declarations.literal(target.slice)
                for target in node.targets
                if isinstance(target, ast.Subscript)
                and _headers_value(target.value)]

    if not isinstance(node, ast.Call):
        return []

    func = node.func
    if (isinstance(func, ast.Attribute) and _headers_value(func.value)
            and func.attr in _HEADER_MUTATORS):
        if func.attr in ('update', 'extend'):
            return _names_in(node.args[0]) if node.args else [None]
        return [declarations.literal(node.args[0]) if node.args else None]

    if _call_name(func) in _RESPONSE_CONSTRUCTORS:
        names = []
        if len(node.args) >= 3:
            names.extend(_names_in(node.args[2]))
        for keyword in node.keywords:
            if keyword.arg == 'headers':
                names.extend(_names_in(keyword.value))
        return names

    return []


def _header_assignments():
    """(module, class, function, header) for every header the API sets.

    Found by AST rather than by grep so that a header set on a response
    held under any local name still counts: the ``.headers`` attribute
    is what matters, not what the response object is called.
    """
    found = set()
    for path in sorted(glob.glob(
            os.path.join(declarations.API_DIR, '*.py'))):
        with open(path) as f:
            found |= _sites_in_source(os.path.basename(path), f.read())
    return found


def _sites_in_source(module, source):
    """(module, class, function, header) for each site in one module."""
    found = set()
    tree = ast.parse(source)
    scopes = [node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef,
                                   ast.AsyncFunctionDef,
                                   ast.ClassDef))]
    for node in ast.walk(tree):
        for name in _sites_in(node):
            enclosing = sorted(
                (scope for scope in scopes
                 if scope.lineno <= node.lineno
                 <= (scope.end_lineno or 0)),
                key=lambda scope: scope.lineno)
            # The innermost enclosing function, which for a handler
            # is the handler and for a helper is the helper, and
            # the innermost class around that function.
            functions = [scope for scope in enclosing
                         if not isinstance(scope, ast.ClassDef)]
            function = functions[-1] if functions else None
            classes = [scope for scope in enclosing
                       if isinstance(scope, ast.ClassDef)
                       and function is not None
                       and scope.lineno <= function.lineno]
            found.add((module,
                       classes[-1].name if classes else None,
                       function.name if function else None,
                       name if isinstance(name, str) else None))
    return found


class ResponseHeaderDeclarationTestCase(base.ShakenFistTestCase):

    def test_every_header_the_api_sets_is_in_the_table(self):
        found = _header_assignments()

        # Nothing unreadable: a site whose header name or enclosing
        # function could not be resolved would otherwise compare
        # unequal to every table key and read as a new site, which is
        # the right failure but the wrong message. The class is
        # legitimately None for a module-level helper.
        unreadable = [site for site in found
                      if site[2] is None or site[3] is None]
        self.assertEqual([], unreadable, 'unreadable header sites: %s'
                         % (unreadable,))

        self.assertEqual(
            set(HEADER_SITES), found,
            'the API sets response headers this audit does not know '
            'about, or the table names sites which no longer exist. '
            'Each site needs the declarations which publish it: '
            'missing %s, stale %s'
            % (sorted(found - set(HEADER_SITES)),
               sorted(set(HEADER_SITES) - found)))

    def test_every_response_which_carries_a_header_declares_it(self):
        # Read through declarations.response_headers() with base.py's
        # real namespace, because a header declared as
        # api_base.RETRY_AFTER_HEADER is assembled from another
        # constant and so is not a literal a source read can resolve.
        declared: dict[tuple[str, str, int], set[str]] = {}
        for path, tree, cls, fn in declarations.handlers():
            for httpcode, names in declarations.response_headers(
                    fn, vars(api_base)).items():
                self.assertIsNotNone(
                    names,
                    f'{cls.name}.{fn.name} has a response declaration '
                    f'this audit cannot read')
                declared[(cls.name, fn.name, httpcode)] = names

        missing = []
        for (module, _, function, header), responses in \
                HEADER_SITES.items():
            for response in responses:
                if response not in declared:
                    missing.append(
                        '%s:%s sets %s on %s, which declares no such '
                        'response at all' % (module, function, header,
                                             response))
                elif header not in declared[response]:
                    missing.append(
                        '%s:%s sets %s on %s, which does not publish it'
                        % (module, function, header, response))

        self.assertEqual([], missing, '\n'.join(missing))

    def test_the_audit_can_tell_a_declared_header_from_an_undeclared_one(self):
        """The positive control.

        Both assertions above pass trivially if response_headers()
        answers an empty set for everything, which is what it did
        before the fourth tuple element existed.
        """
        source = ast.parse(
            'class Probe:\n'
            '    @swag_from(api_base.swagger_helper(\n'
            "        's', 'd', [], [\n"
            "            (200, 'Fine.', None),\n"
            "            (503, 'Later.', None,\n"
            "             {'headers': [('Retry-After', 'integer', 'S.')]})\n"
            '        ]))\n'
            '    def post(self):\n'
            '        pass\n')
        fn = source.body[0].body[0]

        self.assertEqual(
            {200: set(), 503: {'Retry-After'}},
            declarations.response_headers(fn, {}))


class HeaderSiteDiscoveryTestCase(base.ShakenFistTestCase):
    """The positive controls for _sites_in_source().

    The table comparison above passes equally well when the walk has
    stopped recognising a form, so each recognised form is proven here
    on a probe module.
    """

    def _sites(self, body):
        return _sites_in_source('probe.py', body)

    def test_a_subscript_assignment_is_a_site(self):
        self.assertEqual(
            {('probe.py', None, 'helper', 'Retry-After')},
            self._sites('def helper(resp):\n'
                        "    resp.headers['Retry-After'] = '15'\n"))

    def test_each_mutator_is_a_site(self):
        for mutator in ('add', 'set', 'setdefault', 'add_header'):
            self.assertEqual(
                {('probe.py', None, 'helper', 'X-Probe')},
                self._sites('def helper(resp):\n'
                            "    resp.headers.%s('X-Probe', '1')\n"
                            % mutator),
                mutator)

    def test_an_update_names_every_key_it_sets(self):
        self.assertEqual(
            {('probe.py', None, 'helper', 'X-One'),
             ('probe.py', None, 'helper', 'X-Two')},
            self._sites('def helper(resp):\n'
                        "    resp.headers.update({'X-One': '1', "
                        "'X-Two': '2'})\n"))

    def test_a_response_constructed_with_headers_is_a_site(self):
        self.assertEqual(
            {('probe.py', None, 'helper', 'X-Keyword'),
             ('probe.py', None, 'helper', 'X-Positional')},
            self._sites('def helper():\n'
                        "    flask.Response('', headers={'X-Keyword': 1})\n"
                        "    return flask.make_response('', 503, "
                        "{'X-Positional': 1})\n"))

    def test_reading_a_header_is_not_a_site(self):
        self.assertEqual(
            set(),
            self._sites('def helper(resp):\n'
                        "    return resp.headers.get('X-Probe')\n"))

    def test_an_outgoing_request_header_is_not_a_site(self):
        self.assertEqual(
            set(),
            self._sites('def helper():\n'
                        "    requests.request('GET', 'u', "
                        "headers={'Authorization': 'x'})\n"))

    def test_a_header_that_cannot_be_read_is_reported_not_skipped(self):
        self.assertEqual(
            {('probe.py', None, 'helper', None)},
            self._sites('def helper(resp, extra):\n'
                        '    resp.headers.update(extra)\n'))

    def test_the_enclosing_class_distinguishes_two_posts(self):
        """Two handlers named post in one module are two sites."""
        self.assertEqual(
            {('probe.py', 'First', 'post', 'Retry-After'),
             ('probe.py', 'Second', 'post', 'Retry-After')},
            self._sites('class First:\n'
                        '    def post(self, resp):\n'
                        "        resp.headers['Retry-After'] = '1'\n"
                        'class Second:\n'
                        '    def post(self, resp):\n'
                        "        resp.headers['Retry-After'] = '1'\n"))


class ResponseHeadersReadingTestCase(base.ShakenFistTestCase):

    def _read(self, responses):
        source = ast.parse(
            'class Probe:\n'
            '    @swag_from(api_base.swagger_helper(\n'
            "        's', 'd', [], [%s]))\n"
            '    def post(self):\n'
            '        pass\n' % responses)
        return declarations.response_headers(source.body[0].body[0], {})

    def test_a_response_code_that_is_not_a_literal_is_unreadable(self):
        """Not an empty set under None, which would read as declared."""
        self.assertEqual(
            {200: set(), None: None},
            self._read("(200, 'Fine.', None), (CODE, 'Later.', None)"))
