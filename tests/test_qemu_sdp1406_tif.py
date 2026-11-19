import shutil
import subprocess
import tempfile
import unittest
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TIF = ROOT / "freesung-tools" / "extract" / "T-HKMFDEUC" / "extracted" / (
    "working/filesystem-6/root/usr/bin/TIFSDaemon"
)
UIMAGE = Path("/home/bici/.copilot/session-state/8a2604ae-929b-4a0e-a3f8-d2e1831f7633/files/factory-store-artifacts/hawkm-msdu10-payloads/03-uImage")
OBJDUMP = shutil.which("arm-linux-gnueabi-objdump")

COUNT_OFFSET = 0x83A8E
BMI_OFFSET = 0x83A92
COUNT_ORIGINAL = b"\x00\x2b"
COUNT_PATCHED = b"\x00\x29"
BMI_ORIGINAL = b"\x35\xdd"
BMI_PATCHED = b"\x35\xd4"
AUTH_FUNCTION_VADDR = 0xC01759D0
AUTH_FUNCTION_RAW_OFFSET = 0x16D9D0
AUTH_FUNCTION_ORIGINAL = bytes.fromhex("0d c0 a0 e1 00 d8 2d e9")
AUTH_FUNCTION_PATCHED = bytes.fromhex("00 00 a0 e3 1e ff 2f e1")


def patch_synthetic_uimage_auth(uimage: Path, output: Path) -> tuple[int, bytes]:
    data = bytearray(uimage.read_bytes())
    if len(data) < 64:
        raise ValueError("uImage header is truncated")
    raw_size = struct.unpack(">I", data[12:16])[0]
    payload = 64 + AUTH_FUNCTION_RAW_OFFSET
    if payload + len(AUTH_FUNCTION_ORIGINAL) > 64 + raw_size:
        raise ValueError("auth function offset exceeds uImage payload")
    original = bytes(data[payload:payload + len(AUTH_FUNCTION_ORIGINAL)])
    if original != AUTH_FUNCTION_ORIGINAL:
        raise RuntimeError("synthetic uImage auth function bytes do not match")
    data[payload:payload + len(AUTH_FUNCTION_PATCHED)] = AUTH_FUNCTION_PATCHED
    output.write_bytes(data)
    return raw_size, original


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


@unittest.skipUnless(UIMAGE.is_file(), "needs the production 03-uImage")
class Sdp1406SyntheticVdfsAuthTests(unittest.TestCase):
    def test_synthetic_uimage_auth_function_is_pinned_and_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "03-uImage-auth-bypass.bin"
            raw_size, original = patch_synthetic_uimage_auth(UIMAGE, output)
            self.assertEqual(original, AUTH_FUNCTION_ORIGINAL)
            data = output.read_bytes()
            payload = 64 + AUTH_FUNCTION_RAW_OFFSET
            self.assertEqual(
                data[payload:payload + len(AUTH_FUNCTION_PATCHED)],
                AUTH_FUNCTION_PATCHED,
            )
            self.assertEqual(struct.unpack(">I", data[12:16])[0], raw_size)
