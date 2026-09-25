# Copyright 2026 Michael Still and contributors

"""Tests for choosing the OVMF firmware of a UEFI instance (issue 4329).

The file sets and sizes below are what the ovmf packages actually ship:
Debian 12 and Ubuntu 22.04 carry both the 2 MB and the 4 MB images,
Debian 13 and Ubuntu 24.04 only the 4 MB ones. A code image boots only
with an NVRAM of its own flash size, which is why an instance that
already has an NVRAM file is held to the pair it was created with.
"""

import os
import shutil
import tempfile

from shakenfist import exceptions
from shakenfist import instance
from shakenfist.tests import base


VARS_2M = 131072
VARS_4M = 540672

LEGACY = {
    'OVMF_CODE.fd': 1966080,
    'OVMF_CODE.secboot.fd': 1966080,
    'OVMF_VARS.fd': VARS_2M,
    'OVMF_VARS.ms.fd': VARS_2M,
}
FOUR_MB = {
    'OVMF_CODE_4M.fd': 3653632,
    'OVMF_CODE_4M.secboot.fd': 3653632,
    'OVMF_VARS_4M.fd': VARS_4M,
    'OVMF_VARS_4M.ms.fd': VARS_4M,
}


class SelectOVMFFirmwareTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.ovmf_dir = os.path.join(self.tmpdir, 'OVMF')
        os.makedirs(self.ovmf_dir)
        self.nvram = os.path.join(self.tmpdir, 'nvram')

    def _install(self, files):
        for name, size in files.items():
            with open(os.path.join(self.ovmf_dir, name), 'wb') as f:
                f.truncate(size)

    def _nvram_of(self, size):
        with open(self.nvram, 'wb') as f:
            f.truncate(size)

    def _select(self, secure_boot):
        code, nvvars = instance.select_ovmf_firmware(
            secure_boot, self.nvram, ovmf_dir=self.ovmf_dir)
        return os.path.basename(code), os.path.basename(nvvars)

    def test_debian_13_only_has_4m(self):
        self._install(FOUR_MB)
        self.assertEqual(('OVMF_CODE_4M.fd', 'OVMF_VARS_4M.fd'),
                         self._select(False))
        self.assertEqual(('OVMF_CODE_4M.secboot.fd', 'OVMF_VARS_4M.ms.fd'),
                         self._select(True))

    def test_new_instance_prefers_4m_when_both_are_installed(self):
        self._install(LEGACY)
        self._install(FOUR_MB)
        self.assertEqual(('OVMF_CODE_4M.fd', 'OVMF_VARS_4M.fd'),
                         self._select(False))

    def test_legacy_only_host_still_works(self):
        self._install(LEGACY)
        self.assertEqual(('OVMF_CODE.secboot.fd', 'OVMF_VARS.ms.fd'),
                         self._select(True))

    def test_existing_2m_nvram_keeps_the_2m_code(self):
        # An instance created before this change has a 2 MB NVRAM. Handing
        # it the 4 MB code image would stop it booting.
        self._install(LEGACY)
        self._install(FOUR_MB)
        self._nvram_of(VARS_2M)
        self.assertEqual(('OVMF_CODE.fd', 'OVMF_VARS.fd'), self._select(False))

    def test_existing_4m_nvram_uses_the_4m_code(self):
        self._install(LEGACY)
        self._install(FOUR_MB)
        self._nvram_of(VARS_4M)
        self.assertEqual(('OVMF_CODE_4M.secboot.fd', 'OVMF_VARS_4M.ms.fd'),
                         self._select(True))

    def test_2m_nvram_on_a_4m_only_host_is_refused_clearly(self):
        # A 2 MB instance on a hypervisor upgraded to Debian 13 cannot boot.
        # Say so, rather than letting libvirt fail five times.
        self._install(FOUR_MB)
        self._nvram_of(VARS_2M)
        e = self.assertRaises(exceptions.UEFIFirmwareUnavailable,
                              self._select, False)
        self.assertIn(f'is {VARS_2M} bytes', str(e))
        self.assertIn('OVMF_VARS_4M.fd', str(e))

    def test_nothing_installed_names_what_was_looked_for(self):
        e = self.assertRaises(exceptions.UEFIFirmwareUnavailable,
                              self._select, False)
        self.assertIn('OVMF_CODE_4M.fd with', str(e))
        self.assertIn('OVMF_CODE.fd with', str(e))

    def test_half_a_pair_is_not_installed(self):
        # A code image is useless without VARS of the same size.
        self._install({'OVMF_CODE_4M.fd': 3653632})
        self._install(LEGACY)
        self.assertEqual(('OVMF_CODE.fd', 'OVMF_VARS.fd'), self._select(False))
