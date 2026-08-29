# Copyright 2026 Michael Still and contributors

"""Regression guard for the layout of .claude/skills/.

A skill is `<skills dir>/<name>/SKILL.md` with `name` and `description`
frontmatter. A bare markdown file directly in `.claude/skills/`, or a
subdirectory with no `SKILL.md`, is inert: the agent never discovers it,
so it also never gets linted, and the repository appears to have working
skills while they do nothing. Both of this repository's skills sat in
exactly that state from the day they were written until the LLM context
linting consistency audit noticed (issue 3831). This test keeps them
loadable.
"""

import os

import yaml

from shakenfist.tests import base


REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
SKILLS_DIR = os.path.join(REPO_ROOT, '.claude', 'skills')

# Documentation is allowed to sit beside skill directories, matching the
# audit's own allowance.
ALLOWED_FILES = {'README.md', 'index.md'}


def _frontmatter(path):
    with open(path) as f:
        content = f.read()
    if not content.startswith('---\n'):
        return None
    _, frontmatter, _ = content.split('---\n', 2)
    return yaml.safe_load(frontmatter)


class ClaudeSkillLayoutTestCase(base.ShakenFistTestCase):
    def test_skills_directory_exists(self):
        # README.md documents these skills as present, so a deleted or
        # renamed skills directory is a defect rather than "not applicable".
        self.assertTrue(os.path.isdir(SKILLS_DIR))
        self.assertNotEqual([], sorted(os.listdir(SKILLS_DIR)))

    def test_no_inert_markdown(self):
        # A flat markdown file in .claude/skills/ never loads as a skill.
        for entry in sorted(os.listdir(SKILLS_DIR)):
            path = os.path.join(SKILLS_DIR, entry)
            if os.path.isfile(path):
                self.assertIn(
                    entry, ALLOWED_FILES,
                    f'{entry} is a flat file in .claude/skills/ and will '
                    'never load as a skill; move it to '
                    f'{os.path.splitext(entry)[0]}/SKILL.md')

    def test_every_skill_directory_loads(self):
        for entry in sorted(os.listdir(SKILLS_DIR)):
            path = os.path.join(SKILLS_DIR, entry)
            if not os.path.isdir(path):
                continue

            skill_md = os.path.join(path, 'SKILL.md')
            self.assertTrue(
                os.path.isfile(skill_md),
                f'.claude/skills/{entry}/ has no SKILL.md, so it is inert')

            frontmatter = _frontmatter(skill_md)
            self.assertIsNotNone(
                frontmatter,
                f'.claude/skills/{entry}/SKILL.md has no YAML frontmatter')
            self.assertEqual(
                entry, frontmatter.get('name'),
                f'.claude/skills/{entry}/SKILL.md frontmatter name must '
                'match its directory name')
            self.assertTrue(
                frontmatter.get('description'),
                f'.claude/skills/{entry}/SKILL.md frontmatter has no '
                'description, so the agent cannot decide when to load it')
