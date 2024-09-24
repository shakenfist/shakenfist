# Copyright 2020 Michael Still
import uuid

from shakenfist_utilities import logs

from shakenfist import etcd
from shakenfist.exceptions import IPManagerMissing


LOG, _ = logs.setup(__name__)


#####################################################################
# IPManagers
#####################################################################


def get_ipmanager(network_uuid):
    ipm = etcd.get('ipmanager', None, network_uuid)
    if not ipm:
        raise IPManagerMissing('IP Manager not found for network %s' % network_uuid)
    return ipm


def persist_ipmanager(network_uuid, data):
    etcd.put('ipmanager', None, network_uuid, data)


def delete_ipmanager(network_uuid):
    etcd.delete('ipmanager', None, uuid)
