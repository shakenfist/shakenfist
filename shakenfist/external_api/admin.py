# Documentation state:
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: operator
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:
import os

import flask
from flasgger import swag_from
from shakenfist_utilities import api as sf_api
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist import locks
from shakenfist import mariadb
from shakenfist import scheduler
from shakenfist.external_api import base as api_base
from shakenfist.namespace_claim import NamespaceClaims
from shakenfist.util import vdi_tokens


admin_locks_get_example = """{
    "cluster/": {
        "node": "sf-1",
        "operation": "Cluster maintenance",
        "pid": 3326781
    }
}"""


class AdminLocksEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'admin', 'List locks currently held in the cluster.', [],
        [(200, 'All locks currently held in the cluster.',
          admin_locks_get_example)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self):
        return locks.get_existing_locks()


admin_cacert_get_example = """-----BEGIN CERTIFICATE-----
MIIEFzCCAn+gAwIBAgIUCs+LmF8yISmu02Jht+LeM/9SF+owDQYJKoZIhvcNAQEL
...
LFPuUi9WNH611ybJLriyFIN4a8v67CX0VJ8G9yIyYGrDlY6jBWu16br/Fw==
-----END CERTIFICATE-----"""


class AdminClusterCaCertificateEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'admin', 'Retrieve the CA certificate used for TLS in this cluster.', [],
        [(200, 'A PEM encoded CA certificate.',
          admin_cacert_get_example)]))
    @api_base.log_token_use
    def get(self):
        cacert = ''
        if os.path.exists('/etc/pki/libvirt-spice/ca-cert.pem'):
            with open('/etc/pki/libvirt-spice/ca-cert.pem') as f:
                cacert = f.read()

        resp = flask.Response(cacert, mimetype='text/plain')
        resp.status_code = 200
        return resp


admin_vdi_token_pubkey_get_example = """{
    "active_kid": "3f2a9c1e",
    "keys": [
        {
            "kid": "3f2a9c1e",
            "alg": "EdDSA",
            "public_pem": "-----BEGIN PUBLIC KEY-----\\n...\\n-----END PUBLIC KEY-----\\n",
            "created": 1789000000
        }
    ]
}"""


class AdminVDITokenPublicKeyEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'admin',
        'Retrieve the public half of the Kerbside VDI console token '
        'signing key(s).', [],
        [(200, 'The active key id and all currently published public keys.',
          admin_vdi_token_pubkey_get_example),
         (404, 'No Kerbside VDI token signing key has been configured.',
          None),
         (500, 'The stored signing key material is corrupt.', None)]))
    @api_base.log_token_use
    def get(self):
        material = vdi_tokens.get_signing_material()
        if material is None:
            return sf_api.error(
                404,
                'no Kerbside VDI token signing key configured, run '
                'sf-ctl ensure-kerbside-signing-key')
        try:
            return vdi_tokens.public_view(material)
        except vdi_tokens.SigningKeyError as e:
            return sf_api.error(
                500, f'stored Kerbside VDI token signing key is corrupt: {e}')


admin_resources_get_example = """{
    "total": {
        "cpu_available": 66.0,
        "ram_available": 344064,
        "capacity_degraded": false
    },
    "per_node": {
        "00000000-0000-4000-8000-000000000001": {
            "cpu_max_per_instance": 32,
            "cpu_schedulable": 30,
            "cpu_reservation_clamped": false,
            "memory_reservation_clamped": false,
            "cpu_committed_row_present": true,
            "cpu_limit": 90,
            "cpu_hard_max": 90.0,
            "cpu_measured": 18,
            "domains_enumerated_at": 1792886390.12,
            "cpu_committed": 24,
            "cpu_available": 66.0,
            "cpu_load_1": 2.41,
            "cpu_load_5": 2.13,
            "cpu_load_15": 1.87,
            "memory_reserved_mb": 8192,
            "ram_max_per_instance": 81920,
            "ram_max": 393216.0,
            "ram_committed": 40960,
            "ram_available": 344064,
            "disk_available": 1780.0,
            "disk_limit_gb": 1900,
            "disk_committed_gb": 700,
            "instances_total": 9,
            "instances_active": 8
        }
    },
    "excluded": {
        "00000000-0000-4000-8000-000000000002": {
            "reason": "stale metrics",
            "metrics_timestamp": 1792886087.53
        }
    },
    "cluster_capacity": {
        "row_present": true,
        "degraded": false,
        "updated_at": 1792886100.0,
        "total_cpus": 180,
        "total_memory_mb": 770048,
        "total_disk_gb": 3800,
        "claimed_cpus": 40,
        "claimed_memory_mb": 81920,
        "claimed_disk_gb": 2000,
        "unclaimed_used_cpus": 12,
        "unclaimed_used_memory_mb": 16384,
        "unclaimed_used_disk_gb": 100,
        "unallocated_cpus": 128,
        "unallocated_memory_mb": 671744,
        "unallocated_disk_gb": 1700
    }
}"""


class AdminResourcesEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'admin', 'List resources currently available in the cluster.', [],
        [(200, 'All summary of resource usage and availability in the cluster.',
          admin_resources_get_example)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self):
        s = scheduler.Scheduler()
        return s.summarize_resources()


admin_claims_get_example = """[
    {
        "uuid": "00000000-0000-4000-8000-0000000000c1",
        "state": "created",
        "version": 1,
        "metadata": {},
        "namespace": "ci",
        "limit_cpus": 40,
        "limit_memory_mb": 81920,
        "limit_disk_gb": 2000,
        "used_cpus": 12,
        "used_memory_mb": 24576,
        "used_disk_gb": 600,
        "peak_used_cpus": 28,
        "peak_used_memory_mb": 57344,
        "peak_used_disk_gb": 1400,
        "coverage_state": "active",
        "expires_at": 1793491200.0,
        "updated_at": 1792886100.0
    },
    {
        "uuid": "00000000-0000-4000-8000-0000000000c2",
        "state": "created",
        "version": 1,
        "metadata": {},
        "namespace": "batch",
        "limit_cpus": 16,
        "limit_memory_mb": 32768,
        "limit_disk_gb": 500,
        "used_cpus": 0,
        "used_memory_mb": 0,
        "used_disk_gb": 0,
        "peak_used_cpus": 16,
        "peak_used_memory_mb": 30720,
        "peak_used_disk_gb": 420,
        "coverage_state": "expired",
        "expires_at": 1792540800.0,
        "updated_at": 1792540800.0
    }
]"""


# The coverage states the claims listing can filter on, built from the
# database layer's own list so the declaration and the read cannot
# disagree about which values are valid.
COVERAGE_STATE_PATTERN = '^(%s)$' % '|'.join(mariadb.CLAIM_COVERAGE_STATES)


class AdminClaimsEndpoint(api_base.Resource):
    # The shipped client sends GET parameters as a JSON body, and a raw
    # ?coverage_state= must work too, so the schema is bound where both
    # are read.
    get_args = {
        'coverage_state': fields.String(load_default='')
    }

    @swag_from(api_base.swagger_helper(
        'admin', 'List the capacity claims held by every namespace.',
        [('coverage_state', 'query', 'string',
          'Only list claims in this coverage state, active or expired. '
          'When omitted, every claim is listed.', False,
          {'pattern': COVERAGE_STATE_PATTERN})],
        [(200, 'Every capacity claim in the cluster.',
          admin_claims_get_example),
         (400, 'coverage_state is neither active nor expired.', None),
         (401, 'The caller is not a cluster administrator.', None)],
        requires_admin=True))
    @use_kwargs(get_args, location='json_or_query')
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self, coverage_state=''):
        # The declared pattern refuses a bad value before this runs when
        # validation is enforced; this is the refusal when it is not,
        # and it must happen before the read, which would raise.
        if coverage_state and coverage_state not in mariadb.CLAIM_COVERAGE_STATES:
            return sf_api.error(
                400, 'coverage_state must be "active" or "expired"')

        # Every claim the cluster holds, whatever its object state, for
        # the reason NamespaceClaims gives. The coverage filter is applied
        # by the database, and each claim renders from the row the listing
        # already read.
        return [c.external_view(row=row)
                for c, row in NamespaceClaims(
                    coverage_state=coverage_state).with_rows()]
