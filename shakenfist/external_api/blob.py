import flask
from flask_jwt_extended import jwt_required, get_jwt_identity
import os
import random
import requests
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist import baseobject
from shakenfist.blob import Blob, Blobs
from shakenfist.config import config
from shakenfist import etcd
from shakenfist.external_api import base as api_base
from shakenfist.util import general as util_general


def _read_file(filename, offset):
    with open(filename, 'rb') as f:
        f.seek(offset)
        d = f.read(8192)
        while d:
            yield d
            d = f.read(8192)


def _read_remote(target, blob_uuid, offset=0):
    api_token = util_general.get_api_token(
        'http://%s:%d' % (target, config.API_PORT),
        namespace=get_jwt_identity()[0])
    url = 'http://%s:%d/blobs/%s/data?offset=%d' % (
        target, config.API_PORT, blob_uuid, offset)

    r = requests.request('GET', url, stream=True,
                         headers={'Authorization': api_token,
                                  'User-Agent': util_general.get_user_agent()})
    for chunk in r.iter_content(chunk_size=8192):
        yield chunk


class BlobEndpoint(api_base.Resource):
    @jwt_required()
    def get(self, blob_uuid=None):
        b = Blob.from_db(blob_uuid)
        if not b:
            return api_base.error(404, 'blob not found')

        return b.external_view()


class BlobDataEndpoint(api_base.Resource):
    # NOTE(mikal): note that arguments from URL routes (blob_uuid for example),
    # are not included in the webargs schema because webargs doesn't appear to
    # know how to find them.
    get_args = {
        'offset': fields.Int(missing=0)
    }

    @jwt_required()
    @use_kwargs(get_args, location='query')
    def get(self, blob_uuid=None, offset=0):
        # Ensure the blob exists
        b = Blob.from_db(blob_uuid)
        if not b:
            return api_base.error(404, 'blob not found')

        # Fast path if we have the blob locally
        os.makedirs(os.path.join(config.STORAGE_PATH, 'blobs'), exist_ok=True)
        blob_path = os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid)
        if os.path.exists(blob_path):
            return flask.Response(
                flask.stream_with_context(_read_file(blob_path, offset)),
                mimetype='text/plain', status=200)

        # Otherwise find a node which has the blob and proxy.
        locations = b.locations
        if not locations:
            return api_base.error(404, 'blob missing')

        random.shuffle(locations)
        return flask.Response(flask.stream_with_context(
            _read_remote(locations[0], blob_uuid, offset=offset)),
            mimetype='text/plain', status=200)


class BlobsEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    def get(self, node=None):
        retval = []

        with etcd.ThreadLocalReadOnlyCache():
            for b in Blobs(filters=[baseobject.active_states_filter]):
                if node and node in b.locations:
                    retval.append(b.external_view())
                else:
                    retval.append(b.external_view())

        return retval
