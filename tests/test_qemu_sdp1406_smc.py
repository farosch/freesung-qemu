import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import importlib.util
from pathlib import Path

# Qtest check for the SMC trap device in freesung-qemu/hw/arm/sdp1406.c
# (sdp1406_add_smc_log). The guest monitor stores r0, r1, ip and a commit word at
# 0x00201000..0x0020100c. The kernel exports its 4 KB ring page with ip = 0x0e000005
# and r1 = 4096; the device records it at 0x00201014 onwards (NPAGES is at
# 0x00201010) and translates kernel VAs (>= 0xc0000000) to PA. Requests (r1 = 7)
# are answered in the exported ring pages. The QEMU binary comes from
# FREESUNG_QEMU; the test skips when neither has the sdp1406 machine.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

SMC_BASE = 0x00201000
MONITOR_BASE = 0x00200000
MONITOR_SMC_VECTOR = 0x08
MONITOR_SMC_HANDLER = 0x20
R0 = 0x00
R1 = 0x04
IP = 0x08
COMMIT = 0x0C
NPAGES = 0x10
PAGES = 0x14
CMD_EXPORT = 4096
CMD_TMEM_EXPORT = 65536
CMD_WATCH = 0
CMD_WAIT = 14
CMD_REQ = 7
IP_EXPORT = 0x0E000005
IP_WATCH = 0x0E000000
PAGE_A = 0x83825000
PAGE_B = 0x838A4000
KERNEL_VA_OFF = 0x40000000
RING_OFF = 0x800
WP_OFF = 0xffc
RX_COUNT_OFF = 0x7fc
REC_HDR = 12
REC_DATA = 2
REC_ERR = 1
CHANNEL = 0x1001
TRM_INITIALIZE_CONTEXT = 2
TRM_OPEN_SESSION = 6
TRM_UNKNOWN = 12
TRM_SIZE = 104  # record body the firmware sends for one struct trm_message

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "tzmem_smc_rsp",
    ROOT / "tools" / "tzmem_smc_rsp.py",
)
tzmem_smc_rsp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tzmem_smc_rsp)


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


def _connect(path):
    deadline = time.time() + 10
    while True:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
            break
        except OSError:
            sock.close()
            if time.time() > deadline:
                raise
            time.sleep(0.05)
    sock.settimeout(5)
    return sock


def _qtest(sock, command):
    sock.sendall(command.encode() + b"\n")
    data = b""
    while True:
        if data.endswith(b"\n"):
            lines = data.decode(errors="replace").splitlines()
            if lines[-1].startswith(("OK", "ERR")):
                break
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode(errors="replace").strip()


def _trm(command, size=TRM_SIZE):
    return struct.pack("<I", command).ljust(size, b"\0")


def _record(preamble, channel, body):
    return struct.pack("<III", preamble, channel, len(body)) + body


class Sdp1406SmcTests(unittest.TestCase):
    def setUp(self):
        if SKIP:
            self.skipTest(SKIP)
        self.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(self.temp.name) / "qtest.sock")
        self.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-accel", "qtest", "-qtest", f"unix:{socket_path},server,nowait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.sock = _connect(socket_path)

    def tearDown(self):
        if SKIP:
            return
        self.sock.close()
        self.proc.kill()
        self.proc.wait()
        self.temp.cleanup()

    def _smc(self, r0, r1, ip):
        for offset, value in ((R0, r0), (R1, r1), (IP, ip), (COMMIT, 1)):
            reply = _qtest(self.sock, f"writel 0x{SMC_BASE + offset:x} 0x{value:x}")
            self.assertEqual(reply, "OK")

    def _read(self, offset):
        return self._peek(SMC_BASE + offset)

    def _peek(self, address):
        reply = _qtest(self.sock, f"readl 0x{address:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _guest_write(self, address, data):
        data += b"\0" * (-len(data) % 4)
        for offset in range(0, len(data), 4):
            value = int.from_bytes(data[offset:offset + 4], "little")
            reply = _qtest(self.sock, f"writel 0x{address + offset:x} 0x{value:x}")
            self.assertEqual(reply, "OK")

    def _guest_bytes(self, address, size):
        data = b"".join(struct.pack("<I", self._peek(address + offset))
                        for offset in range(0, size, 4))
        return data[:size]

    def _register_page(self, page):
        self._smc(page, CMD_EXPORT, IP_EXPORT)

    def _submit(self, page, records):
        blob = b"".join(records)
        self._guest_write(page + RING_OFF, blob)
        self._guest_write(page + WP_OFF, struct.pack("<I", len(blob)))
        self._smc(0, CMD_REQ, IP_EXPORT + 1)

    def test_monitor_smc_vector_and_handler_are_live(self):
        smc_vector = self._peek(MONITOR_BASE + MONITOR_SMC_VECTOR)
        self.assertEqual(smc_vector, 0xEA000004)
        handler = []
        for offset in range(MONITOR_SMC_HANDLER,
                            MONITOR_SMC_HANDLER + 24,
                            4):
            handler.append(self._peek(MONITOR_BASE + offset))
        self.assertEqual(handler, [
            0xE58F0FD8,
            0xE58F1FD8,
            0xE58FCFD8,
            0xE58F0FD8,
            0xE3A00000,
            0xE1B0F00E,
        ])

    def test_ring_export_is_recorded(self):
        self._register_page(PAGE_A)
        self.assertEqual(self._read(NPAGES), 1)
        self.assertEqual(self._read(PAGES), PAGE_A)

    def test_kernel_va_export_is_translated_to_physical(self):
        self._register_page(PAGE_A + KERNEL_VA_OFF)
        self.assertEqual(self._read(PAGES), PAGE_A)

    def test_watch_page_registration_is_not_a_ring(self):
        self._smc(PAGE_A, CMD_WATCH, IP_WATCH)
        self.assertEqual(self._read(NPAGES), 0)

    def test_export_with_other_ip_is_not_recorded(self):
        self._smc(PAGE_A, CMD_EXPORT, IP_EXPORT + 4)
        self.assertEqual(self._read(NPAGES), 0)

    def test_large_export_is_not_a_ring(self):
        self._smc(PAGE_A, CMD_TMEM_EXPORT, IP_EXPORT)
        self.assertEqual(self._read(NPAGES), 0)

    def test_wait_command_is_not_recorded(self):
        self._smc(PAGE_A, CMD_WAIT, IP_EXPORT)
        self.assertEqual(self._read(NPAGES), 0)

    def test_duplicate_export_is_recorded_once(self):
        self._register_page(PAGE_A)
        self._register_page(PAGE_A)
        self.assertEqual(self._read(NPAGES), 1)

    def test_registry_keeps_first_four_pages(self):
        for page in (PAGE_A, PAGE_B, 0x83D3A000, 0x838C0000, 0x83EB2000):
            self._register_page(page)
        self.assertEqual(self._read(NPAGES), 4)
        self.assertEqual(self._read(PAGES + 12), 0x838C0000)

    def test_request_leaves_registry_unchanged(self):
        self._register_page(PAGE_A)
        self._smc(0, CMD_REQ, IP_EXPORT + 1)
        self.assertEqual(self._read(NPAGES), 1)
        self.assertEqual(self._read(PAGES), PAGE_A)


    def test_initialize_context_replies_with_context_id(self):
        self._register_page(PAGE_A)
        self._submit(PAGE_A, [_record(REC_DATA, CHANNEL,
                                      _trm(TRM_INITIALIZE_CONTEXT))])
        self.assertEqual(self._peek(PAGE_A + RX_COUNT_OFF), REC_HDR + TRM_SIZE)
        reply = self._guest_bytes(PAGE_A, REC_HDR + TRM_SIZE)
        self.assertEqual(struct.unpack_from("<III", reply, 0),
                         (REC_DATA, CHANNEL, TRM_SIZE))
        self.assertEqual(struct.unpack_from("<I", reply, REC_HDR + 4)[0], 0)
        self.assertEqual(struct.unpack_from("<I", reply, REC_HDR + 16)[0], 1)

    def test_open_session_assigns_sequential_ids(self):
        self._register_page(PAGE_A)
        record = _record(REC_DATA, CHANNEL, _trm(TRM_OPEN_SESSION))
        self._submit(PAGE_A, [record, record])
        reply_size = REC_HDR + TRM_SIZE
        reply = self._guest_bytes(PAGE_A, 2 * reply_size)
        ids = [struct.unpack_from("<I", reply, base + REC_HDR + 16)[0]
               for base in (0, reply_size)]
        self.assertEqual(ids, [1, 2])

    def test_malformed_bodies_get_error_records(self):
        self._register_page(PAGE_A)
        self._submit(PAGE_A, [
            _record(REC_DATA, CHANNEL, _trm(TRM_UNKNOWN)),
            _record(REC_DATA, CHANNEL, _trm(TRM_OPEN_SESSION, size=16)),
        ])
        self.assertEqual(self._peek(PAGE_A + RX_COUNT_OFF), 2 * REC_HDR)
        reply = self._guest_bytes(PAGE_A, 2 * REC_HDR)
        for base in (0, REC_HDR):
            self.assertEqual(struct.unpack_from("<III", reply, base),
                             (REC_ERR, CHANNEL, 0))

    def test_oversized_body_gets_error_record(self):
        self._register_page(PAGE_A)
        self._submit(PAGE_A, [_record(REC_DATA, CHANNEL, _trm(TRM_UNKNOWN, size=109))])
        self.assertEqual(self._peek(PAGE_A + RX_COUNT_OFF), REC_HDR)
        reply = self._guest_bytes(PAGE_A, REC_HDR)
        self.assertEqual(struct.unpack_from("<III", reply, 0),
                         (REC_ERR, CHANNEL, 0))

    def test_truncated_record_is_served_once_complete(self):
        self._register_page(PAGE_A)
        record = _record(REC_DATA, CHANNEL, _trm(TRM_INITIALIZE_CONTEXT))
        self._submit(PAGE_A, [record[:20]])
        self.assertEqual(self._peek(PAGE_A + RX_COUNT_OFF), 0)
        self._submit(PAGE_A, [record])
        self.assertEqual(self._peek(PAGE_A + RX_COUNT_OFF), REC_HDR + TRM_SIZE)

    def test_served_request_is_not_answered_twice(self):
        self._register_page(PAGE_A)
        self._submit(PAGE_A, [_record(REC_DATA, CHANNEL,
                                      _trm(TRM_INITIALIZE_CONTEXT))])
        self._guest_write(PAGE_A + RX_COUNT_OFF, b"\0\0\0\0")
        self._smc(0, CMD_REQ, IP_EXPORT + 1)
        self.assertEqual(self._peek(PAGE_A + RX_COUNT_OFF), 0)

    def test_consumed_request_ring_is_emptied_for_next_request(self):
        self._register_page(PAGE_A)
        self._submit(PAGE_A, [_record(REC_DATA, CHANNEL,
                                      _trm(TRM_INITIALIZE_CONTEXT))])
        self.assertEqual(self._peek(PAGE_A + WP_OFF), 0)
        self._guest_write(PAGE_A + RX_COUNT_OFF, b"\0\0\0\0")
        self._submit(PAGE_A, [_record(REC_DATA, CHANNEL,
                                      _trm(TRM_OPEN_SESSION))])
        self.assertEqual(self._peek(PAGE_A + RX_COUNT_OFF), REC_HDR + TRM_SIZE)

    def test_export_page_is_kernel_va_aligned_and_4kb_sized(self):
        page_va = PAGE_A + KERNEL_VA_OFF
        self._smc(page_va, CMD_EXPORT, IP_EXPORT)
        self.assertEqual(self._read(NPAGES), 1)
        self.assertEqual(self._read(PAGES), PAGE_A)
        self.assertEqual(PAGE_A & 0xFFF, 0)
        self.assertEqual(CMD_EXPORT, 4096)


class Sdp1406SmcTraceHelperTests(unittest.TestCase):
    def test_live_trace_pins_monitor_and_ring_addresses(self):
        self.assertEqual(tzmem_smc_rsp.MONITOR_SMC_HANDLER, 0x00200020)
        self.assertEqual(tzmem_smc_rsp.SMC_LOG_BASE, 0x00201000)
        self.assertEqual(tzmem_smc_rsp.SMC_LOG_NPAGES, 0x00201010)
        self.assertEqual(tzmem_smc_rsp.TZ_RING_OFF, 0x800)
        self.assertEqual(tzmem_smc_rsp.TZ_RX_COUNT_OFF, 0x7FC)
        self.assertEqual(tzmem_smc_rsp.TZ_WP_OFF, 0xFFC)

    def test_linear_map_export_va_is_translated_to_physical(self):
        self.assertEqual(tzmem_smc_rsp.physical_page(PAGE_A + KERNEL_VA_OFF),
                         PAGE_A)
        self.assertEqual(tzmem_smc_rsp.physical_page(PAGE_A), PAGE_A)


if __name__ == "__main__":
    unittest.main()
