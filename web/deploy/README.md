# Deploying WorkOnward Read Web (GitHub Actions → DigitalOcean droplet)

Pushing to **`main`** (changes under `web/**`) builds the image, pushes it to
GitHub Container Registry, and redeploys it on the droplet as a Docker
container behind Caddy (automatic HTTPS).

```
push main ──► GH Actions build ──► ghcr.io/rahvis/workonward-read-web ──► SSH droplet ──► docker compose up
                                                                         │
                                                        Caddy (TLS) ──► https://redact.gitdate.ink
```

## One-time setup

### 1. Repo secrets (set for `rahvis/redact`)

| Secret            | Value                                   |
|-------------------|-----------------------------------------|
| `DROPLET_HOST`    | `redact.gitdate.ink` (or `104.248.124.39`) |
| `DROPLET_USER`    | `root`                                  |
| `DROPLET_SSH_KEY` | private SSH deploy key (see below)      |
| `SITE_DOMAIN`     | `redact.gitdate.ink`                    |

`GITHUB_TOKEN` is automatic — used to push/pull the image from GHCR.

### 2. Install the deploy key on the droplet

CI authenticates with an SSH key, not your password. Add the **public** key to
the droplet once (you'll be asked for the root password):

```bash
ssh root@redact.gitdate.ink \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh && \
   echo "PASTE_PUBLIC_KEY_HERE" >> ~/.ssh/authorized_keys && \
   chmod 600 ~/.ssh/authorized_keys'
```

### 3. Prerequisites on the droplet

- DNS: `redact.gitdate.ink` → droplet IP (already set: `104.248.124.39`).
- Ports **80** and **443** open (Caddy needs them for ACME + serving). The
  deploy script opens them via `ufw` automatically if ufw is active.
- Docker is installed automatically on first deploy if missing.

## Deploy

```bash
git add web .github && git commit -m "Deploy WorkOnward Read Web" && git push origin main
```

Watch it in the repo's **Actions** tab. When it's green:

```
https://redact.gitdate.ink
```

Trigger a redeploy without a code change from **Actions → Deploy WorkOnward Read Web →
Run workflow**.

## Security notes

- **Rotate the droplet root password** — it was shared in plaintext. Better
  still, disable password SSH (`PasswordAuthentication no`) once the deploy key
  works, and consider a non-root deploy user.
- The pipeline never stores your password anywhere; it uses the SSH deploy key.
- The image is pulled from GHCR using the workflow's short-lived `GITHUB_TOKEN`.

## Fallback: password auth instead of an SSH key

If you'd rather use the password (not recommended), set a `DROPLET_PASSWORD`
secret and, in `.github/workflows/deploy.yml`, replace both
`key: ${{ secrets.DROPLET_SSH_KEY }}` lines with
`password: ${{ secrets.DROPLET_PASSWORD }}`.

## Rollback

Images are tagged with the commit SHA. To pin a previous version on the droplet:

```bash
ssh root@redact.gitdate.ink
cd /opt/coverup   # deploy directory on the droplet (kept from the original setup)
WORKONWARD_IMAGE=ghcr.io/rahvis/workonward-read-web:<old-sha> \
  docker compose -f docker-compose.prod.yml up -d
```
