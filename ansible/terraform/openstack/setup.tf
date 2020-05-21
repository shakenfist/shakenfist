provider "openstack" {
}

variable "ssh_key" {
  description = "The ssh private key file to use"
}

data "openstack_images_image_v2" "ubuntu" {
  name        = "Ubuntu 18.04 Bionic Beaver"
  most_recent = true
}

resource "openstack_networking_floatingip_v2" "sf_1_floating_ip" {
  pool = "ext-net"
}

resource "openstack_compute_instance_v2" "sf_1" {
  name      = "sf-1"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = "0fa3a4a7-a3e4-474c-be52-281c0f4e5b36" # 2C-4GB-50GB
  key_pair  = var.ssh_key
}

resource "openstack_compute_floatingip_associate_v2" "associate_sf_1" {
  floating_ip = openstack_networking_floatingip_v2.sf_1_floating_ip.address
  instance_id = openstack_compute_instance_v2.sf_1.id
}

resource "openstack_networking_floatingip_v2" "sf_2_floating_ip" {
  pool = "ext-net"
}

resource "openstack_compute_instance_v2" "sf_2" {
  name      = "sf-2"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = "0fa3a4a7-a3e4-474c-be52-281c0f4e5b36" # 2C-4GB-50GB
  key_pair  = var.ssh_key
}

resource "openstack_compute_floatingip_associate_v2" "associate_sf_2" {
  floating_ip = openstack_networking_floatingip_v2.sf_2_floating_ip.address
  instance_id = openstack_compute_instance_v2.sf_2.id
}

resource "openstack_networking_floatingip_v2" "sfdb_floating_ip" {
  pool = "ext-net"
}

resource "openstack_compute_instance_v2" "sfdb" {
  name      = "sfdb"
  image_id  = data.openstack_images_image_v2.ubuntu.id
  flavor_id = "0fa3a4a7-a3e4-474c-be52-281c0f4e5b36" # 2C-4GB-50GB
  key_pair  = var.ssh_key
}

resource "openstack_compute_floatingip_associate_v2" "associate_sfdb" {
  floating_ip = openstack_networking_floatingip_v2.sfdb_floating_ip.address
  instance_id = openstack_compute_instance_v2.sfdb.id
}

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

output "sfdb_internal" {
  value = openstack_compute_instance_v2.sfdb.access_ip_v4
}

output "sfdb_external" {
  value = openstack_networking_floatingip_v2.sfdb_floating_ip.address
}
