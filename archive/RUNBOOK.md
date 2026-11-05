# The archive: setting it up

Everything on this page is yours to run. Nothing here is done by an agent, and nothing in the repository changes Proxmox
or Cloudflare. Each step says where it runs: **Proxmox node**, **the container**, **Cloudflare dashboard**, or **the deck**.

The archive is one small server (`gatbox-archive`) in its own Debian 13 container. It keeps the library (games, manuals,
rail limits, facts, ROM lists, ROM images) and the files, and answers the deck, your partner's phone, and later the two
apps. It reaches the internet only through a Cloudflare tunnel, and it never calls out.

Words in `<ANGLE BRACKETS>` are yours to fill in: your storage names, bridge, container number and hostname. I don't know
your node, so none of them is guessed. Cloudflare's menus and Proxmox's options were read from their documentation on
2026-11-04; if a menu has moved, the name in bold is what to look for.

## The order, and why

1. The container (Proxmox).
2. The archive in it (install script).
3. **The Access application, before the tunnel route.** A route with no Access application in front of it is open to the
   whole internet.
4. The tunnel and its route.
5. Tokens for the deck and the apps.
6. The deck's side (comes with phase D3), then the first sync as a dry run.

## 1. The container (Proxmox node)

```bash
pveam update
pveam available --section system | grep debian-13
```

Pick the Debian 13 template that line shows, then:

```bash
pveam download <TEMPLATE_STORAGE> <TEMPLATE_FILE_NAME>
pct create <VMID> <TEMPLATE_STORAGE>:vztmpl/<TEMPLATE_FILE_NAME> \
  --hostname gatbox-archive --unprivileged 1 --cores 2 --memory 1024 --swap 512 \
  --rootfs <STORAGE>:8 \
  --mp0 <STORAGE>:50,mp=/srv/archive,backup=1 \
  --net0 name=eth0,bridge=<BRIDGE>,ip=dhcp \
  --onboot 1
pct start <VMID>
```

- `/srv/archive` is its own volume on purpose: the installer refuses to run if it is not a mount point, so a full or
  forgotten data disk can't silently fill the container's root disk. 50 GB is a start. Grow it later with
  `pct resize <VMID> mp0 +50G`.
- `backup=1` puts the data volume in the container's backups. Check `pct config <VMID> | grep mp0` shows it, and that the
  container is in your backup job (**Datacenter > Backup**). Everything the archive holds is on that volume.
- The container needs outbound internet (apt, the fonts at install time, and the tunnel on port 7844). It needs **no
  inbound port**: the tunnel dials out. Don't forward anything to it.

## 2. The archive (the container)

```bash
pct enter <VMID>
apt-get update && apt-get install -y git
git clone https://github.com/digitalunconciousness/Gatbox.git
cd Gatbox
git checkout phase-d-archive        # until phase D is merged to main; drop this line afterwards
bash archive/install.sh --check     # what a run would change; changes nothing
bash archive/install.sh
```

The script installs the packages (`python3`, `poppler-utils`, `curl`), the `gatbox-archive` user, the server, the pages'
two fonts (pinned by checksum), the unit and `/etc/gatbox-archive/archive.conf`, and starts the service. Re-run it after a
`git pull` to update; a re-run changes nothing when nothing changed, and works offline once everything is installed.

Check it:

```bash
systemctl status gatbox-archive --no-pager
curl -s http://127.0.0.1:8080/api/v1/health
```

Open `/etc/gatbox-archive/archive.conf` and set `ARCHIVE_PEOPLE` to your email and your partner's, comma-separated. It is a
second lock behind Cloudflare Access: only these addresses get pages and can edit. Then `systemctl restart gatbox-archive`.

## 3. The Access application (Cloudflare dashboard)

You need a domain on your Cloudflare account. Pick the public name now, for example `archive.<YOUR_DOMAIN>`; it is
`<HOSTNAME>` below.

**The service token for the deck.** Go to **Zero Trust > Access controls > Service credentials > Service Tokens** and
select **Create Service Token**. Name it `gatbox-deck`, choose a **Service Token Duration**, select **Generate token**.
The Client ID and the Client Secret appear once. Copy both somewhere safe now (a password manager); Cloudflare can't show
the secret again. **The token expires** on the date its duration gives. Put that date in your calendar. Cloudflare can send
an alert a week ahead, and **Refresh** extends it by a year. When it lapses the deck's sync stops with an authorization
error and nothing else breaks.

**The application.** Go to **Zero Trust > Access controls > Applications**, select **Create new application**, then
**Self-hosted and private**, then **Add public hostname** and choose `<HOSTNAME>`'s domain and subdomain. Under **Access
policies** make two policies:

| Name | Action | Include |
|---|---|---|
| people | Allow | Emails: yours, your partner's |
| deck | Service Auth | Service Token: `gatbox-deck` |

Pick the identity provider you use, choose a **Session Duration**, and select **Create**. Access applications deny by
default, so anyone not matching one of the two policies is turned away.

## 4. The tunnel (Cloudflare dashboard, then the container)

Go to **Networking > Tunnels**, select **Create a tunnel**, name it `gatbox-archive`, and select **Create Tunnel**. Choose
Debian, 64-bit, copy the command the dashboard shows, and run it **in the container**. It installs `cloudflared` and
starts it as a service. When the tunnel shows `Healthy`, select **Continue**.

Then on the tunnel's **Routes** tab select **Add route**, then **Published application**. Enter the subdomain and domain
of `<HOSTNAME>`, and as the **Service URL** enter `http://localhost:8080`. Select **Add route**.

**The one rule about the tunnel.** The archive reads who is signed in from the token Access attaches to each request
(`Cf-Access-Jwt-Assertion`, its `email` claim). It does not check the token's signature: it has no crypto library and never
calls out for Cloudflare's keys. That is safe only because nothing but the tunnel can reach port 8080, and every route of
the tunnel that points at it is behind the Access application from step 3. So: never add a second route to port 8080 that
isn't covered by that application, and don't publish this tunnel's port 8080 any other way.

Check it from your own computer:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<HOSTNAME>/api/v1/health
```

That should **not** print 200 (Access turns it away or redirects it to a login). Then, with the service token, it should:

```bash
read -rs CF_SECRET
curl -s -H "CF-Access-Client-Id: <CLIENT_ID>" -H "CF-Access-Client-Secret: $CF_SECRET" https://<HOSTNAME>/api/v1/health
```

and print `{"ok": true, "app": "gatbox-archive", ...}`. Last, open `https://<HOSTNAME>/` in a browser, sign in, and you
should see an empty games list. Your partner signs in the same way, from his phone.

## 5. Tokens (the container)

The archive has its own tokens behind Cloudflare's. One for the deck (it writes), one per app (they only read):

```bash
gatbox-archive token add deck --role device
gatbox-archive token add cabcrawl --role reader
gatbox-archive token add tracker --role reader
gatbox-archive token list
```

Run them as root in the container, with the code updated first (`cd ~/Gatbox && git pull && bash archive/install.sh`). The command
carries on as the archive's own user, which owns `/srv/archive`, before it opens anything, so the token file belongs to the
service and not to root. (A token file made by root is unreadable to the service, and every token then fails with no error
anywhere.) If your shell says `gatbox-archive: command not found`, its PATH lacks `/usr/local/bin`: use the full path,
`/usr/local/bin/gatbox-archive`, or open a fresh root shell. On a copy of the code from before this change, run each command as
`runuser -u gatbox-archive -- /usr/local/bin/gatbox-archive token add ...` instead.

Each token is printed once and stored only as a hash. Copy it when it appears. A lost one is revoked and replaced:
`gatbox-archive token revoke <name>`, then add it again under a new name. A reader token sees only the games its app asks
for, can read manuals, and is never given a ROM image.

**How the apps reach it.** Either through the tunnel (each app gets its own Cloudflare service token and a Service Auth
policy, like the deck's) or, if you'd rather keep that traffic on your network, on the container's private address:
set `ARCHIVE_PRIVATE_BIND=<CONTAINER_IP>:8081` and `ARCHIVE_PRIVATE_ALLOW=<APPS_NETWORK_CIDR>` in `archive.conf`, run
`bash archive/install.sh` again, and the apps use `http://<CONTAINER_IP>:8081` with their reader token. That listener answers
reader tokens only, read-only, and no pages.

## 6. The deck

The deck needs `/etc/gatbox/archive.conf` (mode 600, root), four lines:

```
ARCHIVE_URL=https://<HOSTNAME>
ARCHIVE_TOKEN=<the deck's device token from step 5>
ARCHIVE_ACCESS_ID=<the service token's Client ID from step 3>
ARCHIVE_ACCESS_SECRET=<the service token's Client Secret from step 3>
```

The bootstrap creates the file empty (`bash bootstrap/gatbox-bootstrap.sh`, after you have read what it changes with
`--check`); fill it with `sudo nano /etc/gatbox/archive.conf`. The sync service loads it as a credential, which is a change
to a systemd unit, so BRINGUP.md shows it to you as a diff first ("the D3 install diff") and it is not applied until you
say so. With no file, or an empty one, nothing here runs and the deck behaves as before.

The first sync is a dry run (`GATBOX_SYNC_DRYRUN=1`): it prints what it would send and fetch, changes nothing, and you read
it before the real one.

## Looking after it

- **Backups.** The Proxmox backup of the container is the backup. To restore, restore the container and start it.
- **Updating.** In the container: `cd Gatbox && git pull && bash archive/install.sh`.
- **Logs.** `journalctl -u gatbox-archive`: the method, the path (no query) and the status of each request. It never logs a
  token, and never the `models=` list an app asks for.
- **Status.** `gatbox-archive status` shows the library's size and counts.

| Seen | Likely cause |
|---|---|
| The pages say "Not signed in" | The request didn't come through the tunnel, or `ARCHIVE_PEOPLE` doesn't list that email |
| The deck's sync fails with an authorization error | The Cloudflare service token expired (step 3), or the device token was revoked (step 5) |
| Upload says a PDF is too large | The pages take 90 MB a PDF (Cloudflare's request limit is 100 MB); the deck's own uploads go in 8 MiB parts and are not limited by it |
| `gatbox-archive: command not found` | The shell's PATH lacks `/usr/local/bin`: use `/usr/local/bin/gatbox-archive` |
| Pages show a plain font | The fonts couldn't be fetched at install time; run `bash archive/install.sh` again with internet |
| `install.sh` says `/srv/archive` is not a mount point | The data volume from step 1 isn't mounted; check `pct config <VMID>` |
