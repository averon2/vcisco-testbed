output "vciso_role_arn" {
  description = "Paste this into vcisco's 'Connect AWS' flow."
  value       = aws_iam_role.vciso.arn
}

output "vciso_external_id" {
  description = "Paste this alongside the role ARN in vcisco."
  value       = var.vciso_external_id
  sensitive   = true
}

output "inventory_bucket" {
  description = "S3 bucket where SSM Resource Data Sync deposits inventory JSON."
  value       = aws_s3_bucket.inventory.bucket
}

output "web_public_ip" {
  description = "Public IP of acme-web-01 (nginx)."
  value       = aws_instance.web.public_ip
}

output "app_public_ip" {
  description = "Public IP of acme-app-01 (Tomcat)."
  value       = aws_instance.app.public_ip
}

output "region" {
  value = var.region
}
