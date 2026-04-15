# ─────────────────────────────────────────────────────────────
# EC2 instance role — lets the SSM agent talk to the SSM service
# and collect inventory. This is what makes "AL2023 + SSM" work
# with zero host-side configuration.
# ─────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "acme-ec2-ssm"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm_managed" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "acme-ec2-ssm"
  role = aws_iam_role.instance.name
}

# ─────────────────────────────────────────────────────────────
# Cross-account role — vcisco's production AWS account assumes
# this to read inventory and issue patch commands. The external
# ID stops the confused-deputy problem if someone else learns
# the role ARN.
# ─────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "vciso_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.vciso_account_id}:root"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.vciso_external_id]
    }
  }
}

data "aws_iam_policy_document" "vciso_permissions" {
  statement {
    sid    = "ReadInventoryAndInstances"
    effect = "Allow"
    actions = [
      "ssm:DescribeInstanceInformation",
      "ssm:ListInventoryEntries",
      "ssm:GetInventory",
      "ssm:GetInventorySchema",
      "ssm:ListResourceDataSync",
      "ec2:DescribeInstances",
      "ec2:DescribeTags",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "RunPatchingCommands"
    effect = "Allow"
    actions = [
      "ssm:SendCommand",
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
      "ssm:ListCommands",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "ReadSyncedInventoryBucket"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.inventory.arn,
      "${aws_s3_bucket.inventory.arn}/*",
    ]
  }
}

resource "aws_iam_role" "vciso" {
  name               = "vciso-readonly"
  assume_role_policy = data.aws_iam_policy_document.vciso_assume.json
}

resource "aws_iam_role_policy" "vciso" {
  name   = "vciso-inventory-access"
  role   = aws_iam_role.vciso.id
  policy = data.aws_iam_policy_document.vciso_permissions.json
}
