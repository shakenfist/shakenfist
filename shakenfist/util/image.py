import os
import re


# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, logutils, and specific
# other util modules.
from shakenfist import logutil
from shakenfist.util import process as util_process


LOG, _ = logutil.setup(__name__)


VALUE_WITH_BRACKETS_RE = re.compile(r'.* \(([0-9]+) bytes\)')


def identify(path):
    """Work out what an image is."""

    if not os.path.exists(path):
        return {}

    out, _ = util_process.execute(None, 'qemu-img info %s' % path)

    data = {}
    for line in out.split('\n'):
        line = line.lstrip().rstrip()
        elems = line.split(': ')
        if len(elems) > 1:
            key = elems[0]
            value = ': '.join(elems[1:])

            m = VALUE_WITH_BRACKETS_RE.match(value)
            if m:
                value = float(m.group(1))

            elif value.endswith('K'):
                value = float(value[:-1]) * 1024
            elif value.endswith('M'):
                value = float(value[:-1]) * 1024 * 1024
            elif value.endswith('G'):
                value = float(value[:-1]) * 1024 * 1024 * 1024
            elif value.endswith('T'):
                value = float(value[:-1]) * 1024 * 1024 * 1024 * 1024

            try:
                data[key] = float(value)
            except Exception:
                data[key] = value

    return data


def create_cow(locks, cache_file, disk_file, disk_size):
    """Create a COW layer on top of the image cache.

    disk_size is specified in Gigabytes.
    """

    if os.path.exists(disk_file):
        return

    if disk_size:
        util_process.execute(locks,
                             'qemu-img create -b %s -f qcow2 %s %dG'
                             % (cache_file, disk_file, int(disk_size)))
    else:
        util_process.execute(locks,
                             'qemu-img create -b %s -f qcow2 %s'
                             % (cache_file, disk_file))


def create_qcow2(locks, cache_file, disk_file):
    """Make a qcow2 copy of the disk from the image cache."""

    if os.path.exists(disk_file):
        return

    util_process.execute(locks,
                         'qemu-img convert -t none -O qcow2 %s %s'
                         % (cache_file, disk_file))


def create_blank(locks, disk_file, disk_size):
    """Make an empty image."""

    if os.path.exists(disk_file):
        return

    util_process.execute(locks, 'qemu-img create -f qcow2 %s %sG'
                         % (disk_file, disk_size))


def snapshot(locks, source, destination):
    """Convert a possibly COW layered disk file into a snapshot."""

    util_process.execute(locks,
                         'qemu-img convert --force-share -O qcow2 %s %s'
                         % (source, destination))
