#!/usr/bin/env python3
"""Build a blank GPT eMMC card image that mirrors a HawkM partition map.

Linux names GPT entries 1-based, so partition N of partition.txt is GPT entry
N-1 and appears as mmcblk0pN. Partition 0 is the raw BOOTLOADER0 area with no
block node, so it gets no entry. Partitions with a 0K size become one-sector
placeholders to keep the numbering. The platform and data images are shrunk to PLACEHOLDER_KIB; the official-kernel boot
does not read them and this keeps the card inside the 1 GiB the model advertises.

The image carries no firmware unless --root-image is given. That option copies a
VDFS root image into the root partition (PLATFORM0, p22) and keeps that partition
at its declared size. --data-image copies a VDFS seed into CONTENT0 (p24, /opt)
at its declared size, because the seed's superblock describes the whole
partition; init.d/mount.sh then mounts it without its boot-time dd fallback.
Other data sectors are zero-filled and the card file is sparse.

--firmware-root formats p26 (/mnt/drm, VDFS) with the firmware's mkfs.vdfs, run
under qemu-arm, at its declared size. The guest mount of p26 fails and stalls
boot without that superblock, and psa_mount.sh's fallback never runs.
"""

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib
from pathlib import Path

SECTOR = 512
ENTRY_SIZE = 128
ENTRY_COUNT = 128
PLACEHOLDER_KIB = 1024
SHRUNK_NAMES = {"platform.img", "data.img"}
# PLATFORM0; the official kernel boots with root=/dev/mmcblk0p22 (see freesung-qemu/README.md).
ROOT_INDEX = 22
# CONTENT0 (data.img, mounted as /opt by init.d/mount.sh).
DATA_INDEX = 24
# /mnt/drm (p26, VDFS). The guest mount fails and stalls without a VDFS superblock here.
VDFS_INDEX = 26
MKFS_VDFS = "usr/bin/mkfs.vdfs"
DEFAULT_MAP = Path(__file__).resolve().parents[2] / (
    "freesung-tools/extract/T-HKMFDEUC/extracted/working/filesystem-6/root/etc/include/partition.txt")
GPT_TYPE_GUID = uuid.UUID("0FC63DAF-8483-4772-8E79-3D69D8477DE4").bytes_le


def parse_partition_map(text):
    """Return [(index, size_kib, image_name)] for each partition row."""
    parts = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 4 or not fields[0].isdigit():
            continue
        size = fields[2].strip()
        if not size.endswith("K") or not size[:-1].isdigit():
            raise ValueError(f"unexpected size {size!r} for partition {fields[0]}")
        parts.append((int(fields[0]), int(size[:-1]), fields[3].strip()))
    if [p[0] for p in parts] != list(range(len(parts))):
        raise ValueError("partition indices are not contiguous from 0")
    return parts


def layout(parts, keep=()):
    """Return ([(index, first_lba, last_lba)], first_free_lba).

    Partitions listed in keep (indices) take their declared size; other
    platform and data images are shrunk."""
    entries = []
    lba = 2048
    for index, size_kib, image in parts:
        if index == 0:
            continue
        if size_kib == 0:
            sectors = 1
        elif index in keep:
            sectors = size_kib * 1024 // SECTOR
        elif image in SHRUNK_NAMES:
            sectors = PLACEHOLDER_KIB * 1024 // SECTOR
        else:
            sectors = size_kib * 1024 // SECTOR
        entries.append((index, lba, lba + sectors - 1))
        lba += sectors
    return entries, lba


def format_vdfs(firmware_root, size_kib, out):
    """Write an empty read-write VDFS image of size_kib to out.

    The firmware mkfs.vdfs formats an existing file in place, so out is
    created at the partition size first. Run under qemu-arm. Using -i instead
    writes a compact read-only image that is too small for /mnt/drm."""
    tool = Path(firmware_root) / MKFS_VDFS
    if not tool.is_file():
        raise FileNotFoundError(f"{tool} not found")
    if shutil.which("qemu-arm") is None:
        raise FileNotFoundError("qemu-arm is needed to run mkfs.vdfs")
    out = Path(out)
    with out.open("wb") as fh:
        fh.truncate(size_kib * 1024)
    result = subprocess.run(
        ["qemu-arm", "-L", str(firmware_root), str(tool), str(out)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"mkfs.vdfs failed: {(result.stderr or result.stdout).strip()}")
    head = out.read_bytes()[:8]
    if head != b"VDFS1002":
        raise RuntimeError(f"mkfs.vdfs wrote no VDFS1002 superblock: {head!r}")
    return out


def gpt_entry(first, last, name, unique_seed):
    unique = uuid.uuid5(uuid.NAMESPACE_URL, unique_seed).bytes_le
    raw = struct.pack("<16s16sQQQ", GPT_TYPE_GUID, unique, first, last, 0)
    return raw + name.encode("utf-16-le").ljust(72, b"\0")


def build(parts, out, seed="freesung-hawkm", root_image=None, root_index=ROOT_INDEX,
          data_image=None, data_index=DATA_INDEX, vdfs_image=None, vdfs_index=VDFS_INDEX):
    sizes = {index: size_kib for index, size_kib, _ in parts}
    keep = set()
    if root_image is not None:
        if root_index not in sizes:
            raise ValueError(f"partition {root_index} is not in the map")
        image_bytes = Path(root_image).stat().st_size
        if image_bytes == 0 or image_bytes > sizes[root_index] * 1024:
            raise ValueError(
                f"root image is {image_bytes} bytes; partition {root_index} "
                f"holds {sizes[root_index] * 1024}")
        keep.add(root_index)
    if data_image is not None:
        if data_index not in sizes:
            raise ValueError(f"partition {data_index} is not in the map")
        image_bytes = Path(data_image).stat().st_size
        if image_bytes == 0 or image_bytes > sizes[data_index] * 1024:
            raise ValueError(
                f"data image is {image_bytes} bytes; partition {data_index} "
                f"holds {sizes[data_index] * 1024}")
        keep.add(data_index)
    if vdfs_image is not None:
        if vdfs_index not in sizes:
            raise ValueError(f"partition {vdfs_index} is not in the map")
        image_bytes = Path(vdfs_image).stat().st_size
        if image_bytes == 0 or image_bytes > sizes[vdfs_index] * 1024:
            raise ValueError(
                f"vdfs image is {image_bytes} bytes; partition {vdfs_index} "
                f"holds {sizes[vdfs_index] * 1024}")
        keep.add(vdfs_index)
    entries, end = layout(parts, keep)
    last_usable = end + 33
    total = last_usable + 34
    table = bytearray(ENTRY_SIZE * ENTRY_COUNT)
    for index, first, last in entries:
        off = (index - 1) * ENTRY_SIZE
        table[off:off + ENTRY_SIZE] = gpt_entry(first, last, f"p{index}",
                                                f"{seed}-{index}")
    table_crc = zlib.crc32(bytes(table)) & 0xffffffff
    disk_guid = uuid.uuid5(uuid.NAMESPACE_URL, seed).bytes_le

    def header(my_lba, alt_lba, entries_lba):
        body = struct.pack(
            "<8sIIIIQQQQ16sQIII",
            b"EFI PART", 0x00010000, 92, 0, 0,
            my_lba, alt_lba, 34, last_usable, disk_guid,
            entries_lba, ENTRY_COUNT, ENTRY_SIZE, table_crc)
        crc = zlib.crc32(body) & 0xffffffff
        body = body[:16] + struct.pack("<I", crc) + body[20:]
        return body.ljust(SECTOR, b"\0")

    mbr = bytearray(SECTOR)
    mbr[446:462] = (bytes([0, 0, 2, 0, 0xee, 0xff, 0xff, 0xff, 1, 0, 0, 0])
                    + struct.pack("<I", total - 1))
    mbr[510:512] = b"\x55\xaa"

    with open(out, "wb") as fh:
        fh.truncate(total * SECTOR)
        fh.seek(0)
        fh.write(bytes(mbr))
        fh.write(header(1, total - 1, 2))
        fh.seek(2 * SECTOR)
        fh.write(bytes(table))
        fh.seek((last_usable + 1) * SECTOR)
        fh.write(bytes(table))
        fh.seek((total - 1) * SECTOR)
        fh.write(header(total - 1, 1, last_usable + 1))
        if root_image is not None:
            first = next(first for index, first, _ in entries if index == root_index)
            fh.seek(first * SECTOR)
            with open(root_image, "rb") as src:
                shutil.copyfileobj(src, fh, 1024 * 1024)
        if data_image is not None:
            first = next(first for index, first, _ in entries if index == data_index)
            fh.seek(first * SECTOR)
            with open(data_image, "rb") as src:
                shutil.copyfileobj(src, fh, 1024 * 1024)
        if vdfs_image is not None:
            first = next(first for index, first, _ in entries if index == vdfs_index)
            fh.seek(first * SECTOR)
            with open(vdfs_image, "rb") as src:
                shutil.copyfileobj(src, fh, 1024 * 1024)
    return total * SECTOR


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path, help="card image to write")
    ap.add_argument("--map", type=Path, default=DEFAULT_MAP,
                    help="HawkM partition.txt (default: extracted copy)")
    ap.add_argument("--root-image", type=Path, default=None,
                    help="VDFS root image written to partition 22 (platform.img)")
    ap.add_argument("--data-image", type=Path, default=None,
                    help="VDFS seed image written to partition 24 (usr/data_backup.img)")
    ap.add_argument("--firmware-root", type=Path, default=None,
                    help="extracted firmware root; formats partition 26 (/mnt/drm) "
                         "with its usr/bin/mkfs.vdfs")
    args = ap.parse_args(argv)
    parts = parse_partition_map(args.map.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        vdfs_image = None
        if args.firmware_root is not None:
            size_kib = dict((i, k) for i, k, _ in parts)[VDFS_INDEX]
            vdfs_image = format_vdfs(args.firmware_root, size_kib, Path(tmp) / "p26.img")
        size = build(parts, args.out, root_image=args.root_image,
                     data_image=args.data_image, vdfs_image=vdfs_image)
    print(f"wrote {args.out} ({size} bytes, {len(parts)} partitions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
