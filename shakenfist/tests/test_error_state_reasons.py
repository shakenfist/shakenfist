# Copyright 2026 Michael Still and contributors
"""An object in an error state must say why it got there.

CI observed an instance in state error with error_message null and no
way to tell what had put it there (issue 3665). The cause is that the
``state`` property setter writes ``message=None``, so entering an error
state through it records no reason at all -- and where a caller did set
one, it did so in a second write, which leaves a window with no reason
and loses the reason entirely if the process dies in between.

``DatabaseBackedObject.enter_error_state()`` takes the reason and
writes it with the state. This module asserts that production code uses
it, by reading the working tree: a new bare ``obj.state = <error>``
assignment fails the audit rather than silently recreating the dead
end.
"""

import ast
import os

import testtools

from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.baseobject import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from unittest import mock


# The audit reads the sources next to baseobject.py, for the reasons
# set out in tests/external_api/test_parameter_declarations.py: tox
# installs a wheel, and discovery is what puts this checkout first.
PACKAGE_ROOT = os.path.dirname(os.path.abspath(baseobject.__file__))

# Directories the audit does not read. Tests construct error states
# deliberately, and the deploy tree is the CI harness rather than the
# daemons.
SKIPPED_DIRECTORIES = ['tests', 'deploy']

# Assignments which may set an error state without a reason, each with
# the reason it is allowed to.
#
# An entry is '<path relative to the package>:<function>'. It is not an
# escape hatch for a new failure path: a failure with nothing to say
# about itself is the defect this audit exists to stop.
BARE_ERROR_STATE_ALLOWED = {
    # The error-to-error normalisation on delete. Both sides are error
    # states, so _state_update() carries the message set when the error
    # was raised across the transition rather than clearing it, which is
    # the whole point of issue 4776. There is no new reason to record
    # here -- inventing one would overwrite the real one.
    'instance.py:_delete_globally': (
        'error-to-error transition, message carried by _state_update'),
}


def _is_error_state_expression(node):
    """Does this expression look like it evaluates to an error state?

    Deliberately generous: a constant ending in 'error', a NAME or
    attribute mentioning ERROR, an f-string or a concatenation with an
    '-error' piece in it. A false positive here costs an allow-list
    entry with a reason, which is the outcome this audit wants anyway.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if child.value.endswith('error'):
                return True
        if isinstance(child, ast.Name) and 'ERROR' in child.id:
            return True
        if isinstance(child, ast.Attribute) and 'ERROR' in child.attr:
            return True
    return False


def _enclosing_function(tree, target):
    """The name of the function a node sits in, or '<module>'."""
    best = '<module>'
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = getattr(node, 'end_lineno', None)
        if end is None:
            continue
        if node.lineno <= target.lineno <= end:
            best = node.name
    return best


def _python_sources():
    for dirpath, dirnames, filenames in os.walk(PACKAGE_ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIPPED_DIRECTORIES
                       and not d.startswith('__')]
        for filename in sorted(filenames):
            if filename.endswith('.py'):
                yield os.path.join(dirpath, filename)


def find_bare_error_state_assignments():
    """Every `something.state = <an error state>` in production code."""
    found = []
    for path in _python_sources():
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        relative = os.path.relpath(path, PACKAGE_ROOT)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Attribute):
                    continue
                if target.attr != 'state':
                    continue
                if not _is_error_state_expression(node.value):
                    continue
                found.append((
                    relative, node.lineno,
                    _enclosing_function(tree, node)))
    return found


class ErrorStateAuditTestCase(testtools.TestCase):
    def test_the_audit_reads_this_checkout(self):
        # If this ever reads an installed copy rather than the tree
        # under test, every assertion below passes vacuously.
        self.assertTrue(os.path.exists(
            os.path.join(PACKAGE_ROOT, 'baseobject.py')))
        self.assertTrue(list(_python_sources()))

    def test_the_audit_can_see_an_error_state_assignment(self):
        # The audit is only worth running if its matcher fires. Prove it
        # on each shape of error state expression the tree uses.
        for expression in ['Instance.STATE_ERROR', 'self.STATE_ERROR',
                           "dbo.STATE_ERROR", "'preflight-error'",
                           "inst.state.value + '-error'",
                           "f'{inst.state.value}-error'"]:
            tree = ast.parse(f'obj.state = {expression}')
            assign = tree.body[0]
            self.assertTrue(
                _is_error_state_expression(assign.value),
                f'matcher did not recognise {expression}')

        # And that it does not fire on a non-error state.
        for expression in ['Instance.STATE_CREATED', "'created'"]:
            tree = ast.parse(f'obj.state = {expression}')
            self.assertFalse(
                _is_error_state_expression(tree.body[0].value),
                f'matcher wrongly recognised {expression}')

    def test_no_production_code_enters_an_error_state_bare(self):
        unexpected = []
        for relative, lineno, function in find_bare_error_state_assignments():
            if f'{relative}:{function}' in BARE_ERROR_STATE_ALLOWED:
                continue
            unexpected.append(f'{relative}:{lineno} (in {function})')

        self.assertEqual(
            [], sorted(unexpected),
            'These assignments enter an error state with no reason '
            'recorded, which is the diagnostic dead end of issue 3665. '
            'Call enter_error_state(state, reason) instead, or add an '
            'entry to BARE_ERROR_STATE_ALLOWED saying why this one has '
            'nothing to say for itself.')

    def test_every_allow_list_entry_is_still_real(self):
        # An allow-list entry which no longer matches anything is a
        # stale exemption waiting to excuse an unrelated defect.
        live = {f'{relative}:{function}' for relative, _, function
                in find_bare_error_state_assignments()}
        self.assertEqual(
            set(), set(BARE_ERROR_STATE_ALLOWED) - live,
            'allow-list entries which no longer match any assignment')


class _StatefulObject(DatabaseBackedObject):
    """A minimal object with state transitions, for the tests below."""

    object_type = ObjectType.INSTANCE
    state_targets = {
        DatabaseBackedObject.STATE_INITIAL: (
            DatabaseBackedObject.STATE_CREATED,
            DatabaseBackedObject.STATE_ERROR),
        DatabaseBackedObject.STATE_CREATED: (
            DatabaseBackedObject.STATE_ERROR,
            DatabaseBackedObject.STATE_DELETED),
        DatabaseBackedObject.STATE_ERROR: (
            DatabaseBackedObject.STATE_DELETED,),
    }


class EnterErrorStateTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.set_state = mock.patch('shakenfist.mariadb.set_state').start()
        self.set_state.return_value = True
        # The mutation event these writes emit goes to the database.
        mock.patch.object(DatabaseBackedObject, 'add_event').start()
        self.addCleanup(mock.patch.stopall)

    def _object(self, current_state, message=None,
                cls=_StatefulObject):
        # _state_update() reads through _state_read() rather than the
        # property, so the database read is what has to be mocked.
        mock.patch(
            'shakenfist.mariadb.get_state',
            return_value=State(value=current_state, update_time=10,
                               message=message)).start()
        return cls('12345678-1234-4321-8234-123456789012')

    def test_the_reason_is_written_with_the_state(self):
        obj = self._object(DatabaseBackedObject.STATE_CREATED)
        obj.enter_error_state(
            DatabaseBackedObject.STATE_ERROR, 'the disk fell off')

        self.set_state.assert_called_once()
        written = self.set_state.call_args[0][2]
        self.assertEqual(DatabaseBackedObject.STATE_ERROR, written.value)
        self.assertEqual('the disk fell off', written.message)

    def test_entering_an_error_state_takes_one_write(self):
        # Two writes is what leaves an object in error with no reason if
        # the process dies in between (issue 3665).
        obj = self._object(DatabaseBackedObject.STATE_CREATED)
        obj.enter_error_state(
            DatabaseBackedObject.STATE_ERROR, 'the disk fell off')
        self.assertEqual(1, self.set_state.call_count)

    def test_a_reason_is_required(self):
        obj = self._object(DatabaseBackedObject.STATE_CREATED)
        for empty in [None, '']:
            self.assertRaises(
                exceptions.InvalidStateException,
                obj.enter_error_state,
                DatabaseBackedObject.STATE_ERROR, empty)
        self.set_state.assert_not_called()

    def test_a_non_error_state_is_refused(self):
        obj = self._object(DatabaseBackedObject.STATE_INITIAL)
        self.assertRaises(
            exceptions.InvalidStateException,
            obj.enter_error_state,
            DatabaseBackedObject.STATE_CREATED, 'not an error')
        self.set_state.assert_not_called()

    def test_the_bare_setter_records_where_it_came_from(self):
        # The backstop. A caller the audit above has not caught still
        # must not produce a null error_message: record the caller so
        # the next occurrence names a line of code.
        obj = self._object(DatabaseBackedObject.STATE_CREATED)
        obj.state = DatabaseBackedObject.STATE_ERROR

        self.set_state.assert_called_once()
        written = self.set_state.call_args[0][2]
        self.assertEqual(DatabaseBackedObject.STATE_ERROR, written.value)
        self.assertIsNotNone(written.message)
        self.assertIn('no reason recorded', written.message)

    def test_a_new_reason_is_recorded_when_already_in_that_state(self):
        # _state_update() writes nothing when the state does not change,
        # so an object which failed a second time kept the first reason
        # and the caller was told the second had been recorded.
        obj = self._object(DatabaseBackedObject.STATE_ERROR,
                           message='the first failure')
        obj.enter_error_state(
            DatabaseBackedObject.STATE_ERROR, 'the second failure')

        self.set_state.assert_called_once()
        written = self.set_state.call_args[0][2]
        self.assertEqual(DatabaseBackedObject.STATE_ERROR, written.value)
        self.assertEqual('the second failure', written.message)

    def test_the_backstop_message_names_no_source_path(self):
        # The message is the object's error_message, which anyone who
        # can read the object sees. Where the state was set goes to the
        # log instead.
        obj = self._object(DatabaseBackedObject.STATE_CREATED)
        obj.state = DatabaseBackedObject.STATE_ERROR

        written = self.set_state.call_args[0][2]
        self.assertEqual(baseobject.NO_REASON_RECORDED, written.message)
        self.assertNotIn('.py', written.message)

    def _logged_caller(self, obj):
        with mock.patch.object(baseobject, 'LOG') as mock_log:
            obj.state = DatabaseBackedObject.STATE_ERROR
        return mock_log.with_fields.call_args[0][0]['caller']

    def test_the_backstop_logs_the_line_which_set_the_state(self):
        obj = self._object(DatabaseBackedObject.STATE_CREATED)
        caller = self._logged_caller(obj)
        self.assertIn('test_error_state_reasons.py', caller)
        self.assertIn('_logged_caller()', caller)

    def test_the_backstop_sees_past_an_overridden_state_update(self):
        # Blob overrides _state_update() and calls its parent, which
        # adds a frame. A fixed offset named the state setter, which is
        # no use to anybody.
        obj = self._object(DatabaseBackedObject.STATE_CREATED,
                           cls=_OverridingStatefulObject)
        caller = self._logged_caller(obj)
        self.assertIn('_logged_caller()', caller)


class _OverridingStatefulObject(_StatefulObject):
    """Overrides _state_update() the way Blob does."""

    def _state_update(self, new_value, skip_transition_validation=False,
                      state_attribute_name='state', message=None):
        super()._state_update(
            new_value, skip_transition_validation=skip_transition_validation,
            state_attribute_name=state_attribute_name, message=message)
