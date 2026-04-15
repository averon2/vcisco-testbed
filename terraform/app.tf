locals {
  app_user_data = <<-EOT
    #!/bin/bash
    set -eux

    # Java runtime — deliberately using an older Corretto line so vcisco has
    # something to flag on the JVM as well.
    dnf install -y java-11-amazon-corretto-headless

    # Install a deliberately old Tomcat for demo purposes. 9.0.40 has a good
    # stack of known CVEs that show up cleanly in NVD.
    cd /opt
    curl -fsSLO https://archive.apache.org/dist/tomcat/tomcat-9/v9.0.40/bin/apache-tomcat-9.0.40.tar.gz
    tar xzf apache-tomcat-9.0.40.tar.gz
    ln -s /opt/apache-tomcat-9.0.40 /opt/tomcat

    # Drop in an old log4j jar so Log4Shell lights up in the assessment.
    # (Purely for the demo — this host is not reachable from the internet.)
    mkdir -p /opt/acme-crm/lib
    curl -fsSLo /opt/acme-crm/lib/log4j-core-2.14.1.jar \
      https://repo1.maven.org/maven2/org/apache/logging/log4j/log4j-core/2.14.1/log4j-core-2.14.1.jar

    useradd -r -s /sbin/nologin tomcat || true
    chown -R tomcat:tomcat /opt/apache-tomcat-9.0.40 /opt/acme-crm
  EOT
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  user_data              = local.app_user_data

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
  }

  tags = {
    Name = "acme-app-01"
    Role = "app-server"
  }
}
