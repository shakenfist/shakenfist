from flask_jwt_extended import get_jwt_identity

from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist import db
from shakenfist.instance import Instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist.networkinterface import NetworkInterface


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


def metadata_putpost(meta_type, owner, key, value):
    if meta_type not in ['namespace', 'instance', 'network']:
        return api_base.error(500, 'invalid meta_type %s' % meta_type)
    if not key:
        return api_base.error(400, 'no key specified')
    if not value:
        return api_base.error(400, 'no value specified')

    with db.get_lock('metadata', meta_type, owner,
                     op='Metadata update'):
        md = db.get_metadata(meta_type, owner)
        if md is None:
            md = {}
        md[key] = value
        db.persist_metadata(meta_type, owner, md)


def assign_floating_ip(ni):
    float_net = net.Network.from_db('floating')
    if not float_net:
        return api_base.error(404, 'floating network not found')

    # Address is allocated and added to the record here, so the job has it later.
    db.add_event('interface', ni.uuid, 'api', 'float', None, None)
    with db.get_lock('ipmanager', None, 'floating', ttl=120, op='Interface float'):
        ipm = IPManager.from_db('floating')
        addr = ipm.get_random_free_address(ni.unique_label())
        ipm.persist()

    ni.floating = addr


def safe_get_network_interface(interface_uuid):
    ni = NetworkInterface.from_db(interface_uuid)
    if not ni:
        return None, None, api_base.error(404, 'interface not found')

    log = LOG.with_fields({'network': ni.network_uuid,
                           'networkinterface': ni.uuid})

    n = net.Network.from_db(ni.network_uuid)
    if not n:
        log.info('Network not found or deleted')
        return None, None, api_base.error(404, 'interface network not found')

    if get_jwt_identity()[0] not in [n.namespace, 'system']:
        log.info('Interface not found, failed ownership test')
        return None, None, api_base.error(404, 'interface not found')

    i = Instance.from_db(ni.instance_uuid)
    if get_jwt_identity()[0] not in [i.namespace, 'system']:
        log.with_object(i).info('Instance not found, failed ownership test')
        return None, None, api_base.error(404, 'interface not found')

    return ni, n, None
