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
SII_WR = 0x40
SII_RD = 0x41
CH4_BASE = 0x00190180
CH8_BASE = 0x00191000
CH10_BASE = 0x00191040
# Group 1 shared IRQ words sit in the CH:15 window (0x001910e0 + 0x14/0x18).
GROUP1_IRQ_STAT = 0x001910F4
GROUP1_IRQ_EN = 0x001910F8
STAT_BUSY = 0x20
STAT_LRB = 0x01
# Must match SDP1406_I2C_XFER_NS in hw/arm/sdp1406.c.
SDP1406_I2C_XFER_NS = 100000

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
        # Clear the shared IRQ status bits left by earlier tests. A completion
        # still latched behind a set bit is raised by the first ack, so a
        # second ack clears it too.
        self._write(I2C_IRQ_STAT, 0xFFFFFFFF)
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

    def _advance(self, ns):
        reply = _qtest(self.sock, f"clock_step {ns}")
        self.assertTrue(reply.startswith("OK"), reply)

    def test_start_write_completes_after_transfer_time(self):
        # The driver reads the byte counter right after the start write, so
        # the transfer must still be busy and unacknowledged at that point.
        self._write(I2C_STAT, 0xF0)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, STAT_BUSY)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, 0)
        self._advance(SDP1406_I2C_XFER_NS)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, 0)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, CH7_BIT)

    def test_idle_stat_write_does_not_start_transfer(self):
        self._write(I2C_STAT, 0xD0)
        self._advance(SDP1406_I2C_XFER_NS)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, 0)

    def test_ds_write_completes_byte_after_transfer_time(self):
        # The handler clears the channel status it reads, so a byte's
        # completion must not appear during the DS write itself.
        self._write(I2C_DS, 0x55)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, STAT_BUSY)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, 0)
        self._advance(SDP1406_I2C_XFER_NS)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, 0)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, CH7_BIT)

    def test_read_address_queues_a_data_byte_completion(self):
        # An ACKed read address is followed by one data-byte completion, so
        # the driver gets a second IRQ after it acks the address.
        self._write(I2C_STAT, 0xD0)
        self._write(I2C_DS, 0x23)
        self._advance(SDP1406_I2C_XFER_NS)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, CH7_BIT)
        self.assertEqual(self._read(I2C_STAT) & STAT_LRB, 0)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, STAT_BUSY)
        self._write(I2C_IRQ_STAT, CH7_BIT)
        self._advance(SDP1406_I2C_XFER_NS)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, 0)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, CH7_BIT)

    def test_each_ds_read_with_ack_set_completes_the_next_byte(self):
        self._write(I2C_CON, 0x100)
        self._write(I2C_STAT, 0xD0)
        self._write(I2C_DS, 0x23)
        self._advance(SDP1406_I2C_XFER_NS)
        self._write(I2C_IRQ_STAT, CH7_BIT)
        self._advance(SDP1406_I2C_XFER_NS)
        self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertEqual(self._read(I2C_DS), 0)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, STAT_BUSY)
        self._advance(SDP1406_I2C_XFER_NS)
        self.assertEqual(self._read(I2C_STAT) & STAT_BUSY, 0)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, CH7_BIT)

    def _address_byte(self, addr):
        # The driver writes STAT idle, then the raw 7-bit address into DS.
        self._write(I2C_STAT, 0xD0)
        self._write(I2C_DS, addr)
        self._advance(SDP1406_I2C_XFER_NS)
        return self._read(I2C_STAT) & STAT_LRB

    def test_address_0x40_is_acked_by_sii9777(self):
        self.assertEqual(self._address_byte(0x40), 0)

    def test_address_0x22_is_acked(self):
        self.assertEqual(self._address_byte(0x22), 0)

    def test_data_byte_after_address_is_not_an_address(self):
        self.assertEqual(self._address_byte(0x40), 0)
        self._write(I2C_STAT, 0xF0)
        self._write(I2C_DS, 0x55)
        self._advance(SDP1406_I2C_XFER_NS)
        self.assertEqual(self._read(I2C_STAT) & STAT_LRB, 0)

    def test_irq_status_is_write_one_to_clear(self):
        self._write(I2C_STAT, 0xF0)
        self._advance(SDP1406_I2C_XFER_NS)
        self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertEqual(self._read(I2C_IRQ_STAT) & CH7_BIT, 0)

    def test_stat_read_reports_ack_and_idle_when_not_busy(self):
        self._write(I2C_STAT, 0xD0)
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
        self._advance(SDP1406_I2C_XFER_NS)
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
        # The QEMU instance is shared by the class; start each test with CH:7
        # unmasked and no pending bits.
        self._write(I2C_IRQ_EN, 0)
        # A completion latched by an earlier test is re-raised by the first
        # ack, so a second ack is needed to clear it.
        self._write(I2C_IRQ_STAT, 0xFFFFFFFF)
        self._write(I2C_IRQ_STAT, 0xFFFFFFFF)
        self._write_abs(GROUP1_IRQ_STAT, 0xFFFFFFFF)
        self._write_abs(GROUP1_IRQ_EN, 0)

    def _write_abs(self, addr, value):
        return _qtest(self.sock, f"writel 0x{addr:x} 0x{value:x}")

    def _advance(self, ns):
        return _qtest(self.sock, f"clock_step {ns}")

    def test_ch4_completion_raises_spi_44_on_group0_bit4(self):
        # CH:4 is the 0x00190180 controller; its completions share the group 0
        # status word at 0x001901f4 on bit 4.
        self._write_abs(CH4_BASE + I2C_STAT, 0xF0)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertIn("IRQ raise 44", reply.splitlines())
        self.assertEqual(_qtest(self.sock, f"readl 0x{I2C_BASE + I2C_IRQ_STAT:x}"),
                         "OK 0x0000000000000010")
        reply = self._write_abs(I2C_BASE + I2C_IRQ_STAT, 0x10)
        self.assertIn("IRQ lower 44", reply.splitlines())

    def test_ch8_completion_raises_spi_48_on_group1_bit0(self):
        # CH:8 is the first controller of group 1; its completions set bit 0 of
        # the shared status word at 0x001910f4, read through CH:15's window.
        self._write_abs(CH8_BASE + I2C_STAT, 0xF0)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertIn("IRQ raise 48", reply.splitlines())
        self.assertEqual(_qtest(self.sock, f"readl 0x{GROUP1_IRQ_STAT:x}"),
                         "OK 0x0000000000000001")
        reply = self._write_abs(GROUP1_IRQ_STAT, 0x01)
        self.assertIn("IRQ lower 48", reply.splitlines())

    def test_ch10_completion_raises_spi_50_on_group1_bit2(self):
        # CH:10 (i2c@00191040, the FRC TCON panel at 0x64) completes on bit 2
        # of the group 1 status word. Its DS write of 0xc8 is ACKed by the model.
        self._write_abs(CH10_BASE + I2C_STAT, 0xD0)
        self._write_abs(CH10_BASE + I2C_DS, 0xC8)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertIn("IRQ raise 50", reply.splitlines())
        self.assertEqual(_qtest(self.sock, f"readl 0x{GROUP1_IRQ_STAT:x}"),
                         "OK 0x0000000000000004")
        stat = int(_qtest(self.sock, f"readl 0x{CH10_BASE + I2C_STAT:x}").split()[1], 16)
        self.assertEqual(stat & STAT_LRB, 0)
        reply = self._write_abs(GROUP1_IRQ_STAT, 0x04)
        self.assertIn("IRQ lower 50", reply.splitlines())

    def test_ch10_read_address_is_acked_and_queues_data_completion(self):
        self._write_abs(CH10_BASE + I2C_STAT, 0xD0)
        self._write_abs(CH10_BASE + I2C_DS, 0xC9)
        self._advance(SDP1406_I2C_XFER_NS)
        stat = int(_qtest(self.sock, f"readl 0x{CH10_BASE + I2C_STAT:x}").split()[1], 16)
        self.assertEqual(stat & STAT_LRB, 0)
        self._write_abs(GROUP1_IRQ_STAT, 0x04)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertIn("IRQ raise 50", reply.splitlines())

    def test_masked_channel_event_does_not_raise_spi(self):
        # A masked channel keeps the pending bit but must not drive the GIC line.
        self._write(I2C_IRQ_EN, CH7_BIT)
        self._write(I2C_STAT, 0xF0)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertNotIn(f"IRQ raise {self.SPI}", reply.splitlines())
        self.assertEqual(_qtest(self.sock, f"readl 0x{I2C_BASE + I2C_IRQ_STAT:x}"),
                         f"OK 0x{CH7_BIT:016x}")

    def test_unmasked_channel_event_raises_and_clear_lowers_spi(self):
        self._write(I2C_STAT, 0xF0)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertIn(f"IRQ raise {self.SPI}", reply.splitlines())
        reply = self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertIn(f"IRQ lower {self.SPI}", reply.splitlines())

    def test_unmasking_a_pending_channel_raises_spi(self):
        self._write(I2C_IRQ_EN, CH7_BIT)
        self._write(I2C_STAT, 0xF0)
        self._advance(SDP1406_I2C_XFER_NS)
        reply = self._write(I2C_IRQ_EN, 0)
        self.assertIn(f"IRQ raise {self.SPI}", reply.splitlines())

    def test_completion_during_unacked_bit_is_raised_on_ack(self):
        # The driver can start the next byte before it acks the previous IRQ.
        # The second completion stays latched and is raised again on ack, so
        # the line never drops.
        self._write(I2C_STAT, 0xF0)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertIn(f"IRQ raise {self.SPI}", reply.splitlines())
        self._write(I2C_STAT, 0xF0)
        self._advance(SDP1406_I2C_XFER_NS)
        reply = self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertNotIn(f"IRQ lower {self.SPI}", reply.splitlines())
        self.assertEqual(_qtest(self.sock, f"readl 0x{I2C_BASE + I2C_IRQ_STAT:x}"),
                         f"OK 0x{CH7_BIT:016x}")
        reply = self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertIn(f"IRQ lower {self.SPI}", reply.splitlines())

    def test_each_queued_completion_is_raised_on_its_own_ack(self):
        # Two completions land while the first is still unacked. Each must
        # survive as a separate IRQ, so the line drops only after three acks.
        self._write(I2C_STAT, 0xF0)
        self._advance(SDP1406_I2C_XFER_NS)
        self._write(I2C_STAT, 0xF0)
        self._advance(SDP1406_I2C_XFER_NS)
        self._write(I2C_STAT, 0xF0)
        self._advance(SDP1406_I2C_XFER_NS)
        for _ in range(2):
            reply = self._write(I2C_IRQ_STAT, CH7_BIT)
            self.assertNotIn(f"IRQ lower {self.SPI}", reply.splitlines())
        reply = self._write(I2C_IRQ_STAT, CH7_BIT)
        self.assertIn(f"IRQ lower {self.SPI}", reply.splitlines())

    def test_transfer_started_with_driver_mask_sequence_raises_spi(self):
        # The driver clears the CH:7 mask bit before a transfer and sets it
        # again when the transfer ends. Completion must still raise SPI 47.
        self._write(I2C_IRQ_EN, 0)
        self._write(I2C_STAT, 0xD0)
        self._write(I2C_STAT, 0xF0)
        reply = self._advance(SDP1406_I2C_XFER_NS)
        self.assertIn(f"IRQ raise {self.SPI}", reply.splitlines())


class Sdp1406I2cEepromTests(unittest.TestCase):
    # The S24C512C at 0xa0 on CH:4 (0x00190180). Its completions use group 0
    # status bit 4, which lives in the CH:7 window at 0x001901f4.
    EEP_BIT = 0x10
    EEP_MAC = [0x02, 0x53, 0x44, 0x14, 0x06, 0x01]

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
        self._write_abs(I2C_BASE + I2C_IRQ_STAT, 0xFFFFFFFF)
        self._write_abs(I2C_BASE + I2C_IRQ_STAT, 0xFFFFFFFF)

    def _write_abs(self, addr, value):
        reply = _qtest(self.sock, f"writel 0x{addr:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _read_abs(self, addr):
        reply = _qtest(self.sock, f"readl 0x{addr:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _ch4(self, offset, value):
        self._write_abs(CH4_BASE + offset, value)

    def _step(self):
        # Let one byte complete, then ack its IRQ so the next one is not latched.
        reply = _qtest(self.sock, f"clock_step {SDP1406_I2C_XFER_NS}")
        self.assertTrue(reply.startswith("OK"), reply)
        self._write_abs(I2C_BASE + I2C_IRQ_STAT, self.EEP_BIT)

    def _byte(self, value):
        self._ch4(I2C_DS, value)
        self._step()

    def _start(self, addr):
        # Idle STAT write begins an address phase, then the 8-bit address.
        self._ch4(I2C_STAT, 0xD0)
        self._byte(addr)

    def _set_pointer(self, word_addr):
        self._start(0xA0)
        self._byte(word_addr >> 8)
        self._byte(word_addr & 0xFF)

    def _write_bytes(self, word_addr, data):
        self._set_pointer(word_addr)
        for value in data:
            self._byte(value)

    def _read_bytes(self, count, addr=0xA1):
        self._ch4(I2C_CON, 0x100)
        self._start(addr)
        self._step()
        out = []
        for _ in range(count):
            out.append(self._read_abs(CH4_BASE + I2C_DS) & 0xFF)
            self._step()
        return out

    def test_mac_is_read_at_0x1e04(self):
        self._set_pointer(0x1E04)
        self.assertEqual(self._read_bytes(len(self.EEP_MAC)), self.EEP_MAC)

    def test_factory_flag_at_0xf212_is_0x01(self):
        # automation-helper reads 0xf212; 0x01 selects the factory production path.
        self._set_pointer(0xF212)
        self.assertEqual(self._read_bytes(1), [0x01])

    def test_sequential_read_follows_pointer(self):
        self._write_bytes(0x0200, [0x5A, 0xA5, 0x3C])
        self._set_pointer(0x0200)
        self.assertEqual(self._read_bytes(3), [0x5A, 0xA5, 0x3C])

    def test_write_address_0xa0_with_no_data_sets_only_pointer(self):
        self._write_bytes(0x0300, [0x77])
        self._set_pointer(0x0300)
        self.assertEqual(self._read_bytes(1), [0x77])

    def test_page_write_wraps_within_page(self):
        # Writes starting at 0x7e wrap at the 128-byte page boundary, so the
        # third byte lands at 0x0000 and not at 0x0080.
        self._write_bytes(0x007E, [0x11, 0x22, 0x33])
        self._set_pointer(0x0000)
        self.assertEqual(self._read_bytes(1), [0x33])
        self._set_pointer(0x0080)
        self.assertEqual(self._read_bytes(1), [0xFF])

    def test_erased_bytes_read_0xff(self):
        self._set_pointer(0x4000)
        self.assertEqual(self._read_bytes(2), [0xFF, 0xFF])

    def test_other_address_on_ch4_reads_zero(self):
        self._ch4(I2C_CON, 0x100)
        self._start(0xA3)
        self._step()
        self.assertEqual(self._read_abs(CH4_BASE + I2C_STAT) & STAT_LRB, 0)
        self.assertEqual(self._read_bytes(1, addr=0xA3), [0x00])


class Sdp1406SiiTests(unittest.TestCase):
    # The Sii9777 at DS byte 0x40 on CH:7 (0x001901e0). Its completions use
    # the CH:7 bit of the group 0 status word, which lives in the CH:7 window.
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
        self._write(I2C_IRQ_STAT, 0xFFFFFFFF)
        self._write(I2C_IRQ_STAT, 0xFFFFFFFF)

    def _write(self, offset, value):
        reply = _qtest(self.sock, f"writel 0x{I2C_BASE + offset:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{I2C_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _step(self):
        reply = _qtest(self.sock, f"clock_step {SDP1406_I2C_XFER_NS}")
        self.assertTrue(reply.startswith("OK"), reply)
        self._write(I2C_IRQ_STAT, CH7_BIT)

    def _byte(self, value):
        self._write(I2C_DS, value)
        self._step()

    def _start(self, addr):
        self._write(I2C_STAT, 0xD0)
        self._byte(addr)

    def _set_pointer(self, reg):
        self._start(SII_WR)
        self._byte(reg >> 8)
        self._byte(reg & 0xFF)

    def _write_regs(self, reg, data):
        self._set_pointer(reg)
        for value in data:
            self._byte(value)

    def _read_regs(self, count):
        self._write(I2C_CON, 0x100)
        self._start(SII_RD)
        self._step()
        out = []
        for _ in range(count):
            out.append(self._read(I2C_DS) & 0xFF)
            self._step()
        return out

    def test_address_0x40_is_acked_and_sets_no_error(self):
        self._start(SII_WR)
        self.assertEqual(self._read(I2C_STAT) & STAT_LRB, 0)

    def test_written_registers_read_back(self):
        self._write_regs(0x0010, [0x70, 0x61, 0x0F])
        self._set_pointer(0x0010)
        self.assertEqual(self._read_regs(3), [0x70, 0x61, 0x0F])

    def test_registers_auto_increment_without_page_wrap(self):
        # The Sii9777 does not wrap at the EEPROM's 128-byte page: the third
        # byte lands at 0x0080, not at 0x0000.
        self._write_regs(0x007E, [0x11, 0x22, 0x33])
        self._set_pointer(0x007E)
        self.assertEqual(self._read_regs(3), [0x11, 0x22, 0x33])
        self._set_pointer(0x0080)
        self.assertEqual(self._read_regs(1), [0x33])
        self._set_pointer(0x3000)
        self.assertEqual(self._read_regs(1), [0x00])

        self._set_pointer(0x1234)
        self.assertEqual(self._read_regs(2), [0x00, 0x00])

    def test_pointer_is_16_bit_high_byte_first(self):
        self._write_regs(0x0100, [0xAA])
        self._write_regs(0x0000, [0xBB])
        self._set_pointer(0x0100)
        self.assertEqual(self._read_regs(1), [0xAA])
        self._set_pointer(0x0000)
        self.assertEqual(self._read_regs(1), [0xBB])


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
    # Async "IRQ raise/lower" lines can arrive before the command's final
    # OK/ERR line, so read until that line, not just the first newline.
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


if __name__ == "__main__":
    unittest.main()
