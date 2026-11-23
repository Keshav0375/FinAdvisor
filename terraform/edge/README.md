# terraform/edge

Network for the gateway edge (ADR-024; deep-dive 04 s4.3, 08 s8.3). Separate Terraform root with
its own state; the MVP root in `terraform/` is untouched.

| Resource | Purpose |
|---|---|
| `google_compute_network.edge` | Custom-mode VPC (ASSUMPTION: no VPC existed, so the edge owns one) |
| `google_compute_subnetwork.edge` | `private_ip_google_access = true`, for Cloud Run Direct VPC egress `private-ranges-only` |
| `google_dns_managed_zone.run_app` | Private zone `run.app.` bound to the VPC |
| `google_dns_record_set.run_app_wildcard` | `*.run.app.` CNAME to `private.googleapis.com.` |
| `google_dns_managed_zone.private_googleapis` + record set | `private.googleapis.com.` A to 199.36.153.8-11 (own zone: the record is outside `run.app.`) |

Outputs `network` and `subnetwork` (names) feed the Cloud Run services (E2-T5).
No Cloud NAT and no Serverless VPC Access connector: `scripts/check_no_nat.sh` fails the CI job if
either resource type appears under `terraform/`. Every value is a variable (`variables.tf`).

## Checks that need no GCP project (demo mode, run in CI)

```sh
cd terraform/edge
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test            # mock_provider "google", project_id demo-finadvisor
cd ../.. && scripts/check_no_nat.sh terraform && tests/terraform/check_no_nat_test.sh
```

CI pins Terraform 1.9.8 (`terraform >= 1.7` is required for `mock_provider`). The provider lock
file is committed for linux_amd64 and darwin_arm64 (`terraform providers lock -platform=...`).

## Needs K1 (a real GCP project; not run in demo mode)

Enable the APIs once: `gcloud services enable compute.googleapis.com dns.googleapis.com --project "$PROJECT"`.
State lives in the existing bucket under its own prefix.

```sh
cd terraform/edge
terraform init -backend-config="bucket=finadvisor-tfstate" -backend-config="prefix=terraform/edge/state"
```

`versions.tf` declares an empty `backend "gcs" {}`; bucket and prefix are passed at init, so
`init -backend=false` (validate, test, CI) needs no credentials. ASSUMPTION: the edge reuses the
MVP bucket `finadvisor-tfstate` under its own prefix; the K1 owner may pick another bucket.

```sh
terraform plan  -var "project_id=$PROJECT" -out edge.tfplan   # shows private_ip_google_access = true and dns_name "run.app."
terraform apply edge.tfplan

# AC3
gcloud dns managed-zones describe finadvisor-run-app --project "$PROJECT" \
  --format="value(visibility,privateVisibilityConfig.networks[0].networkUrl)"   # private + the edge VPC
gcloud compute routers nats list --project "$PROJECT" --region us-central1      # prints nothing
```
