import json
import requests
import secrets
from shakenfist_utilities import logs
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
    current_version = 3
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
                {'full_trust': 'system'})
            static_values['version'] = 3
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
        n._db_set_attribute('trust', {'full_trust': 'system'})
        return n

    @property
    def service_key(self):
        db_data = self._db_get_attribute('service_key')
        if not db_data:
            return {}
        return db_data

    @service_key.setter
    def service_key(self, value):
        self._db_set_attribute('service_key', {'service_key': value})

    @property
    def keys(self):
        with self.get_lock_attr('keys', 'Read keys'):
            db_data = self._db_get_attribute('keys')
        if not db_data:
            return {}
        return db_data

    def add_key(self, name, value):
        with self.get_lock_attr('keys', 'Add key'):
            k = self._db_get_attribute('keys')
            k[name] = value
            self._db_set_attribute('keys', k)

    def remove_key(self, name):
        with self.get_lock_attr('keys', 'Remove key'):
            k = self._db_get_attribute('keys')
            del k[name]
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
    if ns.service_key:
        key = ns.service_key['service_key']
    else:
        key = ''.join(secrets.choice(string.ascii_lowercase)
                      for i in range(50))
        ns.service_key = key

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
