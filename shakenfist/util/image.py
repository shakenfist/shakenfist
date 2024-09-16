import os
import re
import shutil

import versions
from shakenfist_utilities import logs

from shakenfist import constants
from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.node import Node
from shakenfist.util import process as util_process
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


LOG, _ = logs.setup(__name__)


VALUE_WITH_BRACKETS_RE = re.compile(r'.* \(([0-9]+) bytes\)')
QEMU_REQUIRES_BACKING_FORMAT = versions.parse_version_set(">=6.0.0")


def convert_numeric_qemu_value(qemu_value):
    if not isinstance(qemu_value, str):
        return qemu_value

    if qemu_value.endswith('T'):
        qemu_value = float(qemu_value[:-1]) * constants.TiB
    elif qemu_value.endswith('G'):
        qemu_value = float(qemu_value[:-1]) * constants.GiB
    elif qemu_value.endswith('M'):
        qemu_value = float(qemu_value[:-1]) * constants.MiB
    elif qemu_value.endswith('K'):
        qemu_value = float(qemu_value[:-1]) * constants.KiB
    else:
        try:
            qemu_value = float(qemu_value)
        except ValueError:
            pass

    return qemu_value


def identify(path):
    """Work out what an image is."""

    if not os.path.exists(path):
        return {}

    out, _ = util_process.execute(None, 'qemu-img info --force-share %s' % path,
                                  suppress_command_logging=True)

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

            value = convert_numeric_qemu_value(value)

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

    if (virtual_size and disk_size and virtual_size > disk_size * constants.GiB):
        raise exceptions.ImagesCannotShrinkException(
            'The specified size of %dgb (%d bytes) is smaller than the existing size '
            'of the image of %s bytes.'
            % (disk_size, disk_size * constants.GiB, info['virtual size']))

    qemu_command = ('qemu-img create -b %(cache_file)s '
                    '-o cluster_size=%(cluster_size)s -f qcow2 -F qcow2 '
                    '%(disk_file)s')
    if Node.from_db(config.NODE_NAME).qemu_version.matches(QEMU_REQUIRES_BACKING_FORMAT):
        qemu_command += ' -F qcow2'

    if disk_size:
        qemu_command += ' %(disk_size)dG'
        util_process.execute(
            locks, qemu_command % {
                'cache_file': cache_file,
                'cluster_size': constants.QCOW2_CLUSTER_SIZE,
                'disk_file': disk_file,
                'disk_size': int(disk_size)
            },
            iopriority=util_process.PRIORITY_LOW)
    else:
        util_process.execute(
            locks, qemu_command % {
                'cache_file': cache_file,
                'cluster_size': constants.QCOW2_CLUSTER_SIZE,
                'disk_file': disk_file
            },
            iopriority=util_process.PRIORITY_LOW)


def create_qcow2(locks, cache_file, disk_file, disk_size=None):
    """Make a qcow2 copy of the disk from the image cache."""

    if os.path.exists(disk_file):
        return

    util_process.execute(
        locks,
        'qemu-img convert -t none -o cluster_size=%s -O qcow2 %s %s -c'
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


def snapshot(locks, source, destination, thin=False):
    """Convert a possibly COW layered disk file into a snapshot."""
    backing_file = identify(source).get('backing file')
    LOG.with_fields({
        'source': source,
        'backing file': backing_file}).debug('Detecting backing file for snapshot')

    if thin and backing_file:
        # NOTE(mikal): we use relative paths for the backing file when we create
        # the snapshot because it makes it easier to move the stack of layers
        # around, especially if a user downloads them. This means we need to make
        # the snapshot in the image cache directory and then move it to the right
        # place or qemu-img gets confused.
        LOG.with_fields({'source': source}).debug('Producing thin snapshot')
        backing_path, backing_uuid_with_extension = os.path.split(backing_file)
        backing_uuid = backing_uuid_with_extension.split('.')[0]

        _, destination_uuid = os.path.split(destination)
        temporary_location = os.path.join(config.STORAGE_PATH, 'image_cache',
                                          destination_uuid + '.partial')

        qemu_command = ('qemu-img convert --force-share -o '
                        'cluster_size=%(cluster_size)s -O qcow2 '
                        '-B %(backing)s')
        if config.COMPRESS_SNAPSHOTS:
            qemu_command += ' -c'
        if Node.from_db(config.NODE_NAME).qemu_version.matches(QEMU_REQUIRES_BACKING_FORMAT):
            qemu_command += ' -F qcow2'

        cmd = qemu_command % {
            'cluster_size': constants.QCOW2_CLUSTER_SIZE,
            'backing': backing_uuid_with_extension
        }

        util_process.execute(locks, ' '.join([cmd, source, temporary_location]),
                             iopriority=util_process.PRIORITY_LOW, cwd=backing_path)

        # TODO(mikal): its likely this move should be done with a low IO priority?
        shutil.move(temporary_location, destination)
        return backing_uuid

    # Produce a single file with any backing files flattened. This is also the
    # fall through from the "thin" option when no backing files are present.
    qemu_command = ('qemu-img convert --force-share -o '
                    'cluster_size=%(cluster_size)s -O qcow2')
    if config.COMPRESS_SNAPSHOTS:
        qemu_command += ' -c'

    cmd = qemu_command % {
        'cluster_size': constants.QCOW2_CLUSTER_SIZE
    }

    util_process.execute(locks, ' '.join([cmd, source, destination]),
                         iopriority=util_process.PRIORITY_LOW)
    return None
