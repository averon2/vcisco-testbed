variable "region" {
  description = "AWS region for the Acme testbed."
  type        = string
  default     = "us-east-1"
}

variable "allowed_ssh_cidr" {
  description = "Your public IP in CIDR form (e.g. 203.0.113.4/32) for SSH access. SSM Session Manager works without this but SSH is handy for debugging."
  type        = string
}

variable "vciso_account_id" {
  description = "AWS account ID where vcisco's backend runs. Used in the cross-account trust policy so only that account can assume the readonly role."
  type        = string
}

variable "vciso_external_id" {
  description = "ExternalId value vcisco must present when assuming the cross-account role. Use a long random string; treat it as a secret."
  type        = string
  sensitive   = true
}

variable "enable_windows" {
  description = "When true, deploys an additional Windows Server workstation. Adds ~$20/mo."
  type        = bool
  default     = false
}
