# Copyright 2026 Michael Still and contributors

"""Tests for the libvirt domain template.

Nothing else in this repository renders
``deploy/collection/roles/hypervisor/files/libvirt.tmpl``. It is an ansible
file copied verbatim onto each hypervisor, so a lost attribute or a malformed
comment reaches a real instance start before anything notices. Two things
make that worth guarding:

* Free page reporting is a single attribute on ``<memballoon>``. Losing it in
  an edit or a bad merge silently returns every guest to a high-water-mark
  host footprint, with no failure to point at (see issue 3920).
* A ``--`` inside an XML comment is illegal, and this file is mostly comment.
  A draft of the free page reporting NOTE contained one, which would have
  broken *every* instance start on the cluster. ``_create_domain_xml()``
  parses the rendered XML and calls ``enqueue_delete_due_error()`` when it
  does not parse, so the blast radius is instances deleted on power on.

The renders below therefore cover both machine types and both VDI types,
because the template's jinja conditionals mean no single render sees the
whole file.
"""

import os
import xml.etree.ElementTree as ET

import jinja2

from shakenfist.tests import base


TEMPLATE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles',
    'hypervisor', 'files', 'libvirt.tmpl'))


# A minimal but complete render context, matching the keyword arguments
# Instance._create_domain_xml() passes to Template.render().
BASE_CONTEXT = {
    'uuid': '11111111-2222-3333-4444-555555555555',
    'memory': 1024 * 1024,
    'vcpus': 2,
    'disks': [
        {
            'source_type': 'file',
            'present_as': 'disk',
            'type': 'qcow2',
            'cache_mode': 'none',
            'source': "<source file='/srv/shakenfist/instances/i/vda'/>",
            'backing': '',
            'device': 'vda',
            'bus': 'virtio',
        },
    ],
    'networks': [
        {
            'macaddr': '02:00:00:aa:bb:cc',
            'bridge': 'br-vxlan-1',
            'model': 'virtio',
            'mtu': 1450,
        },
    ],
    'instance_path': '/srv/shakenfist/instances/i',
    'console_port': 30000,
    'vdi_port': 30001,
    'vdi_tls_port': 30002,
    'video_model': 'qxl',
    'video_memory': 16384,
    'uefi': False,
    'secure_boot': False,
    'nvram_template_attribute': '',
    'extracommands': [],
    'machine_type': 'pc',
    'vdi_type': 'spice',
    'spice_concurrent': False,
    'spice_debug': False,
    'extradevices': [],
}

# The template's jinja conditionals gate large blocks on these, so a single
# render never sees the whole file.
VARIANTS = [
    ('i440fx spice', {}),
    ('i440fx vnc', {'vdi_type': 'vnc'}),
    ('q35 spice', {'machine_type': 'q35'}),
    ('q35 vnc uefi secureboot', {
        'machine_type': 'q35',
        'vdi_type': 'vnc',
        'uefi': True,
        'secure_boot': True,
        'nvram_template_attribute': "template='/usr/share/OVMF/OVMF_VARS.ms.fd'",
    }),
]


def render(**overrides):
    with open(TEMPLATE_PATH) as f:
        template = jinja2.Template(f.read())
    context = dict(BASE_CONTEXT)
    context.update(overrides)
    return template.render(**context)


class LibvirtTemplateTestCase(base.ShakenFistTestCase):
    def test_all_variants_are_well_formed_xml(self):
        # This is the guard for an illegal '--' in a comment, among other
        # malformations. ET.fromstring() is what _create_domain_xml() uses.
        for name, overrides in VARIANTS:
            xml = render(**overrides)
            try:
                ET.fromstring(xml)
            except ET.ParseError as e:
                self.fail(f'{name} variant does not parse as XML: {e}')

    def test_memballoon_reports_free_pages(self):
        for name, overrides in VARIANTS:
            root = ET.fromstring(render(**overrides))
            balloons = root.findall('./devices/memballoon')
            self.assertEqual(
                1, len(balloons),
                f'{name} variant should have exactly one memballoon')
            balloon = balloons[0]

            self.assertEqual(
                'virtio', balloon.get('model'),
                f'{name} variant memballoon is not virtio, so it cannot '
                'negotiate VIRTIO_BALLOON_F_REPORTING')
            self.assertEqual(
                'on', balloon.get('freePageReporting'),
                f'{name} variant memballoon has lost freePageReporting, so '
                'guests can no longer hand freed memory back to the host '
                '(issue 3920)')

    def test_memballoon_reports_statistics(self):
        # The stats period is what puts guest internal memory numbers into
        # instance usage events. It predates free page reporting and is
        # independent of it, but lives on the same element and is just as
        # easy to lose in an edit.
        for name, overrides in VARIANTS:
            root = ET.fromstring(render(**overrides))
            stats = root.find('./devices/memballoon/stats')
            self.assertIsNotNone(
                stats, f'{name} variant memballoon has no stats element')
            self.assertEqual(
                '10', stats.get('period'),
                f'{name} variant memballoon stats period is not 10 seconds')
