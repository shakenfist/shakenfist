variable "project" {
  description = "The google cloud project id to use"
}

variable "uniqifier" {
  description = "A unique string to prefix hostnames with"
}

provider "google" {
  project = var.project
}

resource "google_compute_instance" "sf_1" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-1"
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
}

resource "google_compute_instance" "sf_2" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-2"
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
}

resource "google_compute_instance" "sfdb" {
  machine_type     = "n1-standard-1"
  name             = "${var.uniqifier}sfdb"
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

output "sf_1_external" {
  value = google_compute_instance.sf_1.*.network_interface.0.access_config.0.nat_ip
}

output "sf_2_external" {
  value = google_compute_instance.sf_2.*.network_interface.0.access_config.0.nat_ip
}

output "sfdb_external" {
  value = google_compute_instance.sfdb.*.network_interface.0.access_config.0.nat_ip
}

output "sf_1_internal" {
  value = google_compute_instance.sf_1.*.network_interface.0.network_ip
}

output "sf_2_internal" {
  value = google_compute_instance.sf_2.*.network_interface.0.network_ip
}

output "sfdb_internal" {
  value = google_compute_instance.sfdb.*.network_interface.0.network_ip
}
