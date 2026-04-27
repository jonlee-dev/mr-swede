# Runbook

Things that go wrong and how to recover. The bot is intended to be the
day-to-day control surface; this runbook is for cases where the bot is
itself broken or where state has drifted enough that Terraform alone
won't fix it.

## Quick reference

```bash
PROJECT_ID="$(gcloud config get-value project)"
ZONE=us-central1-a
INSTANCE=valheim-server

# Connect (no public port 22 — IAP tunnel only)
gcloud compute ssh "$INSTANCE" --tunnel-through-iap --zone "$ZONE" --project "$PROJECT_ID"

# Service-level
sudo systemctl status valheim
sudo journalctl -u valheim -e -n 200
sudo journalctl -u valheim-fetch-secrets -e -n 50

# Container-level
sudo docker compose -f /opt/valheim/docker-compose.yml logs --tail 200
sudo docker compose -f /opt/valheim/docker-compose.yml ps
```

## Scenarios

### 1. Bot deploy is failing

1. Check the `CI` workflow in GitHub Actions.
2. If image build fails: reproduce locally with `cd bot && docker build .`.
3. If Cloud Run rollout fails: inspect the failing revision's logs in Cloud Run.

### 2. Valheim VM is up but no one can connect

1. Confirm the VM is `RUNNING` (`gcloud compute instances describe $INSTANCE --zone $ZONE`).
2. Check that the firewall rule `valheim-allow-game-udp` exists and targets the `valheim-server` tag (`gcloud compute firewall-rules describe valheim-allow-game-udp`).
3. SSH via IAP and confirm the container is up: `sudo docker ps`.
4. The PlayFab join code is printed by the server in its log — `docker compose logs | grep -i 'join code'`. If absent, `SERVER_PUBLIC` or `CROSSPLAY` may have been disabled.

### 3. startup-script failed on first boot

Symptom: VM is `RUNNING` but `valheim.service` doesn't exist (or `systemctl is-active valheim` returns `inactive`/`failed`).

1. View the startup-script's own log on the VM: `sudo cat /var/log/valheim-startup-script.log`. Also check the journal: `sudo journalctl -u google-startup-scripts.service -e`.
2. The four heredoc-decoded files should land at `/opt/valheim/docker-compose.yml`, `/opt/valheim/scripts/fetch-secrets.sh`, and the two systemd unit files. Missing files = templatefile() rendered something invalid (check `terraform plan` output) or the heredoc base64 didn't decode cleanly.
3. Re-run the startup-script manually:
   ```bash
   sudo google_metadata_script_runner --script-type startup
   ```
   Or simpler: `sudo systemctl restart google-startup-scripts.service`. The script is idempotent.
4. If you suspect the rendered metadata is stale (TF apply pushed a new template but the VM hasn't rebooted), force a reboot:
   ```bash
   gcloud compute instances reset valheim-server --zone us-central1-a
   ```
   The reset triggers google-guest-agent to re-fetch and re-run the script.

### 4. fetch-secrets is failing

Symptom: `valheim-fetch-secrets.service` is `failed`; `valheim.service` won't start because of the `Requires=` dependency.

1. `sudo journalctl -u valheim-fetch-secrets -e -n 50`. The most common error is HTTP 403 on `secretmanager.googleapis.com` — the SA lost its `secretmanager.secretAccessor` binding. Re-apply Terraform to restore.
2. Check the secret has at least one version: `gcloud secrets versions list valheim-server-password --project $PROJECT_ID`. A freshly-created secret container has none until you seed it.
3. Seed (or rotate) the password:

   ```bash
   echo -n 'CHOOSE-A-PASSWORD' | \
     gcloud secrets versions add valheim-server-password \
       --project="$PROJECT_ID" --data-file=-
   ```

4. Restart: `sudo systemctl restart valheim-fetch-secrets valheim`.

### 5. World file corruption suspected

The persistent data disk has `prevent_destroy = true`, so Terraform won't
nuke it. Recovery has three increasingly drastic steps:

1. **In-place rewind.** lloesche/valheim-server keeps rolling backups under `/opt/valheim/data/backups/auto-N/`. Stop the service, copy the most recent backup over `/opt/valheim/data/worlds_local/<world>.{db,fwl}`, restart.
2. **Snapshot restore (Phase 2 onward).** `gcloud compute snapshots list --filter="sourceDisk:valheim-world-data"`. Create a new disk from the snapshot, detach the live data disk, attach the new one, restart the VM.
3. **Off-VM bucket restore (Phase 2 onward).** `gsutil rsync -r gs://<project>-valheim-backups/worlds/ /opt/valheim/data/worlds_local/` then restart.

### 6. VM disk is full

20GB is generous but rolling backups + 3 worlds can grow:

1. `df -h` and `du -h --max-depth=1 /opt/valheim/data`.
2. Trim auto-backups: `BACKUPS_MAX_AGE` in `docker-compose.yml` defaults to 3 days; lower it temporarily.
3. If still full, expand the disk in Terraform (`data_disk_size_gb`), `terraform apply`, then on the VM: `sudo resize2fs /dev/disk/by-id/google-valheim-data`.

### 7. "I want to delete and rebuild the VM"

Both the VM and the data disk have deletion guards. Recipe:

1. Snapshot first: `gcloud compute disks snapshot valheim-world-data --zone $ZONE --snapshot-names valheim-pre-rebuild`.
2. Flip both flags off in Terraform: set `deletion_protection = false` (variable on the module) and `prevent_destroy = false` (lifecycle in `disk.tf`).
3. `terraform apply` the flag changes, then `terraform destroy -target=module.valheim_vm`.
4. Restore the flags before the next apply, otherwise Terraform plan will show a no-op drift.

### 8. Idle watcher (Phase 7) is stopping the server too aggressively

1. Check the Cloud Function logs for A2S query responses.
2. Common cause: firewall rule changed and the watcher can't reach the query port.
3. Override: manually start the VM via `/valheim start` or `gcloud compute instances start $INSTANCE --zone $ZONE`.
