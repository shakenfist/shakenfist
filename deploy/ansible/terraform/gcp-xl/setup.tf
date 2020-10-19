variable "project" {
  description = "The google cloud project id to use"
}

resource "random_pet" "deployment_name" {
  separator = "-"
  length = 2
}

variable "ssh_user" {
  description = "An optional ssh username to add a key to"
  default     = ""
}

variable "ssh_key" {
  description = "An optional ssh key"
  default     = ""
}

variable "node_count" {
  description = "Number of SF nodes to create"
  default = 10
}

provider "google" {
  project = var.project
}

resource "google_compute_instance" "sf_nodes" {
  count            = var.node_count
  machine_type     = "n1-standard-4"
  name             = "${random_pet.deployment_name.id}-sf-${count.index}"
  zone             = "us-central1-b"
  min_cpu_platform = "Intel Haswell"
  boot_disk {
    initialize_params {
      image = "sf-image"
      size  = 50
    }
  }
  network_interface {
    access_config {}
    network = "default"
  }
  metadata = {
    ssh-keys = "${var.ssh_user}:${var.ssh_key}"
  }
}

output "sf_nodes_external_ip" {
  value = google_compute_instance.sf_nodes.*.network_interface.0.access_config.0.nat_ip
}

output "sf_nodes_internal_ip" {
  value = google_compute_instance.sf_nodes.*.network_interface.0.network_ip
}
