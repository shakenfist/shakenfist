# Copyright 2026 Michael Still and contributors

"""An example inventory's api_url is one of the two shapes we document.

docs/operator_guide/load_balancing.md defines exactly two shapes for the
``api_url`` an operator gives the deployer:

* the load-balanced shape, ``<lb>/api``, where the ``/api`` prefix is what the
  operator's proxy strips before forwarding to an ``sf-api`` worker on port
  13000, with no version segment after it; and
* the single-node escape hatch, a loopback ``:13000`` URL with **no** ``/api``
  prefix, for a deploy that runs everything on one box and operates no proxy.

The examples are templates operators copy, so a value which matches neither
shape is a defect however well the deploy goes: issue 4460 was
``examples/cluster`` pairing a load-balancer comment with the escape hatch's
prefix-less ``:13000`` URL, which reaches one node's ``sf-api`` rather than the
load balancer the comment promised. Nothing in the deploy validates ``api_url``
-- it is written into ``sfrc`` and the global auth file, and read back by
clients long afterwards -- so this file is the check.

The shapes are tested against each example's inventory rather than against its
name, so the rule holds for an example added later too.
"""

import os
import urllib.parse

import yaml

from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


EXAMPLES_PATH = os.path.join(_repo_root(), 'examples')

# The port every sf-api worker listens on, which is both the backend port a
# proxy forwards to and the port the escape hatch names directly.
API_PORT = 13000

# The prefix a load-balanced api_url ends in, which the proxy strips.
API_PREFIX = '/api'


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def examples():
    """Yield (name, api_url, allsf hosts) for every example deploy.

    An example deploy is a directory under examples/ with both an
    inventory.yaml and a group_vars/all.yml; examples/_shared holds the
    playbook they import and has neither.
    """
    for name in sorted(os.listdir(EXAMPLES_PATH)):
        inventory_path = os.path.join(EXAMPLES_PATH, name, 'inventory.yaml')
        group_vars_path = os.path.join(EXAMPLES_PATH, name, 'group_vars', 'all.yml')
        if not os.path.exists(inventory_path) or not os.path.exists(group_vars_path):
            continue

        inventory = load_yaml(inventory_path)
        allsf = (inventory.get('all', {}).get('children', {}).get('allsf', {}).get('hosts') or {})
        yield name, load_yaml(group_vars_path).get('api_url'), sorted(allsf.keys())


def is_load_balanced(api_url):
    """Whether api_url is the <lb>/api shape load_balancing.md documents."""
    return urllib.parse.urlsplit(api_url).path == API_PREFIX


class ExampleInventoryTestCase(base.ShakenFistTestCase):
    def test_the_examples_are_found(self):
        # Without this, every assertion below passes by iterating nothing --
        # a renamed or moved examples directory would read as compliance.
        found = list(examples())
        self.assertNotEqual(
            [], found,
            f'no example deploys found under {EXAMPLES_PATH}, so the checks in this file no longer check '
            'anything')
        for name, api_url, allsf in found:
            self.assertTrue(api_url, f'the {name} example sets no api_url in group_vars/all.yml')
            self.assertNotEqual([], allsf, f'the {name} example has no hosts in its allsf inventory group')

    def test_api_url_is_an_http_url_with_no_trailing_slash(self):
        for name, api_url, _ in examples():
            parsed = urllib.parse.urlsplit(api_url)
            self.assertIn(
                parsed.scheme, ('http', 'https'),
                f'the {name} example\'s api_url ({api_url}) is not an http or https URL')
            self.assertTrue(
                parsed.hostname,
                f'the {name} example\'s api_url ({api_url}) names no host')
            self.assertFalse(
                parsed.path.endswith('/'),
                f'the {name} example\'s api_url ({api_url}) ends in a slash, so a client joining a path onto '
                'it asks for a doubled separator')
            self.assertEqual(
                '', parsed.query + parsed.fragment,
                f'the {name} example\'s api_url ({api_url}) carries a query or fragment, which is not a base '
                'URL')

    def test_api_url_matches_a_documented_shape(self):
        for name, api_url, _ in examples():
            parsed = urllib.parse.urlsplit(api_url)
            self.assertIn(
                parsed.path, ('', API_PREFIX),
                f'the {name} example\'s api_url ({api_url}) has a path of "{parsed.path}". '
                f'docs/operator_guide/load_balancing.md documents two shapes: "<lb>{API_PREFIX}" behind a '
                f'load balancer, with no version segment after the prefix, or a prefix-less ":{API_PORT}" URL '
                'for the single-node escape hatch')

    def test_a_multi_node_example_is_load_balanced(self):
        # The escape hatch reaches exactly one node's sf-api, so in a cluster
        # it is not an endpoint: every client fails when that node does.
        for name, api_url, allsf in examples():
            if len(allsf) < 2:
                continue
            self.assertTrue(
                is_load_balanced(api_url),
                f'the {name} example has {len(allsf)} Shaken Fist nodes but its api_url ({api_url}) does not '
                f'end in "{API_PREFIX}", so it names one node\'s sf-api rather than a load balancer in front '
                'of all of them (issue 4460)')

    def test_the_escape_hatch_is_single_node_and_loopback(self):
        # The other direction: a prefix-less api_url is only correct for the
        # one deploy shape load_balancing.md offers it for.
        for name, api_url, allsf in examples():
            if is_load_balanced(api_url):
                continue
            parsed = urllib.parse.urlsplit(api_url)
            self.assertEqual(
                1, len(allsf),
                f'the {name} example\'s api_url ({api_url}) is the single-node escape hatch, but the example '
                f'deploys {len(allsf)} nodes')
            self.assertEqual(
                API_PORT, parsed.port,
                f'the {name} example\'s api_url ({api_url}) has no "{API_PREFIX}" prefix, so it must name an '
                f'sf-api worker on port {API_PORT} directly')
            self.assertIn(
                parsed.hostname, ('localhost', '127.0.0.1', '::1'),
                f'the {name} example\'s api_url ({api_url}) is the escape hatch for a deploy with no proxy, '
                'so it addresses the box it is deployed on')
