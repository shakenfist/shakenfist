# Copyright 2026 Michael Still and contributors
import ast
import os
from unittest import mock

import testtools

from shakenfist.tests import ansible_module_loader
from shakenfist.tests import base


class AnsibleModuleOutcomeTestCase(base.ShakenFistTestCase):
    """The terminal outcomes must fail a test, not the worker.

    Issue 4815: ModuleExited and ModuleFailed were SystemExit subclasses,
    mirroring what the real AnsibleModule raises. SystemExit is not an
    Exception, so when a module took the other outcome than the one a
    test named, neither assertRaises() nor testtools caught it: it
    propagated out of the test runner and killed the stestr worker. The
    worker's remaining tests dropped out of the totals and the mismatch
    was reported as a crashed worker rather than as one failing
    assertion.
    """

    def test_the_outcomes_are_ordinary_exceptions(self):
        for outcome in (ansible_module_loader.ModuleExited,
                        ansible_module_loader.ModuleFailed):
            self.assertTrue(issubclass(outcome, Exception), outcome)
            self.assertFalse(issubclass(outcome, SystemExit), outcome)

    def test_the_two_outcomes_are_told_apart(self):
        # The point of having two: a test which names a success must not
        # be satisfied by an early bail-out, and the other way round.
        exited = ansible_module_loader.ModuleExited
        failed = ansible_module_loader.ModuleFailed
        self.assertFalse(issubclass(exited, failed))
        self.assertFalse(issubclass(failed, exited))
        for outcome in (exited, failed):
            self.assertTrue(
                issubclass(outcome, ansible_module_loader.ModuleOutcome))

    def test_a_mismatched_outcome_is_reported_as_a_failing_test(self):
        # The regression itself, at the only level it is visible from:
        # run a test which expects one outcome and gets the other, and
        # require that the runner collected it.
        def fail_json():
            raise ansible_module_loader.ModuleFailed({'msg': 'boom'})

        class Mismatched(testtools.TestCase):
            def test_outcome(inner):
                inner.assertRaises(
                    ansible_module_loader.ModuleExited, fail_json)

        result = testtools.TestResult()
        try:
            Mismatched('test_outcome').run(result)
        except BaseException as e:
            # With a SystemExit subclass this is where the stestr worker
            # died, taking its remaining tests with it.
            self.fail('a mismatched outcome escaped the test runner: %r' % e)

        self.assertEqual(1, result.testsRun)
        self.assertEqual(
            1, len(result.errors) + len(result.failures),
            'a mismatched outcome was not reported: %s' % result.errors)

    def test_an_outcome_is_raisable_as_a_mock_side_effect(self):
        # How the sf_instance tests install them: mock raises the class
        # itself, with no arguments, so neither may require one.
        module = mock.MagicMock()
        module.exit_json.side_effect = ansible_module_loader.ModuleExited
        module.fail_json.side_effect = ansible_module_loader.ModuleFailed

        self.assertRaises(ansible_module_loader.ModuleExited,
                          module.exit_json, changed=True)
        self.assertRaises(ansible_module_loader.ModuleFailed,
                          module.fail_json, msg='boom')

    def test_an_outcome_carries_the_result_it_was_given(self):
        # How the sf_claim tests install them: the stub module's
        # fail_json() hands the kwargs to the exception.
        failed = ansible_module_loader.ModuleFailed({'msg': 'boom'})
        self.assertEqual({'msg': 'boom'}, failed.result)
        self.assertIsNone(ansible_module_loader.ModuleExited().result)
        self.assertEqual('module failed', str(failed))


class AnsibleTerminalCallTestCase(base.ShakenFistTestCase):
    """No terminal call may sit inside a broad except in the collection.

    This is what makes an ordinary exception safe to use for an outcome
    where production raises SystemExit. A SystemExit is not caught by
    `except Exception`, so a module could put a fail_json() inside one
    and still terminate; a ModuleOutcome in the same place would be
    swallowed, and the test would then go on to exercise code which
    cannot run. There is no such place today -- the single `except
    Exception` in the collection wraps a client call -- and this keeps it
    that way rather than leaving the next one to be found as a passing
    test of an unreachable path.
    """

    TERMINAL = ('exit_json', 'fail_json')

    def _sources(self):
        for directory in (ansible_module_loader.MODULE_DIR,
                          ansible_module_loader.MODULE_UTILS_DIR):
            for name in sorted(os.listdir(directory)):
                if not name.endswith('.py'):
                    continue
                path = os.path.join(directory, name)
                with open(path) as f:
                    yield path, ast.parse(f.read())

    def _terminal_names(self, tree):
        """Module level functions which terminate, directly or by helper."""
        functions = {node.name: node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef,
                                          ast.AsyncFunctionDef))}
        names = set()
        while True:
            found = {name for name, node in functions.items()
                     if name not in names
                     and self._terminates(node, names)}
            if not found:
                return names
            names |= found

    def _terminates(self, node, helpers):
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr in self.TERMINAL:
                return True
            if isinstance(func, ast.Name) and func.id in helpers:
                return True
        return False

    def _is_broad(self, handler):
        if handler.type is None:
            return True
        clauses = (handler.type.elts if isinstance(handler.type, ast.Tuple)
                   else [handler.type])
        return any(isinstance(c, ast.Name)
                   and c.id in ('Exception', 'BaseException')
                   for c in clauses)

    def test_no_terminal_call_is_inside_a_broad_except(self):
        offences = []
        seen = []
        for path, tree in self._sources():
            helpers = self._terminal_names(tree)
            # A wrong directory would make this sweep pass by reading
            # nothing, so require that each file really is collection
            # code: every one of them terminates the module somewhere.
            self.assertNotEqual(set(), helpers, path)
            seen.append(os.path.basename(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                if not any(self._is_broad(h) for h in node.handlers):
                    continue
                for statement in node.body:
                    if self._terminates(statement, helpers):
                        offences.append(
                            '%s:%d' % (os.path.basename(path),
                                       statement.lineno))
        self.assertIn('sf_instance.py', seen)
        self.assertIn('sf_connection.py', seen)
        self.assertEqual(
            [], offences,
            'a module terminal call sits inside a try which catches '
            'Exception, where the test stubs\' ModuleOutcome would be '
            'swallowed instead of ending the module: %s' % offences)

    def test_the_guard_finds_a_terminal_call_in_a_broad_except(self):
        # The guard above is only worth having if it fires, and the shape
        # it looks for does not exist in the tree to demonstrate on.
        tree = ast.parse(
            'def run_module():\n'
            '    try:\n'
            '        module.fail_json(msg="boom")\n'
            '    except Exception:\n'
            '        pass\n')
        helpers = self._terminal_names(tree)
        tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        self.assertTrue(any(self._is_broad(h) for h in tries[0].handlers))
        self.assertTrue(self._terminates(tries[0].body[0], helpers))
