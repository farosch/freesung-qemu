import os
import shutil
import subprocess
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


if __name__ == "__main__":
    unittest.main()
