import base64
import secrets
import string
import time

import bcrypt
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sfrandom  # noreorder

from shakenfist import etcd
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import access_tokens


LOG, _ = logs.setup(__name__)


class Namespace(dbo):
    object_type = ObjectType.NAMESPACE
    initial_version = 2
    current_version = 6

    # docs/developer_guide/state_machine.md has a description of these states.
    ACTIVE_STATES = {dbo.STATE_CREATED}

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        # Namespace uses the namespace name as its identifier for historical
        # reasons instead of a proper UUID. We store it directly to avoid UUID
        # conversion. The uuid property is overridden to return a string.
        self.__namespace_name = static_values['uuid']
        self._DatabaseBackedObject__uuid = self.__namespace_name  # type: ignore
        self._DatabaseBackedObject__version = static_values.get('version')
        self._DatabaseBackedObject__in_memory_only = False
        self.log = LOG.with_fields({self.object_type: self.__namespace_name})

    @property
    def uuid(self) -> str:
        """Return the Namespace's identifier (name) as a string.

        This overrides the base class to return a string instead of uuid.UUID
        because namespaces use their name as their identifier for historical
        reasons.
        """
        return self.__namespace_name

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values):
        etcd.put('attribute/namespace', static_values['uuid'], 'trust',
                 {'full_trust': ['system']})

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values):
        nonced_keys = {}

        keys = etcd.get('attribute/namespace', static_values['uuid'], 'keys')
        if keys:
            # Convert across keys in the correct location
            for k in keys.get('keys', {}):
                nonced_keys[k] = {
                    'key': keys['keys'][k],
                    'nonce': sfrandom.random_id()
                }
            if 'keys' in keys:
                del keys['keys']

            # Move across keys in the incorrect location. These override as they
            # are how the namespace and auth code was actually using keys.
            for k in keys:
                nonced_keys[k] = {
                    'key': keys[k],
                    'nonce': sfrandom.random_id()
                }

        # Move across the service key
        db_data = etcd.get('attribute/namespace', static_values['uuid'], 'service_key')
        if db_data:
            nonced_keys['_service_key'] = {
                'key': db_data['service_key'],
                'nonce': sfrandom.random_id(),
                'expiry': time.time() + 300
            }
            etcd.delete('attribute/namespace', static_values['uuid'], 'service_key')

        etcd.put(
            'attribute/namespace', static_values['uuid'], 'keys',
            {'nonced_keys': nonced_keys})

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        cls._upgrade_metadata_to_attribute(static_values['uuid'])

    @classmethod
    def _upgrade_step_5_to_6(cls, static_values):
        # State migration to MariaDB is now handled by sf-ctl migrate-state-to-mariadb
        ...

    @classmethod
    def new(cls, name):
        n = Namespace.from_db(name, suppress_failure_audit=True)
        if n:
            return n

        Namespace._db_create(name, {
            'uuid': name,
            'version': cls.current_version
        })
        n = Namespace.from_db(name)
        n.state = cls.STATE_CREATED
        n._db_set_attribute('trust', {'full_trust': ['system']})
        return n

    @property
    def keys(self):
        db_data = self._db_get_attribute('keys')
        if not db_data:
            return {'nonced_keys': {}}

        nonced_keys = db_data.get('nonced_keys', {})
        for k in list(nonced_keys.keys()):
            if 'expiry' in nonced_keys[k]:
                if time.time() > nonced_keys[k]['expiry']:
                    del nonced_keys[k]

        return {'nonced_keys': nonced_keys}

    def add_key(self, name, value, expiry=None):
        encoded = str(base64.b64encode(bcrypt.hashpw(
            value.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        nonce = sfrandom.random_id()

        with self.get_lock_attr('keys', 'Add key'):
            k = self.keys
            k['nonced_keys'][name] = {
                'key': encoded,
                'nonce': nonce
            }
            if expiry:
                k['nonced_keys'][name]['expiry'] = expiry
            self._db_set_attribute('keys', k)

        return nonce

    def remove_key(self, name):
        with self.get_lock_attr('keys', 'Remove key'):
            k = self.keys
            if name in k['nonced_keys']:
                del k['nonced_keys'][name]
                self._db_set_attribute('keys', k)

    @property
    def trust(self):
        db_data = self._db_get_attribute('trust')
        if not db_data:
            return []
        return db_data['full_trust']

    def add_trust(self, namespace):
        with self.get_lock_attr('trust', 'Add trust'):
            db_data = self._db_get_attribute('trust')
            if namespace not in db_data['full_trust']:
                db_data['full_trust'].append(namespace)
                self._db_set_attribute('trust', db_data)

    def remove_trust(self, namespace):
        with self.get_lock_attr('trust', 'Remove trust'):
            # You cannot remove the trust of the system namespace, because if
            # you could then the cluster admin wouldn't see your resources.
            if namespace == 'system':
                return

            db_data = self._db_get_attribute('trust')
            if namespace in db_data['full_trust']:
                db_data['full_trust'].remove(namespace)
                self._db_set_attribute('trust', db_data)

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        retval = self._external_view()
        del retval['uuid']
        retval.update({
            'name': self.uuid,
            'keys': [],
            'trust': {
                'full': self.trust
            }
        })

        # Mix in key names
        keys = self.keys
        for k in keys.get('nonced_keys', {}):
            retval['keys'].append(k)

        return retval


class Namespaces(dbo_iter):
    base_object = Namespace

    def __iter__(self):
        for _, static_values in self.get_iterator():
            uniq = static_values.get('uuid')
            if not uniq:
                uniq = static_values.get('name')
            if not uniq:
                continue

            n = Namespace.from_db(uniq)
            if not n:
                continue

            out = self.apply_filters(n)
            if out:
                yield out


CACHED_TOKENS = {}


def get_api_token(base_url, namespace='system'):
    if namespace in CACHED_TOKENS:
        expiry, access_token = CACHED_TOKENS[namespace]
        if expiry - time.time() > 15:
            return 'Bearer %s' % access_token

    auth_url = base_url + '/auth'
    LOG.info('Fetching %s auth token from %s', namespace, auth_url)

    ns = Namespace.from_db(namespace)

    key = ''.join(secrets.choice(string.ascii_letters) for i in range(50))
    unique = ''.join(secrets.choice(string.ascii_letters) for i in range(5))
    keyname = '_service_key_%s' % unique
    expiry = time.time() + 300
    nonce = ns.add_key(keyname, key, expiry=expiry)

    # Cheat and don't actually call the auth API to create a token, just call its
    # underlying code, thus saving a network round trip.
    token = access_tokens.create_token(ns, keyname, nonce, duration=5)

    CACHED_TOKENS[namespace] = (expiry, token['access_token'])
    return 'Bearer %s' % token['access_token']


def namespace_is_trusted(namespace, requestor):
    if namespace == requestor:
        return True

    ns = Namespace.from_db(namespace, suppress_failure_audit=True)
    if not ns:
        return False

    if requestor not in ns.trust:
        return False

    return True
