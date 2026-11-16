import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

# Qtest check for the log-only SMC trap device in freesung-qemu/hw/arm/sdp1406.c
# (sdp1406_add_smc_log). The guest monitor stores r0, r1, ip and a commit word at
# 0x00201000..0x0020100c. The device records watch-page registrations (r1 = 0 with
# ip = 0x0e000000) at 0x00201014 onwards; NPAGES is at 0x00201010. The QEMU binary
# comes from FREESUNG_QEMU; the test skips when neither has the sdp1406 machine.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

SMC_BASE = 0x00201000
R0 = 0x00
R1 = 0x04
IP = 0x08
COMMIT = 0x0C
NPAGES = 0x10
PAGES = 0x14
CMD_REG = 0
CMD_WAIT = 14
CMD_REQ = 7
IP_REG = 0x0E000000
PAGE_A = 0x83825000
PAGE_B = 0x838A4000


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


def _connect(path):
    deadline = time.time() + 10
    while True:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
            break
        except OSError:
            sock.close()
            if time.time() > deadline:
                raise
            time.sleep(0.05)
    sock.settimeout(5)
    return sock


def _qtest(sock, command):
    sock.sendall(command.encode() + b"\n")
    data = b""
    while True:
        if data.endswith(b"\n"):
            lines = data.decode(errors="replace").splitlines()
            if lines[-1].startswith(("OK", "ERR")):
                break
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode(errors="replace").strip()


class Sdp1406SmcTests(unittest.TestCase):
    def setUp(self):
        if SKIP:
            self.skipTest(SKIP)
        self.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(self.temp.name) / "qtest.sock")
        self.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-accel", "qtest", "-qtest", f"unix:{socket_path},server,nowait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.sock = _connect(socket_path)

    def tearDown(self):
        if SKIP:
            return
        self.sock.close()
        self.proc.kill()
        self.proc.wait()
        self.temp.cleanup()

    def _smc(self, r0, r1, ip):
        for offset, value in ((R0, r0), (R1, r1), (IP, ip), (COMMIT, 1)):
            reply = _qtest(self.sock, f"writel 0x{SMC_BASE + offset:x} 0x{value:x}")
            self.assertEqual(reply, "OK")

    def _read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{SMC_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def test_watch_page_registration_is_recorded(self):
        self._smc(PAGE_A, CMD_REG, IP_REG)
        self.assertEqual(self._read(NPAGES), 1)
        self.assertEqual(self._read(PAGES), PAGE_A)

    def test_registration_needs_the_registration_ip(self):
        self._smc(PAGE_A, CMD_REG, IP_REG + 4)
        self.assertEqual(self._read(NPAGES), 0)

    def test_wait_command_is_not_recorded(self):
        self._smc(PAGE_A, CMD_WAIT, IP_REG)
        self.assertEqual(self._read(NPAGES), 0)

    def test_registry_keeps_first_four_pages(self):
        for page in (PAGE_A, PAGE_B, 0x83D3A000, 0x838C0000, 0x83EB2000):
            self._smc(page, CMD_REG, IP_REG)
        self.assertEqual(self._read(NPAGES), 4)
        self.assertEqual(self._read(PAGES + 12), 0x838C0000)

    def test_request_leaves_registry_unchanged(self):
        self._smc(PAGE_A, CMD_REG, IP_REG)
        self._smc(0, CMD_REQ, IP_REG + 1)
        self.assertEqual(self._read(NPAGES), 1)
        self.assertEqual(self._read(PAGES), PAGE_A)


if __name__ == "__main__":
    unittest.main()
