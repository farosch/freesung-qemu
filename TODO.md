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
- Not reproduced on the current build (re-checked to 150 s). `card-own.img` is not in the repo, and
  `freesung-probe-artifacts.py` does not produce it. The card the tool does produce
  (`hawkm-card-root.img`, p22 VDFS root byte-identical to the shipped card) boots with Xorg ready,
  `[CoreGL] Driver GL version 2.0`, and no Cogl or `XOpenDisplay` error through 150 s. The 200 s
  run of the shipped card also showed no Cogl error. This is past the ~89 s failure point the
  earlier notes gave.
- Earlier claim, unverified in this build: the abort reproduces on a 300 s boot of `card-own.img`,
  and the squashfs root masks it because the squashfs lacks Smack labels. The extracted tree has
  no xattrs (`os.listxattr` is empty on `Xorg`, `firstscreen`, and `xserver`), which fits that
  explanation. Re-test at 120 s+ before relying on it.
- The shipped `hawkm-card-root.img` (Nov 7) has a stale `/opt` seed in p24. It does not match the
  current `usr/data_backup.img` (Nov 5), while the regenerated card's p24 matches it byte for byte.
  Regenerate the card before boot tests.
- Side finding: `smack-watchdog` aborts at about 64 s (`pure virtual method called`,
  `Cannot add event log`). This is not firstscreen. Unverified whether it matters on production.
  Not seen in the 200 s or 150 s runs.
- Open lead: `org.tizen.tv.factory-service` dies with SIGSEGV (`[VDLP COREDUMP] SIGNR:11`) about
  every 10 s from 48 s on the regenerated card (11 coredumps in 150 s, 22 `Died` lines). It is
  relaunched each time. Not yet investigated. Check whether it blocks any boot step.
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
- Static (production `03-uImage`, scratch disassembly `/tmp/fsqemu/kfrag3.dis`): the micom sender
  (`0xc02662a0`, tail `0xc0266444`-`0xc02664d8`) waits with `0xc03f85e0` (interruptible
  completion wait, 400 ms via `0xc003b988`). It tests `cmn r0, #512`. On `-512` it does not take
  the failure exit; it re-enters the send loop (`0xc02662ec`) when its retry flag `r8` is set.
  So `-512` is an interrupted wait that is restarted. A timeout is a different return value.
- Consequence: `-512` points at a signal pending on the waiting thread (freezer or kill), not at a
  rejected `0xd1` reply. If the signal stays pending, every wait returns `-512` at once, the
  retries burn out, and the abort follows. This is a hypothesis; the signal source is not found.
  The `0xd1` zero-payload idea is now weaker.
- Open: which signal is pending and who sends it. Log search (existing run logs): the only kernel
  signals are `TIFSDaemon` SIGSEGV (11) and userspace SIGKILL (9) from `launchpad`/`dbus-daemon`
  (`send signal ... __send_signal`). No freezer or suspend signal appears. The run A log with the
  `-512` retries was not found, so that sequence cannot be re-checked from a log file yet.
  No `freeze for suspend` string is in the rootfs.
- Needed: a healthy-micom control. The repo has no micom responder, so `-512` cannot be removed yet.
- Trace run (scratch only, 170 s, `MBOXTRACE` on every mailbox SEND, not in the repo): only one
  `0xd1` send occurred (tx `01d1ffff 00000000 135e84d2`), right after `ft_dpms_on` at 83.99 s. It
  got no retry. `SIGNR:6` did not occur; there were 13 `SIGNR:11` (`TIFSDaemon`), and `tzdaemon`
  waits grew from 72 s to 74 s. So run A's `-512`/`0xd1` retry storm did not reproduce. The
  payload test is not justified until the storm reproduces. The trace is not in the repo.

## 5. Micom device registration (drop explained, judged benign; not the TCON/scanout blocker)

- Trace (scratch, not in repo): the first mailbox sends are `cmd 0x20` then `cmd 0x18` (retries). Both
  are answered with the default tag `0x29`, and `0x29` is the reply that reaches the device-3 lookup
  before registration. So the `did not registered completely` line is the first unmatched reply, at
  about 0.23 s, in every boot.
- Scratch experiments (8 s boots, scratch only):
  - Moving the first `0x20` reply, then the first two replies, off `0x29` did not remove the line.
  - Forcing the first eight default-tag replies to `0x00` also did not remove it. The default arm
    of the dispatcher catches any unmatched command byte, not only `0x29`.
  - So the line cannot be avoided by a reply tag. It is the first unmatched frame before device 3 registers.
- Downstream: detect_tcon still reports `PANEL_TYPE [T�]` and `Unknown TCON type` in every variant,
  the same as the control.
- Judgement: the drop does not change the TCON or scanout result. The static note below
  (device-3 handler treats `0x29` as a no-op) fits. Item 5 is not the black-framebuffer blocker.
  Item 5 remains open only for the registration order, which is a model timing detail.

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

- Build and test status (repo-matched build, `/tmp/fsqemu/qemu-10.2.1/build-repo`): the
  i2c qtests pass 30 of 30 (`python3 -m unittest tests.test_qemu_sdp1406_i2c`). The earlier 23
  failures had three causes: (1) the ninja tree in `/home/bici/work/eth0-lab/qemu-src/qemu-10.2.1`
  has an older sdp1406 model with no `sdp1406_i2c_update_irq`, so any boot or gdb trace taken from
  it does not test this repo's model; (2) `_qtest` returned after the first newline, so an
  `IRQ raise` line that came before `OK` desynced the socket; (3) temporary debug `fprintf`s in
  the model filled the unread stderr pipe and stalled QEMU. Items 1 to 3 are fixed in the test
  harness or the build. Model code was not changed.
- Boot on the repo-matched build (150 s, README arguments plus `snapshot=on`): the CH:7 `0x40`
  NACK repeats about 150 times (designed, see item 16) and one CH:4 `0xa0` timeout appears at about
  3 s, followed by `[eep_reg_read] i2c read error!!` at about 13 s. The single timeout is
  intermittent, as the entries below describe. Open: whether that one timeout still needs a model
  change.
- The earlier gdb trace below was run on the stale build. Its addresses are still valid for the
  guest kernel, but its timing results are not.
- gdb trace of a `-kernel` boot (non-stopping breakpoints, `/tmp/fsqemu/i2c-dbg.py`): the guest
  reaches the CH:4 window (`sdp-i2c 190180.i2c`). The transfer function `0xc0293edc` ran 10+
  times and `timeout_print` at `0xc0294428` ran 7 times, matching the 7 `timeout` lines in the
  serial log in the same order. `r3` at the timeout print
  is the 7-bit device address (`0x50`=`0xa0>>1`, `0x67`=`0xce>>1`, `0x64`=`0xc8>>1`). The IRQ
  handler breakpoint `0xc02938fc` and exit breakpoint `0xc0293ce8` never hit in 60 s, so the
  completion path is not yet observed.
- Disassembly (`/home/bici/Downloads/k.dis`, raw binary, physical addresses; virtual = physical
  + `0x40000000`): the waiter is `0x80293854` (virtual `0xc0293854`). It writes CON, the STAT
  value (`0x90`/`0xd0`), the command byte, then calls through a function pointer, and only after
  that call returns does it OR `0x20` (BUSY) into STAT. The candidate IRQ handler is
  `0x802938f0` (virtual `0xc02938f0`). No literal or direct call in the image references it, so
  it is reached indirectly. Second gdb run: the waiter returned `r0=0` on all 8 calls (timeout)
  and the candidate handler never ran. The model raises the channel IRQ on SPI `40 + channel`
  (`hw/arm/sdp1406.c:769`) only when the completion timer fires, and masks it when the group's
  IRQ mask bit is set (`sdp1406_i2c_update_irq`).
- Lead (unverified): BUSY is written after the wait call, and the model arms `xfer_timer` only
  when BUSY is set (`hw/arm/sdp1406.c:691`). If the start path does not set BUSY before the wait,
  the transfer never starts and the timeout is expected. Next: confirm which function sets BUSY
  before `0x8029437c`, and check the IRQ mask bit the driver sets for CH:4.

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
- Latest (counted latch, `irq_latched[bit]`): the single-bit latch dropped the second of two queued
  completions. A/B on six parallel 120 s untraced boots under the same load: pre-fix 20 CH:10 and
  19 CH:4 timeouts; fixed 3 CH:10 and 7 CH:4 (5 of 6 fixed boots still log at least one timeout).
  Open: the CH:4 `0xa0` timeouts are the same symptom and need their own trace, since CH:4 does
  not share the CH:10 read sequence.
- Earlier (Sii9777 build, two 90 s boots): one boot logged the CH:10 `0xc8` timeout at 30.9 s and
  the `-110` after it. The other boot logged no i2c errors, and CH:7 logged none in either boot.
  The completions are modelled, so the timeout is a lost or late completion on group 1 bit 2.
  Open: find the race (a re-arm of the transfer timer or a latched bit during the device-ID read
  is the first suspect) and check the CH:10 read sequence with a repeated-start trace.
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

  - CH:4 race root cause (trace in a scratch build, run `c4tr5`): the driver's address sequence
    is `STAT 0xd0`, `DS 0xa0`, `CON`, `STAT 0xf0`. If a read data byte is still pending when the
    address write arrives, the second start calls `timer_mod` on the pending timer. The two
    transfers merge into one completion, so one IRQ is raised for two transfers and the waiter
    for the address write is never woken.
  - Tried and reverted: a per-start queue (a start while the timer is pending is counted and
    completed later). It fixed the merge, but a normal address write is `DS` (start) then `STAT
    0xf0` (second start), so every address transfer got two completions. Sii9777 register reads
    then returned one byte late. A flag that makes the START after a DS write a no-op removed the
    double count, but the Sii read sequence still failed. The cause is not found yet. The
    attempt is not in the repo (kept in `/tmp/fsqemu/sdp1406.queue-attempt.c`, scratch only).
  - Second attempt (the queue plus an `xfer_from_ds` flag, and a test harness that drains queued
    transfers in `setUp`): all 36 i2c qtests pass, and a new qtest
    `test_address_written_during_pending_read_gets_own_completion` fails on the committed model
    and passes with the queue. A six-way A/B of 120 s root-card boots then showed a regression:
    the queue gave 20 `timeout waiting for bus idle` on CH:6 (`1901c0`) and 3 CH:4 fails, against
    0 bus-idle timeouts and 5 CH:4 fails for the committed model.
  - Cause of the bus-idle regression (traced with a file trace on CH:6, not stderr): a data-byte
    `DS` write (`addr_phase` false) during a pending transfer queued another completion. The
    queue depth grew to 5 and BUSY stayed set. Fix: only an address `DS` write queues; a data byte
    merges into the pending transfer, as before.
  - Third attempt (in the repo now): address-only queue, plus `test_data_write_during_transfer_merges_into_it`.
    37 of 37 i2c qtests pass, three runs. Two six-way A/B rounds (12 boots, 120 s, same host load):
    committed model 0 bus-idle timeouts, 11 `i2c fail` (CH:4 8, CH:10 2, CH:7 1); this model
    0 bus-idle timeouts, 6 `i2c fail` (CH:4 4, CH:6 1, CH:10 1). This is an improvement, not a fix:
    CH:4 `0xa0` still times out. Committed as partial progress; CH:4 is still open.
  - Ordering attempt (reverted): in `xfer_done`, a read's `rd_arm` data arm ran before the queued
    transfer. The trace in `c4tr1.log` showed `DS 0xa1` (read address), `DS 0xa0` queued, then the
    read's data byte arriving after the queued completion. Four six-way A/B rounds (24 boots, 120 s):
    reorder 17 `i2c fail` (CH:4 12, bus-idle 2); address-only queue 9 `i2c fail` (CH:4 8, bus-idle 0).
    The reorder is worse, so it is reverted. The test for this ordering is kept as
    `test_queued_read_address_does_not_overtake_pending_data` (marked `expectedFailure`).
  - Per-transfer snapshot (committed, CH:4 still open): the root cause of the ordering problem is
    that the address-phase state (`rd_arm`, `eep_sel`, `sii_sel`) was written at address-write time
    but consumed at completion time. A queued write address overwrote a pending read's arm. Each
    queued transfer now carries its own `SDP1406I2cXfer` snapshot, and the test
    `test_queued_write_address_keeps_pending_read_data` replaces the `expectedFailure` test.
    38 of 38 i2c qtests pass, three runs; the new test fails on the previous build.
    A/B, 60 s boots (not 120 s), three rounds, six boots each: this model 16 `i2c fail` (CH:4 14,
    bus-idle 0, per round 5/7/4); committed `ac75660` 11 (CH:4 10, bus-idle 0, per round 7/2/2).
    No improvement shown, and the counts are within noise, so CH:4 is still open. The change is
    kept as a correctness fix for the per-transfer state.
    Open: the reviewer's claims that LRB is never set and that one IRQ per DS byte is expected are
    untested. Test them before acting on them.
  - Next: the CH:4 timeouts that remain. Trace the CH:4 `0xa0` wait the same way (file trace in a
    scratch build) to see whether a START is still merged, or whether the driver's wait is too short.




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
  Update: `mkcard.py --firmware-root` now formats p26 host-side (item 20); the guest mkfs question is still open.

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


## 16. Sii9777 HDMI bridge on CH:7 (modelled; residual CH:4 and CH:10 timeouts open)

- Done: the Sii9777 is modelled at DS byte `0x40` on CH:7 as a 64 KiB register file (16-bit
  sub-address, high byte first, no page wrap). The NACK is gone. The 90 s README boot no longer
  logs the 120 CH:7 `incomplete xfer (-6)` errors, and `Sii9777 Instance creation SUCCESS` appears.
  Tests: `Sdp1406SiiTests` in `tests/test_qemu_sdp1406_i2c.py` (35 i2c tests pass).
- Done: the IRQ latch is a per-channel count, so queued completions are not lost. This cut CH:10
  `0xc8` timeouts from 20 to 3 in six parallel 120 s boots. Residual CH:4 and CH:10 timeouts remain.
- Open: the Sii9777 driver's device-ID and firmware-version checks use register values we do
  not model yet. Check the next boot log for `Sii9777-I2C` errors and disassemble
  `tztv_hdmi_switch_api.ko` if any appear.

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

## 20. Black scanout: micom and panel identity chain (open lead, not proven)

- A 90 s SDL boot (README command) with a monitor `screendump` at 60 s is 1920x1080 all zero.
  Xorg and CoreGL start, and `ECHO-FP` reads a 1920x1080 panel, but nothing is scanned out.
- Panel identity is never set. `tztv_get_panel_type` prints garbage (`[T�]`), `tztv_get_svc_panel_year`
  returns -1, and `detect_tcon` logs `Unknown TCON type`, so `tcon_loaded=[0]`. `kfactory_drv`
  reads the year and type from the micom product info (`micom_prd_info`, `sdp_micom_send_cmd_ack_data`)
  and from GPIO straps. Factory store `/opt/dbspace/.factory.db` is absent (item 17).
- The micom message path (`msg-wt61p807`) times out on `ack[30]`, `[68]`, `[84]`, `[98]`, `[AC]`,
  `[B3]`, `[D7]` (two each, about 3–25 s). `tzdaemon` reports `thread has been waiting` 68 times.
- The HDMI switch reports `[m_WaitBootDone] Boot failure... [JackBoston]` at 15.7 s and
  `HDMI BKSV is still INVALID`. `SBB is not connected` is printed by FB:INIT.
- `fb0` is never registered (item 2), so the scanout depends on the Xorg/sdp_drm path, which is
  blocked by the panel/TCON state above. Not confirmed: the model's micom replies are generic
  (tag `0x29`, zero payload) for every command except those listed in `sdp1406_mbox_ack_for`.
- Static findings (`kfactory_drv.ko`, disassembled with `arm-linux-gnueabi-objdump`):
  - Micom `0xc6` is the product-info request. It is sent up to 3 times (`mov r0,#198`, `mov r3,#6`
    at `kf.dis` 0x2c54 and 0x30a0), and its 5-byte payload is written to `micom_prd_info`. Byte 3 is
    read by `tztv_kf_get_graphic_mix_spec`. The model has no `0xc6` entry, so it replies with the
    default tag `0x29` (`sdp1406_mbox_ack_for`). Tested (scratch only): a `0xc6` echo with a zero
    payload, 150 s serial boot, `-snapshot`. TCON, panel line, micom timeouts, `tzdaemon` waits and
    COREDUMP counts matched the unmodified control within noise. The real 5-byte product info is
    unknown, so a non-zero payload is not tested.
    The `0x30` echo change showed that an echo alone does not reach the waiter.
  - The panel year and type are not read from micom. `tztv_kf_get_svc_panel_year` asks
    `kfactory_drv_get_data(0x51f)`, and `tztv_kf_get_panel_type` asks `kfactory_drv_get_data_str(0xc20)`.
    Both go through `kfactoryd` (`/dev/kfactory`), so they come from the factory store.
  - `Factory_Part1.dat` has the key `db/factory/FC_STORE_LEVEL_FIXED/SVC_PANEL` at byte 17532304 with a
    binary record after it (values such as `0x09`, `0x10`, `0x66e`). The record layout is not decoded.
  - The store is `/opt/dbspace/.factory.db`. It is built by `FCStorageBase::Import_FactoryData`, which is
    gated by `CheckEEPromForAutoImport` (`factory-init`, `TIFSDaemon`). The boot log has no import
    messages, so the import never ran.
  - Auto-import gate (`factory-init` `CheckEEPromForAutoImport` at 0x19180, decoded from Thumb disassembly).
    All four must hold: (a) `/opt/usr/factory_backup_flag` absent; (b) EEPROM `0xf212` == 1 (the model
    sets this, `sdp1406.c` `sdp1406_i2c_eeprom_init`); (c) `FCStorageBase::m_Read(0x1871)` takes the
    EEPROM path and reads EEPROM `0x5671`, which must equal `0x63`; (d) `/opt/usr/apps/factory-service/data/auto_imported`
    absent. The model does not set `0x5671`. The rootfs has neither flag file. The card's p24 seed is not checked.
  - Scratch experiment (not in the repo): setting EEPROM `0x5671 = 0x63` with `0xf212 = 1` and a 120 s boot
    shows no factory import messages and no change in the panel year or TCON log (`Unknown TCON type`,
    `tztv_get_svc_panel_year: [-1]`, `tcon_loaded=[0]`). So (c) is at most part of the cause. Not proven that
    `0x5671` is the real EEPROM address of this byte.
  - Card p24 check (raw byte search of `hawkm-card-root.img`): `factory_backup_flag` has 14 hits in p24 and
    2 in p22 (rootfs binaries). `auto_imported` has 38 hits, all in p24. The seed `usr/data_backup.img` contains
    `factory_backup_flag`. `build-backup-data.sh` touches that flag before `mkfs.vdfs` and removes it afterwards,
    so the seed keeps it. Nothing on the device creates or removes either flag at boot (`TIFSDaemon` and
    `factory-init` only test them). So gate (a) most likely blocks the import on this card. Not proven: the
    hits could be stale VDFS data rather than live files.
  - Scratch test (`/tmp/fsqemu/gate`, not in the repo): renaming the flag names inside p24 breaks the VDFS
    btree CRC (`vdfs_fill_super: failed to load root directory`), so the rootfs does not mount. A valid
    seed needs `mkfs.vdfs` with the original `/opt` tree, which is not in the repo. `qemu-arm` user mode is
    installed, so `mkfs.vdfs` can run on the host once that tree is available.
  - Scratch test 2 (not in the repo): EEPROM `0x5671 = 0x63` plus p24 zeroed, so neither flag can exist. `/opt`
    does not mount (`wrong fs type`), `VDLP` coredumps repeat, and the boot takes a different path. The panel
    year, `GetPanelInfo`, and `Unknown TCON type` lines never appear, and no factory import message appears.
    Inconclusive: the TCON identity cannot be compared with the normal boot.
  - Kernel route: the VDFS CRC check (`CRC missmatch`) is built into the uImage (`03-uImage`, uncompressed,
    load 0x80008000). Its string addresses (0x80471255, 0x80472c77) are not in a literal pool, so the call site
    is not yet found. Patching that check in a scratch kernel would let a renamed-flag seed mount. Not done.
  - Valid seed (rebuilt, not in the repo): `vdfs-unpack` extracts the shipped `usr/data_backup.img` `/opt` tree
    (the image's top-level `root` folder is `/opt`). `mkfs.vdfs` runs under `qemu-arm -L <rootfs>`. Rebuilding
    the unmodified tree gives the same name set as the original (0 differences). Dropping `factory_backup_flag`
    leaves only that name different. The scratch seed `/tmp/fsqemu/seed/noflag.img` boots, and Xorg is ready.
  - Shipped p24 has a live `auto_imported` (file and link). The control boot (pristine card, unmodified QEMU,
    150 s) prints `tztv_get_svc_panel_year: [-1]`, `Unknown TCON type`, and `tcon_loaded=[0]` at ~4.8 s. So the
    shipped card blocks the import at gates (a), (c), and (d).
  - No-flag seed, 150 s, `0x5671` unset: no import, no `auto_imported`, and `detect_tcon` never runs. The panel
    line reads `NOT BOE panel!(NONE_15)` at ~17 s.
  - No-flag seed plus scratch `0x5671 = 0x63`, 150 s: the import runs silently. `auto_imported` appears in p24
    with no log line. This is the first observed unblock of the import. The panel line reads `NOT BOE panel!(T�)`
    at ~16 s. `detect_tcon` still does not run, so the TCON identity is not yet read. `VDLP` coredumps: 15 (control 12).
  - Micom `ack[..] timed out` is 0 in all three runs, and the control has 130 `tzdaemon` waits. The earlier counts
    (7 and 68) came from other runs. The noise is too high to attribute these counts to the seed.
  - Boot order changes with the seed (`detect_tcon` absent, panel line later). So the no-flag seed is not yet a
    like-for-like control for TCON. Not proven that the import fixes the scanout.
- Next: find a seed that unblocks only the import while keeping `detect_tcon` in the same boot order (for example
  the no-flag seed with the 0x5671 change, boot for longer, and check `detect_tcon` and `svc_panel_year`). Decode the
  `0x5671` mapping (the byte is a scratch change only). The `0xc6` echo test is done (no change, see the
  product-info note above). Rule out i2c first: no
  `i2c fail` lines in these boots.
- Run result (no-flag seed + scratch `0x5671 = 0x63`, persistent card, 150 s): the import ran (`auto_imported`
  created under `usr/apps/factory-service/data`), but `kfactoryd` crashed (`unhandled page fault (11)`, SIGSEGV)
  at about 15 s. Extracting the post-boot p24 with `vdfs-unpack` reports 64 failed objects, including
  `dbspace/.factory.db` (object 1660) and its journal (1659): "extent ... reaches the metadata area". The seed
  extracts with 0 failures and a 94208-byte `.factory.db`. So the persistent write damaged the factory store.
  Not yet known whether the import, the crash, or the card state caused the damage. Next: repeat with a `-snapshot`
  boot to separate the import from the card state, and check whether `SVC_PANEL` is populated.
- `-snapshot` run (no-flag seed copy, scratch `0x5671 = 0x63`, 150 s, card writes discarded): the `kfactoryd` page
  fault reproduces at about 17 s (`unhandled page fault (11) at 0xeeb3a7f8`, SIGSEGV, `can not run multi process!!`
  twice). So the crash comes from the seed or the import path, not from persistent card state. `This is NOT BOE
  panel!(T�)` is still printed at about 23 s, and `detect_tcon` is still absent. The import does not appear in the
  serial log, and `-snapshot` discards its disk effect, so `SVC_PANEL` is still unchecked. Item 20 stays open.
- Crash source (read-only analysis): `GetStrDataFromDB` in `libTIFactoryData-DVB.so` opens the panel index files
  under `/opt/apps/factory-service/share/` (`PanelIndex.dat` 8260 bytes = 295 records of 28 bytes;
  `FactoryIndex_sorted.dat`; `ModelIndex.dat`). It indexes `buf + 28*idx` with no bounds check, so a bad index
  from DB key 17 faults on memcpy. The `-snapshot` serial maps `FactoryIndex_sorted.dat` at inode 2531 on p24.
- Index files are runtime products, not seed content. `factory-init` generates `PanelIndex.dat`, `ModelIndex.dat`,
  `FactoryIndex_sorted.dat`, and `ParentChildIndex_sorted.dat` under `/opt/apps/factory-service/share/` from the
  factory store (strings in `usr/bin/factory-init`). The shipped seed `usr/data_backup.img` has only
  `db_initialized` and `spi_version_updated` there (max inode 2513). The index inodes on
  `hawkm-card-root.img` p24 (2527–2532) are above that, and the card's p24 reaches inode 2758. So that card was
  written by guest boots after the seed was built (mtime 2026-11-11 20:32), so it is not a pristine
  `freesung-probe-artifacts.py` / `mkcard.py` output. The control boot from the seed-built card
  (`/tmp/fsqemu/noflagcard/serial_ctrl.log`) also maps these files, so the seed does not need to ship them.
- Consequence: the `GetStrDataFromDB` crash (bad index into `PanelIndex`) depends on the store contents that
  `factory-init` used to generate the index. The earlier "mixed card" explanation is withdrawn.
- Rebuilt probe card (`freesung-probe-artifacts.py --output /tmp/fsqemu/probe-rebuild`): p24 is clean
  (max inode 2513, no index files). But its p26 has no VDFS superblock (the original card has one), so the
  guest fails `mount /dev/mmcblk0p26` at about 4.8 s and the serial log stops. `mkcard.py` does not format p26
  (see DEVELOP.md "Card images"). So the probe card was not bootable as built.
- Fixed in `mkcard.py` (`--firmware-root`, now passed by `freesung-probe-artifacts.py`): p26 is formatted
  with the firmware `mkfs.vdfs` under `qemu-arm` at 6144K. Verified: the rebuilt card at
  `/tmp/fsqemu/probe-p26` has `VDFS1002` at p26 start and `vdfs-unpack --list` reads it.
- Clean `-snapshot` boot of the fixed card (`/tmp/fsqemu/qemu-10.2.1/build/qemu-system-arm`, patches 0001-0003
  applied, 60 s, log `/tmp/fsqemu/fix11-boot.log`): no `bad signature` on p26, and `FactoryIndex_sorted.dat` is
  mapped from `/opt`. The `kfactoryd` page fault still happens, at about 45 s. The fault PC is the same memcpy
  (`0x428e3f54`), but the caller LR is `0x4308a6ad` (earlier runs: `0x4308a503`, `GetStrDataFromDB`). Open: which
  function contains `0x4308a6ad` (the library offset is `0xa6ad`; `/usr/bin/objdump` here cannot disassemble ARM), and
  whether `PanelIndex.dat` and `ModelIndex.dat` are read (not seen in the log).
- gdb trace of the fixed card (`/tmp/fsqemu/kf_bp.py`, run `fix17`): the crash is a page fault at `0xedc07420`
  (memcpy, LR `0x4308a6ad`) at about 71 s. The breakpoint hit before it had a source address that is not on the
  28-byte record grid, so the index came from a 32-bit wrap. The stored index read returned nothing.
- Static trace of the index source (`/tmp/fsqemu/tifd.dis`):
  - `GetIntDataFromDB` (`0x4308a3d0`) calls `factory_initialize_source_info`, then
    `GetIntDataFromDBSrcRes(key, out)` (`0x4308a1cc`).
  - `GetIntDataFromDBSrcRes` calls `GetFactoryUID(key, &uid_out, ...)` (`0x43089da0`) and copies the `[r7+4]`
    word to `*out`. The word is zero before the call. On a failed lookup it is not updated, so a `-1` must come
    from `GetFactoryUID`.
  - `GetFactoryUID` does a binary search with `GDcmpr` over a sorted table. Record stride is 284 bytes (`mla` with
    `r9 = 0x11c`), count at `[r3,#60]`, base at `[r3,#64]`. This is a different table from the 28-byte records that
    `GetStrDataFromDB` indexes, so the record index it returns is not a valid index into the 28-byte table.
  - `loadIndexDatFile` (`0x430888d8`) maps `FactoryIndex_sorted.dat` with the same 284-byte entry size, so
    `GetFactoryUID` searches `FactoryIndex_sorted.dat`.
  - `GetStrDataFromDB` opens `/opt/apps/factory-service/share/PanelIndex.dat` (path literal at `0x43095dac`,
    `open` at `0x4308a542`), reads the whole file into a heap buffer, and copies `buf + 28 × index` (`rsb`/`lsl`
    at `0x4308a4f6`–`0x4308a4fa`).
  - Runtime check (`/tmp/fsqemu/pi_bp.py`, run `fix20`, QEMU with `-S`): the `fstat` size before the read is
    `8260`, which is 295 × 28 exactly (and not a multiple of 284). So the 28-byte stride matches `PanelIndex.dat`.
    The stride mismatch I suspected is not the cause.
  - The open question is the value of key 17: it comes from `GetFactoryUID`'s binary search over
    `FactoryIndex_sorted.dat` (284-byte entries). Its result is used as a row number into the 295-row
    `PanelIndex.dat`. A wrapped or out-of-range result is the likely fault. Only one key-17 read was readable
    (value `0`, index 0 valid); the others were in other processes (`na`), so the crashing value is still unseen.
  - Library-filtered runs (`/tmp/fsqemu/pi_bp2.py` run `fix21`, `/tmp/fsqemu/pi_bp3.py` run `fix22`; breakpoints
    at `0x4308a4ec` and `0x4308a696` for keys 17 and 18, copies at `0x4308a4fe` and `0x4308a6a8`; filter: library
    range plus readable stored word): every readable key-17 and key-18 value is `-1`. Each copy then reads
    `buf - 28` (`delta=-28`), which is in the heap, so it does not fault by itself. So `GetFactoryUID` returns "not
    found" for both keys in every readable hit. The `-1` is not a row index.
  - Crash timing and address vary between runs (`0xedc07420`, `0xed507420`, `0xee3e7420`, `0xed9f27f8`; 17–71 s).
    None of the logged copies had a bad index, so the faulting memcpy has not been captured with its index yet.
  - Open: why `GetFactoryUID` finds no match on `FactoryIndex_sorted.dat` (its row count, or whether the
    store key that factory-init writes is missing), and which copy faults at the crash.
  - Index source (static, seed `usr/data_backup.img` extracted to `/tmp/fsqemu/seedx`): `FactoryIndex_sorted.dat`
    is built by `FCStorageBase::m_CreateFactoryIndexSorted` from the store `/opt/dbspace/.factory.db`. In the seed,
    every `STORE_LEVEL_*` table has 0 rows, all `STORE_LEVEL_VERSION` values are 0, and
    `DB_PARAMETERS.initialization_status` is 0. So the seed store is empty and the index it produces has no
    matching key, which fits the `-1` results.
  - The store is filled by `Import_FactoryData` from `/usr/share/factory-data/Factory_Part1.dat` (17.6 MB) and
    `Factory_Part2.dat` (2.5 MB). The import is gated by `CheckEEPromForAutoImport`, which I re-decoded from
    `factory-init` at 0x19180:
    - (a) `/opt/usr/factory_backup_flag` is absent. The return value of the gate is not a simple skip flag:
      runtime shows a return of 2 still leads to `Import_FactoryData` (see the run below).
    - (b) EEPROM `0xf212` == 1 (the model sets this).
    - (c) `m_ReadEEprom(addr=0x1871, len=1)` reads EEPROM `0x1871 + 0x3e00 = 0x5671`, which must equal `0x63`.
    - (d) `/opt/usr/apps/factory-service/data/auto_imported` is absent.
  - The seed's `factory_backup_flag` (see above) and the p24 flag hits point to gate (a) blocking the import.
    Gate (c) is confirmed as `0x5671`. The model currently holds `0xff` there. The `0x5671 = 0x63` scratch run
    did not import because gate (a) still applies, so the import needs (a) and (c) both satisfied.
  - Next (not done): confirm the `FactoryIndex_sorted.dat` size on the card at boot (expected 0 or header-only
    for an empty store), and decide whether to clear gate (a) in a scratch card before testing the import.
  - Caller found: `main` in `factory-init` at 0xec0c (`bl 0x19180`). The earlier search missed it because
    `main` starts at 0xeae8. Gate result is in r9. A nonzero result goes to 0xef7c, which calls
    `fac_internal_set_db_initialized(false)`, then reads `[r4,#8]`. Only if `[r4,#8]` is nonzero does it call
    `FCStorageBase::Import_FactoryData` (0x217f8). Otherwise it jumps to 0xf064.
  - Boot path: `factory-init` is started by `usr/apps/fastboot/bin/fb_hawkm.sh` (`/usr/bin/factory-init.sh &`),
    which runs before the production-mode branch. The boot log shows `Production mode`.
  - Runtime run `fi4` (QEMU `-S`, gdb script `/tmp/fsqemu/fi_bp2.py`, 75 s). VA breakpoints also fire in other
    processes, so each hit is filtered by the factory-init rodata string at VA `0x26334`
    (`/opt/usr/factory_backup_flag`, file offset `0x1e334`). Result: `main` runs and the gate runs.
    - The gate `0x19180` returns `2`, with `[r4,#8]` = 1 and `[r4,#9]` = 0. The code branches to `0xef7c` on a
      nonzero result, calls `fac_internal_set_db_initialized(false)`, then calls `Import_FactoryData` (`0x217f8`).
      So **the import IS attempted at boot**. My earlier note that a return of 2 skips the import was wrong.
    - The import source `/usr/share/factory-data/Factory_Part1.dat` is on the card (the name is in the card image).
      The store is still empty after boot, and the boot log has no import output.
    - Static read of `Import_FactoryData`: `BeginTransaction` (`0xf52c`), seven `m_ImportFactoryDataToFileSystem`
      calls (`0x2185a` to `0x218b8`, each returns to call+4), `CommitTransaction` (`0xf68c`, `0x218c4`), then a
      `GetFactoryUniqueItemData` check (`0x218da`), with a `fopen`/`fwrite` fallback (`0x21918`).
    - Run `fi5` (`/tmp/fsqemu/fi_bp3.py`, 75 s): `M_IMPORT_ENTRY` hits with `lr=0x2185f` confirm the import
      calls run. Return values were captured for only some calls (`0x9d5f8`, `0x9d8b0`, and one `-19`).
      The run is inconclusive: `stop()` returned False, so gdb kept running while reads raced ("target is running").
      Some `IMP_ENTRY` hits came from a bad `lr=0x24053` (not a real call site). `kfactoryd` still crashed at 21 s.
    - Run `fi8` (`/tmp/fsqemu/fi_bp6.py`, 75 s, fewer breakpoints): `m_ImportFactoryDataToFileSystem` is entered
      3 times per import (`r1=0x40b09`, `r2=0`), and every call takes the `0x2178c` branch (the `+468` pointer is
      zero, `[r11,#468]`, `0x204e6`–`0x204ec`). That block calls `_TAssert` with `"FlagCreate() == true"` (rodata
      `0x22ee4`, file `TList.h`, line 126) and then jumps back to `0x204f0`, so the import continues. No assert text
      appears in the serial log.
    - Constructor `FCStorageBaseC2Ev` (`0x1b611`, 1348 bytes). Found the `+468` store that the earlier grep missed:
      `strd r5, r3, [r4, #464]` (`0x1b738`) writes `r3` to `+468`, and `r3` is `0` (`movs r3, #0` at `0x1b64c`, no
      other `r3` write before it). So the constructor leaves `+468` NULL. My earlier `#468]` grep missed `strd`
      stores, so the "no writer" conclusion above was wrong.
      Nothing else in `factory-init` or `libTIFactoryData-DVB.so` assigns `+468` (checked with a plain `#468]`
      grep, which would also miss a later `strd`; recheck with `strd` before relying on this).
      What `+460`..`+468` is: `r11+460` is passed as `this` to `TList::Seek` (`0x20550`, `0x2055a`), so it is an
      embedded `TList`, and `+468` is its head/current pointer (inferred from use; `TList.h` is not in the image).
      The constructor also zeroes `+460` (`str.w r2,[r4,#460]` at `0x1b709`; `r2` not yet confirmed as 0).

      Import loop behaviour with `+468 == NULL` (import static walk, `0x204e6`–`0x2056c`, and `0x2178c`/`0x214a8`):
      - `0x204e6`: `if (this->+468 == NULL) goto 0x2178c`.
      - `0x2178c`: `_TAssert("FlagCreate() == true", TList.h, 126)`, then `b 0x204f0`. Non-fatal.
      - `0x2053c`: loop head checks `+468` again. NULL goes to `0x214a8`, which asserts the same text, then returns to
        `0x20546`. The count is `+492` (`ldr r1,[r11,#492]` at `0x20502`, dlog argument). Loop runs `i < +492`
        and calls `TList::Seek(this+460, i, 0)` each time (`0x2055a`).
      - So a NULL `+468` only produces assert log lines and a per-item `TList::Seek` on the empty list. It does not
        abort the import by itself. Whether items get imported depends on whether the list is ever filled.
      - `+492` is never written by a direct `str` in `factory-init` (`0x9000`–`0x2b000`), and `+468` has no
        direct store except the `strd` at `0x1b739`. So the list fill path is still unknown; check the `strd #488`
        case and helper writes before concluding.

      Runtime run `fi9` (`/tmp/fsqemu/fi_bp7.py`, Python `stop()` returns False, identity filter on `0x26334`):
      - `IMP_ENTRY n=1` and `n=2`, `this=0x50f68`: `+460=0x23080`, `+464=0x23198`, `+468=0x57c98` (non-NULL),
        `+492=709`. So for this object `+468` is set after the constructor, and the list has 709 entries.
        `PRE468` fired once (`0x204e6`, this=`0x50f68`, `+468=0x57c98`), so this call does not take the NULL branch.
        My earlier conclusion that the NULL branch always applies is wrong for this object.
      - Other `IMP_ENTRY` hits (`n=3`–`11`) have `this` values with zero or ASCII-like fields (for example
        `+460=0x462f7972`). Those `this` pointers are not valid objects, so the identity filter is not enough to
        select the real import. Those hits are likely other calls or stale frames. Treat them as noise.
      - `ASSERT_A` fired 39 times, but most reads of `this+468` failed (memory error), so those hits are not
        analysed. `ASSERT_B` did not fire.
      - `SEEK` hits used `tlist=0xbe3f49b4` (a stack address, not `0x50fc4` = `0x50f68+460`). None came from the
        main object. The logged `+492` values change between hits on the same `tlist`, so `+492` is not a stable
        count there. The log was capped at 30 `SEEK` lines, so the main object's `Seek` calls (if any) are not in it.
      - Boot (`fi9-boot.log`): `kfactoryd` faulted at 22.9 s (`unhandled page fault at 0xed99e7f8`), consistent
        with the earlier `-1` index. No import completion message appeared in the serial log.
      Run `fi10` (`/tmp/fsqemu/fi_bp8.py`, main object found at runtime as the first entry with a non-NULL list):
      no probe output. gdb refused the script's `continue` with "Cannot execute this command while the target is
      running", so no breakpoint was serviced. `kfactoryd` faulted at 17.0 s (`0xeea767f8`). No conclusion about
      the import from this run. Next: find the cause (for example `interrupt` before `continue`), then rerun
      `fi10`'s probes. Also find what sets `+468` between the constructor and the import call (a `TList` insert or
      the `ReadFactoryDataFile`/`m_CreateMemoryMapFromFile` path). Each run needs user approval.
      Run `fi11` (same script, QEMU relaunched without `< /dev/null`): the same "target is running" error occurred
      after the third `IMP_ENTRY` (`this=0x637c8`, `+468=0`, `+492=0`). So `/dev/null` was not the cause. The
      main object (`this=0x50f68`) was seen twice, but `SEEK_MAIN` and `COMMIT_RET` never fired. `kfactoryd`
      faulted at 19.8 s (`0xed5067f8`). The fix belongs in the probe script, not in QEMU's launch.
      Run `fi12` (`/tmp/fsqemu/fi_bp9.py`, no `continue` inside the script; run as
      `gdb-multiarch -batch -x fi_bp9.py -ex continue`): the script's breakpoints ran. The main object was
      found (`this=0x50f68`, `+468=0x57c98`, `+492=709`) and was hit twice. Both `CommitTransaction` returns hit
      (`0x218c8`) with `r0=0x0`, which suggests the commit succeeded. `SEEK_MAIN` never fired, and no
      `ASSERT_A`/`ASSERT_B` hit. The "target is running" error still appeared, after the second main-object hit.
      The batch `-ex continue` fixed the early stop but not the error. `kfactoryd` faulted at 20.2 s
      (`0xee3ae7f8`).
      Run `fi13` (`/tmp/fsqemu/fi_bp10.py`: `fi_bp9` plus a commit-return dump of the main object's
      `+460/+464/+468/+492` and a capped `0x2053c` loop-head probe): the "target is running" error came right
      after the first main-object hit, so neither the commit dump nor the loop-head probe fired. The error
      position varies between runs (after hit 2 in `fi12`, after hit 1 in `fi13`), so it is not a fixed
      point in the import. `kfactoryd` faulted at 19.7 s (`0xedea67f8`). Open: the error comes from gdb's
      own state, not from the probed code. Next candidates: check whether `stop()` returning False leaves
      gdb in a state where the queued `-ex continue` runs too early, or drop `-ex continue` and use a
      `gdb.events.stop` handler that calls `gdb.execute("continue")` only from a non-stop context.
      Static check (no boot): in the constructor (`0x1b611`) and import body (`0x20440`–`0x21900`), the only store
      to `+464`/`+468` is the zeroing `strd r5, r3, [r4, #464]` at `0x1b739`. The import body calls only
      `TList::Seek` and `TList::Data`, and no `TList` insert is referenced anywhere in `factory-init`. So the
      runtime head value (`0x57c98`) must come from a write through a computed base (for example `this+460`
      plus 8), or from another module. Checked the loader functions `ReadFactoryDataFile` (`0x18358`) and
      `m_CreateMemoryMapFromFile` (`0x1a7fc`): their `#8` stores (`0x183be`–`0x183ea`, `0x18622`–`0x18636`, and
      the `str [sp,#8]` run) write stack-local buffers around `open()`, not list nodes. So neither loader inserts
      into `this+460`. Next: find the caller that constructs the import object, or check `libTIFactoryData` for
      an inline `TList` insert, before any more boots.
      Full-function static pass (`/tmp/fsqemu/fi_funcs.dis`, 331 `.text` symbols disassembled one by one, 0 read
      errors): every `str`/`strd` to `#460`/`#464`/`#468` in `factory-init` is in the destructor
      `FCStorageBase::~FCStorageBase` (`D2Ev`, `0x19xxx`–`0x1a011`, `+1086` to `+3504`) or the constructor
      (`C2Ev+248` `0x1b709` and `C2Ev+296` `0x1b739`). The destructor clears the list. The constructor zeroes it.
      No function stores a non-NULL pointer to `+468` by its literal offset. Any non-NULL head must come from
      a computed base (`add rX, r4, #460`) or from another module. Still no `bl`/`blx` to `0x1b611`, so the
      constructor's caller is unknown. Next: find computed-base stores, or confirm the object's real class.
      Computed-base pass (`add rX, rY, #460`): the list is built by `ReadFactoryDataFile` (`0x1aed9`). It calls
      `TList::Create` (`0x113e0`) on `+316`, `+352`, `+388`, `+424`, `+460`, then `m_ConstructFactoryItemList`
      (`0x1a438`) at `0x1b2bb` (level 1) and `0x1b2cb` (level 2). That routine calls `m_AllocateFactoryItemList`
      (`0x172ec`), which inserts into the item lists with `TList::Add` (`0x1147c`). So `+468` gets its non-NULL
      head from the parsed factory file. It is not a constructor side effect. This corrects the earlier
      "list is never inserted" note.
      RUNTIME RESULT (run `fi14`, `fi_add.py`, breakpoint `TList::Add` at `0x1147c`, 80 hits logged, cap hit):
      two insert sites, both reached, both with `flag=1`:
      - `lr=0x173f5` (the `bl` at `0x173f1`, the `add.w r0, r0, #460` case at `0x173eb`), `this=0x51134`.
        `this+460 = 0x50f68`, the main import object. Head `this+8` = `0x57c98`, count `this+32` rises by one
        per insert (486 … 505 in the logged window). This is the list that fills `+468`/`+492`.
      - `lr=0x1a7cf` (inside `m_ConstructFactoryItemList`), `this=0x50ff0`, a second list, head `0x56108`.
      So the static "unreachable" conclusion above was WRONG. The tbb decode is not the whole story: the
      `+460` block runs at runtime. The main list is filled by `m_AllocateFactoryItemList` from
      `m_ConstructFactoryItemList`, which is called from `ReadFactoryDataFile` (`0x1b2bb`, `0x1b2cb`).
      `TList::Create` does not write the head; `TList::Add` does. The earlier "no writer" claim is withdrawn.
      QEMU run ended by the `timeout 80` wrapper (SIGTERM from the wrapper, `fi14-qemu.log`); no qemu left.
      RUN `fi15` (`/tmp/fsqemu/fi_count.py`, uncapped, logs every main-list insert with its count):
      - The main list receives exactly 709 inserts (count 0 → 708 after the last one, `lr=0x173f5` for all).
        This matches the `+492 = 709` value from `fi9`.
      - All 709 inserts come before the first `ReadFactoryDataFile` call on the main object
        (`this=0x50f68`, `lr=0x1b903`, `r1=2`, `r2=0xb8e8a900`). The order is therefore: build the list,
        then parse. The other calls (`this=0`, `CONSTRUCT` with `r0` of 0x14–0x1d) go to smaller lists.
      - The boot log (`fi15-boot.log`) shows `kfactoryd` faulting at 20 s (`unhandled page fault (11) at
        0xee2ea7f8`), earlier than the 17–71 s range. It maps `/opt/usr/apps/factory-service/share/FactoryIndex_sorted.dat`
        (`b6114000`, size `0x6a000`, `b3:18` inode 2531). That file is NOT in the extracted firmware tree
        (`freesung-tools/.../filesystem-6`), so its contents are unknown.
      - gdb prints "Cannot execute this command while the target is running" at the end of the batch run. It
        happens after the last `MAIN` line, so it does not affect the captured data.
      New open questions: (1) what key the `kfactoryd` lookup uses, and whether it matches anything in the 709
      entries; (2) what `FactoryIndex_sorted.dat` contains and where it comes from; (3) whether the `-1` index
      comes from that sorted file.

      SOURCE OF `FactoryIndex_sorted.dat` (static, no boot):
      - Generator: `FCStorageBase::m_CreateFactoryIndexSorted()` (`0x185bc` in `factory-init`). Strings:
        `/opt/apps/factory-service/data/FactoryIndex.dat` (input) and `/opt/apps/factory-service/share/FactoryIndex_sorted.dat` (output).
        The file is only referenced by `factory-init`, `libTIFactoryData-DVB.so` and `TIFSDaemon` (the reader).
      - Flow: `open` input → `fstat` → `mmap` (`MAP_PRIVATE`, prot RW) → `qsort` in memory → `open` output → `write` → `munmap`.
      - Record size is `284` (`0x11c`, `mov.w r4, #284` at `0x186e6`). Record count is file size / 284 (magic-number division).
      - Comparator `FCStorageBase::m_cmpr` (`0x16920`): unsigned, compares the little-endian `u32` at record offset 0.
        So the sorted file is ordered by the first 4 bytes of each 284-byte record.
      - The card image (`probe-artifacts/mmc/hawkm-card-root.img`) contains the names several times (byte offsets
        ~1455619914, 1456063130, 1456390810, 1456669338, 1458363570), probably as vdfs directory entries.
        The input file contents are NOT extracted yet; a vdfs read of the `/opt/apps/factory-service/data` entry is needed.
      Next: confirm the lookup key in `kfactoryd` (binary search on the same `u32`?) and read `FactoryIndex.dat` from the card image.
      `IMP_ENTRY` hit 26 times, most with `lr=0x24053` (not the import call site `0x2185f`). Only the return
      breakpoints for the 3rd and 4th import calls hit (`0x21878`, `0x21888` +4). The 1st, 2nd and 5th–7th returns,
      the commit return, the unique-item return and the `0xef92` return never hit.
      Checked the bytes: `Import_FactoryData` is `0x217f9` in the ELF (Thumb bit set, real start `0x217f8`). The 7
      `m_ImportFactoryDataToFileSystem` calls at `0x2185a`…`0x218b8` are valid Thumb BL pairs (`0xf7fe 0xfdxx`),
      so the return addresses are correct. `0x24050` is rodata (not code), so `lr=0x24053` is a stale or
      non-call entry, not a real caller. The missing returns remain unexplained.
- Offline VDFS read of the `/opt` partition (p24), to get `FactoryIndex.dat` and `FactoryIndex_sorted.dat`:
  - GPT from `hawkm-card-root.img` gives p24 start `1428226560`, length `1162592256`. The superblock is at
    p24+1024 (`VDFS1002`, `bc54 0400…`). The earlier carve at `1428225536` was off by 1024 bytes; it is wrong.
  - Carving exactly p24 and running `restore_vdfs1002` fails with `native restore exited with status 1`:
    `Directories: 753`, `Files: 1838`, `Error: 136 entries failed to restore`
    (`extent … reaches the metadata area`). The partial tree has `factory-service/data/ParentChildIndex.dat`
    but no `FactoryIndex.dat` or `FactoryIndex_sorted.dat`.
  - Cause of the 136 failures: `vdfs1002/image.rs:278` (`read_file`) rejects any extent whose end is past
    `meta[1].begin`. On this image `meta[1]` is the node area at blocks 6630–12309 (extended superblock at
    `1536+88+16`). Data placed after the node area (for example blocks 12438 and 12544) is rejected, though it
    does not overlap the area. The seed never reaches this because its data sits below block 6630.
    The check should test overlap with `[meta[1].begin, meta[1].begin+length)`, not `begin > limit`.
    The extractor was not changed; this is recorded for the vdfs-unpack project.
  - `FactoryIndex.dat` (inode 2529) and `FactoryIndex_sorted.dat` (inode 2531) are among the 136 failures
    (`extent 12438+106` and `12544+106`). Both are read raw from the slice (4096-byte blocks, offset = block
    × 4096 from the slice start): 1523 records of 284 bytes each. The sorted copy is an exact permutation of the
    unsorted copy (same record multiset, keys non-decreasing).
  - Index contents: 1478 distinct keys, 46 records with key 0. Key `0xffffffff` is absent.
    Key 17 is `db/factory/FC_STORE_LEVEL_FIXED/TYPE` (fields `[17, 0, 295, 145, 6, 2, 0]`).
    Key 18 is `db/factory/FC_STORE_LEVEL_FIXED/SW_MODEL` (fields `[18, 0, 120, 70, 6, 2, 0]`).
    String is at record offset 28.
  - The index holds valid entries for keys 17 and 18, so the `-1` from `GetIntDataFromDB(17|18)` is not
    explained by the index file itself. Whether the store is empty at lookup time is not yet verified.
  - Import probe (`/tmp/fsqemu/fi_items.py`, `TList::Add` at `0x1147c`, logs the record name from each
    inserted 364-byte item). Full uncapped run `fi19`: 2083 inserts over 9 list objects (1316 distinct names):
    - main (`0x51134`): 709, all `FC_STORE_LEVEL_EER_C`. This was the only list counted in `fi15`/`fi16`.
    - `0x5105c`: 102 `FC_STORE_LEVEL_FIXED`, including `TYPE` and `SW_MODEL` (keys 17/18).
    - Other lists (`0x50ff0`, `0x51014`, `0x51038`, `0x510a4`, `0x510ec`, `0x51110`, `0xbddf99b4`) carry
      EER_B, EER_C, FAC, WB and FIXED rows in mixed sets.
    - So the earlier "main list only" view was incomplete. The FIXED table is imported, into a list other
      than the main one. The seven `m_ImportFactoryDataToFileSystem` call sites (`0x2185a`…`0x218b8`) may map
      one-to-one to these lists; not verified.
  - The 2083 inserts show duplicate names across lists (1316 distinct), and one name (`EER_C/ADVANCED`) that
    is not in the index file. It may come from another structure.
  - Open: where each list is written to the store, whether that write succeeds, and whether it happens before
    the first `kfactoryd` lookup of keys 17/18.
  - Probe notes: `fi17` and `fi18` gave partial or empty logs. The `-ex continue` race was the cause: a boot
    with a short gdb connect delay (4 s) failed, while an 8 s delay worked (`fi19`). Use a longer delay.
  - Lookup path (`libTIFactoryData-DVB.so`): `GetIntDataFromDBSrcRes` (`0x4308a1cc`) calls `GetFactoryUID`
    (`0x43089da0`). The SQL is `SELECT Current_value from STORE_LEVEL_<TABLE> where Unique_ID = <key>`
    against `/opt/dbspace/.factory.db`. The table is FIXED, FAC, WB, WB_A, EER_B or EER_C. So the value
    comes from a row keyed by Unique_ID, not from an in-memory list. The in-memory lists from the import
    probe are a separate structure.
  - Lookup probe `fi20`/`fi21` (`/tmp/fsqemu/fi_kf.py`, `GetIntDataFromDB` at `0x4308a3d0`): 67 calls.
    Keys are in `r0` (`0x11` = 17, `0x12` = 18). Status in `r0` on return is 0 for most calls. Reading
    the value through the out pointer `r1` on return failed for most calls, so the per-call values are
    not reliable. Readable results for keys 17 and 18 were `-1` (11 of them, with a few other values not
    read). So the `-1` is confirmed for some calls, but not all.
  - Still open: whether the `STORE_LEVEL_FIXED` rows for Unique_ID 17/18 exist in `.factory.db` when the
    lookup runs. Next: probe `sqlite3_step`/`sqlite3_exec` in `libsqlite3` for the factory DB, or read the
    DB file from the guest after a boot.
  - Import return codes `fi22`/`fi23`/`fi25`: the per-level values are NOT a usable error signal.
    - `Import_FactoryData` (`factory-init` `0x21840`–`0x218c8`) ORs the seven level returns into `sl`
      and never branches on it. The return to `CommitTransaction` is also not checked.
    - `m_ImportFactoryDataToFileSystem` (`0x20440`) returns `sl`, which is used as a COW string pointer
      (`_M_leak_hard`, `_M_mutate`, `append` around `0x21050`). The values `0x9d690`, `0x9d758`, `0x9cd08`
      vary between runs, and some are unreadable from gdb (`Cannot access memory`). So they are not
      error strings.
    - The `commit` probe at `0x218c8` also fires from other code paths (repeated hits with `r0=0x6f` and
      changing `r10`), so it is not a clean per-import signal either.
    - Conclusion: a non-zero level return does not mean a failed level. The import success question
      needs the database contents, not these return values.
  - In-guest database check: blocked for now. The boot runs systemd through `init.wrapper`, so the
    sysvinit `inittab` line for agetty on `ttySAC2` never runs. That explains the silent second UART.
    `DTB /chosen/bootargs` overrides `-append`, so the earlier `init=/bin/sh` attempts never took effect.
    With a scratch DTB (`init=/bin/sh` at the end of bootargs) the guest reaches a `sh-3.2#` prompt on
    `ttyS0`, but typed input never reaches it (no echo, no output, even after filling the 16-byte FIFO).
    RX probe results (monitor `xp` on UART0 `0x00190a00`, held-open client):
    `UFCON=0x1` (FIFO on), `UCON=0x3085` (RX interrupt mode), `UINTM=0`, `UTRSTAT=0x6`, `UFSTAT=0`.
    Bytes sent to `ser.sock` never raise `UTRSTAT` bit 0 or fill `UFSTAT`. `UINTP=0x2` (TX only).
    So QEMU's chardev input does not reach `exynos4210_uart_receive()` (`hw/char/exynos4210_uart.c:559`).
    Plain `-serial unix:` variant (`ser2.sock`): no guest output in 130 s, so the boot itself did not
    reach the console in that run. Not conclusive, and the test was abandoned.
    Open: why the socket input path does not deliver to the UART. Options: check the chardev frontend
    connection with `info chardev` while a client is attached, or use a gdb breakpoint on
    `exynos4210_uart_receive`.
    Option 2 (dump `.factory.db` without typed input) is blocked, three ways:
    - `init=/bin/sh -c "..."` in bootargs: the kernel keeps the leading `"` literal and drops the rest. The shell fails with `unexpected EOF while looking for matching '"'`.
    - Splitting the command with tabs (`echo\tX`): the kernel splits on tabs too, and the command runs with the wrong arguments.
    - Offline: `vdfs-unpack --extract .factory.db --parent 849` fails with `object 883 extents cover 12844 blocks, fork declares 41`. The extent reader misparses this file. Relaxing the `data_limit` guard in a `/tmp` copy is not enough. The journal `.factory.db-journal` (object 862) has the same problem. A hot journal is present on the card, so the DB may need rollback before reading.
    Remaining routes: an initramfs with `/init` as a script (needs the guest's ARM `sh`, `mount`, and libs copied in), or fix the VDFS extent reader for this file.
    Host-side progress (no guest needed):
    - The fork record for object 883 reports `size=167936` (41 blocks of 4096). Its extent list is `4004×23`, `0×12797` (garbage), `17×23`, `13967×1`.
    - Block 4004 (byte offset 4004×4096 in `opt-p24.img`) starts with a valid SQLite header: `page_size=4096`, `pages=41`, `change_counter=663`. So the 41-page size is correct and the extent start is right.
    - A contiguous 41-block read from block 4004 is malformed (`integrity_check` fails). Several plausible orderings of the listed extents also fail. The rest of the extent list (or the page map) still needs decoding. The `0×12797` entry suggests a field-width or overflow decoding bug in `format.rs` (`file_extents`).
    - The host has no `sqlite3` CLI. Use Python's `sqlite3` module.
    - Decoder check: sibling DBs in `dbspace` (`.mida.db`, `.app_info.db`, `.privacy.db`, `.ace.db`, `.notification.db`, `.rua.db`) extract fine. The fault is specific to object 883.
    - Raw fork slots for 883 (`format.rs` `parse_fork`, offsets 16+16·n): `4004×23`, `0×12797` (not an extent), `17×23` (all zeros on the card), `13967×1`, then unused. The fork accounts for at most 24 of 41 pages. Overflow (`image.rs` `overflow_extents`) has no owner 883, so the fork alone cannot locate pages 24–41. The reader is not wrong; the record is incomplete. The repo decoder is unchanged.
    - Pointer-map page (block 4005) confirms the layout: pages 24–41 are non-root b-tree pages (type 5) under interior pages 18–20.
    - Journal 862 (magic at block 13928) is a rollback journal with 2 records: page 1 (old header) and page 52 (truncated tail). Original size was 53 pages, now 41. Pages 24–41 were not journaled.
    - Partial recovery: pages 1–23 from block 4004, pages 24–41 zero-filled, opened with Python `sqlite3`. This gives a usable read of the tables whose b-trees fit in pages 1–23.
    - Card-state answer (from that partial copy, `/tmp/fsqemu/vdfs/db/partial41.db`):
      - `STORE_LEVEL_FIXED` is a single leaf (root page 3, 102 rows), so it is complete. 98 of 102 rows are `-1`. Key 1311 (`SVC_PANEL`), 17 (`TYPE`), 18 (`SW_MODEL`) are all `-1`. Key 3104 is absent.
      - `STORE_LEVEL_FAC` (74 rows), `STORE_LEVEL_WB` (138), `STORE_LEVEL_EER_B` (277): none of keys 1311, 3104, 17, 18.
      - `STORE_LEVEL_EER_C` is unreadable (missing pages). `STORE_LEVEL_WB_A` has 0 rows.
    - Conclusion: the card's factory store has no real `SVC_PANEL` value. `-1` is the same sentinel `kfactoryd` returns. The guest reads the same `-1` because the card DB has no panel identity. The QEMU run uses `snapshot=on`, so guest writes from `factory-init` do not persist to the card.
    - Source of the value: `usr/share/factory-data/Factory_Part1.dat` (in the guest root image, under `filesystem-6/root`) holds the `db/factory/FC_STORE_LEVEL_FIXED/SVC_PANEL` record at byte 17532304. Its 356-byte record has a `ff ff ff ff ff ff ff ff` sentinel at +256 and a real value `0x66e` at +264 (the card DB has `-1`). The value is not decoded beyond that.
    - Writable-card boot (`/tmp/fsqemu/persist/card-run.img`, no `snapshot=on`, 170 s): the guest did write the card (996 blocks in the `/opt` slice, including `.factory.db` pages 1, 2, 3, 12, 18, 19, 20). Only three FIXED/FAC values changed (keys 281, 46 and FAC 24). `SVC_PANEL` stayed `-1`. So the boot does not import the panel record, even with a writable card. This corrects the item above: `snapshot=on` is not why the value is `-1`.
    - The boot prints `non-factory production mode`, which means the EEPROM flag at `0xf212` read `0xff`. The import gate needs flag `== 1` and EEPROM `0x5671 == 0x63` (`CheckEEPromForAutoImport`). The model sets only the flag.
    - The flag read is nondeterministic on the same card and binary (about 30% of parallel boots give `[ff]`). The earlier `eep_ptr` hypothesis is wrong. Trace-backed mechanism (scratch build with temporary prints, reverted):
      - In the `[ff]` and `[1]` runs, the EEPROM block read from `0xf200` delivers identical bytes. `0xf212` reads `0x01` in both.
      - The only difference before the `automation-helper` print is one extra guest DS read while the controller is busy (`busy=1 timer=1`, the 100 µs completion is still pending). The model returns the stale DS value, so the guest's byte alignment shifts in that run.
      - So the race is between the guest's DS poll and the fixed `SDP1406_I2C_XFER_NS` completion timer, which runs on host-paced virtual time. It is not an `eep_ptr` snapshot problem.
      - `-icount shift=6,align=off,sleep=off` gave `[1]` in 4 of 4 boots. One of them hit a real EEPROM read timeout at guest 261 s (`[i2c fail] CH:4 Device ID:0xa0`), so icount does not make the run fully repeatable.
    - Open: what real hardware returns for a DS read while busy, and whether the driver should wait for the IRQ. The model comment at `hw/arm/sdp1406.c:~476-490` says the driver reads the byte counter before it waits, and the 100 µs completion (`SDP1406_I2C_XFER_NS`) is a guess. A driver poll that reaches DS before 100 µs of virtual time has passed sees a stale byte. Under TCG, virtual time runs with host time, so the window depends on host load. The guest i2c driver is only in the guest image (no source in the repo), so its DS loop has not been disassembled yet.
    - Options (not decided): (a) keep the 100 µs timing and run boots with `-icount shift=6,align=off,sleep=off` for repeatable results; (b) raise `SDP1406_I2C_XFER_NS` (changes all i2c timing; the i2c tests and item 6 must be re-checked); (c) disassemble the driver's DS loop to find the intended wait. Pending the user's choice.
    - Driver side (kernel `samsung,sdp-i2c`, linked at `0xC0008000`): the DS byte is read only in the IRQ receive handler at guest PC `0xc0293ba0` (`ldrb r1,[r3,#12]`). The address-done handler (`0xc0293958`) does not read DS for a read transfer.
    - PC trace (scratch build, EEPROM channel only, 8 wall-clock boots: `[1]` in 5, `[ff]` in 3): every busy DS read is at `0xc0293ba0`. The first divergence is one extra completion before the read address in the `[ff]` runs. After that, the `[ff]` runs read DS while `busy=1 dp=1 timer=1`. The `[1]` runs never read DS while busy in the first events. So the stale read is the differentiator, and it is caused by an IRQ that the model raises for the rd_arm completion while BUSY is still set.
    - Correction: the rd_arm IRQ is not the cause. A second trace (START, QUEUE, DONE, STATSTART, DSADDR events) shows that in the `[ff]` runs the driver's START write (STAT bit 0x20) for a DS-initiated address arrives after the address completion. `sdp1406_i2c_xfer_done` had already cleared `xfer_from_ds`, so the START was treated as a new transfer. That created an extra completion and an early DS read.
    - Fix (option c, applied): `xfer_from_ds` is now cleared only by the START write, and a START during an in-flight transfer is ignored. Inferred from the driver's sequence, not measured on hardware: real hardware's START-after-completion behavior is unconfirmed.
    - Result: 16 wall-clock boots on the fixed build, all `[1]`. Repeated CH:4 `0xa0` timeouts still occur in several boots, so the i2c timeouts are not fully resolved. Next: find the source of the remaining CH:4 timeouts. Also decide whether `0x5671 = 0x63` belongs in the model.
    - Next: (1) pick one of the options above (a, b, or c); (2) decide whether `0x5671 = 0x63` belongs in the model (the retail EEPROM value is unknown); (3) only then re-test whether `SVC_PANEL` is imported. Decide whether the black scanout is blocked by this at all, since the TCON black framebuffer is unproven.
  - Lookup order `fi23` (`/tmp/fsqemu/fi_order.py`, combined commit and lookup probe): 66 lookups, all for
    keys 17 and 18, all after the level imports. Of the readable values, 41 were `-1`. The `begin`/`commit`
    lines did not print in this run, so the commit order against the lookups is still unproven.
  - Correction: the earlier note "the import never ran" is outdated. The `fi19` insert log (2083 inserts)
    and the `fi22` returns show the import runs. The open question is whether the FIXED rows reach
    `.factory.db`, not whether the import runs.
  - `kfactory_drv` request flow: `tztv_kf_get_svc_panel_year` requests key `0x51f` (1311,
    `FIXED/SVC_PANEL`) and `tztv_kf_get_panel_type` requests `0xc20` (3104, not found in the index).
    `kfactory_drv_get_data` waits up to 5000 ms and then calls `kfactory_restore_user_app`.
