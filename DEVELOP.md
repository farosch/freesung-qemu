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

Upstream QEMU 10.2.1 with the board file and three patches:

```sh
sudo apt install meson ninja-build libfdt-dev libpixman-1-dev libsdl2-dev
curl -LO https://download.qemu.org/qemu-10.2.1.tar.xz
tar -xJf qemu-10.2.1.tar.xz
cd qemu-10.2.1
patch -p1 < /path/to/freesung-qemu/patches/0001-hw-arm-wire-sdp1406-board.patch
patch -p1 < /path/to/freesung-qemu/patches/0002-hw-char-exynos4210-raise-tx-irq-only-on-write.patch
patch -p1 < /path/to/freesung-qemu/patches/0003-hw-char-exynos4210-ack-rx-tx-events-via-utrstat.patch
cp /path/to/freesung-qemu/hw/arm/sdp1406.c hw/arm/
mkdir build && cd build
../configure --target-list=arm-softmmu --without-default-features \
    --disable-docs --disable-werror --disable-tools --disable-guest-agent \
    --disable-download --disable-tcg-interpreter --enable-fdt=system \
    --enable-sdl --enable-pixman
ninja qemu-system-arm
```

- Patch `0002` stops the Exynos UART from re-raising TX pending on each update.
- Patch `0003` adds the `UTRSTAT` TX/RX ack path. Without it IRQ 88 storms.
- `--enable-pixman` is required for the monitor's `screendump`.

## Tests

Board suite (100 cases in 10 files, about 80 s; the Linux banner case is skipped without kernel artifacts):

```sh
FREESUNG_QEMU=/path/to/qemu-system-arm python3 -m unittest discover -s tests -p 'test_qemu_sdp1406_*.py'
```

Single file (replace the name):

```sh
FREESUNG_QEMU=/path/to/qemu-system-arm python3 -m unittest discover -s tests -p test_qemu_sdp1406_gmac.py -v
```

| File | Covers |
|------|--------|
| `test_qemu_sdp1406_board.py` | MMIO map, GIC, RAM banks, Mali GP/PP reset, Mali MMU DTE_ADDR, scanout frame |
| `test_qemu_sdp1406_mixer.py` | Mixer register file |
| `test_qemu_sdp1406_mmc.py` | eMMC registers and card writes |
| `test_qemu_sdp1406_mbox.py`, `test_qemu_sdp1406_mailbox.py` | Messagebox replies and ACK tags |
| `test_qemu_sdp1406_gmac.py` | MII/PHY and DMA TX/RX with a UDP peer |
| `test_qemu_sdp1406_i2c.py` | sdp-i2c CH:4/7/8 registers, IRQ mask semantics, SPI 44/47/48 |
| `test_qemu_sdp1406_usb.py` | EHCI/OHCI capability registers |
| `test_qemu_sdp1406_mkcard.py` | Offline GPT/partition checks for `mmc/mkcard.py` |
| `test_qemu_sdp1406_serial.py` | UART banner from `tests/uart/start.S` (bare metal); kernel version banner on the console |

The serial tests fail on silence instead of waiting for a full boot. The Linux banner test
(`Sdp1406LinuxSerialTests`) runs only when `FREESUNG_KERNEL`, `FREESUNG_DTB`, `FREESUNG_SYSMEM`
and `FREESUNG_CARD` point at local artifacts, and it is skipped otherwise. It uses a 60 s cap.

Tests skip when `FREESUNG_QEMU` (or `qemu-system-arm` on PATH) lacks the `sdp1406` machine.
The i2c tests (`test_qemu_sdp1406_i2c.py`) need a build that includes the sdp-i2c model. With
`qemu-10.2.1` five of them fail. Run them against the i2c build (`qemu-i2c-build/qemu-system-arm`).
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

### Stubs and devices

- `sdp1406.unzip` (`0x300000`, IRQ 34): DMA gzip/DEFLATE engine. Used to decompress the VDFS root.
- `sdp1406.ga` (`0x660000`, SPI 104): DRM GA. Start sets status bit 0; IRQ asserts on `status & mask`.
- `sdp1406.uddec` (`0xb00000`), `sdp1406.demux` (`0x900000`), `sdp1406.demux2` (`0x930000`):
  register stubs. Added after external aborts. Not otherwise modelled.
- Media DSP status (`0xa00000`): reads at `0xa04010` and `0xa08010` return 1. The
  `devAe_WatchDog` value it feeds is not identified.
- Generic windows with no device model: `0x5d0000`, `0x5e0000`-`0x65ffff`, `0xb50000`-`0xb5ffff`,
  `0xb74000`-`0xb7ffff`.
- USB: four EHCI at `0x500000 + n*0x10000` (cells 11, 13, 15, 17) and four OHCI at
  `0x508000 + n*0x10000` (cells 12, 14, 16, 18), stock QEMU devices. The DTB xHCI
  `/xhci@00400000` (cell 10) is QEMU's sysbus xHCI (`sdp1406_add_xhci`, `USB_XHCI_SYSBUS`).
  The OHCI MICOM and GPIO sequence is not modelled.
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
  item 6.
- Test: CH:4 raises SPI 44 on group 0 bit 4, CH:8 raises SPI 48 on group 1 bit 0.

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
- Address NACK: a STAT write with busy clear opens the address phase. The next DS byte is the
  7-bit address. Address `0x40` is NACKed (LRB = 1, read from STAT); all other addresses are
  ACKed. The driver's Sii9777 transfer for `0x40` is STAT `0xd0`, DS `0x40`, STAT `0xf0`, DS
  `0x70`, `0x61`, `0x0f` (from the probe-artifact driver log). The driver reports
  `incomplete xfer (-6)` and retries, which is the expected behaviour for an absent device.
- Read path: an ACKed read address (`DS` with bit 0 set) queues one data-byte completion. Each
  DS read with CON `0x100` (ACK) set, while the channel is idle, arms the next byte. Clearing
  CON `0x100` during a read arms the final byte (the driver NAKs the last byte this way). The
  read data is 0 (slaves not modelled).
- Latch: a completion that arrives while the group's status bit is still set is kept in
  `irq_latched` (per group, not per channel: the ack write may reach the group through any channel
  window). The IRQ_STAT write re-raises latched bits, so the driver's ack does not drop a
  completion it has not yet seen.
- Result: a 20 s full-root boot logs no CH:7 timeouts. The only CH:7 messages are `incomplete xfer
  (-6)` for the `0x40` NACK, repeated as the driver retries (135-150 per 20 s). CH:4 `0xa0`
  timed out once in three boots. See TODO.md item 6.
- Test: `FREESUNG_QEMU=<build>/qemu-system-arm python3 -m unittest tests.test_qemu_sdp1406_i2c`
  (21 tests; `setUp` acks IRQ_STAT twice to clear a latched bit from an earlier test).

- Accepts 1-4 byte accesses; sub-word writes merge.

### Serial and console

- `ttyS0` (`0x00190a00`, IRQ 88) is the kernel console, wired to `-serial stdio` or `-serial file:<log>`.
- `ttySAC2` (`0x00190a80`, IRQ 90) is a second `-serial`. Used for `agetty`. No getty runs in the
  systemd boot (see TODO.md).
- The legacy UART micom protocol (`0x00190ac0`) is not modelled. The window is an inert stub.

#### Capturing the console for a probe

- Use `-serial file:<log>` for probes. The log fills as the guest writes, so you can read it while
  QEMU runs or after `timeout` stops it.
- Always pass the sysmem `loader` device (see Boot inputs). Without it the kernel prints nothing,
  and an empty log looks like a serial or buffering problem when it is not.
- Stop the run once the output you need has appeared. Boot to userspace takes about 200 s, so a
  short `timeout 60` is enough for most i2c and early-boot checks.
- After the run, confirm no QEMU is left: `pgrep -x qemu-system-arm` must print nothing.

### Micom (SDP messagebox)

- Production transport: `sdp_messagebox` at `0x00800100` (DTB), modelled at `0x00800000`
  (`sdp1406_mbox_reply`, `sdp1406_mbox_ack_for`).
- Guest writes the TX frame to `0x484`-`0x48c` (command in bits 16-23 of `0x484`), length to
  `0x480`, and kicks with `0x4c0`.
- The model replies at `0x400` (length 9) and `0x404`-`0x41c`, sets pending bit 0 at `0x440`,
  and clears it on `0x444`.
- ACK tag (wire byte 2) by command: `0x10`→`0x20`, `0x18`→`0x29`, `0x19`→`0x2a`, `0x1a`→`0x2b`,
  `0x60`→`0x50`, `0x61`→`0x51`, `0x6a`→`0x6a`, `0x6e`→`0x6e`, `0xd1`→`0xd1`, other→`0x29`.
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

## Card images

- `mmc/mkcard.py` builds the card from the extracted `platform.img` (p22) and the
  `usr/data_backup.img` seed (p24, `/opt`). Test: `test_qemu_sdp1406_mkcard.py`.
- Root partition is p22. `init.d/mount.sh` mounts p24 as `/opt`. p26 is `/mnt/drm`.
- The guest runs from p22. Host-side mkfs writes a VDFS superblock on p26; whether the guest mkfs
  does is still open (see TODO.md).
