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

import os
import secrets
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, render_template, request, session


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

    return jsonify(out)


if __name__ == "__main__":
    port = int(os.environ.get("DASHBOARD_PORT", "5050"))
    print(f"Acme testbed dashboard running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
