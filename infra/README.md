# Infrastructure (Terraform)

All GCP resources for the `mr-swede` repo are declared here. Modules live in `modules/` and are composed per environment under `envs/`.

## Layout

```
infra/
├── envs/
│   └── prod/           # The only environment for now — a personal/prod deployment
│       ├── backend.tf      # Terraform state backend (local in Phase 0 → GCS later)
│       ├── main.tf         # Module composition
│       ├── providers.tf    # Google provider config
│       ├── variables.tf    # Input variables
│       └── versions.tf     # TF + provider version pins
└── modules/            # Reusable building blocks (populated per phase)
```

## Prerequisites

- Terraform `>= 1.9.0`  (see `.terraform-version`)
- `gcloud` CLI authenticated as a user with `roles/owner` or equivalent on the target project
- One-time: `gcloud auth application-default login`  (ADC for Terraform to use)

## Local workflow

```bash
cd infra/envs/prod
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your project_id and github_owner

terraform init           # Phase 0: local backend, no cloud calls needed
terraform validate
terraform plan
terraform apply          # applies nothing in Phase 0 — main.tf is intentionally empty
```

## Phase 0 expectation

In Phase 0 there are **no managed resources**. `terraform plan` should output:
```
No changes. Your infrastructure matches the configuration.
```

That's the milestone: the skeleton is wired up correctly, nothing has been provisioned, and no money is being spent.

## State management

- **Phase 0**: local state (`terraform.tfstate` in this directory, gitignored).
- **Phase 0.5**: migrate to GCS backend once we bootstrap Workload Identity Federation. See `backend.tf` for the migration comment.

# noop change
