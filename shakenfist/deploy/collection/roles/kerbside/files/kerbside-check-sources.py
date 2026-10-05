#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors

"""Check a rendered sources.yaml before it replaces the running one.

Usage: kerbside-check-sources.py SOURCES.yaml

Kerbside's daemon reads sources.yaml on every scrape pass, and a file it cannot
use crash-loops it (kerbside#465). The role therefore runs this as the write
task's validate command, so a bad file is refused and the old one stays in
place.

The file must load with yaml.safe_load, as Kerbside loads it, into a non-empty
list of mappings, each carrying the keys Kerbside's Shaken Fist source reads,
as non-empty strings.

The file holds the source password, so no message here ever includes a value,
or the text around a YAML error. Messages name the source and key, or the line
number, instead.

Exits 0 when the file is good, and 1 with the reasons on stderr when it is not.
"""

import sys

import yaml


REQUIRED_KEYS = ('source', 'type', 'url', 'username', 'password', 'ca_cert')


def check(path):
    """Return a list of reasons path is not a good sources.yaml."""
    try:
        with open(path) as f:
            sources = yaml.safe_load(f)
    except OSError as e:
        return ['the file could not be read: %s' % e.strerror]
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        where = ' at line %d' % (mark.line + 1) if mark else ''
        return ['the file is not valid YAML%s' % where]
    except (yaml.YAMLError, UnicodeDecodeError):
        return ['the file is not valid YAML']
    except Exception as e:
        return ['the file could not be read: %s' % type(e).__name__]

    if not isinstance(sources, list) or not sources:
        return ['the file is not a non-empty list of sources']

    errors = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append('source %d is not a mapping' % index)
            continue
        for key in REQUIRED_KEYS:
            value = source.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append('source %d has no %s, or it is not a non-empty string'
                              % (index, key))
    return errors


def main(argv):
    if len(argv) != 2:
        sys.stderr.write('usage: %s SOURCES.yaml\n' % argv[0])
        return 2

    errors = check(argv[1])
    for error in errors:
        sys.stderr.write('sources.yaml refused: %s\n' % error)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
