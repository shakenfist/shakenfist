terraform {
  backend "local" {
    path = "/etc/sf/tfstate"
  }
}

variable "uniqifier" {
  description = "A unique string to prefix hostnames with"
}
