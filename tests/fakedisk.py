"""The fake machine the disk tests run against (tests/test-disk.py, tests/test-dash.py): a sysfs tree, plain files
standing in for /dev nodes, a mount table and an image store with its marker. Nothing here, or anywhere in the tests,
opens a real block device. Every name in it is invented (Widget Wars, the WIDGET drives).

    F = FakeDisks(tmpdir)
    F.usb_disk("sda", 3 << 20, "WIDGET", "WIDGETDRIVE 1000", port=1, devnum="8:0", data=b"...")
    F.mount_table()                    # the Pi's own root and boot, and the store on sdc1
    subprocess.run([...gatbox-disk...], env=F.env())
"""
import os
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MiB = 1 << 20


class FakeDisks:
    def __init__(self, root):
        self.root = root
        self.sys, self.dev, self.store, self.mountinfo = f"{root}/sys", f"{root}/dev", f"{root}/store", f"{root}/mountinfo"
        self.log = f"{root}/ddrescue.log"
        self.blocks = {}                    # name -> its sysfs block directory
        for d in (self.sys, self.dev, self.store):
            os.makedirs(d, exist_ok=True)
        open(f"{self.store}/.gatbox-image-store", "w").write("GATBOX image store\n")

    def usb_disk(self, name, nbytes, vendor, model, port, vid="1234", pid="5678", logical=512, devnum="8:0", parts=(),
                 data=None, driver="uas"):
        """A whole disk behind a USB bridge, as the kernel lays it out, and a plain file of NBYTES standing in for it."""
        usb = f"{self.sys}/devices/platform/axi/1000120000.pcie/xhci-hcd.0/usb2/2-{port}"
        os.makedirs(f"{usb}/2-{port}:1.0", exist_ok=True)
        for f, v in (("idVendor", vid), ("idProduct", pid), ("serial", "WIDGETDOCK0001")):
            open(f"{usb}/{f}", "w").write(v + "\n")
        drv = f"{self.sys}/bus/usb/drivers/{driver}"
        os.makedirs(drv, exist_ok=True)
        link = f"{usb}/2-{port}:1.0/driver"
        if not os.path.lexists(link):
            os.symlink(drv, link)
        scsi = f"{usb}/2-{port}:1.0/host{port}/target{port}:0:0/{port}:0:0:0"
        blk = f"{scsi}/block/{name}"
        shutil.rmtree(blk, ignore_errors=True)
        os.makedirs(f"{blk}/queue")
        for f, v in (("vendor", vendor), ("model", model), ("rev", "1.0")):
            open(f"{scsi}/{f}", "w").write(v + "\n")
        os.symlink(scsi, f"{blk}/device")
        open(f"{blk}/size", "w").write(f"{nbytes // 512}\n")
        open(f"{blk}/dev", "w").write(devnum + "\n")
        open(f"{blk}/removable", "w").write("0\n")
        open(f"{blk}/queue/logical_block_size", "w").write(f"{logical}\n")
        open(f"{blk}/queue/rotational", "w").write("1\n")
        major, minor = devnum.split(":")
        for i, _ in enumerate(parts, 1):
            os.makedirs(f"{blk}/{name}{i}")
            open(f"{blk}/{name}{i}/partition", "w").write(f"{i}\n")
            open(f"{blk}/{name}{i}/dev", "w").write(f"{major}:{int(minor) + i}\n")
        os.makedirs(f"{self.sys}/block", exist_ok=True)
        if os.path.lexists(f"{self.sys}/block/{name}"):
            os.remove(f"{self.sys}/block/{name}")
        os.symlink(blk, f"{self.sys}/block/{name}")
        with open(f"{self.dev}/{name}", "wb") as f:
            if data is not None:
                f.write(data)
            f.truncate(nbytes)
        self.blocks[name] = blk

    def unplug(self, name):
        """The drive is taken off the dock: its sysfs entry and its node go."""
        if os.path.lexists(f"{self.sys}/block/{name}"):
            os.remove(f"{self.sys}/block/{name}")
        if os.path.exists(f"{self.dev}/{name}"):
            os.remove(f"{self.dev}/{name}")
        self.blocks.pop(name, None)

    def other_disk(self, name, kind):
        """An SD card, NVMe drive or SATA disk on the Pi itself (not USB): sysfs has it, and gatbox-disk must never
        list or open it. A SATA disk on a PCIe HAT is still called sdX: only its place in sysfs says it isn't on USB."""
        where = {"mmc": "mmc_host/mmc0/mmc0:0001", "nvme": "1000110000.pcie/nvme/nvme0",
                 "sata": "1000110000.pcie/0000:01:00.0/ata1/host0/target0:0:0/0:0:0:0"}[kind]
        blk = f"{self.sys}/devices/platform/axi/{where}/block/{name}"
        os.makedirs(f"{blk}/queue", exist_ok=True)
        open(f"{blk}/size", "w").write("1000\n")
        open(f"{blk}/dev", "w").write("179:0\n")
        os.makedirs(f"{self.sys}/block", exist_ok=True)
        os.symlink(blk, f"{self.sys}/block/{name}")

    def by_id(self, name, link):
        os.makedirs(f"{self.dev}/disk/by-id", exist_ok=True)
        p = f"{self.dev}/disk/by-id/{link}"
        if os.path.lexists(p):
            os.remove(p)
        os.symlink(f"../../{name}", p)

    def mount_table(self, store=True, extra=()):
        lines = ["22 1 179:2 / / rw,noatime shared:1 - ext4 /dev/mmcblk0p2 rw",
                 "23 22 179:1 / /boot/firmware rw shared:2 - vfat /dev/mmcblk0p1 rw"]
        if store:
            lines.append(f"40 22 8:33 / {self.store} rw,noatime shared:9 - ext4 /dev/sdc1 rw")
        lines += list(extra)
        with open(self.mountinfo + ".tmp", "w") as f:       # gatbox-web may read it at any moment
            f.write("\n".join(lines) + "\n")
        os.replace(self.mountinfo + ".tmp", self.mountinfo)

    def env(self, **extra):
        """gatbox-disk's and gatbox-web's environment for this fake machine."""
        e = dict(os.environ, GATBOX_SYSFS=self.sys, GATBOX_DEV=self.dev, GATBOX_MOUNTINFO=self.mountinfo,
                 GATBOX_DISKS=self.store, GATBOX_DDRESCUE=os.path.join(REPO, "tests/fake-ddrescue"),
                 GATBOX_API="http://127.0.0.1:9", FAKE_DDRESCUE_LOG=self.log)
        e.update({k: str(v) for k, v in extra.items()})
        return e


def fat_card(nbytes, label=b"WIDGETCARD "):
    """A FAT32 boot sector at the start of a whole card (no partition table), as a camera formats one."""
    b = bytearray(nbytes)
    b[3:11] = b"MSDOS5.0"
    b[71:82] = label
    b[82:90] = b"FAT32   "
    b[510:512] = b"\x55\xaa"
    return bytes(b)
