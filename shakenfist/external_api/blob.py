# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:
import math
import os
import random

import flask
import requests
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.constants import BLOB_HASH_ALGORITHMS
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import mariadb
from shakenfist.node import Node
from shakenfist.daemons import daemon
from shakenfist.schema.operations.baseclusteroperation \
    import PRIORITY
from shakenfist.schema.operations.node_blob_op \
    import create_and_enqueue as nbo_create_and_enqueue
from shakenfist.schema.operations.node_blob_op \
    import model_tasks as nbo_tasks
from shakenfist.external_api import base as api_base
from shakenfist.instance import instance_usage_for_blob_uuid
from shakenfist.namespace import get_api_token
from shakenfist.util.access_tokens import request_namespace
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


CHUNK_SIZE = 128 * 1024


def _read_file(filename, offset, limit=0):
    remaining = limit
    if limit == 0:
        remaining = math.inf

    with open(filename, 'rb') as f:
        f.seek(offset)
        while d := f.read(min(CHUNK_SIZE, remaining)):
            yield d
            remaining -= len(d)


def _read_remote(target, blob_uuid, offset=0, limit=0):
    api_token = get_api_token(
        f'http://{target}:13000', namespace=request_namespace())
    url = (f'http://{target}:13000/blobs/{blob_uuid}/'
           f'data?offset={offset}&limit={limit}')

    LOG.with_fields({
        'blob': blob_uuid,
        'offset': offset,
        'host': target
    }).info('Requesting blob from remote host')
    r = requests.request(
        'GET', url, stream=True,
        headers={
            'Authorization': api_token,
            'User-Agent': util_general.get_user_agent(),
            'X-Request-ID': flask.request.headers.get('X-Request-ID')
        })
    yield from r.iter_content(chunk_size=CHUNK_SIZE)


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
    "checksums": {
        "sha1": "40a4d...d601d",
        "sha256": "e3a57...59afb",
        "sha512": "db80b...93659",
        "xxh128": "75584...92f29"
    }
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
    "virtual size": 32212254720.0,
    "references_to": {
        "disk": [
            {
                "source_object_type": "instance",
                "source_uuid": "0a56ef2c-8331-4ed7-a443-267f53bfb24c",
                "relationship": "disk",
                "relationship_value": "0",
                "target_object_type": "blob",
                "target_uuid": "578da8b6-eb98-4e10-bb36-e4d4d763d312",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ]
    },
    "references_from": {}
}"""


class BlobEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Get blob information.',
        [('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True)],
        [(200, 'Information about a single blob.', blob_get_example),
         (404, 'Blob not found.', None)]))
    @api_base.log_token_use
    @arg_is_blob_uuid
    def get(self, blob_uuid=None, blob_from_db=None):
        out = blob_from_db.external_view()
        out['instances'] = instance_usage_for_blob_uuid(blob_uuid)
        return out


class BlobDataEndpoint(api_base.Resource):
    # NOTE(mikal): note that arguments from URL routes (blob_uuid for example),
    # are not included in the webargs schema because webargs doesn't appear to
    # know how to find them.
    # NOTE(mikal): a marshmallow validate=Range(min=0) here would be
    # the obvious place for the bound, but webargs raises
    # UnprocessableEntity and the app's error handler renders that as a
    # 500 -- the same serialisation hazard the json_or_query loader in
    # base.py documents. So the bound is checked in the handler, where
    # it can answer 400 like every other malformed argument in the
    # tree. Phase 3 compiles the declaration into real validation and
    # this goes away.
    get_args = {
        'offset': fields.Int(load_default=0),
        'limit': fields.Int(load_default=0)
    }

    @swag_from(api_base.swagger_helper(
        'blobs', 'Get blob data.',
        [
            ('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True),
            ('offset', 'query', 'unsignedinteger',
             'The offset into the file to start reading from.', False),
            ('limit', 'query', 'unsignedinteger',
             ('The maximum amount of data to return in one response. '
              '0 means no limit.'), False)
        ],
        [(200, 'Content of a blob as a streaming binary HTTP result.', 'n/a'),
         (400, 'The offset or limit is negative.', None),
         (404, 'Blob not found.', None)]))
    @use_kwargs(get_args, location='query')
    @api_base.log_token_use
    @arg_is_blob_uuid
    def get(self, blob_uuid=None, offset=0, limit=0, blob_from_db=None):
        # The declaration publishes minimum 0 on both, and this is the
        # server backing it rather than waiting for phase 4 to compile
        # the bound. Unbacked, both failed worse than meaninglessly: a
        # negative offset reached f.seek() inside stream_with_context,
        # so the OSError arrived after the 200 had begun and the caller
        # saw a truncated body rather than an error, and a negative
        # limit made `remaining` negative so f.read(min(CHUNK_SIZE, -1))
        # read to EOF, quietly defeating the cap it was asked for.
        # Checked before the local and the proxied paths, because the
        # proxy would otherwise pass the bad value to another node.
        if offset < 0:
            return sf_api.error(400, 'offset cannot be negative')
        if limit < 0:
            return sf_api.error(400, 'limit cannot be negative')

        # Fast path if we have the blob locally
        blob_path = Blob.filepath(blob_uuid)
        if os.path.exists(blob_path):
            LOG.debug('Returning direct result')
            return flask.Response(
                flask.stream_with_context(_read_file(blob_path, offset,
                                                     limit=limit)),
                mimetype='text/plain', status=200)

        # Otherwise find a node which has the blob and proxy.
        LOG.debug('Returning proxied result')
        locations = blob_from_db.locations
        if not locations:
            return sf_api.error(404, 'blob missing')

        random.shuffle(locations)
        return flask.Response(flask.stream_with_context(
            _read_remote(locations[0], blob_uuid, offset=offset, limit=limit)),
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


class BlobsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', ('Get all blobs.'),
        [('node', 'body', 'node',
          'Limit results to a specific hypervisor node.', False)],
        [(200, ('A list of blob dictionaries, each containing the same '
                'output as a GET for a blob artifact would show.'),
          blobs_get_example),
         (503, 'The database is unavailable, so the list of blobs could '
               'not be read.', None)],
        requires_admin=True))
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self, node=None):
        retval = []

        for blob_uuid in mariadb.get_active_blob_uuids():
            b = Blob.from_db(blob_uuid)
            if not b:
                continue
            if not node or node in b.locations:
                retval.append(b.external_view())

        return retval


blob_events_example = """[
    ...
    {
            "duration": null,
            "extra": {},
            "fqdn": "sf-3",
            "message": "artifact fetch complete",
            "timestamp": 1684718452.2673004,
            "type": "audit"
        },
    ...
]"""


class BlobEventsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Get blob event information.',
        [
            ('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100 and is '
             'capped at 1000.', False, {'minimum': 1, 'maximum': 1000})
        ],
        [(200, 'Event information about a single blob.', blob_events_example),
         (404, 'Blob not found.', None)]))
    @api_base.log_token_use
    def get(self, blob_uuid=None, event_type=None, limit=100):
        return api_base.object_events_response(
            'blob', blob_uuid, limit, event_type)


class BlobChecksumEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Get a checksum for a blob.',
        [
            ('blob_uuid', 'path', 'uuid', 'The UUID of the blob.', True),
            ('algorithm', 'path', 'string',
             'The hash algorithm, one of sha1, sha512, or xxh128.', True)
        ],
        [
            (200, 'The hash of this blob, if known.', None),
            (404, 'Blob not found.', None)
        ]))
    @api_base.log_token_use
    @arg_is_blob_uuid
    def get(self, blob_uuid=None, algorithm=None, blob_from_db=None):
        if algorithm not in BLOB_HASH_ALGORITHMS:
            return sf_api.error(400, 'unknown hash algorithm')

        # Get hashes from MariaDB and find the requested algorithm
        hashes = mariadb.get_blob_hashes(str(blob_from_db.uuid))
        for h in hashes:
            if h.algorithm == algorithm and h.verification_status == 'valid':
                return h.hash_value

        # Otherwise, request a hashing of this blob and return None
        locations = blob_from_db.locations
        if not locations:
            return None
        # Locations are FQDNs (from BLOB_LOCATION refs), convert to UUID
        location_node = Node.from_db(locations[0])
        if not location_node:
            return None
        nbo_create_and_enqueue(
            str(location_node.uuid), blob_from_db.uuid,
            [nbo_tasks.verify_size_and_checksum], PRIORITY.user_waiting)


class BlobChecksumsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Search for a blob by sha512 hash.',
        [
            ('algorithm', 'path', 'string',
             'The hash algorithm, one of sha1, sha512, or xxh128.', True),
            ('hash', 'path', 'string', 'The hash to search for.', True)
        ],
        [
            (200, 'Information about a single blob.', blob_get_example),
            (400, 'Invalid hash algorithm or hash.', None),
            (404, 'Blob not found.', None)
        ]))
    @api_base.log_token_use
    def get(self, algorithm=None, hash=None):
        if algorithm not in BLOB_HASH_ALGORITHMS:
            return sf_api.error(400, 'unknown hash algorithm')
        if not hash:
            return sf_api.error(400, 'you must specify a hash')

        # O(1) lookup via idx_hash_lookup index in MariaDB
        blob_uuid = mariadb.find_blob_by_hash(algorithm, hash)
        if not blob_uuid:
            return None

        b = Blob.from_db(blob_uuid)
        if not b:
            return None
        if b.state.value != dbo.STATE_CREATED:
            return None

        out = b.external_view()
        out['instances'] = instance_usage_for_blob_uuid(b.uuid)
        return out


class BlobMetadatasEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Fetch metadata for a blob.',
        [
            ('blob_uuid', 'path', 'uuid', 'The blob to fetch metadata for.', True)
        ],
        [(200, 'Blob metadata, if any.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @arg_is_blob_uuid
    @api_base.log_token_use
    def get(self, blob_uuid=None, blob_from_db=None):
        return blob_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'blobs', 'Add metadata for a blob.',
        [
            ('blob_uuid', 'path', 'uuid', 'The blob to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
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


class BlobMetadataEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'blobs', 'Update a metadata key for an blob.',
        [
            ('blob_uuid', 'path', 'uuid', 'The blob to add a key to.', True),
            ('key', 'path', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
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
            ('blob_uuid', 'path', 'uuid', 'The blob to remove a key from.', True),
            ('key', 'path', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Blob not found.', None)],
        requires_admin=True))
    @arg_is_blob_uuid
    @api_base.log_token_use
    def delete(self, blob_uuid=None, key=None, value=None, blob_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        blob_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        blob_from_db.remove_metadata_key(key)
