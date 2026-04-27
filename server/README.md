# Valheim server runtime

Static artifacts that get baked into the Valheim VM via the GCE
startup-script metadata key. These files have **no GCP-specific
dependencies** beyond `fetch-secrets.sh` -- everything else runs
identically on any Linux host with Docker installed, which is what
makes the AWS swap path realistic later.

## Layout

```
server/
├── docker-compose.yml          # the only thing the VM actually runs
├── startup-script.sh.tftpl     # Terraform template; rendered into metadata.startup-script at apply time
├── scripts/
│   ├── fetch-secrets.sh        # pulls SERVER_PASS from Secret Manager
│   ├── valheim-fetch-secrets.service  # systemd oneshot
│   └── valheim.service                # systemd service wrapping compose
└── README.md
```

## Boot-time data flow

```
google-guest-agent fetches metadata.startup-script (every boot)
        │
        └── runs the rendered bash script as root
                  │
                  ├── installs Docker (first boot only)
                  ├── formats + mounts the persistent data disk (first boot only)
                  └── drops these files:
                       /opt/valheim/docker-compose.yml
                       /opt/valheim/scripts/fetch-secrets.sh
                       /etc/systemd/system/valheim-fetch-secrets.service
                       /etc/systemd/system/valheim.service
                       /etc/valheim/world.env  (SERVER_NAME, WORLD_NAME)

systemd starts valheim.service
        │
        └── Requires=valheim-fetch-secrets.service (runs first)
                  │
                  └── writes /etc/valheim/secret.env (SERVER_PASS, 0600)
        │
        └── docker compose up reads both env files
```

## Why a startup-script and not cloud-init?

GCP's standard Debian image (`debian-cloud/debian-12`) does NOT include
cloud-init. Setting `metadata.user-data` on a VM running this image
leaves the user-data blob unprocessed forever. The
`metadata.startup-script` mechanism (handled by `google-guest-agent`)
IS supported by default and runs the script as root on every boot.
The script itself is idempotent -- subsequent boots skip the apt /
Docker install and just re-enable the systemd units.

## Why two env files?

`world.env` is rewritten by the bot when a user runs `/world switch`
(planned). `secret.env` is fetched fresh from Secret Manager on every
boot and never appears in instance metadata or Terraform state.
Splitting them keeps the rotation surface small -- changing the
password is `gcloud secrets versions add` + a service restart, no
Terraform run.

## Cloud-portability

The startup-script template only assumes:
- a Linux distro with apt and systemd
- a metadata server reachable at `http://metadata.google.internal/...`
- a Secret Manager-style HTTP API for the secret fetch

Swapping to AWS would mean replacing `fetch-secrets.sh` with an
SSM Parameter Store / Secrets Manager equivalent and rewriting the
metadata-server URL inside it. The script's apt-install step is already
vendor-neutral; the compose file and systemd units are unchanged.

On AWS, the startup-script ships via EC2 user-data (with cloud-init
present in the AMI by default) instead of GCE startup-script -- but
the script content is the same.
