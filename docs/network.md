# Getting GATBOX onto a network

GATBOX is portable. It lives at home, goes to the arcade, and sometimes sits inside a cabinet
where nobody can reach a keyboard. This is how it joins a network, what happens when it
cannot, and what to do when a join goes wrong.

## What it does on its own

`gatbox-ap-fallback.timer` runs every two minutes, and the first time about 90 seconds after
boot.

- **On a network** — nothing to do.
- **No network, no hotspot** — it raises the **GATBOX** hotspot. Join it from a phone and
  reach the box at `gatbox.local` (the hotspot's gateway address is in `BRINGUP.md`; it is
  not repeated here because the site pattern list flags that subnet).
- **On the hotspot, and a saved network comes back into range** — it drops the hotspot so
  NetworkManager rejoins the network.
- **…and that rejoin keeps failing** — it waits before trying again: ten minutes, then half
  an hour, then an hour. A password that changed at the router is the ordinary cause, and
  without the wait the box drops its hotspot every two minutes for ever, cutting anyone
  connected to GATBOX each time. The count is per network and lives in
  `/var/lib/gatbox-ap-fallback/attempts`; getting onto any network clears it, so fixing the
  password does not need a reboot. `journalctl -u gatbox-ap-fallback` says how long it is
  holding for and why.

That last pair is why there is a timer at all. This used to run once at boot and the
instruction was "reboot near known Wi-Fi to go back", which is not something you can do to a
Pi sitting in a cabinet across town.

The hotspot only exists if it was armed: `sudo GATBOX_AP_PSK='…' bash
bootstrap/gatbox-bootstrap.sh`. `GATBOX_AP_PSK=off` removes it.

## Three ways to join a network nobody coded in

### 1. Scan a Wi-Fi QR — easiest at the bench

Both Android and iOS can show a QR for a network you are already on: **share this network**.
Scan it with GATBOX's scanner and it joins.

The scanner is loopback-only, so this needs someone standing at the box — which is the
authorization. The code is the password, so it is never logged, never shown on the dashboard
and never echoed back: the journal and the recent-scans list both say `WIFI:<redacted>`.

**Off a phone screen this often does not work** (2026-10-06, tried on the bench). The EY-H2
reads the printed cabinet labels every time and would not read a Wi-Fi QR held up on a phone.
It is a density problem, not a fault: a label carries a short URL, while a Wi-Fi code carries
an SSID *and* a passphrase, so the same area holds far more modules — each one a pixel or two
on a phone, behind glass that reflects the scanner's own light back at it.

Worth a try before giving up: screen brightness to maximum with auto-brightness off, the QR
pinch-zoomed as large as it will go, held 10–15 cm away and tilted a few degrees so the
reflection misses the lens.

To tell a scanner problem from a screen problem in one go: `journalctl -u gatbox-scand -f`,
then scan a printed cabinet label. A line like `scan '<base>/g/<slug>': machine` means the
scanner, the daemon and the web app are all fine and it is the screen. No line at all means
nothing was decoded — `gatbox-scand` logs every code it reads, and a half-finished read
flushes and logs within half a second, so silence means no keystrokes arrived.

If you want the scan to work, print the QR. Keep that piece of paper *with* the box rather
than on it: a printed Wi-Fi QR is the network's password in plain sight, which is the same
trade the coin-door labels make.

### 2. The dashboard, on the 7″ screen

SYSTEM → the **Wi-Fi** card. SCAN looks for what is in range, then type the key and JOIN.
FORGET removes a saved network.

While the helper is working the card says so, and it says which request it is answering, so
an outcome from a minute ago is never shown as the result of the button you just pressed.
There is one request slot: press JOIN while one is in flight and the second is refused out
loud rather than replacing the first.

If the card says a request was **left unanswered**, nothing took it — the job runs because
`gatbox-wifi.path` saw the file appear, so check `systemctl is-enabled gatbox-wifi.path`.

**Only on the Pi's own screen.** Not because the dashboard is precious about it, but because
asking for a join over the hotspot would cut the connection making the request, and you would
never learn whether it worked.

### 3. The desktop — the one that always works

GATBOX runs a desktop. Exit the kiosk, use NetworkManager's applet as on any other machine,
and go back. No part of this phase can break it, which is exactly why it is worth knowing:
if anything above misbehaves, this is the way in.

## When a join fails

It costs about thirty seconds, not a trip. A failed join brings the hotspot back by itself, so
the box stays reachable — join **GATBOX** again and try another key.

The dashboard's Wi-Fi card shows what the helper last did. `journalctl -u gatbox-wifi` has
nmcli's own reason for a failed join, and `sudo cat /var/spool/gatbox-wifi/status.json` has the
last outcome in full. A key never appears in any of them.

**If a network in the list will not join**, and the attempt fails in about a second rather than
taking thirty: nmcli works out the security type from the access point in *its* scan cache, and
with the AP missing from it there is nothing to infer from, so it answers
`802-11-wireless-security.key-mgmt: property is missing` without trying to associate. The card
goes on offering the network because it reads its own older scan list. Since 2026-10-06 a join
rescans first and prefers a profile the box already has, which knows its own security type —
and, for the home network here, the static address set by hand. Press SCAN if a network is
missing from the list; it is the same refresh.

## What a key is, and is not, protected from

A Wi-Fi key is never logged, never written to the status file and never returned by any
endpoint. It exists in two places: the request file in `/var/spool/gatbox-wifi` (mode 600,
removed before the helper does anything else), and nmcli.

**One known gap.** nmcli receives the key as a command-line argument, so for the few seconds a
join takes it is visible in `/proc/<pid>/cmdline` — which is to say, to `ps`, to anyone with a
login on the Pi. That is the bench box's owner, so it is recorded here rather than treated as
urgent. Closing it means persisting the key through NetworkManager some other way (a keyfile
this code writes itself, or `nmcli connection edit` fed on stdin), which rewrites the one path
that has to work and can only honestly be tested against the real nmcli on the Pi.

## If the box seems unreachable

1. Is the hotspot armed at all? `systemctl is-enabled gatbox-ap-fallback.timer`.
2. `journalctl -u gatbox-ap-fallback -n 20` says what it decided and why — including
   whether it is deliberately holding the hotspot up because a saved network keeps refusing
   it. To clear that immediately: `sudo rm /var/lib/gatbox-ap-fallback/attempts`.
3. Plug in a keyboard and a monitor and use the desktop. Nothing here is a trap you cannot
   get out of at the box itself.

## Reaching the hub

`/etc/gatbox/hub.conf`, mode 600, root-owned. `gatbox-sync` reads it through `LoadCredential`,
so the file itself is never readable by the job's own user.

```
HUB_URLS=https://tracker.example
HUB_TOKEN=gbx_<public_id>.<secret>
```

`HUB_URLS` is a list, tried in order, and the first to answer `/api/v1/health` wins. An
unreachable entry costs one failed connect on a two-second timeout, so listing several is
safe — the choice is made per run and deliberately not remembered, because a box that travels
is somewhere different each time.

**A LAN address is not listed, on purpose** (2026-10-05). The hub binds to loopback inside its
own container and is reachable only through its tunnel, which is a posture worth keeping. Add
a LAN entry only if that changes.

Mint a token on the hub with `scripts/create_device.py --name gatbox-01`; it is shown once.
