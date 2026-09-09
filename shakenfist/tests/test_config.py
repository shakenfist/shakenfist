import json
import os
from unittest import mock

from pydantic import SecretStr

from shakenfist.config import SECRET_CONFIG_KEY_RE
from shakenfist.config import SFConfig
from shakenfist.config import UNCONFIGURED_AUTH_SECRET_SEED
from shakenfist.config import _exportable_cluster_config_key
from shakenfist.config import load_cluster_config
from shakenfist.config import verify_config
from shakenfist.tests import base
from shakenfist.util import vdi_tokens


class ConfigTestCase(base.ShakenFistTestCase):
    @mock.patch('socket.getfqdn', return_value='a.b.com')
    def test_hostname(self, mock_fqdn):
        conf = SFConfig()
        mock_fqdn.assert_called()
        self.assertEqual('a.b.com', str(conf.NODE_NAME))

    @mock.patch.dict('os.environ', {'SHAKENFIST_STORAGE_PATH': 'foo'})
    def test_string_override(self):
        conf = SFConfig()
        self.assertTrue(isinstance(conf.STORAGE_PATH, str))
        self.assertEqual('foo', conf.STORAGE_PATH)

    @mock.patch.dict('os.environ', {'SHAKENFIST_CPU_OVERCOMMIT_RATIO': '1'})
    def test_int_override(self):
        conf = SFConfig()
        self.assertTrue(isinstance(conf.CPU_OVERCOMMIT_RATIO, float))
        self.assertEqual(1, conf.CPU_OVERCOMMIT_RATIO)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_NODE_RAM_RESERVATION_GB': '4.0'})
    def test_float_override(self):
        conf = SFConfig()
        self.assertTrue(isinstance(conf.NODE_RAM_RESERVATION_GB, float))
        self.assertEqual(4.0, conf.NODE_RAM_RESERVATION_GB)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_NODE_RAM_RESERVATION_GB': 'banana'})
    def test_bogus_override(self):
        self.assertRaises(ValueError, SFConfig)

    @mock.patch.dict('os.environ')
    def test_validation_mode_defaults_to_enforce(self):
        # Decision D16 of PLAN-api-input-validation: enforcement is the
        # default rather than an operator opt-in, because leaving it at
        # 'warn' would mean the defect class stays open in every
        # deployment while the machinery to close it sits unused.
        # Asserted here rather than only in the plan, so a well meant
        # revert to the safer looking value has to argue with a test.
        #
        # The override is removed first: this asserts the default, and
        # a developer who happens to run the suite with the setting
        # exported would otherwise be testing their shell.
        os.environ.pop('SHAKENFIST_API_VALIDATION_MODE', None)

        self.assertEqual('enforce', SFConfig().API_VALIDATION_MODE)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_API_VALIDATION_MODE': 'enforced'})
    def test_bogus_validation_mode_fails_at_load(self):
        # A typo silently meaning something other than what the
        # operator wrote is no signal anything is wrong, so anything
        # other than the three literals must refuse to load. This is
        # the rollback path's safety net: an operator reaching for
        # 'warn' in an incident must not get enforcement because they
        # typed 'Warn'.
        self.assertRaises(ValueError, SFConfig)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_API_VALIDATION_MODE': 'warn'})
    def test_valid_validation_mode_loads(self):
        # 'warn' rather than 'enforce', so this cannot pass by
        # accidentally reading the default back.
        self.assertEqual('warn', SFConfig().API_VALIDATION_MODE)


class AgentOperationAttemptCapTestCase(base.ShakenFistTestCase):
    """AGENT_OPERATION_MAX_ATTEMPTS below 1 must refuse to load.

    Zero makes "attempts >= cap" true on the very first check, which
    disables retry entirely while reporting "after 1 attempts" as the
    expiry reason -- indistinguishable in the logs from a working
    configuration.
    """

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_AGENT_OPERATION_MAX_ATTEMPTS': '0'})
    def test_zero_attempts_is_refused(self):
        self.assertRaises(ValueError, SFConfig)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_AGENT_OPERATION_MAX_ATTEMPTS': '-1'})
    def test_a_negative_cap_is_refused(self):
        self.assertRaises(ValueError, SFConfig)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_AGENT_OPERATION_MAX_ATTEMPTS': '1'})
    def test_one_attempt_loads(self):
        # One attempt and no retries is a legitimate choice, and is the
        # smallest value which still dispatches the operation.
        self.assertEqual(1, SFConfig().AGENT_OPERATION_MAX_ATTEMPTS)


class SecretConfigFieldTestCase(base.ShakenFistTestCase):
    """The three configuration values which carry credentials.

    Two of them -- AUTH_SECRET_SEED and MARIADB_PASSWORD -- were logged
    verbatim by the sf-queues startup banner and shipped to Loki for as
    long as they were plain strings. SecretStr is the half of that fix
    which travels with the value instead of living at one log site.
    """

    SECRET_FIELDS = ['AUTH_SECRET_SEED', 'MARIADB_PASSWORD',
                     'LOKI_AUTH_HEADER']

    def test_secret_fields_are_secretstr(self):
        conf = SFConfig()
        for name in self.SECRET_FIELDS:
            self.assertIsInstance(getattr(conf, name), SecretStr, name)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_AUTH_SECRET_SEED': 'a-real-seed',
                      'SHAKENFIST_MARIADB_PASSWORD': 'a-real-password'})
    def test_secret_values_do_not_render(self):
        conf = SFConfig()

        # The value survives, reachable only through get_secret_value().
        self.assertEqual(
            'a-real-seed', conf.AUTH_SECRET_SEED.get_secret_value())
        self.assertEqual(
            'a-real-password', conf.MARIADB_PASSWORD.get_secret_value())

        # None of the ways a value ordinarily reaches a log line produce
        # it. The f-string is the one which actually leaked.
        for field in [conf.AUTH_SECRET_SEED, conf.MARIADB_PASSWORD]:
            secret = field.get_secret_value()
            self.assertNotIn(secret, str(field))
            self.assertNotIn(secret, repr(field))
            self.assertNotIn(secret, f'{field}')
            self.assertNotIn(secret, '%s' % field)

        # Nor through a whole-model dump, which is how both the startup
        # banner and _config_failure() reach every field at once.
        self.assertNotIn('a-real-seed', str(conf.model_dump()))
        self.assertNotIn('a-real-password', str(conf.model_dump()))

    @mock.patch.dict('os.environ', {'SHAKENFIST_LOKI_AUTH_HEADER': ''})
    def test_empty_secret_is_falsey(self):
        # logship_drainer tests emptiness to decide whether to send the
        # header at all. SecretStr implements __len__ so that keeps
        # working -- but if it ever stopped, an unconfigured cluster
        # would start sending a masked Authorization header on every
        # push.
        conf = SFConfig()
        self.assertFalse(conf.LOKI_AUTH_HEADER)

    @mock.patch.dict(
        'os.environ',
        {'SHAKENFIST_AUTH_SECRET_SEED': UNCONFIGURED_AUTH_SECRET_SEED})
    def test_unconfigured_seed_is_still_refused(self):
        # The environment is pinned rather than inherited. SFConfig
        # reads SHAKENFIST_* from os.environ, and importing
        # shakenfist.config runs load_cluster_config(), which pushes
        # every cluster_config row into the environment on any host
        # which can reach a database tier. On such a host the seed is
        # already configured and this test -- the most important one in
        # the phase -- would fail for a reason that has nothing to do
        # with the code under test.
        #
        # The regression this phase was most likely to introduce.
        # SecretStr('x') == 'x' is False, so comparing the field against
        # the sentinel directly makes this check unsatisfiable, and a
        # cluster would sign every token in its zone with the value
        # shipped in config.py.
        conf = SFConfig()
        self.assertEqual(
            UNCONFIGURED_AUTH_SECRET_SEED,
            conf.AUTH_SECRET_SEED.get_secret_value())

        with mock.patch('shakenfist.config.config', conf):
            self.assertRaises(SystemExit, verify_config)

            # ...and it is the seed being complained about, rather than
            # some unrelated validation failure passing by coincidence.
            verify_config(skip_auth_seed=True)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_AUTH_SECRET_SEED': 'configured-properly'})
    def test_configured_seed_passes(self):
        conf = SFConfig()
        with mock.patch('shakenfist.config.config', conf):
            verify_config()

    @mock.patch.dict(
        'os.environ',
        {'SHAKENFIST_AUTH_SECRET_SEED': UNCONFIGURED_AUTH_SECRET_SEED})
    def test_the_sentinel_never_equals_the_wrapper(self):
        # A guard on the mistake itself, independent of how
        # verify_config() happens to be written: the wrapper never
        # equals the bare sentinel, so any future code comparing them
        # directly is wrong. Pinned to the sentinel for the same reason
        # as the test above, so that it is the sentinel comparison being
        # tested and not whatever seed the host happens to carry.
        conf = SFConfig()
        self.assertNotEqual(
            UNCONFIGURED_AUTH_SECRET_SEED, conf.AUTH_SECRET_SEED)


class ClusterConfigExportFilterTestCase(base.ShakenFistTestCase):
    """load_cluster_config() must not export undeclared secrets.

    cluster_config is a general cluster-wide key/value store as well as
    the backing store for SFConfig fields, and KERBSIDE_JWT_SIGNING_KEY
    keeps an unencrypted Ed25519 private PEM in it. Exporting every row
    put that private key in the environment of every daemon on every
    node, and in every child privexec spawns, where it is readable from
    /proc/<pid>/environ -- enough to forge a console token for any
    instance in the cluster.
    """

    def test_declared_secret_field_is_exportable(self):
        # AUTH_SECRET_SEED matches SECRET_CONFIG_KEY_RE via _SEED$ but
        # is a declared field which SFConfig reads from the
        # environment. If the filter ever stops exporting it, every
        # daemon in the cluster falls back to the unconfigured
        # sentinel. This is the trap a "never export a secret" filter
        # walks into.
        self.assertTrue(SECRET_CONFIG_KEY_RE.search('AUTH_SECRET_SEED'))
        self.assertIn('AUTH_SECRET_SEED', SFConfig.model_fields)
        self.assertTrue(_exportable_cluster_config_key('AUTH_SECRET_SEED'))

    def test_undeclared_secret_row_is_not_exportable(self):
        self.assertTrue(
            SECRET_CONFIG_KEY_RE.search('KERBSIDE_JWT_SIGNING_KEY'))
        self.assertNotIn('KERBSIDE_JWT_SIGNING_KEY', SFConfig.model_fields)
        self.assertFalse(
            _exportable_cluster_config_key('KERBSIDE_JWT_SIGNING_KEY'))

    def test_ordinary_undeclared_row_is_exportable(self):
        # A row which is not a declared field and does not look like a
        # secret is still exported. cluster_config carries options
        # which do not exist as fields yet, and a new option must reach
        # a daemon without a code change.
        self.assertIsNone(SECRET_CONFIG_KEY_RE.search('SOME_FUTURE_OPTION'))
        self.assertNotIn('SOME_FUTURE_OPTION', SFConfig.model_fields)
        self.assertTrue(_exportable_cluster_config_key('SOME_FUTURE_OPTION'))

    @mock.patch.dict(
        'os.environ', {'SHAKENFIST_MARIADB_HOST': 'db.example.com'},
        clear=True)
    @mock.patch('sqlalchemy.create_engine')
    def test_direct_branch_applies_the_filter(self, mock_create_engine):
        conn = mock.MagicMock()
        conn.__enter__.return_value = conn
        conn.execute.return_value.fetchall.return_value = [
            ('AUTH_SECRET_SEED', json.dumps('a-real-seed')),
            ('KERBSIDE_JWT_SIGNING_KEY', json.dumps(
                {'active_kid': 'deadbeef',
                 'keys': [{'kid': 'deadbeef',
                           'private_pem': 'THE-PRIVATE-PEM'}]})),
            ('KERBSIDE_URL', json.dumps('https://kerbside.example.com')),
            ('SOME_FUTURE_OPTION', json.dumps('a-value')),
        ]
        mock_create_engine.return_value.connect.return_value = conn

        load_cluster_config()

        self.assertEqual(
            'a-real-seed', os.environ.get('SHAKENFIST_AUTH_SECRET_SEED'))
        self.assertEqual(
            'https://kerbside.example.com',
            os.environ.get('SHAKENFIST_KERBSIDE_URL'))
        self.assertEqual(
            'a-value', os.environ.get('SHAKENFIST_SOME_FUTURE_OPTION'))
        self.assertNotIn(
            'SHAKENFIST_KERBSIDE_JWT_SIGNING_KEY', os.environ)
        # Belt and braces: the private material is not anywhere in the
        # environment under any name.
        self.assertNotIn(
            'THE-PRIVATE-PEM', ''.join(os.environ.values()))

    @mock.patch.dict(
        'os.environ',
        {'SHAKENFIST_MARIADB_GATEWAY_HOSTS': '10.0.0.1'}, clear=True)
    @mock.patch('shakenfist.util.grpc_channel.make_database_channel')
    @mock.patch('shakenfist.protos.database_pb2_grpc.DatabaseServiceStub')
    def test_grpc_branch_applies_the_filter(self, mock_stub, mock_channel):
        entries = [
            mock.Mock(key_name='AUTH_SECRET_SEED',
                      value_json=json.dumps('a-real-seed')),
            mock.Mock(key_name='KERBSIDE_JWT_SIGNING_KEY',
                      value_json=json.dumps(
                          {'active_kid': 'deadbeef',
                           'keys': [{'kid': 'deadbeef',
                                     'private_pem': 'THE-PRIVATE-PEM'}]})),
            mock.Mock(key_name='SOME_FUTURE_OPTION',
                      value_json=json.dumps('a-value')),
        ]
        mock_stub.return_value.GetClusterConfig.return_value = mock.Mock(
            entries=entries)

        load_cluster_config()

        self.assertEqual(
            'a-real-seed', os.environ.get('SHAKENFIST_AUTH_SECRET_SEED'))
        self.assertEqual(
            'a-value', os.environ.get('SHAKENFIST_SOME_FUTURE_OPTION'))
        self.assertNotIn(
            'SHAKENFIST_KERBSIDE_JWT_SIGNING_KEY', os.environ)
        self.assertNotIn(
            'THE-PRIVATE-PEM', ''.join(os.environ.values()))

    def test_the_signing_key_is_still_readable_from_the_database(self):
        # The filter removes an environment copy, not the value. Its
        # only reader goes to the database for it, and
        # mariadb.get_cluster_config() does not consult os.environ for
        # the row's value.
        material = {'active_kid': 'deadbeef',
                    'keys': [{'kid': 'deadbeef',
                              'private_pem': 'THE-PRIVATE-PEM'}]}
        with mock.patch('shakenfist.mariadb.get_cluster_config',
                        return_value={
                            vdi_tokens.SIGNING_KEY_CONFIG_NAME: material}):
            self.assertEqual(material, vdi_tokens.get_signing_material())
