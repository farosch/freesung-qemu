import json
import os
import shutil
import subprocess
import threading
import unittest
from pathlib import Path

# Register-level check for the USB host controllers in freesung-qemu/hw/arm/sdp1406.c.
# The DTB places four EHCI controllers at 0x500000 + n*0x10000 and four OHCI controllers
# at 0x508000 + n*0x10000. The test reads their standard capability and revision
# registers through the qtest MMIO path. It needs no firmware, kernel or device access.
# The QEMU binary comes from FREESUNG_QEMU or PATH; the test skips when neither has the
# sdp1406 machine.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

EHCI_BASE = 0x00500000
OHCI_BASE = 0x00508000
SLOT_STRIDE = 0x10000
SLOTS = 4
XHCI_BASE = 0x00400000
XHCI_SIZE = 0x0000c700
XHCI_WINDOW = 0x00100000
XHCI_CAP_WORD = 0x01000000 | 0x40

OHCI_HCREVISION = 0x00
OHCI_REV_1_0 = 0x10
EHCI_HCIVERSION_1_0 = 0x0100


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


class QtestSession:
    def __init__(self, extra=()):
        self.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-S", "-display", "none", "-serial", "null",
             "-qtest", "stdio", *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True)

    def _request(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        while True:
            reply = self.proc.stdout.readline()
            if not reply:
                raise RuntimeError(f"qtest closed after {line!r}")
            # qtest echoes trace lines in square brackets; the replies start with OK.
            if reply.startswith("OK"):
                return reply.split()

    def read(self, addr):
        parts = self._request(f"readl {addr:#x}")
        return int(parts[1], 16) if len(parts) > 1 else 0

    def write(self, addr, value):
        self._request(f"writel {addr:#x} {value:#x}")

    def close(self):
        # QEMU does not exit on stdin EOF in qtest mode, so kill it directly.
        self.proc.stdin.close()
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406UsbTests(unittest.TestCase):
    def setUp(self):
        self.qt = QtestSession()

    def tearDown(self):
        self.qt.close()

    def test_ohci_revision_in_every_slot(self):
        for n in range(SLOTS):
            base = OHCI_BASE + n * SLOT_STRIDE
            rev = self.qt.read(base + OHCI_HCREVISION) & 0xff
            self.assertEqual(rev, OHCI_REV_1_0, f"OHCI slot {n} at {base:#x}")

    def test_ehci_version_in_every_slot(self):
        for n in range(SLOTS):
            base = EHCI_BASE + n * SLOT_STRIDE
            # CAPLENGTH is byte 0 and HCIVERSION is the high halfword of the word.
            version = self.qt.read(base) >> 16
            self.assertEqual(version, EHCI_HCIVERSION_1_0,
                             f"EHCI slot {n} at {base:#x}")

    def test_xhci_capability_registers(self):
        # Real sysbus xHCI (hcd-xhci.c): HCIVERSION 0x0100 in the high halfword,
        # CAPLENGTH 0x40 in the low byte.
        self.assertEqual(self.qt.read(XHCI_BASE), XHCI_CAP_WORD)

    def test_xhci_tail_reads_zero_and_ignores_writes(self):
        # The DTB reg size is 0xc700; the rest of the window up to 0x500000 is a stub.
        tail = XHCI_BASE + XHCI_SIZE
        self.assertEqual(self.qt.read(tail), 0)
        self.assertEqual(self.qt.read(XHCI_BASE + XHCI_WINDOW - 4), 0)
        self.qt.write(tail + 0x20, 0x1)
        self.assertEqual(self.qt.read(tail), 0)


def _info_usb():
    # Ask the monitor for the USB topology; QMP is used because qtest has no USB view.
    script = "\n".join([
        '{"execute":"qmp_capabilities"}',
        '{"execute":"human-monitor-command","arguments":{"command-line":"info usb"}}',
        '{"execute":"quit"}',
    ]) + "\n"
    result = subprocess.run(
        [QEMU, "-M", "sdp1406", "-S", "-display", "none", "-serial", "null",
         "-qmp", "stdio"],
        input=script, capture_output=True, text=True, timeout=60)
    for line in result.stdout.splitlines():
        reply = json.loads(line) if line.startswith("{") else {}
        if isinstance(reply.get("return"), str) and "Device" in reply["return"]:
            return reply["return"]
    raise RuntimeError(f"no info usb reply: {result.stdout!r}")


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406BtUsbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.usb = _info_usb()

    def test_bt_controller_on_ehci_slot0_root_hub(self):
        # Slot 0 is the only EHCI with the Bluetooth device; it runs at high speed.
        self.assertEqual(self.usb.count("Bluetooth USB Device"), 1, self.usb)
        self.assertIn("Port 1, Speed 480 Mb/s, Product Bluetooth USB Device",
                      self.usb)


# Guest-boot check for the Bluetooth HCI bring-up. It needs the same local artifacts as
# the serial kernel test in test_qemu_sdp1406_serial.py and is skipped without them.
KERNEL = os.environ.get("FREESUNG_KERNEL", "")
DTB = os.environ.get("FREESUNG_DTB", "")
SYSMEM = os.environ.get("FREESUNG_SYSMEM", "")
CARD = os.environ.get("FREESUNG_CARD", "")
CMDLINE = ("root=/dev/mmcblk0p22 init=/sbin/init rootfstype=vdfs ro console=ttyS0,115200N8 "
           "SELP_ENABLE=00000000 rootwait no_console_suspend earlyprintk sdp_sparsemem vmalloc=550M")
HCI_TRACE = b"hci opcode "
# Opcodes the guest btusb/bt-service stack sends in order at bring-up.
BRING_UP_ORDER = (b"0x0c03", b"0x1001", b"0x0c1a")


def _boot_trace_until(args, marker, timeout):
    """Boot with the device trace on and return the output up to the first `marker` line.
    The QEMU process is always stopped on return."""
    proc = subprocess.Popen([QEMU] + args, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    done = threading.Event()

    def reader():
        for chunk in iter(lambda: proc.stdout.read1(4096), b""):
            output.extend(chunk)
            if marker in output:
                done.set()
                return
        done.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        done.wait(timeout)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        thread.join(5)
        proc.stdout.close()
    return bytes(output)


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406BtGuestBringUpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [name for name, value in (("FREESUNG_KERNEL", KERNEL), ("FREESUNG_DTB", DTB),
                                            ("FREESUNG_SYSMEM", SYSMEM), ("FREESUNG_CARD", CARD))
                   if not value or not os.path.exists(value)]
        if missing:
            raise unittest.SkipTest("kernel artifacts not set: " + ", ".join(missing))
        args = ["-M", "sdp1406", "-display", "none", "-serial", "stdio",
                "-trace", "bcm_btusb_hci_cmd",
                "-kernel", KERNEL, "-dtb", DTB,
                "-device", f"loader,file={SYSMEM},addr=0x80542b94,force-raw=on",
                "-drive", f"if=none,id=sdp1406-mmc,format=raw,snapshot=on,file={CARD}",
                "-append", CMDLINE]
        cls.output = _boot_trace_until(args, HCI_TRACE + b"0x0c1a", timeout=300)

    def test_guest_sends_bring_up_commands_in_order(self):
        positions = []
        for opcode in BRING_UP_ORDER:
            pos = self.output.find(HCI_TRACE + opcode)
            self.assertNotEqual(pos, -1, f"guest never sent {opcode!r}; "
                                         f"got {self.output.count(HCI_TRACE)} HCI commands")
            positions.append(pos)
        self.assertEqual(positions, sorted(positions), "bring-up opcodes out of order")


if __name__ == "__main__":
    unittest.main()
