import os
import re


# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, logutils, and specific
# other util modules.
from shakenfist import constants
from shakenfist import exceptions
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

    disk_size is specified in GiBs.
    """

    if os.path.exists(disk_file):
        return

    info = identify(cache_file)
    virtual_size = None
    try:
        virtual_size = int(info['virtual size'])
    except TypeError:
        pass

    if (virtual_size and disk_size and
            virtual_size > disk_size * 1024 * 1024 * 1024):
        raise exceptions.ImagesCannotShrinkException(
            'The specified size of %dgb (%d bytes) is smaller than the existing size '
            'of the image of %s bytes.'
            % (disk_size, disk_size * 1024 * 1024 * 1024, info['virtual size']))

    if disk_size:
        util_process.execute(
            locks,
            ('qemu-img create -b %s -o cluster_size=%s -f qcow2 %s %dG'
             % (cache_file, constants.QCOW2_CLUSTER_SIZE, disk_file,
                int(disk_size))),
            iopriority=util_process.PRIORITY_LOW)
    else:
        util_process.execute(
            locks,
            'qemu-img create -b %s -o cluster_size=%s -f qcow2 %s'
            % (cache_file, constants.QCOW2_CLUSTER_SIZE, disk_file),
            iopriority=util_process.PRIORITY_LOW)


def create_qcow2(locks, cache_file, disk_file, disk_size=None):
    """Make a qcow2 copy of the disk from the image cache."""

    if os.path.exists(disk_file):
        return

    util_process.execute(
        locks,
        'qemu-img convert -t none -o cluster_size=%s -O qcow2 %s %s'
        % (constants.QCOW2_CLUSTER_SIZE, cache_file, disk_file),
        iopriority=util_process.PRIORITY_LOW)
    if disk_size:
        util_process.execute(
            locks, 'qemu-img resize %s %dG' % (disk_file, int(disk_size)),
            iopriority=util_process.PRIORITY_LOW)


def create_blank(locks, disk_file, disk_size):
    """Make an empty image."""

    if os.path.exists(disk_file):
        return

    util_process.execute(
        locks, 'qemu-img create -o cluster_size=%s -f qcow2 %s %sG'
        % (constants.QCOW2_CLUSTER_SIZE, disk_file, disk_size),
        iopriority=util_process.PRIORITY_LOW)


def snapshot(locks, source, destination):
    """Convert a possibly COW layered disk file into a snapshot."""

    util_process.execute(
        locks,
        ('qemu-img convert --force-share -o cluster_size=%s -O qcow2 -c %s %s'
         % (constants.QCOW2_CLUSTER_SIZE, source, destination)),
        iopriority=util_process.PRIORITY_LOW)
