#!/usr/bin/env python3
"""Attach a debugger-style probe to a QEMU sdp1406 guest and release the SELP wait.

This is a GDB Remote Serial Protocol client for QEMU's built-in gdbstub
(started with ``-gdb tcp::PORT -S``). It stands in for a Lauterbach TRACE32
attach: it halts the guest at reset, plants a hardware breakpoint on the SELP
``please attach T32`` spin, writes the handshake value into the status word
the wait loop polls, moves PC past the spin, and detaches.

It is NOT the TRACE32 protocol. Lauterbach's wire protocol is proprietary.
"""

import argparse
import socket
import struct
import sys

# Addresses in the production 03-uImage (the probe kernel differs only at file offset 0x4C7350, outside these).
SELP_BREAK_PC = 0xC0014E04  # "b ." spin after the "please attach T32" printk
SELP_RESUME_PC = 0xC0014E0C  # cleanup/fallthrough after the WFE spin
SELP_PTR_ADDR = 0xC0014EE0  # literal pool: kernel VA of the status block
STATUS_OFFSET = 0x18  # status word inside the block

NUM_CORE_REGS = 16  # r0..r15 (r15 = PC)


def checksum(data: bytes) -> bytes:
    if isinstance(data, str):
        data = data.encode("ascii")
    return b"%02x" % (sum(data) & 0xFF)


def encode_packet(payload: str) -> bytes:
    body = payload.encode("ascii")
    return b"$" + body + b"#" + checksum(body)


def le_hex(value: int, size: int = 4) -> str:
    return value.to_bytes(size, "little").hex()


def parse_le(hexstr: str) -> int:
    return int.from_bytes(bytes.fromhex(hexstr), "little")


def parse_stop_thread(reply: str) -> str:
    """Return the thread id from a stop reply such as ``T05thread:01;``."""
    marker = "thread:"
    idx = reply.find(marker)
    if idx < 0:
        return "1"
    return reply[idx + len(marker):].split(";")[0]


class RspClient:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""

    def _recv_byte(self) -> bytes:
        while not self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("gdbstub closed the connection")
            self.buf += chunk
        b, self.buf = self.buf[:1], self.buf[1:]
        return b

    def _read_packet(self) -> str:
        while True:
            c = self._recv_byte()
            if c == b"$":
                break
        data = bytearray()
        while True:
            c = self._recv_byte()
            if c == b"#":
                break
            data += c
        self._recv_byte()
        self._recv_byte()
        return data.decode("ascii")

    def request(self, payload: str) -> str:
        """Send a packet, wait for its ack and its reply."""
        self.sock.sendall(encode_packet(payload))
        if self._recv_byte() != b"+":
            raise RuntimeError("gdbstub did not ack packet %r" % payload)
        return self._next_reply()

    def wait_stop(self) -> str:
        return self._next_reply()

    def _next_reply(self) -> str:
        """Return the next non-console reply, acking each packet.

        ``O<hex>`` packets are asynchronous console output and are skipped.
        Plain ``OK`` is a real reply (hex-encoded output can never equal it).
        """
        while True:
            reply = self._read_packet()
            self.sock.sendall(b"+")
            if reply.startswith("O") and reply != "OK":
                continue
            return reply


def read_mem(client: RspClient, addr: int, length: int) -> int:
    reply = client.request("m%x,%x" % (addr, length))
    if reply.startswith("E"):
        raise RuntimeError("memory read at 0x%08x failed: %s" % (addr, reply))
    return parse_le(reply)


def attach_and_release(client: RspClient, log=print, max_releases=1) -> dict:
    """Halt the guest, hit the SELP exit point, and write r2 and PC.

    Returns a dict with the values read and written.
    """
    # QEMU's gdbstub answers '?' only when the guest is halted (e.g. started
    # with -S). A running guest does not answer Ctrl-C in our tests, so a
    # running guest cannot be attached to this way.
    stop = client.request("?")
    if not stop[:1] in ("T", "S"):
        raise RuntimeError("guest is not halted (stop reply %r); start QEMU with -S" % stop)
    log("halted: %s" % stop)
    thread = parse_stop_thread(stop)
    client.request("Hg%s" % thread)

    # The QEMU gdbstub has no hardware-breakpoint support. A software
    # breakpoint temporarily replaces the instruction and works with the
    # kernel virtual PC before/after MMU setup.
    if client.request("Z0,%x,4" % SELP_BREAK_PC) != "OK":
        raise RuntimeError("could not set hardware breakpoint")
    last = None
    for release in range(1, max_releases + 1):
        client.sock.sendall(encode_packet("c"))
        if client._recv_byte() != b"+":
            raise RuntimeError("continue was not acked")
        hit = client.wait_stop()
        log("breakpoint %d: %s" % (release, hit))
        client.request("z0,%x,4" % SELP_BREAK_PC)

        regs_hex = client.request("g")
        if regs_hex.startswith("E"):
            raise RuntimeError("register read failed: %s" % regs_hex)
        regs = [parse_le(regs_hex[i * 8:(i + 1) * 8]) for i in range(NUM_CORE_REGS)]
        log("r2 before = 0x%08x, pc = 0x%08x" % (regs[2], regs[15]))

        block = read_mem(client, SELP_PTR_ADDR, 4)
        status_addr = block + STATUS_OFFSET
        status = read_mem(client, status_addr, 4)
        log("status block = 0x%08x, status word = 0x%08x" % (block, status))

        if client.request("M%x,4:%s" % (status_addr, le_hex(regs[2]))) != "OK":
            raise RuntimeError("failed to write status word")
        if client.request("Pf=" + le_hex(SELP_RESUME_PC)) != "OK":
            raise RuntimeError("failed to write PC")
        log("status word := 0x%08x, pc := 0x%08x" % (regs[2], SELP_RESUME_PC))
        last = {
            "block": block,
            "status_before": status,
            "handshake": regs[2],
            "resume_pc": SELP_RESUME_PC,
        }
        if release < max_releases:
            if client.request("Z0,%x,4" % SELP_BREAK_PC) != "OK":
                raise RuntimeError("could not reset software breakpoint")
    if client.request("D") != "OK":
        raise RuntimeError("detach failed")
    return last


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="seconds to wait for the SELP breakpoint to hit")
    parser.add_argument("--repeat", type=int, default=1,
                        help="number of repeated SELP breakpoints to release")
    args = parser.parse_args(argv)

    sock = socket.create_connection((args.host, args.port), timeout=args.timeout)
    try:
        attach_and_release(
            RspClient(sock),
            log=lambda m: print(m, flush=True),
            max_releases=args.repeat,
        )
    except (OSError, RuntimeError, ConnectionError) as exc:
        print("t32_attach: %s" % exc, file=sys.stderr)
        return 1
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
