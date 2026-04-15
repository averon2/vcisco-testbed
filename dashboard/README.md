# Testbed Control Dashboard

A small, independent Flask app that shows the live status of the
vcisco-testbed AWS environment. Not part of vcisco — this is an operator
tool for the fake customer environment.

## What it does

- Takes AWS credentials via a form. Keeps them **in the dashboard
  process's memory only** — never written to disk. Restart or click
  Disconnect to drop them.
- Validates the creds with `sts:GetCallerIdentity`.
- Reads (view-only):
  - EC2 instances tagged `Project=vcisco-testbed`
  - SSM-managed hosts and their ping status
  - The `acme-ssm-inventory-*` bucket (object count + synthetic count)
  - The `vciso-readonly` cross-account role
- Shows a rough monthly cost estimate based on running t3.micro hours.

## What it deliberately does NOT do

- No `terraform apply` or `destroy`. Those stay on the CLI where
  destructive ops belong.
- No credential persistence. A process restart wipes everything.
- No database.

## Run it

```bash
cd dashboard
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5050.

Override the port with `DASHBOARD_PORT=5555 python app.py`.

## IAM permissions the dashboard needs

Minimal read-only policy for the IAM user / role you paste in:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": [
        "sts:GetCallerIdentity",
        "ec2:DescribeInstances",
        "ec2:DescribeTags",
        "ssm:DescribeInstanceInformation",
        "s3:ListAllMyBuckets",
        "s3:ListBucket",
        "iam:GetRole"
      ], "Resource": "*" }
  ]
}
```

Don't use a root access key. Don't use anything with write permissions
unless you trust this box and browser.
