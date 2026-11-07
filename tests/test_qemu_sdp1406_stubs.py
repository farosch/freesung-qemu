import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

# Behaviour check for the log-only stub windows in freesung-qemu/hw/arm/sdp1406.c.
# A stub reads 0, keeps no written value, and logs each access under -d unimp.
# Test addresses are offset 4 into each stub base, outside the later overlays
# (i2c, Mali, USB, xHCI). The QEMU binary comes from FREESUNG_QEMU or PATH.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

STUBS = {
    "sdp1406.chipid": 0x00180000,
    "sdp1406.periph": 0x00190000,
    "sdp1406.demux_ci": 0x00170000,
    "sdp1406.clkpin": 0x005C0000,
    "sdp1406.osd": 0x005D0000,
    "sdp1406.unnamed": 0x005E0000,
    "sdp1406.hard790": 0x00790000,
    "sdp1406.uddec": 0x00B00000,
    "sdp1406.unnamed2": 0x00B50000,
    "sdp1406.extin": 0x00B60000,
    "sdp1406.extin2": 0x00B70000,
    "sdp1406.unnamed3": 0x00B74000,
    "sdp1406.media": 0x00A00000,
    "sdp1406.demux": 0x00900000,
    "sdp1406.demux2": 0x00930000,
    "sdp1406.ddrbus": 0x00E00000,
}
OFFSET = 4
WRITE_VALUE = 0xDEADBEEF


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


class StubSession:
    def __init__(self, log):
        self.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-S", "-display", "none", "-serial", "null",
             "-qtest", "stdio", "-d", "unimp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)

    def _request(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        while True:
            reply = self.proc.stdout.readline()
            if not reply:
                raise RuntimeError(f"qtest closed after {line!r}")
            if reply.startswith("OK"):
                return reply.split()

    def read(self, addr):
        parts = self._request(f"readl {addr:#x}")
        return int(parts[1], 16) if len(parts) > 1 else 0

    def write(self, addr, value):
        self._request(f"writel {addr:#x} {value:#x}")

    def close(self):
        # QEMU does not exit on stdin EOF in qtest mode, so kill it directly.
        self.proc.kill()
        self.proc.wait()
        self.proc.stdin.close()
        self.proc.stdout.close()


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406StubTests(unittest.TestCase):
    def _run(self, action):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "unimp.log"
            with open(log_path, "w") as log:
                session = StubSession(log)
                try:
                    result = action(session)
                finally:
                    session.close()
            return result, log_path.read_text()

    def test_every_stub_reads_zero_and_keeps_no_write(self):
        def action(session):
            outcome = {}
            for name, base in STUBS.items():
                session.write(base + OFFSET, WRITE_VALUE)
                outcome[name] = session.read(base + OFFSET)
            return outcome

        outcome, _ = self._run(action)
        for name in STUBS:
            with self.subTest(region=name):
                self.assertEqual(outcome[name], 0)

    def test_every_stub_access_is_logged(self):
        def action(session):
            for base in STUBS.values():
                session.read(base + OFFSET)
                session.write(base + OFFSET, WRITE_VALUE)

        _, log = self._run(action)
        for name in STUBS:
            with self.subTest(region=name):
                self.assertIn(f"{name}: read 0x{OFFSET:x} (4 bytes)", log)
                self.assertIn(
                    f"{name}: write 0x{OFFSET:x} = 0x{WRITE_VALUE:x} (4 bytes)", log)


if __name__ == "__main__":
    unittest.main()
