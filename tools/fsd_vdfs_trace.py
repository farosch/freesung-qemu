#!/usr/bin/env python3
"""GDB trace helper for the FSDaemon VDFS write-page invariant and CH:5 I2C ring.

This scratch/diagnostic script is intended for the stock 03-uImage. It releases
SELP, stops at the VDFS page-lock UDF and write call, and prints CH:5 ring
records when available.
"""

from __future__ import annotations

import gdb
import struct

SELP_BREAK_PC = 0xC0014E04
SELP_RESUME_PC = 0xC0014E0C
SELP_PTR_ADDR = 0xC0014EE0
STATUS_OFFSET = 0x18
FSD_UDF_PC = 0xC0182E8C
VDFS_WRITE_PC = 0xC0182E90
CH5_TRACE = 0xC10E0000
FSD_PAGE_LOCKED_FLAG = 1


def reg(name: str) -> int | None:
    try:
        return int(gdb.parse_and_eval("$" + name)) & 0xFFFFFFFF
    except gdb.error:
        return None


class SelpBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__(f"*{SELP_BREAK_PC:#x}", gdb.BP_BREAKPOINT)

    def stop(self) -> bool:
        r2 = reg("r2")
        block = int(gdb.parse_and_eval(
            f"(unsigned int)*(unsigned int*){SELP_PTR_ADDR:#x}"
        ))
        status = block + STATUS_OFFSET
        gdb.execute(f"set *(unsigned int *){status:#x} = {r2}")
        gdb.execute(f"set $pc = {SELP_RESUME_PC:#x}")
        gdb.write(
            f"SELP_RELEASE r2={r2:#010x} status={status:#010x}\n",
            gdb.STDOUT,
        )
        gdb.flush()
        return False


class FsdUdfBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__(f"*{FSD_UDF_PC:#x}", gdb.BP_BREAKPOINT)

    def stop(self) -> bool:
        values = {
            name: reg(name)
            for name in ("r0", "r1", "r4", "r5", "r6", "r7",
                         "r8", "r9", "lr", "sp", "pc")
        }
        page = values.get("r6")
        page_info = None
        if page is not None:
            try:
                inferior = gdb.selected_inferior()
                descriptor = bytes(inferior.read_memory(page, 64))
                flags = struct.unpack_from("<I", descriptor, 0)[0]
                raw_word_1 = struct.unpack_from("<I", descriptor, 4)[0]
                raw_word_2 = struct.unpack_from("<I", descriptor, 8)[0]
                raw_word_3 = struct.unpack_from("<I", descriptor, 12)[0]
                page_info = {
                    "address": page,
                    "flags": flags,
                    "raw_word_1": raw_word_1,
                    "raw_word_2": raw_word_2,
                    "raw_word_3": raw_word_3,
                    "locked": bool(flags & FSD_PAGE_LOCKED_FLAG),
                }
            except gdb.error:
                pass
        records = []
        try:
            inferior = gdb.selected_inferior()
            seq = int.from_bytes(bytes(inferior.read_memory(CH5_TRACE + 4, 4)),
                                 "little")
            for seq_no in range(max(1, seq - 31), seq + 1):
                slot = (seq_no - 1) % 128
                data = bytes(inferior.read_memory(CH5_TRACE + 8 + slot * 24, 24))
                record = struct.unpack("<6I", data)
                if record[1] == 5:
                    records.append(record)
        except gdb.error:
            pass
        gdb.write(
            f"FSD_VDFS_UDF regs={values} page={page_info}\n",
            gdb.STDOUT,
        )
        for record in records[-12:]:
            gdb.write(
                "CH5_RING " + " ".join(f"{x:#010x}" for x in record) + "\n",
                gdb.STDOUT,
            )
        gdb.flush()
        return True


class FsdVdfsWriteBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__(f"*{VDFS_WRITE_PC:#x}", gdb.BP_BREAKPOINT)

    def stop(self) -> bool:
        values = {
            name: reg(name)
            for name in ("r0", "r1", "r2", "r3", "r4", "r5",
                         "r6", "r7", "r8", "r9", "lr", "sp", "pc")
        }
        gdb.write(f"FSD_VDFS_WRITE regs={values}\n", gdb.STDOUT)
        gdb.flush()
        return False


SelpBreakpoint()
FsdUdfBreakpoint()
FsdVdfsWriteBreakpoint()
