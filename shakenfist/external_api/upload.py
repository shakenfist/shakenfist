import flask
from flask_jwt_extended import jwt_required
import os
from shakenfist_utilities import api as sf_api, logs
import uuid

from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.config import config
from shakenfist.upload import Upload


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


class UploadCreateEndpoint(sf_api.Resource):
    @jwt_required()
    def post(self):
        return Upload.new(str(uuid.uuid4()), config.NODE_NAME).external_view()


class UploadDataEndpoint(sf_api.Resource):
    @jwt_required()
    @api_base.arg_is_upload_uuid
    @api_base.redirect_upload_request
    def post(self, upload_uuid=None, upload_from_db=None):
        upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        upload_path = os.path.join(upload_dir, upload_from_db.uuid)
        with open(upload_path, 'ab') as f:
            f.write(flask.request.get_data(cache=False, as_text=False,
                                           parse_form_data=False))

        st = os.stat(upload_path)
        return st.st_size


class UploadTruncateEndpoint(sf_api.Resource):
    @jwt_required()
    @api_base.arg_is_upload_uuid
    @api_base.redirect_upload_request
    def post(self, upload_uuid=None, offset=None, upload_from_db=None):
        upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
        os.makedirs(upload_dir, exist_ok=True)

        upload_path = os.path.join(upload_dir, upload_from_db.uuid)
        os.truncate(upload_path, int(offset))
