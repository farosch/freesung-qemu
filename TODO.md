# TODO

Open investigation leads for the SDP1406 (HawkM) QEMU board. Use the production kernel
`03-uImage` only. Current verified behaviour is in [DEVELOP.md](DEVELOP.md).

## 1. Firstscreen waits for the kdbus bus (resolved)

- The 240 s baseline on the fixed build prints `Kdbus system bus found!` and launches
  `org.volt.firstscreen`. The wait no longer blocks; see item 2 for the next blocker.

- `org.volt.firstscreen` never reaches EGL. Its fds show log devices, `/dev/snd/controlC0`,
  `/dev/bptime`, one unconnected unix socket, and inotify. No `umplock`, `mali0`, `dri`, or `fb*`.
- Its `main` polls `/tmp/xorg_ready` (`0x9406`, satisfied) and then loops with 50 ms sleeps on
  `access("/dev/kdbus/0-kdbus-system/bus")` (string `0x9a34`, loop `0x9430`). That loop is the blocker.
- Production `init.wrapper` runs systemd and exports `DBUS_SYSTEM_BUS_ADDRESS=kdbus:...`.
  `dbus.service` runs `dbus-daemon --address=kdbus:`.
- Scratch divergence (not production): `etc/switch-init.conf` blanks `INIT`, so sysvinit runs and
  never starts dbus.
- Scratch dbus-daemon test: the daemon is started by `rc3.d/S98kdbus`. `/dev/kdbus` contains only
  `control`. The bus node is never created. The daemon's stderr and stdout are empty and no process
  remains. The cause is unknown.
- One boot stalled at `waiting firstscreen launch` before rc3. It did not recur.
- Next: capture the daemon's exit status, or boot the production systemd path.

## 2. No framebuffer device is registered (open, current firstscreen blocker)

- On the fixed build the firstscreen process reaches `Clutter-CRITICAL: Cogl-Renderer could NOT
  connect`, aborts, and `firstscreen.sh` relaunches it. This is the first blocker after kdbus.
- `firstscreen.sh` and the apps export `DISPLAY=:0`, so firstscreen targets X11, not fbdev.
  `/usr/bin/Xorg :0` starts from the xserver init and prints `/tmp/xorg_ready` at about 10.8 s.
  Firstscreen first fails at about 89 s, after `[CoreGL] Driver GL version 2.0`. Later
  `ecore_x_init() XOpenDisplay is failed` appears at about 167 s. `rc.sysinit` only chowns
  `/dev/fb0` and never creates it. The `fastvideo` `FB:INIT` run finishes without an fb device.
- Scratch squashfs root (systemd path, `/opt/var/log` tmpfs, 300 s): Xorg writes its log to
  tmpfs, `/tmp/.X11-unix/X0` stays up, and firstscreen launches once and runs with no Cogl or
  `XOpenDisplay` error. A control with the same squashfs and no tmpfs gives the same result, so the
  tmpfs is not the reason.
- The production VDFS root (`card-own.img`, same kernel and DTB, 300 s) still reproduces the abort:
  `Cogl-Renderer could NOT connect`, then `XOpenDisplay is failed`. So the squashfs root masks the
  bug. The extracted tree has no xattrs (`os.listxattr` is empty on `Xorg`, `firstscreen`, and
  `xserver`), so the squashfs cannot carry the Smack labels that production has. That is the likely
  cause. Do not use the squashfs root to judge firstscreen until its labels match.
- Side finding: `smack-watchdog` aborts at about 64 s (`pure virtual method called`,
  `Cannot add event log`). This is not firstscreen. Unverified whether it matters on production.
- Open: whether production firstscreen's Cogl failure is an X connection failure or an EGL/fb0 one.
  The Xorg log from the production root is still unread.
- Also seen: many `could not load module /lib/modules/3.10.30/kernel/...: No such file` lines
  (evdev, snd, regmap, hid). This may be a card or rootfs issue, not a model issue.

- Runtime: `/dev/fb0` and `/dev/fb1` return ENOENT. `/proc/fb` and `/sys/class/graphics` are empty.
  The console is `colour dummy device 80x30`. `register_framebuffer` (`c01f67c8`) was never hit.
- libEGL (`usr/lib/driver/libEGL.so`): `__egl_platform_display_search_and_create_map` (`0x28fec`)
  opens `$FRAMEBUFFER`, defaulting to `/dev/fb0` (`0x29018`). On failure it returns NULL
  (`0x29150`), with no fallback. A bare `mknod` would pass `open` but fail `FBIOGET_VSCREENINFO`
  (`0x29028`), since no `fb_info` exists. A scratch-only `mknod` test is not a fix.
- Static: `register_framebuffer` has one caller, `drm_fb_helper_initial_config` (`c0221654`),
  reached only from `drm_fbdev_cma_init` (`c0223d38`), which has no caller. No module imports a
  framebuffer registration API.
- Open: which production path creates fb0, or whether firstscreen should use another display path.
  Next: compare with the production image's `/dev` and modules on systemd.

## 3. The scanout stays black (open)

- Scanout `0x3c500000`, 1920x1080 XRGB8888. Screendumps at about 140 s are all zero.
- Fixed: the Xorg cursor abort at about 37 s (store to PA `0x22600000`). The model maps
  `0x22600000`-`0x227fffff` (see DEVELOP.md).
- Blocked by items 1 and 2 (no fbdev, and the firstscreen wait). Recheck the scanout after both.

## 4. `system_server` aborts on the TV power path (open)

- Sequence (run A, 170 s): frc_tcon submicom `0xd1` retries 0-4 (96.5-104.9 s), then `-512`
  (freeze for suspend) retries 5-9 (105.4-105.8 s), then `[VDLP COREDUMP] SIGNR:6` at 107.8 s.
- Abort site, symbolized against `libtvsystem.so.0.1` (linked base `0x452b8000`): PC is the entry of
  `TDsSystem::SetPower` (`0x4531fc94`). LR is inside `tv_system_set_power` (`0x453600d4`).
- The earlier chain `device_set_property` → `display_set_prop` → `OEM_sys_set_lcd_power` → ioctl
  `0xc00466ad` is not the abort site. The plugin base changes per boot, and the gdb breakpoint did
  not read the hit CPU's memory.
- Open: whether `-512` causes the abort or is a result of a freeze request. `-512` is
  `-ERESTARTSYS`. The static check found no `freeze for suspend` string in the rootfs, but the
  frc_tcon error path is in its module (`submicom[0x%x:%d]retry` only). Resolve the string source
  first.
- Needed: a healthy-micom control. The repo has no micom responder, so `-512` cannot be removed yet.

## 5. Micom device registration (open)

- `device3` (`ontimer-wt61p807`) logs `did not registered completely` and registers late.
  Not shown to block video.
- The `0xd1` command goes through `sdp_micom_send_cmd_ack` → `sdp_messagebox_write`
  (mailbox), not UART.
- Boot reports micom `Timeout error` lines even with the mailbox replies modelled.
- Open: which sender writes the first request frame. The reply byte matches the armed tag under
  gdb, so the waiter does complete.
- Found the `0xa0` sender (gdb stack walk at the first send): `jackident_thread` in
  `tztv-jackident.ko` (module base `0xbefa7000`, return `0xbefa7ee4`). It calls
  `sdp_micom_send_cmd_ack` (kernel `0xc0266568`) with `r0 = r1 = 0xa0`, `r3 = 6`, and a 5-byte
  reply buffer. The driver then tests bit 5 of reply byte 0. Chain: `0xc0266568` →
  `0xc0266514` → `0xc02662a0` → `0xc0265fdc` (retry) → `0xc025ab10` (TX, timeout log).
- Fixed: the `0xa0` reply tag was `0x29`, so the RX completion check at `0xc0265e34` (reply tag
  must equal the pending ack) rejected it and the `0xa0` request timed out. `sdp1406_mbox_ack_for`
  now echoes `0xa0`. Boot (20 s run) no longer logs `Timeout error - command 0xa0`. The
  `device3 did not registered completely` line remains; it is still open.
- Ack path (kernel `03-uImage`, module `tztv-jackident.ko`):
  - Module `ec0`-`ee0`: `r0 = 0xa0` (cmd), `r1 = 0xa0` (ack, via `mov r1, r0`), `r2 = 0`,
    `r3 = 6`, 5th arg = reply buffer at `ip+100`; `blx sdp_micom_send_cmd_ack`.
  - `0xc0266568` forwards `r0`-`r3` unchanged to `0xc0266514`.
  - `0xc0266514` loads ctx `r4 = *(0xc0631628)` and calls `0xc02662a0(r0..r3)` when `ctx[64] == 1`.
  - `0xc02662a0` saves `r1` (the ack) and stores it at `0xc026631c`: `strb r2, [r4, #20]`.
    So `ctx+20` = ack = `0xa0`.
  - Other writers of `ctx+20` clear it to `0`: `0xc0266464`, `0xc02664b0` (timeout/error),
    `0xc0265e68` (after a matched reply).
  - RX check `0xc0265e34` (inside `0xc0265a38`): accepts the reply only if `buf[6] == ctx[20]`,
    then copies 5 bytes from `buf+7` to `ctx+40`. `0xc0266370` and `0xc0266720` pass `buf = base+32`.
  - Open: which wire byte of the 9-byte model frame lands at `buf+6`. The model sets both byte 2
    and byte 8 to the tag, so `0xa0` passes either way. If `buf` starts at wire byte 2, the check
    is against byte 8, which may be a checksum. This is not yet verified.

## 6. i2c timeouts (CH:7 fixed; CH:4 intermittent; CH:5, 6, 10 unverified)

- Current state (clean build, 20 s boots): three runs. CH:7 logs only the designed `0x40` NACK
  (`incomplete xfer (-6)`, 135-150 per boot). `0x40` is the Sii9777 HDMI bridge, which NACKs by
  design (item 16). The `Sii9777-I2C` write errors follow from it.
- Open: the single CH:4 `0xa0` timeout (EEPROM, run 2 of 3). Not reproduced in the other runs. CH:4 `0xa0` timed out once (run 1 of 3). CH:5,
  CH:6 and CH:10 logged no timeouts in these runs (earlier timeouts predate the read-path fix).
- Re-verified on `boot-a0.log` (128 s, after the `0xa0` fix): 134 CH:7 NACKs and zero CH:4, 5,
  6 or 10 timeouts. The CH:4 timeout is still intermittent and not reproduced in this run.
- Read path and latch are in DEVELOP.md. The earlier trace below predates them.

- Boot logged `[i2c fail] CH:7 Device ID:0x40 timeout`. Slave devices are not modelled
  (DEVELOP.md).
- Root cause: the model treated `+0x18` (shared IRQ block, `0xfe0901f8`) as an enable. The
  driver clears CH:7 bit 7 when it starts a transfer and sets it when the transfer ends, which
  is a mask. With enable semantics the completion of a transfer started with the bit clear
  never raised SPI 47, so the waiter timed out. The model now treats a set bit as masked
  (`sdp1406_i2c_update_irq`). Qtests cover masked, unmasked, unmask-while-pending, and the
  driver's start/complete sequence.
- Other fixes that went in first:
  - A DS (data byte) write completes asynchronously, like the STAT start write, so the exit path
    of the IRQ handler (`0x80293cf0`) does not clear the completion before the next byte.
  - Address NACK: a STAT write with busy clear opens the address phase. The next DS byte is the
    raw 7-bit address. `0x40` is NACKed (LRB set), other addresses are ACKed. Qtests:
    `test_address_0x40_is_nacked`, `test_address_0x22_is_acked`,
    `test_data_byte_after_nacked_address_is_not_an_address`.
- Model now covers all 16 DTB controllers (two groups, see DEVELOP.md). Qtests:
  `test_ch4_completion_raises_spi_44_on_group0_bit4`, `test_ch8_completion_raises_spi_48_on_group1_bit0`.
- Result (20 s full-root boot, group model): CH:7 logs only `incomplete xfer (-6)` NACKs (49).
  Timeouts remain on CH:4 `0xa0` (5x), CH:5 `0x18`/`0x80`/`0xc0`/`0xcc`/`0xce` (5x),
  CH:6 `0x44`/`0x54` (6x), CH:10 `0x34`/`0xc8` (2x). Those channels fall into the EEPROM and
  sensor drivers, which then log `eep_reg_read` errors.
- Trace of CH:4 (temporary prints in a scratch build, not in the repo): the driver writes STAT
  `0xd0`, DS `0xa0`, STAT `0xf0`; the completion sets bit 4 and SPI 44 rises. The driver acks
  with `0x1901f4` = `0x10`, sends `0xf2`, `0x00`, and then the read address `0xa1` with STAT
  `0x90`/`0xb0`. The last completion's IRQ is raised after the driver's ack and the timeout is
  reported. The ordering between the driver's ack and the next transfer's completion is still
  unexplained.
- Facts from the trace (gdb breakpoints at the handler branches, `0xc02938fc` entry):
  - `[r4,#12]` is the `struct i2c_msg` cursor (12-byte stride at `0x80293cb4`). Its halfword
    at +2 is `flags` (bit 0 read, `0x1000` ignore-NAK). At +4 is `len`.
  - `[r4,#44]` is the controller (`0xfe0901e0`). `[r4,#48]` is the shared IRQ block
    (`0xfe0901f4`: status at `+0`, mask at `+4`).
  - The start function (`0x80294330`-`0x80294370`) clears the CH:7 mask bit with `bic` and sets
    CON `|= 0x40`. The `orr` sites that set it again are at `0x80293c7c` and `0x80294450`-`0x80294464`.
- Open leads:
  - Mask semantics are inferred from the driver's `bic`/`orr` pattern. Not verified on hardware.
    The reset value of `+0x18` is assumed to be 0 (unmasked).
  - CH:4 root-cause lead (GIC trace, failing boot): SPI 44 is GIC INTID 76. The model's line
    rises at 13.543 s when the driver's CH:4 completion lands. The driver acks bit 4 twice
    (`0x1901f4` = `0x10`) while a second completion is latched. The first ack re-raises the
    latch, and the second ack clears it before the driver reads CH:4 again. The CH:4 CON word
    keeps ACK (`0x140`), so the final-byte NAK path never arms, and the transfer times out at
    about 15.4 s. Open: whether the second ack is legitimate, and what the driver expects.
  - CH:4, CH:5, CH:6 and CH:10 still time out (see Result above). The IRQ handler reads each
    channel's own STAT and control words (`[r4,#44]`, `[r3,#4]`), so check whether the handler
    state for those channels matches the CH:7 path. Compare the byte sequence of the failing
    transfers (for example CH:5 `0x18`, CH:6 `0x44`) with the handler branches.
  - Confirm the `0x40` device really has no DT child (the DTB has children only at 0x22 and 0x2a).
- Another agent's binary `/tmp/fsqemu/qemu-i2c-build/qemu-system-arm` prints no serial output
  with the arguments that boot `qemu-10.2.1`. Not investigated.

## 7. Network: `eth0` status conflicts (open)

- Current boot (20 s, README command): `eth0` does appear in serial. The name is set by
  `vendor-config`, which logs `Set MAC Address of eth0 [00:00:00:00:00:00]`. The kernel rejects
  that address: `sdp-gmac 120000.ethernet: MAC Address is not valid! requested 00:00:00:00:00:00`.
  So the interface exists, but without a usable MAC.
- Fixed: the driver read MAC_Address0 (`0x040`/`0x044`), which the model did not implement. Reads
  returned 0 and writes were dropped. The model now backs those registers with the NIC address
  (qtests `test_mac_address_*`). The kernel still reports the zero address after the fix, so this
  was not the cause of the zero MAC.
- Cause: `vendor-config` reads the MAC from the EEPROM with `eeprom_read_mac` (offset `0x1e04`).
  The EEPROM is `s24c512c@50` on CH:4 (see item 6).
- Fixed: the model has a 64 KiB EEPROM slave on CH:4 `0xa0` (see DEVELOP.md). It holds a
  synthetic MAC `02:53:44:14:06:01` at `0x1e04`. A 20 s boot shows `Set MAC Address of eth0
  [02:53:44:14:06:01]`, and the kernel no longer reports an invalid address.
- Open: the EEPROM content is synthetic. Replace the MAC with the real device value if it is
  available (no EEPROM dump exists in the extracted tree).
- Open: the `samsung,eeprom-wp` GPIO (3,6) is not modelled.
- Not yet checked: the `Clk ... IRQ is` line (absent from this boot).

## 8. SELP hold and RCU stalls (partly resolved)

- Resolved for the default command: the `[SELP] ... please attach T32` hold was caused by an
  unbacked CI probe write at `0x90000` (`sdp_ci_probe`). Backing the four CI probe windows as
  RAM (DEVELOP.md) removed the abort and the hold in three 20 s boots with
  `SELP_ENABLE=00000000`. Boots longer than 20 s are not yet checked on this build.
- Older observations (before the CI fix), kept for reference:
- README's command uses `SELP_ENABLE=00000000`. That spun at `0xc0014e04` (`b .`) after
  `[SELP] while loop ... please attach T32`.
- `SELP_ENABLE=00000001` avoids the spin through about 97 s, but RCU stalls on CPU 1 at about
  71 s and 251 s. The `[SELP]` message still appears at about 54 s when the monitor stub is present.
- Open: what the SELP check tests, and whether `1` is the correct value for this device.
  Next: static read of the code at `0xc00148f0`, reached after `0xc0014dfc`.

## 9. TrustZone SMC (open)

- `tzdev.ko` issues SMCs from `tzdev_ipc_thread` (module offset `0x1b30`) and `0x664`. The first
  has `r0=0x14`. The loop waits with `tzdev:tzio_exchange_message: tzdaemon thread has been
  waiting 19 > 5 s`. `/usr/bin/tzdaemon` is present, but its start is not in the log.
  Re-verified on `boot-a0.log`: the wait count reads `7 > 5 s`, not 19. The count varies by run.
- The monitor stub returns `r0=0`. Open: whether the guest expects `0`, and whether the daemon
  is the real blocker.
- Boot-path caveat: the bootloader path faults on the SMC at `IFAR 0x200008` (see
  `freesung-bootloader/TODO.md`). The stub runs only on the control path.

## 10. Card p26 (`/mnt/drm`) superblock (open, runs conflict)

- Host-side `mkfs.vdfs` on a carved p26 writes `VDFS1002` at offset 1024.
  Re-verified: the shipped `hawkm-card-root.img` has p26 at LBA 5062241 with `VDFS1002` at byte 1024.
  This is the host image, not the state after a guest mkfs.
- One run reported a guest mkfs superblock after the first failed mount. Another run, on a writable
  copy, found p26 still zero after the guest mkfs, and `Cannot restore /mnt/drm`.
- `psa_mount.sh` does not log the mkfs exit status, so a failing exit is not visible.
- The `non-auth` denials for `ld.so.preload`/`ld.so.cache` (`check_execution_available`,
  `0xC01759D4`) are intermittent and not explained.
- Next: re-run one boot from a fresh copy, check the superblock, and log the mkfs status.

## 11. `mali.ko` and device nodes (open)

- `rc.sysinit` loads `/lib/modules/mali.ko`, but the real path is `soc/mali.ko`.
- `rc.sysinit` chmods `/dev/mali`, which the driver never creates (it creates `/dev/mali0`).
- The `chmod /dev/dri/*` line runs before `sdp_drm` loads, so its error is a timing artifact.
- Re-verified in `rc.sysinit`: line 39 loads `/lib/modules/mali.ko`, which is not at that path
  (the module is `kernel/soc/mali.ko`). Line 48 chmods `/dev/mali0`. Line 153 chmods `/dev/mali`.
  The `/dev/mali` line is the one the driver does not create.
- These are scratch-image observations. Check the production script before changing the model.

## 12. Clock registration error (open)

- `sdp_clk_register_fixed_div: failed to register clock emac_clk` at about 0 s. The error code
  is not printed. Static: the failing call is in the fixed-div loop (`0x804ede18`).
- Not checked against the eth0 item. Next: break on the return of `0x802e18f4` with its error value.

## 13. `fastvideo` framebuffer pointer (open)

- The MFC firmware fault is fixed (RAM2). `fastvideo` runs `FB:INIT`.
- Re-verified: `0xd6c00000` is not present in `usr/bin/fastvideo` as a raw little-endian word or
  as ASCII. The earlier claim that it appears in the ELF symbol table was wrong. The pointer is
  computed at runtime, or lives in a shared library. Open: which register or buffer it reads, and
  why it faulted there.

## 14. Initrd `/init` cannot be executed (open, low priority)

- The kernel reports `Failed to execute /init` with no errno, for both the probe init and a
  freestanding one.
- This route is only needed for a root shell. Production runs systemd, and `console-shell.service`
  (`WantedBy=getty.target`) is not pulled in by the default target. The route would need a
  `bootmode.target.wants` link in a bind-mounted `/etc/systemd/system`.
- Next: find the initramfs or ELF exec failure before using this route.

## 15. Secondary-core path (open)

- All cores run `_start` in the bare-metal UART program. It parks cores other than CPU 0, but the
  Linux secondary-core path is not checked.


## 16. Sii9777 HDMI bridge writes fail (open)

- After the CH:7 timeout, the Sii9777 driver logs write errors at SubAddr `0x7062` (slave `0x40`).
  Re-verified on the 128 s boot (`boot-a0.log`): 34 `7062` lines, not about 15. Slaves are not modelled on CH:7, so this may be expected.
- Open: check whether these failures block firstscreen or only add noise.

## 17. Vendor-config WIFI_VENDOR read fails at boot (open, lead for net-config watchdog)

- Boot log (`boot-a0.log`): `Set MAC Address of eth0 [...][vendor-config] Error: Fail to get
  SYSTEM_INFO_KEY_CHECK_WIFI_VENDOR value` at 8.2 s (line 320), then `Failed to get system-info`.
  `wpa_supplicant` has the same string (`[WPAS] Fail to get ...`).
- Key: `db/factory/FC_STORE_LEVEL_FIXED/WIFI_VENDOR`. It is read by `libcapi-system-info`
  (`system_info_check_wifi_vendor`) through the factory-data store. The name is present in
  `usr/share/factory-data/Factory_Part1.dat` (byte offset 10219835), and that file is inside the
  guest root image, so the guest can see it.
- The boot runs in `non-factory production mode` (line 20). `kfactoryd` starts (line 64) and
  reports unset values such as `id_tuner[-1]` and `sif_carrier_mute(-1)` (lines 185–190) before
  `vendor-config` runs. The WIFI_VENDOR value is probably not loaded into `kfactory` in this mode.
- Root of non-factory mode: `usr/bin/automation-helper` (run from `fb.sh`) prints
  `FP_NVRAM_FASTBOOT_IN_PRODUCTION [ff]` (boot log line 16). The value is read with
  `ioctl(fd, 0x80044906, {cmd=0xf212, len=1, buf})` on `/dev/eeprom`. A `0xff` result selects
  `non-factory production mode` and skips `/tmp/FACTORY_PRODCUTION`. The byte is not at a fixed
  EEPROM offset in `libeeprom-map`. The kernel `eep` driver (`eep_ioctl`, `eeprom_dev_read`, cache
  `eep_cache_read`, string `nvram_size`) decodes `0xf212`. The DTB has no NVRAM node.
- Open: which EEPROM offset the kernel's `eep_ioctl` maps `0xf212` to. The kernel has no symbols.
  Searches for the `eep_ioctl` and `invalid cmd %d` strings (`0xc0480fb0`, `0xc0480ff9`) found no
  literal-pool word, no ARM `movw/movt` pair, and no Thumb-2 `movw/movt` pair in `03-uImage`.
- Resolved by runtime trace (host gdb on `sdp1406_i2c_eeprom_write`/`_read`, `eep-trace.gdb`):
  the `0xf212` ioctl sets the EEPROM pointer to `0xf200` (write bytes `0xf2 0x00`), then reads
  128 bytes from there. The flag is EEPROM byte `0xf212`. The synthetic EEPROM holds `0xff`.
- Branch (`automation-helper` at `0x87f2`–`0x885c`): flag `== 1` takes the production branch and
  creates `/tmp/FACTORY_PRODCUTION`, so `fb.sh` starts `TIFADaemon`, `tiny_tifa` and `avocd`.
  Any other value (including `0xff`) prints `non-factory production mode` and creates
  `/tmp/AFTER_PRODUCTION`. The real retail value is unknown: no EEPROM dump exists.
- Changed: the synthetic EEPROM now holds `0x01` at `0xf212` (`SDP1406_I2C_EEPROM_FLAG`), so
  the boot takes the factory production branch. Test: `test_factory_flag_at_0xf212_is_0x01`.
  Unverified on boot: no kernel or card artifacts are available to measure a 20 s boot.
- Open: why `kfactory` does not load the factory record (`/opt/dbspace/.factory.db` is absent
  from the rootfs, so the store must be built at runtime), and whether the record value in
  `Factory_Part1.dat` is valid.
- Open: the link to the `net-config` watchdog kill (item 5 / coredump trace) is not confirmed. The
  kill is a systemd watchdog SIGABRT while `net-config` is idle in `__poll` (`PC 0x4292a6f4`). The
  `WIFI_VENDOR` failure precedes it, and `msg-wt61p807 ack[30] timed out` (line 322) is the micom
  command the failing read depends on. A gdb breakpoint on `__poll` did not fire: the pid changes
  between boots (489 in one run, 498 in another), so a fixed stack filter misses it.
- Not yet checked: which micom command `0x30` is, and whether it returns the wifi vendor.

## Dropped

- The audio DSP abort (`0xc0184438`) was seen only on the removed probe kernel. It did not
  reproduce on the production kernel and is not an open lead.
- Earlier micom and UART-responder leads are removed. The responder and its tests were deleted.
