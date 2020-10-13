variable "project" {
  description = "The google cloud project id to use"
}

variable "uniqifier" {
  description = "A unique string to prefix hostnames with"
}

variable "ssh_user" {
  description = "An optional ssh username to add a key to"
  default     = ""
}

variable "ssh_key" {
  description = "An optional ssh key"
  default     = ""
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
  metadata = {
    ssh-keys = "${var.ssh_user}:${var.ssh_key}"
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
  metadata = {
    ssh-keys = "${var.ssh_user}:${var.ssh_key}"
  }
}

resource "google_compute_instance" "sf_3" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-3"
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

resource "google_compute_instance" "sf_4" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-4"
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

resource "google_compute_instance" "sf_5" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-5"
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

resource "google_compute_instance" "sf_6" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-6"
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

resource "google_compute_instance" "sf_7" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-7"
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

resource "google_compute_instance" "sf_8" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-8"
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

resource "google_compute_instance" "sf_9" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-9"
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

resource "google_compute_instance" "sf_10" {
  machine_type     = "n1-standard-4"
  name             = "${var.uniqifier}sf-10"
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

output "sf_1_external" {
  value = google_compute_instance.sf_1.*.network_interface.0.access_config.0.nat_ip
}

output "sf_1_ssh_keys" {
  value = google_compute_instance.sf_1.metadata
}

output "sf_2_external" {
  value = google_compute_instance.sf_2.*.network_interface.0.access_config.0.nat_ip
}

output "sf_3_external" {
  value = google_compute_instance.sf_3.*.network_interface.0.access_config.0.nat_ip
}

output "sf_4_external" {
  value = google_compute_instance.sf_4.*.network_interface.0.access_config.0.nat_ip
}

output "sf_5_external" {
  value = google_compute_instance.sf_5.*.network_interface.0.access_config.0.nat_ip
}

output "sf_6_external" {
  value = google_compute_instance.sf_6.*.network_interface.0.access_config.0.nat_ip
}

output "sf_7_external" {
  value = google_compute_instance.sf_7.*.network_interface.0.access_config.0.nat_ip
}

output "sf_8_external" {
  value = google_compute_instance.sf_8.*.network_interface.0.access_config.0.nat_ip
}

output "sf_9_external" {
  value = google_compute_instance.sf_9.*.network_interface.0.access_config.0.nat_ip
}

output "sf_10_external" {
  value = google_compute_instance.sf_10.*.network_interface.0.access_config.0.nat_ip
}

output "sf_1_internal" {
  value = google_compute_instance.sf_1.*.network_interface.0.network_ip
}

output "sf_2_internal" {
  value = google_compute_instance.sf_2.*.network_interface.0.network_ip
}

output "sf_3_internal" {
  value = google_compute_instance.sf_3.*.network_interface.0.network_ip
}

output "sf_4_internal" {
  value = google_compute_instance.sf_4.*.network_interface.0.network_ip
}


output "sf_5_internal" {
  value = google_compute_instance.sf_5.*.network_interface.0.network_ip
}


output "sf_6_internal" {
  value = google_compute_instance.sf_6.*.network_interface.0.network_ip
}


output "sf_7_internal" {
  value = google_compute_instance.sf_7.*.network_interface.0.network_ip
}


output "sf_8_internal" {
  value = google_compute_instance.sf_8.*.network_interface.0.network_ip
}


output "sf_9_internal" {
  value = google_compute_instance.sf_9.*.network_interface.0.network_ip
}


output "sf_10_internal" {
  value = google_compute_instance.sf_10.*.network_interface.0.network_ip
}

