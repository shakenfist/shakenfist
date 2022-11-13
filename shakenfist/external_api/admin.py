from flask_jwt_extended import jwt_required
from shakenfist_utilities import api as sf_api


from shakenfist import db
from shakenfist.external_api import base as api_base


class AdminLocksEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.log_token_use
    def get(self):
        return db.get_existing_locks()
