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
6. The deck's side, then the first sync as a dry run.
7. The apps (cabcrawl and the tracker): their settings, then one keyed machine to prove the link.

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

**In the web UI instead.** The create-container wizard has no data volume, only the root disk, so the volume is added after the
container exists:

1. **Create CT**: General (hostname `gatbox-archive`, **Unprivileged container** ticked, a password or SSH key), Template
   (the Debian 13 one), Disks (the root disk, 8 GiB), CPU 2 cores, Memory 1024 MiB with 512 MiB swap, Network (your bridge,
   IPv4 **DHCP**). Tick **Start after created**, or start it yourself. Under **Options** make sure **Start at boot** is Yes.
2. Select the container, then **Resources**, then **Add**, then **Mount Point**. Storage: the one that holds your container
   volumes. Disk size: 50 GiB. Path: `/srv/archive`. Tick **Backup**: without it the volume is left out of the container's
   backups, and the backup is the archive's only backup.
3. A mount point added to an existing container does not appear until it restarts: **Reboot** it from the top right.
4. Check it. On the node, `pct config <VMID> | grep mp0` shows `mp=/srv/archive` and `backup=1`. In the container,
   `mountpoint /srv/archive` says it is a mountpoint. `install.sh` in step 2 refuses to run until that is true.

The same from the node's shell, for a container that already exists: `pct set <VMID> -mp0 <STORAGE>:50,mp=/srv/archive,backup=1`
and `pct reboot <VMID>`.

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
second lock behind Cloudflare Access: only these addresses get pages and can edit. Then run `bash archive/install.sh` again, which restarts the service because the file changed.

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

**Two more checks, from a machine that isn't signed in.** Run them again whenever you change anything in the Access
application. Both addresses must answer `302` (a redirect to `<TEAM>.cloudflareaccess.com`):

```bash
for p in / /api/v1/health; do printf '%s ' "$p"; curl -sS -o /dev/null -w '%{http_code}\n' "https://<HOSTNAME>$p"; done
```

A `401` for `/` is the archive itself saying "Not signed in", which means the request got past Access without being
stopped: the application doesn't cover that path, or a second application or a Bypass policy matches first. That matters
because the pages trust the identity header (see the rule above), so anyone could send a header of their own. Fix the
application so its destination is the bare hostname with no path, then run the loop again. The last check sends exactly that
forged header and must also print `302`:

```bash
J=$(printf '{"alg":"none"}' | base64 -w0 | tr '+/' '-_' | tr -d =).$(printf '{"email":"nobody@example.com"}' | base64 -w0 | tr '+/' '-_' | tr -d =).x
curl -sS -o /dev/null -w '%{http_code}\n' -H "Cf-Access-Jwt-Assertion: $J" https://<HOSTNAME>/
```

**All of these checks in one command**, from any machine with `curl`. It changes nothing and prints no secret:

```bash
bash archive/check.sh https://<HOSTNAME>
```

Put `ARCHIVE_ACCESS_ID` and `ARCHIVE_ACCESS_SECRET` in the environment and it checks the service token's side as well
(it reaches health, gets no page, and a wrong secret is turned away). On the deck,
`sudo bash archive/check.sh --conf /etc/gatbox/archive.conf` takes the address and the token from `archive.conf`. For the
apps' private listener (step 5), `ARCHIVE_READER_TOKEN=gbx_... bash archive/check.sh --private http://<CONTAINER_IP>:8081`
checks that a reader token reads the feed and that nothing else is served. Run it after any change in the Access
application.

**If you recreate the application,** attach both policies to the new one before you test: `people` (Allow) and `deck`
(Service Auth, with the `gatbox-deck` token, not Allow and not "Any Access Service Token"). Without `deck` the service-token
call gets a `302` again and the deck's sync stops.

**A `302` with the service token** means Cloudflare didn't match what you sent to a token that a Service Auth policy on this
application lists. Check, in this order: the policy's action is Service Auth; the policy is ticked on the application; the
Client ID in `archive.conf` ends in `.access`; and the secret is the whole secret. A secret cut short in the copy is
accepted by everything except Cloudflare, which answers every request with the same `302`, and the token's **Last seen** in
the dashboard stays empty. Copy the secret with the copy button. Before you save it, paste it at a hidden prompt and look at
its length, then check that the length of the value in `archive.conf` matches it:

```bash
read -rsp "secret: " S; echo; echo "pasted: ${#S}"; unset S
awk -F= '/^ARCHIVE_ACCESS_SECRET=/{print "in the file:", length($2)}' /etc/gatbox/archive.conf
```

If they differ, or you can't tell, **Rotate** the token in the dashboard and start again. The Client ID stays the same, so
the policy keeps working.

## Steps 3 and 4 from a terminal

Everything in steps 3 and 4 can be done with Cloudflare's API and `cloudflared` instead of the dashboard. These commands follow
Cloudflare's API reference and tunnel guide as read on 2026-11-05 ([Access service tokens][cf-st], [Access policies][cf-pol],
[Access applications][cf-app], [tunnels through the API][cf-tun], [the package repository][cf-pkg]). **Nobody has run them
against a real account yet, so you are the first:** read each response before the next command, and the checks at the end are
the test. Run everything in this section **on your own computer**, except part E, which runs in the container.

The order matters here too: the Access application (A to C) exists **before** the public DNS record (F), so there is never a
moment when the name points at the archive with nothing in front of it.

**Once, in the dashboard: an API token.** My Profile, **API Tokens**, **Create Token**, **Create Custom Token**. Permissions
(the dashboard says Edit where the API reference says Write): Account / **Access: Apps and Policies** / Edit, Account /
**Access: Service Tokens** / Edit, Account / **Cloudflare Tunnel** / Edit, Zone / **DNS** / Edit. Limit the account to yours and
the zone to your domain. Copy the token once. It can change your Access settings, so delete it when you have finished (part H).
You also need the **Account ID** and the domain's **Zone ID**; both are on the domain's Overview page. Your Access login
method (what step 3 calls the identity provider) must already be set up under Zero Trust, **Settings**, **Authentication**.

```bash
sudo apt-get install -y jq curl                    # or your package manager; curl 7.82 or newer
read -rs CLOUDFLARE_API_TOKEN; export CLOUDFLARE_API_TOKEN
ACCOUNT_ID=<ACCOUNT_ID>
ZONE_ID=<ZONE_ID>
ARCHIVE_HOST=archive.<YOUR_DOMAIN>                 # one subdomain level; two levels need an Advanced Certificate
EMAIL_1=<your email>; EMAIL_2=<your partner's email>
API=https://api.cloudflare.com/client/v4
cf() { curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "$@"; }
```

**A. The deck's service token** (step 3's first half).

```bash
R=$(cf "$API/accounts/$ACCOUNT_ID/access/service_tokens" --json '{"name":"gatbox-deck","duration":"8760h"}')
echo "$R" | jq '{success, errors}'
TOKEN_ID=$(echo "$R" | jq -r .result.id)
echo "$R" | jq -r '.result | "Client ID:     \(.client_id)\nClient Secret: \(.client_secret)"'
```

The secret is shown once. Copy both lines into a password manager now, then `clear` the terminal. `8760h` is one year: put the
expiry date in your calendar (step 3 says why).

**B. The two policies** (step 3's table). Each prints a UUID; `null` means it failed, so read the error.

```bash
PEOPLE=$(cf "$API/accounts/$ACCOUNT_ID/access/policies" --json "$(jq -n --arg a "$EMAIL_1" --arg b "$EMAIL_2" \
  '{name:"gatbox-archive people",decision:"allow",session_duration:"24h",include:[{email:{email:$a}},{email:{email:$b}}]}')" \
  | tee /dev/stderr | jq -r .result.id)
DECKPOL=$(cf "$API/accounts/$ACCOUNT_ID/access/policies" --json "$(jq -n --arg t "$TOKEN_ID" \
  '{name:"gatbox-archive deck",decision:"non_identity",include:[{service_token:{token_id:$t}}]}')" \
  | tee /dev/stderr | jq -r .result.id)
echo "$PEOPLE $DECKPOL"
```

`non_identity` is the API's name for the dashboard's **Service Auth** action; Cloudflare's pages say a service-token rule only
works with that decision but never spell out the mapping, so part C ends by having you look.

**C. The application.**

```bash
cf "$API/accounts/$ACCOUNT_ID/access/apps" --json "$(jq -n --arg d "$ARCHIVE_HOST" --arg p "$PEOPLE" --arg k "$DECKPOL" \
  '{type:"self_hosted",name:"gatbox-archive",domain:$d,session_duration:"24h",policies:[{id:$p,precedence:1},{id:$k,precedence:2}]}')" \
  | jq '{success, errors}'
```

Now open **Zero Trust, Access controls, Applications, gatbox-archive** in the dashboard: two policies, `people` as **Allow** with
both emails and `deck` as **Service Auth** with the `gatbox-deck` token. If `deck` shows a different action, fix it there before
going on.

**D. The tunnel and what it serves** (step 4's first half).

```bash
R=$(cf "$API/accounts/$ACCOUNT_ID/cfd_tunnel" --json '{"name":"gatbox-archive","config_src":"cloudflare"}')
echo "$R" | jq '{success, errors}'
TUNNEL_ID=$(echo "$R" | jq -r .result.id)
TUNNEL_TOKEN=$(echo "$R" | jq -r .result.token)
cf "$API/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations" --request PUT --json "$(jq -n --arg h "$ARCHIVE_HOST" \
  '{config:{ingress:[{hostname:$h,service:"http://localhost:8080"},{service:"http_status:404"}]}}')" | jq '{success, errors}'
```

The service is `http://localhost:8080` and the last rule is the catch-all Cloudflare requires. The same rule as step 4 applies:
nothing but this tunnel reaches port 8080, and every hostname that points at it is behind the application from part C.

**E. `cloudflared`, in the container.** Print the tunnel token here with `echo "$TUNNEL_TOKEN"` and paste it below.

```bash
mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | tee /etc/apt/sources.list.d/cloudflared.list
apt-get update && apt-get install -y cloudflared
cloudflared service install <TUNNEL_TOKEN>
```

**F. The public name, last.**

```bash
cf "$API/zones/$ZONE_ID/dns_records" --json "$(jq -n --arg n "$ARCHIVE_HOST" --arg c "$TUNNEL_ID.cfargotunnel.com" \
  '{type:"CNAME",proxied:true,name:$n,content:$c}')" | jq '{success, errors}'
```

**G. Check it.** The tunnel should say `healthy`:

```bash
cf "$API/accounts/$ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID" | jq '.result.status'
```

Then the two `curl` checks and the browser check at the end of step 4, unchanged: no 200 without the service token, the
health answer with it, and an empty games list in the browser after you sign in.

**H. Tidy up.** `unset CLOUDFLARE_API_TOKEN TUNNEL_TOKEN`, and delete the API token in the dashboard (My Profile, **API Tokens**).
Steps 5 and 6 carry on from here with the Client ID and Secret from part A.

[cf-st]: https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/service_tokens/methods/create/
[cf-pol]: https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/policies/methods/create/
[cf-app]: https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/create/
[cf-tun]: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/
[cf-pkg]: https://pkg.cloudflare.com/

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
policy, like the deck's) or, if you'd rather keep that traffic on your network, on the container's private address. That
listener answers reader tokens only, read-only, and no pages. To turn it on, in the container:

```bash
ip -4 route show scope link
```

One line of that output looks like `192.0.2.0/24 dev eth0 proto kernel scope link src 192.0.2.50`. Its first field is the
network the apps' hosts sit on, and `src` is this container's own address. Put them in `/etc/gatbox-archive/archive.conf`:

```
ARCHIVE_PRIVATE_BIND=<CONTAINER_IP>:8081
ARCHIVE_PRIVATE_ALLOW=<NETWORK>/<PREFIX>
```

`ARCHIVE_PRIVATE_ALLOW` takes **one** network, not a list: `install.sh` strips the spaces from the line, so two addresses
would be glued into one that means nothing. If the two apps are on different networks, use one network that contains both.
Then apply it. The service reads `archive.conf` only when it starts, so `install.sh` restarts it whenever the file has
changed since the service was last started (it keeps a checksum of the file in `/var/lib/gatbox-archive/conf.sha256`).
`bash archive/install.sh --check` lists that restart before you do it. The first run after you update to this version
restarts the service once, because there is no checksum yet.

```bash
cd ~/Gatbox && git pull && bash archive/install.sh
journalctl -u gatbox-archive -n 3 --no-pager | grep -i listener
```

The last line must end `private listener <CONTAINER_IP>:8081`. If it doesn't, the `ARCHIVE_PRIVATE_BIND` line isn't being
read: look for a typo, a space around the `=`, or a `#` in front. If the script said the restart failed, the journal says
why.

Then, **from each app's host** (not from the container), try a reader token. `read -rsp` shows a prompt but not what you
paste, and `--max-time` stops it hanging:

```bash
read -rsp "reader token: " T; echo
curl -sS --max-time 10 -H "Authorization: Bearer $T" "http://<CONTAINER_IP>:8081/api/v1/library/changes?since=0&limit=1" | head -c 300; echo; unset T
```

JSON starting `{"records"` means it works. `Connection refused` means nothing listens there (`install.sh` didn't run after the edit, or
the bind address is wrong). A timeout means the packets are dropped: `ARCHIVE_PRIVATE_ALLOW` doesn't cover that host, or a firewall sits between
the two. A JSON error about the token means the route is fine and the token is wrong or revoked.

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

The first sync is a dry run (`GATBOX_SYNC_DRYRUN=1`): it changes nothing, and you read it before the real one. It says the
totals first (`dry run: push 118 rom_list, 2 rom_image; pull nothing; ...`), then names the first five records of each kind
and counts the rest, then how many files it would send and fetch. The files step can take a minute and says so.
`GATBOX_SYNC_DRYRUN=full` names every record. The real run always names every record it moves, in the journal.

## 7. The apps (cabcrawl and the tracker)

Each app has its own steps for this: the "game library" section of cabcrawl's `deploy/RUNBOOK.md`, and section 12 of the
tracker's. They set `LIBRARY_ARCHIVE_URL` (`http://<CONTAINER_IP>:8081` on the private listener, no path, no trailing slash)
and `LIBRARY_ARCHIVE_TOKEN` (that app's reader token from step 5) in the app's env file, deploy, and install a timer. Three
things the apps' pages don't make obvious:

**The env file is read as a shell script.** One `KEY=value` per line, no space around the `=`, and no line break inside the
token. cabcrawl's `deploy.sh` sources `.env`, so a line that isn't an assignment stops it in its preflight with
`<the line>: command not found`, before the backup, the pull or any change. To check the shape without printing a value:

```bash
awk '{ i = index($0, "="); if (i) print NR": "substr($0, 1, i-1); else print NR": (no = sign, " length($0) " characters)" }' <ENV_FILE>
```

Every line should show a key name. A line with no `=` is the broken one.

**A pull asks only for the games that its machines or assets name.** With none named, it logs
`library: changed=0 … ok=True` and makes no request at all, so that line proves only that the settings loaded. The
connection is proved by one keyed machine: link cabcrawl's machines from the deck's plan (its runbook has the script),
or set **Manuals library key (GATBOX)** on a tracker asset, then run the app's pull unit by hand and read the result:

```bash
systemctl start <PULL_UNIT>.service
journalctl -u <PULL_UNIT>.service --no-pager | grep 'library:' | tail -3
```

(`arcade-tracker-library` for cabcrawl, `shop-hub-library` for the tracker.) `changed=` should now be above 0.

**The key has to exist in the archive.** The deck sends the games of the barcade's roster, so those arrive on their own. A
game that isn't on that roster, such as a customer's machine in the shop, has no record until you add one: **Add** on the
archive's games list, with the same key you set on the machine or asset. To see whether the archive has a key, open
`https://<HOSTNAME>/games/<KEY>` while signed in. "There is no game with the key" means it doesn't. A game's key is shown
under its title, and in the address of its page.

To ask the feed itself, walk every page. The feed filters by game **after** it takes a page of changes, so a small `limit`
can return an empty page with `"more": true` for a game that exists. Run this where the app's env file is loaded
(`set -a; . <ENV_FILE>; set +a`); it prints kinds and ids only, never data or the token:

```bash
python3 - <<'EOF'
import json, os, urllib.request
base, tok, key = os.environ["LIBRARY_ARCHIVE_URL"], os.environ["LIBRARY_ARCHIVE_TOKEN"], "widget-wars"
since, found = 0, []
while True:
    req = urllib.request.Request(f"{base}/api/v1/library/changes?since={since}&limit=500&models={key}",
                                 headers={"Authorization": "Bearer " + tok})
    page = json.load(urllib.request.urlopen(req, timeout=15))
    found += [(r["kind"], r["id"]) for r in page["records"]]
    since = page["cursor"]
    if not page["more"]:
        break
print(len(found), "records:", found[:10])
EOF
```

Replace `widget-wars` with your key. `0 records` means the archive has nothing under it.

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
| Every call through Access gets a `302`, the service token shows no "Last seen" | The secret was cut short in the copy, the policy's action isn't Service Auth, or the policy isn't ticked on the application (step 4) |
| `/` answers `401` to someone who isn't signed in | Access doesn't cover that path; the pages would trust a forged header. Fix the application's destination now (step 4) |
| An app's pull says `changed=0 … ok=True` | Nothing is keyed in that app, or the key isn't in the archive; the pull made no request (step 7) |
| A `curl` to `:8081` prints nothing for a long time | You are at a hidden `read -rs` prompt: paste the token and press Enter. Use `read -rsp` and `--max-time 10` (step 5) |
| `curl` to `:8081` says `Connection refused` / times out | No listener (check the startup line in the journal, and that `install.sh` ran after the edit) / `ARCHIVE_PRIVATE_ALLOW` or a firewall drops the packets (step 5) |
| `deploy.sh` stops in the preflight with `<text>: command not found` | A line of the app's `.env` isn't `KEY=value`; a token landed on a line of its own (step 7) |
