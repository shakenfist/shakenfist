# This example terraform requires that you have installed the
# provider from golang/terraform-provider-shakenfist...

provider "shakenfist" {
  address = "http://localhost"
  port    = 13000
}

resource "shakenfist_network" "mynet" {
  name         = "my network"
  netblock     = "192.168.68.0/24"
  provide_dhcp = true
  provide_nat  = true
}

resource "shakenfist_instance" "myinstance" {
  name   = "myinstance"
  cpus   = 1
  memory = 1

  ssh_key   = "..."
  user_data = ""

  # Terraform 0.12 introduced object types, which would be a
  # much nicer way of dowing these two, but I can't for the
  # life of me figure out how to make them work. Contributions
  # gratefully accepted!
  disks = [
    "size=8,base=cirros"
  ]
  networks = [
    "uuid=${shakenfist_network.mynet.uuid}"
  ]
}

output "mynet_output" {
  value = shakenfist_network.mynet
}

output "myinstance_output" {
  value = shakenfist_instance.myinstance
}
