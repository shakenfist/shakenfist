# Copyright 2026 Michael Still and contributors

"""The Agent Operations state diagram must match state_targets.

docs/developer_guide/state_machine.md is the published description of
which transitions an agent operation may make, and the mermaid block in
its Agent Operations section is the part a reader trusts without
reading any code. Nothing was comparing it to
AgentOperation.state_targets, which is what the runtime actually
enforces: the two agreed by luck, and a state added to the object
without a matching edge in the diagram, or an edge drawn in the diagram
which _state_update() would refuse, is invisible to a reviewer who is
not reading both files side by side. check-doc-anchors checks that the
page's links resolve, not what it draws.

That drift is worth a test rather than a review item because both
directions of it are expensive. An edge in the code and not the diagram
is an undocumented transition, which for a terminal state means a
client is never told the state exists; an edge in the diagram and not
the code is a transition the documentation promised and
InvalidStateException refuses.

Only the Agent Operations section is compared. The other sections on
that page describe objects whose state machines this test says nothing
about, and widening it to all of them is a larger job than the one this
test exists to do: it wants a map from each section heading to an
object class, and several of those sections document states which are
set by more than one class.
"""

import os
import re

from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.tests import base


#: The section of the page whose diagram belongs to AgentOperation.
SECTION = 'Agent Operations'

#: `  initial --> queued`, or `  [*] --> initial`. A mermaid state name
#: cannot contain whitespace, which is what makes this safe to anchor.
_EDGE = re.compile(r'^(\S+)\s+-->\s+(\S+)$')

_HEADING = re.compile(r'^#+\s')

#: mermaid's own name for the start and end pseudo-states. state_targets
#: spells the same two things as the None key and a None value.
_PSEUDO_STATE = '[*]'


def _doc_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, 'docs', 'developer_guide', 'state_machine.md')


def parse_diagram(text, section):
    """The (from, to) edges of one section's stateDiagram-v2 block.

    The start and end pseudo-states are returned as None, so the result
    can be compared directly with a state_targets map, where the same
    two are the None key and a None value.

    Raises ValueError rather than returning an empty set if the section
    or its diagram cannot be found, because a parser which silently
    matches nothing would make every comparison below pass.
    """
    in_section = False
    found_section = False
    in_diagram = False
    found_diagram = False
    edges = set()

    for line in text.splitlines():
        stripped = line.strip()

        if _HEADING.match(stripped):
            in_section = stripped.lstrip('# ').strip() == section
            found_section = found_section or in_section
            continue

        if not in_section:
            continue

        if stripped.startswith('```'):
            if in_diagram:
                in_diagram = False
            elif stripped == '```mermaid':
                in_diagram = True
            continue

        if not in_diagram:
            continue

        if stripped == 'stateDiagram-v2':
            found_diagram = True
            continue

        matched = _EDGE.match(stripped)
        if matched:
            if not found_diagram:
                raise ValueError(
                    'an edge appeared before the stateDiagram-v2 line in the '
                    '%s section: %s' % (section, stripped))
            edges.add(tuple(
                None if name == _PSEUDO_STATE else name
                for name in matched.groups()))

    if not found_section:
        raise ValueError('no "%s" section in %s' % (section, _doc_path()))
    if not found_diagram:
        raise ValueError(
            'the "%s" section of %s has no stateDiagram-v2 block'
            % (section, _doc_path()))
    return edges


def targets_to_edges(state_targets):
    """The same (from, to) set, built from a state_targets map."""
    edges = set()
    for state, targets in state_targets.items():
        if targets is None:
            # A state with nowhere to go, which the diagram draws as an
            # edge to the end pseudo-state.
            edges.add((state, None))
            continue
        for target in targets:
            edges.add((state, target))
    return edges


def render(edges):
    """Edges as the diagram spells them, for a failure message."""
    return ', '.join(sorted(
        '%s --> %s' % (frm or _PSEUDO_STATE, to or _PSEUDO_STATE)
        for (frm, to) in edges))


class AgentOperationStateDocTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        with open(_doc_path()) as f:
            self.documented = parse_diagram(f.read(), SECTION)
        self.declared = targets_to_edges(AgentOperation.state_targets)

    def test_the_diagram_was_parsed(self):
        """The parse is the part of this test most likely to rot.

        A heading rename, a fence which stops saying mermaid, or a
        mermaid dialect which draws an edge differently would make the
        comparison below vacuous rather than failing, which is the worst
        outcome a consistency check can have. parse_diagram() raises on
        a missing section or diagram; these are the assertions for a
        block which was found but whose edges were not.
        """
        self.assertNotEqual(set(), self.documented)
        self.assertIn(
            (None, 'initial'), self.documented,
            'the start pseudo-state edge was not recognised, so the diagram '
            'parse is not comparable with state_targets')
        self.assertIn(
            ('deleted', None), self.documented,
            'the end pseudo-state edge was not recognised, so the diagram '
            'parse is not comparable with state_targets')

    def test_the_diagram_is_the_state_machine(self):
        self.assertEqual(
            self.declared, self.documented,
            'the Agent Operations state diagram in %s and '
            'AgentOperation.state_targets describe different transitions. '
            'Only in state_targets: %s. Only in the diagram: %s.'
            % (_doc_path(),
               render(self.declared - self.documented) or 'none',
               render(self.documented - self.declared) or 'none'))
