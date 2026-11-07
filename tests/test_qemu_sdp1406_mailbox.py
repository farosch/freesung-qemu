import subprocess
import tempfile
import unittest
from pathlib import Path

from test_qemu_sdp1406_mixer import SKIP, QEMU, _connect, _qtest

# sdp_messagebox (DTB node at 0x00800100, reg size 0x500; the model maps the block at
# 0x00800000 and the guest offsets below are absolute within it). The guest writes the
# TX frame words at 0x484..0x48c, the length at 0x480, and reads the reply at 0x400
# (length) and 0x404..0x41c (frame). Reply pending is bit 0 of 0x440; 0x444 clears it.
MBOX_BASE = 0x00800000
MBOX_STATUS = 0x440
MBOX_CLEAR = 0x444
MBOX_SEND = 0x480
MBOX_TX = 0x484
MBOX_RX_LEN = 0x400
MBOX_RX_DATA = 0x404


def _tx_word(cmd):
    # Wire byte 2 is the command; the TX words are little-endian, so it sits in bits 16..23.
    return cmd << 16


class Sdp1406MailboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(cls.temp.name) / "qtest.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none",
             "-serial", "null", "-serial", "null",
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

    def _readl(self, offset):
        reply = _qtest(self.sock, f"readl 0x{MBOX_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK "), reply)
        return int(reply.split()[1], 16)

    def _writel(self, offset, value):
        self.assertEqual(_qtest(self.sock, f"writel 0x{MBOX_BASE + offset:x} 0x{value:x}"), "OK")

    def _request(self, cmd):
        self._writel(MBOX_TX, _tx_word(cmd))
        self._writel(MBOX_SEND, 9)
        self.assertTrue(self._readl(MBOX_STATUS) & 1)
        raw = b"".join(self._readl(MBOX_RX_DATA + 4 * i).to_bytes(4, "little") for i in range(3))
        self._writel(MBOX_CLEAR, 1)
        self.assertEqual(self._readl(MBOX_STATUS) & 1, 0)
        return raw[:9]

    def test_reply_length_and_pending_bit(self):
        self._writel(MBOX_TX, _tx_word(0x10))
        self._writel(MBOX_SEND, 9)
        self.assertEqual(self._readl(MBOX_RX_LEN), 9)
        self.assertTrue(self._readl(MBOX_STATUS) & 1)
        self._writel(MBOX_CLEAR, 1)

    def test_poweron_mode_ack_is_0x20(self):
        frame = self._request(0x10)
        self.assertEqual(frame[:2], b"\xff\xff")
        self.assertEqual(frame[2], 0x20)
        self.assertEqual(frame[8], sum(frame[2:8]) & 0xFF)

    def test_device3_registration_ack_is_0x29(self):
        self.assertEqual(self._request(0x18)[2], 0x29)

    def test_version_request_gets_ack_0x50_and_0x51(self):
        self.assertEqual(self._request(0x60)[2], 0x50)
        self.assertEqual(self._request(0x61)[2], 0x51)

    def test_state_change_requests_get_ack_0x2a_and_0x2b(self):
        self.assertEqual(self._request(0x19)[2], 0x2A)
        self.assertEqual(self._request(0x1A)[2], 0x2B)

    def test_status_requests_echo_their_command(self):
        self.assertEqual(self._request(0x6A)[2], 0x6A)
        self.assertEqual(self._request(0x6E)[2], 0x6E)

    def test_sw_inverter_request_echoes_0xd1(self):
        self.assertEqual(self._request(0xD1)[2], 0xD1)

    def test_jackident_request_echoes_0xa0(self):
        self.assertEqual(self._request(0xA0)[2], 0xA0)


if __name__ == "__main__":
    unittest.main()
