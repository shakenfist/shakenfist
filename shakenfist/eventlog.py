import copy
import datetime
import json
import os
import pathlib
import sqlite3
import threading
import time

import flask
import grpc
from oslo_concurrency import lockutils
from shakenfist_utilities import logs  # noreorder

from shakenfist import constants
from shakenfist import etcd
from shakenfist import event_pb2
from shakenfist import event_pb2_grpc
from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.util import callstack as util_callstack
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


# This module stores some state in thread local storage.
local = threading.local()
local.sf_eventlog_client = None


def get_eventlog_client():
    c = getattr(local, 'sf_eventlog_client', None)
    if c:
        # Ensure the channel is ready
        try:
            grpc.channel_ready_future(c).result(timeout=0.5)
        except grpc.FutureTimeoutError:
            # We do not close the channel here because this cause grpc to sometimes
            # throw a traceback from another thread trying to monitor a now closed
            # channel.
            c = None

    if not c:
        local.sf_eventlog_client = grpc.insecure_channel(
            f'{config.EVENTLOG_NODE_IP}:{config.EVENTLOG_API_PORT}',
            options=[
                ('keepalive_timeout_ms', 200),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.keepalive_permit_without_calls', 1),
            ]
        )
        c = local.sf_eventlog_client
    return c


def add_event(
        event_type, object_type, object_uuid, message, duration=None,
        extra=None, suppress_event_logging=False, log_as_error=False):
    if not object_type or not object_uuid:
        return

    add_event_multi(
        event_type, [(object_type, object_uuid)], message, duration=duration,
        extra=extra, suppress_event_logging=suppress_event_logging,
        log_as_error=log_as_error)


def _add_event_multi_inner(
        event_type, log, timestamp, simpler_objects, message, duration=None,
        extra=None):
    # Attempt to send with the newer EventMultiRequest
    try:
        channel = get_eventlog_client()
        stub = event_pb2_grpc.EventServiceStub(channel)

        request = event_pb2.EventMultiRequest(
            event_type=event_type,
            timestamp=timestamp,
            fqdn=config.NODE_NAME,
            duration=duration,
            message=message,
            extra=util_json.json_dump(extra)
        )

        for object_type, object_uuid in simpler_objects:
            if not object_uuid:
                continue

            try:
                eo = request.objects.add()
                eo.object_type = object_type
                eo.object_uuid = object_uuid
            except TypeError as e:
                log.warning(
                    f'Failed to add event for {object_type} with uuid '
                    f'{object_uuid}: {e}')

        response = stub.RecordMultiEvent(request)
        if response.ack:
            return

    except grpc._channel._InactiveRpcError as e:
        if e.code().name == 'UNAVAILABLE':
            log.debug('Server unavailable')

        elif e.code().name == 'UNIMPLEMENTED':
            log.debug('Server does not support multi event with gRPC, '
                      'trying single events')
        else:
            log.info('Unknown server error while sending multi event with gRPC, '
                     'trying single events: %s' % e)

        raise e


def _add_event_inner(
        event_type, log, timestamp, simpler_objects, message, duration=None,
        extra=None):
    # Attempt to send with the older EventRequest
    failed = simpler_objects
    try:
        channel = get_eventlog_client()
        stub = event_pb2_grpc.EventServiceStub(channel)

        for object_type, object_uuid in simpler_objects:
            request = event_pb2.EventRequest(
                object_type=object_type,
                object_uuid=object_uuid,
                event_type=event_type,
                timestamp=timestamp,
                fqdn=config.NODE_NAME,
                duration=duration,
                message=message,
                extra=util_json.json_dump(extra)
            )
            response = stub.RecordEvent(request)

            if not response.ack:
                del failed[(object_type, object_uuid)]

    except grpc._channel._InactiveRpcError as e:
        log.info('Failed to send event with gRPC, adding to dead letter queue: %s' % e)

    return failed


def _add_event_dlq_inner(
        event_type, log, timestamp, simpler_objects, message, duration=None,
        extra=None):
    # We use the old eventlog mechanism as a queueing system to get the logs
    # to the eventlog node.
    for object_type, object_uuid in simpler_objects:
        etcd.put(
            'event/%s' % object_type, object_uuid, timestamp,
            {
                'timestamp': timestamp,
                'event_type': event_type,
                'object_type': object_type,
                'object_uuid': object_uuid,
                'fqdn': config.NODE_NAME,
                'duration': duration,
                'message': message,
                'extra': extra
            })


def add_event_multi(
        event_type, objects, message, duration=None, extra=None,
        suppress_event_logging=False, log_as_error=False):
    # Queue an event in etcd to get shuffled over to the long term data store
    timestamp = time.time()

    if not objects:
        return

    # Flatten objects down to a single data type, whilst also not recording
    # events for in memory only artifacts.
    simpler_objects = []
    for obj in objects:
        if isinstance(obj, tuple):
            simpler_objects.append(obj)
            continue

        if obj.in_memory_only:
            continue
        simpler_objects.append((obj.object_type, obj.uuid))

    # If we alter extra, we don't want that to leak back to the caller.
    if not extra:
        extra = {}
    else:
        extra = copy.deepcopy(extra)

    # If this event was created in the context of a request from our API, then
    # we should record the request id that caused this event.
    try:
        request_id = flask.request.environ.get('FLASK_REQUEST_ID')
    except RuntimeError:
        request_id = None
    if request_id and 'request-id' not in extra:
        extra['request-id'] = request_id

    log = LOG.with_fields({
        'event_type': event_type,
        'fqdn': config.NODE_NAME,
        'duration': duration,
        'message': message,
        'extra': extra
    })
    for object_type, object_uuid in simpler_objects:
        log = log.with_fields({object_type: object_uuid})

    if not suppress_event_logging:
        if log_as_error:
            log.error('Added event')
        else:
            log.info('Added event')

    # *** Note that the APIs are different here!
    if not config.EVENTLOG_SUPPRESS_GRPC:
        # If your eventlog server isn't setup, we get cranky. Note that this
        # happens during unit test discovery for py3 unit tests.
        if not config.EVENTLOG_NODE_IP:
            caller = util_callstack.generate_traceback()
            log.error(
                'Cannot record event, no configured server! Caller was:\n'
                f'{caller}')
            return

        # Try the new way
        attempts = 0
        while attempts < 3:
            try:
                # Here we get an exception on error, because there's only one
                # message sent.
                _add_event_multi_inner(
                    event_type, log, timestamp, simpler_objects, message,
                    duration=duration, extra=extra)
                return
            except grpc.RpcError:
                attempts += 1
                time.sleep(0.2)

        # Try the old way
        simpler_objects = _add_event_inner(
            event_type, log, timestamp, simpler_objects, message,
            duration=duration, extra=extra)
        if not simpler_objects:
            return

    # And then the dead letter queue for the remainders
    _add_event_dlq_inner(
        event_type, log, timestamp, simpler_objects, message,
        duration=duration, extra=extra)


def upgrade_data_store():
    # Upgrades for the actual underlying data store
    version_path = os.path.join(config.STORAGE_PATH, 'events', '_version')
    if os.path.exists(version_path):
        with open(version_path) as f:
            version = json.loads(f.read())['version']
    else:
        version = 1
    start_version = version
    start_time = time.time()

    if version == 1:
        # Version two is sharded, and uses an EventLog chain instead of a single
        # sqlite database. This list of objects is as existed at the time of the
        # resharding. It is assumed that the event store has not been offline
        # while the other object types were created.
        version = 2
        count = 0
        for objtype in [
                    'api-requests',
                    'artifact',
                    'blob',
                    'instance',
                    'namespace',
                    'network',
                    'networkinterface',
                    'node'
                ]:
            objroot = os.path.join(config.STORAGE_PATH, 'events', objtype)
            if os.path.exists(objroot):
                for ent in os.listdir(objroot):
                    entpath = os.path.join(objroot, ent)
                    if os.path.isdir(entpath):
                        continue
                    if ent.endswith('.lock'):
                        continue

                    if len(ent) < 3:
                        # A special case -- very short namespace and node
                        # names upset the system by clashing with the shard
                        # directory name.
                        os.rename(entpath, entpath + '.mv')
                        if os.path.exists(entpath + '.lock'):
                            os.rename(entpath + '.lock', entpath + '.mv.lock')
                        entpath += '.mv'

                    # Moving data between chunks is hard, so we don't. Instead
                    # we just use the year month that this code was written as
                    # the chunk name.
                    newdir = _shard_db_path(objtype, ent)
                    os.makedirs(newdir, exist_ok=True)
                    os.rename(entpath, os.path.join(newdir, ent + '.202303'))
                    if os.path.exists(entpath + '.lock'):
                        os.rename(entpath + '.lock',
                                  os.path.join(newdir, ent + '.lock'))

                    count += 0

        if count > 0:
            LOG.info('Resharded %d event log databases' % count)

    if version == 2:
        # Version three handled the rename of "networkinterface" to "interface".
        objroot = os.path.join(config.STORAGE_PATH, 'events')
        networkinterfaces = os.path.join(objroot, 'networkinterface')
        interfaces = os.path.join(objroot, 'interface')
        if os.path.exists(networkinterfaces):
            os.rename(networkinterfaces, interfaces)
        version = 3

    if start_version != version:
        os.makedirs(os.path.dirname(version_path), exist_ok=True)
        with open(version_path, 'w') as f:
            f.write(util_json.json_dump({'version': version}))
        LOG.info('Event datastore upgrade took %.02f seconds'
                 % (time.time() - start_time))


def _shard_db_path(objtype, objuuid):
    path = os.path.join(config.STORAGE_PATH, 'events', objtype, objuuid[0:2])
    os.makedirs(path, exist_ok=True)
    return path


def _timestamp_to_year_month(timestamp):
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.year, dt.month


class EventLog:
    # An EventLog is a meta object which manages a chain of EventLogChunks,
    # which are per-month sqlite databases. This is done to keep individual
    # database sizes manageable, and provide a form of simple log rotation.
    # Locking for a given object is handled at this level, as well as handling
    # corruption of a single chunk.
    def __init__(self, objtype, objuuid):
        self.objtype = objtype
        self.objuuid = objuuid
        self.log = LOG.with_fields({self.objtype: self.objuuid})
        self.dbdir = _shard_db_path(self.objtype, self.objuuid)

        self.write_elc_cache = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        for (year, month) in self.write_elc_cache:
            self.write_elc_cache[(year, month)].close()

    def write_event(self, event_type, timestamp, fqdn, duration, message,
                    extra=None, correlation_id=None):
        return self._write_event_inner(
            event_type, timestamp, fqdn, duration, message, extra=extra,
            correlation_id=correlation_id)

    def _write_event_inner(self, event_type, timestamp, fqdn, duration, message,
                           extra=None, correlation_id=None):
        year, month = _timestamp_to_year_month(timestamp)
        if (year, month) in self.write_elc_cache:
            elc = self.write_elc_cache[(year, month)]
        else:
            elc = EventLogChunk(self.objtype, self.objuuid, year, month)
            self.write_elc_cache[(year, month)] = elc

        try:
            elc.write_event(event_type, timestamp, fqdn, duration, message,
                            extra=extra, correlation_id=correlation_id)
            return True
        except (sqlite3.DatabaseError, exceptions.CorruptEventChunk) as e:
            if str(e) == 'database is locked':
                return False

            else:
                self.log.with_fields({
                    'chunk': '%04d%02d' % (year, month),
                    'error': e
                }).error('Chunk corrupt on write, moving aside: %s.' % e)
                os.rename(elc.dbpath, elc.dbpath + '.corrupt')
                del self.write_elc_cache[(year, month)]

                # Make a new chunk for this event
                elc = EventLogChunk(self.objtype, self.objuuid, year, month)
                self.write_elc_cache[(year, month)] = elc
                try:
                    elc.write_event(event_type, timestamp, fqdn, duration, message,
                                    extra=extra)
                except sqlite3.DatabaseError:
                    return False

    def _get_all_chunks(self):
        p = pathlib.Path(self.dbdir)
        yearmonths = []
        for ent in p.glob('%s*' % self.objuuid):
            ent = str(ent)
            if ent.endswith('.lock'):
                continue
            if ent.endswith('.corrupt'):
                continue

            try:
                _, yearmonth = ent.split('/')[-1].split('.')
                if '-' in yearmonth:
                    # Entries like -journal, -wal, -shm that internals of sqlite
                    continue
                yearmonths.append(yearmonth)
            except ValueError:
                self.log.error('Could not parse yearmonth from chunk %s' % ent)

        for yearmonth in sorted(yearmonths, reverse=True):
            year = int(yearmonth[0:4])
            month = int(yearmonth[4:])
            yield year, month

    def read_events(self, limit=100, event_type=None):
        yield from self._read_events_inner(limit=limit, event_type=event_type)

    # Use a negative limit to read everything
    def _read_events_inner(self, limit=100, event_type=None):
        count = 0

        for year, month in self._get_all_chunks():
            elc = EventLogChunk(self.objtype, self.objuuid, year, month)
            try:
                for e in elc.read_events(limit=(limit - count), event_type=event_type):
                    count += 1
                    yield e
                elc.close()

                if limit > 0 and count >= limit:
                    break

            except (sqlite3.DatabaseError, exceptions.CorruptEventChunk) as e:
                self.log.with_fields({
                    'chunk': '%04d%02d' % (year, month),
                    'error': e
                }).error('Chunk corrupt on read, moving aside: %s.' % e)
                os.rename(elc.dbpath, elc.dbpath + '.corrupt')

    def delete(self):
        self._delete_inner()

    def _delete_inner(self):
        for year, month in self._get_all_chunks():
            elc = EventLogChunk(self.objtype, self.objuuid, year, month)
            elc.delete()
        self.log.info('Removed event log')

    def prune_old_events(self, before_timestamp, event_type):
        removed = 0

        for year, month in self._get_all_chunks():
            elc = EventLogChunk(self.objtype, self.objuuid, year, month)
            try:
                this_chunk_removed = elc.prune_old_events(
                    before_timestamp, event_type)
                removed += this_chunk_removed

                if this_chunk_removed > 0:
                    if event_type != constants.EVENT_TYPE_PRUNE:
                        self._write_event_inner(
                            constants.EVENT_TYPE_PRUNE, time.time(), config.NODE_NAME, 0,
                            'pruned %d events of type %s from before %f from chunk '
                            '%04d%02d'
                            % (removed, event_type, before_timestamp, year, month))

                if elc.count_events() == 0:
                    elc.delete()
                    if event_type != constants.EVENT_TYPE_PRUNE:
                        self._write_event_inner(
                            constants.EVENT_TYPE_PRUNE, time.time(), config.NODE_NAME, 0,
                            'deleted event log chunk %04d%02d as it is now empty'
                            % (year, month))
                else:
                    elc.close()
            except (sqlite3.DatabaseError, exceptions.CorruptEventChunk) as e:
                self.log.with_fields({
                    'chunk': '%04d%02d' % (year, month),
                    'error': e
                }).warning('Chunk corrupt on prune, will retry later: %s.' % e)
                this_chunk_removed = 0

        return removed


# This is the version for an individual sqlite file
VERSION = 8
CREATE_EVENT_TABLE = [
    (
        'CREATE TABLE IF NOT EXISTS events('
        'type text, timestamp real, fqdn text, duration float, message text, '
        'extra text, correlation_id text);'
    ),
    'CREATE INDEX IF NOT EXISTS timestamp_idx ON events (timestamp);',
    'CREATE INDEX IF NOT EXISTS correlation_id_idx ON events (correlation_id);',
]
CREATE_VERSION_TABLE = """CREATE TABLE IF NOT EXISTS version(version int primary key)"""


class EventLogChunk:
    # An event log chunk is a single sqlite database which covers a specific
    # calendar month. Note that locking is done at the EventLog level, not the
    # EventLogChunk level to reduce the number of lock files we are storing
    # in the filesystem.
    def __init__(self, objtype, objuuid, year, month):
        self.objtype = objtype
        self.objuuid = objuuid
        self.chunk = '%04d%02d' % (year, month)

        self.log = LOG.with_fields({
            self.objtype: self.objuuid,
            'chunk': self.chunk
            })

        self.dbdir = _shard_db_path(self.objtype, self.objuuid)
        self.dbpath = os.path.join(self.dbdir, self.objuuid + '.' + self.chunk)
        self.bootstrapped = False

        self.lock = lockutils.external_lock(
            f'{self.objtype}-{self.objuuid}-events-{self.chunk}',
            lock_path='/run/lock', lock_file_prefix='sflock-')

    def _create_version_table(self, cur):
        # Check if we already have a version table with data
        cur.execute("SELECT count(name) FROM sqlite_master WHERE "
                    "type='table' AND name='version'")
        if cur.fetchone()['count(name)'] == 0:
            # We do not have a version table, skip to the latest version
            try:
                for statement in CREATE_EVENT_TABLE:
                    self.con.execute(statement)
                self.con.execute(CREATE_VERSION_TABLE)
                self.con.execute('INSERT INTO version VALUES (?)', (VERSION, ))
                self.con.commit()

            except sqlite3.DatabaseError as e:
                if str(e).startswith('UNIQUE constraint failed'):
                    # I'm not sure how we get here because we're protected by
                    # a lock, but this sure does feel like a race to me...
                    return False

                raise e

        return True

    def _bootstrap(self):
        sqlite_ver = sqlite3.sqlite_version_info
        os.makedirs(self.dbdir, exist_ok=True)
        if not os.path.exists(self.dbpath):
            self.log.info('Creating event log')

        self.con = sqlite3.connect(self.dbpath)
        self.con.row_factory = sqlite3.Row
        cur = self.con.cursor()

        # Ensure there's a version table in the chunk at all
        attempts = 0
        while not self._create_version_table(cur):
            attempts += 1
            if attempts > 3:
                raise exceptions.CorruptEventChunk(
                    'Failed to initialize version table three times')
            time.sleep(0.2)

        # Open an existing database, which _might_ require upgrade
        cur.execute('SELECT * FROM version')
        row = cur.fetchone()
        if not row:
            raise exceptions.CorruptEventChunk('No version rows')
        ver = dict(row).get('version')
        if not ver:
            raise exceptions.CorruptEventChunk('No version recorded')
        start_ver = ver

        if ver == 1:
            ver = 2
            self.log.info('Upgrading database from v1 to v2')
            self.con.execute(
                'ALTER TABLE events ADD COLUMN extra text')
            self.con.execute(
                'INSERT INTO events(timestamp, message) '
                'VALUES (%f, "Upgraded database to version 2")'
                % time.time())

        if ver == 2:
            ver = 3
            self.log.info('Upgrading database from v2 to v3')
            if self.objtype == 'node':
                self.con.execute(
                    'DELETE FROM events WHERE timestamp < %d AND '
                    'message = "Updated node resources"'
                    % (time.time() - config.MAX_NODE_RESOURCE_EVENT_AGE))

            self.con.execute(
                'INSERT INTO events(timestamp, message) '
                'VALUES (%f, "Upgraded database to version 3")'
                % time.time())

        if ver == 3:
            ver = 4
            self.log.info('Upgrading database from v3 to v4')
            if self.objtype == 'node':
                self.con.execute(
                    'DELETE FROM events WHERE timestamp < %d AND '
                    'message = "Updated node resources and package versions";'
                    % (time.time() - config.MAX_NODE_RESOURCE_EVENT_AGE))

            self.con.execute(
                'INSERT INTO events(timestamp, message) '
                'VALUES (%f, "Upgraded database to version 4");'
                % time.time())

        if ver == 4:
            ver = 5
            self.log.info('Upgrading database from v4 to v5')
            self.con.execute(
                'ALTER TABLE events ADD COLUMN type text')
            self.con.execute('UPDATE events SET type="historic"')
            self.con.execute(
                'INSERT INTO events(type, timestamp, message) '
                'VALUES ("audit", %f, "Upgraded database to version 5")'
                % time.time())

        if ver == 5:
            # Support for dropping columns in sqlite is relative recent, so
            # we end up having to do a bit of a dance here.
            ver = 6
            self.log.info('Upgrading database from v5 to v6 using sqlite version %s'
                          % str(sqlite_ver))
            if sqlite_ver[1] >= 35:
                self.con.execute('ALTER TABLE events DROP COLUMN operation')
                self.con.execute('ALTER TABLE events DROP COLUMN phase')

                self.con.execute(
                    'INSERT INTO events(type, timestamp, message) '
                    'VALUES ("audit", %f, "Upgraded database to version 6 in modern mode")'
                    % time.time())
            else:
                # Older versions of sqlite don't have a drop column, so we
                # have to do this the hard way.
                self.con.execute('ALTER TABLE events RENAME TO events_old')
                for statement in CREATE_EVENT_TABLE:
                    self.con.execute(statement)
                self.con.execute(
                    'INSERT INTO events (type, timestamp, fqdn, duration, message, extra) '
                    'SELECT type, timestamp, fqdn, duration, message, extra '
                    'FROM events_old')
                self.con.execute('DROP TABLE events_old')

                self.con.execute(
                    'INSERT INTO events(type, timestamp, message) '
                    'VALUES ("audit", %f, "Upgraded database to version 6 in compatibility mode")'
                    % time.time())

        if ver == 6:
            # Timestamp is no longer the primary key, its an index. You can't
            # just drop the constraint, you need to re-write the table.
            ver = 7
            self.log.info('Upgrading database from v6 to v7')
            self.con.execute('ALTER TABLE events RENAME TO events_old;')
            for statement in CREATE_EVENT_TABLE:
                self.con.execute(statement)
            self.con.execute('INSERT INTO events SELECT * FROM events_old;')
            self.con.execute('DROP TABLE events_old')

            self.con.execute(
                'INSERT INTO events(timestamp, message) '
                'VALUES (%f, "Upgraded database to version 7");'
                % time.time())

        if ver == 7:
            # We add a correlation id to events with an associated index.
            ver = 8
            self.log.info('Upgrading database from v7 to v8')
            self.con.execute('ALTER TABLE events ADD COLUMN correlation_id text;')
            self.con.execute('CREATE INDEX IF NOT EXISTS correlation_id_idx ON '
                             'events (correlation_id);')

            self.con.execute(
                'INSERT INTO events(type, timestamp, message) '
                'VALUES ("audit", %f, "Upgraded database to version 8")'
                % time.time())

        if start_ver != ver:
            self.con.execute('UPDATE version SET version = ?', (ver,))
            self.con.commit()
            self.con.execute('VACUUM')
            self.con.execute(
                    'INSERT INTO events(type, timestamp, message) '
                    'VALUES ("audit", %f, "Compacted database")'
                    % time.time())

    def close(self):
        if self.bootstrapped:
            self.con.close()

    def write_event(self, event_type, timestamp, fqdn, duration, message,
                    extra=None, correlation_id=None):
        with self.lock:
            self._write_event_inner(
                event_type, timestamp, fqdn, duration, message, extra=extra,
                correlation_id=correlation_id)

    def _write_event_inner(self, event_type, timestamp, fqdn, duration, message,
                           extra=None, correlation_id=None):
        if not self.bootstrapped:
            self._bootstrap()

        self.con.execute(
            'INSERT INTO events(type, timestamp, fqdn, duration, message, '
            'extra, correlation_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (event_type, timestamp, fqdn, duration, message,
             util_json.json_dump(extra), correlation_id))
        self.con.commit()

    def read_events(self, limit=100, event_type=None):
        with self.lock:
            if not self.bootstrapped:
                self._bootstrap()

            sql = 'SELECT * FROM events '
            if event_type:
                if event_type not in constants.EVENT_TYPES:
                    raise exceptions.InvalidEventType()
                sql += 'WHERE type = "%s" ' % event_type
            sql += 'ORDER BY TIMESTAMP DESC LIMIT %d' % limit

            cur = self.con.cursor()
            cur.execute(sql)
            for row in cur.fetchall():
                out = dict(row)
                if out.get('extra'):
                    try:
                        out['extra'] = json.loads(out['extra'])
                    except json.decoder.JSONDecodeError:
                        pass
                yield out

    def count_events(self):
        with self.lock:
            if not self.bootstrapped:
                self._bootstrap()

            cur = self.con.cursor()
            cur.execute('SELECT COUNT(*) FROM events')
            return cur.fetchone()[0]

    def delete(self):
        self.close()
        if os.path.exists(self.dbpath):
            os.unlink(self.dbpath)
        if os.path.exist(self.lock.path):
            os.unlink(self.lock.path)
        self.log.info('Removed event log chunk')

    def prune_old_events(self, before_timestamp, event_type):
        with self.lock:
            if not self.bootstrapped:
                self._bootstrap()

            sql = ('DELETE FROM EVENTS WHERE timestamp < %s AND TYPE="%s"'
                   % (before_timestamp, event_type))

            cur = self.con.cursor()
            cur.execute(sql)

            cur.execute('SELECT CHANGES()')
            changes = cur.fetchone()[0]
            if changes > 0:
                self.log.with_fields({
                    'before_timestamp': before_timestamp,
                    'event_type': event_type
                    }).info('Removed %d old events' % changes)
                self.con.commit()
                cur.execute('VACUUM')
            return changes
