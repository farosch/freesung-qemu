import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_qemu_sdp1406_mixer import SKIP, QEMU, _connect, _qtest

# Register checks for the gmac model in freesung-qemu/hw/arm/sdp1406.c
# (sdp1406_add_gmac). The tests drive the machine over the qtest socket, so
# they need no kernel or firmware. The DMA tests attach a UDP socket netdev and
# use a host-side peer to send and receive frames.
GMAC_BASE = 0x00120000
GICD_CTLR = 0x00781000
GICD_ISENABLER1 = 0x00781104
GICD_ISPENDR1 = 0x00781204
MII_ADDR = 0x10
MII_DATA = 0x14
MII_BUSY = 0x1
MII_WRITE = 0x2
DMA_BUS_MODE = 0x1000
DMA_TX_POLL = 0x1004
DMA_RX_POLL = 0x1008
DMA_RX_BASE = 0x100c
DMA_TX_BASE = 0x1010
DMA_STATUS = 0x1014
DMA_BUS_MODE_DSL2 = 0x10808
DMA_OP_MODE = 0x1018
DMA_OP_SR = 0x2
DMA_OP_ST = 0x2000
DMA_ST_TI = 0x1
DMA_ST_RI = 0x40
DMA_INT_EN = 0x101C
DES_OWN = 0x80000000
TDES0_IC = 0x40000000
TDES0_LS = 0x20000000
TDES0_FS = 0x10000000
TDES0_TER = 0x00200000
RDES1_RER = 0x8000


def mii_read(pa, gr):
    return (pa << 11) | (gr << 6) | MII_BUSY


def mii_write(pa, gr):
    return (pa << 11) | (gr << 6) | MII_WRITE | MII_BUSY


def make_frame(payload):
    frame = bytes.fromhex("ffffffffffff525400123456") + b"\x88\xb5" + payload
    return frame.ljust(64, b"\0")


class Sdp1406GmacTests(unittest.TestCase):
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
        reply = _qtest(self.sock, f"writel 0x{GMAC_BASE + offset:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{GMAC_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _mdio_read(self, pa, gr):
        self._write(MII_ADDR, mii_read(pa, gr))
        self.assertEqual(self._read(MII_ADDR) & MII_BUSY, 0)
        return self._read(MII_DATA)

    def test_phy_id_reads_at_address_one(self):
        self.assertEqual(self._mdio_read(1, 2), 0xabcd)
        self.assertEqual(self._mdio_read(1, 3), 0x0000)

    def test_bmsr_reports_link_up_and_autoneg_complete(self):
        bmsr = self._mdio_read(1, 1)
        self.assertTrue(bmsr & 0x0004, hex(bmsr))
        self.assertTrue(bmsr & 0x0020, hex(bmsr))

    def test_absent_addresses_read_all_ones(self):
        for pa in (0, 2, 31):
            with self.subTest(pa=pa):
                self.assertEqual(self._mdio_read(pa, 2), 0xffff)

    def test_mdio_write_to_writable_register_reads_back(self):
        self._write(MII_DATA, 0x1140)
        self._write(MII_ADDR, mii_write(1, 0))
        self.assertEqual(self._mdio_read(1, 0), 0x1140)

    def test_mdio_write_to_id_register_is_ignored(self):
        self._write(MII_DATA, 0xbeef)
        self._write(MII_ADDR, mii_write(1, 2))
        self.assertEqual(self._mdio_read(1, 2), 0xabcd)

    def test_dma_reset_bit_reads_clear(self):
        self.assertEqual(self._read(0x1000) & 1, 0)

    def test_version_register_passes_reset_check(self):
        # sdpGmac_reset() accepts low bytes 0x34, 0x36 and 0x37.
        self.assertIn(self._read(0x20) & 0xff, (0x34, 0x36, 0x37))


class Sdp1406GmacDmaTests(unittest.TestCase):
    TX_RING = 0x80200000
    TX_BUF = 0x80210000
    RX_RING = 0x80300000
    RX_BUF = 0x80310000

    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(cls.temp.name) / "qtest.sock")
        # QEMU sends TX frames to the peer port and listens for RX frames on
        # its own port. Both ports are picked by binding to port 0 first.
        cls.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cls.peer.bind(("127.0.0.1", 0))
        cls.peer.settimeout(5)
        cls.peer_port = cls.peer.getsockname()[1]
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        cls.qemu_port = probe.getsockname()[1]
        probe.close()
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-accel", "qtest", "-qtest", f"unix:{socket_path},server,nowait",
             "-nic", f"socket,udp=127.0.0.1:{cls.peer_port},"
                     f"localaddr=127.0.0.1:{cls.qemu_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(socket_path)

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.peer.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def setUp(self):
        # Software reset clears the ring and status state. Then use the driver's
        # descriptor layout (DSL 2, 24-byte stride, bus mode 0x10808) and
        # acknowledge any leftover status bits.
        self._reg_write(DMA_BUS_MODE, 0x1)
        self._reg_write(DMA_BUS_MODE, DMA_BUS_MODE_DSL2)
        self._reg_write(DMA_STATUS, 0x1ffff)
        # The QEMU instance is shared across tests, so clear buffers left behind.
        self._put_bytes(self.TX_BUF, bytes(128))
        self._put_bytes(self.RX_BUF, bytes(128))

    def _reg_write(self, offset, value):
        reply = _qtest(self.sock, f"writel 0x{GMAC_BASE + offset:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _reg_read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{GMAC_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _mem_write(self, addr, value):
        reply = _qtest(self.sock, f"writel 0x{addr:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _mem_read(self, addr):
        reply = _qtest(self.sock, f"readl 0x{addr:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _put_bytes(self, addr, data):
        data = data.ljust((len(data) + 3) & ~3, b"\0")
        for off in range(0, len(data), 4):
            self._mem_write(addr + off, int.from_bytes(data[off:off + 4], "little"))

    def _get_bytes(self, addr, n):
        out = b"".join(self._mem_read(addr + off).to_bytes(4, "little")
                       for off in range(0, n, 4))
        return out[:n]

    def _put_desc(self, addr, des0, des1, des2):
        # Six words per descriptor, as the production driver writes them.
        for off, value in enumerate((des0, des1, des2, 0, 0, 0)):
            self._mem_write(addr + 4 * off, value)

    def _wait_for_owner_clear(self, desc, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._mem_read(desc) & DES_OWN:
                return True
            time.sleep(0.02)
        return False

    def test_tx_ring_sends_owned_frame(self):
        frame = make_frame(b"tx-owned")
        self._put_bytes(self.TX_BUF, frame)
        self._put_desc(self.TX_RING, DES_OWN | TDES0_IC | TDES0_LS | TDES0_FS |
                       TDES0_TER, len(frame), self.TX_BUF)
        self._reg_write(DMA_TX_BASE, self.TX_RING)
        self._reg_write(DMA_OP_MODE, DMA_OP_ST | DMA_OP_SR)
        self._reg_write(DMA_TX_POLL, 0)

        got, _ = self.peer.recvfrom(2048)
        self.assertEqual(got, frame)
        self.assertEqual(self._mem_read(self.TX_RING) & DES_OWN, 0)
        status = self._reg_read(DMA_STATUS)
        self.assertTrue(status & DMA_ST_TI, hex(status))
        self._reg_write(DMA_STATUS, DMA_ST_TI)
        self.assertEqual(self._reg_read(DMA_STATUS) & DMA_ST_TI, 0)

    def test_tx_ring_walks_24_byte_stride(self):
        first = make_frame(b"tx-stride-0")
        second = make_frame(b"tx-stride-1")
        self._put_bytes(self.TX_BUF, first)
        self._put_bytes(self.TX_BUF + 0x100, second)
        # The slot at +16 stays unowned, so a 16-byte stride would stop early.
        self._put_desc(self.TX_RING, DES_OWN | TDES0_LS | TDES0_FS,
                       len(first), self.TX_BUF)
        self._put_desc(self.TX_RING + 24, DES_OWN | TDES0_LS | TDES0_FS |
                       TDES0_TER, len(second), self.TX_BUF + 0x100)
        self._reg_write(DMA_TX_BASE, self.TX_RING)
        self._reg_write(DMA_OP_MODE, DMA_OP_ST | DMA_OP_SR)
        self._reg_write(DMA_TX_POLL, 0)

        got, _ = self.peer.recvfrom(2048)
        self.assertEqual(got, first)
        got, _ = self.peer.recvfrom(2048)
        self.assertEqual(got, second)
        self.assertEqual(self._mem_read(self.TX_RING) & DES_OWN, 0)
        self.assertEqual(self._mem_read(self.TX_RING + 24) & DES_OWN, 0)

    def test_tx_ignored_without_start_bit(self):
        frame = make_frame(b"tx-idle")
        self._put_bytes(self.TX_BUF, frame)
        self._put_desc(self.TX_RING, DES_OWN | TDES0_IC | TDES0_LS | TDES0_FS |
                       TDES0_TER, len(frame), self.TX_BUF)
        self._reg_write(DMA_TX_BASE, self.TX_RING)
        self._reg_write(DMA_OP_MODE, DMA_OP_SR)
        self._reg_write(DMA_TX_POLL, 0)

        self.peer.settimeout(1)
        try:
            with self.assertRaises(socket.timeout):
                self.peer.recvfrom(2048)
        finally:
            self.peer.settimeout(5)
        self.assertTrue(self._mem_read(self.TX_RING) & DES_OWN)

    def test_rx_delivers_frame_into_owned_descriptor(self):
        frame = make_frame(b"rx-owned")
        self._put_desc(self.RX_RING, DES_OWN, RDES1_RER | 1518, self.RX_BUF)
        self._reg_write(DMA_RX_BASE, self.RX_RING)
        self._reg_write(DMA_OP_MODE, DMA_OP_SR)

        self.peer.sendto(frame, ("127.0.0.1", self.qemu_port))
        self.assertTrue(self._wait_for_owner_clear(self.RX_RING))
        des0 = self._mem_read(self.RX_RING)
        self.assertEqual((des0 >> 16) & 0x3fff, len(frame))
        self.assertEqual(self._get_bytes(self.RX_BUF, len(frame)), frame)
        status = self._reg_read(DMA_STATUS)
        self.assertTrue(status & DMA_ST_RI, hex(status))

    def test_rx_holds_frame_until_descriptor_is_owned(self):
        frame = make_frame(b"rx-held")
        self._put_desc(self.RX_RING, 0, RDES1_RER | 1518, self.RX_BUF)
        self._reg_write(DMA_RX_BASE, self.RX_RING)
        self._reg_write(DMA_OP_MODE, DMA_OP_SR)

        self.peer.sendto(frame, ("127.0.0.1", self.qemu_port))
        time.sleep(0.5)
        self.assertEqual(self._mem_read(self.RX_RING) & DES_OWN, 0)
        self.assertEqual(self._mem_read(self.RX_BUF), 0)

        self._mem_write(self.RX_RING, DES_OWN)
        self._reg_write(DMA_RX_POLL, 0)
        self.assertTrue(self._wait_for_owner_clear(self.RX_RING))
        self.assertEqual(self._get_bytes(self.RX_BUF, len(frame)), frame)

    def test_rx_raises_gic_interrupt_37(self):
        # DTB "samsung,sdp-mac" interrupts <0 5 0>: SPI 5, GIC IRQ 37.
        self._gic_write(GICD_CTLR, 1)
        self._gic_write(GICD_ISENABLER1, 1 << 5)
        frame = make_frame(b"rx-irq")
        self._put_desc(self.RX_RING, DES_OWN, RDES1_RER | 1518, self.RX_BUF)
        self._reg_write(DMA_RX_BASE, self.RX_RING)
        self._reg_write(DMA_INT_EN, DMA_ST_RI)
        self._reg_write(DMA_OP_MODE, DMA_OP_SR)

        self.peer.sendto(frame, ("127.0.0.1", self.qemu_port))
        self.assertTrue(self._wait_for_owner_clear(self.RX_RING))
        self.assertTrue(self._gic_read(GICD_ISPENDR1) & (1 << 5))

    def _gic_write(self, addr, value):
        reply = _qtest(self.sock, f"writel 0x{addr:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _gic_read(self, addr):
        reply = _qtest(self.sock, f"readl 0x{addr:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)


if __name__ == "__main__":
    unittest.main()
