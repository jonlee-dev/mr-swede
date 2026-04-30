# Infrastructure (Terraform)

All GCP resources for the `mr-swede` repo are declared here. Modules live in `modules/` and are composed per environment under `envs/`.

## Layout

```
infra/
├── envs/
│   └── prod/           # The only environment for now — a personal/prod deployment
│       ├── backend.tf      # GCS state backend
│       ├── main.tf         # Module composition (bootstrap, Valheim VM, Lavalink VM, bot, idle watcher)
│       ├── providers.tf    # Google provider config
│       ├── variables.tf    # Input variables
│       └── versions.tf     # TF + provider version pins
└── modules/
    ├── gcp-bootstrap/      # APIs, TF state bucket, Workload Identity Federation
    ├── gcp-valheim-vm/     # Valheim VM, persistent disk, firewall, password secret
    ├── gcp-lavalink-vm/    # Lavalink VM (e2-small, no PD), firewall, password secret
    ├── gcp-bot-runtime/    # Cloud Run service, Artifact Registry, Cloud Build trigger, IAM
    └── gcp-idle-watcher/   # Multi-target Cloud Function + Scheduler (Valheim + Lavalink)
```

## Prerequisites

- Terraform `>= 1.9.0`  (see `.terraform-version`)
- `gcloud` CLI authenticated as a user with `roles/owner` or equivalent on the target project
- One-time: `gcloud auth application-default login`  (ADC for Terraform to use)

## Local workflow

```bash
cd infra/envs/prod
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your project_id, github_owner, discord_guild_id, music_command_channel_id

terraform init
terraform validate
terraform plan
terraform apply
```

## State management

State lives in GCS (`<project>-tfstate`), bootstrapped by the `gcp-bootstrap` module. The first apply runs against a local backend so the state bucket can come into existence; subsequent applies use the GCS backend after a `terraform init -migrate-state`. See `envs/prod/backend.tf` for the comment trail.

CI applies via Workload Identity Federation — no JSON keys checked in. The `gcp-bootstrap` module wires the `terraform-ci@` SA + WIF pool that GitHub Actions impersonates.
