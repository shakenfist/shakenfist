# Copyright 2026 Michael Still and contributors
#
# The one connection story shared by every module in this collection. Each
# module takes the same three connection parameters -- api_url, an identity
# naming the namespace to authenticate as, and key -- and builds the same
# shakenfist_client, so the parameters, the all-or-nothing rule over them
# and the client construction are all defined here once. The modules used
# to each carry a copy and the copies diverged (issue 4314).
#
# The rule table -- which parameter carries each module's identity, and
# whether it may be supplied alone -- is stated for operators in
# docs/user_guide/ansible.md and enforced by
# shakenfist/tests/test_ansible_make_client.py.
from __future__ import annotations

from shakenfist_client import apiclient


def connection_argument_spec(identity_param='namespace'):
    """The argument spec fragment for the shared connection parameters.

    Merged into each module's own argument_spec, so the three parameters
    are spelled once. identity_param is the name of the identity: every
    module but sf_claim calls it namespace; sf_claim already uses
    namespace for the namespace the claim covers, so its identity is
    auth_namespace.
    """
    return {
        'api_url': {'required': False, 'type': 'str'},
        identity_param: {'required': False, 'type': 'str'},
        'key': {'required': False, 'type': 'str', 'no_log': True},
    }


def check_connection(module, identity_param='namespace', identity_optional=False):
    """Refuse a partially specified connection, and say what was supplied.

    api_url, the identity and key are one connection: any of them arriving
    without the rest is a mistake rather than a request to discover,
    because the values passed would be discarded and the module pointed at
    whatever cloud discovery found, with nothing said about it.

    identity_optional is the whole of the difference between the modules.
    Where the identity parameter also names the object to operate on --
    sf_instance and sf_network pass namespace to get_instance() and
    get_network(), and the deployment playbooks have always passed it
    alone -- it is legitimate on its own and keeps meaning "work here, and
    find the credentials the usual way". Where it is an identity and
    nothing else (sf_claim's auth_namespace, sf_namespace, sf_snapshot),
    supplying it alone says only "authenticate as this", which is exactly
    the instruction that would be discarded, so all three are held
    together.

    Each module's run_module() calls this immediately after AnsibleModule
    is built, which is what makes the rule hold on every path -- including
    the check mode paths that return before a client is ever needed.
    make_client() calls it again for callers reaching it directly.

    The rule is deliberately not declared on the argument spec. Ansible's
    required_together and required_by count key presence and never look at
    the value, so an empty string -- which is what
    "{{ sf_url | default('') }}" yields in a templated inventory, and
    which the rest of the collection treats as not supplied -- would be
    refused outright, while a full set with one member empty would be
    accepted and only caught later. Checking here keeps one definition of
    the rule, with one meaning of "supplied".
    """
    supplied = [n for n in ('api_url', identity_param, 'key')
                if module.params.get(n)]
    if supplied and len(supplied) != 3 and not (
            identity_optional and supplied == [identity_param]):
        if identity_optional:
            rule = ('api_url, %s and key must be supplied together, or %s '
                    'alone, which also names the object to operate on'
                    % (identity_param, identity_param))
        else:
            rule = ('api_url, %s and key must be supplied together or not '
                    'at all' % identity_param)
        module.fail_json(
            msg=('%s. Got only %s, which would be discarded in favour of '
                 'discovered configuration.' % (rule, ', '.join(supplied))),
            meta=None, log=[])
    return supplied


def make_client(module, identity_param='namespace', identity_optional=False,
                async_strategy=None):
    """Build a quiet, patient client for a module's task.

    When the full connection is supplied, configuration lookup is
    suppressed and the parameters are used verbatim; otherwise the client
    auto-discovers from the environment and sfrc config exactly like the
    sf-client CLI does. async_strategy defaults to blocking, which is what
    every call site but sf_snapshot's async=true wants.
    """
    supplied = check_connection(
        module, identity_param=identity_param,
        identity_optional=identity_optional)

    kwargs = {
        'verbose': False,
        'sync_request_timeout': 1800,
        'async_strategy': async_strategy or apiclient.ASYNC_BLOCK,
    }
    if len(supplied) == 3:
        kwargs.update({
            'base_url': module.params.get('api_url'),
            'namespace': module.params.get(identity_param),
            'key': module.params.get('key'),
            'suppress_configuration_lookup': True,
        })
    try:
        return apiclient.Client(**kwargs)
    except apiclient.UnconfiguredException as e:
        module.fail_json(
            msg='Could not configure the Shaken Fist client: %s' % e,
            meta=None, log=[])
