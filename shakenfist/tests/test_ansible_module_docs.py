# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Hold the ansible module documentation to the modules' own declarations.

Each collection module declares its parameters twice: once in its
`DOCUMENTATION` block, which is the module's own reference and what
`ansible-doc` renders, and once as a table in
`docs/user_guide/ansible.md`, which is what a user reads. Issue #3270
is what that costs when nothing compares them -- it reported, correctly
at the time, that the user guide did not list all of `sf_instance`'s
parameters. It was filed in 2026-06 and discharged in pieces, mostly by
`9f701125b7` and finally by `786f30c5d9`, each time by hand.

Nothing has held them together since, so this does. It is not a
narrative-parity test: the guide's wording is its own and usually
longer than the module's, which is the point of having both. What is
asserted is the set of parameter *names*, in both directions, because
that is the part with no legitimate second answer -- a parameter a
module accepts and the guide does not mention is undiscoverable, and
one the guide documents and the module does not accept is a lie a user
will act on.

`argument_spec` is read as well, because the module's two halves can
also disagree with each other: `DOCUMENTATION` is prose to Ansible and
nothing validates it against the spec that decides what the module
really takes.
"""

import ast
import os
import re
from unittest import mock

import yaml

from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


MODULE_DIR = os.path.join(
    _repo_root(), 'shakenfist', 'deploy', 'collection', 'plugins', 'modules')
MODULE_UTILS_DIR = os.path.join(
    _repo_root(), 'shakenfist', 'deploy', 'collection', 'plugins',
    'module_utils')
GUIDE = os.path.join(_repo_root(), 'docs', 'user_guide', 'ansible.md')

#: Which `##` section of the user guide documents each module. A module
#: with no section is a module with no user documentation, which is the
#: defect in its largest form, so the mapping is asserted rather than
#: discovered.
GUIDE_SECTIONS = {
    'sf_namespace': 'Namespaces',
    'sf_network': 'Networks',
    'sf_instance': 'Instances',
    'sf_snapshot': 'Snapshots',
    'sf_claim': 'Capacity claims',
}

#: The connection parameters every module takes, merged in from
#: module_utils/sf_connection.py's connection_argument_spec() rather
#: than written into each module's own argument_spec. Read from there
#: rather than listed here, so a fourth one would be picked up.
CONNECTION_IDENTITY_PARAMETERS = frozenset(['namespace', 'auth_namespace'])


def _documentation_options(module):
    """The parameter names a module's DOCUMENTATION block declares."""
    source = open(os.path.join(MODULE_DIR, '%s.py' % module)).read()
    match = re.search(r"DOCUMENTATION\s*=\s*r?'''(.*?)'''", source, re.S)
    if match is None:
        match = re.search(r'DOCUMENTATION\s*=\s*r?"""(.*?)"""', source, re.S)
    assert match is not None, '%s has no DOCUMENTATION block' % module
    return set(yaml.safe_load(match.group(1)).get('options', {}))


def _connection_parameters():
    """The parameter names connection_argument_spec() contributes."""
    source = open(os.path.join(MODULE_UTILS_DIR, 'sf_connection.py')).read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [key.value for key in node.keys
                if isinstance(key, ast.Constant)]
        if 'api_url' in keys and 'key' in keys:
            return set(keys)
    raise AssertionError(
        'connection_argument_spec() no longer builds a dict literal '
        'carrying api_url and key, so this test cannot say which '
        'parameters it contributes')


def _argument_spec(module):
    """The parameter names a module's own argument_spec declares.

    Plus the connection parameters, which the module merges in from
    connection_argument_spec() and so does not spell itself. The
    identity parameter is whichever of `namespace` and
    `auth_namespace` the module's DOCUMENTATION names -- sf_claim's
    `namespace` is the namespace a claim covers rather than the one it
    authenticates as, so it documents `auth_namespace` instead.
    """
    source = open(os.path.join(MODULE_DIR, '%s.py' % module)).read()
    tree = ast.parse(source)
    spec = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name)
                        and target.id == 'argument_spec'
                        and isinstance(node.value, ast.Dict)):
                    spec = {key.value for key in node.value.keys
                            if isinstance(key, ast.Constant)}
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if (keyword.arg == 'argument_spec'
                        and isinstance(keyword.value, ast.Dict)):
                    spec = {key.value for key in keyword.value.keys
                            if isinstance(key, ast.Constant)}
    assert spec is not None, (
        '%s does not build its argument_spec as a dict literal, so this '
        'test cannot read what it accepts' % module)

    connection = _connection_parameters()
    documented_identity = _documentation_options(module) & (
        CONNECTION_IDENTITY_PARAMETERS)
    return spec | (connection - CONNECTION_IDENTITY_PARAMETERS) | (
        documented_identity)


def _guide_sections():
    """The lines of each `##` section of the user guide, by heading."""
    sections = {}
    current = None
    for line in open(GUIDE).read().splitlines():
        if line.startswith('## '):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def _guide_parameters(module, sections):
    """The parameter names the guide tabulates for a module.

    A parameter row is `| name<br/>*type* | description |`, which is
    the shape every parameter table in that document uses. Return
    rows are tabulated identically but live under their own `###`
    heading, so the section is cut at the first one which is not
    `### Parameters`.
    """
    section = GUIDE_SECTIONS[module]
    lines = []
    in_parameters = False
    for line in sections[section]:
        if line.startswith('### '):
            in_parameters = line[4:].strip() == 'Parameters'
            continue
        if in_parameters:
            lines.append(line)
    return set(re.findall(r'^\| ([a-z_][a-z0-9_]*)<br/>', '\n'.join(lines),
                          re.M))


class AnsibleModuleDocumentationTestCase(base.ShakenFistTestCase):

    def test_every_module_has_a_guide_section(self):
        modules = {name[:-3] for name in os.listdir(MODULE_DIR)
                   if name.startswith('sf_') and name.endswith('.py')}
        self.assertEqual(
            set(GUIDE_SECTIONS), modules,
            'the collection ships modules this test does not know about, '
            'or names ones it no longer ships')

        sections = _guide_sections()
        for module, heading in GUIDE_SECTIONS.items():
            self.assertIn(
                heading, sections,
                '%s is documented under "%s", which the user guide no '
                'longer has' % (module, heading))

    def test_the_guide_documents_every_declared_parameter(self):
        sections = _guide_sections()
        missing = []
        for module in sorted(GUIDE_SECTIONS):
            declared = _documentation_options(module)
            # A floor, so a module whose DOCUMENTATION stopped parsing
            # cannot make this pass by declaring nothing.
            self.assertGreater(
                len(declared), 4,
                '%s declares only %d options, which is not a module this '
                'test is reading correctly' % (module, len(declared)))
            documented = _guide_parameters(module, sections)
            for name in sorted(declared - documented):
                missing.append('%s.%s is declared and not in the guide'
                               % (module, name))
        self.assertEqual([], missing, '\n'.join(missing))

    def test_the_guide_documents_nothing_a_module_does_not_take(self):
        sections = _guide_sections()
        invented = []
        for module in sorted(GUIDE_SECTIONS):
            declared = _documentation_options(module)
            for name in sorted(_guide_parameters(module, sections) - declared):
                invented.append(
                    '%s.%s is in the guide and not declared' % (module, name))
        self.assertEqual([], invented, '\n'.join(invented))

    def test_each_module_documents_what_it_accepts(self):
        """The module's own two halves, which can also disagree.

        DOCUMENTATION is prose as far as Ansible is concerned; the
        argument_spec is what decides whether a parameter is accepted.
        """
        mismatched = []
        for module in sorted(GUIDE_SECTIONS):
            declared = _documentation_options(module)
            accepted = _argument_spec(module)
            for name in sorted(accepted - declared):
                mismatched.append(
                    '%s accepts %s and documents nothing about it'
                    % (module, name))
            for name in sorted(declared - accepted):
                mismatched.append(
                    '%s documents %s and does not accept it'
                    % (module, name))
        self.assertEqual([], mismatched, '\n'.join(mismatched))

    def test_only_the_parameter_table_is_read(self):
        """Why the section is cut at the first other `###` heading.

        A module's section also tabulates its return value, in the same
        row shape. Today that excludes nothing -- every return field
        either has a dotted name or lives in a table this reader does
        not match, which a check below states -- so the restriction is
        defensive, and this is what makes it real rather than
        decorative: without it a return field sharing a parameter's
        name would read as a parameter, and one that did not would be
        reported as documented-but-not-accepted.
        """
        sections = {
            'Probe': [
                '### Parameters',
                '',
                '| **Parameter** | **Comments** |',
                '|---|---|',
                '| real_one<br/>*string* | A parameter. |',
                '',
                '### Return value',
                '',
                '| **Field** | **Comments** |',
                '|---|---|',
                '| returned_one<br/>*string* | Not a parameter. |',
            ]
        }
        with mock.patch.dict(GUIDE_SECTIONS, {'probe': 'Probe'}):
            self.assertEqual(
                {'real_one'}, _guide_parameters('probe', sections))

    def test_the_real_sections_have_a_return_table_this_skips(self):
        # The restriction above is defensive today rather than
        # load-bearing, and this says so out loud: if a return field
        # ever shares a parameter's row shape and name, the assertion
        # here is what will have changed.
        sections = _guide_sections()
        under_parameters = _guide_parameters('sf_instance', sections)
        whole_section = set(re.findall(
            r'^\| ([a-z_][a-z0-9_]*)<br/>',
            '\n'.join(sections['Instances']), re.M))
        self.assertEqual(under_parameters, whole_section)
        # And that there is a return table there to have skipped, so
        # this is not passing because the document changed shape.
        self.assertIn('### Return value', sections['Instances'])

    def test_the_comparison_notices_a_parameter_it_should(self):
        """The positive control.

        Every assertion above passes if the guide reader stops finding
        rows, which is the way a test like this dies quietly. This
        proves the reader finds the rows it claims to, and that
        removing one would be noticed.
        """
        sections = _guide_sections()
        documented = _guide_parameters('sf_instance', sections)
        self.assertIn('await_timeout', documented)

        without = {
            'Instances': [line for line in sections['Instances']
                          if not line.startswith('| await_timeout<br/>')]
        }
        self.assertNotIn(
            'await_timeout', _guide_parameters('sf_instance', without))
        self.assertIn(
            'await_timeout',
            _documentation_options('sf_instance') - _guide_parameters(
                'sf_instance', without))
