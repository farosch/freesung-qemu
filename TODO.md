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

## 6. i2c timeouts (CH:7 fixed; CH:4 intermittent; CH:5, 6, 10 unverified)

- Current state (clean build, 20 s boots): three runs. CH:7 logs only the designed `0x40` NACK
  (`incomplete xfer (-6)`, 135-150 per boot). CH:4 `0xa0` timed out once (run 1 of 3). CH:5,
  CH:6 and CH:10 logged no timeouts in these runs (earlier timeouts predate the read-path fix).
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

## 8. SELP hold and RCU stalls (open)

- README's command uses `SELP_ENABLE=00000000`. That spins at `0xc0014e04` (`b .`) after
  `[SELP] while loop ... please attach T32`.
- `SELP_ENABLE=00000001` avoids the spin through about 97 s, but RCU stalls on CPU 1 at about
  71 s and 251 s. The `[SELP]` message still appears at about 54 s when the monitor stub is present.
- Open: what the SELP check tests, and whether `1` is the correct value for this device.
  Next: static read of the code at `0xc00148f0`, reached after `0xc0014dfc`.

## 9. TrustZone SMC (open)

- `tzdev.ko` issues SMCs from `tzdev_ipc_thread` (module offset `0x1b30`) and `0x664`. The first
  has `r0=0x14`. The loop waits with `tzdev:tzio_exchange_message: tzdaemon thread has been
  waiting 19 > 5 s`. `/usr/bin/tzdaemon` is present, but its start is not in the log.
- The monitor stub returns `r0=0`. Open: whether the guest expects `0`, and whether the daemon
  is the real blocker.
- Boot-path caveat: the bootloader path faults on the SMC at `IFAR 0x200008` (see
  `freesung-bootloader/TODO.md`). The stub runs only on the control path.

## 10. Card p26 (`/mnt/drm`) superblock (open, runs conflict)

- Host-side `mkfs.vdfs` on a carved p26 writes `VDFS1002` at offset 1024.
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
- These are scratch-image observations. Check the production script before changing the model.

## 12. Clock registration error (open)

- `sdp_clk_register_fixed_div: failed to register clock emac_clk` at about 0 s. The error code
  is not printed. Static: the failing call is in the fixed-div loop (`0x804ede18`).
- Not checked against the eth0 item. Next: break on the return of `0x802e18f4` with its error value.

## 13. `fastvideo` framebuffer pointer (open)

- The MFC firmware fault is fixed (RAM2). `fastvideo` runs `FB:INIT`.
- `0xd6c00000` appears only in its ELF symbol table, so the framebuffer pointer is computed at
  runtime. Open: which register or buffer it reads, and why it faulted there.

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

- After the CH:7 timeout, the Sii9777 driver logs write errors at SubAddr `0x7062` (slave `0x40`),
  about 15 times per boot. Slaves are not modelled on CH:7, so this may be expected.
- Open: check whether these failures block firstscreen or only add noise.

## Dropped

- The audio DSP abort (`0xc0184438`) was seen only on the removed probe kernel. It did not
  reproduce on the production kernel and is not an open lead.
- Earlier micom and UART-responder leads are removed. The responder and its tests were deleted.
