# vcisco-testbed

A simulated small-business AWS environment — **"Acme Widgets Co."** — used as a
fake customer to point [vcisco](https://github.com/averon2/vciso) at during
demos and development.

## The persona

> **Acme Widgets Co.** — 12 employees, small B2B manufacturer.
> Marketing website on a single box, internal CRM server, a handful of laptops.
> No dedicated IT. An MSP patches "when they remember."

That footprint is a very representative SMB. If vcisco can handle Acme, it can
handle most of the small-business market.

## What this deploys

| Thing | Real or synthetic | Why |
|---|---|---|
| `acme-web-01` (nginx on AL2023) | Real EC2 | Demonstrates the live patch loop — `SendCommand` actually upgrades nginx. |
| `acme-app-01` (Tomcat 9.0.40 on AL2023) | Real EC2 | Generates interesting CVEs (old Tomcat, Java) for the assessment view. |
| 10 "workstations" (Windows laptops) | **Synthetic** | SMB CVE risk lives on endpoints, not servers. We fabricate SSM-Inventory-shaped data for these rather than running 10 Windows EC2s at $20/mo each. |
| SSM Inventory + Resource Data Sync → S3 | Real | This is the telemetry vcisco reads. Free, AWS-native, zero-install on AL2023. |
| Cross-account IAM role (`vciso-readonly`) | Real | vcisco's SaaS account assumes this with an ExternalId to read inventory and issue patch commands. |

## Design constraints

1. **Isolated account.** Runs in its own AWS account. No blast radius into production.
2. **$0 when off.** `make down` runs `terraform destroy`. No idle resources remain —
   no stopped-but-billed EBS volumes, no $0.50/mo hosted zones, no KMS keys.
3. **On/off in one command.** `make up` / `make down`. No clicky-clicky.
4. **Believable drift.** The inventory should look like a real SMB — uneven patching,
   one user who never updates, some KEV exposure, some mundane findings.

## Cost envelope

| State | Cost/month |
|---|---|
| `make down` (destroyed) | **$0** |
| `make up` in free-tier year | **~$0–3** (S3 pennies) |
| `make up` outside free tier | **~$10–15** (2× t3.micro + S3) |
| `enable_windows = true` | adds **~$20** |

Terraform state lives in a local file you keep in the testbed directory (or in
a personal S3 bucket if you want — see `terraform/backend.tf.example`). State
itself costs nothing.

## Quickstart

1. **Create an isolated AWS account.** AWS Organizations → Add account, or a
   standalone account is fine. Log in as an admin user/role.
2. **Copy the tfvars template and fill it in:**
   ```
   cp terraform/terraform.tfvars.example terraform/terraform.tfvars
   # edit: your IP, vcisco's AWS account ID, and a random external ID
   ```
3. **Bring the environment up:**
   ```
   make up
   ```
   Takes ~2 minutes. At the end Terraform prints the `vciso_role_arn` you paste
   into vcisco's "Connect AWS" flow.
4. **Seed synthetic workstations** (optional, for a fuller demo):
   ```
   make seed
   ```
5. **When done, tear it all down:**
   ```
   make down
   ```
   Everything is gone. Billing returns to $0.

## Demo loop

1. vcisco → Integrations → Connect AWS → paste role ARN + external ID.
2. Sync pulls 2 real hosts + 10 synthetic hosts, ~60 apps, ~200 CVEs.
3. Briefing surfaces critical KEVs on the web server, a cluster of stale Chrome
   across workstations, and an end-of-life Office install on one laptop.
4. One-click "Patch nginx" action → `SendCommand` runs for real → re-scan
   shows posture improved.
5. Workstation actions (Chrome/Office/etc.) generate tickets — simulated, since
   real SMBs route those through Intune/Jamf.

## Repository layout

```
terraform/           Infrastructure as code. Apply = stand up, destroy = zero cost.
  versions.tf        Terraform + provider versions, default tags.
  variables.tf       All knobs: region, your IP, vcisco account ID, external ID.
  network.tf         VPC, public subnet, IGW, security groups.
  iam.tf             EC2 instance profile + cross-account role for vcisco.
  web.tf             acme-web-01 (nginx).
  app.tf             acme-app-01 (Tomcat).
  ssm.tf             Inventory association + Resource Data Sync → S3.
  outputs.tf         What to paste into vcisco after apply.
  terraform.tfvars.example   Fill in and rename to terraform.tfvars.

synthetic/           Fake workstation inventory (the 10 laptops).
  workstations.yaml  Personas + installed software list per persona.
  publish.py         Renders YAML into SSM-Inventory-shaped JSON, uploads to S3.

scripts/             Lifecycle helpers invoked by the Makefile.

Makefile             up / down / seed / plan / status / cost.
```

## Open design notes

- **Network:** single public subnet. No NAT gateway (saves $32/mo). Not realistic
  but cheap. The patch loop doesn't depend on private subnets.
- **State:** local tfstate for v1. If multiple people will operate the testbed,
  migrate to S3 backend later (see `backend.tf.example`).
- **Auto-drift:** a future Lambda that periodically "installs" an old package
  on synthetic workstations so re-demos show new findings. Nice-to-have; not v1.
- **Windows box:** `enable_windows = false` by default. Flip on when a prospect
  specifically asks to see the Windows patch path.
