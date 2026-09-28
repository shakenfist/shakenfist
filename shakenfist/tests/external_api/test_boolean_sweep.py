# Copyright 2019 Michael Still and contributors

"""Ask every declared boolean whether the server reads it the way it publishes it.

Written by the phase 8 push audit of
``docs/plans/PLAN-api-input-validation.md`` (finding B-4). The defect it
exists to close is not a bug in one handler, it is a class:

This validation layer is check-only (decision D14). ``validate_request``
runs the compiled schema for its *findings* and throws the deserialised
result away (``shakenfist/external_api/base.py``), so a handler is handed
the raw request body. marshmallow's ``Boolean`` accepts a set of string
spellings -- ``'false'``, ``'no'``, ``'off'``, ``'0'``, ``'f'``, ``'n'``
and some of their cases are all False -- and every one of them is a
non-empty string, which Python reads as True. So an endpoint which
declares a parameter ``boolean`` and then writes ``if flag:`` publishes a
specification which says the opposite of what the server does. That is
worse than not declaring the parameter at all:
``validation.declared_boolean()`` exists for exactly this, and its
docstring says why.

The phase 7 review found one member of the class (``float`` on a
netdesc) and fixed that one member. The audit then found three more in
twenty minutes by hand, which is the evidence that a reviewer's eye is
the wrong instrument: the class is *not* uniform, so there is nothing to
pattern-match on. Of the nineteen top-level declared booleans, three
arrive through a ``@use_kwargs`` schema and are deserialised before the
handler sees them, three are coerced somewhere downstream, and three are
read with ``is not True`` and fail closed. Only a differential
measurement can tell those apart from the ones that are simply wrong.

So this file is derived and differential:

* **Derived.** ``boolean_declarations()`` walks
  ``declarations.handlers()`` -- the same enumeration
  ``test_required_sweep.required_declarations()`` and the published
  specification are built from -- and reports every parameter declared
  ``boolean``, *and* every boolean property of a structured one, read out
  of ``api_base.ARGTYPES``. That second half arrived in the second round
  of review, which pointed out that filtering on the top-level argtype
  made ``network[].float`` invisible here -- so the one member of the
  class already known to have failed was the one member this enumeration
  could not see. ``test_every_declared_boolean_has_a_reading`` fails if a
  boolean has no entry in ``BOOLEAN_READS``, and if an entry names a
  declaration which no longer exists. A twenty-second boolean cannot join
  the class silently: it fails CI until somebody has measured it.
* **Differential.** Each entry names a request and one *observable*: an
  argument the handler passes on, a key of the response, or the status
  code. Every parameter is sent four times -- the JSON boolean and a
  string spelling, for each of true and false -- and the assertion is
  that the string spelling produces the same observable as the JSON
  boolean marshmallow says it means. There is no hand-written expected
  value anywhere in the table, which is the point: a pinned expectation
  is how ``net.float.yes`` passed for years while being wrong (see
  ``tools/mutate-nested-sweep.sh``'s header on a row which passes for
  the wrong reason).

  The spellings differ by location, because a query string carries no
  JSON: a query parameter's ``true`` *is* the JSON boolean's rendering,
  so sending both would compare a request with itself. ``QUERY_SPELLINGS``
  uses ``'1'`` for the truthy string there, which is in the same
  marshmallow set and is four distinct requests rather than three.

Each entry also carries the anti-vacuity check for free. Before any
spelling is compared, the two JSON booleans are required to produce
*different* observables: an observable which cannot tell ``true`` from
``false`` cannot tell a misread string from a correct one either, and
would make the whole row a decoration.

Three entries deliberately do not agree with their schema, and say so:
``confirm`` on the three delete-all routes is read with
``if confirm is not True``, so every string spelling is refused. That
diverges from the published contract in the *safe* direction, on a
destructive route where an exact JSON ``true`` is a reasonable thing to
insist on, and the verdict ``refuses_strings`` pins it as a deliberate
choice rather than leaving it to look like the defect above.

Runs at ``enforce``, the shipped default, because that is the mode in
which the published specification is making its promise. Every string
spelling used here is in marshmallow's own accepted sets, so the
validation layer answers 200 for all four requests and the handler's
reading is what is left to measure.
"""

import copy
import importlib
import json
from unittest import mock

from shakenfist.config import config
from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.tests.external_api.test_required_sweep import (
    SweepFixtureTestCase)


# Every parameter the API declares ``boolean``, and how to see what the
# handler did with it.
#
# ``url``/``method``/``body``/``query`` build a complete, valid request
# *without* the boolean in it; the sweep adds the boolean itself, once
# per spelling. ``{instance}``, ``{network}``, ``{artifact}``, ``{blob}``
# and ``{unique}`` are substituted by the fixture, exactly as in
# ``test_required_sweep.RECIPES``.
#
# ``spy`` optionally patches one target for the duration of the request:
# ``returns`` gives it a canned answer, ``wraps`` makes it delegate to
# the real implementation so the rest of the handler still works.
#
# A key's third element is the parameter name, and ``parent.property``
# names a boolean *property* of a structured parameter -- the sweep
# writes it into the parent the entry's body already carries, at element
# zero if that parent is an array.
#
# ``read`` says what to observe. ``kwarg:<name>`` reads one keyword
# argument of the spy's recorded call, ``called`` reads whether the spy
# was called at all, ``body:<key>`` reads a key of the JSON response,
# ``body`` reads the whole response document, and ``status`` the status
# code.
#
# ``verdict`` is ``agrees`` -- the string spelling must mean what the
# schema says it means -- or ``refuses_strings``, which requires a 4xx
# and a written reason in ``why``.
BOOLEAN_READS = {
    # The four `get_args` webargs schemas (#4098) are the one shape in
    # the API which is deserialised rather than checked: `all` here is
    # `fields.Boolean(load_default=False)` passed through
    # `@use_kwargs`, so marshmallow hands the handler a real bool and
    # the class cannot reach these three routes. Swept anyway, because
    # "cannot reach" is a property of a declaration somebody could
    # change.
    ('ArtifactOutstandingOperationsEndpoint', 'get', 'all'): {
        'method': 'get',
        'url': '/artifacts/{artifact}/clusteroperations',
        'query': {},
        'spy': {'target': 'shakenfist.artifact.Artifact.get_cluster_operations',
                'returns': []},
        'read': 'kwarg:outstanding_only',
        'verdict': 'agrees',
    },
    ('InstanceOutstandingOperationsEndpoint', 'get', 'all'): {
        'method': 'get',
        'url': '/instances/{instance}/clusteroperations',
        'query': {},
        'spy': {'target': 'shakenfist.instance.Instance.get_cluster_operations',
                'returns': []},
        'read': 'kwarg:outstanding_only',
        'verdict': 'agrees',
    },
    ('NetworkOutstandingOperationsEndpoint', 'get', 'all'): {
        'method': 'get',
        'url': '/networks/{network}/clusteroperations',
        'query': {},
        'spy': {'target': 'shakenfist.network.network.Network.'
                          'get_cluster_operations',
                'returns': []},
        'read': 'kwarg:outstanding_only',
        'verdict': 'agrees',
    },

    # Listings, where a falsy string widens the listing to include
    # deleted objects. The observable is the prefilter the handler
    # chooses, because a fixture with no deleted objects in it would
    # make the response identical either way and the row vacuous.
    ('InstancesEndpoint', 'get', 'all'): {
        'method': 'get',
        'url': '/instances',
        'body': {},
        'spy': {'target': 'shakenfist.instance.Instances', 'returns': []},
        'read': 'kwarg:prefilter',
        'verdict': 'agrees',
    },
    ('NetworksEndpoint', 'get', 'all'): {
        'method': 'get',
        'url': '/networks',
        'body': {},
        'spy': {'target': 'shakenfist.network.network.Networks',
                'returns': []},
        'read': 'kwarg:prefilter',
        'verdict': 'agrees',
    },
    ('InstanceAgentOperationsEndpoint', 'get', 'all'): {
        'method': 'get',
        'url': '/instances/{instance}/agentoperations',
        'body': {},
        'read': 'body',
        'verdict': 'agrees',
    },

    # Instance create. Both of these were shipping wrong: `{"uefi":
    # "false"}` booted with UEFI, and `{"secure_boot": "off"}` turned
    # secure boot on *and* defeated the `secure_boot and not uefi`
    # refusal, because both operands were truthy strings.
    ('InstancesEndpoint', 'post', 'uefi'): {
        'method': 'post',
        'url': '/instances',
        'body': {'name': 'boolsweep{unique}', 'cpus': 1, 'memory': 1024,
                 'disk': [{'size': 8}]},
        'spy': {'target': 'shakenfist.instance.Instance.new', 'wraps': True},
        'read': 'kwarg:uefi',
        'verdict': 'agrees',
    },
    ('InstancesEndpoint', 'post', 'secure_boot'): {
        'method': 'post',
        'url': '/instances',
        'body': {'name': 'boolsweep{unique}', 'cpus': 1, 'memory': 1024,
                 'disk': [{'size': 8}], 'uefi': True},
        'spy': {'target': 'shakenfist.instance.Instance.new', 'wraps': True},
        'read': 'kwarg:secure_boot',
        'verdict': 'agrees',
    },

    # `float`, inside a netdesc, on both routes which take one. This is
    # the member of the class phase 7's review found by eye, and it is
    # here because the enumeration above now descends into the
    # structured schemas -- before that it was the one shape already
    # known to have failed and the one shape this file could not see.
    # `assign_floating_ip` is spied rather than run: whether it was
    # called *is* the reading, and the real one needs a floating network
    # the fixture does not have. The create route answers 507 at
    # placement, after the netdesc is allocated, which is why the
    # observable is the call and not the response.
    ('InstancesEndpoint', 'post', 'network.float'): {
        'method': 'post',
        'url': '/instances',
        'body': {'name': 'boolsweep{unique}', 'cpus': 1, 'memory': 1024,
                 'disk': [{'size': 8}],
                 'network': [{'network_uuid': '{network}'}]},
        'spy': {'target': 'shakenfist.external_api.util.assign_floating_ip',
                'returns': None},
        'read': 'called',
        'verdict': 'agrees',
    },
    ('InstanceInterfacesEndpoint', 'post', 'network.float'): {
        'method': 'post',
        'url': '/instances/{instance}/interfaces',
        'body': {'network': {'network_uuid': '{network}'}},
        'spy': {'target': 'shakenfist.external_api.util.assign_floating_ip',
                'returns': None},
        'read': 'called',
        'verdict': 'agrees',
    },

    # Snapshots. `thin` is read with `if not thin:` and falls back to
    # SNAPSHOTS_DEFAULT_TO_THIN, which a falsy string defeats. That
    # fallback is also why this is the one row whose observable depends
    # on cluster configuration: with SNAPSHOTS_DEFAULT_TO_THIN true,
    # `thin=False` reads as True as well and the anti-vacuity gate fires
    # instead of the row measuring anything. setUp pins the setting for
    # that reason -- the row is about the reading, not about the default.
    ('InstanceSnapshotEndpoint', 'post', 'all'): {
        'method': 'post',
        'url': '/instances/{instance}/snapshot',
        'body': {},
        'spy': {'target': 'shakenfist.instance.Instance.snapshot',
                'returns': {}},
        'read': 'kwarg:all',
        'verdict': 'agrees',
    },
    ('InstanceSnapshotEndpoint', 'post', 'thin'): {
        'method': 'post',
        'url': '/instances/{instance}/snapshot',
        'body': {},
        'spy': {'target': 'shakenfist.instance.Instance.snapshot',
                'returns': {}},
        'read': 'kwarg:thin',
        'verdict': 'agrees',
    },

    # Artifact sharing. Admin-only in effect -- the
    # `request_namespace() != 'system'` check is *inside* the `if
    # shared:` branch, so an unprivileged caller sending 'false' gets a
    # 403 rather than a shared artifact -- but a system operator who
    # sends 'false' meaning "not shared" got an artifact shared with
    # every namespace, silently. The observable is the stored value in
    # the reply.
    ('ArtifactsEndpoint', 'post', 'shared'): {
        'method': 'post',
        'url': '/artifacts',
        'body': {'url': 'http://example.com/boolsweep{unique}.qcow2'},
        'read': 'body:shared',
        'verdict': 'agrees',
    },
    ('ArtifactUploadEndpoint', 'post', 'shared'): {
        'method': 'post',
        'url': '/artifacts/upload/boolsweep{unique}',
        'body': {'blob_uuid': '{blob}'},
        'read': 'body:shared',
        'verdict': 'agrees',
    },

    # Network create. These three are coerced somewhere below the
    # handler, so they were never members of the class -- which is
    # precisely why this table measures rather than inspects.
    ('NetworksEndpoint', 'post', 'provide_dhcp'): {
        'method': 'post',
        'url': '/networks',
        'body': {'netblock': '10.11.12.0/24', 'name': 'boolnet{unique}'},
        'read': 'body:provide_dhcp',
        'verdict': 'agrees',
    },
    ('NetworksEndpoint', 'post', 'provide_nat'): {
        'method': 'post',
        'url': '/networks',
        'body': {'netblock': '10.11.13.0/24', 'name': 'boolnet{unique}'},
        'read': 'body:provide_nat',
        'verdict': 'agrees',
    },
    ('NetworksEndpoint', 'post', 'provide_dns'): {
        'method': 'post',
        'url': '/networks',
        'body': {'netblock': '10.11.14.0/24', 'name': 'boolnet{unique}'},
        'read': 'body:provide_dns',
        'verdict': 'agrees',
    },

    # Delete-all of every network in the system namespace, where the
    # fixture network has an interface: `clean_wait` decides whether a
    # network in use is deleted anyway (202) or refused (403).
    # `_delete_network` is spied rather than run, so the four requests
    # do not consume the fixture they are measuring.
    ('NetworksEndpoint', 'delete', 'clean_wait'): {
        'method': 'delete',
        'url': '/networks',
        'body': {'confirm': True, 'namespace': 'system'},
        'spy': {'target': 'shakenfist.external_api.network._delete_network',
                'returns': (None, 'network_delete', 'op-uuid')},
        'read': 'status',
        'verdict': 'agrees',
    },

    # The three delete-all confirmations. `if confirm is not True` is an
    # identity test, so 'true' is refused even though the published
    # schema says it is a valid boolean meaning True. Kept: a rollback
    # of this one would widen a destructive route, and the divergence
    # is in the direction of refusing.
    #
    # Each spies the per-object destructive call rather than running it,
    # the way the `clean_wait` row spies `_delete_network`. The verdict's
    # positive control has to send a JSON `true`, which is the spelling
    # that actually deletes, so without a spy these rows would run three
    # real delete-alls against the shared fixture and leave every later
    # row's observable dependent on which of them ran first. `scratch`
    # holds nothing today; this is what keeps that from mattering.
    ('ArtifactsEndpoint', 'delete', 'confirm'): {
        'method': 'delete',
        'url': '/artifacts',
        'body': {'namespace': 'scratch'},
        'spy': {'target': 'shakenfist.artifact.Artifact.delete',
                'returns': None},
        'read': 'status',
        'verdict': 'refuses_strings',
        'why': ('read with `if confirm is not True`, so a string spelling '
                'is refused rather than acted on -- deliberate on a route '
                'which deletes every artifact in a namespace'),
    },
    ('InstancesEndpoint', 'delete', 'confirm'): {
        'method': 'delete',
        'url': '/instances',
        'body': {'namespace': 'scratch'},
        'spy': {'target': 'shakenfist.instance.Instance.enqueue_delete',
                'returns': None},
        'read': 'status',
        'verdict': 'refuses_strings',
        'why': ('read with `if confirm is not True`, so a string spelling '
                'is refused rather than acted on -- deliberate on a route '
                'which deletes every instance in a namespace'),
    },
    ('NetworksEndpoint', 'delete', 'confirm'): {
        'method': 'delete',
        'url': '/networks',
        'body': {'namespace': 'scratch'},
        'spy': {'target': 'shakenfist.external_api.network._delete_network',
                'returns': (None, 'network_delete', 'op-uuid')},
        'read': 'status',
        'verdict': 'refuses_strings',
        'why': ('read with `if confirm is not True`, so a string spelling '
                'is refused rather than acted on -- deliberate on a route '
                'which deletes every network in a namespace'),
    },
}


# The spellings sent for each declaration. Both strings are in
# marshmallow's own truthy/falsy sets, so the compiled schema accepts
# all four requests at `enforce` and the only thing which can differ is
# the handler's reading. 'off' rather than 'no' for the falsy string
# because that is the spelling which was found in the wild defeating
# `secure_boot and not uefi`.
SPELLINGS = (
    (True, 'true'),
    (False, 'off'),
)

# The same, for a parameter which arrives in the query string. There is
# no JSON on the wire there, so `_observe` renders the JSON boolean as
# its lower-case literal -- which makes the JSON `true` and the string
# `'true'` the same four bytes, and `observed['true'] != observed[True]`
# a comparison of a request with itself. '1' is in the same marshmallow
# truthy set and is a distinct request, so the truthy half measures
# something here too. The falsy half already did: 'off' is not 'false'.
QUERY_SPELLINGS = (
    (True, '1'),
    (False, 'off'),
)


def _nested_booleans(argtype):
    """The boolean properties inside a structured declaration's schema.

    A parameter declared ``networkspec`` or ``arrayofnetworkspec`` does
    not publish a boolean, but the object it publishes has one --
    ``float`` -- and that is where the defect this whole file exists to
    close actually lived. Read out of ``api_base.ARGTYPES``, which is
    the same rendering the published specification and the compiled
    validator are built from, so a boolean property cannot be in one and
    not the other.
    """
    spec = api_base.ARGTYPES.get(argtype)
    if not isinstance(spec, dict):
        return []
    properties = spec.get('properties')
    if properties is None:
        items = spec.get('items')
        properties = items.get('properties') if isinstance(items, dict) else None
    if not isinstance(properties, dict):
        return []
    return sorted(
        key for key, value in properties.items()
        if isinstance(value, dict) and value.get('type') == 'boolean')


def boolean_declarations():
    """Every boolean the API declares, nested ones included.

    Returns sorted ``(class, method, name)`` tuples, read through
    ``declarations.declarations()`` so this enumeration, the published
    specification and the compiled schemas are all reading the
    declarations the same way. A declaration which cannot be read
    statically is a loud failure rather than a silent omission, for the
    reason ``test_required_sweep.required_declarations()`` gives.

    A boolean *property* of a structured parameter is reported as
    ``parameter.property``. Filtering on ``argtype == 'boolean'`` alone
    would have missed ``network[].float``, which is the member of the
    class phase 7's review found by eye -- so the one shape already
    known to fail would have been the one shape this enumeration could
    not see. Added by the second round of review on phase 8.
    """
    rows = []
    unreadable = []
    for _, _, cls, fn in declarations.handlers():
        for dec in declarations.declarations(fn, cls=cls.name):
            if dec.name is None or dec.location is None:
                unreadable.append('%s.%s' % (cls.name, fn.name))
                continue
            if dec.argtype != 'boolean':
                for prop in _nested_booleans(dec.argtype):
                    rows.append(
                        (cls.name, fn.name, '%s.%s' % (dec.name, prop)))
                continue
            rows.append((cls.name, fn.name, dec.name))
    if unreadable:
        raise AssertionError(
            'declarations on %s cannot be read statically, so this '
            'enumeration cannot claim to be complete; '
            'test_parameter_declarations.py audits the same declarations '
            'and will say what is wrong with them'
            % ', '.join(sorted(set(unreadable))))
    return sorted(rows)


class BooleanSweepTestCase(SweepFixtureTestCase):
    """Every declared boolean, read four ways, at the shipped default."""

    mode = 'enforce'

    def setUp(self):
        super().setUp()

        # `thin` is read with `if not thin: thin = SNAPSHOTS_DEFAULT_TO_THIN`,
        # so the row's observable collapses to True for both JSON
        # booleans if that setting is ever true -- the anti-vacuity gate
        # would fire and the failure would name the observable rather
        # than anything about the reading. Pinned, with the restore, so
        # the sweep measures the handler and not the configuration.
        saved_thin = config.SNAPSHOTS_DEFAULT_TO_THIN
        self.addCleanup(
            setattr, config, 'SNAPSHOTS_DEFAULT_TO_THIN', saved_thin)
        config.SNAPSHOTS_DEFAULT_TO_THIN = False

        # The agent operations listing reads a dict off the instance and
        # picks a key from it, so without two different keys the
        # response cannot tell `all` from not-`all` and the row would
        # pass whatever the handler did.
        queued = 'aaaaaaaa-0000-0000-0000-000000000001'
        finished = 'aaaaaaaa-0000-0000-0000-000000000002'
        agent_ops = mock.patch(
            'shakenfist.instance.Instance.agent_operations',
            new_callable=mock.PropertyMock,
            return_value={'queue': [queued], 'all': [queued, finished]})
        agent_ops.start()
        self.addCleanup(agent_ops.stop)

        def _agentop(uuid, *args, **kwargs):
            op = mock.MagicMock()
            op.external_view.return_value = {'uuid': uuid}
            return op

        agentop_from_db = mock.patch(
            'shakenfist.operations.agentoperation.AgentOperation.from_db',
            side_effect=_agentop)
        agentop_from_db.start()
        self.addCleanup(agentop_from_db.stop)

    def _refusal(self, key, entry, spellings, failures):
        """Check a row whose verdict is a deliberate string refusal.

        These three rows do not get the anti-vacuity gate the rest do.
        That gate compares the two JSON booleans, and this verdict never
        looks at the falsy one -- so it was asserting nothing here.
        Sending the JSON ``true`` is unavoidable, because that is the
        spelling which has to be accepted; each of these rows therefore
        spies its per-object delete, so the control exercises the
        handler's confirm arm without consuming the shared fixture.
        The positive control is what the verdict actually needs: the
        spelling the schema says means true has to be *accepted*, or a
        row claiming that strings are refused would pass just as well
        against a route which refused everything. One request each, and
        every one of them asserted. Added by the second round of review
        on phase 8.
        """
        cls, method, name = key
        accepted = self._observe(entry, name, True)
        if 400 <= accepted < 500:
            failures.append(
                '%s.%s %s: a JSON true answered %r, so this row cannot '
                'tell a deliberate string refusal (%s) from a route which '
                'refuses everything'
                % (cls, method, name, accepted, entry['why']))
            return
        for _, spelling in spellings:
            observed = self._observe(entry, name, spelling)
            if not (400 <= observed < 500):
                failures.append(
                    '%s.%s %s: declared a deliberate string refusal (%s) '
                    'but %r answered %r'
                    % (cls, method, name, entry['why'], spelling, observed))

    def _observe(self, entry, name, value):
        """Send one request with ``name`` set to ``value``, and read it.

        ``deepcopy`` rather than ``dict()``: a nested name writes into a
        structure inside the body, and a shallow copy would leave that
        write in the table for every later request.
        """
        recipe = {
            'url': entry['url'],
            'body': (copy.deepcopy(entry.get('body', {}))
                     if 'body' in entry else None),
            'query': (copy.deepcopy(entry.get('query', {}))
                      if 'query' in entry else {}),
        }
        if recipe['body'] is not None:
            if '.' in name:
                parent, prop = name.split('.', 1)
                target = recipe['body'].get(parent)
                if isinstance(target, list):
                    target = target[0] if target else None
                if not isinstance(target, dict):
                    raise AssertionError(
                        '%r names a property of %r, but this entry\'s body '
                        'carries no %r object to write it into'
                        % (name, parent, parent))
                target[prop] = value
            else:
                recipe['body'][name] = value
        else:
            recipe.pop('body')
        if 'query' in entry:
            # A query string carries text, so the JSON spelling of a
            # boolean is its lower-case literal. webargs and the
            # compiled schema both read that as marshmallow does.
            recipe['query'][name] = (
                str(value).lower() if isinstance(value, bool) else value)

        spy = None
        patcher = None
        spec = entry.get('spy')
        if spec:
            if spec.get('wraps'):
                patcher = mock.patch(
                    spec['target'],
                    wraps=_import_attribute(spec['target']))
            else:
                patcher = mock.patch(spec['target'],
                                     return_value=spec['returns'])
            spy = patcher.start()

        try:
            status, _, response = self._send(entry['method'], recipe)
        finally:
            if patcher:
                patcher.stop()

        read = entry['read']
        if read == 'status':
            return status
        if read in ('called',) or read.startswith('kwarg:'):
            # A reading which needs a spy and has not got one is a
            # named failure rather than an AttributeError from inside
            # this helper -- the same argument the rest of this suite
            # makes about a confident wrong answer versus an absence.
            if spy is None:
                raise AssertionError(
                    'the entry for %r reads %r, which needs a spy, but '
                    'declares none' % (name, read))
            if read == 'called':
                return bool(spy.call_args_list)
            if not spy.call_args_list:
                return ('not called', status)
            return spy.call_args_list[-1].kwargs.get(
                read.split(':', 1)[1], 'absent')
        body = response.get_data(as_text=True)
        if status != 200:
            return ('status %s' % status, body[:200])
        document = json.loads(body)
        if read == 'body':
            return document
        return document[read.split(':', 1)[1]]

    def test_every_declared_boolean_has_a_reading(self):
        """The enumeration is derived, so a new boolean fails here first."""
        declared = set(boolean_declarations())
        described = set(BOOLEAN_READS)
        self.assertEqual(
            set(), declared - described,
            'a parameter is declared boolean and nothing here measures how '
            'the handler reads it. Add an entry to BOOLEAN_READS: a valid '
            'request, an observable, and a verdict. See the module '
            'docstring for why a reviewer reading the handler is not '
            'enough.')
        self.assertEqual(
            set(), described - declared,
            'BOOLEAN_READS describes a declaration which no longer exists; '
            'delete the entry')

    def test_every_reading_agrees_with_the_declaration(self):
        """A string spelling means what the published schema says it means."""
        self._sweep(sorted(BOOLEAN_READS))

    def test_the_readings_do_not_depend_on_row_order(self):
        """The same sweep, rows reversed.

        Every row shares one fixture and several of them create real
        objects -- eight instances, twelve networks in the ``system``
        namespace -- so a row's observable could in principle depend on
        what an earlier row left behind. The first round of review on
        phase 8 read the ``clean_wait`` row as depending on it: its
        observable is a 202-versus-403 over every network in ``system``,
        and it runs before the network-creating rows only because
        ``'clean_wait'`` sorts before ``'post'``.

        It does not, in fact: the falsy arm answers 403 because the
        *fixture* network has an interface, and the rows which run in
        between add networks which have none. But that is an argument,
        and an argument is the thing this file exists not to rely on. So
        the order is measured instead, which also covers the rows
        nobody has reasoned about.
        """
        self._sweep(sorted(BOOLEAN_READS, reverse=True))

    def _sweep(self, keys):
        failures = []
        for key in keys:
            cls, method, name = key
            entry = BOOLEAN_READS[key]
            spellings = QUERY_SPELLINGS if 'query' in entry else SPELLINGS

            if entry['verdict'] == 'refuses_strings':
                self._refusal(key, entry, spellings, failures)
                continue

            observed = {}
            for literal, spelling in spellings:
                observed[literal] = self._observe(entry, name, literal)
                observed[spelling] = self._observe(entry, name, spelling)

            # Anti-vacuity: an observable which cannot tell the two JSON
            # booleans apart cannot tell a misread string from a correct
            # one either.
            if observed[True] == observed[False]:
                failures.append(
                    '%s.%s %s: the observable %r does not distinguish '
                    'true from false (both %r), so this row proves '
                    'nothing' % (cls, method, name, entry['read'],
                                 observed[True]))
                continue

            for literal, spelling in spellings:
                if observed[spelling] != observed[literal]:
                    failures.append(
                        '%s.%s %s: the schema publishes %r as a boolean '
                        'meaning %r, and the handler read it as %r rather '
                        'than %r. Read it with '
                        'validation.declared_boolean()'
                        % (cls, method, name, spelling, literal,
                           observed[spelling], observed[literal]))

        self.assertEqual([], failures, '\n'.join([''] + failures))


def _import_attribute(dotted):
    """Return the object a dotted path names, importing as needed.

    ``mock.patch`` does this itself but does not hand the original
    back, and a ``wraps`` spy needs the original to delegate to.

    Not ``_resolve``: ``SweepFixtureTestCase`` already has a
    ``self._resolve`` which substitutes fixture uuids into a recipe, and
    ``_observe`` uses both within a dozen lines.
    """
    parts = dotted.split('.')
    for split in range(len(parts) - 1, 0, -1):
        try:
            obj = importlib.import_module('.'.join(parts[:split]))
        except ImportError:
            continue
        for attribute in parts[split:]:
            obj = getattr(obj, attribute)
        return obj
    raise ImportError('cannot resolve %s' % dotted)
