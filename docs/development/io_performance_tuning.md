# IO performance tuning

This page documents experiments in tuning the IO performance of Shaken Fist. It explains how we've ended up with the options we use, and what other options we considered along the way. qemu has quite a helpful guide to performance options for IO tuning at https://git.qemu.org/?p=qemu.git;a=blob;f=docs/qcow2-cache.txt , but it does not provide concrete recommendations.

## Before tuning

First off, here are some base line performance numbers before we did any tuning. All performance tests were on a Samsung Pro 980 with libvirt / kvm / qemu. All disks were a 100GB copy on write layer on top of a 30 GB virtual backing file, on an otherwise idle machine. The virtual machine had 16 vCPU and 32 GB RAM.

In the interests of the comparisons below, the untuned cluster uses writethrough caching and a cluster size of 64K:

```
dd if=/dev/zero of=test1.img bs=1G count=50 oflag=dsync
```

All numbers are the first run (incurs new cluster allocation cost, see later in this document), and then the average of the fastest three of five subsequent runs.

* Raw NVMe disk: 1,229 MB/s
* Before tuning: 611 MB/s, 655 MB/s (689, 589, 524, 572, 689)

```
hdparm -Tt /dev/vda1
```

I have only recorded the buffered disk read value, as the other value is based on caching. I've used the same average of the fastest three from a run of five that I did above:

* Raw NVMe disk: 2,572 MB/s
* Before tuning: 1,283 MB/s (695.13, 967.22, 1142.13, 1310.75, 1396.96)

Note that all experiments below are based on a single change compared to the starting state. So for example the cluster size change experiment used the default caching of writethrough.

## Disk cache mode

qemu supports a variety of disk caching modes for disks. https://documentation.suse.com/sles/11-SP4/html/SLES-kvm4zseries/cha-qemu-cachemodes.html is a good summary of the options, which are all exposed by libvirt. Modern libvirts default to writeback, which is equivalent to using a battery backed RAID controller in a physical machine. It therefore does assume a similar level of reliability from your hypervisor nodes.

Within libvirt, the caching is specified using the cache attribute to the driver element. The libvirt documentation states:

> The optional cache attribute controls the cache mechanism, possible values are "default", "none", "writethrough", "writeback", "directsync" (like "writethrough", but it bypasses the host page cache) and "unsafe" (host may cache all disk io, and sync requests from guest are ignored). Since 0.6.0, "directsync" since 0.9.5, "unsafe" since 0.9.7.

So for the libvirt.tmpl domain XML template within Shaken Fist, specifying a writethrough as a caching mode would look like this:

```
{%- for disk in disks %}
{%- if disk.bus != 'nvme' %}
<disk type='{{disk.source_type}}' device='{{disk.present_as}}'>
  <driver name='qemu' type='{{disk.type}}' cache='writethrough'/>
  {{disk.source}}
  {{disk.backing}}
  <target dev='{{disk.device}}' bus='{{disk.bus}}'/>
</disk>
{%- endif %}
{%- endfor %}
```

Some performance numbers:

```
dd if=/dev/zero of=test1.img bs=1G count=50 oflag=dsync
```

All numbers are the first run (incurs new cluster allocation cost, see later in this document), and then the average of the fastest three of five subsequent runs.

* Raw NVMe disk: 1,229 MB/s
* Cache none: 958 MB/s, 987 MB/s (948, 991, 1024, 866, 837)
* Cache writethrough: 499 MB/s, 656 MB/s (577, 735, 637, 596, 574)
* Cache writeback: 606 MB/s, 677 MB/s (686, 721, 589, 568, 625)
* Cache directsync: 626 MB/s, 823 MB/s (676, 938, 760, 771, 727)
* Cache unsafe: 857 MB/s, 1,012 MB/s (963, 1126, 948, 859, 840)

```
hdparm -Tt /dev/vda1
```

I have only recorded the buffered disk read value, as the other value is based on caching. I've used the same average of the fastest three from a run of five that I did above:

* Raw NVMe disk: 2,572 MB/s
* Cache none: 849 MB/s (535.09, 712.36, 791.02, 859.13, 899.20)
* Cache writethrough: 1,352 MB/s (598.37, 927.03, 1237.42, 1300.84, 1519.23)
* Cache writeback: 1,589.12 MB/s (756.97, 1244.58, 1433.67, 1639.21, 1694.48)
* Cache directsync: 1,038.69 MB/s (914.48, 1031.09, 1065.92, 755.77, 1019.06)
* Cache unsafe: 1,272 MB/s (656.32, 944.18, 1122.15, 1270.44, 1424.00)

What cache modes a safe in terms of data integrity? This is what https://documentation.suse.com/sles/11-SP4/html/SLES-kvm4zseries/cha-qemu-cachemodes.html has to say:

> **cache = writethrough, cache = none, cache=directsync**: These are the safest modes, and considered equally safe, given that the guest operating system is “modern and well behaved”, which means that it uses flushes as needed. If you have a suspect guest, use writethough, or directsync. Note that some file systems are not compatible with cache=none or cache=directsync, as they do not support O_DIRECT, which these cache modes relies on.

Specifically, I don't think that Shaken Fist should use any unsafe caching mode, which eliminates the aptly named *unsafe* as an option.

**Recommendation: we should convert to using cache mode "none" for instances. It provides slower read performance, but much better write performance.**

## Cluster size

qcow2 defaults to a cluster size of 64KB, and the maximum is 2MB. The value must be a power of two. The balance here is that the cluster size is the unit of allocation of disk when the disk needs to grow -- so a large cluster size will cause an image to grow larger than it might otherwise, but 2MB doesn't seem like a large overhead. However, if you are using a copy on write layer and change one byte of a fully allocated cluster, a 2MB cluster size means that 2MB must be read from the backing file, the byte changed, and then that 2MB written to the copy on write layer. That IO cost can add up depending on your workload.

https://www.ibm.com/cloud/blog/how-to-tune-qemu-l2-cache-size-and-qcow2-cluster-size has a good description of how the cluster size affects cache behaviour with qcow2, as a larger cluster size also implies that you're more likely to have in-memory cache hits and avoid extra IO operations looking up caches from disk. In the worst case, a single IO can incur the actual IO operations if the cache entries required are not currently in memory.

Pleasingly, cluster size is an attribute of the qcow2 file, not the hypervisor configuration. This makes it easy for us to run benchmarks against without having to tweak the hypervisor too much.

You tune cluster size like this:

```
qemu-img create -f qcow2 -o cluster_size=2M foo.qcow2 100G
```

First off, the backing image size changes as I change the cluster size. Remember that in a hypervisor environment where the backing image is shared between VMs, the cost of increased size here is reduced by the multiple users of th backing image.

It should be noted that you can have a different cluster size in the copy on write layer compared to the backing image, but I have not tested that as I want to keep the number of permutations here manageable.

Some performance numbers, noting that performance will vary based on the size of the disk -- that is, very large disks would benefit from higher cluster sizes. I have selected what I think is a representative size for a Shaken Fist instance in these test runs:

```
qemu-img convert -p -O qcow2 -o cluster_size=256K old.qcow2 new.qcow2
```

* Cluster size 64 K: 672 MB
* Cluster size 128 K: 1.7 GB
* Cluster size 256 K: 1.7 GB
* Cluster size 512 K: 1.7 GB
* Cluster size 1,024 K: 1.7 GB
* Cluster size 2,048 K: 1.8 GB

```
dd if=/dev/zero of=test1.img bs=1G count=50 oflag=dsync
```

All numbers are the first run (incurs new cluster allocation cost), and then the average of the fastest three of five subsequent runs.

* Raw NVMe disk: 1,229 MB/s
* Cluster size 64 K: 611 MB/s, 655 MB/s (689, 589, 524, 572, 689)
* Cluster size 128 K: 631 MB/s, 654 MB/s (465, 701, 594, 531, 666)
* Cluster size 256 K: 630 MB/s, 651 MB/s (708, 667, 580, 539, 574)
* Cluster size 512 K: 630 MB/s, 643 MB/s (666, 680, 531, 584, 560)
* Cluster size 1,024 K: 632 MB/s, 631 MB/s (734, 605, 520, 555, 541)
* Cluster size 2,048 K: 648 MB/s, 704 MB/s (732, 731, 650, 559, 558)

```
hdparm -Tt /dev/vda1
```

I have only recorded the buffered disk read value, as the other value is based on caching. I've used the same average of the fastest three from a run of five that I did above:

* Raw NVMe disk: 2,572 MB/s
* Cluster size 64 K: 1,283 MB/s (695.13, 967.22, 1142.13, 1310.75, 1396.96)
* Cluster size 128 K: 2,742 MB/s (2618.60, 3080.81, 2500.19, 2526.23, 1740.01)
* Cluster size 256 K: 3,378 MB/s (1286.86, 2167.27, 2903.52, 3399.29, 3830.55)
* Cluster size 512 K: 2,575 MB/s (1011.34, 1543.60, 2126.33, 2605.66, 2992.87)
* Cluster size 1,024 K: 2,631 MB/s (2279.93, 2329.68, 2229.67, 2622.82, 2940.58)
* Cluster size 2,048 K: 2,402 MB/s (899.56, 1517.90, 1992.42, 2447.05, 2767.72)

**Recommendation: a cluster size of 2,048K will use marginally more RAM to store the caches, but improves disk performance significantly, especially for reads.**

## Final performance

In the interests of gloating, here are our original performance numbers, compared to after tuning:


```
dd if=/dev/zero of=test1.img bs=1G count=50 oflag=dsync
```

All numbers are the first run (incurs new cluster allocation cost, see later in this document), and then the average of the fastest three of five subsequent runs.

* Raw NVMe disk: 1,229 MB/s
* Before tuning: 611 MB/s, 655 MB/s (689, 589, 524, 572, 689)
* After tuning: 992 MB/s, 991 MB/s (958, 1024, 906, 869, 838)

```
hdparm -Tt /dev/vda1
```

I've used the same average of the fastest three from a run of five that I did above:

* Raw NVMe disk: 2,572 MB/s
* Before tuning: 1,283 MB/s (695.13, 967.22, 1142.13, 1310.75, 1396.96)
* After tuning: 1,560 MB/s (717.71, 1120.44, 1394.56, 1516.47, 1770.01)
