import flask
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import jwt_required
import os
import random
import requests

from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.external_api import base as api_base
from shakenfist import util


class BlobEndpoint(api_base.Resource):
    @jwt_required
    def get(self, blob_uuid=None):
        # Fast path if we have the blob locally
        os.makedirs(os.path.join(config.STORAGE_PATH, 'blobs'), exist_ok=True)
        blob_path = os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid)
        if os.path.exists(blob_path):
            def read_file(filename):
                with open(blob_path, 'rb') as f:
                    d = f.read(8192)
                    while d:
                        yield d
                        d = f.read(8192)

            return flask.Response(flask.stream_with_context(read_file(blob_path)),
                                  mimetype='text/plain', status=200)

        # Otherwise find a node which has the blob and proxy. Write to our blob
        # store as well if the blob is under replicated.
        b = Blob.from_db(blob_uuid)
        if not b:
            return api_base.error(404, 'blob not found')

        locations = b.locations
        if not locations:
            return api_base.error(404, 'blob missing')

        def read_remote(target, blob_uuid, blob_path=None):
            api_token = util.get_api_token(
                'http://%s:%d' % (target, config.API_PORT),
                namespace=get_jwt_identity())
            url = 'http://%s:%d/blob/%s' % (target, config.API_PORT, blob_uuid)

            if blob_path:
                local_blob = open(blob_path + '.partial', 'wb')
            r = requests.request('GET', url,
                                 headers={'Authorization': api_token,
                                          'User-Agent': util.get_user_agent()})
            for chunk in r.iter_content(chunk_size=8192):
                if blob_path:
                    local_blob.write(chunk)
                yield chunk

            if blob_path:
                local_blob.close()
                os.rename(blob_path + '.partial', blob_path)
                Blob.from_db(blob_uuid).observe()

        if len(locations) >= config.BLOB_REPLICATION_FACTOR:
            blob_path = None

        random.shuffle(locations)
        return flask.Response(flask.stream_with_context(
            read_remote(locations[0], blob_uuid, blob_path=blob_path)),
            mimetype='text/plain', status=200)
