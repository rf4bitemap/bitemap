# Running the BiteMap server (any VPS)

The stack is three containers: **PostgreSQL**, the **BiteMap API + website** (FastAPI) and **Caddy**, which gets a
Let's Encrypt certificate automatically. A small VPS (2 vCPU, 2-4 GB RAM) is more than enough.

## 1. Domain

Create a DNS `A` record (and `AAAA` if you use IPv6) for e.g. `bitemap.example.org` pointing at the VPS.

## 2. Server

```bash
# Debian/Ubuntu
curl -fsSL https://get.docker.com | sh
git clone https://github.com/rf4bitemap/bitemap.git && cd bitemap
cp .env.example .env
nano .env            # DOMAIN, ACME_EMAIL, POSTGRES_PASSWORD (long random string)
docker compose up -d --build
docker compose logs -f api
```

Open `https://<DOMAIN>` – the website should load and `https://<DOMAIN>/api/v1/health` returns `{"ok":true}`.
Ports 80 and 443 must be reachable (check your provider's firewall).

## 2b. Server that already runs a Caddy proxy in Docker

If ports 80/443 already belong to another project's Caddy container, don't start BiteMap's own Caddy:

```bash
cd /opt/bitemap
PROXY_NETWORK=<that project's docker network> docker compose -f deploy/compose.shared-proxy.yml --env-file .env up -d --build
```

The API joins that network as `bitemap-api`. Append `deploy/Caddyfile.site` to the existing Caddyfile, validate and
reload: `docker exec <caddy> caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` and
`docker exec <caddy> caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`.

Watch out: a Caddyfile that is bind-mounted as a single file and later *replaced* (editors, `cp`, re-deploys)
leaves the running container on the old file. If `caddy reload` says "config is unchanged", copy the file into the
container (`docker cp Caddyfile <caddy>:/tmp/Caddyfile.new`) and reload with `--config /tmp/Caddyfile.new`, or restart
the Caddy container.

## 3. Point the app at it

Set `DEFAULT_SERVER = 'https://<DOMAIN>'` in `client/bitemap_logger/__init__.py` and tag a release.
Users can also change the server in the app's settings.

## Updating

```bash
git pull
docker compose up -d --build
```

Map images are not in the git repository. Copy them once to the server:
`scp data/maps/*.webp root@<server>:/root/bitemap/data/maps/` – the folder is mounted into the container.

To stop accepting catches from old app versions (e.g. after a detection bug), set
`BITEMAP_MIN_CLIENT=0.1.4` in `.env` and run `docker compose up -d`. Older apps then get
HTTP 426, keep their catches and upload them once updated.

## Backups

```bash
# daily dump, keep 14 days  (crontab -e)
0 4 * * * cd /root/bitemap && docker compose exec -T db pg_dump -U bitemap bitemap | gzip > /root/backup/bitemap-$(date +\%F).sql.gz && find /root/backup -name 'bitemap-*.sql.gz' -mtime +14 -delete
```

Restore: `gunzip -c dump.sql.gz | docker compose exec -T db psql -U bitemap bitemap`

## Moderation

Every upload is checked (known fish and waterbody, weight below 125 % of the world record, coordinates in range,
time within 30 days, at most 400 catches per install and hour). For anything that still gets through:

```bash
docker compose exec api python -m app.admin stats
docker compose exec api python -m app.admin top-installs     # most active installs in the last 24 h
docker compose exec api python -m app.admin ban <install_id>  # hides all of its catches
```

## Privacy

The server stores per install only a SHA-256 hash of its token, creation/last-seen time and client version, and per
catch the fish, weight, length, trophy flag, waterbody, coordinates and times. IP addresses are only held in memory
for rate limiting. Put a short privacy notice with your contact details on the site if you run it publicly in the EU.
