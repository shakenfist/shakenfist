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
    data_source_reference = {
      kind = "image",
      uuid = nutanix_image.ubuntu_1804.metadata.uuid
    }
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
    data_source_reference = {
      kind = "image",
      uuid = nutanix_image.ubuntu_1804.metadata.uuid
    }
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
    data_source_reference = {
      kind = "image",
      uuid = nutanix_image.ubuntu_1804.metadata.uuid
    }
  }

  nic_list {
    subnet_uuid = var.subnet
  }
}
