###############################################################################
# Cloud Build trigger.
#
# Watches the master branch on GitHub and runs cloudbuild.yaml on push.
# The OAuth handshake that lets Cloud Build read the GitHub repo CANNOT
# be done in Terraform -- it's a one-time GitHub App install via the
# console. Prereq:
#
#   GCP Console → Cloud Build → Triggers → "Connect Repository"
#   → choose GitHub (Cloud Build GitHub App), authorize the
#   jonlee-dev/mr-swede repo. After that the App's webhook is in place
#   and the resource below works.
#
# Using the 1st-gen `github { ... }` block intentionally. The 2nd-gen
# `google_cloudbuildv2_connection` + `google_cloudbuildv2_repository`
# pair adds two more resources and an OAuth-token-stored-in-Secret-Manager
# step; not worth it for one repo.
#
# Substitutions match cloudbuild.yaml. Updating both files in lockstep is
# part of the deal; keeping them as TF inputs would over-couple the
# module to that file's internal vocabulary.
###############################################################################

resource "google_cloudbuild_trigger" "bot_master" {
  project     = var.project_id
  name        = "${var.service_name}-master"
  description = "Build and deploy ${var.service_name} on push to ${var.github_branch_regex}."
  filename    = "cloudbuild.yaml"

  github {
    owner = var.github_owner
    name  = var.github_repo

    push {
      branch = var.github_branch_regex
    }
  }

  # Path filter: don't fire builds for changes that can't affect the
  # container (TF-only changes, doc-only changes). Anything inside bot/
  # plus the cloudbuild.yaml itself counts.
  included_files = [
    "bot/**",
    "cloudbuild.yaml",
  ]

  substitutions = {
    _AR_HOSTNAME   = "${var.region}-docker.pkg.dev"
    _AR_PROJECT_ID = var.project_id
    _AR_REPOSITORY = google_artifact_registry_repository.bot.repository_id
    _DEPLOY_REGION = var.region
    _PLATFORM      = "managed"
    _SERVICE_NAME  = var.service_name
  }

  depends_on = [
    # The first build needs the AR repo to push to and the bot SA to
    # deploy as. Without these the trigger's first run would fail with
    # confusing 404s before any human has a chance to look.
    google_artifact_registry_repository.bot,
    google_service_account_iam_member.build_can_act_as_bot,
    google_artifact_registry_repository_iam_member.build_can_push_images,
    google_project_iam_member.build_can_deploy_run,
  ]
}
