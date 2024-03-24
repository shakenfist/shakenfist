# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:

import flask
from flask_jwt_extended import get_jwt_identity
from flasgger import swag_from
import os
import random
import requests
from shakenfist_utilities import api as sf_api
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist.blob import Blob, Blobs
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.external_api import base as api_base
from shakenfist.instance import instance_usage_for_blob_uuid
from shakenfist.namespace import get_api_token
from shakenfist.util import general as util_general


def _read_file(filename, offset):
    with open(filename, 'rb') as f:
        f.seek(offset)
        while d := f.read(8192):
            yield d


def _read_remote(target, blob_uuid, offset=0):
    api_token = get_api_token(
        'http://%s:%d' % (target, config.API_PORT),
        namespace=get_jwt_identity()[0])
    url = 'http://%s:%d/blobs/%s/data?offset=%d' % (
        target, config.API_PORT, blob_uuid, offset)

    r = requests.request(
            'GET', url, stream=True,
            headers={
                'Authorization': api_token,
                'User-Agent': util_general.get_user_agent(),
                'X-Request-ID': flask.request.headers.get('X-Request-ID')
            })
    for chunk in r.iter_content(chunk_size=8192):
        yield chunk


def arg_is_blob_uuid(func):
    def wrapper(*args, **kwargs):
        if 'blob_uuid' in kwargs:
            kwargs['blob_from_db'] = Blob.from_db(
                kwargs['blob_uuid'], suppress_failure_audit=True)

        if not kwargs.get('blob_from_db'):
            return sf_api.error(404, 'blob not found')

        return func(*args, **kwargs)
    return wrapper


blob_get_example = """{
    "cluster_size": 2097152.0,
    "compat": 1.1,
    "compression type": "zlib",
    "depends_on": null,
    "disk size": "3.31 GiB",
    "extended l2": "false",
    "fetched_at": 1683995934.357137,
    "file format": "qcow2",
    "instances": [
        "0a56ef2c-8331-4ed7-a443-267f53bfb24c",
        "0d0fb7fd-bfe4-4fc4-af6d-6f0c9fe2acd9",
        "fe55d1fd-80ab-4357-b04d-214f260a2325"
    ],
    "last_used": 1684054381.217045,
    "locations": [
        "sf-2",
        "sf-1",
        "sf-3",
        "sf-4"
    ],
    "metadata": {},
    "mime-type": "application/octet-stream",
    "modified": 1683995934.357137,
    "reference_count": 26,
    "sha512": "e83e19c98de906...289e51a0252b0aa1b3fce",
    "size": 3566573056,
    "state": "created",
    "transcodes": {
        "zlib;qcow2;cluster_size": "ebafb833-8e7f-4df6-97b3-f1ecffd65e86"
    },
    "uuid": "578da8b6-eb98-4e10-bb36-e4d4d763d312",
    "version": 6,
    "virtual size": 32212254720.0
}"""


class BlobEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Get blob information.',
        [('blob_uuid', 'query', 'uuid', 'The UUID of the blob.', True)],
        [(200, 'Information about a single blob.', blob_get_example),
         (404, 'Blob not found.', None)]))
    @api_base.verify_token
    @api_base.log_token_use
    @arg_is_blob_uuid
    def get(self, blob_uuid=None, blob_from_db=None):
        out = blob_from_db.external_view()
        out['instances'] = instance_usage_for_blob_uuid(blob_uuid)
        return out


class BlobDataEndpoint(sf_api.Resource):
    # NOTE(mikal): note that arguments from URL routes (blob_uuid for example),
    # are not included in the webargs schema because webargs doesn't appear to
    # know how to find them.
    get_args = {
        'offset': fields.Int(missing=0)
    }

    @swag_from(api_base.swagger_helper(
        'blobs', 'Get blob data.',
        [
            ('blob_uuid', 'query', 'uuid', 'The UUID of the blob.', True),
            ('offset', 'query', 'integer',
             'The offset into the file to start reading from.', False)
        ],
        [(200, 'Content of a blob as a streaming binary HTTP result.', 'n/a'),
         (404, 'Blob not found.', None)]))
    @api_base.verify_token
    @use_kwargs(get_args, location='query')
    @api_base.log_token_use
    @arg_is_blob_uuid
    def get(self, blob_uuid=None, offset=0, blob_from_db=None):
        # Fast path if we have the blob locally
        blob_path = Blob.filepath(blob_uuid)
        if os.path.exists(blob_path):
            return flask.Response(
                flask.stream_with_context(_read_file(blob_path, offset)),
                mimetype='text/plain', status=200)

        # Otherwise find a node which has the blob and proxy.
        locations = blob_from_db.locations
        if not locations:
            return sf_api.error(404, 'blob missing')

        random.shuffle(locations)
        return flask.Response(flask.stream_with_context(
            _read_remote(locations[0], blob_uuid, offset=offset)),
            mimetype='text/plain', status=200)


blobs_get_example = """[
{
    ...
    "uuid": "578da8b6-eb98-4e10-bb36-e4d4d763d312",
    "version": 6,
    "virtual size": 32212254720.0
},
{
    ...
    "uuid": "bdb179a0-5c4d-42d5-8282-4653b869f430",
    "version": 6,
    "virtual size": 32212254720.0
}
]"""


class BlobsEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', ('Get all blobs.'),
        [('node', 'body', 'node',
          'Limit results to a specific hypervisor node.', False)],
        [(200, ('A list of blob dictionaries, each containing the same '
                'output as a GET for a blob artifact would show.'),
          blobs_get_example)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self, node=None):
        retval = []

        for b in Blobs(filters=[], prefilter='active'):
            if node and node in b.locations:
                retval.append(b.external_view())
            else:
                retval.append(b.external_view())

        return retval


class BlobChecksumsEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Search for a blob by sha512 hash.',
        [('hash', 'query', 'string', 'The sha512 hash to search for.', True)],
        [(200, 'Information about a single blob.', blob_get_example),
         (404, 'Blob not found.', None)]))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, hash=None):
        if not hash:
            return sf_api.error(400, 'you must specify a hash')

        for b in Blobs(filters=[], prefilter='active'):
            if b.checksums.get('sha512') == hash:
                out = b.external_view()
                out['instances'] = instance_usage_for_blob_uuid(b.uuid)
                return out

        return None


class BlobMetadatasEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Fetch metadata for a blob.',
        [
            ('blob_uuid', 'query', 'uuid', 'The blob to fetch metadata for.', True)
        ],
        [(200, 'Blob metadata, if any.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def get(self, blob_uuid=None, blob_from_db=None):
        return blob_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'blobs', 'Add metadata for a blob.',
        [
            ('blob_uuid', 'query', 'uuid', 'The blob to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def post(self, blob_uuid=None, key=None, value=None, blob_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        blob_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'post'})
        blob_from_db.add_metadata_key(key, value)


class BlobMetadataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Update a metadata key for an blob.',
        [
            ('blob_uuid', 'query', 'uuid', 'The blob to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def put(self, blob_uuid=None, key=None, value=None, blob_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        blob_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'put'})
        blob_from_db.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'blobs', 'Delete a metadata key for an blob.',
        [
            ('blob_uuid', 'query', 'uuid', 'The blob to remove a key from.', True),
            ('key', 'query', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @arg_is_blob_uuid
    @api_base.log_token_use
    def delete(self, blob_uuid=None, key=None, value=None, blob_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        blob_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        blob_from_db.remove_metadata_key(key)
