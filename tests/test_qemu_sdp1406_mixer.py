import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

# Register check for the mixer model in freesung-qemu/hw/arm/sdp1406.c
# (sdp1406_add_mixer). It drives the machine over the qtest socket, which
# reaches MMIO handlers directly, so it needs no kernel or firmware. The QEMU
# binary comes from FREESUNG_QEMU; the test skips when neither has the sdp1406
# machine.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

MIXER_BASE = 0x005DC000
OSD_BASE = 0x005D4000
PLANE_BASE = 0x3978D000
# Offsets written by mixer_modeset_init() in sdp-drm.ko (0x20 is the 4K
# branch's main active size, 0x0f000870 = 3840x2160).
MIXER_WRITES = {
    0x04: 0x11,
    0x10: 0x1,
    0x18: 0x0,
    0x1C: 0x113008CA,
    0x20: 0x0F000870,
    0x24: 0x00800019,
}


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


class Sdp1406MixerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(cls.temp.name) / "qtest.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-accel", "qtest", "-qtest", f"unix:{socket_path},server,nowait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(socket_path)

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def _write(self, offset, value):
        reply = _qtest(self.sock, f"writel 0x{MIXER_BASE + offset:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{MIXER_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def test_modeset_writes_read_back(self):
        for offset, value in MIXER_WRITES.items():
            with self.subTest(offset=hex(offset)):
                self._write(offset, value)
                self.assertEqual(self._read(offset), value)

    def test_last_write_wins(self):
        self._write(0x20, 0x07800438)
        self._write(0x20, 0x05560300)
        self.assertEqual(self._read(0x20), 0x05560300)

    def test_unnamed_offset_is_stored(self):
        self._write(0x100, 0xDEADBEEF)
        self.assertEqual(self._read(0x100), 0xDEADBEEF)

    def test_registers_reset_to_zero_before_write(self):
        self.assertEqual(self._read(0x3FFC), 0)

    def test_plane_base_register_updates_model(self):
        self._write(0x30, PLANE_BASE)
        self.assertEqual(self._read(0x30), PLANE_BASE)

    def test_osd_plane_base_register_updates_model(self):
        reply = _qtest(self.sock, f"writel 0x{OSD_BASE + 0x30:x} 0x{PLANE_BASE:x}")
        self.assertEqual(reply, "OK")
        reply = _qtest(self.sock, f"readl 0x{OSD_BASE + 0x30:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        self.assertEqual(int(reply.split()[1], 16), PLANE_BASE)

    def test_osd_commit_control_sets_enable_mask(self):
        reply = _qtest(
            self.sock,
            f"writel 0x{OSD_BASE:x} 0x11",
        )
        self.assertEqual(reply, "OK")
        reply = _qtest(self.sock, f"readl 0x{OSD_BASE:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        self.assertEqual(int(reply.split()[1], 16), 0x11)

    def test_mixer_commit_control_sets_enable_mask(self):
        reply = _qtest(
            self.sock,
            f"writel 0x{MIXER_BASE:x} 0x11",
        )
        self.assertEqual(reply, "OK")
        reply = _qtest(self.sock, f"readl 0x{MIXER_BASE:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        self.assertEqual(int(reply.split()[1], 16), 0x11)

    def test_live_1080p_osd_commit_sequence(self):
        live_writes = {
            0x30: 0x37B84000,
            0x2C: 0x0690018,
            0x28: 0x07800438,
            0x10: 0x50002D0,
            0x14: 0x07800438,
            0x18: 0x140,
            0x20: 0xAAAA,
            0x24: 0xAAAA,
        }
        for offset, value in live_writes.items():
            if offset in (0x28, 0x2C):
                address = MIXER_BASE + offset
                reply = _qtest(
                    self.sock,
                    f"writel 0x{address:x} 0x{value:x}",
                )
                self.assertEqual(reply, "OK")
            else:
                address = OSD_BASE + offset
                reply = _qtest(
                    self.sock,
                    f"writel 0x{address:x} 0x{value:x}",
                )
                self.assertEqual(reply, "OK")
        reply = _qtest(
            self.sock,
            f"writel 0x{OSD_BASE:x} 0x311",
        )
        self.assertEqual(reply, "OK")
        reply = _qtest(self.sock, f"readl 0x{OSD_BASE:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        self.assertEqual(int(reply.split()[1], 16), 0x311)
        reply = _qtest(self.sock, f"readl 0x{OSD_BASE + 0x30:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        self.assertEqual(int(reply.split()[1], 16), 0x37B84000)


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
    while not data.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode(errors="replace").strip()


if __name__ == "__main__":
    unittest.main()
