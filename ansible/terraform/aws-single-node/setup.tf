provider "aws" {
  profile = "default"
  region  = "ap-southeast-2"
  version = "~> 2.27"
}

# Unused, here to keep my terrible script happy
variable "ssh_key" {
  description = "The ssh private key file to use"
}

data "aws_ami" "ubuntu" {
  most_recent = true

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-bionic-18.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  owners = ["099720109477"] # Canonical
}

resource "aws_instance" "sf_single" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "c5d.metal"
  key_name      = "mikal-aws"
  root_block_device {
    delete_on_termination = true
    volume_size           = 20
  }
  tags = {
    Name = "sf-single"
  }
  vpc_security_group_ids = ["sg-0ff088c6b3e980ffd", "sg-0ae4a0742f580e222", "sg-0f4481fe67c40d267", "sg-080eb62cbdf6d0f16"]
}

output "sf_single_external" {
  value = aws_instance.sf_single.public_ip
}

output "sf_single_internal" {
  value = aws_instance.sf_single.private_ip
}
