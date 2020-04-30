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
