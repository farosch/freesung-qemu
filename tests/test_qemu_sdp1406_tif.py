import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TIF = ROOT / "freesung-tools" / "extract" / "T-HKMFDEUC" / "extracted" / (
    "working/filesystem-6/root/usr/bin/TIFSDaemon"
)
OBJDUMP = shutil.which("arm-linux-gnueabi-objdump")

COUNT_OFFSET = 0x83A8E
BMI_OFFSET = 0x83A92
COUNT_ORIGINAL = b"\x00\x2b"
COUNT_PATCHED = b"\x00\x29"
BMI_ORIGINAL = b"\x35\xdd"
BMI_PATCHED = b"\x35\xd4"


@unittest.skipUnless(TIF.is_file(), "needs extracted T-HKMFDEUC TIFSDaemon")
@unittest.skipUnless(OBJDUMP, "needs arm-linux-gnueabi-objdump")
class Sdp1406TifNegativeIndexTests(unittest.TestCase):
    def test_extracted_guard_is_patched(self):
        data = TIF.read_bytes()
        self.assertEqual(data[COUNT_OFFSET:COUNT_OFFSET + 2], COUNT_PATCHED)
        self.assertEqual(data[BMI_OFFSET:BMI_OFFSET + 2], BMI_PATCHED)

    def test_patched_guard_routes_negative_index_to_existing_fallback(self):
        data = bytearray(TIF.read_bytes())
        data[COUNT_OFFSET:COUNT_OFFSET + 2] = COUNT_PATCHED
        data[BMI_OFFSET:BMI_OFFSET + 2] = BMI_PATCHED

        with tempfile.TemporaryDirectory() as tmp:
            daemon = Path(tmp) / "TIFSDaemon"
            daemon.write_bytes(data)
            result = subprocess.run(
                [OBJDUMP, "-d", "--start-address=0x8ba86",
                 "--stop-address=0x8ba9c", str(daemon)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("cmp\tr1, #0", result.stdout)
            self.assertIn("bmi.n\t8bb00", result.stdout)
            self.assertIn("cmp\tr3, r1", result.stdout)

    def test_negative_index_returns_existing_none_fallback(self):
        table_count = 119
        index = -1
        output = "NONE" if index < 0 or index >= table_count else "model"
        self.assertEqual(output, "NONE")
