from flask_jwt_extended import jwt_required
from flasgger import swag_from
from shakenfist_utilities import api as sf_api


from shakenfist import db
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
    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.log_token_use
    def get(self):
        return db.get_existing_locks()
