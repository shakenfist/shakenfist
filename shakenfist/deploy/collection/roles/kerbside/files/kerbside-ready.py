#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors

"""Report whether Kerbside has fetched a Shaken Fist source's signing keys.

Usage: kerbside-ready.py SOURCE_NAME

Run with Kerbside's virtualenv Python by the kerbside role's register entry
point, which polls it until fetched_at is later than the time it recorded
before restarting Kerbside. Prints exactly one JSON object on stdout:

    {"errored": false, "fetched_at": 1791234567.123}

* errored is the source's errored flag, or null when there is no source row.
  It is reported for diagnosis only: a new source is created with errored
  false before it has ever been scraped, and a failed key fetch deliberately
  does not set it, so it does not mean the source works.
* fetched_at is when Kerbside last stored the source's console token signing
  keys (a time.time() float), or null when it never has. Kerbside writes the
  keys only after the source's CA matched the one Shaken Fist serves and its
  credential authenticated, and refreshes the time on every successful scrape,
  which is what makes it the readiness signal. The keys row is never deleted,
  so its existence alone proves nothing about this deploy.
* error is present only when the database could not be read, with errored and
  fetched_at null. It names the exception's type and never its message, which
  can quote the database URL and so its password.

Nothing printed ever includes a key or a secret: the keys themselves and the
source's credentials are never selected.

Importing kerbside.db reads /etc/kerbside/kerbside.ini (the path is fixed in
Kerbside) and prints its progress on stdout. On an INI it cannot parse,
Kerbside prints the parser's error, which can quote a line of the file, and
calls sys.exit() with status 0 (kerbside#313). The import's output is therefore
captured and discarded, so it never reaches stdout or the deploy log, and its
SystemExit is caught and reported as an error. The caller must still parse
stdout rather than trust the exit status: anything which is not this JSON
object means not ready.

Exits 0 whenever it printed the object, which includes not ready.
"""

import contextlib
import io
import json
import sys


def report(errored=None, fetched_at=None, error=None):
    out = {'errored': errored, 'fetched_at': fetched_at}
    if error:
        out['error'] = error
    print(json.dumps(out))


def load_db():
    """Import kerbside.db, or return the reason it could not be imported."""
    chatter = io.StringIO()
    try:
        with contextlib.redirect_stdout(chatter):
            from kerbside import db
        return db, None
    except SystemExit:
        return None, 'kerbside configuration did not load'
    except Exception as e:
        return None, 'importing kerbside.db failed: %s' % type(e).__name__


def read_state(db, source_name):
    """Return the source's errored flag and the keys' fetched_at, or None."""
    from sqlalchemy.orm import Session

    with Session(db.ENGINE) as session:
        source = session.query(db.Source.errored).filter(
            db.Source.name == source_name).one_or_none()
        keys = session.query(db.SfTokenKeys.fetched_at).filter(
            db.SfTokenKeys.source == source_name).one_or_none()

    errored = None if source is None or source[0] is None else bool(source[0])
    fetched_at = None if keys is None or keys[0] is None else float(keys[0])
    return errored, fetched_at


def main(argv):
    if len(argv) != 2:
        report(error='usage: kerbside-ready.py SOURCE_NAME')
        return 2

    db, error = load_db()
    if error:
        report(error=error)
        return 0

    try:
        errored, fetched_at = read_state(db, argv[1])
    except Exception as e:
        report(error='reading the database failed: %s' % type(e).__name__)
        return 0

    report(errored=errored, fetched_at=fetched_at)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
