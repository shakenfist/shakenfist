# Gluster support

Shaken Fist now supports gluster as a shared block filesystem for instance storage,
and by supports we mean "doesn't really know what disk it is using". This document
seeks to explain how that works, and why it is built the way it is. There are
possible future improvements that could be made, and those are included here as well.

## What works

The deployer knows how to install and configure glusterfs as a shared filesystem
with optional replication. This filesystem is mounted at /srv/shakenfist/instances if
GLUSTER_ENABLED is set to "1". You should note that the performance of that shared
filesystem isn't great, because it uses a fuse mount to get data from the virtual
machine to the disk.

## What doesn't work right now

### Lack of qemu support

Ubuntu has chosen to not provide qemu with glusterfs support compiled in (https://bugs.launchpad.net/ubuntu/+source/glusterfs/+bug/1274247). This means
we can't specify direct paths to glusterfs in qemu commands. In a perfect world we'd be
able to execute command lines like this:

```
qemu-img create -f qcow2 gluster://server.domain.com:24007/testvol/a.img 5G
```

We should investigate if there is a qemu PPA which improves this situation.

### Difficulty getting libvirt to work

Modern libvirts support writing directly to gluster instead of using the fuse filesystem
mount. This should perform much better, but I can't get it to work. With a libvirt.xml which includes this:

```
<disk type='network' device='disk'>
  <driver name='qemu' type='qcow2'/>
  <source protocol='gluster'
    name='shakenfist/ba6db72c-7110-40c6-99df-6449ee8b0a69/vda'>
      <host name='sf-3' port='24007' />
  </source>
  <backingStore type='file'>
    <format type='qcow2'/>
    <source file='/srv/shakenfist/image_cache/xxx.v001'/>
  </backingStore>
  <target dev='vda' bus='virtio'/>
</disk>
```

I get an error like this starting the instance:

```
libvirtd[300135]: internal error: qemu unexpectedly closed the monitor: 2020-11-05T04:32:15.672972Z qemu-system-x86_64: -blockdev {"driver":"gluster","volume":"shakenfist","path":"ba6db72c-7110-40c6-99df-6449ee8b0a69/vda","server":[{"type":"inet","host":"sf-3","port":"24007"}],"debug":4,"node-name":"libvirt-2-storage","auto-read-only":true,"discard":"unmap"}: Unknown driver 'gluster'
```

Hints welcome. To use the broken direct gluster support, append "_gluster" to your
SHAKENFIST_DISK_FORMAT environment variable in the sf.service file. You might find
https://staged-gluster-docs.readthedocs.io/en/release3.7.0beta1/Features/qemu-integration/#create-libvirt-xml-to-define-virtual-machine to be helful, as might https://docs.gluster.org/en/latest/Administrator%20Guide/Building%20QEMU%20With%20gfapi%20For%20Debian%20Based%20Systems/#building-qemu be.

## Performance via fuse mount

How does using the fuse mount affect the performance of instances? To test this, I
ran a cirros instance on raw local disk, and ran this test:

```
$ for i in `seq 1 10`
> do
>   time dd if=/dev/zero of=foo bs=1024 count=1024000 2>> out
> done
$ grep real out
real	0m 12.30s
real	0m 13.24s
real	0m 13.47s
real	0m 13.96s
real	0m 16.62s
real	0m 13.77s
real	0m 13.56s
real	0m 13.05s
real	0m 12.08s
real	0m 11.44s
```

So, writing approximately 1gb of data to the qcow2 COW layer of this VM takes a median
time of 13.355 seconds. How does this compare with gluster via the fuse mount?

```
real	0m 26.13s
real	0m 31.66s
real	0m 31.30s
real	0m 30.74s
real	0m 30.42s
real	0m 30.84s
real	0m 26.75s
real	0m 24.47s
real	0m 27.02s
real	0m 24.47s
```

So, about half the speed. Sad face.