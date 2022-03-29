from flask_jwt_extended import jwt_required


from shakenfist import db
from shakenfist.external_api import base as api_base


class AdminLocksEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    def get(self):
        return db.get_existing_locks()
