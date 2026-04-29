# Lavalink server runtime

Static artifacts that get baked into the Lavalink VM via the GCE
startup-script metadata key. Mirrors `server/`'s shape (Valheim) so
ops procedures translate directly.

## Layout

```
server/lavalink/
├── application.yml             # Lavalink config (Spring Boot env substitution)
├── startup-script.sh.tftpl     # Terraform template; rendered into metadata.startup-script
├── scripts/
│   ├── fetch-secrets.sh        # pulls SERVER_PASSWORD from Secret Manager
│   ├── lavalink-fetch-secrets.service  # systemd oneshot
│   └── lavalink.service                # systemd service running java -jar directly
└── README.md
```

Note: no Dockerfile / docker-compose. Lavalink runs as a plain
systemd service from the upstream Lavalink.jar. The
`ghcr.io/lavalink-devs/lavalink:*` Docker images shipped a broken
bundled JDK on multiple tags (random `ClassFormatError` on bootstrap
classes), and Lavalink is a self-contained jar with no
compose-style orchestration needs -- so the Docker layer was just
an indirection without benefit.

## Boot-time data flow

```
google-guest-agent fetches metadata.startup-script (every boot)
        │
        └── runs the rendered bash script as root
                  │
                  ├── installs openjdk-21-jre + curl (first boot only)
                  ├── creates `lavalink` system user (first boot only)
                  ├── downloads Lavalink.jar to /opt/lavalink/Lavalink.jar
                  │   (first boot, or when LAVALINK_VERSION in the script changes)
                  └── drops these files (every boot):
                       /opt/lavalink/application.yml
                       /opt/lavalink/scripts/fetch-secrets.sh
                       /etc/systemd/system/lavalink-fetch-secrets.service
                       /etc/systemd/system/lavalink.service
                       /etc/lavalink/server.env  (SERVER_PORT)

systemd starts lavalink.service (User=lavalink)
        │
        └── Requires=lavalink-fetch-secrets.service (runs first)
                  │
                  └── writes /etc/lavalink/secret.env (LAVALINK_SERVER_PASSWORD, 0600)
        │
        └── EnvironmentFile= loads both env files
        │
        └── ExecStart=/usr/bin/java -Xmx1G -jar /opt/lavalink/Lavalink.jar
                │
                └── Spring Boot resolves ${LAVALINK_SERVER_PASSWORD}
                    from the systemd env at startup
```

## Why no Docker?

The two Valheim runtime artifacts (`server/`) use Docker because the
canonical `lloesche/valheim-server` image ships a curated bundle of
the Valheim binary + auto-updater + cron'd backups + tooling. There's
real upstream value from running their image.

Lavalink has no comparable curation: it's literally `java -jar
Lavalink.jar`. The official Docker images
(`ghcr.io/lavalink-devs/lavalink:*`) bundle a JDK and provide no
extras over running the jar yourself. Worse, those images have shipped
broken jars and broken bundled JDKs at various points; running plain
Java on the VM avoids both classes of upstream regressions.

Trade-offs we're accepting by going Docker-less:

- **Lavalink upgrades require a startup-script edit** (bumping
  `LAVALINK_VERSION`) rather than a tag bump. Acceptable -- we
  already edit the script for every meaningful runtime change.
- **No container sandbox.** Lavalink runs as the unprivileged
  `lavalink` system user, which is the standard non-Docker
  isolation. The VM only runs Lavalink, so process-level isolation
  isn't load-bearing.

## Why a separate Lavalink VM (not a sidecar in the bot)?

- Lavalink is JVM, the bot is Python -- one container running both
  is bigger, slower to cold-start, and harder to ops.
- Lavalink wants persistent CPU when streaming audio; the bot's
  Cloud Run image already runs `cpu_idle=false` for the same
  reason, but bundling them doubles the always-on bill.
- The on-demand-VM pattern (idle-watcher stops it when nobody's
  playing) yields ~$2-3/mo at hobby usage, vs ~$15-20/mo for
  always-on Cloud Run.

## Why the official `lavalink-devs/Lavalink:4` image?

It's the canonical upstream. Configuration is via env vars + an
optional `application.yml` mount; we use both. Plugins are
downloaded by the JVM on first boot from the Maven repo declared
inline in `application.yml`.

## YouTube via plugin

Lavalink's built-in YouTube source is deprecated upstream
(`youtube: false` in `application.yml`). The community-maintained
`dev.lavalink.youtube:youtube-plugin` is the supported path; pinned
to `1.13.5` in `application.yml`. Bumping the version is a
`server/lavalink/application.yml` edit + `terraform apply` (which
pushes the new metadata) + `gcloud compute instances reset` (which
re-runs the startup-script and the new app.yml triggers Lavalink to
re-resolve plugin deps on next boot).

## Cloud-portability

The runtime artifacts assume:
- Linux distro with apt + systemd
- Docker 20+ with the compose v2 plugin
- A metadata server reachable at `http://metadata.google.internal/...`
- A Secret Manager-style HTTP API for the password fetch

Swapping to AWS = replace `fetch-secrets.sh` (SSM Parameter Store /
Secrets Manager) and reconfigure the metadata-server URL inside it.
The compose file, application.yml, and systemd units are unchanged.
