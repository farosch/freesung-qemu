import subprocess
import tempfile
import unittest
from pathlib import Path

from test_qemu_sdp1406_mixer import SKIP, QEMU, _connect, _qtest

# Micom UART (sdp1304-uart-micom at 0x00190ac0) is wired to the second -serial
# chardev (serial_hd(1)) in hw/arm/sdp1406.c. UTXH is at offset 0x20.
MICOM_BASE = 0x00190AC0
UTXH = 0x20


class Sdp1406MicomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SKIP:
            raise unittest.SkipTest(SKIP)
        cls.temp = tempfile.TemporaryDirectory()
        socket_path = str(Path(cls.temp.name) / "qtest.sock")
        cls.micom_out = Path(cls.temp.name) / "micom-tx.bin"
        cls.proc = subprocess.Popen(
            [QEMU, "-M", "sdp1406", "-display", "none",
             "-serial", "null", "-serial", f"file:{cls.micom_out}",
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

    def test_micom_tx_reaches_second_serial(self):
        payload = b"\x10\x6a"
        for byte in payload:
            reply = _qtest(self.sock, f"writel 0x{MICOM_BASE + UTXH:x} 0x{byte:x}")
            self.assertEqual(reply, "OK")
        self.assertEqual(self.micom_out.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
