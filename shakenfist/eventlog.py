import copy
import flask
import json
from oslo_concurrency import lockutils
import os
from shakenfist_utilities import logs
import sqlite3
import time

from shakenfist.config import config
from shakenfist import etcd


LOG, _ = logs.setup(__name__)


VERSION = 4
CREATE_EVENT_TABLE = """CREATE TABLE IF NOT EXISTS events(
    timestamp real PRIMARY KEY, fqdn text,
    operation text, phase text, duration float, message text,
    extra text)"""
CREATE_VERSION_TABLE = """CREATE TABLE IF NOT EXISTS version(version int primary key)"""


def add_event(object_type, object_uuid, message, duration=None,
              extra=None, suppress_event_logging=False):
    timestamp = time.time()

    if not object_type or not object_uuid:
        return

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

    if not suppress_event_logging:
        log = LOG
        if extra:
            log = log.with_fields(extra)
        log.with_fields(
            {
                object_type: object_uuid,
                'fqdn': config.NODE_NAME,
                'duration': duration,
                'message': message
            }).info('Added event')

    # We use the old eventlog mechanism as a queueing system to get the logs
    # to the eventlog node.
    etcd.put('event/%s' % object_type, object_uuid, timestamp,
             {
                 'timestamp': timestamp,
                 'object_type': object_type,
                 'object_uuid': object_uuid,
                 'fqdn': config.NODE_NAME,
                 'duration': duration,
                 'message': message,
                 'extra': extra
             })


class EventLog(object):
    def __init__(self, objtype, objuuid):
        self.objtype = objtype
        self.objuuid = objuuid
        self.log = LOG.with_fields({self.objtype: self.objuuid})
        self.lock = lockutils.external_lock(
            '%s.lock' % self.objuuid,
            lock_path='/srv/shakenfist/events/%s' % self.objtype)

    def __enter__(self):
        self.dbpath = os.path.join(config.STORAGE_PATH, 'events', self.objtype,
                                   self.objuuid)
        if not os.path.exists(self.dbpath):
            self.con = None
        else:
            self._bootstrap()

        return self

    def __exit__(self, *args):
        if self.con:
            self.con.close()
            self.con = None

    def _bootstrap(self):
        with self.lock:
            if not os.path.exists(self.dbpath):
                self.log.info('Creating event log')

            self.con = sqlite3.connect(self.dbpath)
            self.con.row_factory = sqlite3.Row
            cur = self.con.cursor()

            # Check if we already have a version table with data
            cur.execute("SELECT count(name) FROM sqlite_master WHERE "
                        "type='table' AND name='version'")
            if cur.fetchone()['count(name)'] == 0:
                # We do not have a version table, skip to the latest version
                self.con.execute(CREATE_EVENT_TABLE)
                self.con.execute(CREATE_VERSION_TABLE)
                self.con.execute('INSERT INTO version VALUES (?)', (VERSION, ))
                self.con.commit()

            else:
                # Open an existing database, which _might_ require upgrade
                start_upgrade = time.time()
                cur.execute('SELECT * FROM version')
                ver = cur.fetchone()['version']
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
                    self.log.info('Upgraded database from v1 to v2')

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
                    self.log.info('Upgraded database from v2 to v3')

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
                    self.log.info('Upgraded database from v3 to v4')

                if start_ver != ver:
                    self.con.execute('UPDATE version SET version = ?', (ver,))
                    self.con.commit()
                    self.con.execute('VACUUM')
                    self.con.execute(
                            'INSERT INTO events(timestamp, message) '
                            'VALUES (%f, "Compacted database")'
                            % time.time())
                    self.log.info('Database upgrade took %.02f seconds'
                                  % (time.time() - start_upgrade))

    def write_event(self, timestamp, fqdn, duration, message, extra=None):
        if not self.con:
            dbdir = os.path.dirname(self.dbpath)
            os.makedirs(dbdir, exist_ok=True)
            self._bootstrap()

        attempts = 0
        while attempts < 3:
            try:
                self.con.execute(
                    'INSERT INTO events(timestamp, fqdn, duration, message, extra) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (timestamp, fqdn, duration, message,
                     json.dumps(extra, cls=etcd.JSONEncoderCustomTypes)))
                self.con.commit()
                return
            except sqlite3.IntegrityError:
                timestamp += 0.00001
                attempts += 1

        self.log.with_fields({
            'timestamp': timestamp,
            'fqdn': fqdn,
            'duration': duration,
            'message': message,
            'extra': extra
        }).error('Failed to record event after 3 retries')

    def read_events(self):
        if not self.con:
            return

        cur = self.con.cursor()
        cur.execute('SELECT * FROM events ORDER BY TIMESTAMP ASC')
        for row in cur.fetchall():
            yield dict(row)

    def delete(self):
        if self.con:
            self.con.close()
            self.con = None
        if os.path.exists(self.dbpath):
            os.unlink(self.dbpath)

        self.log.info('Removed event log')

    def prune_old_events(self, before_timestamp, message=None, limit=10000):
        sql = 'DELETE FROM EVENTS WHERE timestamp < %s' % before_timestamp
        if message:
            sql += ' AND MESSAGE="%s"' % message
        sql += ' LIMIT %d' % limit

        cur = self.con.cursor()
        cur.execute(sql)

        cur.execute('SELECT CHANGES()')
        changes = cur.fetchone()[0]
        if changes > 0:
            self.log.with_fields({'message': message}).info(
                'Removed %d old events' % changes)
        self.con.commit()
        if changes == limit:
            self.log.info('Vacuuming event database')
            cur.execute('VACUUM')
