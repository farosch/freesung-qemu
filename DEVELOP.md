# freesung-qemu Development

QEMU board model `sdp1406` (HawkM) and the Samsung TV card boot. Commands run from
the repository root. Project rules are in [AGENTS.md](../AGENTS.md). Boot and build
steps are in [README.md](README.md). Open leads are in [TODO.md](TODO.md).

Nothing here is verified on hardware.

## Rules

- Use the production kernel `hawkm-msdu10-payloads/03-uImage`. The probe kernel is removed.
- Every behaviour change needs a test. Update this file when code, tests, or build steps change.
- Verify no QEMU process remains after a run: `pgrep -x qemu-system-arm`.

## Build

Upstream QEMU 10.2.1 with the board file, the Bluetooth USB device, and four patches:

```sh
sudo apt install meson ninja-build libfdt-dev libpixman-1-dev libsdl2-dev
curl -LO https://download.qemu.org/qemu-10.2.1.tar.xz
tar -xJf qemu-10.2.1.tar.xz
cd qemu-10.2.1
patch -p1 < /path/to/freesung-qemu/patches/0001-hw-arm-wire-sdp1406-board.patch
patch -p1 < /path/to/freesung-qemu/patches/0002-hw-char-exynos4210-raise-tx-irq-only-on-write.patch
patch -p1 < /path/to/freesung-qemu/patches/0003-hw-char-exynos4210-ack-rx-tx-events-via-utrstat.patch
patch -p1 < /path/to/freesung-qemu/patches/0004-hw-usb-add-bcm-btusb-meson.patch
patch -p1 < /path/to/freesung-qemu/patches/0005-hw-usb-trace-bcm-btusb-hci.patch
cp /path/to/freesung-qemu/hw/arm/sdp1406.c hw/arm/
cp /path/to/freesung-qemu/hw/usb/dev-bcm-btusb.c hw/usb/
mkdir build && cd build
../configure --target-list=arm-softmmu --without-default-features \
    --disable-docs --disable-werror --disable-tools --disable-guest-agent \
    --disable-download --disable-tcg-interpreter --enable-fdt=system \
    --enable-sdl --enable-pixman
ninja qemu-system-arm
```

- Patch `0002` stops the Exynos UART from re-raising TX pending on each update.
- Patch `0003` adds the `UTRSTAT` TX/RX ack path. Without it IRQ 88 storms.
- Patch `0004` adds `dev-bcm-btusb.c` to `hw/usb/meson.build`. The device is copied, not patched,
  because `usb_desc_*` is private to `hw/usb/`.
- Patch `0005` adds the `bcm_btusb_hci_cmd` trace event (`hw/usb/trace-events`). The guest boot
  test uses it with `-trace bcm_btusb_hci_cmd`.
- `--enable-pixman` is required for the monitor's `screendump`.

## Tests

Board suite (196 cases in 13 files, about 80 s; live kernel and USB cases are skipped without artifact variables):

```sh
FREESUNG_QEMU=/path/to/qemu-system-arm python3 -m unittest discover -s tests -p 'test_qemu_sdp1406_*.py'
```

Single file (replace the name):

```sh
FREESUNG_QEMU=/path/to/qemu-system-arm python3 -m unittest discover -s tests -p test_qemu_sdp1406_gmac.py -v
```

| File | Covers |
|------|--------|
| `test_qemu_sdp1406_board.py` | MMIO map, GIC, RAM banks, Mali GP/PP reset, Mali MMU DTE_ADDR, fixed scanout frame, OSD live plane address `0x37b84000` |
| `test_qemu_sdp1406_mixer.py` | Mixer register file |
| `test_qemu_sdp1406_mmc.py` | eMMC registers and card writes (about 1 s; each test kills its QEMU on close) |
| `test_qemu_sdp1406_mbox.py`, `test_qemu_sdp1406_mailbox.py` | Messagebox replies and ACK tags |
| `test_qemu_sdp1406_gmac.py` | MII/PHY and DMA TX/RX with a UDP peer |
| `test_qemu_sdp1406_i2c.py` | sdp-i2c CH:4/5/7/8/10 registers, IRQ mask semantics, SPI 44/45/46/48/50, observed CH:5 panel addresses `0xc4/0xce`, CH:4 EEPROM slave, completion deferred until the driver acks its unmasked IRQ bit (masked bits are not deferred); CH:7 completion log with mask state (`Sdp1406I2cCompletionLogTests`, runs under `-d unimp`) |
| `test_qemu_sdp1406_usb.py` | EHCI/OHCI capability registers; `usb-bcm-btusb` attached on EHCI slot 0 at 480 Mb/s (`info usb` over QMP); guest HCI bring-up order from the `bcm_btusb_hci_cmd` trace (`Sdp1406BtGuestBringUpTests`, needs the kernel artifacts, about 20 s) |
| `test_qemu_sdp1406_mkcard.py` | Offline GPT/partition checks for `mmc/mkcard.py` |
| `test_qemu_sdp1406_stubs.py` | Log-only stub windows: reads return 0, writes not kept, accesses logged under `-d unimp` |
| `test_qemu_sdp1406_serial.py` | UART banner from `tests/uart/start.S` (bare metal); kernel version banner on the console |

The serial tests fail on silence instead of waiting for a full boot. The Linux banner test
(`Sdp1406LinuxSerialTests`) runs only when `FREESUNG_KERNEL`, `FREESUNG_DTB`, `FREESUNG_SYSMEM`
and `FREESUNG_CARD` point at local artifacts, and it is skipped otherwise. It uses a 60 s cap.
It matches the Samsung `SAMSUNG VDLP Kernel` banner rather than the upstream `Linux version`
banner because the production uImage does not print the standard upstream line.

Tests skip when `FREESUNG_QEMU` (or `qemu-system-arm` on PATH) lacks the `sdp1406` machine.
The i2c tests (`test_qemu_sdp1406_i2c.py`) need a build that includes the sdp-i2c model. Build
`qemu-system-arm` from the same tree as `hw/arm/sdp1406.c` in this repo, then run them against
that binary. Against a stale build the IRQ and EEPROM tests fail. The i2c tests share one QEMU per
class, so the qtest helper reads each reply until its final `OK`/`ERR` line.
`test_qemu_sdp1406_board.py::test_scanout_frame_is_shown` checks the scanout path.

## Machine model (`hw/arm/sdp1406.c`)

### Memory

- RAM0 starts at `0x01000000` and is `SDP1406_RAM0_SIZE` (496 MiB). `-m` is ignored.
- The DTB `/memory` node lists the first 112 MiB of RAM2. The model adds a bank at `0x80000000`
  (`0x10000000`). Without it `free_initmem()` oopses.
- RAM2 `0x40000000`-`0x4fffffff` covers the DTB `sdp_mmap` carve-outs above `/memory`
  (`samsung,mfc-b` at `0x47200000`, `hen-b`, `dp1`, `align1`). Without it the MFC firmware
  load faults. Test: `test_ram2_covers_dtb_sdp_mmap_carve_outs`.
- `sdp1406.sdp-mmap` maps `0x22600000`-`0x227fffff` as RAM (`SDP1406_SDP_MMAP_BASE`). This
  covers `samsung,cursor`, `dp-ttx-vbi`, and `align2`, which are not in `/memory`.
- Scanout: `0x3c500000` (DTB `samsung,dp0`), XRGB8888, 1920x1080 (from the FHD DTS
  `timing-0`).
- The DTB and any initrd are reserved with `/memreserve` by `sdp1406_load_dtb_with_initrd`.
- The DTB `/chosen/bootargs` overrides `-append`. Change kernel options in a scratch DTB copy.
  Edits must keep the same length, because `init=` and `rootfstype=` are in fixed-size fields.
  Put `init=` last: the kernel passes every token after `init=` to init as argv, so
  `init=/bin/sh ... earlyprintk` runs `earlyprintk` as a script and exits 127 (kernel panic).
  Make the scratch DTB with `dtc -I dtb -O dts` (decompile), edit the text, then `dtc -I dts -O dtb`.

### Stubs and devices

Confidence labels used below: a claim is "disassembly" only when the driver code was read and
the register use was checked in the binary. "DTB" and "fault" mean the address came from the device
tree or a boot fault; the register meaning is then open (see TODO.md item 17).

- `sdp1406.unzip` (`0x300000`, IRQ 34): DMA gzip/DEFLATE engine. Used to decompress the VDFS root.
- `sdp1406.ga` (`0x660000`, SPI 104): DRM GA. Start sets status bit 0; IRQ asserts on `status & mask`.
- `sdp1406.uddec` (`0xb00000`), `sdp1406.demux` (`0x900000`), `sdp1406.demux2` (`0x930000`):
  register stubs. Added after external aborts (fault). Not otherwise modelled. Register meaning open (TODO 17).
- `sdp1406.demux_ci` (`0x170000`, one page): DTB `demux@00900000` `ci` range (`0x134` bytes)
  mapped by the CI driver. The page-sized stub covers its last-byte read.
- CI probe windows (`sdp_tsd.ko` `sdp_ci_probe`, `0xd3fc`; not yet confirmed by disassembly here): `0x50000` (4 bytes),
  `0x58000` (`0x8000`), `0x90000` (4 bytes), `0x98000` (`0x8000`). They are plain RAM
  (`sdp1406.ci-probe-*`), not register models. The probe writes `0xc0` at `0x90001` and an
  unbacked write there caused the `external abort` and the SELP hold (TODO item 3). The real
  register semantics are unknown. RAM lets the driver proceed. Test:
  `test_ci_probe_windows_are_backed_by_ram`.
- Media DSP status (`0xa00000`): reads at `0xa04010` and `0xa08010` return 1. The
  `devAe_WatchDog` value it feeds is not identified, and the meaning is not confirmed (TODO 17).
- Generic windows with no device model: `0x5d0000`, `0x5e0000`-`0x65ffff`, `0xb50000`-`0xb5ffff`,
  `0xb74000`-`0xb7ffff`. Addresses are from boot faults; register meanings are open (TODO 17).
- USB: four EHCI at `0x500000 + n*0x10000` (cells 11, 13, 15, 17) and four OHCI at
  `0x508000 + n*0x10000` (cells 12, 14, 16, 18), stock QEMU devices. The DTB xHCI
  `/xhci@00400000` (cell 10) is QEMU's sysbus xHCI (`sdp1406_add_xhci`, `USB_XHCI_SYSBUS`).
  The OHCI MICOM and GPIO sequence is not modelled.
- Bluetooth: `usb-bcm-btusb` (`hw/usb/dev-bcm-btusb.c`) is on EHCI slot 0's root hub, VID:PID
  `0a5c:21e8`, class `E0/01/01`. It is a high-speed device with a full-speed descriptor. It answers
  a basic HCI command set. The vendor RAM download is modelled: `0xFC2E` resets the 1 MiB RAM
  map, `0xFC4C` stores the data at the 24-bit address (granule-tracked), and `0xFC4E` succeeds only
  if its address was written. Writes outside the map return Invalid Parameters. The launch entry
  and real RAM layout are unverified. See TODO.md item 16. The guest bring-up (Reset, Read Local Version, Write Scan Enable, and the
  vendor commands) is checked by `Sdp1406BtGuestBringUpTests`. Qtest-driven HCI tests were dropped:
  the EHCI frame timer does not advance under `clock_step`, so no HCI transfer completes there.
- Monitor stub (`sdp1406_add_monitor`, `0x00200000`): SMC handler returns `r0=0`. Only
  effective on the control boot path (`-kernel` + `-device loader`). See TODO.md.

### eMMC (`sdp-mmc`, IRQ 35)

- Interrupt line: `(RINTSTS & INTMASK) != 0`.
- CMD17/18 read and CMD24/25 write from `-drive if=none,id=sdp1406-mmc,format=raw,file=...`.
  The device requests `BLK_PERM_WRITE` unless the drive is read-only (without it QEMU asserts on
  the first write).
- Writes copy IDMAC buffers to the image at the LBA (`sdp1406_mmc_store`). Without an image
  the card is blank and writes are dropped.

### Mali-400 MP (`mali.ko`)

- Layout (`mali_gpu_resources`): GP `0x600000` (IRQ `0x7e`), GP MMU `0x603000`, PP0 `0x608000`
  with MMU `0x604000`, PP1 `0x60a000`, PP2 `0x60c000`, PP3 `0x60e000`, L2 `0x610000` and
  `0x601000`, broadcast `0x613000`. SPI = IRQ - 32.
- GP reset: write 0 to INT_MASK `0x2c`, `0x80000` to INT_CLEAR `0x28`, `0x400` to the command
  `0x20`, then poll RAWSTAT `0x24` for `0x80000`.
- PP soft reset: write `0x80` to `0x100c`, poll RAWSTAT for bit 12.
- PP product ID `0xcd070000` (Mali-400 MP). The upper 16 bits of the PP version decode as
  `0xc807` = Mali-200 (rejected), `0xcd07` = Mali-400 MP. Test:
  `test_pp_product_id_is_mali_400_not_mali_200`.
- MMU DTE_ADDR (`+0x00` of each MMU block) keeps bits 31:12 only; bits 11:0 read back as 0.
  Without this the driver's `mali_mmu_raw_reset` assert fires on every reset. HARD_RESET
  (`+0x08` = 6) clears DTE_ADDR to 0. Test: `Sdp1406MaliMmuTests`.
- GP version `0x6c` = `0x0b070000`.
- DVFS `ioremap` calls for `0x00f00000`, `0x00e03000`, `0x00650020` all return non-NULL.

### Display and mixer

- Mixer `0x005DC000` (`samsung,sdp1406-mixer`, `reg 0x5dc000 0x4000`) is a register file
  (`sdp1406_add_mixer`). Named offsets store and read back. Unnamed offsets are logged.
- The logo plane base register is modelled at mixer `+0x30` and OSD `0x005D4030`.
  `sdp-logo.ko` writes the plane-derived source here. The display follows that
  non-zero source instead of the fixed `0x3C500000` scanout alias, and invalidates
  the console when the plane base changes.
- Mixer offsets written by `mixer_modeset_init` in `sdp-drm.ko`:

  | Offset | Meaning (inferred) | Values seen |
  |--------|--------------------|-------------|
  | `0x04` | 4K path flag | `0x11` |
  | `0x10` | non-4K path flag | `1` |
  | `0x18` | cleared before scanout | `0` |
  | `0x1c` | timing word | `0x113008ca` (4K), `0x06180320` (1366x768), `0x08980465` (1080p) |
  | `0x20` | width<<16 \| height | `0x0f000870`, `0x07800438`, `0x05560300` |
  | `0x24` | not decoded | `0x00800019`, `0x007b0018` |

- Branch selection (`mixer_modeset_init`, `0x000d7ab0`): 4K when `sdp_soc()` returns 7;
  1080p when `tztv_config_is_hd_resolution()` returns 0; otherwise 1366x768. The values on the
  HawkM are not recorded.

### Network (gmac)

- `sdp1406_add_gmac` (`0x120000`, `samsung,sdp-mac`), IRQ cell 5 = GIC IRQ 37
  (`SDP1406_GMAC_IRQ 5`).
- MAC registers `0x000`-`0x0ff` modelled. Version `0x20` reads `0x37`, which `sdpGmac_reset`
  accepts.
- MAC_Address0 (`0x040` high, `0x044` low) holds the NIC address (`conf.macaddr`, default
  `52:54:00:xx:xx:xx`). The high word reads with AE (bit 31) set. Writes update the same address.
- DMA (offsets from `0x1000`): bus mode (SWR), TX poll `0x1004`, RX poll `0x1008`, RX base
  `0x100c`, TX base `0x1010`, status `0x1014` (write 1 to clear), operation mode `0x1018`
  (ST `0x2000`, SR `0x2`), interrupt enable `0x101c`.
- Descriptors are 24 bytes (DSL 2): word 0 status and OWN, word 1 size in bits 12:0 and RER
  in bit 15, word 2 buffer address. Stride is `16 + 4*DSL`. Bus mode `0x10808` from `dma_init`.
- TX: with ST set, polls the ring while OWN is set, sends through `qemu_send_packet`, clears OWN,
  sets TI if IC. RX: copies into an owned descriptor, RDES0 = FS|LS (`0x300`), length in bits
  29:16. Single-buffer frames only.
- MII (DesignWare): address `0x10`, data `0x14`. One PHY at address 1, ID `0xabcd0000`
  (`SDP-EPHY`, mask `0xfffffff0`, production `phy_driver` table). Link follows the netdev state.
  BMSR `0x782d` is a fixed value, not a hardware read. Absent addresses read `0xffff`.
- Attach a host network for packet tests with
  `-nic socket,udp=127.0.0.1:<peer>,localaddr=127.0.0.1:<qemu>`.
- HDMI MMIO is not modelled.
- Stock 60 s NULL investigation: CPU2 `tHdmiRx01` stopped at
  `0xc03fa01c`; the kernel reported virtual address `0x00000000`, while
  register context showed `r0=0xbeba2b08`. Disassembly shows a 16-bit
  load/add/store sequence at that PC, and the HDMI modules are present.
  A valid HDMI-MMIO or userspace-pointer root cause is not yet established.
- The HDMI MMIO window `0x00580000-0x005bffff` is now stateful:
  - Live `sdp_hdmi.ko` traces read and write `0x10000`, `0x10004`,
    `0x10040`, `0x30000-0x3000c`, `0x17100/0x18100/0x19100/0x1a100`,
    and interrupt/status offsets.
  - The model stores 32-bit words and merges subword writes.
  - Qtests verify live-observed offsets and subword merge behavior.
  - Replacing the discard stub changed the downstream fault in 30 s live
    boots, supporting an HDMI register-gap contribution. Hardware register
    semantics remain unverified.

### sdp-i2c, 16 channels (`0x190100`-`0x1910ff`, SPI 40 + channel)

- `sdp1406_add_i2c()` builds two groups from `sdp1406_add_i2c_group()`: group 0 at `0x190100`
  (CH:0-7) and group 1 at `0x191000` (CH:8-15). Each channel maps 0x20 bytes at
  `base + ch * 0x20` with overlap priority 1. The `periph` stub (`0x190000`, size `0x2000`)
  is overlapped by these windows.
- SPI for channel N is `40 + N`. The status bit for channel N is `N` if N <= 7, otherwise
  `N - 8` (`cmp r3,#7; subgt r3,r3,#8` at `0x80293afc`). The shared IRQ words live in each group's
  last window: status `+0x14` and mask `+0x18` of CH:7 (`0x1901f4`/`0x1901f8`) and of CH:15
  (`0x1910f4`/`0x1910f8`).
- The model currently covers the CH:7 flow in detail (below). Other channels share the same
  register layout and IRQ path, but their driver state machine is not yet matched; see TODO.md
  item 1.
- Tests:
  - CH:4 raises SPI 44 on group 0 bit 4.
  - CH:5 is `i2c@001901a0`, raises SPI 45 on group 0 bit 5, and has no DTB child owner.
  - CH:6 is `i2c@001901c0`, raises SPI 46 on group 0 bit 6, and has no DTB child owner.
  - CH:8 raises SPI 48 on group 1 bit 0.
  - CH:10 is `i2c@00191040`, raises SPI 50 on group 1 bit 2, and has DTB FRC SDP
    child nodes including 7-bit `0x64`.

### sdp-i2c CH:7 details (`0x1901e0`, SPI 47)

- Registers (from `samsung,sdp-i2c` disassembly): CON `+0x00`, STAT `+0x04`, DS `+0x0c`,
  shared IRQ status `+0x14` (`0x1901f4`, CH:7 = `0x80`, write 1 to clear), IRQ mask `+0x18`.
- A DS write (one data byte) and a STAT start write (`BUSY`) both arm the 100 us virtual timer
  (`SDP1406_I2C_XFER_NS`). Until it fires, STAT reads back busy. When it fires, busy clears and
  the pending bit is set. The handler's exit path clears the pending bit it reads, so a DS write
  must not set it at once: that would lose the completion of the next byte. SPI 47 is high while
  the pending bit is set and CH:7 is not masked in `+0x18` (bit 7 set means masked). Writing
  `+0x14` clears the bit and lowers SPI 47. Reads return 0 (slaves not modelled).
- `+0x18` is a mask, not an enable. The driver clears bit 7 (`bic`) when it starts a transfer and
  sets it again (`orr`) when the transfer ends (`0x80293c7c`, `0x80294450`-`0x80294464`). A
  completion that arrives while the bit is clear must raise SPI 47. Treating the bit as an enable
  dropped every completion and produced the CH:7 timeouts. Mask semantics are inferred from the
  driver; they are not verified on hardware.
- Address phase: a STAT write with busy clear opens the address phase. The next DS byte is the
  address byte (R/W in bit 0). Every address is ACKed (LRB = 0).
- Sii9777 on CH:7 (`0x40` write, `0x41` read; `SDP1406_I2C_SII_*`): the Sii9777 driver's transfer
  is STAT `0xd0`, DS `0x40`, then the 16-bit sub-address high byte first, then data. The model
  keeps a 64 KiB register file: the first two bytes after the address set the pointer, later
  bytes are stored and the pointer counts up with no page wrap. Reads return the stored value,
  unwritten registers read 0. The DTB has no child node for it, so the model adds it on CH:7 only.
  The NACK path was removed. Before, `0x40` was NACKed and the driver logged 120 `incomplete xfer
  (-6)` errors in a 90 s boot.
- Read path: an ACKed read address (`DS` with bit 0 set) queues one data-byte completion. Each
  DS read with CON `0x100` (ACK) set, while the channel is idle, arms the next byte. Clearing
  CON `0x100` during a read arms the final byte (the driver NAKs the last byte this way). The
  read data is 0 (slaves not modelled).
- Back-pressure: a completion that arrives while the channel's status bit is still set and unmasked
  waits. The completion handler re-arms `xfer_timer` and returns without touching BUSY, the queue, DS or
  the bit. The driver reads the byte in its IRQ handler and then acks the bit, so the next completion
  raises on the next clock step after the ack. Each completion gets one IRQ and one ack; nothing is
  latched or counted. A masked bit does not hold the completion: the driver masks the group bit at
  transaction end and unmasks it at kick-off, so a masked bit can stay unacked for the whole next
  transfer. Holding on it deadlocked the CH:7 transfer (TODO.md item 1). The earlier latch
  (`irq_latched[bit]`, re-raised on ack) let two completions merge into one unread DS byte and is
  removed. A START write that arrives while a completion is deferred is ignored (the START path arms
  only when the timer is idle); the driver acks before it starts the next transfer, so this should not
  occur. See TODO.md item 1.
- Result: the 90 s README boot logs no CH:7 errors (baseline: 120 `incomplete xfer (-6)`).
  Measured timeouts on parallel 200 s boots are in TODO.md item 1.
- Test: `FREESUNG_QEMU=<build>/qemu-system-arm PYTHONPATH=tests python3 -m unittest tests.test_qemu_sdp1406_i2c`
  (50 tests, all passing). `setUp` acks IRQ_STAT and lets any deferred or queued completion
  finish, then acks again. The deferral test fails on the previous latch build, and the masked-bit test
  (`test_completion_during_masked_unacked_bit_is_not_deferred`) fails on the build that holds on any
  unacked bit. The Sii tests share one QEMU process, so they must not
  depend on register state written by an earlier test; use an address no other test writes. The qtest helper `_qtest` now reads until the command's
  final `OK`/`ERR` line, because `IRQ raise`/`lower` lines can precede it.
- Transfer queue: a START while a transfer is pending is queued only for an address write (`DS`
  while `addr_phase`). A data-byte `DS` write while busy merges into the pending transfer, and
  never postpones a pending completion. A queue for every START caused CH:6 `timeout waiting for
  bus idle` (20 in six boots), because BUSY stayed set longer than the driver waits. See TODO.md
  item 1.
- Per-transfer state: each queued transfer carries its own snapshot (`SDP1406I2cXfer`: `rd_arm`,
  `eep_sel`, `sii_sel`), taken at address-write time. The completion uses that snapshot, not the
  latest address, so a queued write address cannot clear a pending read's data arm. `xfer_done`
  runs a pending read's data arm before it pops the queue.

- Do not add stderr output to the model during the tests. The test harness starts QEMU with
  `stderr=subprocess.PIPE` and never reads it, so a full pipe blocks QEMU and the tests time out.

- Accepts 1-4 byte accesses; sub-word writes merge.

### CH:4 EEPROM slave (`s24c512c@50`, address `0xa0`/`0xa1`)

- The DTB places `s24c512c@50` under `i2c@00190180` (CH:4, SPI 44). The `eep` kernel driver and
  `libeeprom-map` read it through `/dev/eeprom`.
- Model: 64 KiB array (`SDP1406I2c.eeprom`), erased to `0xff`. Address phase `0xa0` or `0xa1`
  is ACKed by the slave on CH:4 only. The first two data
  bytes of a write set the 16-bit word pointer, high byte first. Later bytes write with page wrap
  (128-byte pages). Reads return the byte at the pointer and advance it.
- Inferred write-cycle model: a data write marks `eep_write_pending`; the next
  CH:4 address boundary starts a 5 ms virtual write-cycle timer. Subsequent
  `0xa0`/`0xa1` address phases while that timer is active set `STAT_LRB`.
  Other addresses do not inherit EEPROM busy/NACK state. The implementation is
  synthetic and remains to be compared with guest write behavior.
- Live 30 s A/B verification: busy-enabled stock boot reached CH:4
  `incomplete xfer (-6)` plus `eeprom_dev_write/eep_write` failure at 19.195 s.
  Two busy-disabled 30 s controls reached no CH:4/EEP diagnostic. GDB at
  `0xc0294414` saw repeated CH:4 `0xa0` waits and `STAT=0x91/DS=0xff`.
  This is evidence that the busy/`LRB` path affects the live write tail, not
  evidence that the 5 ms duration or NACK mechanism is hardware-correct.
- Synthetic content: the MAC `02:53:44:14:06:01` is stored at offset `0x1e04` (6 bytes). That is
  the offset `eeprom_read_mac` reads. It is locally administered and not a real device MAC. The
  real MAC is unknown.
- Factory import gate: byte `0x5671` is `0x63` (`SDP1406_I2C_EEPROM_IMPORT`, code-inferred from
  `factory-init` `CheckEEPromForAutoImport`; the retail value is unconfirmed). Test:
  `test_factory_import_gate_at_0x5671_is_0x63`.
- Factory flag: byte `0xf212` is `0x01` (`SDP1406_I2C_EEPROM_FLAG`). `automation-helper` takes the
  production branch for `1`. Before the fix, plain (wall-clock) boots read `0xff` in about 30% of
  runs. Cause: a driver START write for a DS-initiated address that lands after the address
  completed used to queue an extra transfer, which read a stale DS byte. The model now clears
  `xfer_from_ds` only on the START write (`sdp1406_i2c_xfer_done`), and a START during an in-flight
  transfer is ignored (`hw/arm/sdp1406.c`, STAT START branch). Tests:
  `test_start_write_after_address_completed_starts_no_second_transfer` and
  `test_start_write_during_inflight_transfer_adds_no_completion`. After the fix, 16 wall-clock boots
  all read `[1]`. Repeated CH:4 `0xa0` timeouts still appear in some boots (TODO.md item 1).
- Boot-logo mode: NV item 6 is EEPROM `0x2500 + 6 = 0x2506` (`fb_nv_load_data` in
  `libfastlogo_dataprovider`, and `fb_nv_load_logomode` in `libfbclient`). It is seeded to `0`
  (`SDP1406_I2C_EEPROM_LOGOMODE`), the firmware's first-boot default. Erased `0xff` selects the
  missing `bootlogo255.out`, so `fastlogo` draws nothing. Test:
  `test_boot_logo_mode_at_0x2506_defaults_to_0`. The seed fills `0x3978D000` after boot.
  The live display follows mixer `+0x30` and OSD `0x005D4030`; a final PPM screendump
  follows the guest plane-base write, but fresh stock-card timelines remain fully
  black (TODO.md item 15). Xorg ready, DRI nodes, and fastvideo startup do not prove
  that scanout memory contains a frame. The exact plane format remains open.
- EEPROM WP: the `samsung,eeprom-wp` pin is bit 6 (`0x40`) of the word at `0x005c1164`
  (DTB `<3 6 0>`). The kernel helper B (`0xc02937a0`) sets or clears that bit around each EEPROM
  transfer, then ORs `0x3000000` into the word at `0x005c1160`. A live read of the kernel selector
  `0xc0542ca8` showed value 6, so helper B runs. The model keeps the word in
  `SDP1406I2c.eep_wp_dat` as a 4-byte region (`sdp1406_add_eep_wp`, overlapping the pinctrl stub).
  Accesses are word-wide and merged by byte lane. Reset is `0x40` (writable, the agreed default).
  Data bytes are dropped while bit 6 is clear, and the pointer logic still runs. Polarity is
  inferred from the kernel driver (bit set before the data writes, cleared after), not from a
  datasheet. Tests: `test_wp_register_resets_open_and_reads_back`,
  `test_data_dropped_while_wp_is_closed`, `test_data_accepted_again_after_wp_opens`,
  `test_driver_word_rmw_toggles_wp_bit_only`, `test_old_wp_address_no_longer_gates_data`.
- Test: `Sdp1406I2cEepromTests` in `test_qemu_sdp1406_i2c.py` (MAC read, sequential read,
  pointer-only write, page wrap, erased reads, WP gating, no slave on `0xa3`, and
  `test_sequential_read_completes_one_irq_per_byte`, which checks one completion per DS byte).
- CH:5 low-overhead trace: writes, DS reads, completions, IRQ ACK and mask events are
  recorded in a 128-record ring at RAM `0x010e0000`. The ring is guest-RAM-only and
  does not use QEMU `-d` logging. Test: `test_ch5_trace_ring_records_writes_and_completion`.
- I2C NAK: clearing ACK during a read sets `STAT_LRB`; a subsequent address
  phase clears it. Tests: `test_clearing_ack_on_read_sets_lrb`,
  `test_address_phase_clears_stale_lrb`, and CH:10 variants.
- The same trace records CH:4 and CH:10 writes/completions. Test:
  `test_ch10_trace_ring_records_writes_and_completion`.
- A CH-specific GDB breakpoint run confirmed timeout waits for CH:4 `0xa0`,
  CH:5 `0xc4/0xca/0xce`, CH:6 `0x54`, and CH:10 `0xc8`. It showed live
  controller bases and status/state values; this disproves missing-window or
  missing-IRQ-line assumptions, while leaving slave/transaction semantics and
  return-code interpretation open.
- README stock-boot inventory: the live IRQ read-poll stalls are CH:4 `0xa0`,
  CH:5 `0x18/0x80/0xc0/0xc2/0xc4/0xca/0xcc/0xce`, CH:6
  `0x44/0x54`, and CH:10 `0x34/0xc8`. CH:7 `0x52` is resolved. The CH:4
  30 s GDB/disassembly control reached `0xc0294414` and the `0xc0294258`
  timeout branch; `eep_reg_read` is downstream of that result.
- TrustZone SMC reply model (`sdp1406_add_smc_log`): the secure monitor at `0x00200000` stores
  `r0`, `r1` and `ip` into the device at `0x00201000` and returns 0 for every SMC. Writes at
  `+0x0c` commit the call. The ring page is the export with `ip = 0x0e000005` and `r1 = 4096`
  (VA `0xc0000000` and above is translated by `-0x40000000`). Watch-page registrations
  (`ip = 0x0e000000`) are ignored. The first four exported pages are kept at `+0x14`, count at
  `+0x10`. A request (`r1 = 7`) serves the request records appended to each registered page since
  the last request, and writes the replies from offset 0 of that page with the byte count at
  `+0x7fc`. When every appended record has been consumed, the model clears the write index at
  `+0xffc` so the kernel can append again (the kernel asserts `in.size == 0` on exit). Known trm
  commands (types 0–11) get `code = 0`, an initialized context id `1`, and sequential session ids.
  Unknown types and bodies outside 20–108 bytes get an error record, so tzdaemon sees `-1`. These
  answers are protocol-level guesses (TODO.md item 4). Test: `test_qemu_sdp1406_smc.py`, which runs
  with the rest of the suite.
- Run the SMC and I2C qtests: `FREESUNG_QEMU=<build>/qemu-system-arm python3 -m unittest tests.test_qemu_sdp1406_smc tests.test_qemu_sdp1406_i2c`
- TrustZone export regression: `test_export_page_is_kernel_va_aligned_and_4kb_sized`
  verifies that the module-export page is 4 KB aligned and that `cmd=4096`,
  `ip=0x0e000005`, and a linear-map VA are translated to the physical ring.
- A 60 s stock control loaded `tzdev.ko` but reached a NULL-pointer fault and
  SELP hold before the historical 31 s `tzmem_mmap` window. The 60 s log
  contained no tzmem allocation/radix diagnostic, so that historical failure
  is not reproducible in the current stock control alone.

### Serial and console

- `ttyS0` (`0x00190a00`, IRQ 88) is the kernel console, wired to `-serial stdio` or `-serial file:<log>`.
- `ttySAC2` (`0x00190a80`, IRQ 90) is a second `-serial`. Used for `agetty`. No getty runs in the
  systemd boot (see TODO.md).
- The legacy UART micom protocol (`0x00190ac0`) is not modelled. The window is an inert stub.

#### Capturing the console for a probe

- Use `-serial file:<log>` for probes. The log fills as the guest writes, so you can read it while
  QEMU runs or after `timeout` stops it. For a socket that can be read at any time (including output
  printed before a client connects), use `-chardev socket,id=s0,path=<sock>,server=on,wait=off,logfile=<log>`
  with `-serial chardev:s0`. Input on that socket does not reach the shell yet (see TODO.md item 12).
- Always pass the sysmem `loader` device (see Boot inputs). Without it the kernel prints nothing,
  and an empty log looks like a serial or buffering problem when it is not.
- Stop the run once the output you need has appeared. Boot to userspace takes about 200 s, so a
  short `timeout 60` is enough for most i2c and early-boot checks.
- After the run, confirm no QEMU is left: `pgrep -x qemu-system-arm` must print nothing.
- Parallel boots: several QEMU instances can run at once (each with `timeout 25`, `< /dev/null`, its own
  `-serial file:`). Without the `-drive ...,snapshot=on` root card and the `root=` arguments the
  kernel panics at `VFS: Unable to mount root fs`, so the run tells nothing about i2c.
- Timing-sensitive i2c checks: plain runs show CH:4 and CH:10 timeouts under host load; `-icount shift=auto`
  runs did not (TODO.md item 1). Use icount when comparing i2c results across runs.

#### Debugging a probe with gdb

- Start QEMU with `-gdb tcp::1234` and run it in the background with `< /dev/null`. Without
  `< /dev/null` a background QEMU stops on terminal input and gdb cannot connect.
- Run QEMU as `(timeout 100 qemu-system-arm ... < /dev/null > log 2>&1 &)`, then run gdb in the
  foreground with its own `timeout 90`. Stop leftovers by PID (`kill -9 <pid>`). Do not use `pkill -x`,
  which kills every QEMU instance.
- Kernel addresses: the kernel runs at virtual `0xc0xxxxxx` with the MMU on. gdb breakpoints must
  use the virtual address (for example `*0xc0259a9c`). Physical addresses (`0x80259a9c`) did not
  fire in testing.
- Use a Python gdb script with `gdb-multiarch -batch -x script.py`. Batch mode does not handle
  `commands` blocks reliably (they fail with "Cannot execute this command while the target is running").
- In the script, subclass `gdb.Breakpoint` and print in `stop()`, then return `False` so the boot
  continues. Use `gdb.write(..., gdb.STDOUT)` and `gdb.flush()`, so the output lands in the log.
- Read registers with `gdb.parse_and_eval("$r1")` and memory with
  `gdb.selected_inferior().read_memory(addr, n)`.
- Keep `continue` out of the Python script. Run it from the batch command line

#### Production SELP debugger handshake

- The production `03-uImage` SELP function starts at virtual `0xc00148f0`.
  `0xc0014dfc` prints the T32 attach message, `0xc0014e04` enters the WFE
  spin, `0xc0014e08` branches back into the counter wait, and `0xc0014e0c`
  is the cleanup/fallthrough used to release the hold.
- The status block pointer is stored at literal `0xc0014ee0`; the handshake
  word is at offset `0x18` inside that block.
- Use `tools/t32_attach.py` with `-gdb tcp::1234 -S` to reproduce the
  debugger-style release. It uses a software breakpoint, reads r2, writes
  r2 to the status word, changes PC to `0xc0014e0c`, and detaches.
- This is a production external-debugger branch, not a confirmed missing
  SDP1406 register model. `SELP_ENABLE=00000000` keeps the CI probe case
  backed by RAM and reaches this branch. A stock-card reproduction with
  `SELP_ENABLE=00000001` also reaches `c00148f0` and the same T32 spin.
  The earlier CPU 1 stall label was not supported by an RCU stall banner or
  RCU backtrace; the observed CPU 1 process was `tvs-main-loop`, with MMC
  softirq-context warnings. Therefore `SELP_ENABLE=1` is not a valid RCU
  bypass or replacement for the debugger handshake.
  (`gdb-multiarch -batch -x script.py -ex continue`). A `gdb.execute("continue")` at the end of the
  script can hit "Cannot execute this command while the target is running" (see TODO.md item 12).
- Check the script's output (for example `grep HIT log`). A breakpoint that never fires usually
  means the address is virtual, the kernel did not reach that code, or gdb connected too late.
- Userspace probes (factory-init, kfactoryd) share VA breakpoints with other processes. Filter on
  a string at a known VA in `stop()` (for example `/opt/usr/factory_backup_flag` at `0x26334` in
  factory-init). Read it in Python with `try/except gdb.MemoryError` and return `False` when it
  does not match. A gdb `condition` that reads unmapped memory raises "Error in testing condition"
  and stops the run, so do not use memory reads in `condition`.
- Check that `this` (or the register holding the object) looks like a valid object before trusting
  field values. Pointers into the stack or ASCII-like field values mean the hit is not the target
  object.
- Template: `/tmp/fsqemu/waitloop.py` (scratch, not in the repo). It breaks on the micom wait loop
  `*0xc0259a9c` and the builder `*0xc02ad65c`, and prints `bp=<number>` so hits can be separated.
  The wait loop copies the 12-byte request from user `r2` (`copy_from_user`), so read it after the
  copy at `*0xc0259b08` from the kernel frame (`fp-48`). The command is byte 1 of that record
  (it matches the `ack[XX]` timeout tag in the boot log).
- Factory-store import probe: break on `TList::Add` (`0x1147c` in `factory-init`) in Python, filter
  on the `/opt/usr/factory_backup_flag` string, and read the 364-byte item (`r1`) for its
  `db/factory/` name. Guard `r1 == 0` before reading. Use `sleep 8` before gdb connects. With
  `sleep 4` the start raced ("target is running") and gave empty logs.
- Return-value probes (`/tmp/fsqemu/fi_commit.py`, `fi_err.py`, scratch): break at the instruction after
  each `bl` in `Import_FactoryData` (`0x2185e`, `0x2186c`, … `0x218bc`, `0x218c8`). Do not read the
  return value as an error code: `m_ImportFactoryDataToFileSystem` returns a COW string pointer that
  `Import_FactoryData` ORs and never checks, and the `commit` breakpoint also fires from other paths.
  Use the database contents instead of return values to decide whether an import succeeded.

### Micom (SDP messagebox)

- Production transport: `sdp_messagebox` at `0x00800100` (DTB), modelled at `0x00800000`
  (`sdp1406_mbox_reply`, `sdp1406_mbox_ack_for`).
- Guest writes the TX frame to `0x484`-`0x48c` (command in bits 16-23 of `0x484`), length to
  `0x480`, and kicks with `0x4c0`.
- The model replies at `0x400` (length 9) and `0x404`-`0x41c`, sets pending bit 0 at `0x440`,
  and clears it on `0x444`.
- ACK tag (wire byte 2) by command: `0x10`→`0x20`, `0x18`→`0x29`, `0x19`→`0x2a`, `0x1a`→`0x2b`,
  `0x30`→`0x30`,
  `0x60`→`0x50`, `0x61`→`0x51`, `0x6a`→`0x6a`, `0x6e`→`0x6e`, `0xa0`→`0xa0`, `0xd1`→`0xd1`, other→`0x29`.
- Mailbox TX/send/clear transitions are traced in guest-RAM ring `0x010f0000` (128 records).
- Tests: `test_qemu_sdp1406_mbox.py`, `test_qemu_sdp1406_mailbox.py`. These check the register
  protocol only, not that the guest accepts the reply in a boot.

### Boot inputs

- Kernel: `03-uImage` (ARM Image, load `0x80008000`).
- DTB: `05-dtb.bin`. `/chosen/bootargs` contains `init=/sbin/init`, `rootfstype=vdfs`, and
  `quiet`.
- Sysmem table: `sysmem/sysmem-table.bin`, loaded at `0x80542b94` with a `loader` device.
  Without it the boot is silent.
- The `loader` device path is the only one tested. The bootloader path is covered in
  `freesung-bootloader/TODO.md`.

### Boot log notes

- `sdp_clk_register_fixed_div: failed to register clock emac_clk` is benign. The vendor fixed-div
  table (kernel file offset `0x4f6134`) lists `emac_clk` twice with different parents. The second
  registration fails the duplicate-name check (`-EEXIST`), and the first stands. This is static
  analysis; the errno is not printed at runtime. No model change is needed. Re-open only if this
  line blocks a boot step, then trace the errno with virtual-address breakpoints (MMU on).
- TrustZone (TODO.md item 4): with the export-based ring and the write-index reset, the boot shows
  no `tzdaemon thread has been waiting` and no `Wait for request channel`. The remaining TZ error is
  `tzmem_mmap: Can't allocate page for tzmem export` at about 31 s, followed by repeated
  `tzmem_radix_insert` failures on the same wsm node. It is open in TODO.md item 4.
- `org.volt.firstscreen` waits for the kdbus system bus (`/dev/kdbus/0-kdbus-system/bus`). Boot the
  production systemd path: `init.wrapper` sets `DBUS_SYSTEM_BUS_ADDRESS=kdbus:...`, and `dbus.service`
  runs `dbus-daemon --address=kdbus:`. Firstscreen's output goes to log devices, not the serial console.

## Card images

- `mmc/mkcard.py` builds the card from the extracted `platform.img` (p22) and the
  `usr/data_backup.img` seed (p24, `/opt`). Test: `test_qemu_sdp1406_mkcard.py`.
- `--firmware-root <root>` formats p26 (`/mnt/drm`, VDFS) with the firmware's
  `usr/bin/mkfs.vdfs` under `qemu-arm`, at its declared 6144K. The file is pre-sized and
  formatted in place; `-i` must not be used (it writes a compact read-only image).
  `freesung-tools/freesung-probe-artifacts.py` passes this option. Without it p26 stays
  unformatted and the guest mount of p26 stalls boot.
- Root partition is p22. `init.d/mount.sh` mounts p24 as `/opt`. p26 is `/mnt/drm`.
- `psa_mount.sh` mounts p26 at `/mnt/drm` and initializes
  `/mnt/drm/securestorage/backup`. The
  `/mnt/drm/securestorage/backup/*: No such file or directory` line is produced
  by unmatched shell globs in that script, not by CH:4 EEPROM access.
- `freesung-probe-artifacts.py` repacks the p24 seed without
  `usr/factory_backup_flag`, allowing `factory-init` to run its documented
  EEPROM-gated auto-import path. The original extracted `data_backup.img` remains
  unchanged. It surgically patches p22's original VDFS1002 `TIFSDaemon` CZip
  object and preserves all catalog extents and the partition size. Round-trip
  extraction and VDFS audit pass. Live execution is currently rejected by
  Samsung's non-auth executable gate, so this remains an analysis artifact and
  the TIF negative-index crash remains open.
- Test (offline, plus the firmware mkfs check when the extracted tree and `qemu-arm` exist):
  `python3 -m unittest discover -s tests -p 'test_qemu_sdp1406_mkcard.py'` from `freesung-qemu/`.

### TIFSDaemon negative-index guard

- `test_qemu_sdp1406_tif.py` pins the extracted `TIFSDaemon` ELF guard at
  `0x83a8e`/`0x83a92`.
- The synthetic patch changes `cmp r3,r1` to `cmp r1,#0` and BMI to the existing
  `GetModelStr` fallback.
- Run with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_qemu_sdp1406_tif -v
```

- It is an ELF/regression test, not authorization bypass and not proof of a live
  TIFSDaemon boot. The modified executable is rejected as `non-auth` by the
  current VDFS kernel in live boots.
- `Sdp1406SyntheticVdfsAuthTests` validates a disposable uImage copy that
  replaces `check_execution_available` at virtual `0xc01759d0` with `mov r0,#0`
  plus `bx lr`. A live synthetic boot suppressed the TIFSDaemon `non-auth`
  error. It does not alter the repository's official uImage or claim vendor
  authorization.
- `tools/tif_synthetic_patch.py` creates the disposable auth-bypass uImage,
  validates the pinned auth-function bytes, and repairs both payload and header
  CRC fields. This is test-only and must not be used on hardware.
- A 60 s stock-card synthetic-auth control reached no TIFSDaemon `non-auth`
  line and continued until a separate NULL-pointer fault at 29.088 s. This
  is evidence for the auth bypass, not closure of the negative-index ELF bug.
- The synthetic p22 CZip closure control launched TIFSDaemon but logged
  `Wrong magic in compressed extent` for inode `61098` and faulted in the
  ELF dynamic segment at `0x000d5124`. The ELF guard bytes were confirmed
  after p22 unpack. `sdp1406_unzip` now accepts zlib-wrapped CZip chunks,
  covered by `Sdp1406UnzipRegisterTests.test_zlib_wrapped_czip_chunk_decodes`;
  live CZip extent/mapping behavior remains open after a second live control.
