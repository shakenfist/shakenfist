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
does: it finds every place the API layer sets a response header, and
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
# responses publish it. Keyed by (module, enclosing function, header)
# and valued by the (class, method, httpcode) declarations which must
# publish that header.
#
# A helper rather than a handler can be the setter -- capacity_error()
# is one, and _netdesc_allocate_address() is shared between two
# endpoints -- which is why this is a table and not a derivation: no
# static read of this tree says which endpoints reach a given helper.
# The audit's value is that the table cannot quietly fall behind the
# code, because a site missing from it fails.
HEADER_SITES: dict[tuple[str, str, str], set[tuple[str, str, int]]] = {
    # The transient capacity refusal. Its two call sites are both in
    # InstancesEndpoint.post.
    ('base.py', 'capacity_error', 'Retry-After'): {
        ('InstancesEndpoint', 'post', 507),
    },
    # A database failure while recording a network interface, shared
    # between instance create and interface hotplug.
    ('instance.py', '_netdesc_allocate_address', 'Retry-After'): {
        ('InstancesEndpoint', 'post', 503),
        ('InstanceInterfacesEndpoint', 'post', 503),
    },
    # A database failure while recording the placement.
    ('instance.py', 'post', 'Retry-After'): {
        ('InstancesEndpoint', 'post', 503),
    },
}


def _header_assignments():
    """(module, function, header) for every response header set in the API.

    Found by AST rather than by grep so that a header set on a response
    held under any local name still counts: the ``.headers[...]``
    subscript is what matters, not what the response object is called.
    """
    found = set()
    for path in sorted(glob.glob(
            os.path.join(declarations.API_DIR, '*.py'))):
        tree = ast.parse(open(path).read())
        module = os.path.basename(path)
        functions = [node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef,
                                          ast.AsyncFunctionDef))]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Subscript):
                    continue
                if not (isinstance(target.value, ast.Attribute)
                        and target.value.attr == 'headers'):
                    continue
                name = declarations.literal(target.slice)
                # The innermost enclosing function, which for a handler
                # is the handler and for a helper is the helper.
                enclosing = sorted(
                    (fn for fn in functions
                     if fn.lineno <= target.lineno <= (fn.end_lineno or 0)),
                    key=lambda fn: fn.lineno)
                found.add((module,
                           enclosing[-1].name if enclosing else None,
                           name if isinstance(name, str) else None))
    return found


class ResponseHeaderDeclarationTestCase(base.ShakenFistTestCase):

    def test_every_header_the_api_sets_is_in_the_table(self):
        found = _header_assignments()

        # Nothing unreadable: a site whose header name or enclosing
        # function could not be resolved would otherwise compare
        # unequal to every table key and read as a new site, which is
        # the right failure but the wrong message.
        unreadable = [site for site in found if None in site]
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
        for (module, function, header), responses in HEADER_SITES.items():
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
