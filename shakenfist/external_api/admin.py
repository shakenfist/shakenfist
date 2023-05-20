# Documentation state:
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: operator
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:

from flasgger import swag_from
from shakenfist_utilities import api as sf_api


from shakenfist import etcd
from shakenfist.external_api import base as api_base


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
    @sf_api.caller_is_admin
    @api_base.log_token_use
    def get(self):
        return etcd.get_existing_locks()
