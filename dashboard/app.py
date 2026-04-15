"""Acme Testbed control dashboard.

A small, independent Flask app that shows the live status of the
vcisco-testbed AWS environment. Completely separate from vcisco itself —
this is an operator tool for the fake customer environment, not part of
the product.

Design constraints:
  * View-only. Will not run `terraform apply` / `destroy` for you. Those
    stay on the CLI where destructive ops belong.
  * Credentials live in process memory for the life of the session only.
    Never written to disk. Clearing the session or restarting the server
    drops them.
  * One file. No database. No background jobs.
"""

from __future__ import annotations

import json as _json_builtin
import os
import pathlib
import secrets
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, render_template, request, session

try:
    import yaml  # PyYAML — optional; dashboard still works without it
except ImportError:
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKSTATIONS_YAML = ROOT / "synthetic" / "workstations.yaml"


# Canonical list of components Terraform intends to deploy. Used for the
# "Desired" view that renders before credentials are entered.
PLANNED_COMPONENTS = [
    {
        "component": "acme-web-01 (nginx)",
        "want": "t3.micro, AL2023, nginx installed, SSM reporting",
    },
    {
        "component": "acme-app-01 (Tomcat + Log4j)",
        "want": "t3.micro, AL2023, Tomcat 9.0.40, Log4j 2.14.1, SSM reporting",
    },
    {
        "component": "SSM managed (both hosts online)",
        "want": "Both EC2 hosts appear in DescribeInstanceInformation",
    },
    {
        "component": "Inventory bucket",
        "want": "acme-ssm-inventory-* S3 bucket with Resource Data Sync",
    },
    {
        "component": "Synthetic workstations seeded",
        "want": "10 fabricated hosts in s3://…/synthetic/",
    },
    {
        "component": "vcisco-readonly cross-account role",
        "want": "IAM role vcisco can assume with ExternalId",
    },
]


# Trust + permissions policies that define the vcisco-readonly role.
# Mirrored from terraform/iam.tf — update both when one changes.
def vcisco_trust_policy(vcisco_account_id: str = "VCISCO_ACCOUNT_ID",
                        external_id: str = "YOUR_EXTERNAL_ID") -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{vcisco_account_id}:root"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"sts:ExternalId": external_id}
                },
            }
        ],
    }


VCISCO_PERMISSIONS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ReadInventoryAndInstances",
            "Effect": "Allow",
            "Action": [
                "ssm:DescribeInstanceInformation",
                "ssm:ListInventoryEntries",
                "ssm:GetInventory",
                "ssm:GetInventorySchema",
                "ssm:ListResourceDataSync",
                "ec2:DescribeInstances",
                "ec2:DescribeTags",
            ],
            "Resource": "*",
        },
        {
            "Sid": "RunPatchingCommands",
            "Effect": "Allow",
            "Action": [
                "ssm:SendCommand",
                "ssm:GetCommandInvocation",
                "ssm:ListCommandInvocations",
                "ssm:ListCommands",
            ],
            "Resource": "*",
        },
        {
            "Sid": "ReadSyncedInventoryBucket",
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:ListBucket"],
            "Resource": [
                "arn:aws:s3:::acme-ssm-inventory-*",
                "arn:aws:s3:::acme-ssm-inventory-*/*",
            ],
        },
    ],
}


app = Flask(__name__)
app.secret_key = os.environ.get("DASHBOARD_SECRET", secrets.token_hex(32))

# Creds are keyed by session id and expire after this many seconds of
# inactivity. We keep them in memory only — a restart clears everything.
SESSION_TTL_SECONDS = 60 * 60
_SESSIONS: dict[str, dict[str, Any]] = {}


def _session_key() -> str:
    if "sid" not in session:
        session["sid"] = secrets.token_hex(16)
    return session["sid"]


def _get_creds() -> dict | None:
    sid = _session_key()
    entry = _SESSIONS.get(sid)
    if not entry:
        return None
    if time.time() - entry["ts"] > SESSION_TTL_SECONDS:
        _SESSIONS.pop(sid, None)
        return None
    return entry["creds"]


def _set_creds(creds: dict) -> None:
    _SESSIONS[_session_key()] = {"creds": creds, "ts": time.time()}


def _clear_creds() -> None:
    _SESSIONS.pop(_session_key(), None)


def _client(service: str):
    creds = _get_creds()
    if not creds:
        raise RuntimeError("no-credentials")
    return boto3.client(
        service,
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds.get("session_token") or None,
        region_name=creds["region"],
    )


# ─── Routes ──────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/plan")
def plan():
    """Static view of what Terraform intends to deploy + the synthetic
    workstation personas. Works without AWS credentials — lets you see the
    shape of the testbed before connecting."""
    workstations: list[dict] = []
    if WORKSTATIONS_YAML.exists() and yaml is not None:
        try:
            data = yaml.safe_load(WORKSTATIONS_YAML.read_text()) or {}
            workstations = data.get("workstations", [])
        except Exception:
            workstations = []
    return jsonify(
        {
            "components": PLANNED_COMPONENTS,
            "workstations": workstations,
        }
    )


@app.get("/api/policies")
def policies():
    """Trust + permissions policy for the vcisco-readonly role, ready to
    copy into AWS IAM. Optional query params personalize the trust policy:
      /api/policies?account=123456789012&external_id=abc123
    """
    account = request.args.get("account", "VCISCO_ACCOUNT_ID")
    external_id = request.args.get("external_id", "YOUR_EXTERNAL_ID")
    return jsonify(
        {
            "trust_policy": vcisco_trust_policy(account, external_id),
            "permissions_policy": VCISCO_PERMISSIONS_POLICY,
            "role_name": "vciso-readonly",
            "notes": [
                "Trust policy goes on the role's 'Trust relationships' tab.",
                "Permissions policy is attached inline on the same role.",
                "Replace VCISCO_ACCOUNT_ID with the AWS account ID where vcisco runs.",
                "Replace YOUR_EXTERNAL_ID with a long random secret string — paste the same value into vcisco's 'Connect AWS' flow.",
                "Terraform creates all of this for you automatically; these policies are for reference / manual setup / audits.",
            ],
        }
    )


@app.post("/api/connect")
def connect():
    data = request.get_json(force=True, silent=True) or {}
    required = ("access_key", "secret_key", "region")
    if not all(data.get(k) for k in required):
        return jsonify({"ok": False, "error": "Missing access_key, secret_key, or region."}), 400

    creds = {
        "access_key": data["access_key"].strip(),
        "secret_key": data["secret_key"].strip(),
        "session_token": (data.get("session_token") or "").strip(),
        "region": data["region"].strip(),
    }

    # Validate with a cheap call.
    try:
        sts = boto3.client(
            "sts",
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            aws_session_token=creds["session_token"] or None,
            region_name=creds["region"],
        )
        ident = sts.get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    _set_creds(creds)
    return jsonify(
        {
            "ok": True,
            "account": ident.get("Account"),
            "arn": ident.get("Arn"),
            "region": creds["region"],
        }
    )


@app.post("/api/disconnect")
def disconnect():
    _clear_creds()
    return jsonify({"ok": True})


@app.get("/api/status")
def status():
    creds = _get_creds()
    if not creds:
        return jsonify({"connected": False}), 401

    out: dict[str, Any] = {
        "connected": True,
        "region": creds["region"],
        "instances": [],
        "ssm_managed": [],
        "inventory_bucket": None,
        "vciso_role": None,
        "errors": [],
    }

    # ── EC2 instances tagged as part of the testbed ─────────────────
    try:
        ec2 = _client("ec2")
        resp = ec2.describe_instances(
            Filters=[{"Name": "tag:Project", "Values": ["vcisco-testbed"]}]
        )
        for res in resp.get("Reservations", []):
            for inst in res.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                out["instances"].append(
                    {
                        "id": inst["InstanceId"],
                        "name": tags.get("Name", ""),
                        "role": tags.get("Role", ""),
                        "type": inst.get("InstanceType"),
                        "state": inst.get("State", {}).get("Name"),
                        "public_ip": inst.get("PublicIpAddress", ""),
                        "private_ip": inst.get("PrivateIpAddress", ""),
                        "launch_time": inst.get("LaunchTime").isoformat()
                        if inst.get("LaunchTime")
                        else None,
                    }
                )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"EC2: {exc}")

    # ── SSM-managed instance inventory ──────────────────────────────
    try:
        ssm = _client("ssm")
        info = ssm.describe_instance_information()
        for i in info.get("InstanceInformationList", []):
            out["ssm_managed"].append(
                {
                    "id": i.get("InstanceId"),
                    "ping": i.get("PingStatus"),
                    "platform": i.get("PlatformName"),
                    "version": i.get("PlatformVersion"),
                    "last_ping": i.get("LastPingDateTime").isoformat()
                    if i.get("LastPingDateTime")
                    else None,
                }
            )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"SSM: {exc}")

    # ── Inventory S3 bucket ─────────────────────────────────────────
    try:
        s3 = _client("s3")
        buckets = s3.list_buckets().get("Buckets", [])
        candidate = next(
            (b for b in buckets if b["Name"].startswith("acme-ssm-inventory-")),
            None,
        )
        if candidate:
            name = candidate["Name"]
            # Cheap sample: first page of objects.
            objs = s3.list_objects_v2(Bucket=name, MaxKeys=1000).get("Contents", [])
            synthetic = [o for o in objs if o["Key"].startswith("synthetic/")]
            out["inventory_bucket"] = {
                "name": name,
                "object_count": len(objs),
                "synthetic_count": len(synthetic),
                "last_modified": max(
                    (o["LastModified"] for o in objs), default=None
                ).isoformat()
                if objs
                else None,
            }
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"S3: {exc}")

    # ── vcisco-readonly cross-account role check ────────────────────
    try:
        iam = _client("iam")
        role = iam.get_role(RoleName="vciso-readonly").get("Role", {})
        out["vciso_role"] = {
            "arn": role.get("Arn"),
            "created": role.get("CreateDate").isoformat()
            if role.get("CreateDate")
            else None,
        }
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchEntity":
            out["vciso_role"] = None
        else:
            out["errors"].append(f"IAM: {exc}")
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"IAM: {exc}")

    # ── Rough cost estimate ─────────────────────────────────────────
    running = [i for i in out["instances"] if i["state"] == "running"]
    out["cost_estimate"] = {
        "running_instances": len(running),
        # Very rough on-demand figure for t3.micro in us-east-1 outside free tier.
        "hourly_usd": round(len(running) * 0.0104, 4),
        "monthly_usd": round(len(running) * 0.0104 * 24 * 30, 2),
        "note": "Free-tier accounts: first 750 hrs/mo of t3.micro are free.",
    }

    # ── Desired-vs-actual summary ───────────────────────────────────
    # What Terraform is supposed to produce, compared to what we see.
    out["desired_state"] = _build_desired_state(out)

    return jsonify(out)


def _fmt_age(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"
    except Exception:  # noqa: BLE001
        return iso_str


def _find_instance(instances: list[dict], name: str) -> dict | None:
    return next((i for i in instances if i.get("name") == name), None)


def _ssm_for(ssm_list: list[dict], instance_id: str | None) -> dict | None:
    if not instance_id:
        return None
    return next((m for m in ssm_list if m.get("id") == instance_id), None)


def _build_desired_state(out: dict) -> list[dict]:
    """Turn the raw /api/status payload into a per-component health view.

    Each row gets:
      status  → 'live' | 'missing' | 'partial' | 'degraded'
      detail  → one-line human summary ('running · 34.201.x.x · 1h uptime')
      facts   → dict of short key/value pairs for the expanded panel
    """
    rows: list[dict] = []
    instances = out["instances"]
    ssm_list = out["ssm_managed"]
    bucket = out["inventory_bucket"]
    role = out["vciso_role"]

    # ── acme-web-01 ────────────────────────────────────────────────
    web = _find_instance(instances, "acme-web-01")
    web_ssm = _ssm_for(ssm_list, web["id"]) if web else None
    if not web:
        rows.append({
            "component": "acme-web-01 (nginx)",
            "want": "t3.micro, AL2023, nginx installed, SSM reporting",
            "status": "missing",
            "detail": "not deployed — run `make up`",
            "facts": {},
        })
    else:
        running = web["state"] == "running"
        ssm_ok = web_ssm and web_ssm.get("ping") == "Online"
        status = "live" if running and ssm_ok else ("partial" if running else "degraded")
        rows.append({
            "component": "acme-web-01 (nginx)",
            "want": "t3.micro, AL2023, nginx installed, SSM reporting",
            "status": status,
            "detail": f"{web['state']} · {web.get('public_ip') or 'no public IP'} · launched {_fmt_age(web.get('launch_time'))}",
            "facts": {
                "Instance ID": web["id"],
                "Type": web.get("type", ""),
                "Public IP": web.get("public_ip", "—"),
                "Private IP": web.get("private_ip", "—"),
                "SSM ping": (web_ssm or {}).get("ping", "not reporting"),
                "SSM platform": f"{(web_ssm or {}).get('platform','')} {(web_ssm or {}).get('version','')}".strip() or "—",
                "Last ping": _fmt_age((web_ssm or {}).get("last_ping")),
            },
        })

    # ── acme-app-01 ────────────────────────────────────────────────
    app_inst = _find_instance(instances, "acme-app-01")
    app_ssm = _ssm_for(ssm_list, app_inst["id"]) if app_inst else None
    if not app_inst:
        rows.append({
            "component": "acme-app-01 (Tomcat + Log4j)",
            "want": "t3.micro, AL2023, Tomcat 9.0.40, Log4j 2.14.1, SSM reporting",
            "status": "missing",
            "detail": "not deployed — run `make up`",
            "facts": {},
        })
    else:
        running = app_inst["state"] == "running"
        ssm_ok = app_ssm and app_ssm.get("ping") == "Online"
        status = "live" if running and ssm_ok else ("partial" if running else "degraded")
        rows.append({
            "component": "acme-app-01 (Tomcat + Log4j)",
            "want": "t3.micro, AL2023, Tomcat 9.0.40, Log4j 2.14.1, SSM reporting",
            "status": status,
            "detail": f"{app_inst['state']} · {app_inst.get('public_ip') or 'no public IP'} · launched {_fmt_age(app_inst.get('launch_time'))}",
            "facts": {
                "Instance ID": app_inst["id"],
                "Type": app_inst.get("type", ""),
                "Public IP": app_inst.get("public_ip", "—"),
                "Private IP": app_inst.get("private_ip", "—"),
                "SSM ping": (app_ssm or {}).get("ping", "not reporting"),
                "Last ping": _fmt_age((app_ssm or {}).get("last_ping")),
            },
        })

    # ── SSM reporting ──────────────────────────────────────────────
    ec2_ids = {i["id"] for i in instances}
    reporting = [m for m in ssm_list if m.get("id") in ec2_ids]
    online = [m for m in reporting if m.get("ping") == "Online"]
    if not instances:
        ssm_status, ssm_detail = "missing", "no EC2 hosts yet"
    elif len(online) == len(instances) and instances:
        ssm_status = "live"
        ssm_detail = f"{len(online)} of {len(instances)} hosts online"
    elif online:
        ssm_status = "partial"
        ssm_detail = f"{len(online)} of {len(instances)} hosts online"
    else:
        ssm_status = "degraded"
        ssm_detail = f"0 of {len(instances)} hosts pinging — agent may still be starting"

    rows.append({
        "component": "SSM telemetry",
        "want": "Every EC2 host pings SSM and reports inventory on schedule",
        "status": ssm_status,
        "detail": ssm_detail,
        "facts": {
            m.get("id", "?"): f"{m.get('ping','?')} · last {_fmt_age(m.get('last_ping'))}"
            for m in reporting
        },
    })

    # ── Inventory bucket ───────────────────────────────────────────
    if bucket is None:
        rows.append({
            "component": "Inventory S3 bucket",
            "want": "acme-ssm-inventory-* bucket with Resource Data Sync",
            "status": "missing",
            "detail": "bucket not found",
            "facts": {},
        })
    else:
        has_real = bucket["object_count"] - bucket["synthetic_count"] > 0
        status = "live" if has_real else "partial"
        detail = f"{bucket['object_count']} objects · last write {_fmt_age(bucket.get('last_modified'))}"
        if not has_real:
            detail += " · no real-host sync yet"
        rows.append({
            "component": "Inventory S3 bucket",
            "want": "acme-ssm-inventory-* bucket with Resource Data Sync",
            "status": status,
            "detail": detail,
            "facts": {
                "Bucket": bucket["name"],
                "Total objects": bucket["object_count"],
                "Real-host objects": bucket["object_count"] - bucket["synthetic_count"],
                "Synthetic objects": bucket["synthetic_count"],
                "Last modified": _fmt_age(bucket.get("last_modified")),
            },
        })

    # ── Synthetic workstations ─────────────────────────────────────
    synth_count = (bucket or {}).get("synthetic_count", 0)
    # Each workstation writes 2 objects (AWS:Application + AWS:InstanceInformation)
    expected = 10 * 2
    if synth_count == 0:
        rows.append({
            "component": "Synthetic workstations",
            "want": "10 fabricated hosts seeded into synthetic/ prefix",
            "status": "missing",
            "detail": "not seeded — run `make seed`",
            "facts": {},
        })
    elif synth_count >= expected:
        rows.append({
            "component": "Synthetic workstations",
            "want": "10 fabricated hosts seeded into synthetic/ prefix",
            "status": "live",
            "detail": f"{synth_count // 2} workstations seeded",
            "facts": {"Objects": synth_count, "Expected": expected},
        })
    else:
        rows.append({
            "component": "Synthetic workstations",
            "want": "10 fabricated hosts seeded into synthetic/ prefix",
            "status": "partial",
            "detail": f"{synth_count // 2} of 10 workstations seeded",
            "facts": {"Objects": synth_count, "Expected": expected},
        })

    # ── vcisco-readonly role ───────────────────────────────────────
    if role is None:
        rows.append({
            "component": "vcisco-readonly role",
            "want": "IAM role vcisco can assume with ExternalId",
            "status": "missing",
            "detail": "role not found",
            "facts": {},
        })
    else:
        rows.append({
            "component": "vcisco-readonly role",
            "want": "IAM role vcisco can assume with ExternalId",
            "status": "live",
            "detail": f"ready · created {_fmt_age(role.get('created'))}",
            "facts": {
                "Role ARN": role["arn"],
                "Created": _fmt_age(role.get("created")),
            },
        })

    return rows


@app.get("/api/inventory/<instance_id>")
def instance_inventory(instance_id: str):
    """Fetch installed software for one SSM-managed EC2 instance.

    This is the data vcisco would consume. We surface it in the dashboard
    so you can showcase what vcisco will see before connecting it.
    """
    creds = _get_creds()
    if not creds:
        return jsonify({"ok": False, "error": "not connected"}), 401
    try:
        ssm = _client("ssm")
        apps = []
        paginator = ssm.get_paginator("list_inventory_entries")
        pages = paginator.paginate(
            InstanceId=instance_id, TypeName="AWS:Application"
        )
        for page in pages:
            for entry in page.get("Entries", []):
                apps.append(
                    {
                        "name": entry.get("Name", ""),
                        "version": entry.get("Version", ""),
                        "publisher": entry.get("Publisher", ""),
                        "installed": entry.get("InstalledTime", ""),
                    }
                )
        apps.sort(key=lambda a: a["name"].lower())
        return jsonify({"ok": True, "instance_id": instance_id, "apps": apps})
    except ClientError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/synthetic")
def synthetic_list():
    """List synthetic workstations and their installed apps by reading the
    synthetic/ prefix in the inventory bucket."""
    import json as _json

    creds = _get_creds()
    if not creds:
        return jsonify({"ok": False, "error": "not connected"}), 401
    try:
        s3 = _client("s3")
        buckets = s3.list_buckets().get("Buckets", [])
        bucket = next(
            (b["Name"] for b in buckets if b["Name"].startswith("acme-ssm-inventory-")),
            None,
        )
        if not bucket:
            return jsonify({"ok": True, "hosts": [], "note": "no inventory bucket"})

        hosts: dict[str, dict] = {}

        # Pull AWS:InstanceInformation records (one per host)
        info_prefix = "synthetic/AWS:InstanceInformation/"
        for obj in s3.list_objects_v2(Bucket=bucket, Prefix=info_prefix).get(
            "Contents", []
        ):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            doc = _json.loads(body)
            for row in doc.get("Content", []):
                hid = row.get("InstanceId") or row.get("ResourceId")
                hosts[hid] = {
                    "id": hid,
                    "hostname": row.get("ComputerName", ""),
                    "os": row.get("PlatformName", ""),
                    "persona": row.get("_persona", ""),
                    "apps": [],
                }

        # Pull AWS:Application records (one file per host, may contain many apps)
        apps_prefix = "synthetic/AWS:Application/"
        for obj in s3.list_objects_v2(Bucket=bucket, Prefix=apps_prefix).get(
            "Contents", []
        ):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            doc = _json.loads(body)
            for row in doc.get("Content", []):
                hid = row.get("ResourceId")
                if hid not in hosts:
                    hosts[hid] = {"id": hid, "hostname": "", "os": "", "persona": "", "apps": []}
                hosts[hid]["apps"].append(
                    {
                        "name": row.get("Name", ""),
                        "version": row.get("Version", ""),
                        "publisher": row.get("Publisher", ""),
                    }
                )

        ordered = sorted(hosts.values(), key=lambda h: h["id"])
        return jsonify({"ok": True, "hosts": ordered})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", "5050"))
    print(f"Acme testbed dashboard running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
