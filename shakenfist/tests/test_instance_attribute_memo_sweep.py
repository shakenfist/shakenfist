# Copyright 2026 Michael Still and contributors

"""Every method which reads two Instance attributes memoizes them, or says why.

Issue #3654 added Instance.attribute_memo() and entered it for
external_view(), where nine reads of one instance_attributes row cost nine
GetInstanceAttributes RPCs. Issue #3695 asked for the technique to be
applied more broadly. This is the sweep that asks the question
mechanically, so a new multi-read method cannot appear unnoticed.

The property set is derived from Instance.MARIADB_ATTRIBUTES rather than
listed here, because a new MariaDB-backed attribute is exactly the kind of
thing that would otherwise escape the audit.

The sweep is lexical, which bounds what it can claim. It does not follow
calls, so a method whose reads happen inside a helper, or whose memo its
caller entered, has to say so in EXEMPT; and it counts reads rather than
distinct attributes, because two reads of one attribute are two row
fetches just the same.

What the sweep does not and cannot judge is *whether* a method should
memoize. Two of the methods it finds re-read an attribute on purpose, and
memoizing them would reintroduce the race the re-read closes, so the
exemptions below carry that reason and the audit holds the list exactly:
an unexplained new entry fails, and so does an exemption which no longer
reads two attributes.
"""

import ast
import pathlib

from shakenfist.instance import Instance
from shakenfist.tests import base


# Methods which read two or more attributes and are deliberately not
# memoized, with the reason. Keyed by '<path>:<function>'.
EXEMPT = {
    'shakenfist/daemons/cleaner/scheduled_tasks.py:_update_inactive_domain':
        'The second power_state read is inside the instance lock, and exists '
        'to notice a power off which finished and recorded itself while we '
        'waited for that lock. A memo would hide it.',

    'shakenfist/external_api/instance.py:post':
        'agent_operation_enqueue() writes an attribute between the two '
        'reads, which invalidates the memo by design, so there is nothing to '
        'save.',

    'shakenfist/instance.py:_build_external_view':
        'Its only caller, external_view(), is the memo block -- which is '
        'the whole point of issue #3654. The sweep is lexical and cannot '
        'see a memo its caller entered.',

    'shakenfist/daemons/queues/startup_tasks.py:restore_instances':
        'The power state is read before the network restore and the '
        'placement after it, deliberately and minutes apart, so one row '
        'fetch must not serve both.',
}

# Methods whose correctness depends on re-reading an attribute, which a
# memo would defeat. The sweep cannot find all of these -- it does not
# follow calls, and record_observed_agent_state() re-reads through a
# helper -- so they are named here and the audit asserts that none of them
# has grown a memo block. This is the half of issue #3695 that is a
# hazard rather than a saving.
MUST_NOT_MEMOIZE = {
    'shakenfist/instance.py:record_observed_agent_state':
        'The power state is read again after the write, through '
        '_agent_state_when_not_running(), and that is what makes this safe '
        'without a lock (issue #4485).',

    'shakenfist/daemons/cleaner/scheduled_tasks.py:_update_inactive_domain':
        'The second power_state read is inside the instance lock and exists '
        'to notice a power off which finished and recorded itself while we '
        'waited for that lock.',
}

# Marshalling of InstanceAttributesData models and protobufs, where the
# attribute names are model fields rather than Instance property reads.
SKIP_FILES = {
    'shakenfist/mariadb.py',
    'shakenfist/daemons/database/main.py',
}


# 'error' is a MariaDB-backed attribute with a property, and the audit
# cannot see it: `.error` is also how every API handler refuses a request
# (sf_api.error), how every logger records one, and the name of a field on
# several protobuf replies. A lexical sweep cannot tell those from an
# instance's error attribute, and a sweep which reported them would be
# ignored. Reads of it are not covered here.
AMBIGUOUS_PROPERTIES = {'error'}


def _property_names():
    """The Instance properties whose read is a row fetch.

    MARIADB_ATTRIBUTES holds attribute names; the ones with no property of
    the same name (vsock_cids, agent_attributes) are read by other means
    and cannot appear as a plain attribute access.
    """
    return {name for name in Instance.MARIADB_ATTRIBUTES
            if isinstance(getattr(Instance, name, None), property)
            } - AMBIGUOUS_PROPERTIES


class _MemoVisitor(ast.NodeVisitor):
    """Collect functions reading two or more of the named properties.

    A read inside an ``attribute_memo()`` with-block does not count, which
    is what makes a memoized method pass without being listed anywhere.
    """

    def __init__(self, properties):
        self.properties = properties
        self.findings = {}
        self._memo_depth = 0
        self._stack = []

    def visit_FunctionDef(self, node):
        self._stack.append(node.name)
        saved_depth = self._memo_depth
        self._memo_depth = 0
        self.generic_visit(node)
        self._memo_depth = saved_depth
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_With(self, node):
        entered = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == 'attribute_memo'
            for item in node.items)
        if entered:
            self._memo_depth += 1
        self.generic_visit(node)
        if entered:
            self._memo_depth -= 1

    def visit_Call(self, node):
        # A property read is never called, so `thing.ports(...)` is some
        # other thing's method and not a row fetch. The attribute being
        # called is skipped; everything around it is still visited.
        if isinstance(node.func, ast.Attribute):
            self.visit(node.func.value)
        else:
            self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Attribute(self, node):
        if (node.attr in self.properties
                and isinstance(node.ctx, ast.Load)
                and not self._memo_depth
                and self._stack):
            key = (self._stack[-1], ast.unparse(node.value))
            self.findings.setdefault(key, []).append((node.attr, node.lineno))
        self.generic_visit(node)


class InstanceAttributeMemoSweepTestCase(base.ShakenFistTestCase):
    def _unmemoized_multi_readers(self):
        properties = _property_names()
        root = pathlib.Path(__file__).resolve().parent.parent
        found = {}
        for path in sorted(root.rglob('*.py')):
            relative = path.relative_to(root.parent).as_posix()
            if relative in SKIP_FILES or '/tests/' in relative:
                continue
            visitor = _MemoVisitor(properties)
            visitor.visit(ast.parse(path.read_text()))
            for (function, _base), reads in visitor.findings.items():
                # Reads, not distinct attributes: two reads of one
                # attribute are two row fetches just the same.
                if len(reads) >= 2:
                    found.setdefault(f'{relative}:{function}', []).extend(
                        reads)
        return found

    def test_the_property_set_is_not_empty(self):
        # A sweep which finds nothing because it looks for nothing passes
        # silently, so pin the derivation itself.
        properties = _property_names()
        self.assertIn('placement', properties)
        self.assertIn('power_state', properties)
        self.assertIn('block_devices', properties)

    def test_every_multi_attribute_reader_memoizes_or_is_exempt(self):
        unexplained = sorted(
            set(self._unmemoized_multi_readers()) - set(EXEMPT))
        self.assertEqual(
            [], unexplained,
            'These methods read two or more MariaDB-backed Instance '
            'attributes outside an attribute_memo() block, so each read is '
            'its own GetInstanceAttributes RPC for one row. Wrap the reads '
            'in `with <instance>.attribute_memo():`, or add the method to '
            'EXEMPT with the reason it must re-read (issue #3695).')

    def test_no_method_which_must_re_read_has_grown_a_memo(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        memoized = set()
        for key in MUST_NOT_MEMOIZE:
            relative, function = key.rsplit(':', 1)
            path = root.parent / relative
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == function):
                    for inner in ast.walk(node):
                        if (isinstance(inner, ast.With) and any(
                                isinstance(item.context_expr, ast.Call)
                                and isinstance(item.context_expr.func,
                                               ast.Attribute)
                                and item.context_expr.func.attr ==
                                'attribute_memo'
                                for item in inner.items)):
                            memoized.add(key)
        self.assertEqual(
            set(), memoized,
            'These methods depend on re-reading an attribute, and a memo '
            'serves the first read to every later one. See MUST_NOT_MEMOIZE '
            'for why each of them re-reads.')

    def test_every_method_which_must_not_memoize_still_exists(self):
        # A rule about a method which has been renamed or deleted is not a
        # rule, it is a comment nobody will notice is wrong.
        root = pathlib.Path(__file__).resolve().parent.parent
        missing = []
        for key in sorted(MUST_NOT_MEMOIZE):
            relative, function = key.rsplit(':', 1)
            tree = ast.parse((root.parent / relative).read_text())
            if not any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == function for node in ast.walk(tree)):
                missing.append(key)
        self.assertEqual([], missing)

    def test_no_exemption_outlives_its_reason(self):
        # An exemption for a method which no longer reads two attributes
        # is a stale claim about code nobody has read lately.
        stale = sorted(set(EXEMPT) - set(self._unmemoized_multi_readers()))
        self.assertEqual(
            [], stale,
            'These methods are exempt from the attribute memo sweep but no '
            'longer read two or more attributes outside a memo. Remove them '
            'from EXEMPT.')
