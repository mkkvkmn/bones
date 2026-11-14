# ship: deploy bones sites to an OpenBSD server

Scripts that set up one OpenBSD server and publish bones sites to it, with isso comments for
one of them. They are idempotent: running a script again converges to the same state and
reports what changed. The pattern follows `core/ship` (secrets outside repos, private
staging, monitor + heartbeat, encrypted backups), trimmed for static sites.

This folder is public. It holds **no hostnames, IPs, emails or secrets**; `deploy-server.sh`
refuses to run if it finds any of this deployment's in here.

| What | Where |
| --- | --- |
| Scripts and templates (this folder) | `bones/ship/` (public) |
| Which sites, per-site nginx extras and CSP additions | `<sites repo>/ship/` (private) |
| Settings and secrets | `~/.secrets/<sites repo folder>/env_vars.<env>.sh` |
| Local comment backups | `~/.backups/sites/` |

bones and the sites repo must sit side by side (e.g. `C:\a\repos\bones` and `C:\a\repos\sites`).
Run everything **from the sites repo root** in Git Bash: `bash ../bones/ship/<script> prod ...`,
or from anywhere with a launcher that takes the site as the app: `ship <site>
prod <script> [args]` for these scripts, `ops <site> prod <command> [args]` for the server
commands of `ops.sh`. deploy-site and rollback act on that site; the other scripts and the ops
commands act on the whole server, whichever site you name. `ship <site>` and `ops <site>` list them.

```cmd
ship <site> prod deploy-site
ship <site> prod rollback --list
ops <site> prod check
```

## Commands (run on your machine)

| Command | What it does |
| --- | --- |
| `init-secrets.sh <env>` | Create the settings file from `env/env_vars.example`; generates the isso admin password; never overwrites; lists key **names** still to fill |
| `deploy-server.sh <env>` | Set up or re-converge the server (packages, users, sshd, pf, nginx, certificates, isso, mail, cron). Second run changes nothing |
| `deploy-site.sh <env> <site>` | Build the site with bones and publish it as a new release; smoke test; automatic switch back on failure |
| `rollback.sh <env> <site> [--list\|<id>]` | Switch to an earlier kept release |
| `backup.sh <env>` | Fresh encrypted comment backup, downloaded to `~/.backups/sites/` |
| `restore.sh <env> --list\|drill [file]\|live <file>` | List, test or restore comment backups (decrypted locally) |
| `ops.sh <env> <cmd>` | `info`, `status [--test-alert]`, `check`, `logs`, `backups`, `ssh`, `restart`, `maintenance on\|off\|status`, `tls-renew`, `patch`, `import-cert`; `ops.sh help` lists them. Same names as the core apps' `ops.sh` |

`server/` holds the scripts that run **on** the server (`setup.sh`, `monitor.sh`,
`backup-isso.sh`, `auto-update.sh`, `renew-certs.sh`). `deploy-server.sh` installs them; never
run or copy them by hand.

## First setup

1. **VPS**: Vultr (or similar), latest OpenBSD, with your ed25519 public key. Note the IPv4 address.
   Give the key a passphrase (or use a hardware `ed25519-sk` key): with passwordless doas it is
   effectively the root key.
2. **Settings**: `bash ../bones/ship/init-secrets.sh prod`, then fill the keys it lists in the file
   (each is explained there). Run it again until it says `complete`.
3. **Backup key** (once, offline):
   ```bash
   age-keygen -o ~/age-sites-backup.key     # prints "Public key: age1..."
   ```
   Put the public key in `BACKUP_AGE_PUBLIC_KEY`; store the private key file in your password manager.
4. **Mail** (`MAIL_RELAY=tem`): in Scaleway Transactional Email add your sending domain, add the
   SPF/DKIM/DMARC records it shows to DNS, and create an IAM API key limited to Transactional
   Email. `TEM_SMTP_USER` = Project ID, `TEM_SMTP_PASSWORD` = the key's secret,
   `MAIL_FROM` = an address on the verified domain. `MAIL_RELAY=none` skips all of this
   (no comment notifications; alerts only via healthchecks.io).
5. **Server**: `bash ../bones/ship/deploy-server.sh prod` (a few minutes the first time). It connects
   as root once, creates `SERVER_USER`, proves that user can log in and use doas, then turns root
   SSH off. Run it again: everything should be listed as unchanged.
6. **Sites**: `bash ../bones/ship/deploy-site.sh prod <site>` for each site in `ship/sites.conf`.
7. **Patches**: `bash ../bones/ship/ops.sh prod patch` (may reboot once).

Until DNS points at the server, sites are served over plain HTTP for testing:
`curl -H "Host: example.com" http://<server ip>/`. After switching DNS, run `deploy-server.sh`
again: it gets the certificates and turns HTTPS on. To keep HTTPS during the switch, copy the
live certificate first: `ops.sh prod import-cert <domain> <user@old-host>`, then `deploy-server.sh`.

## What the server looks like

| Path | Contents |
| --- | --- |
| `/var/www/sites/<site>/current` | symlink to the live release |
| `/var/www/sites/<site>/releases/<id>/` | last `KEEP_RELEASES` builds (root-owned, read-only for nginx); `<id>` = UTC time + bones and sites commit |
| `/var/www/sites/<site>/shared/pub/` | `<site>/pub/` from the sites repo, outside releases |
| `/etc/nginx/ship/` | one config per site, headers, comments proxy |
| `/var/isso/` | isso venv (root-owned) and `db/comments.db` (`_isso`, 700) |
| `/etc/isso.conf` | isso config with secrets (`root:_isso`, 640) |
| `/var/ship/` | root-only: settings copy, ops scripts, `backups/` |
| `/var/log/ship/monitor.log` | monitor, guard, backups, renewals, auto-update |

## Security

- **pf**: only 22, 80, 443 and ping in. SSH is rate limited (more than 30 new connections a minute puts the
  address in `<bruteforce>`; that blocks only its ssh, for about an hour).
- **sshd**: keys only, root login off, `AllowUsers <SERVER_USER>`, `MaxAuthTries 3`, no forwarding.
- **nginx**: TLS 1.2/1.3 (Mozilla intermediate), `server_tokens off`, small body and short timeouts,
  per-IP rate limits (pages; comment writes 6/min). Unknown host names and the bare IP get the
  connection closed. Only the bare domain serves content; `www.` and `http://` redirect to it.
- **Headers** on every response: HSTS (with subdomains), `X-Frame-Options: DENY`, `nosniff`,
  `Referrer-Policy`, `Permissions-Policy`, and a strict CSP: `default-src 'none'`, scripts only from
  the site (and the comments host), `'unsafe-inline'` for styles only (bones inlines CSS),
  `frame-ancestors 'none'`. A site adds sources in `<sites repo>/ship/<site>.csp`
  (e.g. `form-action https://newsletter.example`).
- **isso**: runs as `_isso`, listens on 127.0.0.1 only, its code is read-only to it. The admin UI is
  off unless `ISSO_ADMIN_ALLOW_IP` is set, and then only that IP reaches `/admin`.
- **Secrets**: only in the settings file on your machine and in root- or service-only files on the
  server. Uploads go to a private temp dir that is deleted afterwards. Scripts print key names,
  never values.
- **Updates**: nightly `syspatch` (reboot when needed), package updates and firmware; release
  upgrades (`sysupgrade`) stay manual.

## Monitoring

- `monitor.sh` every 15 min: nginx/isso running, every site through nginx with its real host name,
  certificate expiry, disk, swap, backup age, auto-update status. Mails `ADMIN_EMAIL` when the state
  changes (and every 6 h while failing) and pings `MONITOR_HEARTBEAT_URL`.
- Restart guard every minute: starts nginx/isso if they died (paused by `ops.sh <env> maintenance on`).
- **healthchecks.io** (set this up): one check, period 15 min, grace 30 min; its ping URL goes in
  `MONITOR_HEARTBEAT_URL`. It alerts when the server stops reporting (dead host, broken cron).
- **UptimeRobot** (set this up): HTTPS monitors on each site and on
  `https://comments.<domain>/js/embed.min.js`, with SSL expiry alerts. It sees DNS, firewall and
  certificate problems that only show from outside.
- Test the alert path: `ops.sh prod status --test-alert`.

## Backups

The comment database is backed up nightly on the server (and before every restore), encrypted
with age to `BACKUP_AGE_PUBLIC_KEY`; the server never has the private key. Kept 14 days.
`backup.sh prod` downloads a fresh one to `~/.backups/sites/`. Run `restore.sh prod drill`
monthly: it decrypts locally, checks integrity and compares the comment count with the live one.
Site content needs no backup: it is rebuilt from git.

## Rotating secrets

- isso admin password: delete the `ISSO_ADMIN_PASSWORD` line, re-run `init-secrets.sh` (it generates
  a new one), then `deploy-server.sh`.
- Scaleway key: create a new one in the console, update `TEM_SMTP_PASSWORD`, `deploy-server.sh`,
  then delete the old key.
- healthchecks.io URL: regenerate it in the check's settings, update, `deploy-server.sh`.
- age key: make a new pair, update `BACKUP_AGE_PUBLIC_KEY`, `deploy-server.sh`; keep the old private
  key as long as you keep backups made with it.
