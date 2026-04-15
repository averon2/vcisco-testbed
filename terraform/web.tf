data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

locals {
  web_user_data = <<-EOT
    #!/bin/bash
    set -eux

    # Install nginx from the AL2023 repo. The exact version it ships is fine
    # for demo purposes — vcisco will query NVD for whatever version it sees
    # and there are always open CVEs for recent nginx releases.
    dnf install -y nginx

    systemctl enable --now nginx

    cat > /usr/share/nginx/html/index.html <<HTML
    <!doctype html>
    <title>Acme Widgets Co.</title>
    <h1>Acme Widgets Co.</h1>
    <p>Quality widgets since 1987.</p>
    HTML
  EOT
}

resource "aws_instance" "web" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  user_data              = local.web_user_data

  # Small root volume — keeps cost trivial and speeds up teardown.
  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  tags = {
    Name = "acme-web-01"
    Role = "web-server"
  }
}
