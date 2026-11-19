# Copyright 2019 Michael Still and contributors
"""The suite's shared agent operation waiters, checked where it is cheap.

``base.py`` carries two waiters which poll one agent operation:
``_await_agentop_complete``, which waits for success, and
``_await_agentop_state``, which waits for named states so a test can
assert on a failure it induced on purpose. Both were hoisted out of a
suite file once they had a second caller, and both are inherited rather
than imported, so a caller sees them only if the class it inherits from
is the one they were written on.

That is the failure this file exists to catch. ``_await_agentop_state``
was hoisted into ``base.py`` immediately after ``_await_agentop_complete``
as intended, but on the far side of the next ``class`` statement, so it
became a method of ``TestDistroBoots``. The file imports, flake8 passes
and every unit test passes; the only thing which notices is a cluster,
and the smoke tier takes an hour and a half to say
``AttributeError: ... has no attribute '_await_agentop_state'``. Four
tests failed that way on shakenfist#4982, three of them tests which had
been passing for weeks.

A positional check cannot see this -- the method was exactly where it was
supposed to be. What distinguishes the two outcomes is which class owns
the method, so that is what is asserted here.

The suite modules import ``shakenfist_client``, which is not installed in
the unit test environment, so they are loaded behind the same stubs
``test_ci_agentops_shared.py`` uses.
"""

import logging
import os
import sys
import types

from shakenfist.tests import base as test_base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')

# Every waiter which base.py owns and the suites inherit. A new one
# belongs here, which is the point at which its class is checked too.
WAITERS = ('_await_agentop_complete', '_await_agentop_state')


def _client_stubs():
    """A shakenfist_client which is only its exception taxonomy.

    ``base.py`` builds ``AGENT_OPERATION_FAILURES`` at import time from
    these exception classes, so the stub defines the taxonomy and the
    handful of module level names the suites read, and nothing else.
    """
    client = types.ModuleType('shakenfist_client')
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    class APIException(Exception):
        def __init__(self, message='', method=None, url=None,
                     status_code=None, text=None):
            super().__init__(message)
            self.status_code = status_code
            self.text = text

    apiclient.APIException = APIException
    for name in ('InsufficientResourcesException',
                 'ResourceStateConflictException',
                 'ResourceNotFoundException',
                 'RequestMalformedException',
                 'ServiceUnavailableException',
                 'IncapableException',
                 'AgentCommandError',
                 'AgentOperationFailed',
                 'AgentAwaitTimeout'):
        setattr(apiclient, name, type(name, (APIException,), {}))

    apiclient.ASYNC_PAUSE = 'pause'
    apiclient.ASYNC_CONTINUE = 'continue'
    apiclient.Client = type(
        'Client', (), {'__init__': lambda self, *a, **kw: None})
    apiclient.agent_dependency = lambda operation, settle_seconds=None: {}

    client.apiclient = apiclient
    return client, apiclient


def _load_suite_modules():
    """Import base.py and the suites which wait on an agent operation.

    Everything mutated here is put back, because stestr runs the whole
    unit suite in one process and a stubbed shakenfist_client left in
    sys.modules would be a trap for a later test rather than a
    convenience for this one.
    """
    stub_names = ('prettytable', 'shakenfist_client',
                  'shakenfist_client.apiclient', 'shakenfist_ci',
                  'shakenfist_ci.base', 'shakenfist_ci.instance_hotplug',
                  'shakenfist_ci.process', 'shakenfist_ci.retries',
                  'shakenfist_ci.smoke_ci_tests',
                  'shakenfist_ci.smoke_ci_tests.test_agentop_deadlines',
                  'shakenfist_ci.smoke_ci_tests.test_agentop_dependencies')
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in stub_names}
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level

    try:
        sys.path.insert(0, os.path.dirname(CI_SUITE))
        for name in stub_names:
            sys.modules.pop(name, None)

        prettytable = types.ModuleType('prettytable')
        prettytable.PrettyTable = type('PrettyTable', (), {})
        sys.modules['prettytable'] = prettytable

        client, apiclient = _client_stubs()
        sys.modules['shakenfist_client'] = client
        sys.modules['shakenfist_client.apiclient'] = apiclient

        import shakenfist_ci.base as ci_base
        import shakenfist_ci.smoke_ci_tests.test_agentop_deadlines as deadlines
        import shakenfist_ci.smoke_ci_tests.test_agentop_dependencies \
            as dependencies
        return ci_base, deadlines, dependencies

    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


ci_base, deadlines, dependencies = _load_suite_modules()


SUITE_CLASSES = (deadlines.TestAgentOperationDeadlines,
                 dependencies.TestAgentOperationDependencies)


class AgentOperationWaiterOwnershipTestCase(test_base.ShakenFistTestCase):
    def test_the_namespaced_base_owns_every_waiter(self):
        for name in WAITERS:
            self.assertIn(
                name, vars(ci_base.BaseNamespacedTestCase),
                f'{name} is not defined on BaseNamespacedTestCase, so no '
                'suite class inherits it. It has most likely been written '
                'or moved past a class statement in base.py')

    def test_no_other_class_in_base_defines_a_waiter(self):
        # A copy left on a leaf class is the same bug wearing a hat: the
        # class which has it passes and every other caller does not.
        for attr, obj in sorted(vars(ci_base).items()):
            if not isinstance(obj, type):
                continue
            if obj.__module__ != ci_base.__name__:
                continue
            if obj is ci_base.BaseNamespacedTestCase:
                continue
            for name in WAITERS:
                self.assertNotIn(
                    name, vars(obj),
                    f'{attr} defines its own {name}; the shared one on '
                    'BaseNamespacedTestCase is the only copy there should '
                    'be')

    def test_every_waiting_suite_resolves_the_shared_waiter(self):
        for suite_class in SUITE_CLASSES:
            for name in WAITERS:
                self.assertIs(
                    getattr(suite_class, name, None),
                    getattr(ci_base.BaseNamespacedTestCase, name),
                    f'{suite_class.__module__}.{suite_class.__name__}.'
                    f'{name} is not the BaseNamespacedTestCase '
                    'implementation')
