#!/usr/bin/env python3
"""Capture the stock tzdev/tzmem SMC export through QEMU's RSP gdbstub."""

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from t32_attach import (  # noqa: E402
    NUM_CORE_REGS,
    RspClient,
    SELP_BREAK_PC,
    SELP_RESUME_PC,
    SELP_PTR_ADDR,
    STATUS_OFFSET,
    le_hex,
    parse_le,
    encode_packet,
)


MONITOR_SMC_HANDLER = 0x00200020
SMC_LOG_BASE = 0x00201000
SMC_LOG_R0 = SMC_LOG_BASE + 0x00
SMC_LOG_R1 = SMC_LOG_BASE + 0x04
SMC_LOG_IP = SMC_LOG_BASE + 0x08
SMC_LOG_COMMIT = SMC_LOG_BASE + 0x0C
SMC_LOG_NPAGES = SMC_LOG_BASE + 0x10
SMC_LOG_PAGES = SMC_LOG_BASE + 0x14

SMC_CMD_WAIT = 14
SMC_CMD_REQ = 7
SMC_CMD_EXPORT = 4096
SMC_IP_EXPORT = 0x0E000005

KERNEL_VA_OFFSET = 0x40000000
TZ_RING_OFF = 0x800
TZ_RX_COUNT_OFF = 0x7FC
TZ_WP_OFF = 0xFFC


def read_regs(client):
    reply = client.request("g")
    if reply.startswith("E"):
        raise RuntimeError("register read failed: %s" % reply)
    regs = [
        parse_le(reply[i * 8:(i + 1) * 8])
        for i in range(NUM_CORE_REGS)
    ]
    return regs


def read_mem(client, address, size=4):
    reply = client.request("m%x,%x" % (address, size))
    if reply.startswith("E"):
        raise RuntimeError("memory read at 0x%08x failed: %s" % (address, reply))
    return parse_le(reply)


def physical_page(address):
    if address >= 0xC0000000:
        return address - KERNEL_VA_OFFSET
    return address


def set_break(client, address):
    reply = client.request("Z0,%x,4" % address)
    if reply != "OK":
        raise RuntimeError("could not set software breakpoint at 0x%08x: %s" %
                           (address, reply))


def clear_break(client, address):
    reply = client.request("z0,%x,4" % address)
    if reply != "OK":
        raise RuntimeError("could not clear software breakpoint at 0x%08x: %s" %
                           (address, reply))


def release_seLP(client, log):
    set_break(client, SELP_BREAK_PC)
    client.sock.sendall(encode_packet("c"))
    if client._recv_byte() != b"+":
        raise RuntimeError("continue was not acked")
    stop = client.wait_stop()
    log("SELP_STOP %s" % stop)
    clear_break(client, SELP_BREAK_PC)

    regs = read_regs(client)
    r2 = regs[2]
    block = read_mem(client, SELP_PTR_ADDR)
    status = block + STATUS_OFFSET
    before = read_mem(client, status)
    if client.request("M%x,4:%s" % (status, le_hex(r2))) != "OK":
        raise RuntimeError("failed to write SELP status word")
    if client.request("Pf=" + le_hex(SELP_RESUME_PC)) != "OK":
        raise RuntimeError("failed to set SELP resume PC")
    log("SELP_RELEASE r2=0x%08x status=0x%08x before=0x%08x resume=0x%08x" %
        (r2, status, before, SELP_RESUME_PC))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    sock = socket.create_connection((args.host, args.port), timeout=args.timeout)
    sock.settimeout(args.timeout)
    client = RspClient(sock)
    log = lambda text: print(text, flush=True)

    stop = client.request("?")
    log("HALTED %s" % stop)

    release_seLP(client, log)

    set_break(client, MONITOR_SMC_HANDLER)
    client.sock.sendall(encode_packet("c"))
    if client._recv_byte() != b"+":
        raise RuntimeError("continue was not acked")
    stop = client.wait_stop()
    log("SMC_STOP %s" % stop)

    regs = read_regs(client)
    r0, r1, r12, pc = regs[0], regs[1], regs[12], regs[15]
    log("SMC_REGS r0=0x%08x r1=0x%08x r12=0x%08x pc=0x%08x" %
        (r0, r1, r12, pc))

    npages = read_mem(client, SMC_LOG_NPAGES)
    pages = []
    for index in range(min(npages, 4)):
        pages.append(read_mem(client, SMC_LOG_PAGES + index * 4))
    log("SMC_LOG npages=%u pages=%s" %
        (npages, " ".join("0x%08x" % page for page in pages)))

    if r1 == SMC_CMD_EXPORT and r12 == SMC_IP_EXPORT:
        page = physical_page(r0)
        ring_type = read_mem(client, page + TZ_RING_OFF)
        ring_id = read_mem(client, page + TZ_RING_OFF + 4)
        ring_len = read_mem(client, page + TZ_RING_OFF + 8)
        rx_count = read_mem(client, page + TZ_RX_COUNT_OFF)
        wp = read_mem(client, page + TZ_WP_OFF)
        log("TZ_EXPORT va=0x%08x pa=0x%08x ring_type=%u ring_id=0x%08x "
            "ring_len=%u rx_count=%u wp=%u" %
            (r0, page, ring_type, ring_id, ring_len, rx_count, wp))
        result = 0
    elif r1 == SMC_CMD_WAIT:
        log("SMC_WAIT_ONLY")
        result = 2
    elif r1 == SMC_CMD_REQ:
        commit = read_mem(client, SMC_LOG_COMMIT)
        log("TZ_REQUEST commit=0x%08x" % commit)
        result = 0
    else:
        log("SMC_OTHER")
        result = 3

    clear_break(client, MONITOR_SMC_HANDLER)
    client.request("D")
    sock.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
