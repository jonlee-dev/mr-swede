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

### 1.5 Boot disk full / `df` shows 100% / Docker `prune` reclaims 0B

Symptom seen 2026-05-03: `scp` to `/tmp/...` writes a sparse null-filled
file when the disk is full, corrupting whatever you copy onto it (e.g.,
`docker-compose.yml` becoming nulls and breaking the next compose up).

```bash
df -h /
# /dev/sda1   9.7G   9.5G    0  100%  /
```

Cause: the boot disk is 10 GB by default (`boot_disk_size_gb=10` in
`gcp-valheim-vm` module). Each `docker compose down + up` cycle (which
the Valheim VM does on every metadata change applied through a TF
edit) creates a new container layer; orphaned old layers accumulate.
The Valheim binary itself plus BepInExPack + Jotunn + PlanBuild push
the active container to ~5.5 GB on its own.

**Immediate fix:**

```bash
# Stop + remove the container (frees the writable layer)
sudo docker stop valheim
sudo docker rm valheim
sudo docker image prune -af
df -h /   # verify recovered space

# Restore docker-compose.yml from git if it was corrupted by a sparse
# write during the disk-full window
gcloud compute scp \
  /path/to/repo/server/docker-compose.yml \
  valheim-server:/tmp/docker-compose.yml \
  --tunnel-through-iap --zone=us-central1-a
sudo cp /tmp/docker-compose.yml /opt/valheim/docker-compose.yml
sudo docker compose -f /opt/valheim/docker-compose.yml up -d
```

**Permanent fix (queued as follow-up):** bump
`boot_disk_size_gb` to 20 or 30 in `gcp-valheim-vm/variables.tf` AND
resize the live disk via `gcloud compute disks resize valheim-server
--size=30GB --zone=us-central1-a`, then `growpart` + `resize2fs`
inside the VM. The TF change alone would require VM recreation
(initialize_params.size is a force-replace field), so do the live
resize first, then update TF to match (adding `lifecycle {
ignore_changes = [boot_disk[0].initialize_params[0].size] }` if TF
plan keeps wanting to recreate).

### 2. Valheim VM is up but no one can connect

1. Confirm the VM is `RUNNING` (`gcloud compute instances describe $INSTANCE --zone $ZONE`).
2. Check that the firewall rule `valheim-allow-game-udp` exists and targets the `valheim-server` tag (`gcloud compute firewall-rules describe valheim-allow-game-udp`).
3. SSH via IAP and confirm the container is up: `sudo docker ps`.
4. **Connection method depends on the `CROSSPLAY` setting**:
   - `CROSSPLAY=false` (current default; Steam-only): players join via **Valheim → Join Game → Join IP** → `<public_ip>:2456`. There is no PlayFab join code; do not look for one. Confirm the server is registered with Steam: `docker compose logs | grep -E "Opened Steam server|Game server connected"`.
   - `CROSSPLAY=true`: players join via the 6-digit PlayFab code from `docker compose logs | grep -i 'join code'`.
5. If players see "Server not found" via direct IP, check that the VM's public IP hasn't changed since the last `/valheim status` (ephemeral IPs rotate on stop/start). Use the IP from the most recent `/valheim status` output.

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

### 6.5 BepInEx mods aren't loading / "Could not load file or assembly 'MMHOOK_assembly_valheim'"

Expected behavior **as of 2026-05-03**: this error appears in the
container logs and is a known limitation. lloesche's image only
auto-syncs `/config/bepinex/plugins/` to the active BepInEx dir; it
does NOT sync `/config/bepinex/patchers/`. PlanBuild's HookGenPatcher
patcher therefore doesn't load, and PlanBuild's `Awake()` throws.

What still works (the "Option A" trade-off accepted in PRD §8 decision
log):
  - BepInEx loads
  - Jotunn loads (registers PlanBuild's RPCs)
  - PlanBuild plugin file present; basic blueprint sync may work
    via Jotunn's RPC bridge
  - Friends with PlanBuild installed locally can place planned
    pieces and use the blueprint rune
  - Server-side **marketplace** feature does NOT work

If a friend reports "PlanBuild isn't working":
  1. Confirm they have PlanBuild + Jotunn + HookGenPatcher installed
     locally via Thunderstore Mod Manager or r2modman (matching the
     server's pinned versions: 0.18.4 / 2.28.0 / 0.0.4).
  2. Their client has BepInExPack auto-installed by the mod manager.
  3. Server doesn't gate connections on PlanBuild presence; clients
     without it can still connect (per PlanBuild's design).

If you ever decide to enable server-side marketplace, see PRD §8's
"Option A" decision for the alternative paths (custom Dockerfile or
two-boot post-merge hook).

### 6.6 Bumping a mod version

Pinned versions live in `server/scripts/install-mods.sh` as a
pipe-delimited list:

```bash
MODS=(
  "ValheimModding|Jotunn|2.28.0|plugins"
  "ValheimModding|HookGenPatcher|0.0.4|patchers"
  "MathiasDecrock|PlanBuild|0.18.4|plugins"
)
```

Procedure:
  1. Edit the version string in install-mods.sh
  2. `terraform apply -target=module.valheim_vm` (updates VM metadata
     with new startup-script content)
  3. SSH to VM. The marker file `.installed-<name>-<old_version>`
     blocks re-download of the previous version; install-mods.sh
     wipes any `.installed-<name>-*` marker matching the mod name
     before downloading, so just re-running the unit picks up the
     new version:
     ```
     sudo systemctl restart install-mods.service
     ```
  4. Bounce the container:
     `sudo docker compose -f /opt/valheim/docker-compose.yml down`
     `sudo docker compose -f /opt/valheim/docker-compose.yml up -d`

A Valheim binary update may also be required if the mod requires a
specific game version. The image's `UPDATE_CRON` is empty (PRD §8
2026-05-03 decision); to update Valheim manually, edit
`docker-compose.yml`, set `UPDATE_CRON: "0 5 * * *"` temporarily,
let it run once, then revert.

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

### 11. Players experiencing 10-30s lag spikes mid-session ("rubber-banding")

If `CROSSPLAY=true`, the most likely cause is a **PlayFab relay reconnect**.
Check container logs for:

```bash
sudo docker logs valheim 2>&1 | grep -iE "PlayFab|cloudapp.azure.com|ZRpc timeout|ResetParty"
```

The pattern is:
```
PlayFab network error ... code '4098': the operation was called with an invalid handle
Player connection lost server "...", now N player(s)
ResetParty / LeaveNetworkTask / CleanPartyTask / InitPartyTask / JoinPartyTask
Joined PlayFab Party network with ID "..."
```

That's PlayFab's Azure-hosted relay invalidating the session and the
server having to re-establish. ~15-25s of degraded gameplay each time.
**Not solvable on our side** — it's Microsoft's relay infrastructure.

Mitigation: disable crossplay (per the 2026-05-02 PRD decision). If the
friend group is Steam-only, this is a free win. To re-enable crossplay
later, flip `CROSSPLAY` back to `"true"` in `server/docker-compose.yml`,
`terraform apply`, and bounce the container with `docker compose down +
up` (which preserves world saves but recreates the container — costs
one re-download of the Valheim binary from Steam).

If `CROSSPLAY=false` and lag is still happening: the cause is somewhere
between the player's ISP and GCP us-central1. Have the affected player
run `mtr -uw -P 2456 <server_ip>` from their machine and look for
packet-loss hops. There's nothing we can fix server-side beyond moving
regions.

### 12a. Emergency: pause the idle watcher entirely

If the watcher is misbehaving and you need to stop it from making any
more decisions while you investigate, you have two options.

**Preferred: TF-managed pause.** Flip the variable, apply, done:

```bash
# in infra/envs/prod/terraform.tfvars
idle_watcher_paused = true

cd infra/envs/prod
terraform apply -target=module.idle_watcher
```

The Cloud Scheduler job goes to `state=PAUSED`. Function stays
deployed (no destroy churn, no rebuild on re-enable). Verify:

```bash
gcloud scheduler jobs describe valheim-idle-watcher-tick \
  --location=us-central1 --format='value(state)'
# expect: PAUSED
```

**While paused, on-demand VMs stay up until manually stopped** via
`/valheim stop` / `/music stop` or `gcloud compute instances stop`.
Plan for ~$2/day of additional idle billing if you forget.

Re-enable: flip `idle_watcher_paused = false` in `terraform.tfvars`,
`terraform apply`, verify scheduler state goes back to `ENABLED`.

**Stopgap (not version-controlled): direct gcloud pause.** Useful in
the moment when terraform isn't handy:

```bash
gcloud scheduler jobs pause valheim-idle-watcher-tick --location=us-central1
# resume with: gcloud scheduler jobs resume ...
```

This will create TF state drift on the next plan -- subsequent
applies will try to set `paused=false` (the default in code). To
make it survive plans, set the tfvar instead.

### 12b. Idle watcher stopped the server with active players on it

**The 2026-05-02 daemon-truncation bug AND the 2026-05-03 follow-stream
fragility bug are both fixed in the A2S-based daemon shipped 2026-05-03.
The symptoms are documented for posterity — read this whole section
before assuming a recurrence.**

History of attempts:
- **v1 (pre-2026-05-02): `docker compose logs --tail 500` every 30s.**
  Re-derived state from scratch each scrape. Lost player_count when
  gameplay was quiet for >15-30 min (the last "now N player(s)" log
  line scrolled past 500 entries).
- **v2 (2026-05-02): `docker compose logs --follow`.**
  Persistent state, ingest line-by-line. Improved on v1 but still
  hit `stream ended (exit=0)` at random — `--follow` isn't actually
  a guaranteed continuous stream in docker compose. Events lost in
  reconnect gaps; players still got false-stopped.
- **v3 (2026-05-03, current): Steam A2S query to UDP localhost:2457.**
  Bypasses log parsing. Queries the game itself for player count.

If a stop with active players recurs:

1. Confirm the daemon is the **A2S** version:
   ```bash
   sudo journalctl -u valheim-status --no-pager -n 5
   ```
   Look for `Valheim status server starting (HTTP :9001, A2S target
   127.0.0.1:2457, ...)`. If you see `docker compose logs --follow
   attached (pid=...)` you're on v2; if you see periodic `scrape ok:`
   lines you're on v1. Either: hot-install per
   `server/scripts/status-server.py` and `sudo systemctl restart
   valheim-status`.

2. Check the live `/status.json`:
   ```bash
   curl -sS http://localhost:9001/status.json
   ```
   Should report current `player_count` and `server_running: true`
   (or `error: a2s_query_failed` if the game container isn't
   responsive — that's a separate problem to investigate).

3. Verify A2S directly (independent of the daemon):
   ```bash
   sudo python3 -c "
   import socket
   s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
   Q = b'\xff\xff\xff\xffTSource Engine Query\x00'
   s.sendto(Q, ('127.0.0.1', 2457))
   d, _ = s.recvfrom(4096)
   s.sendto(Q + d[5:9], ('127.0.0.1', 2457))
   d, _ = s.recvfrom(4096)
   pos = 6
   for _ in range(4): pos = d.index(b'\x00', pos) + 1
   print('players=', d[pos+2], 'max=', d[pos+3])"
   ```
   The two values should match. If they disagree, the daemon has a
   bug; file an issue and pin the daemon's player_count to the raw
   A2S reading.

4. Watcher's `empty_checks_to_stop` is 4 (was 2) — defense in depth
   for any future regression. Tunable via the `gcp-idle-watcher`
   module's variable.

5. **The 2026-05-02 PlayFab/crossplay fix is what enabled A2S to
   work.** If `CROSSPLAY=true` is ever turned back on, A2S queries
   become unreliable again (PlayFab interferes with the query port)
   and the daemon will start logging `a2s_query_failed`. Either
   accept the unreliability or revisit the daemon strategy.

### 13. Music plays silently / bot joins VC but no audio

Almost always a Discord-voice-protocol-versions mismatch. Known good combo:

- Lavalink **4.2.2** (DAVE/E2EE-aware) running directly under systemd, NOT in Docker
- `discord.py[voice]` extra installed (PyNaCl required) and `wavelink ^3.5.0` (sends `channelId` + DAVE)
- JVM flag `-Djava.net.preferIPv4Stack=true` (prevents silent IPv6 hangs on GCE)
- `openjdk-17-jre-headless` (Lavalink 4.x rejects 21+ in some configs)

If you're debugging a regression: hit Lavalink directly with `curl /v4/info` and check the version, then check the bot's `pyproject.toml` for the wavelink pin. Mismatches usually surface as Discord close codes 4003 (auth) or 4017 (E2EE required) in the Lavalink logs.
