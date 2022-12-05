import base64
import bcrypt
import json
import requests
import secrets
from shakenfist_utilities import logs, random as sfrandom
import string
import time

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import etcd
from shakenfist.metrics import get_minimum_object_version as gmov
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Namespace(dbo):
    object_type = 'namespace'
    current_version = 4
    upgrade_supported = True

    ACTIVE_STATES = set([dbo.STATE_CREATED])

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
    }

    def __init__(self, static_values):
        if static_values.get('version', 1) != self.current_version:
            upgraded, static_values = self.upgrade(static_values)

            if upgraded and gmov('namespace') == self.current_version:
                etcd.put(self.object_type, None,
                         static_values.get('uuid'), static_values)
                LOG.with_fields({
                    self.object_type: static_values['uuid']}).info(
                        'Online upgrade committed')

        # We treat a namespace name as a UUID here for historical reasons
        super(Namespace, self).__init__(static_values['uuid'],
                                        static_values['version'])

    @classmethod
    def upgrade(cls, static_values):
        changed = False
        starting_version = static_values.get('version', 1)

        if static_values.get('version', 1) == 1:
            static_values['uuid'] = static_values['name']
            del static_values['name']

            etcd.put(
                'attribute/namespace', static_values['uuid'], 'state',
                {
                    'update_time': time.time(),
                    'value': 'created'
                })

            etcd.put(
                'attribute/namespace', static_values['uuid'], 'keys',
                {
                    'keys': static_values['keys']
                })
            del static_values['keys']

            if 'service_key' in static_values:
                etcd.put(
                    'attribute/namespace', static_values['uuid'], 'service_key',
                    {
                        'service_key': static_values['service_key']
                    })
                del static_values['service_key']

            static_values['version'] = 2
            changed = True

        if static_values.get('version') == 2:
            etcd.put(
                'attribute/namespace', static_values['uuid'], 'trust',
                {'full_trust': ['system']})
            static_values['version'] = 3
            changed = True

        if static_values.get('version') == 3:
            nonced_keys = {}

            keys = etcd.get(
                'attribute/namespace', static_values['uuid'], 'keys')
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
            db_data = etcd.get(
                'attribute/namespace', static_values['uuid'], 'service_key')
            if db_data:
                nonced_keys['_service_key'] = {
                    'key': db_data['service_key'],
                    'nonce': sfrandom.random_id(),
                    'expiry': time.time() + 300
                }
                etcd.delete(
                    'attribute/namespace', static_values['uuid'], 'service_key')

            etcd.put(
                'attribute/namespace', static_values['uuid'], 'keys',
                {'nonced_keys': nonced_keys})

            static_values['version'] = 4
            changed = True

        if changed:
            LOG.with_fields({
                cls.object_type: static_values['uuid'],
                'start_version': starting_version,
                'final_version': static_values.get('version')
            }).info('Object online upgraded')
        return changed, static_values

    @classmethod
    def new(cls, name):
        n = Namespace.from_db(name)
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
            return {}

        nonced_keys = db_data.get('nonced_keys', {})
        for k in list(nonced_keys.keys()):
            if 'expiry' in nonced_keys[k]:
                if time.time() > nonced_keys[k]['expiry']:
                    del nonced_keys[k]

        return {'nonced_keys': nonced_keys}

    def add_key(self, name, value, expiry=None):
        encoded = str(base64.b64encode(bcrypt.hashpw(
            value.encode('utf-8'), bcrypt.gensalt())), 'utf-8')

        with self.get_lock_attr('keys', 'Add key'):
            k = self.keys
            if 'nonced_keys' not in k:
                k['nonced_keys'] = {}

            k['nonced_keys'][name] = {
                'key': encoded,
                'nonce': sfrandom.random_id()
            }
            if expiry:
                k['nonced_keys'][name]['expiry'] = expiry
            self._db_set_attribute('keys', k)

    def remove_key(self, name):
        with self.get_lock_attr('keys', 'Remove key'):
            k = self.keys
            if 'nonced_keys' in k:
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
        return {
            'name': self.uuid,
            'state': self.state.value,
            'trust': {
                'full': self.trust
            }
        }


class Namespaces(dbo_iter):
    def __iter__(self):
        for _, n in etcd.get_all('namespace', None):
            uniq = n.get('uuid')
            if not uniq:
                uniq = n.get('name')
            if not uniq:
                continue

            n = Namespace.from_db(uniq)
            if not n:
                continue

            out = self.apply_filters(n)
            if out:
                yield out


def get_api_token(base_url, namespace='system'):
    auth_url = base_url + '/auth'
    LOG.info('Fetching %s auth token from %s', namespace, auth_url)

    ns = Namespace.from_db(namespace)
    key = ''.join(secrets.choice(string.ascii_letters) for i in range(50))
    unique = ''.join(secrets.choice(string.ascii_letters) for i in range(5))
    ns.add_key('_service_key_%s' % unique, key, expiry=(time.time() + 300))

    r = requests.request('POST', auth_url,
                         data=json.dumps({
                             'namespace': namespace,
                             'key': key
                         }),
                         headers={'Content-Type': 'application/json',
                                  'User-Agent': util_general.get_user_agent()})
    if r.status_code != 200:
        raise Exception('Unauthorized')
    return 'Bearer %s' % r.json()['access_token']


def namespace_is_trusted(namespace, requestor):
    if namespace == requestor:
        return True

    ns = Namespace.from_db(namespace)
    if not ns:
        return False

    if requestor not in ns.trust:
        return False

    return True
