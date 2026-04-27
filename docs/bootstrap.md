# One-time bootstrap (Phase 0.5)

This is the procedure for the **very first** Terraform apply. After it succeeds, every subsequent apply runs in GitHub Actions via Workload Identity Federation — no humans need local `gcloud` credentials to change infra.

You only run this once per project, ever.

## What this creates

- All GCP APIs we'll need across the project (free to enable).
- A GCS bucket to hold Terraform state (`<project_id>-tfstate`).
- A Workload Identity Pool + Provider trusting GitHub's OIDC issuer.
- A `terraform-ci@<project>.iam.gserviceaccount.com` service account that GitHub Actions will impersonate.
- Project-level IAM bindings granting that SA the roles needed to manage everything else.

## Prerequisites

- A GCP project with billing enabled.
- `gcloud` CLI installed and logged in as a user with `roles/owner` on that project.
- Terraform `>= 1.9.0` locally.
- The GitHub repo already exists (private or public, both work).

## Procedure

### 1. Authenticate locally

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <project_id>
```

`application-default login` sets up ADC (Application Default Credentials) — this is what the Terraform `google` provider reads when running on your laptop.

### 2. Configure variables

```bash
cd infra/envs/prod
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars:
#   project_id   = "mr-swede-prod"           # your actual project
#   github_owner = "your-github-username"    # or org name
#   github_repo  = "mr-swede"                # repo name without owner
```

`terraform.tfvars` is in `.gitignore` — keep it out of git even if it has no secrets.

### 3. Apply with local backend

```bash
terraform init                    # local backend, creates .terraform/ locally
terraform plan                    # review what will be created
terraform apply                   # confirm with "yes"
```

Expected output on success includes:

```
Outputs:
state_bucket_name = "mr-swede-prod-tfstate"
workload_identity_provider = "projects/.../workloadIdentityPools/github-actions-pool/providers/github-actions-provider"
terraform_ci_service_account_email = "terraform-ci@mr-swede-prod.iam.gserviceaccount.com"
```

**Write these three outputs down — you need them in step 4 and 5.**

### 4. Migrate state to the new GCS bucket

Edit `infra/envs/prod/backend.tf`:

- Comment out the `backend "local"` block.
- Uncomment the `backend "gcs"` block.
- Replace `REPLACE-ME-project-id-tfstate` with the `state_bucket_name` output from step 3.

Then:

```bash
terraform init -migrate-state
# When prompted, answer "yes" to copy existing state into GCS.
```

After this, you can `rm terraform.tfstate terraform.tfstate.backup` — the canonical state is now in GCS.

### 5. Configure GitHub repository variables

In the GitHub repo:
**Settings → Secrets and variables → Actions → Variables tab → New repository variable**

| Variable name | Value |
|---|---|
| `GCP_PROJECT_ID` | `<project_id>` from step 2 |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `workload_identity_provider` output from step 3 |
| `GCP_TERRAFORM_CI_SA` | `terraform_ci_service_account_email` output from step 3 |

These are *variables*, not *secrets* — none of these values are sensitive (the WIF provider path is public; only presenting a valid GitHub OIDC token from the right repo can make use of it).

### 6. (Recommended) Set up a `prod` GitHub Environment with approval

**Settings → Environments → New environment → `prod`**

Toggle **Required reviewers** and add yourself. From now on, any `terraform apply` job that targets `environment: prod` (ours does) will pause for your approval before actually applying. This is a cheap insurance policy against a rogue PR merging and auto-applying.

### 7. Verify

Push a trivial no-op change to the `master` branch under `infra/` (e.g., reformat a comment). In the GitHub Actions UI you should see:

1. `fmt` ✅
2. `validate` ✅
3. `plan` ✅ — running under the WIF-provided credentials
4. `apply` — gated by the `prod` environment approval

Approve the apply; watch it report "No changes" if nothing meaningful was edited.

### 8. Commit and push

```bash
cd infra/envs/prod
# verify the backend.tf changes
git add backend.tf .terraform.lock.hcl
# DO NOT commit terraform.tfvars — it's in .gitignore for a reason
```

## After bootstrap — Phase 1 apply

The same `infra/envs/prod` root now also calls `module "valheim_vm"`. The
**second** apply (whether local or via GitHub Actions) will create:

- `valheim-vpc` + `valheim-subnet`
- two firewall rules (IAP SSH, Valheim UDP)
- `valheim-vm-sa` service account + project-level log/metric writer roles
- the `valheim-server-password` Secret Manager secret (container only — no value)
- a 20GB `pd-balanced` data disk with `prevent_destroy = true`
- a `valheim-server` GCE instance with shielded VM + startup-script bootstrap

**This is the first apply that costs real money.** Disks and the VM start
billing the moment they're created. Stop the VM with `gcloud compute
instances stop valheim-server --zone us-central1-a` (or via the bot once
Phase 3 lands) when you're not using it.

After the apply succeeds, seed the server password **once**:

```bash
echo -n 'CHOOSE-A-PASSWORD' | \
  gcloud secrets versions add valheim-server-password \
    --project="$PROJECT_ID" --data-file=-
```

The VM picks up the password on the next start (`fetch-secrets.sh` always
reads `latest`). Until you seed it, `valheim-fetch-secrets.service` will
fail and `valheim.service` will refuse to start — that's intentional, not a
bug.

## Disaster recovery

If you ever need to re-bootstrap (new GCP project, lost state, etc.):

1. Delete any existing bucket named `<project>-tfstate` (or rename it in `terraform.tfvars`).
2. Swap `backend.tf` back to `backend "local"`.
3. `rm -rf .terraform/` and repeat steps 1–7.

The WIF pool ID (`github-actions-pool`) is immutable once created. If you destroy it, GCP marks it deleted for 30 days before letting you re-create with the same ID. If you need to re-bootstrap within those 30 days, change the pool ID in `modules/gcp-bootstrap/wif.tf`.
