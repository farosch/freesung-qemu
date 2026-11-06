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

## 6. i2c CH:7 timeout (open)

- Boot logs `[i2c fail] CH:7 Device ID:0x40 timeout`, and it still does with the CH:7 IRQ gating
  fix (boots now reach userspace). Slave devices are not modelled
  (DEVELOP.md). Next: model the slave at address `0x40` or find why the driver does not get the ACK.
- Another agent's binary `/tmp/fsqemu/qemu-i2c-build/qemu-system-arm` prints no serial output
  with the arguments that boot `qemu-10.2.1`. Not investigated.

## 7. Network: `eth0` status conflicts (open)

- DEVELOP.md says the production kernel registers `eth%d`. The older network notes said `eth0`
  never appears in the serial log. The IRQ fix (cell 5 = GIC 37) is in place, and a plugin trace
  (`probe10.log`) showed `register_netdev` returning.
- Open: which is current. Next: one current boot, then check serial for `eth0`, `sdp-gmac`, and
  `Clk ... IRQ is`. If absent, look at `synchronize_irq` (`0xc008baac`) and the IRQ handler
  (`0xc0023218`).

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
