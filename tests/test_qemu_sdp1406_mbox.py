import unittest

from test_qemu_sdp1406_mmc import QtestSession, SKIP

# Messagebox regression checks for freesung-qemu/hw/arm/sdp1406.c. The send register
# (0x480) must fill the RX record with the 0x29 reply frame and raise the
# pending bit; the clear register (0x444) must drop it. Runs over the same
# qtest protocol as test_qemu_sdp1406_mmc.py and skips the same way.
MBOX = 0x00800000
MBOX_STATUS = MBOX + 0x440
MBOX_CLEAR = MBOX + 0x444
MBOX_SEND = MBOX + 0x480
MBOX_TX = MBOX + 0x484
MBOX_BUSY = MBOX + 0x4C0
RX_LEN = MBOX + 0x400
RX_DATA = MBOX + 0x404


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406MboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QtestSession()
        cls.qt.write(MBOX_CLEAR, 1)

    @classmethod
    def tearDownClass(cls):
        cls.qt.close()

    def setUp(self):
        self.qt.write(MBOX_CLEAR, 1)
        self.qt.write(MBOX_BUSY, 0)

    def test_send_sets_pending_reply_bit(self):
        self.assertEqual(self.qt.read(MBOX_STATUS), 0)
        self.qt.write(MBOX_SEND, 0x9)
        self.assertEqual(self.qt.read(MBOX_STATUS) & 1, 1)

    def test_send_fills_rx_record_with_0x29_reply_frame(self):
        self.qt.write(MBOX_TX, 0x18FFFF)
        self.qt.write(MBOX_SEND, 0x9)
        self.assertEqual(self.qt.read(RX_LEN), 9)
        # Frame ff ff 29 00 00 00 00 00 29 as little-endian words.
        self.assertEqual(self.qt.read(RX_DATA), 0x0029FFFF)
        self.assertEqual(self.qt.read(RX_DATA + 4), 0)
        self.assertEqual(self.qt.read(RX_DATA + 8), 0x29)

    def test_reply_tag_follows_request_command(self):
        for cmd, tag in ((0x30, 0x30), (0x60, 0x50), (0x61, 0x51)):
            self.qt.write(MBOX_CLEAR, 1)
            self.qt.write(MBOX_TX, (cmd << 16) | 0xFFFF)
            self.qt.write(MBOX_SEND, 0x9)
            self.assertEqual(self.qt.read(RX_DATA), (tag << 16) | 0xFFFF)
            self.assertEqual(self.qt.read(RX_DATA + 8), tag)

    def test_clear_drops_pending_bit(self):
        self.qt.write(MBOX_SEND, 0x9)
        self.qt.write(MBOX_CLEAR, 1)
        self.assertEqual(self.qt.read(MBOX_STATUS) & 1, 0)

    def test_busy_flag_is_set_by_kick_and_cleared_by_ack(self):
        self.qt.write(MBOX_BUSY, 1)
        self.assertEqual(self.qt.read(MBOX_BUSY) & 1, 1)
        self.qt.write(MBOX_SEND, 0x9)
        self.qt.write(MBOX_CLEAR, 1)
        self.assertEqual(self.qt.read(MBOX_BUSY) & 1, 0)


if __name__ == "__main__":
    unittest.main()
