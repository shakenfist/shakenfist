provider "aws" {
  profile = "default"
  region  = var.region
  version = "~> 2.27"
}

variable "vpc_id" {
  description = "The AWS VPC id"
}

variable "region" {
  description = "The AWS region (e.g. us-east-1)"
}

variable "availability_zone" {
  description = "The AWS availability zone in the region (e.g. us-east-1f)"
}

variable "ssh_key_name" {
  description = "The name of an AWS ssh keypair in that region"
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

data "aws_vpc" "sf_vpc" {
  id = var.vpc_id
}

resource "aws_security_group" "sf_allow_outbound" {
  name        = "sf_allow_outbound"
  description = "Allow all outbound traffic"
  vpc_id      = data.aws_vpc.sf_vpc.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "sf_allow_outbound"
  }
}

resource "aws_security_group" "sf_allow_user" {
  name        = "sf_allow_user"
  description = "Allow inbound user traffic"
  vpc_id      = data.aws_vpc.sf_vpc.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SF API"
    from_port   = 13000
    to_port     = 13000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "sf_allow_user"
  }
}

resource "aws_security_group" "sf_allow_cluster" {
  name        = "sf_allow_cluster"
  description = "Allow SF cluster traffic"
  vpc_id      = data.aws_vpc.sf_vpc.id

  ingress {
    description = "TCP syslog"
    from_port   = 514
    to_port     = 514
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.sf_vpc.cidr_block]
  }

  ingress {
    description = "UDP syslog"
    from_port   = 514
    to_port     = 514
    protocol    = "udp"
    cidr_blocks = [data.aws_vpc.sf_vpc.cidr_block]
  }

  ingress {
    description = "VXLAN"
    from_port   = 8472
    to_port     = 8472
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "VXLAN"
    from_port   = 4789
    to_port     = 4789
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "etcd clients"
    from_port   = 2379
    to_port     = 2379
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.sf_vpc.cidr_block]
  }

  ingress {
    description = "etcd peers"
    from_port   = 2380
    to_port     = 2380
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.sf_vpc.cidr_block]
  }

  tags = {
    Name = "sf_allow_cluster"
  }
}

resource "aws_instance" "sf_1" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "c5d.metal"
  key_name      = var.ssh_key_name
  root_block_device {
    delete_on_termination = true
    volume_size           = 20
  }
  tags = {
    Name = "sf-1"
  }
  vpc_security_group_ids = [
    aws_security_group.sf_allow_outbound.id,
    aws_security_group.sf_allow_user.id,
    aws_security_group.sf_allow_cluster.id
  ]
}

resource "aws_instance" "sf_2" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "c5d.metal"
  key_name      = var.ssh_key_name
  root_block_device {
    delete_on_termination = true
    volume_size           = 20
  }
  tags = {
    Name = "sf-2"
  }
  vpc_security_group_ids = [
    aws_security_group.sf_allow_outbound.id,
    aws_security_group.sf_allow_user.id,
    aws_security_group.sf_allow_cluster.id
  ]
}

resource "aws_instance" "sf_3" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "c5d.metal"
  key_name      = var.ssh_key_name
  root_block_device {
    delete_on_termination = true
    volume_size           = 20
  }
  tags = {
    Name = "sf-3"
  }
  vpc_security_group_ids = [
    aws_security_group.sf_allow_outbound.id,
    aws_security_group.sf_allow_user.id,
    aws_security_group.sf_allow_cluster.id
  ]
}

output "sf_1_external" {
  value = aws_instance.sf_1.public_ip
}

output "sf_2_external" {
  value = aws_instance.sf_2.public_ip
}

output "sf_3_external" {
  value = aws_instance.sf_3.public_ip
}

output "sf_1_internal" {
  value = aws_instance.sf_1.private_ip
}

output "sf_2_internal" {
  value = aws_instance.sf_2.private_ip
}

output "sf_3_internal" {
  value = aws_instance.sf_3.private_ip
}
