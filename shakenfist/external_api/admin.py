# Documentation state:
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: operator
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:

from flasgger import swag_from
import flask
import os
from shakenfist_utilities import api as sf_api


from shakenfist import etcd
from shakenfist.external_api import base as api_base
from shakenfist import scheduler


admin_locks_get_example = """{
    "/sflocks/sf/cluster/": {
        "node": "sf-1",
        "operation": "Cluster maintenance",
        "pid": 3326781
    }
}"""


class AdminLocksEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'admin', 'List locks currently held in the cluster.', [],
        [(200, 'All locks currently held in the cluster.',
          admin_locks_get_example)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self):
        return etcd.get_existing_locks()


admin_cacert_get_example = """-----BEGIN CERTIFICATE-----
MIIEFzCCAn+gAwIBAgIUCs+LmF8yISmu02Jht+LeM/9SF+owDQYJKoZIhvcNAQEL
...
LFPuUi9WNH611ybJLriyFIN4a8v67CX0VJ8G9yIyYGrDlY6jBWu16br/Fw==
-----END CERTIFICATE-----"""


class AdminClusterCaCertificateEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'admin', 'Retrieve the CA certificate used for TLS in this cluster.', [],
        [(200, 'A PEM encoded CA certificate.',
          admin_cacert_get_example)]))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self):
        cacert = ''
        if os.path.exists('/etc/pki/libvirt-spice/ca-cert.pem'):
            with open('/etc/pki/libvirt-spice/ca-cert.pem') as f:
                cacert = f.read()

        resp = flask.Response(cacert, mimetype='text/plain')
        resp.status_code = 200
        return resp


admin_resources_get_example = """{
    ...
}"""


class AdminREsourcesEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'admin', 'List resources currently available in the cluster.', [],
        [(200, 'All summary of resource usage and availability in the cluster.',
          admin_resources_get_example)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self):
        s = scheduler.Scheduler()
        return s.summarize_resources()
