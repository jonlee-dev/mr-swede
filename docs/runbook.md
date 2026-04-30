# Runbook

Things that go wrong and how to recover. The bot is intended to be the
day-to-day control surface; this runbook is for cases where the bot is
itself broken or where state has drifted enough that Terraform alone
won't fix it.

## Quick reference

```bash
PROJECT_ID="$(gcloud config get-value project)"
ZONE=us-central1-a

# Valheim VM
gcloud compute ssh valheim-server --tunnel-through-iap --zone "$ZONE" --project "$PROJECT_ID"
sudo systemctl status valheim
sudo journalctl -u valheim -e -n 200
sudo journalctl -u valheim-fetch-secrets -e -n 50
sudo docker compose -f /opt/valheim/docker-compose.yml logs --tail 200
sudo docker compose -f /opt/valheim/docker-compose.yml ps

# Lavalink VM
gcloud compute ssh lavalink-server --tunnel-through-iap --zone "$ZONE" --project "$PROJECT_ID"
sudo systemctl status lavalink
sudo journalctl -u lavalink -e -n 200
sudo journalctl -u lavalink-fetch-secrets -e -n 50

# Bot (Cloud Run)
gcloud run services logs read mr-swede --region=us-central1 --limit=100

# Idle watcher (multi-target Cloud Function)
gcloud functions logs read valheim-idle-watcher --region=us-central1 --limit=20
gcloud scheduler jobs run valheim-idle-watcher-tick --location=us-central1   # fire manually
gsutil cat gs://${PROJECT_ID}-idle-watcher-state/state-valheim.json
gsutil cat gs://${PROJECT_ID}-idle-watcher-state/state-lavalink.json
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

### 8. Idle watcher is stopping a server too aggressively

The watcher iterates over both targets each tick. State is stored
per-target as `state-<target>.json` in the watcher's state bucket.

1. Check the Cloud Function logs (`gcloud functions logs read valheim-idle-watcher --region=us-central1 --limit=30`). Each tick logs one line per target, e.g. `[valheim] empty 1/2` or `[lavalink] server is active, reset counter (was 0)`.
2. Common cause: firewall rule changed or the daemon crashed and the watcher can't reach the probe endpoint. Probe failures are logged as `probe to <ip> failed, no-op` and do NOT increment the counter, so this should not in itself cause an early stop. If it does, check that the URL pattern in `infra/modules/gcp-idle-watcher/function/main.py` matches what the VM exposes.
3. Override: manually start the affected VM (`/valheim start` or `/music play`) or via `gcloud compute instances start <instance> --zone $ZONE`. To wipe a misbehaving counter: `echo '{"consecutive_empty": 0}' | gsutil cp - gs://${PROJECT_ID}-idle-watcher-state/state-<target>.json`.

### 9. /music play fails with "no nodes are currently CONNECTED" or hangs

The bot caches the Wavelink node connection across requests. When the
Lavalink VM gets restarted (e.g. after the idle watcher stops it and
the next `/music play` brings it back up at a new public IP), the
cached connection is stale.

1. Check `gcloud functions logs read valheim-idle-watcher --region=us-central1` — if it just stopped Lavalink, that's the cause.
2. Check Lavalink itself is up: `curl http://<lavalink-vm-public-ip>:2333/v4/info -H "Authorization: $LAVALINK_PASSWORD"` should return JSON.
3. Bounce the bot revision to clear the cached node:
   ```bash
   gcloud run services update mr-swede --region=us-central1 \
     --update-env-vars=BOT_BOUNCE=$(date +%s)
   ```
   (This forces a new revision; the new instance reconnects fresh.) The
   stale-session detection improvement in the bot's `_ensure_node_connected`
   should remove the need for this manual bounce going forward.

### 10. Spotify URLs fail to resolve (`couldn't resolve that Spotify URL` or "no source for that URL")

The lavasrc plugin handles Spotify URL resolution. Two distinct failure
shapes; check both:

1. **Spotify source isn't enabled.** The fetch-secrets script gates
   lavasrc Spotify behind the presence of the `spotify-client-credentials`
   secret. SSH to the Lavalink VM and check:

   ```bash
   sudo cat /etc/lavalink/secret.env | grep LAVASRC_SPOTIFY
   ```

   If you see `LAVASRC_SPOTIFY_ENABLED=false`, the secret either has no
   versions yet or fetch-secrets couldn't reach it. Seed (or re-seed)
   per [`docs/bootstrap.md`](bootstrap.md#spotify-developer-app-credentials-optional)
   then `gcloud compute instances reset lavalink-server`.

2. **Credentials are seeded but Spotify is rejecting the request.**
   Likely a stale or revoked client_secret. Check Lavalink's logs:

   ```bash
   sudo journalctl -u lavalink -e -n 100 | grep -i spotify
   ```

   `401 Unauthorized` on the token-exchange URL = bad credentials.
   Rotate the Spotify Developer App secret (the dashboard at
   developer.spotify.com lets you regenerate), seed the new value via
   GSM, reset the Lavalink VM.

   `429 Too Many Requests` = rate limit. Spotify's client-credentials
   tier has a generous quota; if we're hitting it our usage exploded.
   Investigate before raising the limit.

3. **Lavalink booted before the secret was reachable.** Boot ordering:
   `lavalink-fetch-secrets.service` runs once before `lavalink.service`.
   If the secret was seeded AFTER Lavalink started, it won't pick up
   the change. Reset the VM.

### 11. Music plays silently / bot joins VC but no audio

Almost always a Discord-voice-protocol-versions mismatch. Known good combo:

- Lavalink **4.2.2** (DAVE/E2EE-aware) running directly under systemd, NOT in Docker
- `discord.py[voice]` extra installed (PyNaCl required) and `wavelink ^3.5.0` (sends `channelId` + DAVE)
- JVM flag `-Djava.net.preferIPv4Stack=true` (prevents silent IPv6 hangs on GCE)
- `openjdk-17-jre-headless` (Lavalink 4.x rejects 21+ in some configs)

If you're debugging a regression: hit Lavalink directly with `curl /v4/info` and check the version, then check the bot's `pyproject.toml` for the wavelink pin. Mismatches usually surface as Discord close codes 4003 (auth) or 4017 (E2EE required) in the Lavalink logs.
