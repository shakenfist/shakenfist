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
import functools
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

# Writes which may set a state without a reason, each with the reason
# it is allowed to.
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

    # The state machine itself, which writes whatever value it was
    # handed: the bare setter (whose error case is the backstop),
    # enter_error_state() (which has the reason), and Blob's override
    # of _state_update(), which passes its message through.
    'baseobject.py:state': 'the setter, which is the backstop',
    'baseobject.py:enter_error_state': 'writes the reason with the state',
    'blob.py:_state_update': 'an override, passes the message through',

    # Every caller passes a lifecycle constant (created, degraded,
    # stopping, stopped) from the sentinel daemons, and the function
    # exists to keep a node *out* of the transition to a lifecycle state
    # when it is in error.
    'node.py:set_lifecycle_state': 'sentinel lifecycle states only',
}


def _could_be_error_state(node):
    """Could this expression evaluate to an error state?

    Answered the safe way round: an expression is cleared only if it is
    provably not an error state -- a string constant which does not end
    in 'error', None, or a ``STATE_*`` name which does not mention
    ERROR. Anything else, including a plain variable, a call or a
    conditional, could be one, and is reported. A false positive costs
    an allow-list entry with a reason, which is the outcome this audit
    wants anyway; a false negative is a production path into an error
    state with no reason, which is what it exists to stop.
    """
    if isinstance(node, ast.Constant):
        if node.value is None:
            return False
        return not (isinstance(node.value, str)
                    and not node.value.endswith('error'))

    if isinstance(node, ast.Name):
        identifier = node.id
    elif isinstance(node, ast.Attribute):
        identifier = node.attr
    else:
        return True
    return not (identifier.startswith('STATE_') and 'ERROR' not in identifier)


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


def _state_value_written(node):
    """The value a node writes as an object's state, or None.

    Every shape which can reach _state_update() from outside it: an
    assignment to ``.state`` (plain or annotated), ``setattr(obj,
    'state', value)``, and a direct ``_state_update(value, ...)`` call,
    which is how AgentOperation.fail() bypassed the bare-assignment
    check this audit started as.
    """
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == 'state':
                return node.value
        return None

    if isinstance(node, ast.AnnAssign):
        if (isinstance(node.target, ast.Attribute)
                and node.target.attr == 'state' and node.value is not None):
            return node.value
        return None

    if not isinstance(node, ast.Call):
        return None

    func = node.func
    if (isinstance(func, ast.Name) and func.id == 'setattr'
            and len(node.args) == 3
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == 'state'):
        return node.args[2]

    if isinstance(func, ast.Attribute) and func.attr == '_state_update':
        if node.args:
            return node.args[0]
        for keyword in node.keywords:
            if keyword.arg == 'new_value':
                return keyword.value
    return None


def find_error_state_writes_in(source, relative):
    """Every write in ``source`` which could enter an error state."""
    tree = ast.parse(source, filename=relative)
    found = []
    for node in ast.walk(tree):
        value = _state_value_written(node)
        if value is None or not _could_be_error_state(value):
            continue
        found.append((relative, node.lineno, _enclosing_function(tree, node)))
    return found


def _python_sources():
    for dirpath, dirnames, filenames in os.walk(PACKAGE_ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIPPED_DIRECTORIES
                       and not d.startswith('__')]
        for filename in sorted(filenames):
            if filename.endswith('.py'):
                yield os.path.join(dirpath, filename)


@functools.cache
def find_bare_error_state_writes():
    """Every write of a possible error state in production code.

    Parsed once per process: it reads the whole package, mariadb.py
    and the generated protos included, and two tests want the answer.
    """
    found = []
    for path in _python_sources():
        with open(path) as f:
            source = f.read()
        found.extend(find_error_state_writes_in(
            source, os.path.relpath(path, PACKAGE_ROOT)))
    return tuple(found)


class ErrorStateAuditTestCase(testtools.TestCase):
    def test_the_audit_reads_this_checkout(self):
        # If this ever reads an installed copy rather than the tree
        # under test, every assertion below passes vacuously. An
        # installed copy has a baseobject.py too, so what is checked is
        # that the package read is the one this test file sits in.
        self.assertEqual(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            PACKAGE_ROOT)
        self.assertTrue(os.path.exists(
            os.path.join(PACKAGE_ROOT, 'baseobject.py')))
        self.assertTrue(list(_python_sources()))

    def test_the_matcher_reports_anything_which_could_be_an_error(self):
        # The audit is only worth running if its matcher fires. Prove it
        # on each shape of error state expression the tree uses, and on
        # a value it cannot see through: a variable is how
        # _end_for_dependency() entered error with no reason, unseen.
        for expression in ['Instance.STATE_ERROR', 'self.STATE_ERROR',
                           'dbo.STATE_ERROR', "'preflight-error'",
                           "inst.state.value + '-error'",
                           "f'{inst.state.value}-error'",
                           'new_state', 'target', 'pick_a_state()',
                           'STATE_CREATED if ok else STATE_ERROR']:
            value = ast.parse(expression, mode='eval').body
            self.assertTrue(
                _could_be_error_state(value),
                f'matcher did not report {expression}')

        # And that it clears what is provably not an error state.
        for expression in ['Instance.STATE_CREATED', "'created'",
                           'STATE_DELETED', 'None']:
            value = ast.parse(expression, mode='eval').body
            self.assertFalse(
                _could_be_error_state(value),
                f'matcher wrongly reported {expression}')

    def test_the_audit_sees_every_way_into_a_state(self):
        for statement in ['obj.state = dbo.STATE_ERROR',
                          'obj.state: str = dbo.STATE_ERROR',
                          "setattr(obj, 'state', dbo.STATE_ERROR)",
                          'self._state_update(dbo.STATE_ERROR, message=m)',
                          'self._state_update(new_value=dbo.STATE_ERROR)',
                          'super()._state_update(new_value)']:
            self.assertEqual(
                1, len(find_error_state_writes_in(statement, 'x.py')),
                f'audit did not see {statement}')

        for statement in ['obj.state = dbo.STATE_CREATED',
                          'obj.error = dbo.STATE_ERROR',
                          "setattr(obj, 'power_state', dbo.STATE_ERROR)",
                          'self._state_update(dbo.STATE_EXPIRED, message=m)',
                          'obj.enter_error_state(dbo.STATE_ERROR, m)']:
            self.assertEqual(
                [], find_error_state_writes_in(statement, 'x.py'),
                f'audit wrongly reported {statement}')

    def test_no_production_code_enters_an_error_state_bare(self):
        unexpected = []
        for relative, lineno, function in find_bare_error_state_writes():
            if f'{relative}:{function}' in BARE_ERROR_STATE_ALLOWED:
                continue
            unexpected.append(f'{relative}:{lineno} (in {function})')

        self.assertEqual(
            [], sorted(unexpected),
            'These writes could enter an error state with no reason '
            'recorded, which is the diagnostic dead end of issue 3665. '
            'Call enter_error_state(state, reason) for an error state, '
            'or add an entry to BARE_ERROR_STATE_ALLOWED saying why this '
            'one cannot be one, or has nothing to say for itself.')

    def test_every_allow_list_entry_is_still_real(self):
        # An allow-list entry which no longer matches anything is a
        # stale exemption waiting to excuse an unrelated defect.
        live = {f'{relative}:{function}' for relative, _, function
                in find_bare_error_state_writes()}
        self.assertEqual(
            set(), set(BARE_ERROR_STATE_ALLOWED) - live,
            'allow-list entries which no longer match any write')


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

    def test_a_missing_reason_still_enters_the_error_state(self):
        # A missing reason is usually the text of an exception which had
        # none, found on a path already handling a failure. Refusing the
        # transition there left the object in the state it was failing
        # out of and raised out of the error handler.
        for empty in [None, '']:
            self.set_state.reset_mock()
            obj = self._object(DatabaseBackedObject.STATE_CREATED)
            with mock.patch.object(baseobject, 'LOG') as mock_log:
                obj.enter_error_state(DatabaseBackedObject.STATE_ERROR, empty)

            self.set_state.assert_called_once()
            written = self.set_state.call_args[0][2]
            self.assertEqual(DatabaseBackedObject.STATE_ERROR, written.value)
            self.assertEqual(baseobject.NO_REASON_RECORDED, written.message)
            caller = mock_log.with_fields.call_args[0][0]['caller']
            self.assertIn('test_error_state_reasons.py', caller)
            self.assertIn(
                'test_a_missing_reason_still_enters_the_error_state()',
                caller)

    def test_a_missing_reason_keeps_the_reason_already_recorded(self):
        # Something said why the object failed the first time; a later
        # caller with nothing to add must not replace it with nothing.
        obj = self._object(DatabaseBackedObject.STATE_ERROR,
                           message='the first failure')
        with mock.patch.object(baseobject, 'LOG'):
            obj.enter_error_state(DatabaseBackedObject.STATE_ERROR, '')
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
