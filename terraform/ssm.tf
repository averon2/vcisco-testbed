# ─────────────────────────────────────────────────────────────
# SSM Inventory — the free, AWS-native telemetry that feeds
# vcisco. Targets every managed instance in the account, runs
# on a 30-minute schedule.
# ─────────────────────────────────────────────────────────────

resource "aws_ssm_association" "inventory" {
  name             = "AWS-GatherSoftwareInventory"
  association_name = "acme-inventory"

  targets {
    key    = "InstanceIds"
    values = ["*"]
  }

  schedule_expression = "rate(30 minutes)"

  parameters = {
    applications                = "Enabled"
    awsComponents               = "Enabled"
    networkConfig               = "Enabled"
    services                    = "Enabled"
    windowsUpdates              = "Enabled"
    instanceDetailedInformation = "Enabled"
  }
}

# ─────────────────────────────────────────────────────────────
# Resource Data Sync — dumps flattened inventory into S3 as
# JSON. vcisco can read this directly instead of paginating
# ListInventoryEntries per-instance.
# ─────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "inventory" {
  bucket_prefix = "acme-ssm-inventory-"
  force_destroy = true # teardown must be clean — no orphaned buckets.
}

resource "aws_s3_bucket_public_access_block" "inventory" {
  bucket                  = aws_s3_bucket.inventory.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "inventory" {
  bucket = aws_s3_bucket.inventory.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "SSMBucketPermissionsCheck"
        Effect    = "Allow"
        Principal = { Service = "ssm.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.inventory.arn
      },
      {
        Sid       = "SSMBucketDelivery"
        Effect    = "Allow"
        Principal = { Service = "ssm.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.inventory.arn}/*"
        Condition = {
          StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" }
        }
      },
    ]
  })
}

resource "aws_ssm_resource_data_sync" "inventory" {
  name = "acme-inventory-sync"

  s3_destination {
    bucket_name = aws_s3_bucket.inventory.id
    region      = var.region
    sync_format = "JsonSerDe"
  }

  depends_on = [aws_s3_bucket_policy.inventory]
}
