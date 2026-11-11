import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

# Offline checks for freesung-qemu/mmc/mkcard.py. The synthetic partition map uses the
# same tab-separated, CRLF layout as the HawkM partition.txt, so no firmware or
# extracted tree is needed. The GPT checks follow the EFI rules that the 3.10
# kernel enforces in block/partitions/efi.c (is_gpt_valid, is_pte_valid).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mmc"))
import mkcard  # noqa: E402

SECTOR = 512
HEADER = (
    "v4\t\t\t\r\n"
    "partitionID\tflash_device_name\tflash_device_size\tflash_image_name\r\n"
)
MAP = HEADER + "".join(
    f"{row}\r\n" for row in [
        "0\t/dev/mmcblk0p0\t512K\tonboot.bin\tMLC\tOTHER\tBOOTLOADER0\tNONE\tWO",
        "1\t/dev/mmcblk0p1\t1536K\tseret.bin\tMLC\tUSER\tSERET0\tNONE\tRO",
        "2\t/dev/mmcblk0p2\t0K\tnone.bin\tMLC\tUSER\tSPARE\tNONE\tRO",
        "3\t/dev/mmcblk0p3\t300000K\tplatform.img\tMLC\tUSER\tPLATFORM\t/\tRW",
        "4\t/dev/mmcblk0p4\t2048K\tdata.img\tMLC\tUSER\tDATA\t/opt\tRW",
    ]
)


class MkcardLayoutTests(unittest.TestCase):
    def test_parse_skips_header_rows_and_keeps_order(self):
        parts = mkcard.parse_partition_map(MAP)
        self.assertEqual([p[0] for p in parts], [0, 1, 2, 3, 4])
        self.assertEqual(parts[1], (1, 1536, "seret.bin"))
        self.assertEqual(parts[3], (3, 300000, "platform.img"))

    def test_parse_rejects_non_contiguous_indices(self):
        text = MAP.replace("\r\n2\t", "\r\n5\t")
        with self.assertRaises(ValueError):
            mkcard.parse_partition_map(text)

    def test_parse_rejects_unexpected_size_unit(self):
        text = MAP.replace("1536K", "1.5M")
        with self.assertRaises(ValueError):
            mkcard.parse_partition_map(text)

    def test_layout_skips_bootloader_and_numbers_from_2048(self):
        entries, end = mkcard.layout(mkcard.parse_partition_map(MAP))
        self.assertEqual([e[0] for e in entries], [1, 2, 3, 4])
        self.assertEqual(entries[0], (1, 2048, 2048 + 1536 * 2 - 1))

    def test_zero_size_partition_gets_one_sector_placeholder(self):
        entries, _ = mkcard.layout(mkcard.parse_partition_map(MAP))
        p2 = next(e for e in entries if e[0] == 2)
        self.assertEqual(p2[2] - p2[1] + 1, 1)

    def test_platform_and_data_images_are_shrunk_to_one_mib(self):
        entries, _ = mkcard.layout(mkcard.parse_partition_map(MAP))
        span = {e[0]: e[2] - e[1] + 1 for e in entries}
        self.assertEqual(span[3], mkcard.PLACEHOLDER_KIB * 1024 // SECTOR)
        self.assertEqual(span[4], mkcard.PLACEHOLDER_KIB * 1024 // SECTOR)

    def test_other_partitions_keep_their_declared_size(self):
        entries, _ = mkcard.layout(mkcard.parse_partition_map(MAP))
        span = {e[0]: e[2] - e[1] + 1 for e in entries}
        self.assertEqual(span[1], 1536 * 1024 // SECTOR)


class MkcardRootImageTests(unittest.TestCase):
    """--root-image: the VDFS image lands at the root partition, unshrunk."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.parts = mkcard.parse_partition_map(MAP)
        cls.image = Path(cls.tmp.name) / "root.img"
        cls.payload = b"VDFS1002" + bytes(range(256)) * 8
        cls.image.write_bytes(cls.payload)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_root_partition_keeps_declared_size_when_image_given(self):
        entries, _ = mkcard.layout(self.parts, keep=(3,))
        span = {e[0]: e[2] - e[1] + 1 for e in entries}
        self.assertEqual(span[3], 300000 * 1024 // SECTOR)
        self.assertEqual(span[4], mkcard.PLACEHOLDER_KIB * 1024 // SECTOR)

    def test_image_bytes_are_written_at_root_partition_start(self):
        path = Path(self.tmp.name) / "card.img"
        mkcard.build(self.parts, path, root_image=self.image, root_index=3)
        entries, _ = mkcard.layout(self.parts, keep=(3,))
        first = next(e[1] for e in entries if e[0] == 3)
        data = path.read_bytes()
        self.assertEqual(data[first * SECTOR:first * SECTOR + len(self.payload)],
                         self.payload)

    def test_oversized_root_image_is_rejected(self):
        path = Path(self.tmp.name) / "small.img"
        big = Path(self.tmp.name) / "big.img"
        big.write_bytes(b"x" * 1024)
        with self.assertRaises(ValueError):
            mkcard.build(self.parts, path, root_image=big, root_index=2)

    def test_root_index_must_exist_in_map(self):
        path = Path(self.tmp.name) / "missing.img"
        with self.assertRaises(ValueError):
            mkcard.build(self.parts, path, root_image=self.image, root_index=22)


class MkcardDataImageTests(unittest.TestCase):
    """--data-image: the VDFS seed lands at the data partition, sized to fit."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.parts = mkcard.parse_partition_map(MAP)
        cls.image = Path(cls.tmp.name) / "data.img"
        cls.payload = b"VDFS1002" + bytes(range(256)) * 6144
        cls.image.write_bytes(cls.payload)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_data_partition_keeps_declared_size_when_seed_given(self):
        path = Path(self.tmp.name) / "card.img"
        mkcard.build(self.parts, path, data_image=self.image, data_index=4)
        card = path.read_bytes()
        span = {e[0]: e[2] - e[1] + 1 for e in self._entries(card)}
        self.assertEqual(span[4], 2048 * 1024 // SECTOR)

    def test_seed_bytes_are_written_at_data_partition_start(self):
        path = Path(self.tmp.name) / "seeded.img"
        mkcard.build(self.parts, path, data_image=self.image, data_index=4)
        card = path.read_bytes()
        first = next(e[1] for e in self._entries(card) if e[0] == 4)
        self.assertEqual(card[first * SECTOR:first * SECTOR + len(self.payload)],
                         self.payload)

    def test_oversized_seed_is_rejected(self):
        path = Path(self.tmp.name) / "over.img"
        big = Path(self.tmp.name) / "big-data.img"
        big.write_bytes(b"x" * (2048 * 1024 + 1))
        with self.assertRaises(ValueError):
            mkcard.build(self.parts, path, data_image=big, data_index=4)

    def test_data_index_must_exist_in_map(self):
        path = Path(self.tmp.name) / "missing-data.img"
        with self.assertRaises(ValueError):
            mkcard.build(self.parts, path, data_image=self.image, data_index=24)

    @staticmethod
    def _entries(card):
        """Return [(index, first_lba, last_lba)] parsed from the primary GPT."""
        lba, count, size = struct.unpack_from("<QII", card, SECTOR + 72)
        table = card[lba * SECTOR:lba * SECTOR + count * size]
        out = []
        for index in range(count):
            entry = table[index * size:(index + 1) * size]
            first, last = struct.unpack_from("<QQ", entry, 32)
            if first:
                out.append((index + 1, first, last))
        return out


class MkcardVdfsTests(unittest.TestCase):
    """VDFS /mnt/drm image: formatted by the firmware mkfs.vdfs and written to p26."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.parts = mkcard.parse_partition_map(MAP)
        cls.image = Path(cls.tmp.name) / "vdfs.img"
        cls.payload = b"VDFS1002" + bytes(range(256)) * 64
        cls.image.write_bytes(cls.payload)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_vdfs_bytes_written_at_partition_start_and_size_kept(self):
        path = Path(self.tmp.name) / "vdfs-card.img"
        mkcard.build(self.parts, path, vdfs_image=self.image, vdfs_index=4)
        card = path.read_bytes()
        entries = {e[0]: e for e in MkcardDataImageTests._entries(card)}
        _, first, last = entries[4]
        self.assertEqual(last - first + 1, 2048 * 1024 // SECTOR)
        self.assertEqual(card[first * SECTOR:first * SECTOR + len(self.payload)],
                         self.payload)

    def test_oversized_vdfs_image_is_rejected(self):
        big = Path(self.tmp.name) / "big-vdfs.img"
        big.write_bytes(b"x" * (2048 * 1024 + 1))
        with self.assertRaises(ValueError):
            mkcard.build(self.parts, Path(self.tmp.name) / "over-vdfs.img",
                         vdfs_image=big, vdfs_index=4)

    def test_format_needs_firmware_mkfs_tool(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                mkcard.format_vdfs(root, 2048, Path(self.tmp.name) / "none.img")

    def test_firmware_mkfs_writes_vdfs_superblock(self):
        firmware = Path(__file__).resolve().parents[2] / (
            "freesung-tools/extract/T-HKMFDEUC/extracted/working/filesystem-6/root")
        if not (firmware / mkcard.MKFS_VDFS).is_file() or shutil.which("qemu-arm") is None:
            self.skipTest("needs the extracted firmware tree and qemu-arm")
        out = Path(self.tmp.name) / "formatted.img"
        mkcard.format_vdfs(firmware, 6144, out)
        self.assertEqual(out.stat().st_size, 6144 * 1024)
        self.assertEqual(out.read_bytes()[:8], b"VDFS1002")


class MkcardGptTests(unittest.TestCase):
    """Build a card and check its GPT with the kernel's validation rules."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "card.img"
        cls.parts = mkcard.parse_partition_map(MAP)
        cls.size = mkcard.build(cls.parts, cls.path)
        cls.data = cls.path.read_bytes()
        cls.entries, cls.end = mkcard.layout(cls.parts)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _header(self, lba):
        raw = self.data[lba * SECTOR:lba * SECTOR + 92]
        fields = struct.unpack("<8sIIIIQQQQ16sQIII", raw)
        zeroed = raw[:16] + b"\0\0\0\0" + raw[20:]
        self.assertEqual(zlib.crc32(zeroed) & 0xffffffff, fields[3],
                         f"header CRC at LBA {lba}")
        return fields

    def _table(self, lba):
        return self.data[lba * SECTOR:lba * SECTOR + 128 * 128]

    def test_file_size_matches_total_sectors(self):
        self.assertEqual(len(self.data), self.size)
        self.assertEqual(self.size % SECTOR, 0)

    def test_protective_mbr_marks_gpt_disk(self):
        self.assertEqual(self.data[450], 0xee)
        self.assertEqual(self.data[510:512], b"\x55\xaa")

    def test_primary_header_fields_are_valid(self):
        total = self.size // SECTOR
        f = self._header(1)
        self.assertEqual(f[0], b"EFI PART")
        self.assertEqual(f[2], 92)
        self.assertEqual(f[5], 1)
        self.assertEqual(f[6], total - 1)
        self.assertEqual(f[7], 34)
        self.assertEqual(f[10], 2)
        self.assertEqual(f[11], 128)
        self.assertEqual(f[12], 128)

    def test_entries_crc_matches_primary_header(self):
        f = self._header(1)
        table = self._table(2)
        self.assertEqual(zlib.crc32(table) & 0xffffffff, f[13])

    def test_backup_header_and_table_mirror_primary(self):
        total = self.size // SECTOR
        primary = self._header(1)
        backup = self._header(total - 1)
        self.assertEqual(backup[5], total - 1)
        self.assertEqual(backup[6], 1)
        self.assertEqual(backup[10], primary[8] + 1)
        self.assertEqual(self._table(primary[8] + 1), self._table(2))
        self.assertEqual(backup[13], primary[13])

    def test_entry_numbering_maps_partition_n_to_entry_n_minus_one(self):
        for index, first, last in self.entries:
            off = (index - 1) * 128
            _, _, got_first, got_last, _ = struct.unpack(
                "<16s16sQQQ", self._table(2)[off:off + 128][:56])
            self.assertEqual((got_first, got_last), (first, last))

    def test_entry_names_follow_partition_index(self):
        off = (2 - 1) * 128
        name = self._table(2)[off + 56:off + 128].decode("utf-16-le")
        self.assertEqual(name.rstrip("\0"), "p2")

    def test_used_entries_pass_pte_checks(self):
        total = self.size // SECTOR
        last_usable = self._header(1)[8]
        for index, first, last in self.entries:
            off = (index - 1) * 128
            type_guid = self._table(2)[off:off + 16]
            self.assertNotEqual(type_guid, b"\0" * 16)
            self.assertLessEqual(first, last)
            self.assertLessEqual(last, last_usable)
            self.assertLess(last, total)

    def test_unused_entries_are_empty(self):
        used = {index - 1 for index, _, _ in self.entries}
        table = self._table(2)
        for slot in range(128):
            if slot not in used:
                self.assertEqual(table[slot * 128:slot * 128 + 16], b"\0" * 16)


if __name__ == "__main__":
    unittest.main()
