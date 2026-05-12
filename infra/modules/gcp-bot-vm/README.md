# Module: gcp-bot-vm

Single GCE VM that hosts BOTH the Mr. Swede Discord bot AND
Lavalink. Replaces the previous architecture where the bot ran on
Cloud Run and Lavalink ran on its own VM (`gcp-lavalink-vm`).

The 2026-05-10 architecture decision in `docs/prd.md` covers the
tradeoffs: ~$35/mo savings + zero Lavalink cold-start UX cost, at
the cost of rebuilding the deploy + auto-restart machinery
ourselves with systemd.

## What this module creates

| Resource | Purpose |
|---|---|
| `google_compute_instance.bot_vm` | e2-small Debian 12 VM in the shared Valheim VPC. Same shape as the other VMs. |
| `google_compute_firewall.bot_vm_iap_ssh` | IAP SSH (port 22 from `35.235.240.0/20`) only. **No public 2333**: Lavalink is localhost-only now. |
| `google_secret_manager_secret_iam_member.bot_vm_can_read_spotify_credentials` | The only IAM grant the VM needs that `mr-swede-sa` didn't already have. Discord/Valheim/Lavalink-password secrets are still granted via `gcp-bot-runtime/secret.tf`. |

The VM uses `mr-swede-sa` (passed in from `gcp-bot-runtime`'s
output), so all existing IAM bindings -- Discord secret, Valheim
secret, Lavalink secret, the `mrSwedeVmController` custom role on
the Valheim VM, Cloud Build's `actAs` permission -- are inherited
for free.

## Runtime layout

```
/opt/lavalink/           # Lavalink.jar + plugins + application.yml
/opt/lavalink/plugins/   # youtube-plugin, lavasrc -- downloaded by lloesche-style fetch
/etc/lavalink/           # lavalink env files (server.env, secret.env)

/opt/mr-swede/           # git clone of the repo
/opt/mr-swede/bot/       # bot subtree
/opt/mr-swede/bot/.venv/ # in-project poetry venv
/etc/bot/                # bot env files (bot.env, secrets.env)
/var/lib/bot-watchdog/   # watchdog state (consecutive failure count)
```

## Systemd units

- `lavalink-fetch-secrets.service` (oneshot): fetches `lavalink-server-password`
  + `spotify-client-credentials` from GSM into `/etc/lavalink/secret.env`.
- `lavalink.service`: runs `java -jar /opt/lavalink/Lavalink.jar`. Same
  unit file the standalone Lavalink VM used.
- `bot-fetch-secrets.service` (oneshot): fetches `lavalink-server-password`
  into `/etc/bot/secrets.env` for the bot's WS auth.
- `bot.service`: runs `/opt/mr-swede/bot/.venv/bin/python -m src.main`.
  `Requires=` bot-fetch-secrets.service + lavalink.service so ordering
  is deterministic.
- `bot-watchdog.timer` + `bot-watchdog.service`: every 60s, curl
  localhost:8080/livez. 5 consecutive failures -> `systemctl restart
  bot.service`. Replicates Cloud Run's liveness_probe.

## Deploy

Manual SSH-based per the 2026-05-10 decision. Bot iteration:

```bash
gcloud compute ssh bot-vm --tunnel-through-iap --zone=us-central1-a
cd /opt/mr-swede
sudo -u bot git pull
sudo -u bot bot/.venv/bin/poetry -C bot install --no-interaction --no-root
sudo systemctl restart bot.service
```

For TF template changes (systemd units, fetch-secrets scripts):
`terraform apply -target=module.bot_vm`. The metadata.startup-script
gets updated; restart `google-startup-scripts.service` or reboot the
VM to apply.

## Why not Cloud Run anymore

Cost: ~$45/mo for Cloud Run vs ~$10/mo for this e2-small. The
managed CI/CD + `/livez` kill-and-replace mechanisms Cloud Run
provided are valuable but replicable with systemd + a watchdog
timer (see `bot-watchdog.*` files). For a hobby project that's
iterated infrequently, $35/mo savings + zero Lavalink cold-start
UX wins out.

## Why bundled with Lavalink

Two services, same RAM/CPU budget: `bot` ~500MB, `Lavalink` JVM
~1GB at `-Xmx512m`. Localhost connection eliminates Lavalink's
cold-start friction entirely -- music starts in <1s after
`/music play`. Plus the firewall surface shrinks: Lavalink no
longer needs port 2333 open to the public internet.

The tradeoff: a bug in Lavalink that crashes the VM takes down the
bot too. We accept that for the simpler topology + lower cost.
