# TODO

Open investigation leads for the SDP1406 (HawkM) QEMU board.

Investigation rule: use the production kernel (`freesung-tools/.../hawkm-msdu10-payloads/03-uImage`) for all work. The probe kernel is historical only; do not use it for new runs.

## Audio DSP kernel fault (open)

- Symptom (historical, observed on the probe kernel `probe/03-uImage-probe`, 3.10.30): a data abort at ~13–15 s. Not yet reproduced on the production kernel; the production headless run of 90 s showed no abort. Faulting PC is the comparator at `0xc0184438` (`ldrd r4,[r0,#8]`), reached from a binary search at `0xc01701e8` (`blx r9`). The bad `r0` comes from helper `0xc01700a8` (`*(struct) + index`).
- Bad pool base: `0xd1234000` in one run, `0xdafd0000` in another. It is not a fixed address and is not in any file.
- Trace (`/tmp/base_store.so`, TCG plugin, not in repo): the bad base is stored at many PCs, mostly the exception-entry register save (`0xc0010b6c`) and a few generic routines (`0xc00bf320`, `0xc01d267c`, `0xc0172bb4`, `0xc00bf1b0`, `0xc0050a5c`, `0xc01d86c8`). The value is register-resident at exception entry, so the origin is an earlier computation, not a single store.
- Open: find the instruction that first produces the base value. Candidates: an allocator or ioremap result, or an uninitialised or raced field read by the kernel.
- Open: whether the baseline README boot hits the same abort. The README does not mention it.
- Not a proven DSP-stub issue. `sdp1406.c` DSP status stub (lines 129–157) returns 1 for all reads and is not implicated by the trace so far.

## Network: link and packet I/O (open)

- Done: the gmac PHY model (MII at `0x10`/`0x14`, PHY at address 1, version `0x37`) removes `Could not find PHY` and the reset error. The kernel registers `eth%d`.
- Done: the production kernel's `phy_driver` table (`03-uImage`, entry at `0xc051d900`, name `SDP-EPHY` at `0xc045870a`) has the same ID `0xabcd0000` and mask `0xfffffff0` as the model. BMSR `0x782d` is the standard capability set, not a hardware read.
- Done: DMA TX and RX with a host UDP peer (see the Network section in `DEVELOP.md`).
- Done: the descriptor stride is 24 bytes (DSL 2). The driver fills six words per descriptor (`sdpGmac_reset` `0xc0022d78`, TX fill `0xc0022e68`, RX fill `0xc0022f5c`), and `dma_init` (`0xc0023814`) gives bus mode `0x10808`.
- Done: the RX layout matches the driver. The completion path (`0xc00263cc`) tests FS|LS as `0x300`, reads the length from bits 29:16, and the fill path sets RER as bit 15 of word 1 (`0xc002300c`). The buffer size mask is bits 12:0 (`bic 0xe000`), so `SDP1406_GMAC_DES_LEN_MASK` is `0x1fff`.
- Done: the RX and TX base registers `0x100c` and `0x1010` are set from `dma_init` (`0xc0023840`–`0xc0023850`).
- Open: the TX and RX poll register offsets (`0x1004`, `0x1008`) are not yet confirmed in the driver. The open path that sets the ST and SR bits in operation mode is not located.
- Done: the production `SDP-EPHY` callbacks (`phy_driver` at `0xc051d900`, Linux 3.10.30 layout inferred from the struct) are `config_init` at `0xc00220dc`, `soft_reset` at `0xc00224dc`, and `config_aneg` at `0xc00226dc`. `config_init` writes vendor registers `0x14`/`0x17` (indirect sequence) and does a read-modify-write of `0x11` (clears bits `0xc0`). It never branches on the value, so the model's storage of those writes is enough for init to succeed. The `read_status`, `aneg_done` and `ack_interrupt` slots are NULL, so link status uses the generic BMSR path. The meaning of vendor register `0x11` is not modelled (it reads 0).
- Open: whether the guest ever opens `eth0`. In the documented boot the guest stops in the SELP hold (about 6 s guest time) before any init script or shell runs, and the log has no `eth0` open, link-up or DMA lines. Blocker: `SELP_ENABLE=00000000` is set both in `-append` and in the DTB bootargs, which override it. Next step is a scratch DTB with `SELP_ENABLE=00000001` (or an `init=/bin/sh` variant), then check `0x100c`/`0x1010` writes.

## Module load fault after gmac (open)

- With the gmac model, `mali.ko` and `sdp_drm.ko` now load. Boots differ in which CPU and task faults first (`udevd` at `0xc01cbd84`, or `dbus-daemon`/`chown` at `0xc00b3668`), so compare by content.
- The same VA is always involved: `0xfac04000` (and `0xfac35000` on CPU0, `0xfac1a000` in the udevd run). Its `pgd` entry is 0, so no mapping exists. `vmalloc=550M` puts this range in vmalloc.
- Fault is a load, not a store: `factory-init` at PC `0xc01c9c0c` runs `ldm r1!` with `r1=0xfac00000`. In a later boot the first `0xfac00000` value stores were on CPU0 at exception entry (`0xc0010b6c`, stack `0xc9401xxx`), so the value was already in a register. Its source load is not yet found.
- Open: which load or computed pointer first produces `0xfac00000`, and which module or ioctl passes it. The framebuffer stays zero until this is resolved. Kernel symbols are not in the uImage, so names must be recovered from the call chain.

## Micom handshake and display wake (open)

- Production kernel (`03-uImage`, Linux 3.10.30) logs `sdp-micom: Timeout error` for commands `0x10`, `0x6a`, `0x6e`, and `sdp-micom: device3 did not registered completely` at boot. The board model does not answer the micom UART (`hw/arm/sdp1406.c`, micom UART at lines 68–71 and 1603–1609).
- The black SDL screendump (production kernel) fits a TV power/panel path that never leaves standby without a micom reply. This is not yet proven.
- Finding: the micom send path (`0xc02662a0`) drops every command before the UART. It reads a
  state byte at `ctx+64`, then calls the dispatcher (`0xc026596c`) with device index 3. The
  dispatcher reads the callback table at `ctx+76 + idx*4`. When that entry is NULL it prints
  `device3 did not registered completely` and writes nothing. The boot log shows this at 0.23 s.
  A trace with the micom UART on its own chardev shows zero UTXH writes to `0x190ac0`, while
  `ttyS0` has about 97k. So the three timeouts are this same gap.
- Correction: device 3 does register. Under gdb (production kernel), the `msg-wt61p807` probe
  (`0xc0259ebc`) runs, and its call to `sdp_micom_register_cb` (`0xc02659e0`, return at
  `0xc025a094`) returns 0. Live callback registrations were seen for ids 0–8, from callers
  `0xc02b3b0c`, `0xc028f1bc`, `0xc02592b8`, `0xc025a094`, `0xc02598b0`, `0xc0258d7c`,
  `0xc0258958`, `0xc04e9b48`, `0xc02b3d70`. The earlier "probe never hit" result came from a run
  that shared gdb port 1234 with another QEMU, so it is discarded.
- Resolved (ordering): the first dispatch (`0xc026596c`) happens before the probe (`0xc0259ebc`).
  That first call prints the one-time boot line `device3 did not registered completely` at
  0.248 s. Later dispatches and sends (`0xc02662a0`) happen after registration, so that line is not
  the ongoing gap. The post-registration drop is still open.
- Open: the platform device for `msg-wt61p807`. `05-dtb.bin` has no such string. The name's
  only data reference is the record at `0xc052c7a0` (name pointer in word 0). No literal-pool word
  points into `0xc052c600`–`0xc052c800` except the micom code below, so no simple table walk
  reaches that record. It is probably read through a linker section or a list. Leads:
  - Literal-pool loads of `0xc052c650` at `0xc0265f40`, `0xc0265fb0`, `0xc0266040`, all in the
    micom area near the dispatcher (`0xc026596c`).
  - Literal-pool load of `0xc052c668` at `0xc0266270`, just before the send path (`0xc02662a0`).
  - Self-linked list head at `0xc052c658` (both words point to itself, an empty list).
  Next step: check whether `0xc052c650`/`0xc052c658` is a micom callback list that the platform
  device path reads.
- Open: the sender of `0x10`/`0x6a`/`0x6e` is not found. Literal-pool searches for `Timeout error` and `send_cmd` hit nothing. The disassembly base must be `0xc0008000` for file offset `0x40`, because the 0x40-byte uImage header is not part of the load address. Earlier slice addresses were off by 0x40.
- Open: the meanings of those command IDs, whether the `regmap-i2c`/`regmap-spi` modules missing from the image matter, and whether `kfactory_drv.ko`'s `NOT BOE panel` message is on the display path.

## SELP / TRACE32 attach (open)

- `tools/t32_attach.py` is a GDB-RSP stand-in for TRACE32, not Lauterbach's proprietary protocol. It halts, sets a hardware breakpoint at `0xc0014e04`, writes the status word at `block+0x18`, moves PC to `0xc0014e08`, and detaches.
- Open: the SELP loop is a post-panic hold, so attach cannot make boot proceed on its own. Fixing the abort above is the prerequisite.
- Finding (lab boot, rebuilt model with eMMC writes, original DTB, documented `-append`): the hold still happens at about 7 s. The first fault is an oops in `xhci_hcd` module init (`xhci_hcd(+)` loading at 0xbed72000, fault PC `0xbed76fe4`, `Internal error: : 8`, which is a synchronous external abort). The model has no xHCI at `0x400000` (see the USB decision above), so the module's access there is unclaimed. The `[SELP]` message follows about 0.3 s later. The eMMC write fix does not clear this hold. Open: confirm the faulting address, then decide whether to model an xHCI stub or remove `xhci_hcd` from the load list (`usr/bin/lists/index.hawkm.json`).
- Missing: a test for `t32_attach.py`, and README/DEVELOP updates.
- Address check: `0xc0014e04`, `0xc0014e08` and the literal at `0xc0014ee0` are identical in `03-uImage` (the probe differed only at file offset `0x4C7350`). `0xc0014e08` is `ldr r1, [pc, #0xd0]`, so the handshake that compares r2 with the status word is not established. The status-word handshake and the status block layout are unverified against the firmware.

## USB host controllers (open)

- Modelled: EHCI at `0x500000 + n*0x10000` (cells 11, 13, 15, 17) and OHCI at `0x508000 + n*0x10000` (cells 12, 14, 16, 18), using QEMU's stock `platform-ehci-usb` and `sysbus-ohci`. Only the capability/revision registers are tested.
- Open: the OHCI wrapper drives a GPIO and sends MICOM commands (`__core_cmd_micom`, `__mcm_cmd_chk_and_send`), and `usb_enable.ko` (`/dev/usben`) sends `set_GPIO_port`/`send_micom_msg`. Nothing answers these, so the USB power sequence is unverified. The DTB `samsung,usb-enable` is `<6 1 0>` (`/pinctrl/gpio13`).
- Decided: the device is considered to have no xHCI (USB 3). The DTB's `/xhci@00400000` node
  (`samsung,xhci-hcd`, cell 10) is treated as a leftover from the SoC template, so `0x400000` has
  no stub. Revisit this if a schematic or a real-device probe shows a USB 3 port. The rootfs has
  no `xhci-hcd.ko`, so the HawkM `usb1` load list (`usr/bin/lists/index.hawkm.json`) fails at that
  entry.
- Open: `num-ports` is left at the QEMU default (OHCI 3, EHCI 1). The real port count per controller is not read from the DTB.
- Done: the boot kernel is Linux 3.10.30 (`03-uImage` banner). The guest root `section-6.bin` (`root=/dev/mmcblk0p22`) has `/lib/modules/3.10.30/kernel/` and `/lib/modules/wifi/`. The extracted `filesystem-6` tree has only a 4.1.10 module set under `usr/lib/modules/` and is not what the guest loads. Use `section-6.bin` as the reference.
- Done: `org.tizen.tv.kmodloader.service` sets `KMOD_HOME=/lib/modules/3.10.30/kernel`, so the earlier `/lib/modules/linux/kernel/...` reading was wrong. The USB modules it loads (`usbnet`, `rtl8150`, `ax88772`, `ax88179_178a`, `usbhid`, `snd-usbmidi-lib`, `snd-usb-audio`) are present in the guest.
- Done: `xhci-hcd.ko` is present at `/lib/modules/3.10.30/kernel/drivers/usb/host/xhci-hcd.ko`. The xHCI decision above stands, since the device has no xHCI, so this module should load nothing. Its load in `usr/bin/lists/index.hawkm.json` (`usb1`) is still unverified.
- Done: `zram.ko` is at `/lib/modules/3.10.30/kernel/zram.ko`. `wl.ko` and `bcm_dbus.ko` are at `/lib/modules/wifi/`. `hid.ko`, `evdev.ko`, `mousedev.ko`, `g_ether.ko`, `samdrv.ko`, `sdp_socite.ko`, `tzdrv.ko`, `sdp_mac.ko`, `r8101.ko`, `compat.ko`, `PreInit.ko`, and `rt5572sta.ko` are not in the walk. Their absence has not been confirmed.
- Done: a full restore of `section-6.bin` (`vdfs-unpack -o`) has 164 `.ko` files. `/lib/modules/3.10.30/modules.dep` lists 170 entries. `sound/core/snd*.ko`, `sound/soundcore.ko`, `drivers/base/regmap/regmap-{i2c,spi}.ko`, `drivers/hid/hid-{elecom,magicmouse,wacom}.ko`, and `drivers/input/evdev.ko` are absent. `org.tizen.tv.kmodloader.service` `insmod`s them, so those lines fail unless the code is built in. Check the kernel config before relying on this.
- Done: the 12 names are absent from the guest root (`mousedev`, `g_ether`, `samdrv`, `sdp_socite`, `tzdrv`, `sdp_mac`, `r8101`, `PreInit`, `rt5572sta`, `hid.ko`, `evdev.ko`). `compat.ko` is only under `/opt/usr/apps/system-plugin-tv/WIFI_LIB/QCA/`, and `g_ether.ko` is under `/usr/driver/`. Neither directory is in `section-6.bin`, so they may be on another partition.
- Done: `etc/rc.d/rc.sysinit` `insmod`s `/lib/modules/{mousedev,evdev,hid,usbhid,hid-microsoft,samdrv,tzdrv,r8101,sdp_mac,xhci-hcd,mali}.ko`. Those flat paths don't exist. The real files are `usbhid` at `drivers/hid/usbhid/usbhid.ko`, `hid-microsoft` at `drivers/hid/`, `mali` at `soc/mali.ko`, and `xhci-hcd` at `drivers/usb/host/`. The `mali` case matters: the path does not match, so that load fails.
- Done: `/dev/mmcblk0p24` (`data.img`, VDFS1002, mounted at `/opt`) is in `probe-artifacts/mmc/hawkm-card-root.img` (GPT partition 24, 2270688 sectors). Restored it with `vdfs-unpack`. It has no `.ko` files at all, and no `system-plugin-tv`, `WIFI_LIB`, or `QCA` directory. So `/opt/usr/apps/system-plugin-tv/WIFI_LIB/QCA/{cfg80211,ath6kl_usb,compat}.ko` are absent from both `p22` and `p24`. `/usr/driver/g_ether.ko` is absent from `p22`. Checked all card partitions: p23 (PLATFORM1), p25, and p26 are blank (no VDFS signature, no nonzero data), and the only VDFS partitions are p22 and p24. `g_ether` appears in the card image once, as text inside `etc/rc.d/rc.sysinit` (`insmod /usr/driver/g_ether.ko` in the `press 1` "script is stopped" branch). No catalog entry named `g_ether.ko` exists in any partition, and no other firmware set on disk has the file. Conclusion: `g_ether.ko` is not shipped on this device. The `rc.sysinit` branch is dead on this device unless the file is provided elsewhere.
- Done: `bin/usb_start.sh` loads `usb-common`, `usbcore`, `ohci-hcd`, `ehci-hcd`, `xhci-hcd`, `usb-storage`, and `usb_enable`, in that order. All exist except `usb-storage` (not checked). This confirms the guest does try the xHCI driver. Without xHCI hardware it loads without a device.
- Open: `preusb` → `usb1` → `usb` (`usr/bin/lists/index.hawkm.json`) is not shipped on this device. The p22 catalog has no `lists` directory and no `index.hawkm.json`. The card and zip have no `preusb` text. The only copy is in `T-KTMDEUC`. The earlier resolution (38 of 69 `insmod` targets resolved, 31 missing) used that other set's list against this device's root, so it describes the list's targets, not this device's real list. `usb1` in p22 is only `usb1.so` (a library), not the list.
- Open: `rc.sysinit` `insmod /lib/modules/mali.ko` fails (real path `soc/mali.ko`). `TODO.md` line 30 says `mali.ko` loads under the gmac model. The two can both be true only if the kmodloader path (`KMOD_HOME`) loads `soc/mali.ko` and the `rc.sysinit` line is a silent no-op. Not resolved. A boot log that shows which path loads it would settle this.
- Open: the scanout board test `test_scanout_frame_is_shown` fails with no screendump file, and it also fails with the USB controllers removed. The failure predates the USB change and is not yet explained.

## /dev/mmcblk0p26 mount failure (open)

- Symptom (production kernel, `root=/dev/mmcblk0p22`): `FB:INIT` and `psa_mount.sh` mount `/dev/mmcblk0p26` (`/mnt/drm`, vdfs). The card's p26 is blank, so the superblock check fails with `SB: bad signature - , expected - VDFS`, and the mount reports `wrong fs type`. Then `Cannot restore /mnt/drm` and the `/mnt/drm/securestorage/backup` writes fail read-only.
- Fallback: `psa_mount.sh` runs `mkfs.vdfs /dev/mmcblk0p26` from `/usr/bin/mkfs.vdfs`. The guest printed nothing for it and the retry mount failed.
- Cause in the extraction (resolved in `vdfs-unpack`): the firmware's `usr/bin/mkfs.vdfs` is a VDFS1002 `IGzp` file. The codec used to accept only the `C` marker, so it wrote the raw gzip members, and the chunk table sits 320 bytes before the descriptor. Fixed in `vdfs-unpack` (`locate_chunk_table`, 64 KiB padding search). The restored file is a 1,316,539-byte ARM EXEC ELF, interpreter `/lib/ld-linux.so.3`. The earlier "ELF at 0x20000" finding was an artefact of decoding the members as one gzip stream and is withdrawn.
- Rootfs: `working/filesystem-6` is now the fixed VDFS1002 restore (the old raw-gzip tree was replaced without a backup, at your request). `usr/bin/mkfs.vdfs` is a 1,316,539-byte ARM ELF, mode 755.
- Fixed in `vdfs-unpack`: the restore now applies record modes (`0x81ed` for `mkfs.vdfs`) and restores symlink records as real symlinks. Before, `bin/sh` was a 4-byte file containing `bash`, and `lib/ld-linux.so.3` was a file containing `ld-2.18.so`, which broke the ELF interpreter. With links, `qemu-arm -L <root> <root>/usr/bin/mkfs.vdfs <image>` runs and writes a `VDFS1002` superblock to a blank 256 MiB image.
- Guest independence: the guest runs from p22 (`section-6.bin`), so the host-side restore modes and links do not change the guest's `mkfs.vdfs`. The rebuilt card (`mkcard.py`, 22:47) boots the same way: `wrong fs type` on p26, no `mkfs` output, no `Cannot restore`.
- Open: why `psa_mount.sh` produces no output for `mkfs.vdfs` in the guest. The `Try to execute non-auth file` lines for `ld.so.preload`/`ld.so.cache` (~59 s) are the next lead to check.
- Open: a formatted p26 needs a VDFS superblock. Confirm `psa_mount.sh` runs the fixed `mkfs.vdfs` and that the mount then succeeds.
- Finding (`/tmp/p26-modes.log`, about 4.4 s): the `git branch: vdfs3...` banner appears right after the first failed mount, so the guest does start `mkfs.vdfs`. The second mount still reports `bad signature`, so the superblock is not written, or is not written where the mount reads it.
- Finding: host-side `qemu-arm -L <root> mkfs.vdfs` on a carved copy of the card's p26 (12288 sectors, from `sfdisk`) exits 0 and writes `VDFS1002` at offset 1024. So the binary and the partition size are not the cause.
- Finding: the `Try to execute non-auth file` denials for `ld.so.preload`/`ld.so.cache` appear at about 59.5 s (`p26-modes.log` lines 2489–2490), long after mkfs ran at about 4.4 s. They do not explain the p26 failure. They are a separate exec-check lead (`check_execution_available`, VA `0xC01759D4`, in `03-uImage`).
- Open: why the guest's mkfs run leaves p26 without a superblock.
- Finding (guest boot, `snapshot=on` and a writable copy, full sequence to about 340 s): the first mount fails at 213 s, `git branch` banner at about 214 s, mkfs prints nothing else, then the second mount fails at 339 s and `Cannot restore /mnt/drm` follows. The writable copy's p26 is still all zeros after the run. So the guest's mkfs does not write a superblock to `/dev/mmcblk0p26`. The host-side mkfs does, so the cause is in the guest.
- Finding: mkfs's stderr is empty in the guest. The exit status is not logged by `psa_mount.sh`, so a failing exit cannot be seen from the console.
- Finding: the `non-auth` denials did not appear in the run to 415 s (`/tmp/p26-snap.log`), though they did in the earlier run. Treat them as nondeterministic.
- Finding: the boot does not stall. It runs slowly, with guest time at about 99 s after 240 s of wall time. The run reaches `[SELP] while loop ... please attach T32` at 415 s, which matches the earlier T32 attach hang.
- Done: the eMMC model writes guest buffers to the card image. `sdp1406_mmc_store` (`freesung-qemu/hw/arm/sdp1406.c`, around line 1425) runs for IDMAC writes and calls `blk_pwrite`. CMD24/CMD25 set the LBA position like CMD17/CMD18. The device requests `BLK_PERM_WRITE` on the `-drive` backend unless it is read-only. Regression test: `test_single_write_stores_the_buffer_in_the_addressed_sector`. Whether this clears the SELP hold and lets p26 mount is not yet verified by a boot.
- Reproducibility: the workspace is now detached at `/tmp/freesung-iso` (`bin/qemu-system-arm`, `boot/03-uImage`, `boot/05-dtb.bin`, `boot/sysmem-table.bin`, `boot/card.img`), with SHA-256 hashes in `MANIFEST.sha256`. A boot from this copy reproduces the same sequence: first mount fails at about 212 s, mkfs banner, second mount fails at about 338 s, `Cannot restore /mnt/drm`, p26 still zeros, `[SELP] ... please attach T32` at about 403 s.
- Open (boot run with the replaced rootfs, probe-artifacts card, headless `-serial file:`): the p26 mount still fails with `wrong fs type`, and userspace enters a crash/restart loop. From about 20 s, `kfactoryd`, `vendor-config`, `tv-viewer`, `avocd`, `tvs-main-loop`, `TIFADaemon`, `tvtimer-manager` and `tvs_deamon` take SIG 11 or SIG 6, each followed by a `Latch-up` restart. Each crash prints a `PC, LR MEMINFO` dump with PC `0x42904524`. The card's root partition comes from `section-6.bin` and not from the restored tree, so the swap does not change the image the guest runs. The swap cannot explain this run: the card (`probe-artifacts/mmc/hawkm-card-root.img`, 22:15) predates the tree swap (`working/filesystem-6`, 22:30), and the card's p22 comes from `section-6.bin`. The crash pattern is not new. Earlier production logs in `/tmp` show it too: `official.log` (8 Latch-up, 11 SIG 11), `prod-run1.log` (13 Latch-up, 17 SIG 11). Correction: an earlier note said the guest never reaches `mkfs.vdfs`. The `git branch` banner in `/tmp/p26-modes.log` at about 4.4 s shows that it does (see the finding above).
