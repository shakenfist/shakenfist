variable "username" {
  description = "A nutanix username"
}

variable "password" {
  description = "The nutanix password"
}

variable "endpoint" {
  description = "The nutanix endpoint"
}

variable "subnet" {
  description = "The UUID of the subnet to place nodes on"
}

provider "nutanix" {
  username     = var.username
  password     = var.password
  endpoint     = var.endpoint
  insecure     = true
  port         = 9440
  wait_timeout = 10
}

data "nutanix_clusters" "clusters" {
}

output "cluster" {
  value = data.nutanix_clusters.clusters.entities.0.metadata.uuid
}

resource "nutanix_subnet" "shakenfist" {
  cluster_uuid               = data.nutanix_clusters.clusters.entities.0.metadata.uuid
  name                       = "shakenfist"
  subnet_type                = "VLAN"
  prefix_length              = 24
  subnet_ip                  = "192.168.200.0"
  vlan_id                    = 3000
  ip_config_pool_list_ranges = ["192.168.200.10 192.168.200.250"]
}

resource "nutanix_image" "ubuntu_1804" {
  name       = "ubuntu-1804"
  source_uri = "https://cloud-images.ubuntu.com/bionic/current/bionic-server-cloudimg-amd64.img"
}

resource "nutanix_virtual_machine" "sfdb" {
  name         = "sfdb"
  cluster_uuid = data.nutanix_clusters.clusters.entities.0.metadata.uuid

  num_vcpus_per_socket = 2
  num_sockets          = 1
  memory_size_mib      = 2048

  disk_list {
    disk_size_mib   = 10000
    disk_size_bytes = 10485760000
  }

  nic_list {
    subnet_uuid = var.subnet
  }
}

resource "nutanix_virtual_machine" "sf_1" {
  name         = "sf-1"
  cluster_uuid = data.nutanix_clusters.clusters.entities.0.metadata.uuid

  num_vcpus_per_socket = 4
  num_sockets          = 4
  memory_size_mib      = 20480

  disk_list {
    disk_size_mib   = 100000
    disk_size_bytes = 104857600000
  }

  nic_list {
    subnet_uuid = var.subnet
  }
}

resource "nutanix_virtual_machine" "sf_2" {
  name         = "sf-2"
  cluster_uuid = data.nutanix_clusters.clusters.entities.0.metadata.uuid

  num_vcpus_per_socket = 4
  num_sockets          = 4
  memory_size_mib      = 20480

  disk_list {
    disk_size_mib   = 100000
    disk_size_bytes = 104857600000
  }

  nic_list {
    subnet_uuid = var.subnet
  }
}

output "sf_1" {
  value = nutanix_virtual_machine.sf_1.nic_list
}

output "sf_2" {
  value = nutanix_virtual_machine.sf_2.nic_list
}

output "sfdb" {
  value = nutanix_virtual_machine.sfdb.nic_list
}
