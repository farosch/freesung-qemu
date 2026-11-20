import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import zlib
from pathlib import Path

# Board-level regression check for freesung-qemu/hw/arm/sdp1406.c. It starts a paused
# machine with no kernel or DTB, so it needs no firmware or local corpus. The
# QEMU binary comes from FREESUNG_QEMU; the test skips when neither has
# the sdp1406 machine.
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""

# MMIO windows that the board model must map (addresses from sdp1406.c).
EXPECTED_REGIONS = {
    "sdp1406.chipid": 0x00180000,
    "sdp1406.periph": 0x00190000,
    "sdp1406.clkpin": 0x005C0000,
    "sdp1406.extin": 0x00B60000,
    "sdp1406.messagebox": 0x00800000,
    "sdp1406.gmac": 0x00120000,
    "sdp1406.padctrl": 0x00130000,
    "sdp1406.demux_ci": 0x00170000,
    "sdp1406.mmc": 0x00100000,
    "sdp1406.unzip": 0x00300000,
    "sdp1406.mixer": 0x005DC000,
    "sdp1406.dp0": 0x00C00000,
    "sdp1406.dp1": 0x00CB0000,
    "sdp1406.dp2": 0x00D00000,
    "sdp1406.dp3": 0x00D80000,
    "sdp1406.dp4": 0x00DC0000,
    "sdp1406.dp5": 0x00980000,
    "sdp1406.dp6": 0x00B90000,
    "sdp1406.dp7": 0x00B80000,
    "sdp1406.hdmi": 0x00580000,
    "sdp1406.mali": 0x00600000,
}
UART_BASE = 0x00190A00
TTYSAC2_BASE = 0x00190A80
MICOM_BASE = 0x00190AC0
GIC_DIST = 0x00781000
RAM_BANKS = (0x01000000, 0x22600000, 0x22800000, 0x40000000, 0x3C500000)
SCANOUT_BASE = 0x3C500000
SCANOUT_WIDTH = 1920
SCANOUT_HEIGHT = 1080
# Hand-encoded ARM program: GP raise/status/clear, PP soft reset, then b .
MALI_PROG = [
    0xE59F0038, 0xE59F4038, 0xE3A01001, 0xE5801024, 0xE5902030, 0xE5842000,
    0xE5801028, 0xE5902030, 0xE5842004, 0xE59F001C, 0xE3A01080, 0xE580100C,
    0xE59F5014, 0xE5952000, 0xE5842008, 0xEAFFFFFE,
    # 0x00609000 + #0xc is the PP0 control register (0x608000 + 0x100c).
    0x00600000, 0x01010000, 0x00609000, 0x0060902C,
]


def _skip_reason():
    if not QEMU or not Path(QEMU).is_file():
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU build has no sdp1406 machine"
    return None


SKIP = _skip_reason()


class Sdp1406BoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(cls.temp.name) / "monitor.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-S", "-display", "none", "-serial", "null",
             "-monitor", f"unix:{socket_path},server,nowait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(socket_path)
        cls.mtree = _monitor(cls.sock, "info mtree")
        cls.qtree = _monitor(cls.sock, "info qtree")

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def assertMapped(self, base, name=None):
        # info mtree prints 16-digit addresses; anchor to a line start so a
        # short hex base cannot match the tail of a longer address.
        pattern = rf"(?m)^\s+0*{base:x}-"
        self.assertRegex(self.mtree, pattern, f"0x{base:08x} is not mapped")
        if name is not None:
            self.assertIn(name, self.mtree)

    def test_stub_windows_are_mapped_at_dtb_addresses(self):
        for name, base in EXPECTED_REGIONS.items():
            with self.subTest(region=name):
                self.assertMapped(base, name)

    def test_ci_probe_windows_are_backed_by_ram(self):
        # sdp_ci_probe() maps these absolute addresses; unbacked, the write
        # at +1 causes an external abort during boot.
        for name in ("sdp1406.ci-probe-50000", "sdp1406.ci-probe-58000",
                     "sdp1406.ci-probe-90000", "sdp1406.ci-probe-98000"):
            with self.subTest(region=name):
                self.assertRegex(self.mtree, rf"(?m)\bram\): {name}\b")

    def test_uart_and_micom_windows_are_mapped(self):
        for base in (UART_BASE, TTYSAC2_BASE, MICOM_BASE):
            with self.subTest(base=hex(base)):
                self.assertMapped(base)

    def test_gic_distributor_is_present(self):
        self.assertIn("arm_gic", self.qtree.lower())
        self.assertMapped(GIC_DIST)

    def test_ram_banks_are_present(self):
        for base in RAM_BANKS:
            with self.subTest(base=hex(base)):
                self.assertMapped(base)

    def test_ram2_covers_dtb_sdp_mmap_carve_outs(self):
        # DTB samsung,mfc-b (0x47200000) and the carve-outs up to
        # samsung,align1 (top 0x50000000) sit above /memory. The kernel maps
        # them, so an unbacked range gives an external abort.
        match = re.search(
            r"(?m)^\s+([0-9a-f]{16})-([0-9a-f]{16}) \(prio \d+, ram\): "
            r"sdp1406\.ram2\b", self.mtree)
        self.assertIsNotNone(match, "sdp1406.ram2 is not mapped")
        end = int(match.group(2), 16)
        self.assertGreaterEqual(end, 0x4FFFFFFF)


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
    sock.settimeout(2)
    _drain(sock)
    return sock


def _drain(sock):
    data = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    return data.decode(errors="replace")


def _read_until_prompt(sock, timeout=10):
    data = b""
    deadline = time.time() + timeout
    while b"(qemu) " not in data[-16:] and time.time() < deadline:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        data += chunk
    return data.decode(errors="replace")


def _monitor(sock, command):
    sock.sendall(command.encode() + b"\n")
    return _read_until_prompt(sock)


class Sdp1406MaliRegisterTests(unittest.TestCase):
    PROG_BASE = 0x01000000
    RESULT_BASE = 0x01010000
    # GP status after raise, GP status after clear, PP raw status after soft
    # reset. The driver's reset wait only passes on bit 12 (see DEVELOP.md).
    EXPECTED = [0x1, 0x0, 1 << 12]

    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp.name)
        prog = tmp / "mali.bin"
        prog.write_bytes(struct.pack(f"<{len(MALI_PROG)}I", *MALI_PROG))
        socket_path = str(tmp / "monitor.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-S", "-display", "none", "-serial", "null",
             "-monitor", f"unix:{socket_path},server,nowait",
             "-device", f"loader,file={prog},addr=0x{cls.PROG_BASE:x},cpu-num=0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(socket_path)
        cls.sock.settimeout(2)
        _monitor(cls.sock, "cont")
        time.sleep(1)
        cls.result = _monitor(cls.sock, f"xp /3wx 0x{cls.RESULT_BASE:x}")
        # PP0 version register (core base 0x608000 + 0x1000).
        cls.pp_version = _monitor(cls.sock, "xp /1wx 0x609000")

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def test_gp_and_pp_registers_behave_as_driver_expects(self):
        # xp prints "<addr>: 0xV0 0xV1 0xV2" on one line; take the words after the colon.
        line = re.search(rf"(?m)^0*{self.RESULT_BASE:x}:(.*)$", self.result)
        self.assertIsNotNone(line, self.result)
        values = [int(v, 16) for v in re.findall(r"0x([0-9a-fA-F]+)", line.group(1))]
        self.assertEqual(values, self.EXPECTED, self.result)

    def test_pp_product_id_is_mali_400_not_mali_200(self):
        # Driver decodes product ID 0xc807 as Mali-200 (unsupported); 0xcd07 is Mali-400 MP.
        line = re.search(r"(?m)^0*609000:(.*)$", self.pp_version)
        self.assertIsNotNone(line, self.pp_version)
        self.assertEqual(int(line.group(1).split()[0], 16), 0xCD070000, self.pp_version)


class Sdp1406ScanoutTests(unittest.TestCase):
    # Loads a colour-bar frame at the DTB scanout base and checks the QMP
    # screendump of the framebuffer console. Runs the machine unpaused so the
    # display refresh runs, and needs no kernel or firmware.
    BANDS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))

    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp.name)
        cls.frame = tmp / "frame.bin"
        cls.ppm = tmp / "screen.ppm"
        row = bytearray()
        for x in range(SCANOUT_WIDTH):
            r, g, b = cls.BANDS[min(x * 4 // SCANOUT_WIDTH, 3)]
            row += bytes((b, g, r, 0))
        cls.frame.write_bytes(bytes(row) * SCANOUT_HEIGHT)
        socket_path = str(tmp / "monitor.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-monitor", f"unix:{socket_path},server,nowait",
             "-device", f"loader,file={cls.frame},addr=0x{SCANOUT_BASE:x},"
             "force-raw=on"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(socket_path)
        cls.sock.settimeout(2)
        _monitor(cls.sock, "cont")
        time.sleep(1)
        _monitor(cls.sock, f"screendump {cls.ppm}")

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def _read_ppm(self):
        for _ in range(50):
            if self.ppm.exists() and self.ppm.stat().st_size > 0:
                break
            time.sleep(0.1)
        data = self.ppm.read_bytes()
        header, _, pixels = data.partition(b"255\n")
        self.assertTrue(header.startswith(b"P6"))
        self.assertIn(f"{SCANOUT_WIDTH} {SCANOUT_HEIGHT}".encode(), header)
        return pixels

    def test_scanout_frame_is_shown(self):
        pixels = self._read_ppm()
        for band, expected in enumerate(self.BANDS):
            x = band * SCANOUT_WIDTH // 4 + 10
            with self.subTest(band=band):
                offset = (100 * SCANOUT_WIDTH + x) * 3
                self.assertEqual(tuple(pixels[offset:offset + 3]), expected)


class Sdp1406PlaneScanoutTests(unittest.TestCase):
    PLANE_BASE = 0x3978D000
    MIXER_BASE = 0x005DC000
    PLANE_OFFSET = 0x30
    COLOUR = (0, 255, 0)

    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        tmp = Path(cls.temp.name)
        cls.frame = tmp / "plane.bin"
        cls.ppm = tmp / "plane.ppm"
        row = bytearray()
        for x in range(SCANOUT_WIDTH):
            r, g, b = cls.COLOUR
            row += bytes((b, g, r, 0))
        cls.frame.write_bytes(bytes(row) * SCANOUT_HEIGHT)

        monitor_path = str(tmp / "monitor.sock")
        qtest_path = str(tmp / "qtest.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-monitor", f"unix:{monitor_path},server,nowait",
             "-accel", "qtest", "-qtest", f"unix:{qtest_path},server,nowait",
             "-device", f"loader,file={cls.frame},addr=0x{cls.PLANE_BASE:x},"
             "force-raw=on"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.monitor = _connect(monitor_path)
        cls.qtest = _connect(qtest_path)
        cls.qtest.settimeout(5)
        reply = _qtest(cls.qtest,
                       f"writel 0x{cls.MIXER_BASE + cls.PLANE_OFFSET:x}"
                       f" 0x{cls.PLANE_BASE:x}")
        if not reply.startswith("OK"):
            raise unittest.SkipTest(f"plane register write failed: {reply}")
        _monitor(cls.monitor, "cont")
        time.sleep(1)
        _monitor(cls.monitor, f"screendump {cls.ppm}")

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.qtest.close()
        cls.monitor.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def test_plane_register_changes_screendump_source(self):
        for _ in range(50):
            if self.ppm.exists() and self.ppm.stat().st_size > 0:
                break
            time.sleep(0.1)
        data = self.ppm.read_bytes()
        header, _, pixels = data.partition(b"255\n")
        self.assertTrue(header.startswith(b"P6"))
        offset = (100 * SCANOUT_WIDTH + 100) * 3
        self.assertEqual(tuple(pixels[offset:offset + 3]), (0, 255, 0))


class Sdp1406HdmiRegisterTests(unittest.TestCase):
    HDMI_BASE = 0x00580000

    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        cls.socket_path = str(Path(cls.temp.name) / "qtest.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-accel", "qtest", "-qtest", f"unix:{cls.socket_path},server,nowait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(cls.socket_path)

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def _write(self, offset, value):
        reply = _qtest(self.sock,
                       f"writel 0x{self.HDMI_BASE + offset:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{self.HDMI_BASE + offset:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def test_live_observed_registers_read_back_last_write(self):
        writes = {
            0x10000: 0x10000000,
            0x10004: 0x00094008,
            0x30004: 0x00003800,
            0x17100: 0x00000010,
            0x17110: 0x12345678,
        }
        for offset, value in writes.items():
            self._write(offset, value)
        for offset, value in writes.items():
            self.assertEqual(self._read(offset), value)

    def test_subword_writes_merge_register_bytes(self):
        reply = _qtest(self.sock, f"writel 0x{self.HDMI_BASE + 0x10040:x} 0x11223344")
        self.assertEqual(reply, "OK")
        reply = _qtest(self.sock, f"writeb 0x{self.HDMI_BASE + 0x10041:x} 0xaa")
        self.assertEqual(reply, "OK")
        reply = _qtest(self.sock, f"readl 0x{self.HDMI_BASE + 0x10040:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        self.assertEqual(int(reply.split()[1], 16), 0x1122aa44)


class Sdp1406MaliMmuTests(unittest.TestCase):
    MMU_BASE = 0x00603000
    DTE_ADDR = 0x00
    COMMAND = 0x08
    HARD_RESET = 6

    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(cls.temp.name) / "qtest.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-accel", "qtest", "-qtest", f"unix:{socket_path},server,nowait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(socket_path)

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def _write(self, offset, value):
        _qtest(self.sock, f"writel 0x{self.MMU_BASE + offset:x} 0x{value:x}")

    def _read(self, offset):
        reply = _qtest(self.sock, f"readl 0x{self.MMU_BASE + offset:x}")
        return int(reply.split()[-1], 16)

    def test_dte_addr_drops_low_twelve_bits(self):
        # The driver writes a non-page-aligned sentinel and expects 0xCAFEB000 back.
        self._write(self.DTE_ADDR, 0xCAFEBABE)
        self.assertEqual(self._read(self.DTE_ADDR), 0xCAFEB000)

    def test_hard_reset_clears_dte_addr(self):
        self._write(self.DTE_ADDR, 0xCAFEBABE)
        self._write(self.COMMAND, self.HARD_RESET)
        self.assertEqual(self._read(self.DTE_ADDR), 0)


class Sdp1406UnzipRegisterTests(unittest.TestCase):
    UNZIP_BASE = 0x00300000
    IN_ADDR = 0x01001000
    OUT_ADDR = 0x01002000

    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        cls.socket_path = str(Path(cls.temp.name) / "qtest.sock")
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none", "-serial", "null",
             "-accel", "qtest", "-qtest", f"unix:{cls.socket_path},server,nowait"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.sock = _connect(cls.socket_path)

    @classmethod
    def tearDownClass(cls):
        if SKIP:
            return
        cls.sock.close()
        cls.proc.kill()
        cls.proc.wait()
        cls.temp.cleanup()

    def _write(self, address, value):
        reply = _qtest(self.sock, f"writel 0x{address:x} 0x{value:x}")
        self.assertEqual(reply, "OK")

    def _read(self, address):
        reply = _qtest(self.sock, f"readl 0x{address:x}")
        self.assertTrue(reply.startswith("OK 0x"), reply)
        return int(reply.split()[1], 16)

    def _write_bytes(self, address, data):
        for offset in range(0, len(data), 4):
            chunk = data[offset:offset + 4]
            value = int.from_bytes(chunk, "little")
            reply = _qtest(
                self.sock,
                f"writel 0x{address + offset:x} 0x{value:08x}",
            )
            self.assertEqual(reply, "OK")

    def test_zlib_wrapped_czip_chunk_decodes(self):
        plain = b"TIFSDaemon-CZip-Synthetic-Chunk"
        compressed = zlib.compress(plain)
        self._write_bytes(self.IN_ADDR, compressed)
        self._write(self.UNZIP_BASE + 0x0c, self.IN_ADDR)
        self._write(self.UNZIP_BASE + 0x10, len(compressed))
        self._write(self.UNZIP_BASE + 0x18, self.OUT_ADDR)
        self._write(self.UNZIP_BASE + 0x1c, 4096)
        self._write(self.UNZIP_BASE + 0x08, 1)

        status = self._read(self.UNZIP_BASE + 0x00)
        error = self._read(self.UNZIP_BASE + 0x3c)
        isize = self._read(self.UNZIP_BASE + 0x50)
        self.assertEqual((status, error, isize),
                         (1, 0, len(plain)))
        out = b""
        for offset in range(0, len(plain), 4):
            value = self._read(self.OUT_ADDR + offset)
            out += value.to_bytes(4, "little")[:min(4, len(plain) - offset)]
        self.assertEqual(out, plain)


def _qtest(sock, command):
    sock.sendall(command.encode() + b"\n")
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.decode(errors="replace").strip()


if __name__ == "__main__":
    unittest.main()
