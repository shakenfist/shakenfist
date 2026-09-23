# Copyright 2026 Michael Still and contributors
"""Load an ansible collection module from source, for testing.

The collection modules are not importable in the normal way: they live in
an ansible collection tree (no __init__.py), and they import ansible and
shakenfist_client, neither of which is a test dependency of this
repository. Load one from source with those two imports stubbed out so the
decisions it makes can be tested here rather than only in the ansible
module CI job, which is merge tier and therefore does not run on a pull
request.

test_ansible_sf_claim.py and test_ansible_sf_instance.py each carry their
own copy of this, predating it. They keep their copies for now because the
exception hierarchies they stub are specific to what those files exercise;
folding them in belongs with the larger deduplication of _make_client()
itself, which is issue 4314.
"""
import importlib.util
import os
import sys
import types
from unittest import mock


MODULE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'plugins',
    'modules'))


def load_collection_module(name, apiclient_attrs=None):
    """Load plugins/modules/<name>.py and return it.

    apiclient_attrs adds to or overrides the attributes placed on the
    stubbed shakenfist_client.apiclient. The defaults cover everything the
    five modules read at import time or in _make_client(); a caller
    testing exception handling wants its own hierarchy instead.
    """
    stubs = {}

    ansible = types.ModuleType('ansible')
    ansible.__path__ = []
    module_utils = types.ModuleType('ansible.module_utils')
    module_utils.__path__ = []
    basic = types.ModuleType('ansible.module_utils.basic')
    basic.AnsibleModule = mock.MagicMock()
    module_utils.basic = basic
    ansible.module_utils = module_utils
    stubs['ansible'] = ansible
    stubs['ansible.module_utils'] = module_utils
    stubs['ansible.module_utils.basic'] = basic

    client = types.ModuleType('shakenfist_client')
    client.__path__ = []
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    # Mirror the real hierarchy: the specific exceptions subclass
    # APIException, so exception clause ordering in a module under test is
    # exercised the same way it runs in production.
    class _APIException(Exception):
        ...

    apiclient.APIException = _APIException
    for specific in ('ResourceNotFoundException', 'IncapableException',
                     'InsufficientResourcesException',
                     'ResourceStateConflictException'):
        setattr(apiclient, specific, type(specific, (_APIException, ), {}))
    apiclient.UnconfiguredException = Exception
    apiclient.ASYNC_BLOCK = 'block'
    apiclient.ASYNC_CONTINUE = 'continue'
    apiclient.Client = mock.MagicMock()
    for attr, value in (apiclient_attrs or {}).items():
        setattr(apiclient, attr, value)
    client.apiclient = apiclient
    stubs['shakenfist_client'] = client
    stubs['shakenfist_client.apiclient'] = apiclient

    saved = {key: sys.modules.get(key) for key in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            '%s_under_test' % name, os.path.join(MODULE_DIR, '%s.py' % name))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for key, previous in saved.items():
            if previous is None:
                del sys.modules[key]
            else:
                sys.modules[key] = previous

    return module
