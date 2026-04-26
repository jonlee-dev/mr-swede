# =============================================================================
# Workload Identity Federation (WIF) for GitHub Actions → GCP
# =============================================================================
# The goal: let our GitHub Actions workflows authenticate to GCP and apply
# Terraform changes, WITHOUT ever storing a long-lived service account JSON key
# in GitHub Secrets.
#
# The trick: GitHub's OIDC issuer (token.actions.githubusercontent.com) mints
# a short-lived JWT for every workflow run. That JWT contains claims describing
# the run — the repo, the branch, the workflow file, the actor, etc. GCP's
# Security Token Service (STS) can be taught to trust that issuer, verify the
# claims, and mint a short-lived GCP access token in exchange.
#
# There are four moving parts below. Read them in this order:
#
#   1. workload_identity_pool       — a container for "external identities"
#   2. workload_identity_pool_provider — a specific trust relationship
#                                        (here: GitHub's OIDC issuer) with
#                                        attribute mapping + condition
#   3. google_service_account "ci"  — the SA that Terraform-in-Actions will
#                                     impersonate when running
#   4. google_service_account_iam_member — binds "GitHub Actions on our repo
#                                          can impersonate this SA"
#   5. project-level IAM bindings   — what the SA is then allowed to do
# =============================================================================

# 1. The pool.
#    A pool is a namespace. You'd have one pool per external identity provider
#    (e.g., one for GitHub, another for GitLab). We just need the GitHub one.
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions"
  description               = "Federates GitHub Actions OIDC tokens into GCP."

  depends_on = [google_project_service.services]
}

# 2. The provider — the actual trust relationship.
#
#    `oidc.issuer_uri` tells GCP to accept JWTs signed by GitHub's OIDC issuer.
#
#    `attribute_mapping` translates claims inside the JWT into Google attributes.
#    The LHS is how we'll refer to it in IAM bindings (e.g., attribute.repository).
#    The RHS is the JSON path inside the JWT (e.g., assertion.repository).
#
#    `attribute_condition` is a CEL expression evaluated at token exchange time.
#    It's a hard gate: tokens that don't match are rejected before any IAM check.
#    We restrict to tokens issued from OUR repo — otherwise *any* public GitHub
#    Actions run could theoretically mint a token that hits the pool.
resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions-provider"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.ref"              = "assertion.ref"
    "attribute.actor"            = "assertion.actor"
  }

  attribute_condition = "assertion.repository_owner == \"${var.github_owner}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# 3. The Terraform CI service account.
#    GitHub Actions doesn't get permissions directly — it IMPERSONATES this SA,
#    which holds all the real roles. That indirection means we can rotate SAs,
#    swap CI providers, or revoke access just by editing this resource.
resource "google_service_account" "terraform_ci" {
  project      = var.project_id
  account_id   = "terraform-ci"
  display_name = "Terraform CI (GitHub Actions)"
  description  = "Impersonated by GitHub Actions via Workload Identity Federation."
}

# 4. The principal binding.
#
#    This is the key WIF resource. Without it, even a valid OIDC token that
#    passed the attribute_condition above can't actually DO anything.
#
#    `principalSet://...attribute.repository/owner/repo` = "any external identity
#    whose `attribute.repository` equals `owner/repo` can act as this SA".
#
#    We scope this to the single repo so tokens from other repos in the same
#    owner (if any) still cannot impersonate terraform-ci.
resource "google_service_account_iam_member" "github_can_impersonate_ci" {
  service_account_id = google_service_account.terraform_ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}

# 5. Project-level roles on the CI SA.
#    These are what Terraform-in-Actions is allowed to do once it's impersonating
#    the SA. `editor` + two admin roles is the canonical set for a Terraform CI
#    identity: broad enough to create almost any resource, narrow enough to be
#    auditable and non-catastrophic if leaked.
#
#    - roles/editor: create/modify/delete most resources (compute, storage, etc.)
#    - roles/iam.serviceAccountAdmin: create SAs (e.g. the VM's SA, watcher SA)
#    - roles/resourcemanager.projectIamAdmin: set IAM bindings at project scope
#
#    If you're ever tempted to add `roles/owner` here: don't. Owner includes the
#    ability to add Owner to other principals, i.e. permanent escalation, and
#    makes compromise recovery much harder.
locals {
  ci_roles = [
    "roles/editor",
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
  ]
}

resource "google_project_iam_member" "terraform_ci_roles" {
  for_each = toset(local.ci_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.terraform_ci.email}"
}
