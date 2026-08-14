# Copyright 2019 Michael Still and contributors
"""Regression tests for the sf-queues startup configuration banner.

startup_tasks() logs every configuration item at INFO. INFO and above
is shipped off the node to Loki, so for as long as AUTH_SECRET_SEED and
MARIADB_PASSWORD were plain strings the cluster's JWT signing seed and
its database password were written into log aggregation on every
sf-queues start. That was found while planning phase 6 of the auth
federation plan, by querying Loki for the credential -- it had been
happening on every node of every cluster, unnoticed, because the leak
is nowhere near the authentication code.

These tests pin both halves of the fix: the name-based redaction in the
banner itself, and that it does not redact ordinary settings (an
over-broad match would hide the configuration operators read this
output to see).
"""

from shakenfist.config import SECRET_CONFIG_KEY_RE
from shakenfist.daemons.queues import startup_tasks
from shakenfist.tests import base


class StartupConfigRedactionTestCase(base.ShakenFistTestCase):
    def _items(self):
        return dict(startup_tasks.redacted_config_items())

    def test_secret_config_values_are_redacted(self):
        items = self._items()
        for key in ['AUTH_SECRET_SEED', 'MARIADB_PASSWORD',
                    'LOKI_AUTH_HEADER']:
            self.assertIn(key, items)
            self.assertEqual('<redacted>', items[key], key)

    def test_ordinary_config_values_are_not_redacted(self):
        items = self._items()
        for key in ['NODE_NAME', 'ZONE', 'DISK_BUS', 'LOKI_BASE_URL']:
            self.assertIn(key, items)
            self.assertNotEqual('<redacted>', items[key], key)

    def test_numeric_settings_are_exempt_from_the_name_match(self):
        # The predicate is shared with show-config and deliberately
        # over-matches, but this banner has no --show-secrets to fall
        # back on. These three match the pattern, cannot be credentials,
        # and are tunables operators read the banner to confirm.
        items = self._items()
        for key in ['API_TOKEN_DURATION', 'FEDERATION_MAX_TOKEN_BYTES',
                    'KERBSIDE_TOKEN_DURATION']:
            self.assertIn(key, items)
            self.assertNotEqual('<redacted>', items[key], key)
            self.assertIsInstance(items[key], int)

    def test_no_secret_value_survives_the_banner(self):
        # The assertion the leak would have failed. Every non-numeric key
        # the shared predicate calls secret must be masked, whatever its
        # name -- including options added after this test was written,
        # which is why this iterates the predicate rather than a fixed
        # list.
        for key, value in startup_tasks.redacted_config_items():
            if not SECRET_CONFIG_KEY_RE.search(key):
                continue
            if isinstance(value, (bool, int, float)):
                continue
            self.assertEqual('<redacted>', value, key)

    def test_every_config_item_is_still_reported(self):
        # Redaction must mask values, not drop keys. An operator reading
        # the banner to confirm a setting took effect needs to see that
        # the item exists even when its value is hidden.
        from shakenfist.config import config
        self.assertEqual(
            sorted(config.model_dump().keys()), sorted(self._items().keys()))

    def test_predicate_matches_the_known_secret_names(self):
        # Guards the regex itself rather than its application, so that
        # loosening it cannot silently stop matching a credential we
        # know leaked.
        for key in ['AUTH_SECRET_SEED', 'MARIADB_PASSWORD',
                    'LOKI_AUTH_HEADER']:
            self.assertTrue(SECRET_CONFIG_KEY_RE.search(key), key)
        for key in ['NODE_NAME', 'ZONE', 'API_PORT', 'LOKI_BASE_URL']:
            self.assertFalse(SECRET_CONFIG_KEY_RE.search(key), key)
