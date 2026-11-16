"""Invented disk images for the drive browser's tests (tests/test-browse.py and the dashboard test): an ext4 volume and a
swap partition behind a DOS table, a FAT32 volume with a label, and the store's sidecar beside each. Everything in them is
made up here (Widget names, random bytes, noise photos); no test uses a real drive's content.

Needs the sleuthkit tools, mke2fs, mkfs.fat and mtools; `missing()` names what is not installed so a test can skip.
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import time

MiB = 1 << 20
TOOLS = ("fls", "mmls", "fsstat", "icat", "mke2fs", "mkfs.fat", "mcopy")
DELETE_TOOLS = ("mdel", "mdeltree", "debugfs")


def missing():
    return [t for t in TOOLS if not shutil.which(t) and not os.path.exists("/usr/sbin/" + t)]


def mbr(path, parts):
    """Write a DOS partition table into the first sector: [(start sector, sectors, type byte, bootable)]."""
    with open(path, "r+b") as f:
        sec = bytearray(512)
        for i, (start, size, typ, boot) in enumerate(parts):
            sec[446 + 16 * i:446 + 16 * (i + 1)] = struct.pack("<B3sB3sII", 0x80 if boot else 0, b"\0\0\0", typ, b"\0\0\0", start, size)
        sec[510:512] = b"\x55\xaa"
        f.seek(0)
        f.write(sec)


def sample_tree(root, many=0):
    """The invented content. Returns the names the tests look for. `many` adds that many small files to one folder, to
    page through."""
    for d in ("Photos/2019", "Docs", "empty", "odd|name dir", "Notes"):
        os.makedirs(f"{root}/{d}")
    from PIL import Image
    Image.effect_noise((1600, 1200), 60).convert("RGB").save(f"{root}/Photos/2019/IMG_0001.jpg", "JPEG", quality=85)
    Image.new("RGB", (300, 200), (200, 30, 30)).save(f"{root}/Photos/2019/red.png")
    pages = [Image.new("RGB", (600, 800), c) for c in ((250, 250, 250), (230, 240, 255), (255, 240, 230))]
    pages[0].save(f"{root}/Docs/manual.pdf", "PDF", save_all=True, append_images=pages[1:])
    open(f"{root}/Docs/notes.txt", "w").write("Widget Wars cabinet notes\nline two\n")
    open(f"{root}/Notes/latin1.txt", "wb").write("caf\xe9 au lait\n".encode("latin-1"))
    open(f"{root}/Notes/long.txt", "w").write("x" * 70000)
    open(f"{root}/Docs/budget.xlsx", "wb").write(os.urandom(5000))
    open(f"{root}/big.bin", "wb").write(os.urandom(2 * MiB))
    open(f"{root}/broken.jpg", "wb").write(b"\xff\xd8\xff\xe0" + os.urandom(3000))
    with open(f"{root}/huge.pdf", "wb") as f:                      # over the preview limit, sparse
        f.write(b"%PDF-1.4\n")
        f.truncate(70 * MiB)
    Image.new("1", (30000, 30000)).save(f"{root}/bomb.png")        # 900 megapixels in a few KB
    open(f"{root}/odd|name dir/pipe|file.TXT", "w").write("x\n")
    os.symlink("Docs/notes.txt", f"{root}/link.txt")
    for i in range(many):
        open(f"{root}/Notes/n{i:04d}.txt", "w").write(f"{i}\n")


def ext4_image(path, tree, mib=160):
    """A DOS table with a bootable Linux partition holding `tree` (ext4) and a swap partition after it."""
    with open(path, "wb") as f:
        f.truncate(mib * MiB)
    mbr(path, [(2048, 200000, 0x83, True), (202048, 40000, 0x82, False)])
    subprocess.run(["mke2fs", "-q", "-F", "-t", "ext4", "-d", tree, "-E", f"offset={2048 * 512}", path, "100000k"], check=True)
    return {"ext": 2048, "swap": 202048}


def fat_image(path, tree_dirs, label="WIDGETFAT", mib=96):
    """A DOS table with one FAT32 partition, volume label `label`, holding the given folders of the sample tree."""
    with open(path, "wb") as f:
        f.truncate(mib * MiB)
    subprocess.run(["mkfs.fat", "-F", "32", "--offset", "2048", "-n", label, path, "90000"], check=True, capture_output=True)
    subprocess.run(["mcopy", "-s", "-i", f"{path}@@{2048 * 512}", *tree_dirs, "::/"], check=True,
                   env=dict(os.environ, MTOOLS_SKIP_CHECK="1"))
    mbr(path, [(2048, 180000, 0x0C, True)])
    return {"fat": 2048}


def fat_delete(path, start_sector, files=(), trees=()):
    """Delete from a FAT32 volume the way the drive's own system does: the directory entry is marked free and the data stays until
    something is written over it. `files` and `trees` are paths inside the volume."""
    img, env = f"{path}@@{start_sector * 512}", dict(os.environ, MTOOLS_SKIP_CHECK="1")
    for f in files:
        subprocess.run(["mdel", "-i", img, f"::/{f}"], check=True, env=env)
    for t in trees:
        subprocess.run(["mdeltree", "-i", img, f"::/{t}"], check=True, env=env)


def fat_overwrite_deleted(path, start_sector, inode, nbytes=4096):
    """Write random bytes over the first `nbytes` of a deleted file's data, in place, as a later write would. Returns them."""
    out = subprocess.run(["istat", "-o", str(start_sector), path, str(inode)], capture_output=True, text=True, check=True).stdout
    first = int(out.split("Sectors:")[1].split()[0])
    data = os.urandom(nbytes)
    with open(path, "r+b") as f:
        f.seek((start_sector + first) * 512)                       # istat's sectors count from the start of the volume
        f.write(data)
    return data


def fat_add(path, start_sector, local_file, dest):
    """Write one more file into a FAT32 volume (it takes the first free clusters, which may be a deleted file's)."""
    subprocess.run(["mcopy", "-i", f"{path}@@{start_sector * 512}", local_file, f"::/{dest}"], check=True,
                   env=dict(os.environ, MTOOLS_SKIP_CHECK="1"))


def ext4_whole(path, tree, mib=64):
    """An ext4 volume with no partition table (the whole image is the volume), holding `tree`."""
    with open(path, "wb") as f:
        f.truncate(mib * MiB)
    subprocess.run(["mke2fs", "-q", "-F", "-t", "ext4", "-d", tree, path], check=True)


def ext4_delete(path, name, clear=False, offset_sectors=0):
    """debugfs `rm`: the inode is marked free and its extents are kept, so the content can still be read. With `clear`, the inode is
    zeroed too, which is what a real ext4 does when it deletes: then nothing can be read back. `offset_sectors` is where the volume
    starts inside the image. Returns the inode number."""
    ino = None
    img = f"{path}?offset={offset_sectors * 512}" if offset_sectors else path
    out = subprocess.run(["debugfs", "-R", f"stat \"{name}\"", img], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("Inode:"):
            ino = int(line.split()[1])
    subprocess.run(["debugfs", "-w", "-R", f"rm \"{name}\"", img], check=True, capture_output=True)
    if clear:
        subprocess.run(["debugfs", "-w", "-R", f"clri <{ino}>", img], check=True, capture_output=True)
    return ino


def put_in_store(store, machine, label, image, extra=None):
    """Move `image` into the store as gatbox-disk would: <store>/<machine>/<label>_<day>_<sha1:8>.img and its sidecar.
    Returns the store-relative name."""
    h = hashlib.sha1()
    with open(image, "rb") as f:
        for b in iter(lambda: f.read(MiB), b""):
            h.update(b)
    sha1 = h.hexdigest()
    rel = f"{machine}/{label}_{time.strftime('%Y%m%d')}_{sha1[:8]}.img"
    os.makedirs(f"{store}/{machine}", exist_ok=True)
    shutil.move(image, f"{store}/{rel}")
    side = {"label": label, "machine": machine, "media": "hdd", "bytes": os.path.getsize(f"{store}/{rel}"), "sha1": sha1,
            "sha256": hashlib.sha256(sha1.encode()).hexdigest(), "source": {"vendor": "", "model": "Widget Drive", "bytes": 0},
            "started": time.time(), "finished": time.time(), "reads": 1, "also_read": [], "archive": {"state": "none"},
            "tool": "gatbox-disk"}
    side.update(extra or {})
    with open(f"{store}/{rel[:-4]}.json", "w") as f:
        json.dump(side, f)
    return rel
