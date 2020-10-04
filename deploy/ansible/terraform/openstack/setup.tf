provider "openstack" {
}

#
# Configuration
#
variable "ssh_key_name" {
  description = "The SSH key-name to use (user to pre-configure in Openstack)"
}
variable "flavor" {
  description = "The Openstack instance flavor name eg. 2C-4GB-50GB"
}
variable "external_net" {
  description = "The Openstack external network name to use"
}
variable "uniqifier" {
  description = "A unique string to prefix hostnames with"
}

data "openstack_images_image_v2" "ubuntu" {
  name        = "Ubuntu 18.04 Bionic Beaver"
  most_recent = true
}
data "openstack_compute_flavor_v2" "sf" {
  name = var.flavor
}
data "openstack_networking_network_v2" "external-net" {
  name = var.external_net
}

#
# Resources
#

# Security Group
resource "openstack_networking_secgroup_v2" "sf" {
  name        = "shaken-fist"
  description = "Shaken Fist Cluster"
}
resource "openstack_networking_secgroup_rule_v2" "ssh" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "http" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 80
  port_range_max    = 80
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "sf-api" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 13000
  port_range_max    = 13000
  remote_ip_prefix  = "0.0.0.0/0"
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "syslog-tcp" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 514
  port_range_max    = 514
  remote_ip_prefix  = openstack_networking_subnet_v2.sf.cidr
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "syslog-udp" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "udp"
  port_range_min    = 514
  port_range_max    = 514
  remote_ip_prefix  = openstack_networking_subnet_v2.sf.cidr
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "vxlan-8472" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "udp"
  port_range_min    = 8472
  port_range_max    = 8472
  remote_ip_prefix  = openstack_networking_subnet_v2.sf.cidr
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "vxlan-4789" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "udp"
  port_range_min    = 4789
  port_range_max    = 4789
  remote_ip_prefix  = openstack_networking_subnet_v2.sf.cidr
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "etcd-clients" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 2379
  port_range_max    = 2379
  remote_ip_prefix  = openstack_networking_subnet_v2.sf.cidr
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

resource "openstack_networking_secgroup_rule_v2" "etcd-peers" {
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 2380
  port_range_max    = 2380
  remote_ip_prefix  = openstack_networking_subnet_v2.sf.cidr
  security_group_id = openstack_networking_secgroup_v2.sf.id
}

# Network
resource "openstack_networking_network_v2" "sf" {
  name           = "${var.uniqifier}sf"
  admin_state_up = "true"
}

resource "openstack_networking_subnet_v2" "sf" {
  name            = "${var.uniqifier}sf"
  network_id      = openstack_networking_network_v2.sf.id
  cidr            = "10.0.0.0/24"
  ip_version      = 4
  dns_nameservers = ["8.8.8.8", "8.8.4.4"]
}

resource "openstack_networking_router_v2" "sf" {
  name                = "${var.uniqifier}sf"
  admin_state_up      = "true"
  external_network_id = data.openstack_networking_network_v2.external-net.id
}

resource "openstack_networking_router_interface_v2" "sf-cluster" {
  router_id = openstack_networking_router_v2.sf.id
  subnet_id = openstack_networking_subnet_v2.sf.id
}

# Instances
resource "openstack_networking_floatingip_v2" "sf_1_floating_ip" {
  pool = var.external_net
}

resource "openstack_compute_instance_v2" "sf_1" {
  name      = "${var.uniqifier}sf-1"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = data.openstack_compute_flavor_v2.sf.id
  key_pair  = var.ssh_key_name
  security_groups = [openstack_networking_secgroup_v2.sf.name]
  network {
    uuid = openstack_networking_network_v2.sf.id
  }
}

resource "openstack_compute_floatingip_associate_v2" "associate_sf_1" {
  floating_ip = openstack_networking_floatingip_v2.sf_1_floating_ip.address
  instance_id = openstack_compute_instance_v2.sf_1.id
}

resource "openstack_networking_floatingip_v2" "sf_2_floating_ip" {
  pool = "ext-net"
}

resource "openstack_compute_instance_v2" "sf_2" {
  name      = "${var.uniqifier}sf-2"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = data.openstack_compute_flavor_v2.sf.id
  key_pair  = var.ssh_key_name
  security_groups = [openstack_networking_secgroup_v2.sf.name]
  network {
    uuid = openstack_networking_network_v2.sf.id
  }
}

resource "openstack_compute_floatingip_associate_v2" "associate_sf_2" {
  floating_ip = openstack_networking_floatingip_v2.sf_2_floating_ip.address
  instance_id = openstack_compute_instance_v2.sf_2.id
}

resource "openstack_networking_floatingip_v2" "sf_3_floating_ip" {
  pool = "ext-net"
}

resource "openstack_compute_instance_v2" "sf_3" {
  name      = "${var.uniqifier}sf-3"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = data.openstack_compute_flavor_v2.sf.id
  key_pair  = var.ssh_key_name
  security_groups = [openstack_networking_secgroup_v2.sf.name]
  network {
    uuid = openstack_networking_network_v2.sf.id
  }
}

resource "openstack_compute_floatingip_associate_v2" "associate_sf_3" {
  floating_ip = openstack_networking_floatingip_v2.sf_3_floating_ip.address
  instance_id = openstack_compute_instance_v2.sf_3.id
}

#
# Outputs
#
output "sf_1_internal" {
  value = openstack_compute_instance_v2.sf_1.access_ip_v4
}

output "sf_1_external" {
  value = openstack_networking_floatingip_v2.sf_1_floating_ip.address
}

output "sf_2_internal" {
  value = openstack_compute_instance_v2.sf_2.access_ip_v4
}

output "sf_2_external" {
  value = openstack_networking_floatingip_v2.sf_2_floating_ip.address
}

output "sf_3_internal" {
  value = openstack_compute_instance_v2.sf_3.access_ip_v4
}

output "sf_3_external" {
  value = openstack_networking_floatingip_v2.sf_3_floating_ip.address
}
