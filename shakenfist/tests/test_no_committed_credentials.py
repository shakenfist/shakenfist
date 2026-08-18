# Copyright 2019 Michael Still and contributors
"""No file in this repository may contain a real key secret.

Cluster-minted key secrets carry an ``sfk_`` prefix and a checksum so
that an escaped one can be found. This test is the cheapest place to
look: it walks the working tree and fails if any file contains a string
the cluster would accept as one of its own credentials.

There is a gitleaks job which does something similar over the whole of
git history, and the two are not redundant. This one understands the
checksum, so it has no false positives and needs no allowlist; it runs
inside the unit suite which is already a required check, where the
gitleaks job is a separate workflow; and it keeps working if gitleaks
is unavailable, unpackaged or red for an unrelated reason. The
credential format is this project's own invention, and the check which
guards it should not depend on a third party's packaging.

This is deliberately a check on *validity*, not on shape. The
documentation shows an example key, and the CI leak detector emits
tokens of the credential shape on purpose -- both are checksum-invalid
by construction, and neither is a credential. Failing on shape alone
would make the honest thing impossible to write down.
"""

import os
import re
import shutil
import tempfile

from shakenfist.tests import base
from shakenfist.util import credentials


# The shape a scanner can match. The checksum is what tells a real
# credential from an example of one, and this expression cannot see it
# -- so a match here is a candidate, and looks_valid() is the verdict.
SECRET_SHAPE_RE = re.compile(r'sfk_[A-Za-z0-9]{38}')

# Directories which are not the source tree. The virtualenv names are
# here for a developer with an in-tree venv: site-packages is tens of
# thousands of files, where the tree itself is around fourteen hundred,
# and none of it is ours to have committed a credential to.
#
# .stestr is here for a subtler reason. It stores the output of previous
# runs, so any test which prints a generated credential in a failure
# message leaves a real one on disk, and this test then reports it --
# correctly, but about a file nobody committed and git ignores. That is
# a false alarm about our own test output, and it happened while the
# nested-directory control below was being written.
SKIP_DIRECTORIES = {'.git', '.tox', '.eggs', '__pycache__', 'node_modules',
                    '.mypy_cache', '.pytest_cache', 'build', 'dist',
                    '.venv', 'venv', 'env', 'cover', '.stestr'}

# Suffixes we know are not text. Anything else which fails to decode is
# skipped when we meet it, so this list is an optimisation rather than
# the mechanism.
SKIP_SUFFIXES = ('.tgz', '.gz', '.qcow2', '.img', '.png', '.jpg', '.gif',
                 '.ico', '.pyc', '.so', '.woff', '.woff2', '.ttf')


def repository_root():
    """The top of the working tree, found from this file's location."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def scan_file(path):
    """Every valid credential in one file, as (line number, secret)."""
    if path.endswith(SKIP_SUFFIXES):
        return []

    try:
        with open(path, encoding='utf-8') as f:
            lines = f.readlines()
    except (UnicodeDecodeError, OSError):
        # Binary, or unreadable. Neither is a place a credential hides
        # in a way this test could speak to.
        return []

    found = []
    for number, line in enumerate(lines, start=1):
        for candidate in SECRET_SHAPE_RE.findall(line):
            if credentials.looks_valid(candidate):
                found.append((number, candidate))
    return found


def scan_tree(root):
    """Every valid credential under root, as (path, line number, secret)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRECTORIES]
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            for number, secret in scan_file(path):
                found.append((os.path.relpath(path, root), number, secret))
    return found


def redact(secret):
    """Enough to find it, not enough to use it."""
    return '%s...' % secret[:8]


class NoCommittedCredentialsTestCase(base.ShakenFistTestCase):
    def _tempfile(self, name, content):
        tmp = tempfile.mkdtemp(prefix='sf-credential-scan-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, name)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_the_scanner_finds_a_real_credential(self):
        """The positive control.

        Without this, a scanner which silently matched nothing -- a
        broken regex, a walk which skipped everything, a validity check
        inverted -- would report a clean tree forever. Phase 6 emptied
        six leak guards exactly that way, so the guard here is proven to
        fire before it is trusted to pass.
        """
        planted = credentials.generate()
        path = self._tempfile(
            'looks_like_config.py',
            '# a plausible accident\nAPI_KEY = \'%s\'\n' % planted)

        found = scan_file(path)
        self.assertEqual(
            1, len(found),
            'The credential scanner did not find a credential which was '
            'deliberately planted, so it cannot be trusted to find a '
            'real one.')
        self.assertEqual(2, found[0][0])
        self.assertEqual(planted, found[0][1])

    def test_the_scanner_ignores_an_invalid_lookalike(self):
        """The documented example, and the CI detector's controls.

        Both are the credential shape with a checksum which cannot be
        right. If this test ever fails, the scanner has started
        reporting on shape rather than validity, and every example in
        the documentation becomes an incident.
        """
        path = self._tempfile(
            'docs.md', 'sfk_e57SPWpK3JGmyhuYLrcUtSwhtdJlONiXzzzzzz\n')

        self.assertEqual([], scan_file(path))

    def test_the_walk_finds_a_credential_in_a_nested_directory(self):
        """The positive control for scan_tree(), not just scan_file().

        The test below is the one that matters, and it calls
        scan_tree(). The control above only ever proves scan_file()
        works on a single path -- so a walk which skipped every
        subdirectory, or which pruned too much, would leave that test
        passing over an almost empty file list. That is the vacuous
        shape this phase exists to prevent, one level up.

        Also asserts the pruning it relies on: a credential inside a
        SKIP_DIRECTORIES directory is deliberately not reported, which
        is what makes an in-tree virtualenv cheap to walk past.
        """
        planted = credentials.generate()
        root = tempfile.mkdtemp(prefix='sf-credential-walk-')
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        nested = os.path.join(root, 'deploy', 'roles', 'templates')
        os.makedirs(nested)
        with open(os.path.join(nested, 'vars.yaml'), 'w') as f:
            f.write('key: %s\n' % planted)

        skipped = os.path.join(root, '.venv', 'lib')
        os.makedirs(skipped)
        with open(os.path.join(skipped, 'vendored.py'), 'w') as f:
            f.write("KEY = '%s'\n" % credentials.generate())

        found = scan_tree(root)

        # Compared without the secret in the message, like every other
        # failure path here. An assertion which embedded the plaintext
        # would write a real credential into .stestr's stored results on
        # failure -- which the whole-tree test below then finds, exactly
        # as it should. That happened while writing this test.
        self.assertEqual(
            [(os.path.join('deploy', 'roles', 'templates', 'vars.yaml'), 1)],
            [(path, number) for path, number, _ in found],
            'The tree walk did not report exactly the credential planted '
            'three directories down, and nothing else. Either it is not '
            'descending -- which would make the whole-tree test below '
            'pass over almost no files -- or it is not pruning the '
            'directories it claims to prune.')
        self.assertTrue(
            found[0][2] == planted,
            'The tree walk reported a string other than the one planted '
            'at that path (planted %s).' % redact(planted))

    def test_no_credential_is_committed_to_the_tree(self):
        found = scan_tree(repository_root())
        self.assertEqual(
            [], [(path, number, redact(secret))
                 for path, number, secret in found],
            'A string the cluster would accept as one of its own key '
            'secrets is committed to this repository. It is shown '
            'redacted above. Treat it as disclosed: rotate the key '
            'before removing it, because removing it from the working '
            'tree does not remove it from git history. See '
            'docs/operator_guide/credential_rotation.md.')
