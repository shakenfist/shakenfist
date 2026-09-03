# Copyright 2026 Michael Still and contributors

"""Tests for the shipped load balancer example configurations.

docs/operator_guide/load_balancing.md prescribes health checking of
/readyz tuned to beat the API_DRAIN_GRACE drain window, and issue 4040
showed what shipping examples without it costs: a textbook sf-api drain
that a client still saw as a 502, because a config descended from the
nginx example kept routing to the draining node for the whole window.
These tests pin the shipped examples to the documented recipe so the
two cannot drift apart again.
"""

import os
import re

from shakenfist.config import config
from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _example(filename):
    with open(os.path.join(_repo_root(), 'examples', filename),
              encoding='utf-8') as f:
        return f.read()


class NginxExampleTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.conf = _example('nginx-loadbalancer.conf')

    def test_every_upstream_server_has_passive_health_checking(self):
        # FOSS nginx has no active health checks, so max_fails and
        # fail_timeout are the only way a backend leaves rotation. A bare
        # server line keeps taking traffic for its whole share of requests
        # while draining.
        servers = re.findall(r'^\s*server\s+(\S+:13000)(.*);$',
                             self.conf, re.MULTILINE)
        self.assertNotEqual(0, len(servers))
        for address, params in servers:
            self.assertIn('max_fails=', params, address)
            self.assertIn('fail_timeout=', params, address)

    def test_a_503_is_retried_against_another_backend(self):
        # A draining worker answers 503; without http_503 in
        # proxy_next_upstream that 503 goes straight back to the client
        # instead of being retried against a healthy backend.
        match = re.search(r'^\s*proxy_next_upstream\s+(.*);$',
                          self.conf, re.MULTILINE)
        self.assertIsNotNone(match)
        for token in ('error', 'timeout', 'http_503'):
            self.assertIn(token, match.group(1).split())


class ApacheExampleTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.conf = _example('apache-loadbalancer.conf')

    def test_every_balancer_member_probes_readyz(self):
        members = re.findall(r'^\s*BalancerMember\s+"([^"]+)"(.*)$',
                             self.conf, re.MULTILINE)
        self.assertNotEqual(0, len(members))
        for address, params in members:
            self.assertIn('hcmethod=GET', params, address)
            self.assertIn('hcuri=/readyz', params, address)

    def test_probe_timing_beats_the_drain_grace_window(self):
        # The load balancer must see the drain 503 and act on it inside
        # API_DRAIN_GRACE, or the worker exits before it is drained. The
        # docs prescribe interval x unhealthy-threshold comfortably inside
        # that window.
        members = re.findall(r'^\s*BalancerMember\s+"([^"]+)"(.*)$',
                             self.conf, re.MULTILINE)
        for address, params in members:
            interval = re.search(r'hcinterval=(\d+)', params)
            fails = re.search(r'hcfails=(\d+)', params)
            self.assertIsNotNone(interval, address)
            self.assertIsNotNone(fails, address)
            self.assertLess(int(interval.group(1)) * int(fails.group(1)),
                            config.API_DRAIN_GRACE, address)

    def test_the_health_check_modules_are_listed_as_required(self):
        # The hc* BalancerMember parameters are silently inert without
        # mod_proxy_hcheck loaded, which is worse than no health checking
        # at all because the config looks like it has some.
        self.assertIn('proxy_hcheck', self.conf)
        self.assertIn('watchdog', self.conf)
