from flask_jwt_extended import get_jwt_identity
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder

from shakenfist.artifact import Artifact
from shakenfist.artifact import ARTIFACT_URL
from shakenfist.artifact import LABEL_URL
from shakenfist.artifact import SNAPSHOT_URL
from shakenfist.artifact import UPLOAD_URL
from shakenfist import ipam
from shakenfist import network
from shakenfist.daemons import daemon
from shakenfist.instance import Instance
from shakenfist.namespace import namespace_is_trusted
from shakenfist.networkinterface import NetworkInterface


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def assign_floating_ip(ni):
    # Address is allocated and added to the record here, so the job has it later.
    fn = network.floating_network()
    ni.floating = fn.ipam.reserve_random_free_address(
        ni.unique_label(), ipam.RESERVATION_TYPE_FLOATING, '')


def assign_routed_ip(n):
    # Address is allocated and then returned, as there is no network interface
    # to associate it with.
    fn = network.floating_network()
    return fn.ipam.reserve_random_free_address(
        n.unique_label(), ipam.RESERVATION_TYPE_ROUTED, '')


def safe_get_network_interface(interface_uuid):
    ni = NetworkInterface.from_db(interface_uuid)
    if not ni:
        return None, None, sf_api.error(404, 'interface not found')

    log = LOG.with_fields({'network': ni.network_uuid,
                           'networkinterface': ni.uuid})

    n = network.Network.from_db(ni.network_uuid)
    if not n:
        log.info('Network not found or deleted')
        return None, None, sf_api.error(404, 'interface network not found')

    if get_jwt_identity()[0] not in [n.namespace, 'system']:
        log.info('Interface not found, failed ownership test')
        return None, None, sf_api.error(404, 'interface not found')

    i = Instance.from_db(ni.instance_uuid)
    if get_jwt_identity()[0] not in [i.namespace, 'system']:
        log.with_fields({'instance': i}).info(
            'Instance not found, failed ownership test')
        return None, None, sf_api.error(404, 'interface not found')

    return ni, n, None


# Convert internal shorthand forms into specific artifacts (but not their blob)
def lookup_artifact_reference(disk_base, namespace, instance_uuid):
    a = None

    if not disk_base:
        return None

    if disk_base.startswith('label:'):
        label = disk_base[len('label:'):]
        a = Artifact.from_url(
            Artifact.TYPE_LABEL,
            f'{LABEL_URL}{get_jwt_identity()[0]}/{label}',
            name=label, namespace=namespace)

    elif disk_base.startswith(SNAPSHOT_URL):
        a = Artifact.from_db(disk_base[len(SNAPSHOT_URL):])

    elif (disk_base.startswith(UPLOAD_URL) or
          disk_base.startswith(LABEL_URL) or
          disk_base.startswith(ARTIFACT_URL)):
        if disk_base.startswith(UPLOAD_URL):
            a = Artifact.from_url(Artifact.TYPE_IMAGE, disk_base,
                                  namespace=namespace)
        elif disk_base.startswith(LABEL_URL):
            a = Artifact.from_url(Artifact.TYPE_LABEL, disk_base,
                                  namespace=namespace)
        else:
            a_uuid = disk_base[len(ARTIFACT_URL):]
            a = Artifact.from_db(a_uuid, suppress_failure_audit=True)

    if not a:
        return None

    log = LOG.with_fields({'artifact': a})
    if instance_uuid:
        log = log.with_fields({'instance': instance_uuid})

    # Is the artifact ready?
    if a.state.value != Artifact.STATE_CREATED:
        log.info('Artifact not in ready state')
        return a, sf_api.error(
            404, 'artifact not ready (state=%s)' % a.state.value)

    # Can we see the artifact?
    if namespace_is_trusted(a.namespace, get_jwt_identity()[0]):
        return a
    if a.shared:
        return a

    # As far as we're concerned the artifact doesn't exist
    return None
