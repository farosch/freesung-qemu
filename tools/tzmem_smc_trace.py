#!/usr/bin/env python3
"""GDB trace helper for the stock tzdev/tzmem SMC export path.

Run this through gdb-multiarch against a stock -S QEMU gdbstub:
  gdb-multiarch -q -batch -ex 'target remote :1234' \
    -x tools/tzmem_smc_trace.py

The script releases the SELP T32 hold and stops at the monitor SMC handler
for ring-page exports and TRM requests. It logs registers and the export page
contents instead of flooding on the tzdaemon wait command.
"""

from __future__ import annotations

import struct

import gdb


SELP_BREAK_PC = 0xC0014E04
SELP_RESUME_PC = 0xC0014E0C
SELP_PTR_ADDR = 0xC0014EE0
STATUS_OFFSET = 0x18

MONITOR_BASE = 0x00200000
MONITOR_SMC_HANDLER = MONITOR_BASE + 0x20
SMC_LOG_BASE = 0x00201000
SMC_LOG_COMMIT = SMC_LOG_BASE + 0x0C

SMC_CMD_WAIT = 14
SMC_CMD_REQ = 7
SMC_CMD_EXPORT = 4096
SMC_IP_EXPORT = 0x0E000005

KERNEL_VA_OFFSET = 0x40000000
TZ_RING_OFF = 0x800
TZ_RX_COUNT_OFF = 0x7FC
TZ_WP_OFF = 0xFFC


def reg(name: str) -> int | None:
    try:
        return int(gdb.parse_and_eval("$" + name)) & 0xFFFFFFFF
    except gdb.error:
        return None


def reg_hex(name: str) -> str:
    value = reg(name)
    return "unknown" if value is None else f"{value:#010x}"


def read_u32(address: int) -> int | None:
    try:
        inferior = gdb.selected_inferior()
        data = bytes(inferior.read_memory(address, 4))
        return struct.unpack("<I", data)[0]
    except gdb.error:
        return None


def physical_export_page(address: int) -> int:
    if address >= 0xC0000000:
        return address - KERNEL_VA_OFFSET
    return address


class SelpBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__(f"*{SELP_BREAK_PC:#x}", gdb.BP_BREAKPOINT)

    def stop(self) -> bool:
        r2 = reg("r2")
        block = read_u32(SELP_PTR_ADDR)
        if r2 is not None and block is not None:
            status = block + STATUS_OFFSET
            gdb.execute(f"set *(unsigned int *){status:#x} = {r2}")
            gdb.execute(f"set $pc = {SELP_RESUME_PC:#x}")
            gdb.write(
                f"SELP_RELEASE r2={r2:#010x} status={status:#010x}\n",
                gdb.STDOUT,
            )
        gdb.flush()
        return False


class SmcBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__(f"*{MONITOR_SMC_HANDLER:#x}", gdb.BP_BREAKPOINT)

    def stop(self) -> bool:
        r0 = reg("r0")
        r1 = reg("r1")
        r12 = reg("r12")
        pc = reg("pc")
        cpsr = reg("cpsr")

        is_export = r1 == SMC_CMD_EXPORT and r12 == SMC_IP_EXPORT
        is_request = r1 == SMC_CMD_REQ
        if r1 == SMC_CMD_WAIT:
            gdb.write(
                "SMC_WAIT "
                f"r0={reg_hex('r0')} r1={reg_hex('r1')} "
                f"r12={reg_hex('r12')} cpsr={reg_hex('cpsr')}\n",
                gdb.STDOUT,
            )
            gdb.flush()
            return False

        gdb.write(
            "SMC "
            f"r0={reg_hex('r0')} r1={reg_hex('r1')} "
            f"r12={reg_hex('r12')} pc={reg_hex('pc')} "
            f"cpsr={reg_hex('cpsr')}\n",
            gdb.STDOUT,
        )

        if is_export and r0 is not None:
            page = physical_export_page(r0)
            ring = read_u32(page + TZ_RING_OFF)
            ring_id = read_u32(page + TZ_RING_OFF + 4)
            ring_len = read_u32(page + TZ_RING_OFF + 8)
            rx_count = read_u32(page + TZ_RX_COUNT_OFF)
            wp = read_u32(page + TZ_WP_OFF)
            gdb.write(
                f"TZ_EXPORT va={r0:#010x} pa={page:#010x} "
                f"ring_type={ring!r} ring_id={ring_id!r} "
                f"ring_len={ring_len!r} rx_count={rx_count!r} wp={wp!r}\n",
                gdb.STDOUT,
            )

        if is_request:
            gdb.write(
                "TZ_REQUEST "
                f"register_commit={read_u32(SMC_LOG_COMMIT)!r}\n",
                gdb.STDOUT,
            )

        gdb.flush()
        if is_export or is_request:
            return True
        return False


SelpBreakpoint()
SmcBreakpoint()
