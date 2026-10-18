# Copyright 2019 Michael Still and contributors
import datetime
import itertools
import json
from unittest import mock

from shakenfist.tests import ansible_module_loader
from shakenfist.tests import base


# The shared loader's default apiclient stub carries the exception
# hierarchy this file raises (ResourceNotFoundException subclassing
# APIException), so no overrides are needed.
sf_instance = ansible_module_loader.load_collection_module('sf_instance')


class SfInstanceDiskDirtinessTestCase(base.ShakenFistTestCase):
    # A minimal instance which is not dirty for any reason other than
    # (possibly) its disks. Interfaces are empty so _check_instance() never
    # calls out to the client.
    def _existing(self, disk_spec):
        return {
            'name': 'test',
            'cpus': 1,
            'memory': 1024,
            'interfaces': [],
            'disk_spec': disk_spec
        }

    def _params(self, **kwargs):
        params = {
            'name': 'test',
            'cpu': 1,
            'ram': 1024
        }
        params.update(kwargs)
        return params

    def _check(self, existing, params):
        log = []
        dirty, _, _ = sf_instance._check_instance(
            mock.MagicMock(), existing, params, log)
        return dirty, log

    def test_server_resolved_blob_uuid_is_not_dirty(self):
        # The exact signature from issue 3669: the server resolved the disk's
        # base into a blob once the image fetch completed, and the unchanged
        # request must still compare clean.
        existing = self._existing([{
            'base': 'debian:11',
            'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, log = self._check(existing, self._params(disks=['10@debian:11']))
        self.assertFalse(dirty, log)

    def test_unresolved_disk_spec_is_not_dirty(self):
        # The same request before the server resolved the base.
        existing = self._existing([{
            'base': 'debian:11',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, log = self._check(existing, self._params(disks=['10@debian:11']))
        self.assertFalse(dirty, log)

    def test_disk_base_is_still_stripped(self):
        existing = self._existing([{
            'base': None,
            'disk_base': None,
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, log = self._check(existing, self._params(disks=['10']))
        self.assertFalse(dirty, log)

    def test_existing_disk_spec_is_not_mutated(self):
        # _check_instance() is called with the instance the caller fetched
        # from the API, and must not edit it in place.
        disk = {
            'base': 'debian:11',
            'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
            'disk_base': 'blob://73b4e968-7276-4b10-a03e-1258bcb930f1',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }
        existing = self._existing([disk])
        self._check(existing, self._params(disks=['10@debian:11']))
        self.assertIn('blob_uuid', disk)
        self.assertIn('disk_base', disk)

    def test_explicitly_requested_blob_uuid_is_compared(self):
        # A caller who names a blob_uuid in a diskspec does want it compared,
        # so a mismatch is still dirty.
        existing = self._existing([{
            'base': None,
            'blob_uuid': 'aaaaaaaa-0000-0000-0000-000000000000',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        params = self._params(diskspecs=[
            'size=10,blob_uuid=bbbbbbbb-0000-0000-0000-000000000000'])
        dirty, _ = self._check(existing, params)
        self.assertTrue(dirty)

    def test_explicitly_requested_blob_uuid_matching_is_clean(self):
        existing = self._existing([{
            'base': None,
            'blob_uuid': 'aaaaaaaa-0000-0000-0000-000000000000',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        params = self._params(diskspecs=[
            'size=10,blob_uuid=aaaaaaaa-0000-0000-0000-000000000000'])
        dirty, log = self._check(existing, params)
        self.assertFalse(dirty, log)

    def test_changed_disk_size_is_still_dirty(self):
        existing = self._existing([{
            'base': 'debian:11',
            'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
            'bus': None,
            'size': 10,
            'type': 'disk'
        }])
        dirty, _ = self._check(existing, self._params(disks=['20@debian:11']))
        self.assertTrue(dirty)

    def test_extra_existing_disk_is_still_dirty(self):
        # An unmatched existing disk has no requested counterpart, so its
        # server populated keys are stripped -- and the length difference
        # must still be caught.
        existing = self._existing([
            {
                'base': 'debian:11',
                'blob_uuid': '73b4e968-7276-4b10-a03e-1258bcb930f1',
                'bus': None,
                'size': 10,
                'type': 'disk'
            },
            {
                'base': None,
                'bus': None,
                'size': 20,
                'type': 'disk'
            }
        ])
        dirty, _ = self._check(existing, self._params(disks=['10@debian:11']))
        self.assertTrue(dirty)


class SfInstanceParameterDirtinessTestCase(base.ShakenFistTestCase):
    """Every parameter compares against the key the API actually reports.

    Issue 4061: the parameter was spelled secureboot and compared
    against existing['secureboot'], but the API reports secure_boot, so
    any explicit value -- including one matching the server exactly --
    was perpetually dirty and the instance was replaced on every run.
    The parameter has since been renamed to the API's name, leaving
    cpu/cpus and ram/memory as the only surviving mappings; the
    round-trip below covers the whole comparable family so a new
    mismatch cannot regress quietly.
    """

    # Every documented parameter which participates in the dirtiness
    # comparison, as (parameter name, API field name, value as passed,
    # value as the API reports it, a different value). video and
    # placement are deliberately absent: their API representations
    # differ in shape from the parameter (a dict and a node attribute
    # respectively), which is the same class of defect but not this
    # fix -- see issue 4061's discussion.
    ROUND_TRIP = [
        ('ssh_key', 'ssh_key', 'ssh-rsa AAAA', 'ssh-rsa AAAA', 'ssh-rsa BBBB'),
        ('user_data', 'user_data', 'dXNlcg==', 'dXNlcg==', 'b3RoZXI='),
        ('nvram_template', 'nvram_template', 'tpl-one', 'tpl-one', 'tpl-two'),
        ('configdrive', 'configdrive', 'openstack-disk', 'openstack-disk',
         'none'),
        ('side_channels', 'side_channels', ['sf-agent'], ['sf-agent'],
         ['sf-agent2']),
        ('uefi', 'uefi', True, True, False),
        ('secure_boot', 'secure_boot', True, True, False),
    ]

    def _existing(self, **kwargs):
        existing = {
            'name': 'test',
            'cpus': 1,
            'memory': 1024,
            'interfaces': [],
            'disk_spec': []
        }
        existing.update(kwargs)
        return existing

    def _params(self, **kwargs):
        params = {
            'name': 'test',
            'cpu': 1,
            'ram': 1024
        }
        params.update(kwargs)
        return params

    def _check(self, existing, params):
        log = []
        dirty, _, instance_kwargs = sf_instance._check_instance(
            mock.MagicMock(), existing, params, log)
        return dirty, instance_kwargs, log

    def test_matching_values_are_clean(self):
        # The exact signature from issue 4061 is secure_boot=False against
        # a server reporting secure_boot=False, but every parameter in the
        # family has to round-trip: an instance which matches its request
        # must never be replaced.
        params = {}
        existing_extras = {}
        for param_name, existing_name, requested, reported, _ in \
                self.ROUND_TRIP:
            params[param_name] = requested
            existing_extras[existing_name] = reported
        dirty, _, log = self._check(
            self._existing(**existing_extras), self._params(**params))
        self.assertFalse(dirty, log)

    def test_explicit_false_booleans_match_the_server_default(self):
        # The value most callers write, and the one issue 4061 reported:
        # the server defaults both booleans to False and reports them.
        dirty, _, log = self._check(
            self._existing(uefi=False, secure_boot=False),
            self._params(uefi=False, secure_boot=False))
        self.assertFalse(dirty, log)

    def test_differing_values_are_dirty(self):
        for param_name, existing_name, _, reported, different in \
                self.ROUND_TRIP:
            dirty, _, _ = self._check(
                self._existing(**{existing_name: reported}),
                self._params(**{param_name: different}))
            self.assertTrue(dirty, '%s should be dirty' % param_name)

    def test_unset_parameters_are_not_compared(self):
        # An unset parameter means "no preference": the server value must
        # not make the instance dirty.
        existing_extras = {}
        for _, existing_name, _, reported, _ in self.ROUND_TRIP:
            existing_extras[existing_name] = reported
        dirty, _, log = self._check(
            self._existing(**existing_extras), self._params())
        self.assertFalse(dirty, log)

    def test_unset_parameters_are_not_passed(self):
        # A clean check returns no kwargs at all, so force dirtiness with
        # a cpu change: even then, unset optional parameters must not be
        # passed to create_instance(), so the server applies its default.
        dirty, instance_kwargs, _ = self._check(
            self._existing(), self._params(cpu=2))
        self.assertTrue(dirty)
        for param_name, existing_name, _, _, _ in self.ROUND_TRIP:
            self.assertNotIn(param_name, instance_kwargs)
            self.assertNotIn(existing_name, instance_kwargs)

    def test_booleans_are_passed_under_the_api_names(self):
        # The create_instance() kwarg has always been secure_boot, whatever
        # the parameter was called; renaming the parameter must not change
        # what is passed, and the old spelling must not reappear.
        _, instance_kwargs, _ = self._check(
            self._existing(), self._params(uefi=True, secure_boot=True))
        self.assertEqual(True, instance_kwargs.get('secure_boot'))
        self.assertEqual(True, instance_kwargs.get('uefi'))
        self.assertNotIn('secureboot', instance_kwargs)


class SfInstanceEmptyStringDirtinessTestCase(base.ShakenFistTestCase):
    """An explicit empty ssh_key or user_data means "none", like the server.

    Issue 4276: the server does not distinguish "no user data" from an
    explicit empty string -- both are stored the same way and reported
    back as null. The module compared the caller's "" against that null,
    so an instance created with user_data: "" was perpetually dirty and
    silently replaced on every run. ssh_key has the same shape.
    """

    def _existing(self, **kwargs):
        existing = {
            'name': 'test',
            'cpus': 1,
            'memory': 1024,
            'interfaces': [],
            'disk_spec': []
        }
        existing.update(kwargs)
        return existing

    def _params(self, **kwargs):
        params = {
            'name': 'test',
            'cpu': 1,
            'ram': 1024
        }
        params.update(kwargs)
        return params

    def _check(self, existing, params):
        log = []
        dirty, instance_args, _ = sf_instance._check_instance(
            mock.MagicMock(), existing, params, log)
        return dirty, instance_args, log

    def test_empty_strings_against_stored_null_are_clean(self):
        # The exact signature from issue 4276: the caller wrote "" and
        # the server reports null for both fields.
        dirty, _, log = self._check(
            self._existing(ssh_key=None, user_data=None),
            self._params(ssh_key='', user_data=''))
        self.assertFalse(dirty, log)

    def test_empty_strings_against_stored_empty_strings_are_clean(self):
        # Defensive symmetry: a server which reported '' rather than null
        # must compare clean too.
        dirty, _, log = self._check(
            self._existing(ssh_key='', user_data=''),
            self._params(ssh_key='', user_data=''))
        self.assertFalse(dirty, log)

    def test_unset_against_stored_empty_strings_is_clean(self):
        dirty, _, log = self._check(
            self._existing(ssh_key='', user_data=''), self._params())
        self.assertFalse(dirty, log)

    def test_empty_strings_are_passed_as_none(self):
        # The server treats '' and None identically on create, so the
        # normalised spelling is what gets passed. Force dirtiness with a
        # cpu change to observe the args of a create that would happen.
        # ssh_key and user_data are positional: (name, cpus, memory,
        # networks, disks, ssh_key, user_data).
        dirty, instance_args, _ = self._check(
            self._existing(), self._params(cpu=2, ssh_key='', user_data=''))
        self.assertTrue(dirty)
        self.assertIsNone(instance_args[5])
        self.assertIsNone(instance_args[6])

    def test_real_values_against_stored_null_are_still_dirty(self):
        dirty, _, _ = self._check(
            self._existing(ssh_key=None, user_data=None),
            self._params(ssh_key='ssh-rsa AAAA', user_data='dXNlcg=='))
        self.assertTrue(dirty)

    def test_real_values_are_still_passed(self):
        dirty, instance_args, _ = self._check(
            self._existing(ssh_key=None, user_data=None),
            self._params(ssh_key='ssh-rsa AAAA', user_data='dXNlcg=='))
        self.assertTrue(dirty)
        self.assertEqual('ssh-rsa AAAA', instance_args[5])
        self.assertEqual('dXNlcg==', instance_args[6])


class _Succeeded(SystemExit):
    """The module called exit_json()."""


class _Failed(SystemExit):
    """The module called fail_json()."""


class SfInstanceCreateBudgetTestCase(base.ShakenFistTestCase):
    """await_timeout is the budget for the whole create, not one leg.

    shakenfist/kerbside#355: the client is built with ASYNC_BLOCK, so
    create_instance waited an hour for the instance to leave 'creating'
    and then returned it still transitional. Only then did the 600 second
    await start, on the same condition. The task took the sum, about 4200
    seconds, and reported the 600 -- which is why three occurrences all
    landed within twelve seconds of each other.
    """

    PARAMS = {
        'name': 'test', 'uuid': None, 'cpu': 1, 'ram': 1024,
        'disks': ['8@cirros'], 'diskspecs': None, 'networks': None,
        'networkspecs': None, 'ssh_key': None, 'user_data': None,
        'placement': None, 'video': None, 'nvram_template': None,
        'configdrive': None, 'side_channels': None, 'uefi': None,
        'secure_boot': None, 'metadata': None, 'state': 'present',
        'api_url': 'http://localhost:13000', 'namespace': 'ns',
        'key': 'notreallyakey',
    }

    def _run(self, *args, **kwargs):
        _module, client = self._run_module_and_client(*args, **kwargs)
        return client

    def _run_module(self, *args, **kwargs):
        module, _client = self._run_module_and_client(*args, **kwargs)
        return module

    def _run_module_and_client(self, await_instance,
                               client_takes_timeout=True, create_seconds=0,
                               existing=None, await_raises=None,
                               expect_failure=False):
        params = dict(self.PARAMS)
        params['await'] = await_instance
        params['await_timeout'] = 600

        module = mock.MagicMock()
        module.params = params
        module.check_mode = False
        # The real AnsibleModule.exit_json terminates the module; a bare
        # MagicMock would let execution fall through into the deletion
        # path below it. Distinct exception types rather than a shared
        # SystemExit, because assertRaises(SystemExit) cannot tell a
        # success from an early bail-out, and a test whose body is two
        # negative assertions would then pass without running any of the
        # code it names.
        module.exit_json.side_effect = _Succeeded
        module.fail_json.side_effect = _Failed

        created = {'uuid': 'notreallyauuid', 'state': 'creating'}

        # An explicit pair rather than an open ended counter: the module
        # reads the clock exactly twice on this path (once before the
        # create, once before the await), so a third read from anywhere
        # fails loudly instead of quietly shifting the elapsed time.
        clock = iter([0, create_seconds])

        # The signature is the thing under test on the compatibility
        # path, so build create_instance from a real function rather than
        # a bare mock, whose signature is always (*args, **kwargs).
        if client_takes_timeout:
            def create_instance(*args, timeout=None, **kwargs):
                return created
        else:
            def create_instance(*args, **kwargs):
                return created

        client = mock.MagicMock()
        # create_autospec rather than a spec'd MagicMock: only the former
        # keeps a signature inspect.signature() can read, which is the
        # thing _create_accepts_timeout() looks at.
        client.create_instance = mock.create_autospec(
            create_instance, side_effect=create_instance)
        if existing is None:
            # Absent to begin with, so the module takes the create path;
            # every later lookup (the exit_json payload) finds it.
            first = sf_instance.apiclient.ResourceNotFoundException()
        else:
            first = existing
        client.get_instance.side_effect = [first] + [created] * 8
        if await_raises:
            client.await_instance_create.side_effect = await_raises

        with mock.patch.object(
                sf_instance, 'AnsibleModule', return_value=module), \
                mock.patch.object(
                    sf_instance, 'make_client', return_value=client), \
                mock.patch.object(
                    sf_instance.time, 'monotonic',
                    side_effect=lambda: next(clock)):
            self.assertRaises(
                _Failed if expect_failure else _Succeeded,
                sf_instance.run_module)

        return module, client

    def test_awaiting_makes_the_create_return_immediately(self):
        client = self._run(await_instance=True)

        _args, kwargs = client.create_instance.call_args
        self.assertEqual(0, kwargs.get('timeout'))

        # The create returns straight away, so the whole budget is left
        # for the await -- which is the point: the task now fails at
        # await_timeout rather than at 3600 + await_timeout.
        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(600, kwargs['timeout'])

    def test_not_awaiting_leaves_the_create_blocking(self):
        # Without an await the create is the only thing that can wait, so
        # the historical behaviour has to survive.
        client = self._run(await_instance=False)

        _args, kwargs = client.create_instance.call_args
        self.assertNotIn('timeout', kwargs)
        client.await_instance_create.assert_not_called()

    def test_an_older_client_is_not_handed_the_new_argument(self):
        # The collection requires shakenfist-client unpinned, so the
        # control node may predate the timeout argument. Passing it there
        # is a TypeError that stops every instance creation.
        client = self._run(await_instance=True, client_takes_timeout=False)

        _args, kwargs = client.create_instance.call_args
        self.assertNotIn('timeout', kwargs)

    def test_time_spent_creating_comes_out_of_the_await_budget(self):
        # The fallback for an older client: it cannot get the task down to
        # await_timeout, but the two waits stop being added together.
        client = self._run(await_instance=True, client_takes_timeout=False,
                           create_seconds=100)

        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(500, kwargs['timeout'])

    def test_the_budget_never_goes_negative(self):
        client = self._run(await_instance=True, client_takes_timeout=False,
                           create_seconds=3600)

        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(0, kwargs['timeout'])

    def test_an_exhausted_budget_names_the_real_cause(self):
        # The client's own message would report a zero second timeout,
        # which is the symptom of the budget having gone rather than an
        # explanation of where it went -- and an unhelpful message is
        # what kerbside#355 was reported as in the first place.
        module = self._run_module(
            await_instance=True, client_takes_timeout=False,
            create_seconds=3600, await_raises=Exception('not created'),
            expect_failure=True)

        msg = module.fail_json.call_args[1]['msg']
        self.assertIn(
            'entire await_timeout budget of 600 seconds was consumed', msg)

    def test_an_instance_needing_no_replacement_gets_the_whole_budget(self):
        # No delete and no create happen on this path at all, so there
        # is nothing to deduct and the whole budget reaches the await.
        client = self._run(
            await_instance=True,
            existing={
                'uuid': 'notreallyauuid',
                'name': 'test',
                'cpus': 1,
                'memory': 1024,
                'namespace': 'ns',
                'interfaces': [],
                'disk_spec': [
                    {'base': 'cirros', 'bus': None, 'size': 8, 'type': 'disk'}
                ]
            })

        client.create_instance.assert_not_called()
        _args, kwargs = client.await_instance_create.call_args
        self.assertEqual(600, kwargs['timeout'])

    def test_the_chosen_path_is_recorded_in_the_log(self):
        # The fallback is otherwise invisible: an operator debugging a
        # recurrence from the task output alone cannot tell whether the
        # fix engaged or the module quietly took the old behaviour.
        module = self._run_module(await_instance=True)
        self.assertIn('Client accepts a create timeout, so the create will '
                      'not wait', module.exit_json.call_args[1]['log'])

        module = self._run_module(
            await_instance=True, client_takes_timeout=False)
        self.assertIn('Client predates the create timeout argument, so the '
                      'create will wait and what it spends is deducted from '
                      'the await budget instead',
                      module.exit_json.call_args[1]['log'])


class SfInstanceDeleteBoundTestCase(base.ShakenFistTestCase):
    """_delete_and_wait() must hold its advertised 180 second bound.

    Issue 3851: the client is built with ASYNC_BLOCK, so a plain
    delete_instance() ran its own poll loop for up to 3600 seconds inside
    the first iteration. The 180 was never a deadline, only a "do not
    start another attempt after this" check evaluated between iterations,
    so a stuck instance held the task for an hour -- with no budget at
    all around it on the state=absent path.
    """

    def _run(self, get_results):
        log = []
        client = mock.MagicMock()
        client.get_instance.side_effect = get_results
        # One simulated second per clock read, and sleeping costs
        # nothing, so a stuck instance runs the loop to its bound in
        # milliseconds of real time.
        clock = itertools.count()
        with mock.patch.object(
                sf_instance.time, 'monotonic',
                side_effect=lambda: next(clock)), \
                mock.patch.object(sf_instance.time, 'sleep'):
            result = sf_instance._delete_and_wait(
                client, log, 'notreallyauuid', 'ns')
        return result, client, next(clock)

    def test_the_delete_is_asked_not_to_wait(self):
        # async_request=True is what makes the client return after the
        # DELETE is accepted instead of polling against its own hour long
        # ASYNC_BLOCK deadline; it returns {} rather than the instance,
        # which this function never reads.
        result, client, _ = self._run([{'state': 'deleted'}])
        self.assertIsNone(result)
        client.delete_instance.assert_called_once_with(
            'notreallyauuid', namespace='ns', async_request=True)

    def test_polling_observes_the_deletion(self):
        result, client, _ = self._run(
            [{'state': 'created'}, {'state': 'created'},
             {'state': 'deleted'}])
        self.assertIsNone(result)
        self.assertEqual(3, client.get_instance.call_count)

    def test_a_vanished_instance_is_a_successful_deletion(self):
        result, _, _ = self._run(
            sf_instance.apiclient.ResourceNotFoundException())
        self.assertIsNone(result)

    def test_a_stuck_instance_returns_within_the_stated_bound(self):
        stuck = {'state': 'created'}
        result, client, elapsed = self._run(itertools.repeat(stuck))

        # Control returned with the instance still there, inside the
        # bound the function advertises rather than an hour after it.
        self.assertEqual(stuck, result)
        self.assertLess(elapsed, 200)

        # And the DELETE was enqueued once, not once per second: 180
        # deletion operations against an instance that is already stuck
        # is the failure mode a non-blocking retrying loop would have.
        self.assertEqual(1, client.delete_instance.call_count)


class SfInstanceAPIErrorLogTestCase(base.ShakenFistTestCase):
    """An API failure must fail through fail_json() with the log.

    Issue 4060: the log carries the dirtiness reasoning -- the only
    record of why the module decided to destroy and recreate an
    instance. The create and delete calls were unguarded, so an
    APIException escaped to ansible as a bare module crash and the log
    was discarded at exactly the moment it became useful: a create
    failure is usually the consequence of a dirtiness decision the
    operator did not expect.
    """

    PARAMS = {
        'name': 'test', 'uuid': None, 'cpu': 1, 'ram': 1024,
        'disks': ['8@cirros'], 'diskspecs': None, 'networks': None,
        'networkspecs': None, 'ssh_key': None, 'user_data': None,
        'placement': None, 'video': None, 'nvram_template': None,
        'configdrive': None, 'side_channels': None, 'uefi': None,
        'secureboot': None, 'metadata': None, 'state': 'present',
        'await': False, 'await_timeout': 600,
        'api_url': 'http://localhost:13000', 'namespace': 'ns',
        'key': 'notreallyakey',
    }

    # Dirty on cpu alone, so every present-path test's log carries a
    # 'Instance dirty: cpu has changed' line -- the evidence the module
    # must not discard.
    EXISTING_DIRTY = {
        'uuid': 'notreallyauuid', 'name': 'test', 'cpus': 2,
        'memory': 1024, 'namespace': 'ns', 'interfaces': [],
        'disk_spec': [
            {'base': 'cirros', 'bus': None, 'size': 8, 'type': 'disk'}
        ],
    }

    def _run(self, client, state='present', expect_failure=True):
        params = dict(self.PARAMS)
        params['state'] = state

        module = mock.MagicMock()
        module.params = params
        module.check_mode = False
        module.exit_json.side_effect = _Succeeded
        module.fail_json.side_effect = _Failed

        # One simulated second per clock read and free sleeps, so the
        # deletion poll in _delete_and_wait() costs no real time.
        clock = itertools.count()
        with mock.patch.object(
                sf_instance, 'AnsibleModule', return_value=module), \
                mock.patch.object(
                    sf_instance, 'make_client', return_value=client), \
                mock.patch.object(
                    sf_instance.time, 'monotonic',
                    side_effect=lambda: next(clock)), \
                mock.patch.object(sf_instance.time, 'sleep'):
            self.assertRaises(
                _Failed if expect_failure else _Succeeded,
                sf_instance.run_module)

        return module

    def _msg_and_log(self, module):
        kwargs = module.fail_json.call_args[1]
        return kwargs['msg'], kwargs['log']

    def test_a_failed_create_reports_the_dirtiness_log(self):
        # The exact shape from the issue: a replacement was decided, the
        # old instance deleted, and then the create 409ed. The log must
        # arrive with the failure, carrying the reasoning.
        client = mock.MagicMock()
        client.get_instance.side_effect = [
            self.EXISTING_DIRTY, {'state': 'deleted'}]
        client.create_instance.side_effect = \
            sf_instance.apiclient.APIException('address 10.0.2.2 in use')

        msg, log = self._msg_and_log(self._run(client))
        self.assertEqual(
            'Instance creation failed: address 10.0.2.2 in use', msg)
        self.assertIn('Instance dirty: cpu has changed from 2 to 1', log)
        self.assertIn(msg, log)

    def test_a_failed_delete_for_update_reports_the_dirtiness_log(self):
        client = mock.MagicMock()
        client.get_instance.side_effect = [self.EXISTING_DIRTY]
        client.delete_instance.side_effect = \
            sf_instance.apiclient.APIException('boom')

        module = self._run(client)
        msg, log = self._msg_and_log(module)
        self.assertEqual('Instance deletion for update failed: boom', msg)
        self.assertIn('Instance dirty: cpu has changed from 2 to 1', log)
        client.create_instance.assert_not_called()

    def test_a_failed_interface_read_reports_the_dirtiness_log(self):
        existing = dict(self.EXISTING_DIRTY)
        existing['interfaces'] = [{'uuid': 'notreallyanifaceuuid'}]
        client = mock.MagicMock()
        client.get_instance.side_effect = [existing]
        client.get_interface.side_effect = \
            sf_instance.apiclient.APIException('boom')

        msg, log = self._msg_and_log(self._run(client))
        self.assertEqual(
            'Comparing against the existing instance failed: boom', msg)
        self.assertIn('Instance dirty: cpu has changed from 2 to 1', log)

    def test_a_failed_existing_lookup_reports_the_log(self):
        client = mock.MagicMock()
        client.get_instance.side_effect = \
            sf_instance.apiclient.APIException('boom')

        msg, log = self._msg_and_log(self._run(client))
        self.assertEqual('Fetching the existing instance failed: boom', msg)
        self.assertIn('Will use identifier test', log)

    def test_a_failed_post_create_fetch_reports_the_log(self):
        client = mock.MagicMock()
        client.get_instance.side_effect = [
            sf_instance.apiclient.ResourceNotFoundException(),
            sf_instance.apiclient.APIException('boom')]
        client.create_instance.return_value = {
            'uuid': 'notreallyauuid', 'state': 'created'}

        msg, log = self._msg_and_log(self._run(client))
        self.assertEqual('Fetching the created instance failed: boom', msg)
        self.assertIn('Not awaiting instance', log)

    def test_an_absent_delete_failure_reports_the_log(self):
        client = mock.MagicMock()
        client.get_instance.side_effect = [self.EXISTING_DIRTY]
        client.delete_instance.side_effect = \
            sf_instance.apiclient.APIException('boom')

        msg, log = self._msg_and_log(self._run(client, state='absent'))
        self.assertEqual('Instance deletion failed: boom', msg)
        self.assertIn('Attempt deletion...', log)

    def test_an_absent_lookup_failure_reports_the_log(self):
        client = mock.MagicMock()
        client.get_instance.side_effect = \
            sf_instance.apiclient.APIException('boom')

        msg, _ = self._msg_and_log(self._run(client, state='absent'))
        self.assertEqual('Fetching the existing instance failed: boom', msg)

    def test_not_found_is_still_not_an_api_failure(self):
        # ResourceNotFoundException subclasses APIException in the real
        # client, so clause ordering matters: not-found must keep taking
        # its specific handler (a clean "nothing to delete") rather than
        # failing the task through the new generic guard.
        client = mock.MagicMock()
        client.get_instance.side_effect = \
            sf_instance.apiclient.ResourceNotFoundException()

        module = self._run(client, state='absent', expect_failure=False)
        module.fail_json.assert_not_called()
        self.assertFalse(module.exit_json.call_args[1]['changed'])


class SfInstanceCreateTimeoutDetectionTestCase(base.ShakenFistTestCase):
    """Feature detection, rather than a version test.

    The collection requires shakenfist-client unpinned, so the control
    node's client is whatever pip resolved. There is no version to test
    against which is not itself a guess about backports.
    """

    def test_a_new_client_is_detected(self):
        client = mock.MagicMock()
        client.create_instance = mock.create_autospec(
            lambda *args, timeout=None, **kwargs: None)
        self.assertTrue(sf_instance._create_accepts_timeout(client))

    def test_an_old_client_is_detected(self):
        client = mock.MagicMock()
        client.create_instance = mock.create_autospec(
            lambda *args, **kwargs: None)
        self.assertFalse(sf_instance._create_accepts_timeout(client))

    def test_a_callable_inspect_cannot_describe_is_assumed_old(self):
        # Some C implementations have no signature to read. Guessing
        # "new" there is a TypeError which stops every instance creation
        # in the fleet, so the unreadable case has to fall back.
        client = mock.MagicMock()
        client.create_instance = dict.update
        self.assertFalse(sf_instance._create_accepts_timeout(client))

    def test_a_bare_mock_is_assumed_old(self):
        # A MagicMock does not raise from inspect.signature() -- it
        # reports (*args, **kwargs), which has no timeout parameter.
        self.assertFalse(sf_instance._create_accepts_timeout(mock.MagicMock()))


# The body the server sends with a 507 a wait can clear (capacity_error()
# in shakenfist/external_api/base.py).
TRANSIENT_BODY = {
    'error': 'No nodes remaining at scheduling stage sufficient_idle_cpu',
    'status': 507,
    'stage': 'sufficient_idle_cpu',
    'transient': True,
}


def _refusal(body, exception='InsufficientResourcesException'):
    """A refusal as the client raises it, with body as the response text.

    The real APIException takes (message, method, url, status_code, text)
    and stores text as an attribute; the stub's exceptions are bare, so
    the attribute is set by hand. A dict body is serialised as the server
    would; anything else is used as the text unchanged.
    """
    text = json.dumps(body) if isinstance(body, dict) else body
    e = getattr(sf_instance.apiclient, exception)(
        'API request failed', 'POST', 'http://localhost:13000/instances',
        507, text)
    e.text = text
    return e


class _FakeClock:
    """A monotonic clock which moves only when the code under test sleeps."""

    def __init__(self):
        self.now = 0
        self.reads = 0
        self.sleeps = []

    def monotonic(self):
        self.reads += 1
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class SfInstanceTransientRefusalTestCase(base.ShakenFistTestCase):
    """Only a 507 the server marked transient is worth waiting out.

    The same four bodies the functional suite's wrapper is tested on
    (shakenfist/shakenfist#4474): the two copies of this rule must agree.
    """

    def test_transient_true_is_transient(self):
        self.assertTrue(sf_instance._transient_refusal(_refusal(TRANSIENT_BODY)))

    def test_transient_false_is_not_transient(self):
        body = dict(TRANSIENT_BODY, transient=False)
        self.assertFalse(sf_instance._transient_refusal(_refusal(body)))

    def test_no_transient_field_is_not_transient(self):
        # An address pool which is full answers 507 with only error and
        # status, and so does a server which predates the marker.
        body = {'error': 'network is congested', 'status': 507}
        self.assertFalse(sf_instance._transient_refusal(_refusal(body)))

    def test_a_non_json_body_is_not_transient(self):
        self.assertFalse(sf_instance._transient_refusal(
            _refusal('<html>507 Insufficient Storage</html>')))

    def test_a_truthy_transient_which_is_not_true_is_not_transient(self):
        # Exactly True, as the server writes it, rather than anything a
        # proxy or a future server might send that happens to be truthy.
        body = dict(TRANSIENT_BODY, transient='yes')
        self.assertFalse(sf_instance._transient_refusal(_refusal(body)))

    def test_a_json_body_which_is_not_an_object_is_not_transient(self):
        self.assertFalse(sf_instance._transient_refusal(_refusal('[true]')))

    def test_another_status_is_never_transient(self):
        # A 409 carrying the same body is still not a capacity refusal.
        e = _refusal(TRANSIENT_BODY, exception='ResourceStateConflictException')
        self.assertFalse(sf_instance._transient_refusal(e))

    def test_an_exception_without_text_is_not_transient(self):
        self.assertFalse(sf_instance._transient_refusal(
            sf_instance.apiclient.InsufficientResourcesException()))


class SfInstancePreflightCapacityAbortTestCase(base.ShakenFistTestCase):
    """The two exact preflight abort messages which mean "no room".

    The strings are named here on purpose: a server change which rewords
    either should fail this test, not quietly stop the wait engaging.
    """

    def test_both_capacity_messages_in_both_states_match(self):
        for message in ('Unable to find suitable node', 'Too many start attempts'):
            for state in ('preflight-error', 'error'):
                self.assertTrue(
                    sf_instance._preflight_capacity_abort(
                        {'state': state, 'error_message': message}),
                    '%s in %s should match' % (message, state))

    def test_other_preflight_aborts_do_not_match(self):
        for message in (
                'No node satisfies the requested affinity constraints: x',
                'Too many start attempts, and no node satisfies the '
                'requested affinity constraints: x',
                'Requested node lacks resources',
                'Requested node is not currently an active node',
                'scheduling failed', None):
            self.assertFalse(
                sf_instance._preflight_capacity_abort(
                    {'state': 'error', 'error_message': message}),
                '%s should not match' % message)

    def test_a_healthy_or_missing_instance_does_not_match(self):
        self.assertFalse(sf_instance._preflight_capacity_abort(
            {'state': 'created',
             'error_message': 'Unable to find suitable node'}))
        self.assertFalse(sf_instance._preflight_capacity_abort(None))
        self.assertFalse(sf_instance._preflight_capacity_abort({}))


class SfInstanceAwaitRecordGoneTestCase(base.ShakenFistTestCase):
    """_await_record_gone() treats only not found and deleted as gone."""

    def _run(self, lookups, deadline=600):
        clock = _FakeClock()
        log = []
        client = mock.MagicMock()
        client.get_instance.side_effect = lookups
        with mock.patch.object(
                sf_instance.time, 'monotonic', side_effect=clock.monotonic), \
                mock.patch.object(sf_instance, '_sleep', clock.sleep):
            gone = sf_instance._await_record_gone(
                client, log, 'test', 'ns', deadline)
        return gone, client, clock

    def test_not_found_is_gone(self):
        gone, client, _ = self._run(
            sf_instance.apiclient.ResourceNotFoundException())
        self.assertTrue(gone)
        client.delete_instance.assert_not_called()

    def test_an_error_record_is_deleted_by_uuid_and_followed_to_deleted(self):
        gone, client, _ = self._run([
            {'uuid': 'refused', 'state': 'error'},
            {'uuid': 'refused', 'state': 'error'},
            {'uuid': 'refused', 'state': 'deleted'}])
        self.assertTrue(gone)
        client.delete_instance.assert_called_once_with(
            'refused', async_request=True)
        self.assertEqual(
            [mock.call('test', namespace='ns'), mock.call('refused'),
             mock.call('refused')],
            client.get_instance.call_args_list)

    def test_a_transitional_error_record_is_not_deleted_until_it_settles(self):
        # A delete request aborts the instance's outstanding operations,
        # so sent while the server's own delete is queued it would cancel
        # that delete and itself end in state error.
        gone, client, _ = self._run([
            {'uuid': 'refused', 'state': 'initial-error'},
            {'uuid': 'refused', 'state': 'initial-error'},
            {'uuid': 'refused', 'state': 'error'},
            {'uuid': 'refused', 'state': 'deleted'}])
        self.assertTrue(gone)
        self.assertEqual(1, client.delete_instance.call_count)
        # Three lookups had happened when the delete was sent.
        self.assertEqual(4, client.get_instance.call_count)

    def test_a_healthy_instance_of_the_name_is_never_deleted_or_gone(self):
        # Some other instance holds the name. Replaying would create a
        # second, so the wait runs out instead -- and it must not delete
        # an instance it did not create.
        gone, client, clock = self._run(
            itertools.repeat({'uuid': 'other', 'state': 'created'}),
            deadline=60)
        self.assertFalse(gone)
        client.delete_instance.assert_not_called()
        self.assertEqual(60, clock.now)

    def test_a_record_which_never_goes_is_not_gone_at_the_deadline(self):
        gone, _, clock = self._run(
            itertools.repeat({'uuid': 'refused', 'state': 'error'}),
            deadline=60)
        self.assertFalse(gone)
        self.assertEqual(60, clock.now)
        # Polled every five seconds, not spun.
        self.assertEqual(12, len(clock.sleeps))


class SfInstanceCapacityWaitTestCase(base.ShakenFistTestCase):
    """capacity_wait replays a create refused for capacity, and only that.

    Phase 0 of the placeable claims plan found that every in-job capacity
    refusal failed its build outright, although most would have been
    placed within half an hour. The module now waits, when asked to, for a
    507 the server marked transient and for a preflight abort for capacity
    -- and with capacity_wait at its default of 0 behaves exactly as it
    did before the option existed.
    """

    PARAMS = dict(SfInstanceCreateBudgetTestCase.PARAMS)
    WHEN = datetime.datetime(2026, 10, 18, tzinfo=datetime.timezone.utc)

    # The refused record a 507 leaves behind, as a lookup by name finds it
    # once the server's own delete has parked it in state error.
    REFUSED = {'uuid': 'refused', 'name': 'test', 'state': 'error'}
    DELETED = {'uuid': 'refused', 'name': 'test', 'state': 'deleted'}

    def _created(self, uuid='created'):
        return {'uuid': uuid, 'name': 'test', 'state': 'created'}

    def _run(self, creates, lookups=None, capacity_wait=1800,
             await_instance=False, awaits=None, instances=None, jitter=0,
             create_seconds=0, expect_failure=False):
        """Run the module against a scripted client and a fake clock.

        creates and awaits are the outcomes of successive
        create_instance() and await_instance_create() calls, an exception
        to raise or a value to return. lookups maps an instance reference
        to the outcomes of successive get_instance() calls for it; the
        last repeats. Every client call is recorded, in order, in
        self.calls, with the fake clock's time for creates.
        """
        params = dict(self.PARAMS)
        params['await'] = await_instance
        params['await_timeout'] = 600
        params['capacity_wait'] = capacity_wait

        module = mock.MagicMock()
        module.params = params
        module.check_mode = False
        module.exit_json.side_effect = _Succeeded
        module.fail_json.side_effect = _Failed

        clock = _FakeClock()
        calls = []
        creates = list(creates)
        awaits = list(awaits or [])
        lookups = {ref: list(outcomes)
                   for ref, outcomes in (lookups or {}).items()}
        lookups.setdefault(
            'test', [sf_instance.apiclient.ResourceNotFoundException()])
        lookups.setdefault('created', [self._created()])

        def outcome(value):
            if isinstance(value, Exception):
                raise value
            return value

        # A real function, so that _create_accepts_timeout() sees the
        # timeout argument and an awaited create is passed timeout=0.
        def create_instance(*args, timeout=None, **kwargs):
            calls.append(('create', clock.now, timeout))
            clock.now += create_seconds
            return outcome(creates.pop(0))

        def get_instance(ref, namespace=None):
            calls.append(('get', ref, namespace))
            responses = lookups[ref]
            return outcome(
                responses.pop(0) if len(responses) > 1 else responses[0])

        def await_instance_create(uuid, timeout=None):
            calls.append(('await', uuid, timeout))
            return outcome(awaits.pop(0) if awaits else None)

        def get_instances(*args, **kwargs):
            calls.append(('get_instances', ))
            return instances or []

        def delete_instance(ref, **kwargs):
            calls.append(('delete', ref, kwargs))

        client = mock.MagicMock()
        client.create_instance = mock.create_autospec(
            create_instance, side_effect=create_instance)
        client.get_instance.side_effect = get_instance
        client.await_instance_create.side_effect = await_instance_create
        client.get_instances.side_effect = get_instances
        client.delete_instance.side_effect = delete_instance

        self.jitter = mock.MagicMock(return_value=jitter)
        with mock.patch.object(
                sf_instance, 'AnsibleModule', return_value=module), \
                mock.patch.object(
                    sf_instance, 'make_client', return_value=client), \
                mock.patch.object(
                    sf_instance.time, 'monotonic',
                    side_effect=clock.monotonic), \
                mock.patch.object(sf_instance, '_sleep', clock.sleep), \
                mock.patch.object(sf_instance, '_jitter', self.jitter), \
                mock.patch.object(
                    sf_instance, '_utcnow', return_value=self.WHEN):
            self.assertRaises(
                _Failed if expect_failure else _Succeeded,
                sf_instance.run_module)

        self.module = module
        self.clock = clock
        self.calls = calls
        return module

    def _calls(self, kind):
        return [c for c in self.calls if c[0] == kind]

    def _waited(self):
        if self.module.exit_json.called:
            return self.module.exit_json.call_args[1]['capacity_waited']
        return self.module.fail_json.call_args[1]['capacity_waited']

    def _fails_at_once(self, refusal):
        module = self._run([refusal], expect_failure=True)
        kwargs = module.fail_json.call_args[1]
        self.assertEqual('Instance creation failed: %s' % refusal,
                         kwargs['msg'])
        self.assertEqual(1, len(self._calls('create')))
        self.assertEqual([], self._calls('get_instances'))
        self.assertEqual([], self._calls('delete'))
        self.assertNotIn('capacity_waited', kwargs)

    def test_capacity_wait_zero_fails_a_transient_507_at_once_as_before(self):
        refusal = _refusal(TRANSIENT_BODY)
        module = self._run([refusal], capacity_wait=0, expect_failure=True)

        # The same call, with the same arguments, as before the option
        # existed -- and nothing else asked of the server or the clock.
        module.fail_json.assert_called_once_with(
            msg='Instance creation failed: %s' % refusal, meta=None,
            log=mock.ANY)
        self.assertEqual(
            [('get', 'test', 'ns'), ('create', 0, None)], self.calls)
        self.assertEqual(1, self.clock.reads)
        self.assertEqual([], self.clock.sleeps)
        self.jitter.assert_not_called()

    def test_capacity_wait_zero_does_not_read_back_a_preflight_abort(self):
        failure = Exception('failed to start (marked as error state, ...)')
        module = self._run(
            [{'uuid': 'aborted', 'state': 'initial'}], capacity_wait=0,
            await_instance=True, awaits=[failure], expect_failure=True)

        module.fail_json.assert_called_once_with(
            msg='Waiting for instance failed: %s' % failure, meta=None,
            log=mock.ANY)
        self.assertEqual(
            [('get', 'test', 'ns'), ('create', 0, 0),
             ('await', 'aborted', 600)], self.calls)
        self.assertEqual(2, self.clock.reads)

    def test_a_transient_507_then_success_replays_once_and_reports_placed(self):
        self._run(
            [_refusal(TRANSIENT_BODY), self._created()],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            },
            instances=[
                {'name': 'test', 'namespace': 'ns'},
                {'name': 'sf1', 'namespace': 'ns'},
                {'name': 'sf2', 'namespace': 'ns'},
                {'name': 'elsewhere', 'namespace': 'other'},
            ])

        self.assertEqual(2, len(self._calls('create')))
        self.assertTrue(self.module.exit_json.call_args[1]['changed'])
        self.assertEqual({
            'seconds': 60,
            'attempts': 2,
            'outcome': 'placed',
            'refusals': [{
                'at': '2026-10-18T00:00:00+00:00',
                'kind': 'api',
                'stage': 'sufficient_idle_cpu',
                'message': TRANSIENT_BODY['error'],
            }],
            # The refused record, and the instance in another namespace,
            # are not the build's.
            'namespace_instances': 2,
        }, self._waited())

    def test_an_unmarked_507_fails_at_once(self):
        self._fails_at_once(
            _refusal({'error': 'network is congested', 'status': 507}))

    def test_a_transient_false_507_fails_at_once(self):
        self._fails_at_once(_refusal(dict(TRANSIENT_BODY, transient=False)))

    def test_a_non_json_507_fails_at_once(self):
        self._fails_at_once(_refusal('<html>507 Insufficient Storage</html>'))

    def test_a_409_fails_at_once(self):
        self._fails_at_once(
            _refusal(TRANSIENT_BODY,
                     exception='ResourceStateConflictException'))

    def test_replays_are_at_least_60_seconds_apart(self):
        refusal = _refusal(TRANSIENT_BODY)
        self._run(
            [refusal, refusal, refusal, self._created()],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            },
            jitter=7)

        times = [c[1] for c in self._calls('create')]
        self.assertEqual([0, 67, 134, 201], times)
        for earlier, later in zip(times, times[1:]):
            self.assertGreaterEqual(later - earlier, 60)
        self.jitter.assert_called_with(0, 15)
        self.assertEqual(3, self.jitter.call_count)

    def test_the_namespace_is_counted_once_at_the_first_refusal(self):
        refusal = _refusal(TRANSIENT_BODY)
        self._run(
            [refusal, refusal, self._created()],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            })
        self.assertEqual(1, len(self._calls('get_instances')))

    def test_the_refused_record_is_awaited_by_name_before_the_replay(self):
        self._run(
            [_refusal(TRANSIENT_BODY), self._created()],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         {'uuid': 'refused', 'state': 'initial-error'}],
                'refused': [{'uuid': 'refused', 'state': 'error'},
                            self.DELETED],
            })

        # A 507 names no uuid, so the record is found by name, followed by
        # the uuid that lookup returned, acknowledged with a delete once
        # the server's own delete has parked it in state error, and gone
        # before the replay is sent.
        self.assertEqual([
            ('get', 'test', 'ns'),
            ('create', 0, None),
            ('get_instances', ),
            ('get', 'test', 'ns'),
            ('get', 'refused', None),
            ('delete', 'refused', {'async_request': True}),
            ('get', 'refused', None),
            ('create', 60, None),
            ('get', 'created', None),
        ], self.calls)

    def test_a_preflight_abort_record_is_awaited_by_uuid_before_the_replay(self):
        aborted = {'uuid': 'aborted', 'name': 'test', 'state': 'error',
                   'error_message': 'Unable to find suitable node'}
        self._run(
            [{'uuid': 'aborted', 'state': 'initial'},
             {'uuid': 'created', 'state': 'initial'}],
            lookups={'aborted': [aborted, aborted, dict(aborted, state='deleted')]},
            await_instance=True,
            awaits=[Exception('failed to start (marked as error state, ...)')])

        self.assertEqual([
            ('get', 'test', 'ns'),
            ('create', 0, 0),
            ('await', 'aborted', 600),
            # Read back to tell a capacity abort from any other error.
            ('get', 'aborted', None),
            ('get_instances', ),
            # Then the record, by its uuid -- never by name.
            ('get', 'aborted', None),
            ('delete', 'aborted', {'async_request': True}),
            ('get', 'aborted', None),
            ('create', 60, 0),
            ('await', 'created', 600),
            ('get', 'created', None),
        ], self.calls)

    def test_a_preflight_unable_to_find_suitable_node_is_replayed(self):
        aborted = {'uuid': 'aborted', 'name': 'test',
                   'state': 'preflight-error',
                   'error_message': 'Unable to find suitable node'}
        self._run(
            [{'uuid': 'aborted', 'state': 'initial'},
             {'uuid': 'created', 'state': 'initial'}],
            lookups={'aborted': [aborted, dict(aborted, state='error'),
                                 dict(aborted, state='deleted')]},
            await_instance=True,
            awaits=[Exception('failed to start (marked as error state, ...)')])

        self.assertEqual(2, len(self._calls('create')))
        waited = self._waited()
        self.assertEqual('placed', waited['outcome'])
        self.assertEqual(
            [{'at': '2026-10-18T00:00:00+00:00', 'kind': 'preflight',
              'stage': None, 'message': 'Unable to find suitable node'}],
            waited['refusals'])

    def test_a_preflight_affinity_abort_is_not_replayed(self):
        aborted = {'uuid': 'aborted', 'name': 'test', 'state': 'error',
                   'error_message': 'No node satisfies the requested '
                                    'affinity constraints: x'}
        failure = Exception('failed to start (marked as error state, ...)')
        module = self._run(
            [{'uuid': 'aborted', 'state': 'initial'}],
            lookups={'aborted': [aborted]},
            await_instance=True, awaits=[failure], expect_failure=True)

        kwargs = module.fail_json.call_args[1]
        self.assertEqual('Waiting for instance failed: %s' % failure,
                         kwargs['msg'])
        self.assertNotIn('capacity_waited', kwargs)
        self.assertEqual(1, len(self._calls('create')))
        self.assertEqual([], self._calls('delete'))

    def test_an_exhausted_wait_message_starts_with_the_server_text(self):
        refusal = _refusal(TRANSIENT_BODY)
        module = self._run(
            [refusal] * 20, capacity_wait=600,
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            },
            expect_failure=True)

        # What the module said before the wait existed, unchanged, and
        # then what the wait did. kerbside-patches' automatic retry of
        # infrastructure failures matches the server's text in job logs.
        msg = module.fail_json.call_args[1]['msg']
        self.assertTrue(
            msg.startswith('Instance creation failed: %s' % refusal), msg)
        self.assertIn('No nodes remaining at scheduling stage', msg)
        self.assertTrue(
            msg.endswith('(waited 600 seconds for capacity over 11 attempts: '
                         'the next replay would have been sent after the '
                         'capacity_wait budget of 600 seconds ran out)'),
            msg)

        # No replay is sent after the budget: one every 60 seconds from 0
        # up to and including 600, and no twelfth at 660.
        times = [c[1] for c in self._calls('create')]
        self.assertEqual(list(range(0, 601, 60)), times)

        waited = self._waited()
        self.assertEqual('exhausted', waited['outcome'])
        self.assertEqual(11, waited['attempts'])
        self.assertEqual(600, waited['seconds'])
        self.assertEqual(11, len(waited['refusals']))

    def test_an_exhausted_preflight_wait_reports_the_abort_message(self):
        aborted = {'uuid': 'aborted', 'name': 'test', 'state': 'error',
                   'error_message': 'Too many start attempts'}
        module = self._run(
            [{'uuid': 'aborted', 'state': 'initial'}], capacity_wait=30,
            lookups={'aborted': [aborted]},
            await_instance=True,
            awaits=[Exception('failed to start (marked as error state, ...)')],
            expect_failure=True)

        # One attempt, in the singular, and the reason no replay was sent
        # in the message itself rather than only in the log.
        self.assertEqual(
            'Waiting for instance failed: Too many start attempts (waited 0 '
            'seconds for capacity over 1 attempt: the next replay would have '
            'been sent after the capacity_wait budget of 30 seconds ran out)',
            module.fail_json.call_args[1]['msg'])

    def test_a_budget_shorter_than_the_spacing_never_replays(self):
        module = self._run(
            [_refusal(TRANSIENT_BODY)], capacity_wait=59,
            expect_failure=True)
        self.assertEqual(1, len(self._calls('create')))
        self.assertEqual([], self._calls('delete'))
        self.assertEqual('exhausted', self._waited()['outcome'])
        self.assertIn('the next replay would have been sent after',
                      ' '.join(module.fail_json.call_args[1]['log']))

    def test_a_record_which_never_goes_exhausts_the_wait(self):
        module = self._run(
            [_refusal(TRANSIENT_BODY)], capacity_wait=600,
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         {'uuid': 'refused', 'state': 'initial-error'}],
                'refused': [{'uuid': 'refused', 'state': 'initial-error'}],
            },
            expect_failure=True)

        # The name is never reused while the record still holds it.
        self.assertEqual(1, len(self._calls('create')))
        self.assertEqual(600, self._waited()['seconds'])
        self.assertIn('still held its name',
                      ' '.join(module.fail_json.call_args[1]['log']))

    def test_a_failed_record_lookup_is_not_replayed(self):
        # Only the create's own refusal is replayed: a failure of any
        # other call fails the task, as it always did.
        module = self._run(
            [_refusal(TRANSIENT_BODY)],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         {'uuid': 'refused', 'state': 'initial-error'}],
                'refused': [sf_instance.apiclient.APIException('boom')],
            },
            expect_failure=True)

        self.assertEqual(
            'Waiting for the refused instance to be deleted failed: boom',
            module.fail_json.call_args[1]['msg'])
        self.assertEqual(1, len(self._calls('create')))

        # What the wait did is still returned, and the five seconds it
        # spent polling the record before the lookup failed are counted.
        waited = self._waited()
        self.assertEqual('failed', waited['outcome'])
        self.assertEqual(5, waited['seconds'])
        self.assertEqual(1, waited['attempts'])
        self.assertEqual(1, len(waited['refusals']))

    def test_a_replay_refused_for_another_reason_returns_the_wait(self):
        unmarked = _refusal({'error': 'network is congested', 'status': 507})
        module = self._run(
            [_refusal(TRANSIENT_BODY), unmarked],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            },
            expect_failure=True)

        # The failure reads as it would have without a wait, and what the
        # wait did before it comes with it.
        self.assertEqual('Instance creation failed: %s' % unmarked,
                         module.fail_json.call_args[1]['msg'])
        self.assertEqual({
            'seconds': 60,
            'attempts': 2,
            'outcome': 'failed',
            'refusals': [{
                'at': '2026-10-18T00:00:00+00:00',
                'kind': 'api',
                'stage': 'sufficient_idle_cpu',
                'message': TRANSIENT_BODY['error'],
            }],
            'namespace_instances': 0,
        }, self._waited())

    def test_a_placed_replay_whose_read_back_fails_returns_the_wait(self):
        module = self._run(
            [_refusal(TRANSIENT_BODY), self._created()],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
                'created': [sf_instance.apiclient.APIException('boom')],
            },
            expect_failure=True)

        self.assertEqual('Fetching the created instance failed: boom',
                         module.fail_json.call_args[1]['msg'])
        self.assertEqual('failed', self._waited()['outcome'])
        self.assertEqual(60, self._waited()['seconds'])

    def test_a_replay_whose_await_fails_otherwise_returns_the_wait(self):
        aborted = {'uuid': 'aborted', 'name': 'test', 'state': 'error',
                   'error_message': 'Unable to find suitable node'}
        affinity = {'uuid': 'created', 'name': 'test', 'state': 'error',
                    'error_message': 'No node satisfies the requested '
                                     'affinity constraints: x'}
        failure = Exception('failed to start (marked as error state, ...)')
        module = self._run(
            [{'uuid': 'aborted', 'state': 'initial'},
             {'uuid': 'created', 'state': 'initial'}],
            lookups={'aborted': [aborted, aborted,
                                 dict(aborted, state='deleted')],
                     'created': [affinity]},
            await_instance=True, awaits=[failure, failure],
            expect_failure=True)

        self.assertEqual('Waiting for instance failed: %s' % failure,
                         module.fail_json.call_args[1]['msg'])
        waited = self._waited()
        self.assertEqual('failed', waited['outcome'])
        self.assertEqual(2, waited['attempts'])
        self.assertEqual(60, waited['seconds'])
        self.assertEqual(['preflight'],
                         [r['kind'] for r in waited['refusals']])

    def test_a_replay_left_no_await_budget_returns_the_wait(self):
        # Each create takes the whole await_timeout. The refused one's
        # time is the capacity wait's, but the accepted one's leaves the
        # await nothing, which fails on the zero budget branch. The
        # replay is long past its spacing, so it goes as soon as the
        # refused record's deletion (five seconds) has been seen.
        failure = Exception('timed out')
        module = self._run(
            [_refusal(TRANSIENT_BODY), {'uuid': 'created', 'state': 'initial'}],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            },
            await_instance=True, awaits=[failure], create_seconds=600,
            expect_failure=True)

        self.assertEqual([('await', 'created', 0)], self._calls('await'))
        msg = module.fail_json.call_args[1]['msg']
        self.assertTrue(msg.startswith(
            'The entire await_timeout budget of 600 seconds was consumed'),
            msg)
        waited = self._waited()
        self.assertEqual('failed', waited['outcome'])
        self.assertEqual(2, waited['attempts'])
        self.assertEqual(605, waited['seconds'])

    def test_failure_changes_nothing(self):
        # failure() only describes a failure; it must not make a later
        # report() on the success path say the task failed.
        capacity = sf_instance._CapacityWait(
            mock.MagicMock(), mock.MagicMock(), [], 1800, 'test', None)
        capacity.refusals.append({'kind': 'api'})
        self.assertEqual('failed',
                         capacity.failure()['capacity_waited']['outcome'])
        self.assertEqual('placed', capacity.report()['outcome'])

    def test_the_final_await_gets_the_full_await_timeout(self):
        # Each create takes five seconds. The refused attempt and the
        # wait after it come out of the capacity budget, not the await's;
        # only the accepted create's own five seconds are charged to
        # await_timeout, as they would have been without a refusal.
        self._run(
            [_refusal(TRANSIENT_BODY), {'uuid': 'created', 'state': 'initial'}],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            },
            await_instance=True, create_seconds=5)

        self.assertEqual([('await', 'created', 595)], self._calls('await'))
        self.assertEqual(60, self._waited()['seconds'])

    def test_every_replay_is_told_not_to_wait(self):
        refusal = _refusal(TRANSIENT_BODY)
        self._run(
            [refusal, refusal, {'uuid': 'created', 'state': 'initial'}],
            lookups={
                'test': [sf_instance.apiclient.ResourceNotFoundException(),
                         self.REFUSED],
                'refused': [self.DELETED],
            },
            await_instance=True)
        self.assertEqual([0, 0, 0], [c[2] for c in self._calls('create')])

    def test_capacity_waited_is_returned_on_success_as_not_refused(self):
        self._run([self._created()])
        self.assertEqual({
            'seconds': 0,
            'attempts': 1,
            'outcome': 'not_refused',
            'refusals': [],
            'namespace_instances': None,
        }, self._waited())
        self.assertEqual([], self._calls('get_instances'))
