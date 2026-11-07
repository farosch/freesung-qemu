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

- `device3` (`ontimer-wt61p807`) logs `device3 did not registered completely` once, at 0.235 s
  (`/tmp/item5-boot.log`). Under gdb (`/tmp/fsqemu/dev3.py`, scratch): the first idx-3 lookup
  finds an empty slot and drops the event (no callback). It comes from the micom interrupt path
  (`0xc026596c` ← `0xc0265e2c` ← `0xc026694c` ← `0xc025aa6c` ← `0xc008c594`). Registration of
  idx 3 (`0xc02659e0`) follows; every later idx-3 lookup finds the slot filled. Success is not
  logged, so only gdb shows the late registration.
- Static (`03-uImage`, format string `device%d did not registered completely` at VA `0xc0481f6b`):
  the pointer is the literal at VA `0xc02659c0`, used by the function at VA `0xc026596c`. That
  function looks up the device slot at `[ctx+76+idx*4]` and prints the message when the slot is
  empty or its index does not match. The registration store is in the function at VA `0xc02659e0`.
- Dropped event (gdb, scratch `/tmp/fsqemu/kind.py`): every idx-3 lookup gets the same stack buffer
  `r1 = 0xcb95feac` built by the parser at `0xc026694c`. Layout: u32 0 at +0, u16 length 6 at +4,
  command byte at +6 (`0x29` for the dropped event; later accepted events show `0x50`, `0x51`, `0x20`).
  Byte 5 is the high byte of the length (always `0x00`).
- Consumer: dispatcher `0xc0265a38` switches on byte 6 (`0xc0265aec`). `0x29` takes the default arm
  `0xc0265e1c` and calls the lookup with idx = `r6` = 3. Byte 5 is read only by the `ldrh [r4,#4]`
  arms (`0xc0265c60`, `0xc0265d5c`, `0xc0265dc0`), none on this path.
- Device-3 handler `0xc025a160` (registered via `0xc02659e0`, `handler = [entry+8]`): on command `0x29`
  it does not match `0xa0` or `0x96`, and compares byte 6 with `[0xc063155c]`, which is `0x00` in every
  sampled call (`/tmp/fsqemu/handler.py`). So `0x29` is a no-op for the handler: no copy and no state change.
- Boot-order check (`/tmp/fsqemu/dev3.py`, 4 runs of 5 s, scratch): each run's first idx-3 lookup (n=1)
  finds an empty slot, device 3 registers at n=7, and later lookups find it filled. The serial log has
  one `did not registered completely` line per run. The order is stable in these runs. The earlier
  "registered before the first `0x29`" run used a different breakpoint set and is not reconciled with
  this one. The drop is benign only if the dropped event is harmless: the device-3 handler treats
  `0x29` as a no-op (see above).
- Post-lookup check at `0xc0265e2c` (dispatcher context `ctx`, `[ctx+20]` = pending reply type): a frame
  is copied to `ctx+40` and wakes `ctx+24` only if `[ctx+20] != 0` and `[ctx+20] == frame[6]`; then `[ctx+20]`
  is cleared at `0xc0265e6c`. Otherwise the frame is dropped as unsolicited (`/tmp/fsqemu/ctx20.py`).
- Send-and-wait `0xc02662a0`: `r0` = request command, `r1` = expected reply tag. It builds the 9-byte
  request `ff ff cmd payload checksum` (checksum at byte 8 = sum of bytes 2..7), stores the tag in
  `[ctx+20]` (`strb` at `0xc026631c`), then waits on `ctx+24` for up to 5 tries of 400 ticks. Return 0
  on wake; `-1` after 5 timeouts (`[ctx+20]` cleared at `0xc0266464`).
- Poll loop `0xc0259d68` (`state = 0xc0631534`): outer loop checks `0xc00532c0`, sleeps 1000 ms while
  `state[0] == 1`, then sends cmd `0x18` via `0xc0266500` (`mov r0,#24` at `0xc0259da4`, `mov r1,#0x29`,
  `bl 0xc0266500` at `0xc0259db0`) 10 times, 400 ms apart. Its result is ignored. If `state[100]` is set
  (set by the device-3 handler, see the next bullet), it calls `0xc0266044(0x96)` and clears the flag.
- Producer of `state[100]` (`0xc0631598`): device-3 handler `0xc025a160`, store `strbeq` at `0xc025a1fc`.
  It sets the flag only when byte 6 equals `0x96` and differs from `state[40]` (`0xc063155c`). `state[40]`
  is written by the sender at `0xc0259a3c` (`strb` at `0xc0259a58`) from its argument byte 1 (or byte 0 when
  byte 1 is 0). That sender has no direct `bl` caller in the image. The model never emits a `0x96` frame
  (no `0x96` in `hw/arm/sdp1406.c` or tests), so this branch is unreachable in QEMU today. The `0x96` callee
  `0xc0266044` uses a different object (`0xc0631628`), not the poll state.
- The `0x29` reply is the completion for cmd `0x18`. Its 5-byte payload is copied to `ctx+40` but no poll
  path reads it. The only reader is `sdp_micom_send_cmd_sync` `0xc0266560` → `0xc0266514`, and no in-kernel
  `bl` calls `0xc0266560`. Module callers are not in the image. The poll loop never copies `ctx+40`.
- Exported-name table at VA `0xc04c...` (kernel `ksymtab`, file address `0x804ac038`) maps `0xc02662a0`
  to `sdp_micom_send_cmd`, `0xc0266500` to `sdp_micom_send_cmd_ack_data`, `0xc0266514` to
  `sdp_micom_send_cmd_ack`, and `0xc0266560` to `sdp_micom_send_cmd_sync`. Hint only: the same table maps
  `0xc02659e0` to `sdp_micom_panel_key_lock`, which conflicts with the device-3 registration role, so
  the names are not evidence.
- Observed order (`ctx20.py`): the first `0x29` frame arrived with `[ctx+20] = 0` and was dropped; the poll
  loop then set `ctx+20 = 0x29`; the next `0x29` frame matched and was copied. Later `0x29` frames with
  `ctx+20 = 0` are dropped as unsolicited. So a `0x29` frame is consumed only inside a pending request window.
- Run-to-run order, from the four gdb logs: in `dev3-gdb.log` the first idx-3 lookup (n=1) finds slot `0`
  and drops the event before `REGISTER` (n=7). In `ctx20-gdb.log` and `trace29-gdb.log` the first `0x29` is
  dropped at `[ctx+20] = 0` (n=3). The mailbox reply is raised synchronously on the `SEND` write
  (`sdp1406_mbox_write`, `hw/arm/sdp1406.c` ~1368), but the guest sees it only when its IRQ path runs. So
  the drop happens when that IRQ runs after the waiter has cleared the tag (`0xc0266464`) or before the
  slot exists. This is a hypothesis: the IRQ-service timing is not yet traced under a fixed schedule.
- The kind-dispatch breakpoint (`0xc0265cc0`) never fired. The kind-`0xc6` attribution (`0xc026636c`) is withdrawn.
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
- Full send/lookup trace (`/tmp/fsqemu/send29.py`, scratch, boot inputs from `probe-artifacts`, gdb stopped by
  the 90 s limit at 515 events):
  - The first `0x29` lookup (n=1, `ctx+20 = 0`, before any tracked send) has no matching `SEND` in the log.
    The `SEND` breakpoint at `0xc02662a0` saw every request after it, so that frame's producer is still unknown.
    It is either a mailbox write that skips `0xc02662a0` or a stale reply, and it is not yet explained.
  - `SEND` (`cmd=0x18 tag=0x29`, n=4) is followed by a lookup with `ctx+20 = 0x29` (n=5). That reply matches and
    the waiter wakes (n=6), so the cmd `0x18` path works once the tag is armed.
  - Each `0x60`, `0x61`, `0x10`, `0x6a`, and `0x6e` `SEND` gets a matching lookup (n=9, 14, 19, 50, 56).
    `0x10` and `0x6e` also show a timeout clear.
  - Storm: after `SEND 0x6e` (n=25), 23 lookups (n=26-48) carry the same `0x29` frame with `ctx+20 = 0`. Each one
    wakes via `0xc0265e34` without a match and is dropped as unsolicited. The RX record should have changed to
    `0x6e` after that send, so this is inconsistent with the model. Explained below: an untracked `0xac` send
    rewrote the RX record to the default `0x29`.
  - Conclusion: the run-order question is still open. The model answers synchronously on `SEND` as designed.
    The guest-side mismatch between `SEND` and the RX frame it reads (the storm) needs a read of the RX record
    at each lookup: done, see the mailbox write trace bullet below.
- Mailbox write trace (`-trace memory_region_ops_write`, scratch `send29w-trace.log`) and the RX record read at
  each lookup (`monitor xp` at `0x00800400`, scratch `send29rx-gdb.log`) resolve the storm and the untracked first `0x29`:
  - The model answers every `SEND` (write to `0x480`) with the RX record from `sdp1406_mbox_ack_for()`. A command not
    in that list gets the default `0x29`, which is an assumption, not a verified reply.
  - Traced `SEND` commands (TX byte 2): `0x20`, `0x18`, `0x60`, `0x61`, `0x10`, `0x18`, `0x6a`, `0x6e`, then `0xac` x5.
    `0x20` and `0xac` never pass through the send-and-wait `0xc02662a0`. The first `0x29` (n=1) is therefore the
    default reply to an untracked `0x20`, which explains the "no tracked send" first drop. The `0xac` requests give the
    storm: every `0x29` lookup after `0x6e` is a default reply to an `0xac`.
  - Untracked sender: the function at VA `0xc0266694` (file `0x80266694`). It builds a frame
    `ff ff cmd ...` with a checksum and calls the retry path `0xc0265fdc` directly (`bl 0xc0265fdc` at
    `0xc0266824`), retrying through `0xc026682c`. It has four direct `bl` callers: `0xc02580cc`, `0xc0258478`,
    `0xc025952c`, `0xc0259a88`. The earlier "no direct caller" note was wrong.
  - Sender chain for `0x20` and `0xac` (runtime-confirmed for `0xac`, gdb `entry2-gdb.log`):
    - Userspace ioctl `0xc00cb301` on fd `0xb`, called from `0xc0011040`. Argument size 12 matches the item.
    - Enclosing function `0xc00df7d4` (no static caller), reached through an ops table.
    - Ops dispatch `0xc00df274` → `0xc00dec38`, which calls the object's `+32` op via `blx` at `0xc00dec64`
      (return `0xc00dec68`).
    - Dispatcher `0xc0259a98` copies the 12-byte item and resends it through the wrapper on each retry.
    - Wrapper `0xc0259a34` sets the command to `item[1] || item[0]`, passes payload `item[2..6]`, and calls
      the sender at `0xc0266694`.
    - Runtime items: `ac ac 00 00 ba 04 ...` (`0xac` storm) and `20 30 00 00 ba 04 ...` (`0x20`, seen in an
      earlier run with the same dispatcher caller; its ioctl was not captured in a 5 s run).
  - Userspace source (disassembly, `libmicom.so.0.1`, `/tmp/fsqemu/libmicom.dis`):
    - `micom_send_msg` (`0x44b9a510`) builds a 12-byte item `[0]=cmd, [1]=sub, [2..6]=payload` and calls
      `ioctl(fd, 0xc00cb301, item)`. `micom_send_msg_noack` uses `0xc00cb300`.
    - `micom_get_micom_version` (`0x44b9ebf8`) calls it with cmd `0x20`, sub `0x30` (the observed `20 30` item).
    - `micom_check_bluetooth_rc_pairing_required` (`0x44b9f204`) calls it with cmd `0xac`, sub `0xac`.
    - On ioctl success, `micom_send_msg` copies the 5 bytes at item offset 7..11 into the caller's out buffer.
      It does not compare the reply tag with the command.
    - Two other importers (`input-playback-daemon`, keyrouter `module.so`) call with cmd `0x1d`, sub `0x2e`.
      They do not send `0x20` or `0xac`.
  - Model consequence: `0x20` and `0xac` get tag `0x29` and an all-zero payload. The library reads the payload
    as data (version bytes, pairing flag), so the zero payload matters more than the tag.
  - Assumption (per AGENTS.md, validated by disassembly only): `0x29` is the tag the device sends. Not yet validated.
  - Kernel (`/tmp/fsqemu/k.dis`, addresses `0x80xxxxxx` = kernel VA `0xc0xxxxxx`):
    - Item sender `0x80266694`: wire byte 2 = item[6] (cmd), wire bytes 3.. = item[7..] payload, checksum is a byte sum.
    - Item cmd `0x20` and `0xc6` branch to `0x80265a38` (r2=3), not the generic wait.
    - Completion check `0x80265e34`: decoded reply byte 6 must equal ctx[20] (pending tag). On match, 5 bytes from reply offset 7 go to ctx+40.
    - Generic RX wait `0x80265fdc` retries on length only, not on tag.
    - ctx[20] (pending tag) is written only by the sender at `0x802662a0` (stores its `ack` argument at `0x8026631c`, cleared at `0x80266464`/`0x802664b0`). The item sender `0x80266694` never writes it.
    - Resolved: the item `0x20`/`0xac` path does not check a reply tag in the kernel. Its completion is the RX length check at `0x80265fdc`, and the reply is then dispatched by its byte-6 cmd at `0x80265a38`. So `0x29` is not required by the kernel for these items. The model's `0x29` default is harmless here, and the payload is what matters.
- Item 5 stays open. No `0x96` producer exists in QEMU, so the `state[100]` branch is still unreachable in the model.

## 6. i2c timeouts (CH:7 fixed; CH:4 and CH:10 are timing-dependent; CH:5, 6 unverified)

- Parallel survey (25 s each, VDFS root card attached read-only): 6 plain runs (two batches of 3)
  each logged CH:4 `0xa0` and/or CH:10 `0xc8` timeouts, 1 to 3 per run. The 6 `-icount shift=auto`
  runs logged none (only the designed CH:7 `0x40` NACKs). CH:5 and CH:6 never timed out. The
  split points at wall-clock coupling of the completion timer (`xfer_timer`, 100 us virtual) rather
  than a missing completion. The driver's wait is not verifiable here (no sdp-i2c driver source).
- Current state (clean build, 20 s boots): three runs. CH:7 logs only the designed `0x40` NACK
  (`incomplete xfer (-6)`, 135-150 per boot). `0x40` is the Sii9777 HDMI bridge, which NACKs by
  design (item 16). The `Sii9777-I2C` write errors follow from it.
- Open: the single CH:4 `0xa0` timeout (EEPROM, run 2 of 3). Not reproduced in the other runs. CH:4 `0xa0` timed out once (run 1 of 3). CH:5,
  CH:6 and CH:10 logged no timeouts in these runs (earlier timeouts predate the read-path fix).
- Re-verified on `boot-a0.log` (128 s, after the `0xa0` fix): 134 CH:7 NACKs and zero CH:4, 5,
  6 or 10 timeouts. The CH:4 timeout is still intermittent and not reproduced in this run.
- Log survey (current `/tmp/fsqemu` logs): `trace50-serial.log` has 0 timeouts and 135 CH:7
  NACKs. The only timeout in any surviving log is one CH:4 `0xa0` in `dev3-serial.log` (line 776,
  about 14.2 s). It comes right after a CH:7 NACK and an unrelated `msg-wt61p807` ack timeout. CH:5,
  6 and 10 have no surviving log evidence, so they stay unverified.
- Mechanism (model, `hw/arm/sdp1406.c`): each transfer completes on `xfer_timer`, a
  `QEMU_CLOCK_VIRTUAL` timer 100 us after arming. Without `-icount`, virtual time follows host
  time, so the completion order against the guest's wait depends on host load. That fits an
  intermittent result. Completions that arrive while the status bit is still set go to
  `irq_latched`, which is promoted only when the driver acks that bit.
- 40 s boot survey, 3 plain and 3 `-icount shift=auto` runs (logs `/tmp/fsqemu/item6-*.log`):
  no CH:4 timeout in any run. Plain run 1 logged `CH:10 Device ID:0xc8 timeout` at 5.5 s
  (line 320), the same timeout as the earlier `0x34`/`0xc8` reports. CH:5 and CH:6 never timed
  out. The `-icount` runs reached the same guest time (about 39.5 s) and showed no CH:10
  timeout. The sample is too small to separate the modes, so the host-timing hypothesis is
  still open.
- CH:10 identity: controller `i2c@00191040` (SPI 50, DTB node in `/tmp/fsqemu/dtb.dts`). The
  `0xc8` byte is the 8-bit form of 7-bit `0x64`, which is `panel@64`
  (`samsung,tztv-frc-sdp-main`). The model has no device at `0x64`, so it ACKs that address by
  default. The timeout coincides with `frc-tcon 10-0064: tztv_frc_tcon_i2c_read ... (-110)`
  and `Unknown TCON type` at 5.5 s.
- Qtest `test_ch10_completion_raises_spi_50_on_group1_bit2`: a `0xc8` write on CH:10 is ACKed,
  completes after one transfer time, raises SPI 50 and sets group 1 bit 2. The model does not
  NACK the FRC device, so the timeout in the boot is not caused by a missing completion.
- Qtest `test_ch10_read_address_is_acked_and_queues_data_completion`: a `0xc9` read address is
  ACKed and queues a data completion, as on CH:7. The model's read path looks correct, so the
  boot timeout is probably in the driver's wait. Open: CH:5, CH:6 (no surviving logs), the CH:4
  intermittency, and the FRC driver's read sequence (no driver source here).
- Per-byte timing from the disassembly (scratch builds, not in repo): the byte-poll loop at
  `0xc0294200` uses backoff `[r6,#104]`, which the transfer function only resets (never raises),
  so the poll runs with no delay. The sleep waiter budget is 2*len+250 jiffies. Plain boots with
  `SDP1406_I2C_XFER_NS` at 22500, 90000 and 100000 (2 runs each, 6 parallel) all logged CH:4 and
  CH:10 timeouts (22500: CH:4 6-7, CH:10 1-7; 90000: CH:4 5, CH:10 0-6; 100000: CH:4 2-3, CH:10
  1-2). Per-byte time alone does not remove them. The `fuzz_ch4.py 0 60` run (9 min) ended without a
  readable result.
- Poll-budget hypothesis (scratch instrumentation, not in repo): counted BUSY status reads per
  transfer and virtual time to completion. Across 26022 completions, 25441 had zero polls, and no
  transfer reached 400 polls. Completions land about 110 us after arming. The 400-iteration poll
  cannot expire before completion, so this race is ruled out. That boot logged no CH:4 or CH:10
  timeouts, so the instrumented run did not reproduce the bug.
- Waiter path (driver disassembly, not in repo): the waiter at `0xc0294330` sleeps in
  `schedule_timeout` for 2*len+250 jiffies and only prints the CH timeout when the budget expires
  with the remaining-work counter `[r6,#16]` still nonzero. The IRQ handler at `0xc02938fc`
  decrements that counter per completion. A timeout therefore means a completion stopped the chain
  for the whole budget. Event tracing (fprintf and in-memory ring buffer, 4 boots) produced zero
  CH:4 or CH:10 timeouts, against 2-8 per untraced boot. The bug is timing-sensitive enough that
  instrumentation hides it, so the stall has not been captured.
- Read path and latch are in DEVELOP.md. The earlier trace below predates them.
- Driver disassembly (kernel image, no source): the sdp-i2c probe is at virt `0xc028c934`, the
  transfer function at `0xc0293edc`. The wait is a guest busy-loop, not a timer or IRQ wait:
  a 400-iteration poll (`movw r9, #401`) of the status bit `0x20` at `reg+4`. Each poll calls a
  delay from a function table at `0xc0526dac` (`+8` is `0xc01cc050`, a loops-per-jiffy scaled
  busy loop) with a backoff value at `[r6,#104]` capped near 300000 us. Exhaustion returns
  `-ETIMEDOUT` (-110) and prints `[i2c fail] CH:%d Device ID:0x%x timeout` (at `0xc0294428`).
  Correction: the printed `[i2c fail] ... timeout` is not that poll budget. It is the sleep-wait
  path after the handler (`0xc0294414`-`0xc0294428`), which waits on a jiffies-scale timeout. A
  100 us completion cannot time that out by timing alone, so a timeout there means the waiter
  never saw the completion. The poll-budget race theory is unlikely.
- IRQ handler (`0xc02938fc`, exit at `0xc0293ce8`-`0xc0293d00`): the exit ack reads the shared
  status, keeps only the channel's own bit, and writes it back. That is a write-1-to-clear of
  the channel's bit, which matches `sdp1406_i2c_write` (IRQ_STAT). The mask `orr` sites
  (`0xc0293c7c`, `0xc0294450`-`0xc0294464`) are read-modify-writes of `+0x18`, not acks.
- Handler trace (4 parallel plain boots, gdb breakpoints at the handler entry, each path, exit
  ack and timeout print; scratch `/tmp/fsqemu/i2c-trace.py`, not in repo): the CH:4 handler
  reaches state 2 and exits with the ack, and the waiter prints `CH:4 0xa0 timeout` two trace
  events later with the status bit clear. The next CH:4 completion (status `0x10`) arrives at
  trace event 36. Correction: the waiter at 0xc0294330-0xc02943e8 sleeps with schedule_timeout
  for a budget of 2*len+250 jiffies, so the timeout means the remaining-work counter at
  [r6,#0x10] never reached zero. The earlier "late, not lost" reading was wrong: trace event
  counts are not guest time, so the gap cannot show lateness. Open: which handler state fails to
  decrement the remaining work for CH:4 (the state 2 read path at 0xc0293b9c, or the CON ACK
  clear that queues the final byte at 0xc0293ca0).
- Counter trace (gdb, plain boot, scratch `/tmp/fsqemu/i2c-trace2.py`): CH:4 reads a 128-byte
  EEPROM segment after a 2-byte address segment. The first timeout follows the state 1 to state
  2 entry for segment 1 with byte index 0. The state 2 read path and the final-byte CON clear are
  not reached. No further byte interrupt arrives before the waiter times out. The state 2 read
  path is not the stall. Open: why the byte 0 completion of the repeated-start read is not
  raised (candidate: a completion latched while the bit is still set, re-raised only on an
  IRQ_STAT write that does not come). Not confirmed. An instrumented build (scratch stderr
  tracing, removed) did not reproduce the timeout, so the trigger is timing-sensitive.

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

## 12. Clock registration error (explained, runtime not confirmed)

- `sdp_clk_register_fixed_div: failed to register clock emac_clk` at about 0 s.
- Static finding: the fixed-div table at kernel file offset `0x4f6134` (11 entries, stride 28)
  lists `emac_clk` twice. Index 5 is `emac_clk` with parent `pll11_emac` (mult 1, div 1). Index 10
  is `emac_clk` with parent `pll12_core` (mult 1, div 8).
- The second registration fails the duplicate-name check in `0x802e0c4c` (`__clk_lookup` at
  `0x802e06f0`). The check returns `-EEXIST` (-17) through `0x802e1068`, `0x802e11c0`, and
  `0x802e18f4`. The kernel does not print it. The first registration stands.
- Conclusion: the duplicate is in the vendor kernel's table, not in the QEMU model. No model change
  is needed. The message is benign on this firmware. The QEMU board model is unchanged.
- Not confirmed at runtime. The gdb breakpoints on `0x804ede50` never fired. The cause was a
  physical address with the MMU on; breakpoints must use the virtual address (see DEVELOP.md).
  Open: confirm the errno with a runtime trace if this error ever blocks a boot step.

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
- Boot-f212 run (after the `0xf212` flag change): `FP_NVRAM_FASTBOOT_IN_PRODUCTION [1]` now
  replaces `[ff]`, so the production branch runs. The `WIFI_VENDOR` error at line 317 is unchanged.
- Negative result: the mailbox reply for command `0x30` was changed to echo `0x30` (other
  commands echo the same way). A 90 s boot still printed `ack[30] timed out`. The change was
  reverted, so the model is unchanged. The driver's expected ack tag for `0x30` is not known.
- Open: which micom command `0x30` is, what tag its waiter expects (disassemble the
  `sdp_micom` wait path in `03-uImage`), and whether it returns the wifi vendor.
- Static wait-path findings (`03-uImage`, file offset = virtual - `0xc0008000`):
  - The `ack[%X] timed out` string (`0xc0481788`) is loaded at `0x80259b80` (`ldr` literal `0x80259cac`)
    in the retry loop at `0x80259a9c`. The first print site `0x802584d0` is a separate 6-try loop.
  - The loop prints on attempts 5 and 6 (`cmp r3,#4` / `cmp r3,#6`), with 400 ms waits (`mov r0,#400`).
    This matches the paired `ack[30]` lines about 0.4 s apart in the boot log.
  - Success check at `0x80259bec`: `ctx+70` must equal reply byte `fp-47` (or `fp-48` if `fp-47` is 0).
  - `ctx+70` is not a simple tag. It is byte 2 of a 32-bit word `sl` written at `0x802af2e0`.
    That word is the return value of a sub-call in the table-driven builder at `0x802aexxx`.
  - Builder entry is `0x802ad65c`. It reads a 72-byte record per request (`mul r0, r4, r0`, `#72`).
    Record count is capped at 64 (`cmp r3,#63` at `0x802ad738`). The 32-bit value at record `+0`
    is range-checked to 1..95 (`sub r2,r3,#1; cmp #94` at `0x802af294`).
    Each record calls a callback at ctx `+560` (`blx r3` at `0x802af268`) and stores the result as `sl`.
  - Checked: `+560` is a self-pointing list head initialised at `0x8013e464`-`0x8013e474`, not a
    callback. The `ldr r3,[r5,#560]` at `0x802af254` therefore reads a different struct than `r5`
    suggests. Not resolved.
  - Checked: no `bl`, `blx`, or `b` to `0x80259a9c` or `0x802ad65c` in `k.dis`. No data word holds
    their virtual addresses (`0xc0259a9c`, `0xc02ad65c`). The earlier pool search used the physical
    form, which is wrong for data pointers. Both are reached by a route the static search cannot see.
  - Runtime (virtual breakpoints, `/tmp/fsqemu/waitloop.py`): the wait loop `0xc0259a9c` fired
    with `lr=0xc00dec68`. That is the `blx r3` at `0x800dec64` in a dispatcher that calls
    `[ [r0+20] +32 ]`. The request is 12 bytes copied from user `r2` (`copy_from_user` at
    `0x80259b04`), so `r2` cannot be read at entry. The command tag is byte 1 of the record
    (not byte 0), which matches the boot's `ack[XX] timed out` tag. The builder `0xc02ad65c` fired with `lr=0xc02a3994`
    and tags `0x04`, `0x24`, `0x07`.
  - Captured the `0x30` request (run with a breakpoint after `copy_from_user` at `0xc0259b08`, which
    reads the kernel copy at `fp-48`). Record: `20 30 00 00 ba 04 00 00 00 00 00 00`. Byte 0 is
    `0x20`, byte 1 is the command `0x30`. A second record `ac ac 00 00 ba 04 ...` gave `ack[AC]`.
    `ack[30]` timed out twice in that run.
  - Success check `0xc0259bec` compares `ctx+70` with `fp-47`, which is record byte 1, so the
    waiter expects reply byte `0x30` for this request. The earlier success hit (`fp-47 = 0x50`)
    is consistent with that. The `0x30` request never reached the check (it timed out).
  - Builder reply path (static): the record's first callback is `blx r3` at `0x802af244`, target
    `[r5+624]`, with `r1 = r6`. Its return value is `sl`. A second callback `[r5+560]` at
    `0x802af268` overrides `sl` if non-null. `sl` bytes are stored to record-context `+68..+71`
    (`0x802af2d4`–`0x802af2f0`), so `ctx+70` is byte 2 of the callback return.
  - Runtime (`/tmp/fsqemu/waitloop.py`, breakpoints at `0xc02af248` and `0xc02af26c` after each
    callback): neither fired in a 170 s boot, and the `0x30` request timed out before the copy/check.
    So the builder's callbacks never ran for the wait that times out. The fault is upstream of the
    reply byte: the micom reply for `0x30` never reaches the waiter.
  - Open: why the `0x30` request never gets a reply through the mailbox (reply frame `0x404`–`0x41c`,
    pending `0x440`, clear `0x444`). The echo change for `0x30` did not fix it, so the next lead is
    the completion/interrupt path the wait loop depends on, not the byte value.

## 18. Stub windows: register meaning not confirmed (open)

- Every log-only stub in `hw/arm/sdp1406.c` came from the DTB or a boot fault. None has a
  disassembly-confirmed register meaning yet. The stubs only stop faults; they do not claim to be correct.
- Open per window (confirm by disassembling the driver that touches it):
  - `0x5c0000` (clkpin) and `0x5d0000` (osd): drm/HDMI init, fault at `0xfe4c6018`.
  - `0x5e0000` (unnamed): touched by the kernel; no DTB node.
  - `0x790000` (`hard790`): the uImage ioremaps it from `0xc03f1538` and stores at offset
    `0x3e0 + index * 4` (`0xc03f15a0`). Meaning open.
  - `0xb50000`, `0xb70000`, `0xb74000`, `0xb60000` (extin): kernel and audio driver touch them
    (`0x00b57058`, `auddec_init_tas` at `0x00b74004`).
  - `0xb00000` (uddec): data abort at `0x00b28004` without the block.
  - `0x900000`, `0x930000`, `0x170000`, `0xe00000`, `0xa00000` (demux, ddrbus, media): semantics
    open. Media status at `0xa04010` and `0xa08010` feeds `devAe_WatchDog`, which is not identified.
- Test: `test_qemu_sdp1406_stubs.py` checks that reads return 0, writes are not kept, and accesses
  are logged. It does not check register meaning.

## 19. Bluetooth controller in the model (open, not added)

- `btapp` launches through `btapp-service.service` (`WantedBy=starter.target`, user unit), which
  is not reached in a 20 s boot.
- The DTB (`05-dtb.bin`) has no Bluetooth node (`bluetooth`, `hci`, `bcm` absent). A model device
  would not bind in the guest kernel.
- Evidence from firmware: `libbt-vendor.so` names a Broadcom BCM4325-family chip, opens
  `/dev/ttyHS99` for UART, and controls power through `/sys/class/rfkill`.
- Candidate: DTB `serial@00190A40` (`samsung,sdp-uart`, `reg 0x190a40`, IRQ cell `0x39`,
  `status = "okay"`). The model does not map it. Not confirmed as the Bluetooth UART.
- Kernel check: the `sdp-uart` driver names its ports `ttyS%d`. No `ttyHS` string exists in the
  kernel image, the modules, or the rootfs `etc`/`lib`/`usr` trees except `libbt-vendor.so`.
  `/dev/ttyHS99` cannot be created by this kernel, so the vendor path is unused or from another
  platform. The chip UART would appear as some `ttyS<n>` if it is wired to `sdp-uart`.
- Long boots: 40 s and 50 s (`trace40/50-serial.log`) show no `ttyS` line and no starter or
  btapp-service start. `TIFSDaemon` (pid 636) crashes repeatedly (about 30 s, 39.7 s, and 6 times by
  50 s), with the same PC `0x428e1d20`. The crash blocks userspace progress, so btapp cannot start.
- TIFSDaemon crash cause: kernel reports `unhandled page fault (11) at 0x000001e1`. The PC is
  `strlen` (libc `0x428e1d20`), called from `FCStorageBase::GetModelStr(int, char*)` (TIFSDaemon
  `0x8ba78`). `GetModelStr` checks the count at `this+72`, then loads `table[idx]` from `this+60`
  and passes it to `strlen` with no NULL or type check. The value `0x1e1` is not a pointer. The
  D-Bus handler `on_get_model_str` (`0x7ace4`) forwards the client index after `LoadFactoryFile`.
  Table build (TIFSDaemon `ReadFactoryDataFile`, `0x8e85c`): the table is `new[count*4]` at
  `0x8f2bc`, and each slot is then set to a `new[40]` record (loop at `0x8ed7c`), filled by
  `m_CopyFactoryBuffer`. The loop only runs when `1 <= count <= 0x7fff` (`0x8ed5a`). If the
  build is skipped with a nonzero count, `table[idx]` is uninitialized heap memory, which can
  hold `0x1e1`. Hypothesis, not yet verified: the factory data (`/opt/dbspace/.factory.db`,
  `/opt/usr/apps/factory-service/data/FactoryClone.dat`) is absent from the QEMU image, so the
  build fails. Those files are not in the extracted rootfs, and the trace has no factory-load line.
- Trace: VDFS-root boot for 5, 10 and 15 s (`trace5/10/15-serial.log` in `/tmp/fsqemu`) shows no
  `ttyS` registration line and no port probe for `0x190a40`. Only the micom port logs.
- Open: no rootfs rfkill config; the port-to-node mapping still needs a longer boot or a `ttyS`
  probe trace in the kernel.
- Side note: `sdp-uart-micom` logs `cs err. cal[2A]!=mic[29]` on every micom cycle in the 20 s
  boot. This is the same `0x29`/`0x2a` reply tag question (item 5).
- No device has been added. Adding one needs the mapping above confirmed.

## Dropped

- The audio DSP abort (`0xc0184438`) was seen only on the removed probe kernel. It did not
  reproduce on the production kernel and is not an open lead.
- Earlier micom and UART-responder leads are removed. The responder and its tests were deleted.
