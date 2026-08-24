# Copyright 2026 Michael Still and contributors
"""Read database_requests_total out of an sf-database metrics endpoint.

Small enough to be tempting to inline at each call site, which is exactly
why it is here: the functional CI suite already carries its own copy
because it is standalone and imports nothing from the server package, and
two copies of a parser is already one more than anybody wants. A test
asserts the two agree on the same sample.
"""

from typing import Optional

import requests


def parse_request_pairs(text: str) -> dict[tuple[str, str], float]:
    """Every (operation, caller_daemon) counter in a metrics response.

    Samples missing either label are skipped rather than being an error:
    the endpoint also serves the older unlabelled per-operation counters,
    and those cannot be attributed to a caller.
    """
    pairs: dict[tuple[str, str], float] = {}
    for line in text.splitlines():
        if not line.startswith('database_requests_total{'):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        labels = parts[0][len('database_requests_total{'):].rstrip('}')
        operation: Optional[str] = None
        caller: Optional[str] = None
        for label in labels.split(','):
            name, _, value = label.partition('=')
            value = value.strip('"')
            if name.strip() == 'operation':
                operation = value
            elif name.strip() == 'caller_daemon':
                caller = value
        if operation is None or caller is None:
            continue
        try:
            key = (operation, caller)
            pairs[key] = pairs.get(key, 0.0) + float(parts[-1])
        except ValueError:
            continue
    return pairs


def scrape_request_pairs(host: str, port: int, timeout: int = 5
                         ) -> dict[tuple[str, str], float]:
    """Scrape one sf-database instance. Raises on any failure."""
    resp = requests.get('http://%s:%d/metrics' % (host, port),
                        timeout=timeout)
    resp.raise_for_status()
    return parse_request_pairs(resp.text)
