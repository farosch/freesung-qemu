# freesung-qemu Development

QEMU board model `sdp1406` (HawkM) and the Samsung TV card boot. Commands run from
the repository root. Project rules are in [AGENTS.md](../AGENTS.md). Build and boot
steps are in [README.md](README.md).

Nothing here is verified on hardware. The old investigation log is in git history
(last full version: commit `107336d`).

## Current status

- Boot to userspace: the card boot (`mmc/hawkm-card-root.img`, VDFS root) reaches
  `vdfs3`, `Kdbus system bus found!`, and the start of Xorg. No kernel oops or abort
  in a 90 s boot.
- Video: not working. The scanout at `0x3c500000` is all zero in every screendump.
- Boot without `-initrd` (README command) works, after commit `e8dd7e2`.
- With the README `-append` (`SELP_ENABLE=00000000`), the kernel spins at `0xc0014e04`
  (`b .`) after `[SELP] while loop ... please attach T32...` at about 36-42 s guest
  time. With `SELP_ENABLE=00000001` in the DTB this does not happen through 97 s.
- The drm kernel abort is fixed: the store to `0xfe4c6018` (phys `0x5c6018`) fell in an
  unmapped gap (`0x5c2000`-`0x5cffff`, no DTB node). The `sdp1406.clkpin` stub now covers
  `0x5c0000`-`0x5cffff`, and `load_drivers_insmod thread end(2)` prints in a 150 s boot.
  The scanout was still zero at `0x3c500000` after `end(2)` in the last recorded run, so video was not written in that run.
  See [Open questions](#open-questions).

## Verified facts

### Boot inputs

- Kernel: `hawkm-msdu10-payloads/03-uImage` (ARM `Image`, load address `0x80008000`).
- DTB: `hawkm-msdu10-payloads/05-dtb.bin`. Its `/chosen/bootargs` overrides `-append`.
  To change kernel arguments, patch a scratch copy of the DTB.
- Sysmem table: `probe-artifacts/sysmem/sysmem-table.bin` must be loaded at `0x80542b94` (a loader
  device). Without it there is no output.
- `-m` is ignored. `SDP1406_RAM0_SIZE` is 496 MiB.
- Boot command: [README.md](README.md), around lines 78-110.

### Memory layout (DTB and QEMU model)

- The DTB `/memory` node is a single node. The board model adds a bank at
  `0x80000000`, size `0x10000000`. Without it the kernel oopses in `free_initmem()`
  (`010099e0`) because the `.init` pages have no sparse-memmap section.
- The DTB and any initrd are reserved with `/memreserve` entries. The reservation is
  made by `sdp1406_load_dtb_with_initrd` in `hw/arm/sdp1406.c`. It packs the blob first
  (libfdt cannot grow a packed blob), so it reserves a placeholder, packs, removes the
  placeholder, and re-adds the reservation at the packed size. Board tests pass with
  this (commit `107336d`).
- The `sdp_mmap` carve-outs below RAM1 (`samsung,cursor` `0x22600000`/`0x180000`,
  `dp-ttx-vbi` `0x22780000`, `align2` `0x227c0000`) are not in `/memory`, so the kernel does
  not manage them. The DRM cursor BO is backed there, so the model maps `0x22600000`-`0x227fffff`
  as RAM (`SDP1406_SDP_MMAP_BASE`). Without it the cursor memcpy aborts the X server.
- The `[SDP]` RAM base (`PHYS_OFFSET`) is inferred from the load address, not verified.

### Stubs and models (all in `hw/arm/sdp1406.c`)

Each stub was added because the kernel or a driver aborted on that address. Address
mapping used for the aborts: physical = virtual - `0xfdf00000` for the `0xfe..` windows.

- `sdp1406.unzip` (`0x300000`, GIC SPI 2): DMA gzip/DEFLATE engine. Reads the input
  buffer and inflates it with zlib. Raises IRQ 34 on done or error. Only the driver
  source was checked for the register contract.
- `sdp1406.uddec` (`0xb00000`, size `0x30000`): stubbed after an external abort at
  guest physical `0xb28004` (the audio codec `sdp_codec.ko` touches it).
- `sdp1406.demux` (`0x900000`, size `0x11f00`) and `sdp1406.demux2` (`0x930000`,
  size `0x1000`): stubbed after an external abort at `0x9086c0`. Never accessed in a
  7 min boot.
- `sdp1406.ga` (`0x660000`): `sdp-drm.ko` writes start `+0x00`, clears status
  `+0x0c`, uses mask `+0x10` and config `+0x14`/`+0x68`. The DTB gives `interrupts =
  <0 0x68 0>`, which is SPI 104 (`SDP1406_GA_IRQ`). Start sets status bit 0 and the
  IRQ asserts when `status & mask` is nonzero. The `GA INT delayed, timed out` message
  is gone.
- Media DSP status (`0xa00000`): experimental overlay. Reads of `0xa04010` and
  `0xa08010` return 1. The `devAe_WatchDog` (`sdp_alsa.ko`) `rtv[0]` value it needs is
  not identified. The `devAe_WatchDog: TDSP(0)` line still logs.
- USB host controllers (`hw/arm/sdp1406.c`, `sdp1406_add_usb`): four EHCI at
  `0x500000 + n*0x10000` (GIC cells 11, 13, 15, 17) and four OHCI at `0x508000 + n*0x10000`
  (cells 12, 14, 16, 18), each mapped with QEMU's stock device. The DTB xHCI node
  `/xhci@00400000` (cell 10, reg size `0xc700`) is QEMU's sysbus xHCI (`TYPE_XHCI_SYSBUS`,
  `sdp1406_add_xhci()`), with `USB_XHCI_SYSBUS` selected in `hw/arm/Kconfig`. The rest of
  `0x40c700`-`0x4fffff` is a read-zero, write-ignore stub. The OHCI MICOM and GPIO sequence is not modelled; see [TODO.md](TODO.md).
- Generic stub windows with no device model: `0x5d0000` (osd), `0x5e0000`-`0x65ffff`,
  `0xb50000`-`0xb5ffff`, `0xb74000`-`0xb7ffff`.
- RAM `sdp1406.hwmem` at `0xf00000` (256 KiB), the gap RAM `0x37800000`-`0x3c4fffff`, and
  the sdp_mmap RAM `0x22600000`-`0x227fffff` (the DRM cursor, dp-ttx-vbi and align2 carve-outs).
- RAM2 `0x40000000`-`0x4fffffff` (256 MiB). `/memory` lists only the first 112 MiB, but the DTB
  sdp_mmap carve-outs above it (`samsung,mfc-b` at `0x47200000`, `hen-b`, `dp1`, `align1`) run to
  `0x50000000`. Without this the MFC firmware load aborts (external abort at `0xd6400000`, which maps
  PA `0x47240000`). Test: `test_ram2_covers_dtb_sdp_mmap_carve_outs`.
- Kernel source for investigation: the public Samsung tree `doitaljosh/linux-3.10.30-famhubv1`
  (the 3.10.30 tree matching the device uImage) is readable with
  `gh api repos/doitaljosh/linux-3.10.30-famhubv1/contents/<path> --jq .content | base64 -d`.
  It has the SDP board DTS files (for example `arch/arm/boot/dts/sdp1406-FHD-1G-tizen.dts`), which
  carry the `memory` and `sdp_mmap` nodes. The vendor `sdp-drm.ko` decompile is
  `/home/bici/tools/out/sdp-drm.c`, and the Ghidra project is in `/home/bici/tools/ghidra-projects`.
  No full 3.10.30 checkout is on disk. The decompile hides indirect calls, so check the source
  before concluding from it.

- eMMC (`sdp-mmc`, GIC IRQ 35): the controller drives `(RINTSTS & INTMASK) != 0`.
  Before this the driver polled and the boot stalled at `vdfs3`. The eMMC card and the
  64-bit IDMAC register map come from the Samsung `sdp_mmc` driver, not hardware.
- eMMC data: reads (CMD17/18) and writes (CMD24/25) use the card image from `-drive
  if=none,id=sdp1406-mmc,file=...`. The device requests write permission on that backend
  unless the drive is read-only. Writes copy the IDMAC buffers to the image at the LBA
  (`sdp1406_mmc_store`). Without an image the card is blank and writes are dropped.

### Mali-400 MP4 (`mali.ko`)

- Layout from `mali_gpu_resources`: GP `0x600000` (IRQ `0x7e`), GP MMU `0x603000`,
  PP0 `0x608000` with MMU `0x604000`, PP1 `0x60a000`, PP2 `0x60c000`, PP3 `0x60e000`,
  L2 `0x610000` and `0x601000`, broadcast `0x613000`. SPI = IRQ - 32.
- GP reset (from `mali_gp_reset_async`, `0x1726c`): write 0 to INT_MASK `0x2c`, write
  `0x80000` to INT_CLEAR `0x28`, write `0x400` to the command `0x20`, then poll RAWSTAT
  `0x24` for `RESET_COMPLETED` (`0x80000`). The model sets that bit on a `0x400`
  command write. This removed the `ioremap fail` and `[SDP GPU] Mali Probe failed` lines.
- PP soft reset: write `0x80` to control `0x100c`, poll RAWSTAT for bit 12 (`0x1000`).
  The model sets RAWSTAT to `1 << 12` on reset. Confirmed on the guest.
- The `ioremap fail` print comes from `mali_gp_reset_wait` (timeout path), confirmed with
  a `printk` breakpoint and the saved `lr`. An earlier MMU-reset explanation was wrong.
- `mali_parse_product_info()` rejected the PP ID as "Mali-200 is not supported". Decoded
  from `mali_initialize_subsystems` (`mali.ko`, `0xd538`): the upper 16 bits of the PP
  version are the product ID. `0xc807` = Mali-200, `0xce07` = Mali-300, `0xcd07` =
  Mali-400 MP, `0xcf07` = Mali-450 MP. The model now reports PP `0xcd070000` (Mali-400 MP).
  GP `0x6c` = `0x0b070000` is unchanged. Test: `test_pp_product_id_is_mali_400_not_mali_200`.
  Card boot effect: see the firstscreen notes in `TODO.md`.
- Firstscreen device-node check (scratch card, not a repo test): the card rootfs is
  repacked as squashfs (`mksquashfs root root.sqfs -all-root -no-xattrs -comp gzip`) and
  written to partition 22 (sector 125057). The card DTB `bootargs` must keep the same
  length (`rootfstype=vdfs` and `quiet` are blanked to spaces). An `rc3.d` `S99zdevdump`
  hook prints `/dev` and runs `exec 3<$n` per node. Result: `/dev/fb0` and `/dev/fb1` are
  ENOENT; UMP, Mali and `dri/card0`/`card1` open. Custom ARM probe binaries (static,
  no-libc, dynamic) fail with ENOEXEC in this kernel, so use the shell probe.
- DVFS `ioremap` calls (`init_mali_dvfs_status`, `0x2d5d8`) for `0x00f00000`,
  `0x00e03000` and `0x00650020` all return non-NULL at runtime (gdb). `0x650020` has no
  DTB `reg`. Its value is a literal in `mali.ko`.

### Display and mixer

- Scanout: `0x3c500000` (DTB `sdp_mmap`), aliased to guest `0x40000000`, 1920x1080 (XRGB8888). The size comes from the firmware's `sdp1406-FHD-1G-tizen` DTS `display-timings` `timing-0` (`hactive = <1920>`, `vactive = <1080>`), which the FHD image uses
  XRGB8888.
- Mixer `0x005DC000` (DTB `samsung,sdp1406-mixer`, `reg 0x5dc000 0x4000`) is a register
  file (`sdp1406_add_mixer`). Named offsets are stored and read back. Unnamed offsets
  are logged as unimplemented. Values come from `sdp-drm.ko` decompiled with Ghidra
  (scripts in `freesung-tools/research/ghidra/`):

  | Offset | Written by | Meaning (inferred) | Values |
  |--------|-----------|--------------------|--------|
  | `0x04` | `mixer_modeset_init` | 4K path flag | `0x11` |
  | `0x10` | `mixer_modeset_init` | non-4K path flag | `1` |
  | `0x18` | `mixer_modeset_init` | cleared before scanout | `0` |
  | `0x1c` | `mixer_modeset_init` | timing word | `0x113008ca` (4K), `0x06180320` (1366x768), `0x08980465` (1080p) |
  | `0x20` | `mixer_modeset_init` | width<<16 \| height | `0x0f000870`, `0x07800438`, `0x05560300` |
  | `0x24` | `mixer_modeset_init` | not decoded | `0x00800019`, `0x007b0018` |

- Branch selection in `mixer_modeset_init` (`0x000d7ab0`): 4K path when `sdp_soc()`
  returns 7. 1080p path when `tztv_config_is_hd_resolution()` returns 0. Otherwise
  1366x768. What these return on the HawkM is not recorded.
- Test: `FREESUNG_QEMU=<qemu> python3 -m unittest discover -s tests -p test_qemu_sdp1406_mixer.py -v`.

### Network (gmac MDIO, PHY, and DMA)

- Gmac `0x120000` (DTB `ethernet`, `samsung,sdp-mac`, `phy_mask = 0`) is `sdp1406_add_gmac`,
  a NIC-backed sysbus device (`sdp1406.gmac`) on DTB cell 5 (SPI 5, GIC IRQ 37).
- The MAC registers (`0x000`–`0x0ff`) and the version register are modelled. Other offsets read
  0 and log `LOG_UNIMP`.
- DMA (DesignWare offsets from `0x1000`): bus mode (SWR self-clears and resets the DMA state),
  TX poll `0x1004`, RX poll `0x1008`, RX base `0x100c`, TX base `0x1010`, status `0x1014`
  (write 1 to clear), operation mode `0x1018` (ST `0x2000`, SR `0x2`), interrupt enable `0x101c`.
- TX: with ST set, a poll walks the ring while OWN is set. Each frame is sent through
  `qemu_send_packet`, OWN is cleared, TI is set if IC is set, and the walk wraps on TER.
- RX: a frame arriving with an owned descriptor is copied into its buffer. RDES0 gets
  FS|LS (`0x300`) and the frame length in bits 29:16, OWN is cleared, and RI is set. A frame
  that arrives with no owned descriptor is queued until SR is set or an RX poll runs. Frames
  longer than the buffer are truncated. Only single-buffer frames are handled.
- Descriptor layout (from the production `sdp-gmac` driver, `03-uImage`): six 32-bit words
  per descriptor. Word 0 is status and OWN, word 1 holds the buffer size in bits 12:0 and RER
  in bit 15, word 2 is the buffer address. The driver fills all six words, so the stride is
  24 bytes (DSL 2). The model computes the stride as `16 + 4*DSL` (DSL from bus mode bits
  6:2), and the driver's `dma_init` bus mode value `0x10808` gives that stride.
- RX and TX base registers are `0x100c` and `0x1010`, as set by the driver's `dma_init`.
- The PHY link is taken from the netdev link state. BMSR reports link down when the netdev is
  down.
- MII (DesignWare): address `0x10` (PA `[15:11]`, GR `[10:6]`, GW bit 1, GB busy bit 0),
  data `0x14`. One PHY at address 1: ID `0xabcd0000`. This value is the `SDP-EPHY` entry in the
  production kernel's `phy_driver` table (`03-uImage`, entry at `0xc051d900`, mask `0xfffffff0`,
  name string at `0xc045870a`). Its `config_init` (`0xc00220dc`) writes vendor registers `0x14`/`0x17`
  and does a read-modify-write of `0x11` without checking the result, so the model's register
  storage is enough. Link status uses the generic BMSR path (`read_status` is NULL in the table).
  BMSR `0x782d` is the standard 10/100 capability set with link up and autoneg complete; it is
  not read from hardware. Absent addresses read `0xffff`.
- Version `0x20` reads `0x37`. `sdpGmac_reset` (probe uImage `0xc0022d74`) reads the low byte,
  subtracts `0x34` and accepts results 0, 2 and 3 (bitmask `0xd`). Before this, it failed with
  `Can't find GMAC`.
- Boot with the production kernel: no `Could not find PHY` and no reset error. The kernel
  registers `eth%d`. Whether the guest opens the interface is not verified, because the
  boot stops at the SELP hold before a shell is available.
- Attach a host network for packet tests with `-nic socket,udp=127.0.0.1:<peer>,localaddr=127.0.0.1:<qemu>`.
  The DMA tests use a host UDP peer in place of a real network.
- Test: `FREESUNG_QEMU=<qemu> python3 -m unittest discover -s tests -p test_qemu_sdp1406_gmac.py -v`.
  This covers the MII/PHY registers and the DMA TX and RX paths (12 cases, including a
  two-descriptor TX ring that checks the 24-byte stride).
- HDMI MMIO is not modelled.

### sdp-i2c CH:7 (`0x1901e0`, GIC SPI 47)

- Model: `sdp1406_add_i2c()` in `hw/arm/sdp1406.c`. It maps 0x20 bytes at `0x1901e0`
  with overlap priority 1, so it wins over the `sdp1406.periph` stub (`0x190000`,
  size `0x2000`) for this window only. Other sdp-i2c controllers (`190100`, `190120`,
  `190140`, ...) still hit the stub.
- DTB: `i2c@001901e0`, `interrupts = <0 0x2f 0>` (SPI 47). The GIC input index equals
  the SPI number, the same convention as ethernet (5) and mmc (3).
- Registers (inferred from `samsung,sdp-i2c` driver disassembly, not a datasheet):
  CON `+0x00`, STAT `+0x04`, DS `+0x0c`, shared IRQ status `+0x14` (`0x1901f4`, one bit per
  channel, CH:7 = `0x80`, write 1 to clear), IRQ enable `+0x18`.
- STAT and DS writes complete at once: they set the CH:7 status bit and raise SPI 47. A
  write of `+0x14` clears the bit and lowers SPI 47. STAT reads return busy and LRB clear.
  Slaves are not modelled: every access is ACKed and reads return 0.
- Access sizes 1–4 bytes. The driver issues 1-byte writes (seen at `+0x08`), so a
  4-byte-only region causes data aborts. Sub-word writes merge into the register.
- Test: `FREESUNG_QEMU=<qemu> python3 -m unittest discover -s tests -p test_qemu_sdp1406_i2c.py -v`
  (register behaviour, byte merge, periph stub, and the SPI 47 raise/lower through
  `irq_intercept_in` on `/machine/unattached/device[4]`, the arm_gic instance).
- Known gap: the boot still reports `[i2c fail] CH:7 Device ID:0x40 timeout`. See TODO.md.

### Serial and console

- Guest `ttyS0` acknowledges TX through `UTRSTAT` (write 1 to bit `0x20`). Without that path IRQ 88 is re-asserted forever. Fixed by patch `0003`.
  Build without patch `0003` and the boot storms on IRQ 88.
- Serial output goes to `-serial file:<log>`.
- Micom UART (`0x00190ac0`, IRQ cell 59) is the second chardev, `serial_hd(1)` in
  `sdp1406.c`. Pass a second `-serial` to capture micom TX. Without it the TX is dropped,
  as before. The guest sends nothing on it yet: the send path is gated on device 3
  registration (see TODO.md).
- `ttySAC2` UART (`0x00190a80`, DTB cell 58 = GIC IRQ 90) is the second chardev, `serial_hd(1)`.
  The Samsung driver numbers ports by probe order, and `/etc/inittab` runs `agetty` on `ttySAC2`.
  The rootfs boots through systemd, so no getty starts from `inittab`. Test: `test_uart_and_micom_windows_are_mapped`.
- The legacy UART micom protocol (`0x00190ac0`, `sdp1304-uart-micom`) is not modelled. Its window is an
  inert stub, and the host responder and its tests were removed.
- Production micom transport is the SDP messagebox at `0x00800100` (DTB `sdp_messagebox@800100`), modelled
  at `0x00800000` in `hw/arm/sdp1406.c` (`sdp1406_mbox_reply`, `sdp1406_mbox_ack_for`). The guest writes the
  TX frame to `0x484`..`0x48c` (command in bits 16..23 of `0x484`), length to `0x480`, and kicks with `0x4c0`.
  The model replies at `0x400` (length 9) and `0x404`..`0x41c`, sets pending bit 0 at `0x440`, and clears it on `0x444`.
  The ACK tag (wire byte 2) is set per command: `0x10`→`0x20`, `0x18`→`0x29`, `0x19`→`0x2a`, `0x1a`→`0x2b`,
  `0x60`→`0x50`, `0x61`→`0x51`, `0x6a`→`0x6a`, `0x6e`→`0x6e`, `0xd1`→`0xd1`, other→`0x29`.
  Test: `FREESUNG_QEMU=<qemu> python3 -m unittest discover -s tests -p test_qemu_sdp1406_mailbox.py -v`.
- The DTB `/chosen` bootargs set the kernel command line, so `-append` is ignored.
  Boot-time options such as `init=` must be changed in the DTB.

## Initrd (historical)

- The probe kernel (removed) is not needed; the official `03-uImage` is the only kernel.
  Its `vmlinux` is not in the workspace.
- Initrd: `-initrd` is delivered. `linux,initrd-start` and `/memreserve` are correct.
  The kernel then reports `Failed to execute /init`, `Failed to execute /sbin/init` and
  `No init found`, with no errno. Both the probe init (`initramfs/init.c`) and the
  The card rootfs has no `/init` (`sbin/init` links to `init.wrapper`). That is not a problem:
  the card boot with `init=/sbin/init` already reaches userspace (`FB:INIT`, `vconf-init`), so the
  initramfs `/init` route is not needed for userspace. The `/init` path stays a dead end.
- Userspace init: `init.wrapper` prints `Kdbus system bus found!` and then `exec`s
  systemd. Nothing after that prints. Shell as init (`init=/bin/sh`) reaches a prompt
  after about 70 guest seconds.
- `/opt` comes from `init.d/mount.sh` (`/dev/mmcblk0p24`, VDFS). The card keeps p24 at
  its declared size and seeds it with `usr/data_backup.img` (`--data-image`).
- `libave` is missing its preload file in the seed. `p26` (`/mnt/drm`) is empty, so
  `SB: bad signature` is logged. Neither has been shown to block video.

## Open questions

Each question is marked with the evidence that would close it.

1. **drm group never completes (blocker).** `fastinit` case 2 (drm, `0xa90e`-`0xaa16`):
   insmod `mali.ko`, then chmod/chown `/dev/mali0`, then insmod `sdp-drm.ko`. The serial
   log shows `mali mod ld`, then `end(8)`, `end(11)`, `end(12)`, but never `end(2)` or
   `end(9)`. Verified: the static code has no wait between the Mali insmod return and the
   group end. Inferred: the block is inside `mali.ko` init. Not verified, because the
   guest CPU was spinning in the SELP loop (question 7), so the drm thread may be starved.
   Verified with `SELP_ENABLE=00000001` (about 4 min guest time): `mali mod ld` at about
   6 s, then no further mali output, no `/dev/mali0`, no `sdp-drm`, no `fastvideo`, and
   no `end(2)`. The framebuffer stays zero. So the SELP spin is not what blocks the drm
   thread. PC samples over about 20 s (SELP=1, after `mali mod ld`): CPU 0 polls a RAM
   halfword with `ldrh r1,[r3,#0x10]; tst r1,#1; beq; b` at `0xc007a028`, with `R3` in
   kernel RAM (`0xcbda379c`). CPU 2 loops on a timer read (`mrrc p15,#1,...,c14`) at
   `0xc02cf434`. CPU 1 is in `wfi` (`0xc001c104`). CPU 3 is stuck at `PC=0xc` in `abt32`
   mode, so it re-faults on fetch from its low vector. No sample lands in `mali.ko`
   (`0xbea39000`), so the drm thread is not spinning in Mali code. Not identified: which
   task CPU 0 is polling. The Mali-init block is still unconfirmed.
   Verified owner: the poll is in the function at `0xc0079db8`, called from `0xc007a060`
   (kernel `kernel/smp.c`). It waits for `csd_data[3].flags` bit 0 to clear, so CPU 0 is
   waiting for CPU 3 to acknowledge a cross-call IPI (`smp_call_function_many`-like).
   CPU 3 is stuck in `abt32` at `PC=0x0c`, so it never acks.
   CPU 3 bring-up (verified): the board sets secondaries to start at `0x803f11c8`
   (`hw/arm/sdp1406.c:1288-1291`, install at `:1467-1468`). That is the kernel's pen, which
   spins on `pen_release` and then jumps to `secondary_startup`. Address 0 is unmapped
   (RAM0 starts at `0x01000000`, `:37`), so the first prefetch abort at a vector slot
   turns into a permanent loop at `0x0c`.
   First abort chain (verified from `-d int`, SELP=1 scratch DTB): CPU 0 executes a Secure
   Monitor Call (exception 13, EL1 to EL3) at log line 1023615. The board model has no SMC
   or PSCI handler, so the EL3 vector is unmapped. Prefetch aborts at EL3 then follow with
   `IFAR=0x8` and `IFAR=0xc`, and the loop repeats. Not verified: which kernel code issues
   that SMC (a raw scan of the kernel image for SMC encodings is ambiguous, because it also
   matches data words). An earlier CPU 3 data abort at `DFAR=0` (log line 3297) comes
   before it, but its faulting PC is not logged by `-d int`.
   Verified: the SMCs come from Samsung's TrustZone driver `tzdev.ko`
   (`lib/modules/3.10.30/kernel/drivers/misc/tzdev/`, loaded at `0xbeeb9000`). The SMC
   instruction is at module offset `0x1b30`, inside `tzdev_ipc_thread`. Its wrapper polls a
   counter and passes `r1=0xe`. The first SMC has `r0=0x14` (meaning unknown). The DTB and
   kernel contain no PSCI strings. Later SMCs alternate between two callers, `0x1b30` and
   `0x664`, in a loop. Verified from serial: `tzdev:tzio_exchange_message: tzdaemon thread
   has been waiting 19 > 5 s`. `/usr/bin/tzdaemon` exists and has a systemd unit, but its
   start is not seen in the serial log. Not verified: whether the monitor's `r0=0` return
   is what `tzdev` expects, or whether the daemon is the real blocker.
   The board now has a monitor stub (`sdp1406_add_monitor`, `0x00200000`) whose SMC handler
   returns `r0=0`. On the control boot (`-kernel` + `-device loader`), the EL3 abort loop is
   gone (0 secure-world exceptions in a 100 s SELP=1 run, 95 SMCs returned; `-d int` labels
   every secure exception "from EL3", so that count is not a monitor-mode indication). Not
   verified: whether `0` is the value the guest expects.
   Boot-path caveat (from the bootloader `loader.elf` run, see
   [freesung-bootloader/TODO.md](../freesung-bootloader/TODO.md) item 2): the loader enters
   the kernel in secure SVC with MMU on. There the single SMC faults on fetch at `IFAR 0x200008`
   (translation fault) and the stub never runs, with or without it. The stub is therefore only
   exercised on the control path. Without the stub, the control path shows an abort loop at
   `IFAR 0xc` on CPU 0 (not CPU 3 as stated above).
   Side effect seen once: with the stub and SELP=1 scratch DTB, the serial log still shows
   `[SELP] while loop ... please attach T32` at about 54 s guest time. So SELP=1 no longer
   avoids the loop with the stub in place, and the earlier "SELP=1 avoids it" finding
   applies only to the no-stub run. Next: check what the SMC return value feeds into the SELP check.
2. **`/dev/mali0` exists?** Not checked on the current build. `mali.ko` registers misc
   device `mali0`. The DTB has no Mali GPU platform node. Runtime check: in
   progress.
3. **Who writes the framebuffer?** `org.volt.firstscreen` and `fastvideo` start, and the
   scanout stays zero. Static check (run T0): `usr/bin/fastvideo` has no `0xd6c00000` or
   `0xd7000000` in its code or rodata. Those values only appear in its ELF symbol table, so the
   faulting framebuffer pointer is computed at runtime, not hard-coded. Next: find which
   device register or buffer address `fastvideo` reads, and why it faults at `0xd6c00000`.
4. **Initrd `/init` failure.** No errno. Not explained.
5. **Micom `device3`.** Registered late (`ontimer-wt61p807`). It logs `did not registered
   completely`. Whether it blocks video is not verified. The micom sync-reply path was
   traced with gdb: the reply byte matches the armed tag, so the waiter is completed. The
   sender that writes the first request frame is not identified.
6. **Audio DSP (TDSP).** `devAe_WatchDog` logs zero `TDSPReg`/`XROADREG` values. Not
   shown to block the display path.
7. **SELP wait loop and RCU stalls.** Verified: with `SELP_ENABLE=00000000` the kernel
   spins at `0xc0014e04` (`b .`) after the `[SELP] while loop` message, and the RCU stall
   on CPU 0 follows. Not verified: what the check tests, and whether `SELP_ENABLE=1` is
   the correct value for this device. The SELP trap is at function `0xc00148f0`, reached
   after the diagnostic call at `0xc0014dfc` (static check). With `SELP_ENABLE=00000001`
   the stall appears once at about 72 s (CPU list empty, detector CPU 2). Samples of CPU 2
   show ordinary kernel code, not a spin loop. Not identified, and not repeated in 247
   log lines. With `SELP_ENABLE=00000001` the DRM run also shows RCU stalls on CPU 1 at
   about 71 s and 251 s, after repeated `devAe_WatchDog` dumps.
8. **Mali product ID.** Fixed: PP reports `0xcd070000` (Mali-400 MP). See the Mali section.
9. **DBADDRU / MMC IRQ line.** No board test covers the MMC IRQ line.

## Reproduction

- Build: stage `hw/arm/sdp1406.c` and patches `0001`-`0003` into the QEMU 10.2.1 tree,
  then `ninja qemu-system-arm` in `build/`. Configure with `--target-list=arm-softmmu
  --enable-fdt=system --enable-pixman`, plus `--enable-sdl` for the GUI.
- Board tests (81 cases across `test_qemu_sdp1406_*.py`, including `test_qemu_sdp1406_usb.py`):
  `FREESUNG_QEMU=<qemu-system-arm> python3 -m unittest discover -s tests -p 'test_qemu_sdp1406_*.py'`
  (about 75 s). One known failure: `test_scanout_frame_is_shown` also fails with the USB
  controllers removed (see [TODO.md](TODO.md)).
- USB test only: `FREESUNG_QEMU=<qemu-system-arm> python3 -m unittest tests.test_qemu_sdp1406_usb`.
- Screendump: `printf 'screendump <path>\n' | socat - UNIX-CONNECT:<monitor-sock>`.
  Count nonzero bytes in the PPM payload to check the scanout.
- Always tail the serial log; do not sleep. Verify with `pgrep -x qemu-system-arm` that
  no QEMU processes remain.
