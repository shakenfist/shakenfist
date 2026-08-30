from unittest import mock

from pydantic import SecretStr

from shakenfist.config import SFConfig
from shakenfist.config import UNCONFIGURED_AUTH_SECRET_SEED
from shakenfist.config import verify_config
from shakenfist.tests import base


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

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_API_VALIDATION_MODE': 'enforced'})
    def test_bogus_validation_mode_fails_at_load(self):
        # The setting exists to be flipped to 'enforce' in phase 4 of
        # PLAN-api-input-validation. A typo silently meaning warn is no
        # validation and no signal anything is wrong, so anything other
        # than the two literals must refuse to load.
        self.assertRaises(ValueError, SFConfig)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_API_VALIDATION_MODE': 'enforce'})
    def test_valid_validation_mode_loads(self):
        conf = SFConfig()
        self.assertEqual('enforce', conf.API_VALIDATION_MODE)


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
