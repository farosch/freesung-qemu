import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

# Register-level regression check for the eMMC host model in
# freesung-qemu/hw/arm/sdp1406.c. It drives the paused machine through QEMU's qtest
# protocol, so each readl/writel goes through the real MMIO path. It needs no
# firmware, kernel or device access. The QEMU binary comes from FREESUNG_QEMU or
# PATH; the test skips when neither has the sdp1406 machine.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

MMC = 0x00100000
CTRL = MMC + 0x00
INTMASK = MMC + 0x24
CMDARG = MMC + 0x28
CMD = MMC + 0x2C
RESP0 = MMC + 0x30
MINTSTS = MMC + 0x40
RINTSTS = MMC + 0x44
HCON = MMC + 0x70
BMOD = MMC + 0x80
DBADDR = MMC + 0x88
IDSTS = MMC + 0x90
IDINTEN = MMC + 0x94
DSCADDR = MMC + 0x98
BYTCNT = MMC + 0x20

CMD_START = 1 << 31
CMD_UPD_CLK = 1 << 21
CMD_RESP = 1 << 6
CMD_DATA = 1 << 9
INT_CMD_DONE = 1 << 2
INT_DTO = 1 << 3
INT_RTO = 1 << 8
HCON_64BIT = 1 << 27
BMOD_DE = 1 << 7
IDSTS_TI = 1 << 0
IDSTS_DU = 1 << 4
IDSTS_NIS = 1 << 8
IDSTS_AIS = 1 << 9

DESC_OWN = 1 << 31
DESC_LD = 1 << 2
DESC_CH = 1 << 4
DESC0 = 0x01100000
DESC1 = 0x01100020
BUF0 = 0x01200000
CARD_R1 = 0x900
CMD_READ_SINGLE = 17
CMD_WRITE_SINGLE = 24 | (1 << 10)


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


class QtestSession:
    def __init__(self, extra=()):
        self.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-S", "-display", "none", "-serial", "null",
             "-qtest", "stdio", *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True)

    def _request(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        while True:
            reply = self.proc.stdout.readline()
            if not reply:
                raise RuntimeError(f"qtest closed after {line!r}")
            # qtest echoes trace lines in square brackets; the replies start with OK.
            if reply.startswith("OK"):
                return reply.split()

    def read(self, addr):
        parts = self._request(f"readl {addr:#x}")
        return int(parts[1], 16) if len(parts) > 1 else 0

    def write(self, addr, value):
        self._request(f"writel {addr:#x} {value:#x}")

    def close(self):
        # QEMU does not exit on stdin EOF in qtest mode, so kill it directly.
        self.proc.stdin.close()
        self.proc.kill()
        self.proc.wait()
        self.proc.stdout.close()


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406MmcTests(unittest.TestCase):
    def setUp(self):
        self.qt = QtestSession()

    def tearDown(self):
        self.qt.close()

    def test_hcon_reports_64bit_mmcif_addressing(self):
        self.assertEqual(self.qt.read(HCON) & HCON_64BIT, HCON_64BIT)

    def test_ctrl_reset_bits_self_clear_and_other_bits_stick(self):
        self.qt.write(CTRL, 0x13)
        self.assertEqual(self.qt.read(CTRL), 0x10)

    def test_bmod_software_reset_self_clears_and_other_bits_stick(self):
        self.qt.write(BMOD, 0x3)
        self.assertEqual(self.qt.read(BMOD), 0x2)

    def test_plain_registers_read_back_written_values(self):
        self.qt.write(CMDARG, 0x12345678)
        self.assertEqual(self.qt.read(CMDARG), 0x12345678)
        self.qt.write(INTMASK, 0x104)
        self.assertEqual(self.qt.read(INTMASK), 0x104)

    def test_command_with_response_sets_done_and_r1_status(self):
        self.qt.write(CMD, CMD_START | CMD_RESP | 13)
        self.assertEqual(self.qt.read(RINTSTS) & (INT_CMD_DONE | INT_RTO),
                         INT_CMD_DONE)
        self.assertEqual(self.qt.read(RESP0), CARD_R1)

    def test_cmd1_answers_with_ocr(self):
        self.qt.write(CMD, CMD_START | CMD_RESP | 1)
        self.assertEqual(self.qt.read(RESP0), 0xc0ff8080)

    def test_cmd2_answers_with_cid_words(self):
        self.qt.write(CMD, CMD_START | CMD_RESP | 0x80 | 2)
        self.assertNotEqual(self.qt.read(RESP0), 0)

    def test_command_without_response_sets_only_done(self):
        self.qt.write(CMD, CMD_START | 0)
        self.assertEqual(self.qt.read(RINTSTS) & (INT_CMD_DONE | INT_RTO),
                         INT_CMD_DONE)

    def test_update_clock_command_sets_no_status(self):
        self.qt.write(CMD, CMD_START | CMD_UPD_CLK)
        self.assertEqual(self.qt.read(RINTSTS) & (INT_CMD_DONE | INT_RTO), 0)

    def test_cmd_start_bit_is_not_read_back(self):
        self.qt.write(CMD, CMD_START | 0x40)
        self.assertEqual(self.qt.read(CMD), 0x40)

    def test_rintsts_is_write_one_to_clear(self):
        self.qt.write(CMD, CMD_START | CMD_RESP | 13)
        self.qt.write(RINTSTS, INT_CMD_DONE)
        self.assertEqual(self.qt.read(RINTSTS) & INT_CMD_DONE, 0)

    def test_mintsts_is_rintsts_masked_by_intmask(self):
        self.qt.write(CMD, CMD_START | CMD_RESP | 13)
        self.qt.write(INTMASK, 0)
        self.assertEqual(self.qt.read(MINTSTS) & INT_CMD_DONE, 0)
        self.qt.write(INTMASK, INT_CMD_DONE)
        self.assertEqual(self.qt.read(MINTSTS) & INT_CMD_DONE, INT_CMD_DONE)

    def test_response_register_is_zero_before_any_command(self):
        self.assertEqual(self.qt.read(RESP0), 0)

    def _write_desc(self, addr, status, length, buf, nxt=0):
        self.qt.write(addr, status)
        self.qt.write(addr + 4, 0)
        self.qt.write(addr + 8, length)
        self.qt.write(addr + 12, 0)
        self.qt.write(addr + 16, buf)
        self.qt.write(addr + 20, 0)
        self.qt.write(addr + 24, nxt)
        self.qt.write(addr + 28, 0)

    def _read_block(self):
        self.qt.write(BMOD, BMOD_DE)
        self.qt.write(BYTCNT, 512)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_READ_SINGLE)

    def test_idmac_single_descriptor_completes_and_releases_ownership(self):
        self._write_desc(DESC0, DESC_OWN | DESC_LD, 512, BUF0)
        self.qt.write(DBADDR, DESC0)
        self._read_block()
        self.assertEqual(self.qt.read(DSCADDR), DESC0)
        self.assertEqual(self.qt.read(IDSTS) & (IDSTS_TI | IDSTS_NIS),
                         IDSTS_TI | IDSTS_NIS)
        self.assertEqual(self.qt.read(DESC0) & DESC_OWN, 0)
        self.assertEqual(self.qt.read(RINTSTS) & INT_DTO, INT_DTO)

    def test_idmac_chain_walks_32_byte_descriptors(self):
        self._write_desc(DESC0, DESC_OWN | DESC_CH, 512, BUF0, DESC1)
        self._write_desc(DESC1, DESC_OWN | DESC_LD | DESC_CH, 512, BUF0 + 512)
        self.qt.write(DBADDR, DESC0)
        self.qt.write(BMOD, BMOD_DE)
        self.qt.write(BYTCNT, 1024)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_READ_SINGLE)
        self.assertEqual(self.qt.read(DSCADDR), DESC1)
        self.assertEqual(self.qt.read(DESC0) & DESC_OWN, 0)
        self.assertEqual(self.qt.read(DESC1) & DESC_OWN, 0)

    def test_idmac_without_ownership_reports_descriptor_unavailable(self):
        self._write_desc(DESC0, DESC_LD, 512, BUF0)
        self.qt.write(DBADDR, DESC0)
        self._read_block()
        self.assertEqual(self.qt.read(IDSTS) & (IDSTS_DU | IDSTS_AIS),
                         IDSTS_DU | IDSTS_AIS)

    def test_idsts_is_write_one_to_clear(self):
        self._write_desc(DESC0, DESC_LD, 512, BUF0)
        self.qt.write(DBADDR, DESC0)
        self._read_block()
        self.qt.write(IDSTS, IDSTS_DU | IDSTS_AIS | IDSTS_TI | IDSTS_NIS)
        self.assertEqual(self.qt.read(IDSTS), 0)


SECTOR_COUNT = 4
CMD_EXT_CSD = 8
CMD_READ_MULTI = 18
EXT_CSD_SEC_COUNT = 212
EXT_CSD_DEVICE_TYPE = 196


def _image_bytes():
    # Sector k is filled with the byte k+1, so each sector is identifiable.
    return b"".join(bytes([k + 1]) * 512 for k in range(SECTOR_COUNT))


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406MmcImageTests(unittest.TestCase):
    """Card data and EXT_CSD come from an attached raw image when one exists.
    These tests build a small synthetic image in a temp directory and attach it
    with -drive, so they need no firmware or device."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "card.img"
        self.path.write_bytes(_image_bytes())
        self.qt = QtestSession([
            "-drive", f"if=none,id=sdp1406-mmc,format=raw,file={self.path}"])

    def tearDown(self):
        self.qt.close()
        self.tmp.cleanup()

    def _desc(self, length):
        self.qt.write(DESC0, DESC_OWN | DESC_LD)
        self.qt.write(DESC0 + 4, 0)
        self.qt.write(DESC0 + 8, length)
        self.qt.write(DESC0 + 12, 0)
        self.qt.write(DESC0 + 16, BUF0)
        self.qt.write(DESC0 + 20, 0)
        self.qt.write(DESC0 + 24, 0)
        self.qt.write(DESC0 + 28, 0)
        self.qt.write(DBADDR, DESC0)
        self.qt.write(BMOD, BMOD_DE)
        self.qt.write(BYTCNT, length)

    def _read(self, lba, length, cmd=CMD_READ_MULTI):
        self._desc(length)
        self.qt.write(CMDARG, lba)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | cmd)

    def test_ext_csd_reports_image_sector_count_and_device_type(self):
        self._desc(512)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_EXT_CSD)
        self.assertEqual(self.qt.read(BUF0 + EXT_CSD_SEC_COUNT), SECTOR_COUNT)
        self.assertEqual(self.qt.read(BUF0 + EXT_CSD_DEVICE_TYPE) & 0xff, 0x03)

    def test_single_read_returns_the_addressed_image_sector(self):
        self._read(2, 512, CMD_READ_SINGLE)
        self.assertEqual(self.qt.read(BUF0), 0x03030303)

    def test_multi_block_read_is_contiguous_from_the_addressed_sector(self):
        self._read(1, 1024)
        self.assertEqual(self.qt.read(BUF0), 0x02020202)
        self.assertEqual(self.qt.read(BUF0 + 512), 0x03030303)

    def test_read_past_the_image_end_zero_fills(self):
        self.qt.write(BUF0, 0xffffffff)
        self._read(SECTOR_COUNT, 512)
        self.assertEqual(self.qt.read(BUF0), 0)

    def test_read_straddling_the_image_end_zero_fills_the_tail(self):
        self.qt.write(BUF0 + 512, 0xffffffff)
        self._read(SECTOR_COUNT - 1, 1024)
        self.assertEqual(self.qt.read(BUF0), 0x04040404)
        self.assertEqual(self.qt.read(BUF0 + 512), 0)

    def test_single_write_stores_the_buffer_in_the_addressed_sector(self):
        self.qt.write(BUF0, 0x0a0b0c0d)
        self._desc(512)
        self.qt.write(CMDARG, 1)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_WRITE_SINGLE)
        data = self.path.read_bytes()
        self.assertEqual(data[512:516], bytes([0x0d, 0x0c, 0x0b, 0x0a]))
        self.assertEqual(data[0:512], bytes([1]) * 512)
        self.assertEqual(data[1024:1536], bytes([3]) * 512)


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406MmcBoundaryTests(unittest.TestCase):
    P24_END_LBA = 5060193

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "p24-p25-boundary.img"
        with self.path.open("wb") as stream:
            stream.truncate((self.P24_END_LBA + 2) * 512)
        self.qt = QtestSession([
            "-drive", f"if=none,id=sdp1406-mmc,format=raw,file={self.path}"])

    def tearDown(self):
        self.qt.close()
        self.tmp.cleanup()

    def test_write_at_p24_p25_boundary_uses_raw_lba(self):
        self.qt.write(BUF0, 0x0d0c0b0a)
        self.qt.write(DESC0, DESC_OWN | DESC_LD)
        self.qt.write(DESC0 + 8, 512)
        self.qt.write(DESC0 + 16, BUF0)
        self.qt.write(DBADDR, DESC0)
        self.qt.write(BMOD, BMOD_DE)
        self.qt.write(BYTCNT, 512)
        self.qt.write(CMDARG, self.P24_END_LBA)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_WRITE_SINGLE)

        with self.path.open("rb") as stream:
            stream.seek(self.P24_END_LBA * 512)
            sector = stream.read(512)
        self.assertEqual(sector[:4], bytes([0x0a, 0x0b, 0x0c, 0x0d]))

    def test_consecutive_write_crosses_p24_p25_boundary(self):
        for lba, value in (
            (self.P24_END_LBA - 1, 0x04030201),
            (self.P24_END_LBA, 0x08070605),
        ):
            self.qt.write(BUF0, value)
            self.qt.write(DESC0, DESC_OWN | DESC_LD)
            self.qt.write(DESC0 + 8, 512)
            self.qt.write(DESC0 + 16, BUF0)
            self.qt.write(DBADDR, DESC0)
            self.qt.write(BMOD, BMOD_DE)
            self.qt.write(BYTCNT, 512)
            self.qt.write(CMDARG, lba)
            self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_WRITE_SINGLE)

        with self.path.open("rb") as stream:
            stream.seek((self.P24_END_LBA - 1) * 512)
            first = stream.read(512)
            second = stream.read(512)
        self.assertEqual(first[:4], bytes([0x01, 0x02, 0x03, 0x04]))
        self.assertEqual(second[:4], bytes([0x05, 0x06, 0x07, 0x08]))


@unittest.skipIf(SKIP, SKIP or "")
class Sdp1406MmcBlankTests(unittest.TestCase):
    """Without a -drive image the card is blank and reports the 1 GiB default."""

    def setUp(self):
        self.qt = QtestSession()

    def tearDown(self):
        self.qt.close()

    def test_blank_card_reports_one_gib_and_reads_zero(self):
        self.qt.write(DESC0, DESC_OWN | DESC_LD)
        self.qt.write(DESC0 + 8, 512)
        self.qt.write(DESC0 + 16, BUF0)
        self.qt.write(DBADDR, DESC0)
        self.qt.write(BMOD, BMOD_DE)
        self.qt.write(BYTCNT, 512)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_EXT_CSD)
        self.assertEqual(self.qt.read(BUF0 + EXT_CSD_SEC_COUNT), 1 << 21)
        self.qt.write(DESC0, DESC_OWN | DESC_LD)
        self.qt.write(BUF0, 0xffffffff)
        self.qt.write(CMDARG, 0)
        self.qt.write(CMD, CMD_START | CMD_RESP | CMD_DATA | CMD_READ_SINGLE)
        self.assertEqual(self.qt.read(BUF0), 0)


if __name__ == "__main__":
    unittest.main()
