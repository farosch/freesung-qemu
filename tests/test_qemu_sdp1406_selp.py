import importlib.util
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_SERVER = (
    ROOT.parent
    / "freesung-tools"
    / "extract"
    / "T-HKMFDEUC"
    / "extracted"
    / "working"
    / "filesystem-6"
    / "root"
    / "usr"
    / "bin"
    / "system_server"
)
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

    def test_fsd_vdfs_trace_constants_are_pinned(self):
        script = ROOT / "tools" / "fsd_vdfs_trace.py"
        text = script.read_text()
        self.assertIn("FSD_UDF_PC = 0xC0182E4C", text)
        self.assertIn("VDFS_WRITE_PC = 0xC0182E8C", text)
        self.assertIn("CH5_TRACE = 0xC10E0000", text)

    @unittest.skipUnless(SYSTEM_SERVER.is_file(), "needs extracted system_server")
    def test_system_server_poweroff_callback_has_no_direct_abort_call(self):
        objdump = shutil.which("arm-linux-gnueabi-objdump")
        if objdump is None:
            self.skipTest("needs arm-linux-gnueabi-objdump")
        result = subprocess.run(
            [objdump, "-d", "--start-address=0x1a508",
             "--stop-address=0x1a640", str(SYSTEM_SERVER)],
            capture_output=True,
            text=True,
            check=True,
        )
        disasm = result.stdout
        self.assertIn("vconf_get_int", disasm)
        self.assertIn("vconf_ignore_key_changed", disasm)
        self.assertIn("vconf_set_int", disasm)
        self.assertIn("poweroff", disasm)
        self.assertNotIn("abort@", disasm)


if __name__ == "__main__":
    unittest.main()
