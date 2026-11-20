import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

# Serial-console regression check. A boot that prints nothing on the UART looks
# identical to a hang, so these tests fail on silence instead of waiting for a
# full boot. The QEMU binary comes from FREESUNG_QEMU (or qemu-system-arm on PATH).
QEMU = os.environ.get("FREESUNG_QEMU") or shutil.which("qemu-system-arm") or ""
UART_DIR = Path(__file__).resolve().parent / "uart"
UART_MARKER = b"SDP1406 UART OK"
LINUX_MARKER = b"SAMSUNG VDLP Kernel"

# Kernel boot needs local firmware artifacts that are not in the repository.
KERNEL = os.environ.get("FREESUNG_KERNEL", "")
DTB = os.environ.get("FREESUNG_DTB", "")
SYSMEM = os.environ.get("FREESUNG_SYSMEM", "")
CARD = os.environ.get("FREESUNG_CARD", "")
CMDLINE = ("root=/dev/mmcblk0p22 init=/sbin/init rootfstype=vdfs ro console=ttyS0,115200N8 "
           "SELP_ENABLE=00000000 rootwait no_console_suspend earlyprintk sdp_sparsemem vmalloc=550M")


def _skip_reason():
    if not QEMU or not os.path.exists(QEMU):
        return "QEMU binary not found (set FREESUNG_QEMU)"
    result = subprocess.run([QEMU, "-M", "help"], capture_output=True, text=True)
    if "sdp1406" not in result.stdout:
        return "QEMU binary lacks the sdp1406 machine"
    return None


def _boot_until(args, marker, timeout):
    """Run QEMU with serial on stdio and return the bytes printed before `marker`
    appears, the timeout expires, or QEMU exits. QEMU is always stopped on return."""
    proc = subprocess.Popen([QEMU] + args, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    done = threading.Event()

    def reader():
        for chunk in iter(lambda: proc.stdout.read1(4096), b""):
            output.extend(chunk)
            if marker in output:
                done.set()
                return
        done.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        done.wait(timeout)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        thread.join(5)
        proc.stdout.close()
    return bytes(output)


class Sdp1406BareMetalSerialTests(unittest.TestCase):
    """The bare-metal UART program must print on the console. It needs no firmware."""

    @classmethod
    def setUpClass(cls):
        reason = _skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        for tool in ("clang", "ld.lld"):
            if not shutil.which(tool):
                raise unittest.SkipTest(f"{tool} not found")
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        src = UART_DIR / "start.S"
        subprocess.run(["clang", "--target=arm-none-eabi", "-march=armv7-a", "-mfpu=none",
                        "-nostdlib", "-c", str(src), "-o", str(tmp / "start.o")], check=True)
        subprocess.run(["ld.lld", "-Ttext=0x80000000", "-e", "_start",
                        str(tmp / "start.o"), "-o", str(tmp / "uart.elf")], check=True)
        cls.elf = str(tmp / "uart.elf")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def test_uart_program_prints_its_banner(self):
        output = _boot_until(["-M", "sdp1406", "-display", "none", "-kernel", self.elf,
                              "-serial", "stdio"], UART_MARKER, timeout=20)
        self.assertIn(UART_MARKER, output, f"no UART banner; got {output[:200]!r}")

    def test_uart_banner_is_not_interleaved(self):
        output = _boot_until(["-M", "sdp1406", "-display", "none", "-kernel", self.elf,
                              "-serial", "stdio"], UART_MARKER, timeout=20)
        self.assertEqual(output.count(UART_MARKER), 1, f"banner garbled: {output[:200]!r}")


class Sdp1406LinuxSerialTests(unittest.TestCase):
    """Kernel boot must produce the version banner on the console. Needs local artifacts:
    FREESUNG_KERNEL (uImage), FREESUNG_DTB, FREESUNG_SYSMEM (sysmem table) and FREESUNG_CARD
    (card image). The test is skipped when any of them is missing."""

    @classmethod
    def setUpClass(cls):
        reason = _skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        missing = [name for name, value in (("FREESUNG_KERNEL", KERNEL), ("FREESUNG_DTB", DTB),
                                            ("FREESUNG_SYSMEM", SYSMEM), ("FREESUNG_CARD", CARD))
                   if not value or not os.path.exists(value)]
        if missing:
            raise unittest.SkipTest("kernel artifacts not set: " + ", ".join(missing))

    def test_kernel_prints_version_banner(self):
        args = ["-M", "sdp1406", "-display", "none", "-serial", "stdio",
                "-kernel", KERNEL, "-dtb", DTB,
                "-device", f"loader,file={SYSMEM},addr=0x80542b94,force-raw=on",
                "-drive", f"if=none,id=sdp1406-mmc,format=raw,snapshot=on,file={CARD}",
                "-append", CMDLINE]
        output = _boot_until(args, LINUX_MARKER, timeout=60)
        self.assertIn(LINUX_MARKER, output, f"kernel printed no banner; got {len(output)} bytes")


if __name__ == "__main__":
    unittest.main()
