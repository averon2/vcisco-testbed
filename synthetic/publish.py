#!/usr/bin/env python3
"""Render synthetic workstation inventory and upload it to the testbed's
inventory bucket.

We fabricate records in the shape SSM Resource Data Sync would produce, so
vcisco can read them through the same code path it uses for real hosts.

Usage:
    python synthetic/publish.py

Reads:
    synthetic/workstations.yaml

Writes (to S3):
    s3://<inventory_bucket>/synthetic/AWS:Application/<host_id>.json
    s3://<inventory_bucket>/synthetic/AWS:InstanceInformation/<host_id>.json

Bucket name is read from `terraform output -json` so this script stays in sync
with whatever the current `make up` produced.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

try:
    import yaml  # PyYAML
except ImportError:
    sys.exit("PyYAML is required. Install with: pip install pyyaml boto3")

try:
    import boto3
except ImportError:
    sys.exit("boto3 is required. Install with: pip install pyyaml boto3")


ROOT = pathlib.Path(__file__).resolve().parent.parent
TF_DIR = ROOT / "terraform"
SOURCE = ROOT / "synthetic" / "workstations.yaml"


def terraform_output() -> dict:
    """Pull outputs from the current terraform state."""
    result = subprocess.run(
        ["terraform", f"-chdir={TF_DIR}", "output", "-json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def render_application_record(host_id: str, app: dict) -> dict:
    """Shape one installed-app row the way SSM's AWS:Application type does."""
    return {
        "Name": app["name"],
        "Version": app["version"],
        "Publisher": app.get("publisher", ""),
        "InstalledTime": app.get(
            "installed_time",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
        "ResourceId": host_id,
    }


def render_instance_info(host: dict) -> dict:
    return {
        "InstanceId": host["id"],
        "ComputerName": host["hostname"],
        "PlatformName": host["os"],
        "ResourceId": host["id"],
        "_persona": host.get("persona", ""),
        "_synthetic": True,
    }


def main() -> int:
    outputs = terraform_output()
    bucket = outputs["inventory_bucket"]["value"]
    region = outputs.get("region", {}).get("value", "us-east-1")

    hosts = yaml.safe_load(SOURCE.read_text())["workstations"]

    s3 = boto3.client("s3", region_name=region)
    uploaded = 0

    for host in hosts:
        apps_payload = {
            "SchemaVersion": "1.0",
            "TypeName": "AWS:Application",
            "CaptureTime": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "Content": [
                render_application_record(host["id"], app) for app in host["apps"]
            ],
        }
        info_payload = {
            "SchemaVersion": "1.0",
            "TypeName": "AWS:InstanceInformation",
            "CaptureTime": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "Content": [render_instance_info(host)],
        }

        s3.put_object(
            Bucket=bucket,
            Key=f"synthetic/AWS:Application/{host['id']}.json",
            Body=json.dumps(apps_payload, indent=2).encode(),
            ContentType="application/json",
        )
        s3.put_object(
            Bucket=bucket,
            Key=f"synthetic/AWS:InstanceInformation/{host['id']}.json",
            Body=json.dumps(info_payload, indent=2).encode(),
            ContentType="application/json",
        )
        uploaded += 1
        print(f"  ✓ {host['id']}  {host['hostname']}  ({len(host['apps'])} apps)")

    print(f"\nUploaded {uploaded} synthetic workstations to s3://{bucket}/synthetic/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
