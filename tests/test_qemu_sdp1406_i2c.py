import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

# Register check for the sdp-i2c CH:7 controller model in
# hw/arm/sdp1406.c (sdp1406_add_i2c). The controller sits at 0x001901e0 and
# its shared IRQ status word is at 0x001901f4 (one bit per channel, CH:7 = 0x80).
# The QEMU binary comes from FREESUNG_QEMU; the test skips when neither has the
# sdp1406 machine.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

I2C_BASE = 0x001901E0
I2C_CON = 0x00
I2C_STAT = 0x04
I2C_DS = 0x0C
I2C_IRQ_STAT = 0x14
I2C_IRQ_EN = 0x18
CH7_BIT = 0x80
STAT_BUSY = 0x20
STAT_LRB = 0x01

# Periph stub at 0x00190000 is outside the CH:7 window and must keep reading 0.
PERIPH_STUB_ADDR = 0x00190100


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


class Sdp1406I2cTests(unittest.TestCase):
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

    def setUp(self):
        # Clear the shared IRQ status bits left by earlier tests.
        self._write(I2C_IRQ_STAT, 0xFFFFFFFF)

    def _write(self, offset, value):
        reply = _qtest(self.sock, f"writel 0x{I2C_BASE + offset:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{I2C_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _read_abs(self, addr):
        reply = _qtest(self.sock, f"readl 0x{addr:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def test_stat_write_sets_channel_irq(self):
        self._write(I2C_STAT, 0xB0)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, CH7_BIT)

    def test_ds_write_sets_channel_irq(self):
        self._write(I2C_DS, 0x55)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, CH7_BIT)

    def test_irq_status_is_write_one_to_clear(self):
        self._write(I2C_STAT, 0xF0)
        self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, 0)

    def test_stat_read_reports_idle_and_ack(self):
        self._write(I2C_STAT, 0xF0)
        stat = self._read(I2C_STAT)
        self.assertEqual(stat & STAT_BUSY, 0)
        self.assertEqual(stat & STAT_LRB, 0)

    def test_con_and_irq_enable_store_values(self):
        self._write(I2C_CON, 0x12345678)
        self._write(I2C_IRQ_EN, 0x0000_0080)
        self.assertEqual(self._read(I2C_CON), 0x12345678)
        self.assertEqual(self._read(I2C_IRQ_EN), 0x80)

    def test_channel_traffic_does_not_set_other_channel_bits(self):
        self._write(I2C_STAT, 0xF0)
        self.assertEqual(self._read(I2C_IRQ_STAT) & ~CH7_BIT & 0xFF, 0)

    def test_byte_write_is_accepted_and_merged(self):
        # The driver issues 1-byte writes (seen at +0x08); the other bytes of
        # the register must be preserved.
        self._write(I2C_CON, 0xAABBCCDD)
        reply = _qtest(self.sock, f"writeb 0x{I2C_BASE + I2C_CON + 1:x} 0x12")
        self.assertEqual(reply, "OK")
        self.assertEqual(self._read(I2C_CON), 0xAABB12DD)

    def test_periph_stub_outside_window_still_reads_zero(self):
        self.assertEqual(self._read_abs(PERIPH_STUB_ADDR), 0)


class Sdp1406I2cIrqTests(unittest.TestCase):
    # Intercepts GIC inputs at the arm_gic instance (unattached device[4] in
    # the sdp1406 machine). The CH:7 controller drives SPI 47.
    GIC_PATH = "/machine/unattached/device[4]"
    SPI = 47

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
        reply = _qtest(cls.sock, f"irq_intercept_in {cls.GIC_PATH}")
        if not reply.endswith("OK"):
            cls.sock.close()
            cls.proc.kill()
            cls.proc.wait()
            cls.temp.cleanup()
            raise AssertionError(f"irq_intercept_in failed: {reply}")

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def _write(self, offset, value):
        return _qtest(self.sock, f"writel 0x{I2C_BASE + offset:x} 0x{value:x}")

    def setUp(self):
        # The QEMU instance is shared by the class; start each test with CH:7 disabled
        # and no pending bits.
        self._write(I2C_IRQ_EN, 0)
        self._write(I2C_IRQ_STAT, 0xFFFFFFFF)

    def test_channel_event_without_enable_does_not_raise_spi(self):
        # The driver never enables CH:7 and polls the status word; the pending bit must
        # not drive the GIC line, or the driver's clear writes cannot stop it.
        reply = self._write(I2C_STAT, 0xF0)
        self.assertNotIn(f"IRQ raise {self.SPI}", reply.splitlines())
        self.assertEqual(_qtest(self.sock, f"readl 0x{I2C_BASE + I2C_IRQ_STAT:x}"),
                         f"OK 0x{CH7_BIT:016x}")

    def test_enabled_channel_event_raises_and_clear_lowers_spi(self):
        self._write(I2C_IRQ_EN, CH7_BIT)
        reply = self._write(I2C_STAT, 0xF0)
        self.assertIn(f"IRQ raise {self.SPI}", reply.splitlines())
        reply = self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertIn(f"IRQ lower {self.SPI}", reply.splitlines())

    def test_enabling_a_pending_channel_raises_spi(self):
        self._write(I2C_STAT, 0xF0)
        reply = self._write(I2C_IRQ_EN, CH7_BIT)
        self.assertIn(f"IRQ raise {self.SPI}", reply.splitlines())


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
