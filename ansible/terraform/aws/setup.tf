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

resource "aws_instance" "sfdb" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "t1.micro"
  key_name      = "mikal-aws"
  root_block_device {
    delete_on_termination = true
    volume_size           = 8
  }
  tags = {
    Name = "sfdb"
  }
  vpc_security_group_ids = ["sg-0ff088c6b3e980ffd", "sg-0008a21805a524651"]
}

resource "aws_instance" "sf_1" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "c5d.metal"
  key_name      = "mikal-aws"
  root_block_device {
    delete_on_termination = true
    volume_size           = 20
  }
  tags = {
    Name = "sf-1"
  }
  vpc_security_group_ids = ["sg-0ff088c6b3e980ffd", "sg-0ae4a0742f580e222", "sg-0f4481fe67c40d267"]
}

resource "aws_instance" "sf_2" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "c5d.metal"
  key_name      = "mikal-aws"
  root_block_device {
    delete_on_termination = true
    volume_size           = 20
  }
  tags = {
    Name = "sf-2"
  }
  vpc_security_group_ids = ["sg-0ff088c6b3e980ffd", "sg-0ae4a0742f580e222", "sg-0f4481fe67c40d267"]
}

output "sf_1_external" {
  value = aws_instance.sf_1.public_ip
}

output "sf_2_external" {
  value = aws_instance.sf_2.public_ip
}

output "sfdb_external" {
  value = aws_instance.sfdb.public_ip
}

output "sf_1_internal" {
  value = aws_instance.sf_1.private_ip
}

output "sf_2_internal" {
  value = aws_instance.sf_2.private_ip
}

output "sfdb_internal" {
  value = aws_instance.sfdb.private_ip
}
