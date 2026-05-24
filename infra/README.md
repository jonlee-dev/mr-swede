# Infrastructure (Terraform)

All GCP resources for the `mr-swede` repo are declared here. Modules live in `modules/` and are composed per environment under `envs/`.

## Layout

```
infra/
├── envs/
│   └── prod/           # The only environment for now — a personal/prod deployment
│       ├── backend.tf      # GCS state backend
│       ├── main.tf         # Module composition (bootstrap, Valheim VM, bot+Lavalink VM, idle watcher, +rollback)
│       ├── providers.tf    # Google provider config
│       ├── variables.tf    # Input variables
│       └── versions.tf     # TF + provider version pins
└── modules/
    ├── gcp-bootstrap/      # APIs, TF state bucket, Workload Identity Federation
    ├── gcp-valheim-vm/     # Valheim VM, persistent disk, firewall, password secret
    ├── gcp-bot-vm/         # **Current bot home**: e2-small co-tenanting bot.service + lavalink.service
    ├── gcp-bot-runtime/    # **Legacy/rollback**: Cloud Run mr-swede (min=0 since 2026-05-12)
    ├── gcp-lavalink-vm/    # **Retired/rollback**: standalone Lavalink VM (folded into gcp-bot-vm 2026-05-12)
    └── gcp-idle-watcher/   # Cloud Function + Scheduler (Valheim only since 2026-05-12)
```

The `gcp-bot-runtime` and `gcp-lavalink-vm` modules are kept as
one-flip rollback options through the bot-vm soak; both will be
destroyed once we're confident the co-tenant setup is stable.

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
