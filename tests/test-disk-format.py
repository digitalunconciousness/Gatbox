#!/usr/bin/env python3
"""FORMAT (docs/plans/2026-11-16-restore-and-format.md): gatbox-disk's quick format, against the fake machine of tests/fakedisk.py
(a sysfs tree and plain files standing in for drives; no real block device is opened).

What is checked is what the owner relies on, because this is a disk write: the layout (MBR to 2 TiB, GPT above, FAT32 refused past
2 TB, a small card gets FAT16); the tables are ones other tools read; a format of each kind leaves one partition whose filesystem
passes its own fsck WITH the drive's old bytes still around it (quick: nothing else is touched), the old table and old signatures
gone; the old table is wiped before anything else and the new one written last; every refusal leaves the drive byte for byte as it
was (not blank without the acknowledgement, the model not typed, a stale check, a run-out arm, another drive, a drive whose contents
changed, a mkfs or an fsck that fails, no room in the store); a read-back that doesn't match stops it and says so; the check says
whether the store holds an image of what is on the drive; the log line; the job mode; and the scratch file never outlives the job.

Needs mkfs.fat, fsck.fat, mkfs.exfat, fsck.exfat, mkfs.ext4, e2fsck, partx and blkid; without them it skips, saying which.
    python3 tests/test-disk-format.py
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))
from fakedisk import FakeDisks, MiB, fat_card  # noqa: E402

TOOL = os.path.join(REPO, "tools", "gatbox-disk")
T = tempfile.mkdtemp(prefix="gatbox-format-test-")
F = FakeDisks(T)
passed = failed = 0
PATH = os.environ.get("PATH", "") + ":/usr/sbin:/sbin"


def check(name, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")


def disk(*args, stdin="", **extra):
    r = subprocess.run(["python3", TOOL, *args], capture_output=True, text=True, env=F.env(GATBOX_DISK_RESCAN="0.2", **extra), input=stdin, timeout=300)
    return r.returncode, r.stdout + r.stderr


def run(*argv):
    r = subprocess.run(argv, capture_output=True, text=True, env=dict(os.environ, PATH=PATH, LC_ALL="C"), timeout=300)
    return r.returncode, r.stdout + r.stderr


def sha(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(4 * MiB), b""):
            h.update(b)
    return h.hexdigest()


def junk(nbytes, head=b""):
    """A drive full of old bytes: a repeating pattern that is never zero, so anything a format left alone still shows."""
    block = bytes(range(1, 256)) * 4096
    body = (block * (nbytes // len(block) + 1))[:nbytes]
    return head + body[len(head):]


def part_of(dev, start, nbytes, out):
    with open(dev, "rb") as f, open(out, "wb") as o:
        f.seek(start)
        left = nbytes
        while left:
            b = f.read(min(4 * MiB, left))
            o.write(b)
            left -= len(b)
    return out


def log_lines():
    p = f"{F.store}/writes.jsonl"
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def main():
    gone = [t for t in ("mkfs.fat", "fsck.fat", "mkfs.exfat", "fsck.exfat", "mkfs.ext4", "e2fsck", "partx", "blkid") if not shutil.which(t, path=PATH)]
    if gone:
        print(f"  skip  the format tests need {', '.join(gone)} (apt: dosfstools, exfatprogs, e2fsprogs, util-linux)")
        return 0
    F.mount_table()
    os.environ.update(F.env(GATBOX_DISK_RESCAN="0.2"))
    loader = importlib.machinery.SourceFileLoader("gatbox_disk", TOOL)
    spec = importlib.util.spec_from_loader("gatbox_disk", loader)
    G = importlib.util.module_from_spec(spec)
    loader.exec_module(G)

    def stops(fn):
        try:
            fn()
        except G.Stop as s:
            return s.what + " | " + s.hint
        return None

    print("the layout:")
    lay = G.format_layout(62 * MiB, "fat32")
    check("a 62 MiB card: an MBR, one partition from 1 MiB that ends on a MiB with at least 1 MiB after it, FAT32",
          lay == {"table": "dos", "start": MiB, "bytes": 60 * MiB, "fs": "fat32", "made": "FAT32"})
    check("a 20 MiB card is too small for FAT32: it says FAT16", G.format_layout(20 * MiB, "fat32")["made"] == "FAT16")
    check("under 16 MiB, or a filesystem that isn't one of the three: refused", "too small" in (stops(lambda: G.format_layout(15 * MiB, "ext4")) or "")
          and "one of" in (stops(lambda: G.format_layout(64 * MiB, "ntfs")) or ""))
    two_tib = 1 << 41
    check("MBR while the drive's sectors fit 32 bits, GPT from 2 TiB up", G.format_layout(two_tib - 512, "exfat")["table"] == "dos"
          and G.format_layout(two_tib, "exfat")["table"] == "gpt" and G.format_layout(3 * two_tib, "ext4")["table"] == "gpt")
    check("FAT32 past 2 TB is refused, and it says to pick exFAT or ext4", "exFAT or ext4" in (stops(lambda: G.format_layout(3 * two_tib, "fat32")) or ""))

    print("the tables, read by another tool:")
    t1 = f"{T}/mbr.img"
    with open(t1, "wb") as f:
        f.write(G.mbr_sector(0x0C, 2048, 122880))
        f.truncate(62 * MiB)
    rc, out = run("partx", "-g", "-o", "NR,START,SECTORS", "--show", t1)
    check("the MBR: partx reads one partition at sector 2048 with the right length", rc == 0 and out.split() == ["1", "2048", "122880"])
    rc, out = run("blkid", "-p", "-o", "export", t1)
    check("... and blkid calls it a dos table", "PTTYPE=dos" in out)
    big = 3 * two_tib
    t2 = f"{T}/gpt.img"
    sectors = big // 512
    head, tail = G.gpt_pair(sectors, 2048, sectors - 2048 - 4096, G.GPT_BASIC, "WIDGET")
    with open(t2, "wb") as f:
        f.write(head)
        f.truncate(big)
        f.seek(big - len(tail))
        f.write(tail)
    rc, out = run("partx", "-g", "-o", "NR,START,SECTORS,NAME", "--show", t2)
    check("the GPT (a 6 TiB drive): partx reads the one partition, its length and its name (the header and entry checksums hold)",
          rc == 0 and out.split() == ["1", "2048", str(sectors - 2048 - 4096), "WIDGET"])
    rc, out = run("blkid", "-p", "-o", "export", t2)
    check("... and blkid calls it gpt", "PTTYPE=gpt" in out)
    with open(t2, "r+b") as f:                               # the primary header damaged: a reader falls back to the copy at the end
        f.seek(512)
        f.write(b"\0" * 512)
    rc, out = run("partx", "-g", "-o", "NR,START", "--show", t2)
    check("... with the first header wiped, the copy at the end of the drive is good enough to read the partition", rc == 0 and out.split() == ["1", "2048"])
    os.remove(t2)

    print("the FORMAT CHECK:")
    F.usb_disk("sda", 64 * MiB, "WIDGET", "WIDGETCARD 64", port=1, devnum="8:0")
    rc, out = disk("format-check", "-d", "sda", "--fs", "fat32", "--label", "widget", "--json")
    c = json.loads(out)
    check("a blank card: it passes, needs no acknowledgement, no warning about an image, the label in capitals, the layout in the answer",
          rc == 0 and c["target_ok"] and not c["needs_ack"] and not c["warn_no_image"] and c["label"] == "WIDGET" and c["layout"]["made"] == "FAT32"
          and c["contents"]["blank"])
    rc, out = disk("format-check", "-d", "sda", "--fs", "fat32", "--label", "has space", "--json")
    check("a label that isn't up to 11 of A-Z, 0-9, _ and -: refused", rc == 2 and "label" in json.loads(out)["error"])
    rc, out = disk("format-check", "-d", "sdz", "--fs", "fat32", "--json")
    check("a drive that isn't there: refused", rc == 2 and "no USB disk" in json.loads(out)["error"])
    F.usb_disk("sdc", 4000 * MiB, "WIDGET", "WIDGET STORE", port=3, devnum="8:32", parts=(1,))
    rc, out = disk("format-check", "-d", "sdc", "--fs", "ext4", "--json")
    check("the image store is never formatted", rc == 2 and "image store" in json.loads(out)["error"])
    F.usb_disk("sda", 64 * MiB, "WIDGET", "WIDGETCARD 64", port=1, devnum="8:0", data=junk(64 * MiB, fat_card(4096)))
    rc, out = disk("format-check", "-d", "sda", "--fs", "exfat", "--json")
    c = json.loads(out)
    check("a card with a filesystem and no image of it in the store: it passes, wants the acknowledgement, and WARNS that the store has no image",
          rc == 0 and c["needs_ack"] and c["warn_no_image"] and not c["has_image"] and c["stored"] == [] and c["contents"]["fs"] == "vfat")
    rc, out = disk("format-check", "-d", "sda", "--fs", "exfat")
    check("... and the terminal says so in capitals", "THE STORE HAS NO IMAGE OF WHAT IS ON THIS DRIVE NOW" in out)
    rc, out = disk("read", "-d", "sda", "-m", "unassigned", "-l", "WIDGETCARD", "--media", "cf", "--json")
    check("(the card is read into the store)", rc == 0 and json.loads(out)["state"] == "done")
    rc, out = disk("format-check", "-d", "sda", "--fs", "exfat", "--json")
    c = json.loads(out)
    check("with an image of it in the store: no warning; the image is named, read from this drive (by make, model and size: it has no serial), "
          "and the drive still starts with its bytes", rc == 0 and c["has_image"] and not c["warn_no_image"] and c["needs_ack"]
          and len(c["stored"]) == 1 and c["stored"][0]["label"] == "WIDGETCARD" and c["stored"][0]["from_this_drive"]
          and c["stored"][0]["by"] == "make, model and size" and c["stored"][0]["head_same"] is True)
    with open(f"{F.dev}/sda", "r+b") as f:                   # the card was used after it was read
        f.seek(8 * MiB)
        f.write(b"new photos since the read" * 100)
    rc, out = disk("format-check", "-d", "sda", "--fs", "exfat", "--json")
    c = json.loads(out)
    check("the same card, changed since that read: the image is still named, it says the drive no longer starts with it, and the warning is back",
          rc == 0 and c["warn_no_image"] and not c["has_image"] and c["stored"][0]["from_this_drive"] and c["stored"][0]["head_same"] is False)
    F.usb_disk("sdb", 64 * MiB, "XinTop", "XT-U33502", port=2, devnum="8:16", data=junk(64 * MiB), ata=("WIDGET HD 64", "WD-WIDGET0009"))
    rc, out = disk("format-check", "-d", "sdb", "--fs", "ext4", "--json")
    c = json.loads(out)
    check("another drive, with a serial, full of something the store has never seen: the card's image is not taken for it, and it warns",
          rc == 0 and c["stored"] == [] and c["warn_no_image"])

    rc, out = disk("read", "-d", "sdb", "-m", "unassigned", "-l", "WIDGETHD", "--media", "hdd", "--json")
    F.usb_disk("sdb", 64 * MiB, "XinTop", "XT-U33502", port=2, devnum="8:16", data=junk(64 * MiB, b"another drive entirely" * 100),
               ata=("WIDGET HD 64", "WD-WIDGET0010"))
    rc2, out = disk("format-check", "-d", "sdb", "--fs", "ext4", "--json")
    c = json.loads(out)
    check("a drive of the same model with ANOTHER serial than a stored image's source, and other contents: that image is not listed for it",
          rc == 0 and rc2 == 0 and c["stored"] == [] and c["warn_no_image"])
    F.usb_disk("sdb", 64 * MiB, "XinTop", "XT-U33502", port=2, devnum="8:16", data=junk(64 * MiB, b"changed since" * 100),
               ata=("WIDGET HD 64", "WD-WIDGET0009"))
    c = json.loads(disk("format-check", "-d", "sdb", "--fs", "ext4", "--json")[1])
    check("... the drive with THAT serial is recognised as the image's source (by serial), whatever is on it now",
          len(c["stored"]) == 1 and c["stored"][0]["by"] == "serial" and c["stored"][0]["head_same"] is False and c["warn_no_image"])

    print("a format of each kind (the model typed, as at a terminal):")
    for fs, made, family, ptype in (("fat32", "FAT32", "vfat", "c"), ("exfat", "exFAT", "exfat", "7"), ("ext4", "ext4", "ext4", "83")):
        old = junk(64 * MiB, fat_card(4096))
        F.usb_disk("sda", 64 * MiB, "WIDGET", "WIDGETCARD 64", port=1, devnum="8:0", data=old)
        with open(f"{F.dev}/sda", "r+b") as f:                 # an older ext filesystem's signature where the new partition will start
            f.seek(MiB + 1024 + 56)
            f.write(b"\x53\xef")
        n_log = len(log_lines())
        rc, out = disk("format", "-d", "sda", "--fs", fs, "--label", "WIDGET", "--overwrite", stdin="WIDGETCARD 64\n")
        check(f"{fs}: FORMATTED, and it says the Pi still lists the old partitions (the fake kernel never re-reads)",
              rc == 0 and "FORMATTED: sda is " + made in out and "switch the dock off and on" in out)
        dev = f"{F.dev}/sda"
        rc, out = run("partx", "-g", "-o", "NR,START,SECTORS,TYPE", "--show", dev)
        check(f"... partx: one partition at sector 2048, 62 MiB long, type 0x{ptype}", rc == 0 and out.split() == ["1", "2048", str(62 * 2048), "0x" + ptype])
        part = part_of(dev, MiB, 62 * MiB, f"{T}/part.img")
        fsck = {"fat32": ("fsck.fat", "-n"), "exfat": ("fsck.exfat", "-n"), "ext4": ("e2fsck", "-fn")}[fs]
        rc, out = run(*fsck, part)
        check(f"... the partition, cut out of the drive with the old bytes still around it, passes {fsck[0]}", rc == 0)
        rc, out = run("blkid", "-p", "-o", "export", part)
        check(f"... blkid: {family}, labelled WIDGET (and no trace of the older ext signature)", f"TYPE={family}" in out and "LABEL=WIDGET" in out
              and out.count("TYPE=") == 1)
        now = open(dev, "rb").read()
        check("... quick: the drive's first MiB is the table and zeros, its last MiB zeros, and the old bytes in the middle are untouched",
              now[512:MiB] == bytes(MiB - 512) and now[-MiB:] == bytes(MiB) and now[40 * MiB:41 * MiB] == old[40 * MiB:41 * MiB]
              and now[446 + 16:510] == bytes(48))
        check("... the partition's own first and last MiB hold nothing of the old drive (an older filesystem's signature at either end is gone)",
              now[62 * MiB:63 * MiB].count(0) > MiB * 0.99 and old[MiB + 4096:MiB + 8192] not in now[MiB:2 * MiB])
        if fs == "fat32":
            check("... the FAT boot sector says where the partition starts (hidden sectors 2048), as a partitioned drive's does",
                  int.from_bytes(now[MiB + 28:MiB + 32], "little") == 2048)
        line = log_lines()[-1]
        check("... one line in writes.jsonl: op format, verified, the filesystem, the table, the label, what was on it, and that it was written",
              len(log_lines()) == n_log + 1 and line["op"] == "format" and line["state"] == "verified" and line["fs"] == made and line["table"] == "dos"
              and line["label"] == "WIDGET" and line["contents"]["fs"] == "vfat" and line["written"] is True and line["armed"] == {"by": "terminal", "typed": True})
        check("... the scratch file is gone from the store", os.listdir(f"{F.store}/_format") == [])
    with open(part, "rb") as f:
        data = f.read()
    has_journal = int.from_bytes(data[1024 + 0x5C:1024 + 0x60], "little") & 4
    journal = int.from_bytes(data[1024 + 0x10C + 64:1024 + 0x10C + 68], "little")
    import re as _re
    zeros = max((len(m.group(0)) for m in _re.finditer(rb"\0{65536,}", data)), default=0)
    check("ext4: it has a journal, and the journal is zeros ON THE DRIVE, where the old bytes were (mke2fs leaves its zeroing as holes in a file: "
          "a copy of the data alone would have left the old bytes there)", bool(has_journal) and journal >= MiB and zeros >= journal * 0.9)
    print("small, and large:")
    F.usb_disk("sda", 20 * MiB, "WIDGET", "WIDGETCARD 20", port=1, devnum="8:0", data=junk(20 * MiB))
    rc, out = disk("format", "-d", "sda", "--fs", "fat32", "--overwrite", "--json", stdin="WIDGETCARD 20\n")
    info = json.loads(out[out.index("{"):])
    rc2, px = run("partx", "-g", "-o", "NR,START,TYPE", "--show", f"{F.dev}/sda")
    check("a 20 MiB card asked for FAT32 gets FAT16 (and the type a small FAT16 has, 0x4), with no label when none is given; fsck.fat passes",
          rc == 0 and info["made"] == "FAT16" and px.split() == ["1", "2048", "0x4"]
          and run("fsck.fat", "-n", part_of(f"{F.dev}/sda", MiB, 18 * MiB, f"{T}/part.img"))[0] == 0)
    huge = two_tib + 64 * MiB
    F.usb_disk("sdb", huge, "WIDGET", "WIDGET 2T", port=2, devnum="8:16")
    rc, out = disk("format", "-d", "sdb", "--fs", "fat32", "--json", stdin="WIDGET 2T\n")
    check("a drive over 2 TiB: FAT32 is refused", rc == 2 and "FAT32 stops at 2 TB" in out)
    rc, out = disk("format", "-d", "sdb", "--fs", "exfat", "--label", "BIG", "--json", stdin="WIDGET 2T\n")
    info = json.loads(out[out.index("{"):]) if rc == 0 else {}
    rc2, px = run("partx", "-g", "-o", "NR,START,SECTORS,NAME", "--show", f"{F.dev}/sdb")
    check("... exFAT on it: a GPT, read by partx, the partition named after the label, and only a sliver of the drive written",
          rc == 0 and info.get("made") == "exFAT" and info["layout"]["table"] == "gpt" and px.split() == ["1", "2048", str(info["layout"]["bytes"] // 512), "BIG"]
          and info["bytes"] < 200 * MiB and log_lines()[-1]["table"] == "gpt")
    with open(f"{F.dev}/sdb", "rb") as f:
        f.seek(MiB)
        boot = f.read(16)
    check("... and the exFAT boot sector is where the partition starts", boot[3:11] == b"EXFAT   ")
    F.unplug("sdb")

    print("every refusal leaves the drive as it was:")

    def fresh(data=None):
        d = data if data is not None else junk(64 * MiB, fat_card(4096))
        F.usb_disk("sda", 64 * MiB, "WIDGET", "WIDGETCARD 64", port=1, devnum="8:0", data=d)
        return sha(f"{F.dev}/sda")
    before = fresh()
    rc, out = disk("format", "-d", "sda", "--fs", "fat32", "--json", stdin="WIDGETCARD 64\n")
    check("not blank and no --overwrite: stopped, it says what is on the drive, and nothing is changed",
          rc == 2 and "isn't blank" in out and sha(f"{F.dev}/sda") == before and log_lines()[-1]["state"] == "stopped" and log_lines()[-1]["written"] is False)
    rc, out = disk("format", "-d", "sda", "--fs", "fat32", "--overwrite", "--json", stdin="WIDGETCARD 46\n")
    check("the model typed wrong: nothing is written", rc == 2 and "wasn't typed" in out and sha(f"{F.dev}/sda") == before)
    rc, out = disk("format", "-d", "sda", "--fs", "fat32", "--overwrite", "--json", stdin="")
    check("nothing typed (the end of a script's input): nothing is written", rc == 2 and sha(f"{F.dev}/sda") == before)

    def go(c, **kw):
        w = G.Format(c, say=lambda *a: None, **kw)
        try:
            w.go()
        except G.Stop as s:
            return s.what + " | " + s.hint, w
        return None, w
    arm = lambda: {"by": "test", "at": time.time(), "until": time.time() + 30}
    c = G.format_check("sda", "fat32", "WIDGET", say=lambda *a: None)
    why, _ = go(dict(c, at=time.time() - 301), ack=True, armed=arm())
    check("a check more than 5 minutes old: refused", "5 minutes old" in (why or "") and sha(f"{F.dev}/sda") == before)
    why, _ = go(c, ack=True, armed={"by": "test", "at": time.time() - 31, "until": time.time() - 1})
    check("an arm that has run out: refused", "arm ran out" in (why or "") and sha(f"{F.dev}/sda") == before)
    why, _ = go(c, ack=True, armed={"by": "test"})
    check("an arm with no expiry, and no arm with no typed model: refused", "malformed" in (why or "") and "wasn't typed" in (go(c, ack=True)[0] or ""))
    why, _ = go(dict(c, op="check"), ack=True, armed=arm())
    check("a TARGET CHECK is not a FORMAT CHECK: refused", "no passing FORMAT CHECK" in (why or ""))
    why, _ = go(dict(c, layout=dict(c["layout"], bytes=c["layout"]["bytes"] + MiB)), ack=True, armed=arm())
    check("a check whose layout isn't this drive's: refused", "doesn't fit this drive" in (why or "") and sha(f"{F.dev}/sda") == before)
    F.usb_disk("sda", 64 * MiB, "WIDGET", "WIDGETCARD 64B", port=1, devnum="8:0", data=junk(64 * MiB, fat_card(4096)))
    why, _ = go(c, ack=True, armed=arm())
    check("another drive on the dock under the same name: refused", "different drive from the one checked" in (why or "") and sha(f"{F.dev}/sda") == before)
    fresh()
    with open(f"{F.dev}/sda", "r+b") as f:
        f.seek(2 * MiB)
        f.write(b"another card of the same make")
    changed = sha(f"{F.dev}/sda")
    why, _ = go(c, ack=True, armed=arm())
    check("the same make and size but other contents than the check saw (another card): refused", "changed since the check" in (why or "") and sha(f"{F.dev}/sda") == changed)
    before = fresh()
    c = G.format_check("sda", "fat32", "WIDGET", say=lambda *a: None)
    stub = f"{T}/bin"
    os.makedirs(stub)
    real_env = dict(G.MKFS_ENV)
    for tool, what in (("mkfs.fat", "mkfs.fat failed"), ("fsck.fat", "did not pass its own check")):
        open(f"{stub}/{tool}", "w").write("#!/bin/sh\necho 'it broke' >&2\nexit 1\n")
        os.chmod(f"{stub}/{tool}", 0o755)
        G.MKFS_ENV = dict(real_env, PATH=stub + ":" + real_env["PATH"])
        why, _ = go(c, ack=True, armed=arm())
        check(f"{tool} fails: stopped before the drive is touched, it says nothing was written, the scratch file is removed",
              what in (why or "") and "nothing was written to the drive" in why and sha(f"{F.dev}/sda") == before and os.listdir(f"{F.store}/_format") == []
              and log_lines()[-1]["written"] is False)
        os.remove(f"{stub}/{tool}")
    G.MKFS_ENV = dict(real_env, PATH=stub)
    why, _ = go(c, ack=True, armed=arm())
    check("mkfs.fat not installed: it says which tool and that the bootstrap installs it", "mkfs.fat isn't installed" in (why or "") and sha(f"{F.dev}/sda") == before)
    G.MKFS_ENV = real_env
    real_usage = shutil.disk_usage
    G.shutil.disk_usage = lambda p: type("U", (), {"free": 100 * MiB})()
    why, _ = go(c, ack=True, armed=arm())
    G.shutil.disk_usage = real_usage
    check("no room in the store for the scratch file: refused", "2 GB free" in (why or "") and sha(f"{F.dev}/sda") == before)
    open(f"{F.store}/_format/999.fs", "w").write("left by a killed job")
    F.mount_table(store=False)
    why, _ = go(c, ack=True, armed=arm())
    check("the store unplugged: refused", "IMAGE STORE NOT CONNECTED" in (why or "") and sha(f"{F.dev}/sda") == before)
    F.mount_table()

    print("the order of the writes, and a format that stops partway:")
    real_pwrite, offs = os.pwrite, []

    def watch(fd, data, off):
        offs.append((off, len(data)))
        return real_pwrite(fd, data, off)
    G.os.pwrite = watch
    why, w = go(c, ack=True, armed=arm())
    G.os.pwrite = real_pwrite
    check("a format from the arm (no model typed): verified, and the stale scratch file a killed job left is cleared away",
          why is None and w.info["verified"] and os.listdir(f"{F.store}/_format") == [] and w.info["kernel"] == {"partitions": [], "current": False})
    check("the first write zeroes the drive's first MiB (the old table goes first), and the last write is the first MiB again (the new table last)",
          offs[0] == (0, MiB) and offs[-1] == (0, MiB) and all(o >= MiB for o, _ in offs[1:-1]))
    spans = sorted(offs[1:])
    check("nothing is written twice (the pieces don't overlap), and far less than the drive is written",
          all(a + n <= b for (a, n), (b, _) in zip(spans, spans[1:])) and sum(n for _, n in offs[1:]) < 8 * MiB)
    before = fresh()
    c = G.format_check("sda", "ext4", "WIDGET", say=lambda *a: None)
    n = [0]

    def dies(fd, data, off):
        n[0] += 1
        if n[0] == 3:
            raise OSError(5, "Input/output error")
        return real_pwrite(fd, data, off)
    G.os.pwrite = dies
    try:
        go(c, ack=True, armed=arm())
        crashed = False
    except OSError:
        crashed = True
    G.os.pwrite = real_pwrite
    left = G.contents(f"{F.dev}/sda")
    check("the drive failing partway: the drive is left with NO partition table (not the old one pointing into half a filesystem), the log says "
          "stopped and written, and the scratch file is removed", crashed and left["table"] is None and left["fs"] is None
          and log_lines()[-1]["state"] == "stopped" and log_lines()[-1]["written"] is True and os.listdir(f"{F.store}/_format") == [])
    fresh()
    c = G.format_check("sda", "fat32", "", say=lambda *a: None)
    real_back = G.Format._read_back
    G.Format._read_back = staticmethod(lambda node, pieces: "0" * 40)
    why, _ = go(c, ack=True, armed=arm())
    G.Format._read_back = staticmethod(real_back)
    check("a read-back that doesn't match what was written: stopped, and it says the drive didn't take the format",
          "read-back 1 doesn't match" in (why or "") and "didn't take the format" in why and log_lines()[-1]["state"] == "stopped")
    fresh()
    c = G.format_check("sda", "fat32", "", say=lambda *a: None)
    real_contents, calls = G.contents, [0]

    def lying(node, logical=512):
        calls[0] += 1
        r = real_contents(node, logical)
        if calls[0] >= 2:
            r["partitions"] = []
        return r
    G.contents = lying
    why, _ = go(c, ack=True, armed=arm())
    G.contents = real_contents
    check("the drive read again not showing the partition that was written: stopped, never called verified",
          "doesn't show the partition and filesystem" in (why or "") and log_lines()[-1]["state"] == "stopped")

    print("the kernel's partition list, and the job mode:")
    fresh()
    blk = F.blocks["sda"]
    os.makedirs(f"{blk}/sda1")
    open(f"{blk}/sda1/partition", "w").write("1\n")
    open(f"{blk}/sda1/dev", "w").write("8:1\n")
    c = G.format_check("sda", "exfat", "", say=lambda *a: None)
    why, w = go(c, ack=True, armed=arm())
    check("when the kernel's list shows the one new partition, the result says it is current", why is None and w.info["kernel"] == {"partitions": ["sda1"], "current": True})
    fresh(junk(64 * MiB, b"a card the store has never seen" * 40))
    spool = f"{T}/spool"
    os.makedirs(spool)
    ident = next(d for d in json.loads(disk("list", "--json")[1])["disks"] if d["name"] == "sda")["identity"]

    def job(req):
        with open(f"{spool}/request.json", "w") as f:
            json.dump(dict(req, id="j" + str(time.time()), at=time.time()), f)
        rc, out = disk("--job", f"{spool}/request.json", "--status", f"{spool}/status.json")
        return rc, json.load(open(f"{spool}/status.json"))
    rc, st = job({"op": "format_check", "device": "sda", "identity": ident, "fs": "ext4", "label": "WIDGET"})
    check("the job: format_check ends done with the check in the status file, and the claim removed",
          rc == 0 and st["state"] == "done" and st["op"] == "format_check" and st["target_ok"] and st["warn_no_image"] and not os.path.exists(f"{spool}/running.json"))
    rc, st2 = job({"op": "format", "check": st, "ack": True, "armed": {"by": "screen", "at": time.time(), "until": time.time() + 30}})
    check("the job: format from that check and an arm ends done and verified, with what was made and the kernel's list in the status file",
          rc == 0 and st2["state"] == "done" and st2["verified"] and st2["made"] == "ext4" and st2["kernel"]["current"] is False
          and [s["step"] for s in st2["steps"]] == ["make", "write", "verify", "verify", "verified"])
    rc, st3 = job({"op": "format", "check": st, "armed": {"by": "screen", "at": time.time(), "until": time.time() + 30}})
    check("the job: the same check again without the acknowledgement (the drive now has a filesystem): stopped", rc == 0 and st3["state"] == "stopped")
    rc, st4 = job({"op": "format_check", "device": "sda", "identity": dict(ident, model="SOMETHING ELSE"), "fs": "ext4"})
    check("the job: a check for a drive that isn't the one picked: stopped", st4["state"] == "stopped" and "different drive" in st4["error"])

    print(f"\nformat: {passed} passed, {failed} failed")
    shutil.rmtree(T, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
