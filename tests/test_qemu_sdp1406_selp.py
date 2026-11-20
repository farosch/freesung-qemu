import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "t32_attach",
    ROOT / "tools" / "t32_attach.py",
)
t32_attach = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(t32_attach)


class Sdp1406SelpHandshakeTests(unittest.TestCase):
    def test_pinned_seLp_addresses_match_production_kernel(self):
        self.assertEqual(t32_attach.SELP_BREAK_PC, 0xC0014E04)
        self.assertEqual(t32_attach.SELP_RESUME_PC, 0xC0014E0C)
        self.assertEqual(t32_attach.SELP_PTR_ADDR, 0xC0014EE0)
        self.assertEqual(t32_attach.STATUS_OFFSET, 0x18)

    def test_le_hex_is_little_endian(self):
        self.assertEqual(t32_attach.le_hex(0x12345678), "78563412")

    def test_rsp_packet_checksum_and_ack_format(self):
        packet = t32_attach.encode_packet("mC0014EE0,4")
        self.assertTrue(packet.startswith(b"$mC0014EE0,4#"))
        self.assertEqual(len(t32_attach.checksum("mC0014EE0,4")), 2)


if __name__ == "__main__":
    unittest.main()
