# Copyright 2026 Michael Still and contributors
"""Read database_requests_total out of an sf-database metrics endpoint.

Small enough to be tempting to inline at each call site, which is exactly
why it is here: the functional CI suite already carries its own copy
because it is standalone and imports nothing from the server package, and
two copies of a parser is already one more than anybody wants. A test
asserts the two agree on the same sample.
"""

import re
from typing import Iterator
from typing import Optional

import requests


# One sample of a labelled metric, split into its label block and its
# value. The label block is matched greedily so that a `}` inside a label
# value does not end it early, and the value is taken as the first field
# after the block rather than the last, because the exposition format
# allows a trailing millisecond timestamp -- "metric{...} 3.0 1700000000000"
# -- and reading the last field there returns the timestamp as the counter.
#
# Splitting the line on whitespace instead, which both copies of this
# parser used to do, truncates the label block at the first space inside a
# quoted label value. Label values may contain spaces, commas, quotes and
# braces; all four are why this is a regex over the whole line rather than
# a sequence of str.split() calls.
_SAMPLE_RE = re.compile(
    r'^database_requests_total\{(?P<labels>.*)\}\s+(?P<value>\S+)')

# A single name="value" label. The value alternation consumes an escape
# sequence whole, so an escaped quote does not terminate the value and a
# crafted label cannot forge a second label -- or a whole second pair --
# out of the inside of the first one.
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

# The exposition format defines exactly these three escapes in a label
# value. A backslash before anything else is not an escape, and is left
# alone rather than being silently swallowed.
_ESCAPES = {'n': '\n', '"': '"', '\\': '\\'}


def _unescape(value: str) -> str:
    """Undo label value escaping, per the exposition format."""
    if '\\' not in value:
        return value

    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != '\\' or index + 1 >= len(value):
            out.append(char)
            index += 1
            continue
        following = value[index + 1]
        if following in _ESCAPES:
            out.append(_ESCAPES[following])
            index += 2
        else:
            out.append(char)
            index += 1
    return ''.join(out)


def parse_request_samples(text: str) -> Iterator[tuple[dict[str, str], float]]:
    """Every database_requests_total sample, as (labels, value).

    Yields nothing for a line which is not such a sample, or whose value
    is not a number, so a malformed line loses itself rather than the
    scrape it arrived in.
    """
    for line in text.splitlines():
        match = _SAMPLE_RE.match(line)
        if not match:
            continue
        try:
            value = float(match.group('value'))
        except ValueError:
            continue
        yield ({name: _unescape(raw)
                for name, raw in _LABEL_RE.findall(match.group('labels'))},
               value)


def parse_request_pairs(text: str) -> dict[tuple[str, str], float]:
    """Every (operation, caller_daemon) counter in a metrics response.

    Samples missing either label are skipped rather than being an error:
    the endpoint also serves the older unlabelled per-operation counters,
    and those cannot be attributed to a caller.
    """
    pairs: dict[tuple[str, str], float] = {}
    for labels, value in parse_request_samples(text):
        operation: Optional[str] = labels.get('operation')
        caller: Optional[str] = labels.get('caller_daemon')
        if operation is None or caller is None:
            continue
        key = (operation, caller)
        pairs[key] = pairs.get(key, 0.0) + value
    return pairs


def scrape_request_pairs(host: str, port: int, timeout: int = 5
                         ) -> dict[tuple[str, str], float]:
    """Scrape one sf-database instance. Raises on any failure."""
    resp = requests.get('http://%s:%d/metrics' % (host, port),
                        timeout=timeout)
    resp.raise_for_status()
    return parse_request_pairs(resp.text)
