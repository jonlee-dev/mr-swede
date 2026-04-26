# Valheim server runtime

Static artifacts that get baked into the Valheim VM via cloud-init. These
files have **no GCP-specific dependencies** -- they run identically on any
Linux host with Docker installed, which is what makes the AWS swap path
realistic later.

## Layout

```
server/
├── docker-compose.yml          # the only thing the VM actually runs
├── cloud-init.yaml.tftpl       # Terraform template; rendered at apply time
├── scripts/
│   ├── fetch-secrets.sh        # pulls SERVER_PASS from Secret Manager
│   ├── valheim-fetch-secrets.service  # systemd oneshot
│   └── valheim.service                # systemd service wrapping compose
└── README.md
```

## Boot-time data flow

```
cloud-init drops files
        │
        ├── /opt/valheim/docker-compose.yml
        ├── /opt/valheim/scripts/fetch-secrets.sh
        ├── /etc/systemd/system/valheim-fetch-secrets.service
        ├── /etc/systemd/system/valheim.service
        └── /etc/valheim/world.env  (SERVER_NAME, WORLD_NAME)

systemd starts valheim.service
        │
        └── Requires=valheim-fetch-secrets.service (runs first)
                  │
                  └── writes /etc/valheim/secret.env (SERVER_PASS, 0600)
        │
        └── docker compose up reads both env files
```

## Why two env files?

`world.env` is rewritten by the bot when a user runs `/world switch`.
`secret.env` is fetched fresh from Secret Manager on every boot and never
appears in cloud-init metadata or Terraform state. Splitting them keeps
the rotation surface small -- changing the password is `gcloud secrets
versions add` + a service restart, no Terraform run.

## Cloud-portability

The cloud-init template only assumes:
- a Linux distro with apt and systemd
- a metadata server reachable at `http://metadata.google.internal/...`
- a Secret Manager-style HTTP API for the secret fetch

Swapping to AWS would mean replacing `fetch-secrets.sh` with an
SSM Parameter Store / Secrets Manager equivalent and the cloud-init
package install step (which is already vendor-neutral). The compose
file and systemd units are unchanged.
