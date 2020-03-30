variable "project" {
  description = "The google cloud project id to use"
}

provider "google" {
  project = var.project
}

variable "node_count" {
  default     = "2"
  description = "The number of nodes"
}

resource "google_compute_instance" "node" {
  machine_type     = "n1-standard-2"
  name             = "sf-${count.index + 1}"
  zone             = "us-central1-b"
  count            = var.node_count
  min_cpu_platform = "Intel Haswell"
  boot_disk {
    initialize_params {
      image = "sf-image"
    }
  }
  network_interface {
    access_config {}
    network = "default"
  }
}

resource "google_compute_instance" "db" {
  machine_type     = "n1-standard-1"
  name             = "sfdb"
  zone             = "us-central1-b"
  min_cpu_platform = "Intel Haswell"
  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-1804-lts"
    }
  }
  network_interface {
    access_config {}
    network = "default"
  }
}

output "sf_access_ip" {
  value = google_compute_instance.node.*.network_interface.0.access_config.0.nat_ip
}

output "sf_node_ip" {
  value = google_compute_instance.node.*.network_interface.0.network_ip
}

output "sfdb_access_ip" {
  value = google_compute_instance.db.*.network_interface.0.access_config.0.nat_ip
}

output "sfdb_node_ip" {
  value = google_compute_instance.db.*.network_interface.0.network_ip
}
