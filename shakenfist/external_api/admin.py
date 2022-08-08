from flask_jwt_extended import jwt_required
from shakenfist_utilities import api as sf_api


from shakenfist import db


class AdminLocksEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    def get(self):
        return db.get_existing_locks()
