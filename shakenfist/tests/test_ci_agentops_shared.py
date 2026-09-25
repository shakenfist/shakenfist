# Copyright 2019 Michael Still and contributors
"""The interface hot plug tests are one implementation, checked here.

Issue 4318: ``test_interface_plug_and_exec_reboot`` (and its dhcp
sibling) existed as near-identical copies in
``smoke_ci_tests/test_agentops.py`` and
``guest_ci_tests/test_agentops.py``, and the copies drifted -- fixes
landed in the smoke copy and never reached the guest copy, which runs in
the merge queue. The fix hoisted the smoke implementation into
``shakenfist_ci/instance_hotplug.py``, following the mixin pattern
``docs/developer_guide/coding_rules.md`` documents with
``database_tier.DatabaseTierTestsMixin`` as the worked example.

This file is the guard against re-drift: it fails if either suite class
stops inheriting the mixin, or grows a local method which shadows one of
the shared tests -- which is exactly what a well-meaning "fix the test
in the suite that failed" edit would do. The functional suites cannot
check this themselves because each only ever imports its own copy.

The suite modules import ``shakenfist_client``, which is not installed
in the unit test environment, so they are imported here behind the same
stubs ``test_ci_capacity_wait.py`` uses to load ``shakenfist_ci.base``.
"""

import logging
import os
import sys
import types

from shakenfist.tests import base as test_base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')


def _client_stubs():
    """A shakenfist_client which is only its exception taxonomy.

    ``base.py`` builds ``AGENT_OPERATION_FAILURES`` at import time from
    these exception classes, and the suite modules assert against them,
    so the stub defines the taxonomy and nothing else.
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

    client.apiclient = apiclient
    return client, apiclient


def _load_suite_modules():
    """Import the mixin and both suites' test_agentops behind stubs.

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
                  'shakenfist_ci.smoke_ci_tests.test_agentops',
                  'shakenfist_ci.guest_ci_tests',
                  'shakenfist_ci.guest_ci_tests.test_agentops')
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

        import shakenfist_ci.guest_ci_tests.test_agentops as guest_agentops
        import shakenfist_ci.instance_hotplug as instance_hotplug
        import shakenfist_ci.smoke_ci_tests.test_agentops as smoke_agentops
        return instance_hotplug, smoke_agentops, guest_agentops

    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


instance_hotplug, smoke_agentops, guest_agentops = _load_suite_modules()


HOTPLUG_TESTS = ('test_interface_plug_and_exec_dhcp',
                 'test_interface_plug_and_exec_reboot')


class InstanceHotplugSharingTestCase(test_base.ShakenFistTestCase):
    def test_mixin_defines_the_hotplug_tests(self):
        for name in HOTPLUG_TESTS:
            self.assertIn(
                name, vars(instance_hotplug.InstanceHotplugTestsMixin),
                f'{name} is missing from InstanceHotplugTestsMixin')

    def test_both_suites_mix_in_the_shared_implementation(self):
        for module in (smoke_agentops, guest_agentops):
            self.assertIn(
                instance_hotplug.InstanceHotplugTestsMixin,
                module.TestAgentOperations.__mro__,
                f'{module.__name__}.TestAgentOperations does not inherit '
                'InstanceHotplugTestsMixin')

    def test_neither_suite_shadows_a_shared_test(self):
        # A local method with one of these names is how the pre-4318
        # duplication would silently come back: the suite would run its
        # own copy and the shared one would look covered in review.
        for module in (smoke_agentops, guest_agentops):
            for name in HOTPLUG_TESTS:
                self.assertNotIn(
                    name, vars(module.TestAgentOperations),
                    f'{module.__name__}.TestAgentOperations defines a local '
                    f'{name}, shadowing the shared implementation on '
                    'InstanceHotplugTestsMixin (issue 4318)')
                self.assertIs(
                    getattr(module.TestAgentOperations, name),
                    getattr(instance_hotplug.InstanceHotplugTestsMixin, name),
                    f'{module.__name__}.TestAgentOperations.{name} is not '
                    'the InstanceHotplugTestsMixin implementation')
