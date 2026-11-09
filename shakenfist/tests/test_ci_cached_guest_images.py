# Copyright 2019 Michael Still and contributors
"""The ansible module scenarios boot guest images from the CI cache disk.

A disk base which is a well known image name -- ``debian:11`` -- is
resolved by the server into a URL on ``config.IMAGE_DOWNLOAD_URL`` and
fetched over the internet. The nested CI cluster has those bytes
already: ``shakenfist/actions`` caches them onto the ``/srv/ci`` disk on
every dependencies build. Nothing routed the cluster at that disk, so
``ansible_module_ci/004.yml`` paid a cold ~407 MiB fetch inside its
await budget -- 578 seconds measured against a 600 second default, which
is no headroom (issues 4000 and 4461).

The fix is that every disk base in those scenarios is a play variable
which ``ansiblemoduletests.sh`` overrides with an artifact it imported
from the cache disk. That only works while three things agree, and all
three are invisible at run time until a create is slow:

* no scenario hard codes a well known image name, because such a base
  is fetched from the internet whatever the cache disk holds;
* the variable each scenario defaults is the variable the script
  overrides, because an ``-e`` naming something else is silently
  ignored and the default -- the internet fetch -- is what runs; and
* the script still falls back to the well known name, so a hand run
  with no cache disk works.

Whether a base is a well known name is asked of ``images``, which is the
code the server decides it with, rather than restated here.

Following ``test_ci_stestr_logging.py``, the scan asserts its own floor:
the scenarios known to boot a guest image must be among what it found,
so a rename or a glob change cannot quietly turn this into a green run
over nothing.
"""

import glob
import os
import re

from shakenfist import images
from shakenfist.tests import base


DEPLOY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy')
SCENARIO_DIR = os.path.join(DEPLOY_DIR, 'ansible_module_ci')
RUNNER = os.path.join(DEPLOY_DIR, 'ansiblemoduletests.sh')

# A `disks:` list entry in the size@base shorthand, which is what the
# sf_instance module parses on `@` (sf_instance.py's requested_disks
# loop). Quotes are optional in YAML and both forms appear.
DISK_RE = re.compile(r'^\s*-\s*"?(\d+)@([^"]+?)"?\s*$')

# A base which is entirely one Jinja variable reference, which is the
# only form that can be overridden from the command line.
JINJA_RE = re.compile(r'^\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}$')

# The scenarios which booted a guest image when this guard was written.
KNOWN_IMAGE_SCENARIOS = {'004.yml', '005.yml', '007.yml'}

# The cache disk entry those scenarios are served from, and the shared
# artifact the runner imports it as.
CACHED_IMAGE = '/srv/ci/debian:11'
IMPORTED_BASE = 'sf://upload/system/debian-11'


def _disk_bases(path):
    """1-indexed (line number, base) for each size@base disk in a playbook."""
    found = []
    with open(path) as f:
        for number, line in enumerate(f, start=1):
            m = DISK_RE.match(line)
            if m:
                found.append((number, m.group(2)))
    return found


def _play_var(path, name):
    """The value a playbook defaults a play variable to, or None."""
    value_re = re.compile(r'^\s+%s:\s*"?([^"\n]+?)"?\s*$' % re.escape(name))
    with open(path) as f:
        for line in f:
            m = value_re.match(line)
            if m:
                return m.group(1)
    return None


class CachedGuestImageTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        with open(RUNNER) as f:
            self.runner = f.read()

    def test_no_scenario_hard_codes_a_well_known_image(self):
        scanned = set()

        for path in sorted(glob.glob(os.path.join(SCENARIO_DIR, '*.yml'))):
            name = os.path.basename(path)
            for number, image_base in _disk_bases(path):
                if JINJA_RE.match(image_base):
                    scanned.add(name)
                    continue

                self.assertFalse(
                    images.is_image_shortcut(image_base),
                    'ansible_module_ci/%s line %d boots %r, a well known '
                    'image name. The server resolves one of those to a URL '
                    'on IMAGE_DOWNLOAD_URL and fetches it over the '
                    'internet, even though the CI cache disk already holds '
                    'the bytes (issue 4461). Use a play variable that '
                    'ansiblemoduletests.sh can override with the imported '
                    'artifact.' % (name, number, image_base))

        missing = KNOWN_IMAGE_SCENARIOS - scanned
        self.assertEqual(
            set(), missing,
            'The scan found no templated disk base in %s. Either those '
            'scenarios were renamed or stopped booting a guest image -- '
            'update KNOWN_IMAGE_SCENARIOS -- or the scan has gone blind '
            'and this guard is passing over nothing.' % sorted(missing))

    def test_every_templated_base_is_overridden_by_the_runner(self):
        found = set()

        for path in sorted(glob.glob(os.path.join(SCENARIO_DIR, '*.yml'))):
            name = os.path.basename(path)
            for number, image_base in _disk_bases(path):
                m = JINJA_RE.match(image_base)
                if not m:
                    continue

                variable = m.group(1)
                found.add(variable)

                self.assertIn(
                    '-e "%s=' % variable, self.runner,
                    'ansible_module_ci/%s line %d boots {{ %s }}, but '
                    'ansiblemoduletests.sh passes no -e for that variable. '
                    'The playbook default would be used instead, which is '
                    'the internet fetch this is meant to avoid (issue '
                    '4461).' % (name, number, variable))

                default = _play_var(path, variable)
                self.assertIsNotNone(
                    default,
                    'ansible_module_ci/%s line %d boots {{ %s }}, but the '
                    'playbook never defaults that variable, so a hand run '
                    'of the scenario fails on an undefined variable.'
                    % (name, number, variable))
                self.assertTrue(
                    images.is_image_shortcut(default),
                    'ansible_module_ci/%s defaults %s to %r, which is not a '
                    'well known image name. The default is what a hand run '
                    'with no cache disk gets, so it has to be something the '
                    'server can fetch by itself.' % (name, variable, default))

        self.assertNotEqual(
            set(), found,
            'The scan found no templated disk base at all, so this guard '
            'checked nothing. Either the scenarios stopped booting guest '
            'images or DISK_RE no longer matches a disks: entry.')

    def test_the_runner_imports_the_cached_image_and_fails_open(self):
        self.assertIn(
            CACHED_IMAGE, self.runner,
            'ansiblemoduletests.sh does not name %s, so the scenarios are '
            'not served from the CI cache disk and pay an internet fetch '
            'for bytes the cluster already has (issue 4461).' % CACHED_IMAGE)
        self.assertIn(
            IMPORTED_BASE, self.runner,
            'ansiblemoduletests.sh does not boot from %s, so whatever it '
            'imports from the cache disk is not what the scenarios use.'
            % IMPORTED_BASE)
        self.assertIn(
            '--shared', self.runner,
            'the artifact imported from the cache disk is not shared. '
            'Scenario 004 boots it from a namespace of its own, and an '
            'unshared upload in the system namespace is not visible there.')
        self.assertIn(
            'if [ -r "${cached_debian_11}" ]; then', self.runner,
            'ansiblemoduletests.sh no longer guards the import on the cache '
            'disk being readable. The import is deliberately fail-open: a '
            'deploy without the cache disk has to keep working on the well '
            'known image name.')
