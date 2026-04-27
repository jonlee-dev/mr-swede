# Module: gcp-idle-watcher

Cloud Scheduler + Cloud Function that polls the Valheim VM's Steam A2S
query port and stops the VM after N consecutive empty checks. Cuts the
VM's monthly bill from ~$50 (24/7) to ~$5-10 (used 1-3 hours/day) by
catching the case where someone forgot to run `/valheim stop`.

## What this module creates

| Resource | Purpose |
|---|---|
| `google_service_account.watcher` (`idle-watcher-sa`) | Runtime + caller identity for the function |
| `google_project_iam_member.watcher_log_writer` | Cloud Logging from the function |
| `google_compute_instance_iam_member.watcher_can_admin_valheim_vm` | Same custom role as the bot, instance-scoped to the Valheim VM |
| `google_storage_bucket.state` | Tiny JSON object holding the empty-check counter |
| `google_storage_bucket_iam_member.watcher_can_rw_state` | Object-level read/write access for the watcher SA |
| `google_storage_bucket.function_source` | Holds the function zip; old zips age out after 30 days |
| `google_storage_bucket_object.function_source` | The zip itself; name embeds md5 so code edits force redeploy |
| `google_cloudfunctions2_function.watcher` | The function; runtime python312, entry `check_and_stop` |
| `google_cloud_run_v2_service_iam_member.scheduler_can_invoke_watcher` | Watcher SA gets run.invoker on its own function (Scheduler self-invocation) |
| `google_cloud_scheduler_job.watcher` | Cron job firing the function every `var.polling_schedule` |

## Inputs

See [`variables.tf`](variables.tf). Required: `project_id`,
`valheim_instance_self_link`, `vm_controller_role_id`. Everything else
has a sensible default.

## Outputs

See [`outputs.tf`](outputs.tf). Most useful: `function_name`,
`scheduler_job_name`, `state_bucket_name`.

## Behavior

Each tick (default every 30 minutes):

1. Read VM state. If `status != RUNNING`, reset counter and no-op.
2. A2S probe to `<vm_public_ip>:<valheim_a2s_port>` (default 2457).
   - On any failure (timeout, unreachable, malformed response), no-op
     conservatively WITHOUT incrementing.
3. If A2S reports >0 players, reset counter to 0.
4. If A2S reports 0 players, increment counter. If the counter hits
   `var.empty_checks_to_stop`, issue `instances.stop` and reset.

State (a single `consecutive_empty` integer) lives in the state bucket
as `state.json`. If the object goes missing, the function starts fresh
at 0 -- the safe default.

## Defaults vs reactivity

| Setting | Default | Effect |
|---|---|---|
| `polling_schedule` | `*/30 * * * *` | Tick every 30 min |
| `empty_checks_to_stop` | 2 | Stop after two consecutive empty ticks |
| Effective idle window | 60-90 min | Time from last player leaving to VM stop |

Tighten by lowering `empty_checks_to_stop` to 1 (idle window: 30-60min)
or by tightening the schedule (`*/15 * * * *` for 30-45min idle window).

## First-apply gotchas

- **APIs**: `cloudfunctions`, `cloudscheduler`, `eventarc`, `pubsub`,
  `cloudbuild`, `run`, and `artifactregistry` must be enabled. The
  `gcp-bootstrap` module already turns all of these on.
- **The function's first deploy takes ~3-5 min** (Cloud Build builds
  the container image from the python312 base). Subsequent deploys
  reuse the buildpack cache and finish in ~1-2 min.
- **Scheduler region pins**: Cloud Scheduler is region-scoped. We use
  `var.region` (default us-central1). Don't pick a region that doesn't
  support Cloud Scheduler -- the API list is shorter than Cloud Run's.

## Manual testing

```bash
PROJECT_ID="$(gcloud config get-value project)"

# Fire the function once (synchronous, returns the body).
gcloud --project="$PROJECT_ID" scheduler jobs run valheim-idle-watcher-tick \
  --location=us-central1

# Read the function's logs.
gcloud --project="$PROJECT_ID" functions logs read valheim-idle-watcher \
  --region=us-central1 --limit=20

# Inspect the state object.
gsutil cat "gs://${PROJECT_ID}-idle-watcher-state/state.json"

# Reset the counter manually (also happens automatically when players
# show up).
echo '{"consecutive_empty": 0}' | \
  gsutil cp - "gs://${PROJECT_ID}-idle-watcher-state/state.json"
```

## Cost shape (us-central1, Apr 2026)

| Component | Monthly |
|---|---|
| Cloud Function 2nd gen, ~1500 invocations × ~3s × 256MB | $0 (under free tier) |
| Cloud Scheduler, 1 job | $0 (3 free jobs) |
| GCS state + source buckets, <1KB + ~50MB | <$0.05 |
| Cloud Build, ~5 builds/month after bootstrap | $0 (under free tier) |
| **Total** | **~$0.05** |

The watcher pays for itself on the first day it catches a forgotten
running VM (an idle e2-standard-2 costs ~$0.07/hour).
