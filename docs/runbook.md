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

# Bot + Lavalink VM (gcp-bot-vm; always-on; 2026-05-12 onward)
gcloud compute ssh bot-vm --tunnel-through-iap --zone "$ZONE" --project "$PROJECT_ID"
# Bot
sudo systemctl status bot
sudo journalctl -u bot -e -n 200
sudo journalctl -u bot-fetch-secrets -e -n 50
sudo systemctl list-timers bot-watchdog.timer
curl -s localhost:8080/livez ; echo   # bot's liveness endpoint (watchdog target)
# Lavalink (same VM, localhost only)
sudo systemctl status lavalink
sudo journalctl -u lavalink -e -n 200
sudo journalctl -u lavalink-fetch-secrets -e -n 50
# Deploy a new bot revision (manual; replaces the Cloud Run trigger):
#   cd /opt/mr-swede && sudo -u bot git pull && \
#   sudo -u bot bash -c 'cd bot && poetry install --no-interaction --no-root' && \
#   sudo systemctl restart bot

# Bot — legacy Cloud Run service (kept at min=0 as a rollback only)
gcloud run services describe mr-swede --region=us-central1
# To roll back to Cloud Run: set min=1 in infra/envs/prod (or module variables),
# `terraform apply`, then SSH to bot-vm and `sudo systemctl stop bot` so the new
# Cloud Run revision can claim the Discord gateway WS without a double-session race.

# Lavalink — retired standalone VM (gcp-lavalink-vm)
# Still applied as of 2026-05-12; scheduled for destroy after the bot-vm soak.

# Idle watcher (single-target since 2026-05-12 — Valheim only)
gcloud functions logs read valheim-idle-watcher --region=us-central1 --limit=20
gcloud scheduler jobs run valheim-idle-watcher-tick --location=us-central1   # fire manually
gsutil cat gs://${PROJECT_ID}-idle-watcher-state/state-valheim.json
```

## Scenarios

### 1. Bot deploy is failing

Post-2026-05-12: deploy is `git pull + poetry install + systemctl
restart bot` on the bot-vm — see runbook §16. There's no Cloud Run
rollout to inspect.

1. Check the `CI` workflow in GitHub Actions — `poetry install` /
   `pytest` failures will block before you even push.
2. If `poetry install` on the VM fails (typically after a dep bump):
   ```bash
   sudo journalctl -u bot -e -n 200
   cd /opt/mr-swede/bot
   sudo -u bot poetry install --no-interaction --no-root
   ```
   The most common cause is `poetry.lock` drift; regenerate locally
   and commit, or hand-pin via `pip install` as we did with
   google-auth (see runbook §15).
3. If `bot.service` starts but exits with an import or pydantic
   error, check `/etc/bot/bot.env` for unrendered `${var}` placeholders
   (would mean `templatefile()` lost a key — `terraform apply` to
   re-render and reboot the VM, OR hand-edit + restart for an
   urgent fix).
4. Legacy Cloud Run rollout (only while the service is at `min=1`
   for rollback): inspect the failing revision's logs in Cloud Run.

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

### 6.6 PlanBuild [Server Settings] configuration

PlanBuild's `marcopogo.PlanBuild.cfg` defaults the `[Server Settings]`
permission flags to `false` (conservative for public servers). Our
private friend-group server flips them all to `true`:

```ini
[Server Settings]
Allow blueprint rune = true            # default true
Allow direct build = true              # was false; flipped 2026-05-03
Allow terrain tools = true             # was false; flipped 2026-05-03
Allow serverside blueprints = true     # was false; flipped 2026-05-03
Allow clients to use the GUI toggle key = true  # default true
```

These flips are encoded in `server/scripts/install-mods.sh` as a `sed`
patch that runs on every install-mods.service tick. The patch is
idempotent (re-running on already-flipped values is a no-op).

**Fresh-VM gotcha:** PlanBuild itself creates `marcopogo.PlanBuild.cfg`
on its first plugin load -- NOT lloesche's bootstrap. On a brand-new
VM the file doesn't exist when install-mods first runs, so the sed
patch is skipped (file-not-found guard). The first container boot
creates the .cfg with defaults; the second boot's install-mods picks
up the file and flips the values. **A fresh VM therefore needs ONE
extra container bounce after first boot to apply these settings.**

To override: edit the .cfg directly on the persistent disk
(`/opt/valheim/data/bepinex/marcopogo.PlanBuild.cfg`). install-mods's
sed is one-way (false→true) so manually setting any of these to
`true` survives a re-run; setting to `false` would get clobbered on
the next install-mods tick.

### 6.7 Bumping a mod version

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

Post-2026-05-12 co-tenancy: the bot connects to Lavalink at
`localhost:2333` on the same VM. Both services are started by systemd
on boot; `bot.service` has `Requires=lavalink.service` so Lavalink
comes up first. The stale-node failure mode that used to be triggered
by the idle watcher stopping Lavalink is gone.

If you still hit this:

1. SSH to `bot-vm` and check Lavalink directly:
   ```bash
   sudo systemctl status lavalink
   sudo journalctl -u lavalink -e -n 100
   curl -s -H "Authorization: $(sudo cat /etc/lavalink/secret.env | \
     grep LAVALINK_SERVER_PASSWORD | cut -d= -f2)" \
     http://localhost:2333/v4/info
   ```
   If Lavalink is dead, restart it (`sudo systemctl restart lavalink`)
   and the bot's `_ensure_node_connected` should reconnect on the
   next `/music play`.

2. Check the bot itself:
   ```bash
   sudo systemctl status bot
   sudo journalctl -u bot -e -n 100 | grep -iE 'wavelink|lavalink|node'
   curl -s localhost:8080/livez
   ```
   If `/livez` is returning 503 the bot's gateway is wedged; the
   watchdog (`bot-watchdog.timer`) will restart the bot after 5
   consecutive failures (~5min). Force-restart with
   `sudo systemctl restart bot` to short-circuit.

3. If everything looks healthy but `/music play` still fails:
   ```bash
   sudo systemctl restart bot
   ```
   Wipes any in-memory Wavelink Pool state. The
   `_drop_stale_node` fix shipped 2026-05-10 should make this
   rarely necessary, but it's the cheapest hammer.

### 10. Spotify URLs fail to resolve (`couldn't resolve that Spotify URL` or "no source for that URL")

The lavasrc plugin handles Spotify URL resolution. Two distinct failure
shapes; check both:

1. **Spotify source isn't enabled.** The fetch-secrets script gates
   lavasrc Spotify behind the presence of the `spotify-client-credentials`
   secret. SSH to `bot-vm` (Lavalink is co-tenanted there) and check:

   ```bash
   sudo cat /etc/lavalink/secret.env | grep LAVASRC_SPOTIFY
   ```

   If you see `LAVASRC_SPOTIFY_ENABLED=false`, the secret either has no
   versions yet or fetch-secrets couldn't reach it. Seed (or re-seed)
   per [`docs/bootstrap.md`](bootstrap.md#spotify-developer-app-credentials-optional)
   then re-run lavalink-fetch-secrets + restart lavalink:
   `sudo systemctl restart lavalink-fetch-secrets.service lavalink.service`.

2. **Credentials are seeded but Spotify is rejecting the request.**
   Likely a stale or revoked client_secret. Check Lavalink's logs:

   ```bash
   sudo journalctl -u lavalink -e -n 100 | grep -i spotify
   ```

   `401 Unauthorized` on the token-exchange URL = bad credentials.
   Rotate the Spotify Developer App secret (the dashboard at
   developer.spotify.com lets you regenerate), seed the new value via
   GSM, then `sudo systemctl restart lavalink-fetch-secrets.service lavalink.service`.

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

### 14. Bot is wedged (gateway WS dropped, slash commands hanging) on bot-vm

Replaces the Cloud Run "force a new revision" recipe. On bot-vm the
equivalent is restarting the systemd unit.

1. SSH to bot-vm and check `/livez`:
   ```bash
   gcloud compute ssh bot-vm --tunnel-through-iap --zone us-central1-a --project mr-swede
   curl -s localhost:8080/livez; echo
   ```
   `{"alive":false,...}` (HTTP 503) = bot's WS is closed or stale
   (>90s since last gateway event). `bot-watchdog.timer` curls this
   every 60s and will `systemctl restart bot` after 5 consecutive
   failures (~5min). To short-circuit:
   ```bash
   sudo systemctl restart bot
   ```

2. Check the watchdog timer is actually scheduled:
   ```bash
   sudo systemctl list-timers bot-watchdog.timer
   sudo journalctl -u bot-watchdog.service -e -n 100
   sudo cat /var/lib/bot-watchdog/consecutive-failures   # current counter
   ```
   If `consecutive-failures` is wedged at a number >5 but the bot
   never got restarted, the timer is probably masked/stopped:
   `sudo systemctl enable --now bot-watchdog.timer`.

3. If `bot.service` itself keeps crash-looping (`systemctl status bot`
   shows "activating (auto-restart)" repeatedly):
   ```bash
   sudo journalctl -u bot -e -n 300
   sudo journalctl -u bot-fetch-secrets -e -n 50
   ```
   Common causes:
   - `bot-fetch-secrets.service` failed → secrets.env missing →
     bot Pydantic-fails on missing `DISCORD_BOT_TOKEN`. Check that
     `mr-swede-sa` still has `roles/secretmanager.secretAccessor` on
     `discord-bot-secrets` (`terraform apply` should be sufficient).
   - `google-auth` regression — see runbook §15.
   - poetry venv is broken after a `git pull` that changed
     `pyproject.toml`. Rebuild:
     ```bash
     cd /opt/mr-swede/bot
     sudo -u bot poetry install --no-interaction --no-root
     sudo -u bot /opt/mr-swede/bot/.venv/bin/pip install --upgrade "google-auth>=2.46"
     sudo systemctl restart bot
     ```

### 15. Bot crashes on boot with `AttributeError: 'Request' object has no attribute 'session'`

google-auth 2.45.0 regression in `_prepare_request_for_mds`. Surfaces
during `SecretManagerServiceClient()` init on GCE. The startup-script
already pins `>=2.46` via `pip install --upgrade` after `poetry
install`, so this should not bite on a fresh boot — but if you've
manually downgraded the venv or hit the bug pre-fix:

```bash
sudo -u bot /opt/mr-swede/bot/.venv/bin/pip install --upgrade "google-auth>=2.46"
sudo systemctl restart bot
```

Durable fix is to regenerate `poetry.lock` with `google-auth>=2.46`
pinned in `pyproject.toml`; we haven't because Poetry isn't installed
on the Windows host. Whenever that changes, drop the `pip install`
line from `server/bot-vm/startup-script.sh.tftpl`.

### 16. Bot deploy — manual flow on bot-vm

Replaces Cloud Build's auto-deploy-on-master trigger. The Cloud Build
trigger is still wired up via `gcp-bot-runtime` (it pushes a new image
to Artifact Registry on every master commit) but the deployed Cloud
Run service is at `min=0`, so it doesn't pick up the new image.

To ship a bot change to production:

```bash
gcloud compute ssh bot-vm --tunnel-through-iap --zone us-central1-a --project mr-swede
cd /opt/mr-swede
sudo -u bot git pull
# Only if pyproject.toml / poetry.lock changed:
sudo -u bot bash -c 'cd bot && poetry install --no-interaction --no-root'
sudo systemctl restart bot
sudo journalctl -u bot -f   # watch it come up; ctrl-c when "Bot ready" appears
curl -s localhost:8080/livez; echo
```

Rollback is just `git checkout <prev-sha>` + `systemctl restart bot`.
There's no image registry to clean up because we run from source.

### 17. Music dropped mid-session (Discord voice gateway issue)

Symptom: bot is still in the voice channel and `/music nowplaying`
shows a track, but no audio is reaching anyone. Bot logs are quiet
because Wavelink doesn't surface a Koe-side voice transport reset.

As of 2026-05-13 the bot has automatic recovery for this — you should
see one of these in `#bot-spam` within ~5-10s of the dropout:

  - 🔁 *"Audio dropped on **<track>**, reconnecting at <m:ss>…"* —
    first wedge per track. Bot rebuilds the voice gateway session and
    resumes the track at its previous position. **Audio should
    return ~3-5s after this message.**
  - ⏭️ *"Couldn't keep **<track>** playing (audio dropped after the
    retry). Skipping."* — second wedge on the same track. Bot moves
    to the next queued track. This usually means the track itself
    is structurally bad; queue advances normally.

If you DON'T see either message and music is silent for >15s:

1. Check Lavalink for the Koe voice gateway error:
   ```bash
   sudo journalctl -u lavalink --since "5min ago" --no-pager | \
     grep -iE 'koe|voice gateway|recvAddress'
   ```
   `Connection reset by peer` on `recvAddress(..)` is the classic
   shape — Discord's voice UDP path dropped.

2. Check the bot's heartbeat is running:
   ```bash
   sudo journalctl -u bot --since "5min ago" --no-pager | \
     grep -iE 'voice heartbeat|wedge detected'
   ```
   You should see periodic activity from the heartbeat task. If
   silence here means the heartbeat task crashed; restart the bot:
   `sudo systemctl restart bot`.

3. Manual recovery fallback (same effect as the auto-recovery, just
   from the user's keyboard):
   ```
   /music stop      # tears down the voice session
   /music play <whatever was playing>
   ```

4. If the voice gateway is broken at the Discord/network level (not
   our bug), the auto-recovery will repeat-skip every track. In that
   case `/music stop` + try again in a minute or two. Discord's
   voice infra occasionally has transient incidents — see
   <https://discordstatus.com>.

### 18. Voice-recovery auto-skipped every track (Discord-side incident)

Symptom: every track gets ⏭️-skipped within ~10s of starting. The
recovery loop is firing correctly, but Discord's voice servers are
sick at the network layer and we can't keep ANY session alive.

Confirm:
1. Check <https://discordstatus.com> for an active voice incident.
2. `sudo journalctl -u lavalink -e -n 100 | grep -c recvAddress` — if
   this returns >5 within a minute, you're seeing a systemic Discord
   issue, not a bot bug.

There's no useful auto-recovery; Discord has to fix it. Stop the bot
out of the voice channel so it doesn't burn the IDENTIFY rate limit
retrying:

```
/music stop
```

Try again in 15-30 minutes. If the incident is fixed and music still
won't stay connected, `sudo systemctl restart bot` to clear any
stuck per-track recovery state.

### 19. Roll back to Cloud Run

For the ~1 week soak (or longer if needed), `gcp-bot-runtime` is kept
at `min=0, max=1`. To restore traffic:

1. Bump min_instances to 1 in `infra/envs/prod/terraform.tfvars` (or
   pass via the module input):
   ```hcl
   module "bot_runtime" {
     # ...
     min_instances = 1
   }
   ```
   `terraform apply`.

2. **Before the new Cloud Run revision finishes coming up**, stop the
   bot on bot-vm so the Discord gateway WS is released:
   ```bash
   gcloud compute ssh bot-vm --tunnel-through-iap --zone us-central1-a --project mr-swede
   sudo systemctl stop bot
   sudo systemctl disable bot bot-watchdog.timer   # prevent the watchdog from restarting it
   ```
   If you don't do this, both replicas will try to hold the gateway
   WS simultaneously and Discord will throw IDENTIFY rate limits.

3. Verify in Cloud Run logs that the bot started cleanly and is
   serving `/livez`:
   ```bash
   gcloud run services logs read mr-swede --region=us-central1 --limit=50
   ```

Re-cutover to bot-vm is the reverse (enable+start the bot service on
bot-vm; scale Cloud Run back to 0).

If you're debugging a regression: hit Lavalink directly with `curl /v4/info` and check the version, then check the bot's `pyproject.toml` for the wavelink pin. Mismatches usually surface as Discord close codes 4003 (auth) or 4017 (E2EE required) in the Lavalink logs.
