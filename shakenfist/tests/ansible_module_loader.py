# Copyright 2026 Michael Still and contributors
"""Load an ansible collection module from source, for testing.

The collection modules are not importable in the normal way: they live in
an ansible collection tree (no __init__.py), and they import ansible and
shakenfist_client, neither of which is a test dependency of this
repository. Load one from source with those two imports stubbed out so the
decisions it makes can be tested here rather than only in the ansible
module CI job, which is merge tier and therefore does not run on a pull
request.

The modules also import the collection's own
plugins/module_utils/sf_connection.py through its installed name,
ansible_collections.shakenfist.shakenfist.plugins.module_utils. That one
is the code under test rather than a dependency, so it is loaded from
source too -- inside the same stub installation, which is what makes
mock.patch.object(module.apiclient, 'Client') reach the client that
sf_connection.make_client() builds: both files bind the same stubbed
apiclient module.
"""
import importlib.util
import os
import sys
import types
from unittest import mock


MODULE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'plugins',
    'modules'))
MODULE_UTILS_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'plugins',
    'module_utils'))

# The dotted path the collection's module_utils is importable as once the
# collection is installed, which is how the modules spell the import.
MODULE_UTILS_PACKAGE = \
    'ansible_collections.shakenfist.shakenfist.plugins.module_utils'
SF_CONNECTION_NAME = MODULE_UTILS_PACKAGE + '.sf_connection'


def _stub_modules(apiclient_attrs):
    """The stub module tree the collection code imports.

    apiclient_attrs adds to or overrides the attributes placed on the
    stubbed shakenfist_client.apiclient. The defaults cover everything the
    five modules read at import time or in make_client(); a caller testing
    exception handling wants its own hierarchy instead.
    """
    stubs = {}

    for name in ('ansible', 'ansible.module_utils',
                 'ansible_collections', 'ansible_collections.shakenfist',
                 'ansible_collections.shakenfist.shakenfist',
                 'ansible_collections.shakenfist.shakenfist.plugins',
                 MODULE_UTILS_PACKAGE):
        package = types.ModuleType(name)
        package.__path__ = []
        stubs[name] = package
    basic = types.ModuleType('ansible.module_utils.basic')
    basic.AnsibleModule = mock.MagicMock()
    stubs['ansible.module_utils'].basic = basic
    stubs['ansible'].module_utils = stubs['ansible.module_utils']
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

    return stubs


def _load_from_source(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_sf_connection(apiclient_attrs=None):
    """Load plugins/module_utils/sf_connection.py and return it."""
    return load_collection_module(None, apiclient_attrs=apiclient_attrs)


def load_collection_module(name, apiclient_attrs=None):
    """Load plugins/modules/<name>.py and return it.

    A name of None returns the loaded sf_connection itself, for tests that
    assert on the shared implementation directly.
    """
    stubs = _stub_modules(apiclient_attrs)

    saved = {key: sys.modules.get(key)
             for key in list(stubs) + [SF_CONNECTION_NAME]}
    sys.modules.update(stubs)
    try:
        # sf_connection has to be executed with the stubs installed (it
        # imports shakenfist_client) and registered before the module is,
        # because the module imports it by its installed name.
        sf_connection = _load_from_source(
            SF_CONNECTION_NAME, os.path.join(MODULE_UTILS_DIR, 'sf_connection.py'))
        sys.modules[SF_CONNECTION_NAME] = sf_connection
        if name is None:
            return sf_connection
        return _load_from_source(
            '%s_under_test' % name, os.path.join(MODULE_DIR, '%s.py' % name))
    finally:
        for key, previous in saved.items():
            if previous is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = previous
