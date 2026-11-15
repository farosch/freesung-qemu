# TODO

Open investigation leads for the SDP1406 (HawkM) QEMU board. Use the production kernel
`03-uImage` only. Current verified behaviour is in [DEVELOP.md](DEVELOP.md).

Items are listed in the order they should be investigated. Earlier items unblock later ones.

## 1. i2c timeouts (CH:7 fixed; CH:4 and CH:10 timeouts residual)

- Current model (`hw/arm/sdp1406.c`, qtests in `tests/test_qemu_sdp1406_i2c.py`, see DEVELOP.md):
  - `+0x18` is a mask: a set bit masks the channel IRQ (`sdp1406_i2c_update_irq`). Inferred from the
    driver's `bic`/`orr` pattern, not verified on hardware. Reset value assumed 0.
  - Each transfer completes on `xfer_timer` (100 us virtual). Completions are counted per channel in
    `irq_latched[bit]`, so queued completions are not lost.
  - A DS write completes asynchronously. A STAT write with busy clear opens the address phase; `0x40`
    (Sii9777, item 2) is NACKed, other addresses are ACKed.
  - The EEPROM START race is fixed (commit `78b5f97`, item 13).
  - All 16 DTB controllers are modelled in two groups.
- Driver facts (kernel `samsung,sdp-i2c`, virtual base `0xC0008000`):
  - Transfer function `0xc0293edc`. Waiter `0xc0294330` sleeps for 2*len+250 jiffies. Timeout print
    `[i2c fail] CH:%d Device ID:0x%x timeout` at `0xc0294428`.
  - Waiter prints the timeout when the remaining-work counter `[r6,#16]` is still nonzero. IRQ handler
    `0xc02938fc` decrements it per completion. DS byte read at `0xc0293ba0`. Exit ack is write-1-to-clear.
  - `[r4,#12]` is the `struct i2c_msg` cursor (flags at +2, len at +4). `[r4,#48]` is the shared IRQ
    block (status +0, mask +4).
- Ruled out:
  - Poll-budget race: the 400-iteration poll cannot expire, since completions land about 110 us after arming.
  - Per-byte timing: `SDP1406_I2C_XFER_NS` at 22500, 90000 and 100000 still gives timeouts.
  - `-icount shift=auto` removes the timeouts only in a small sample (6 runs), not conclusively.
- Residual, intermittent timeouts:
  - CH:4 `0xa0` (EEPROM) and CH:10 `0xc8` (`panel@64`, FRC TCON, the model ACKs `0x64` with no device).
  - Last A/B (six 120 s boots): pre-latch-fix 20 CH:10 and 19 CH:4 timeouts; with the latch fix 3 CH:10
    and 7 CH:4. Five of six fixed boots still log at least one timeout.
  - Lead: the driver acks bit 4 twice while a second completion is latched. The second ack clears the
    latch. The CH:4 CON word keeps ACK (`0x140`), so the final-byte NAK path never arms. Open: whether
    the second ack is legitimate.
  - The timeouts are timing-sensitive. Instrumentation hid them, so the stall has not been captured.
- Open: CH:5 and CH:6 have no surviving log evidence and stay unverified. The FRC driver's read
  sequence is unchecked (no driver source here).

- CH:4 race (root cause found, partly fixed): a read's data byte can still be pending when the
  address write for the next transfer arrives. The second start then re-arms the pending timer, so
  two transfers merge into one completion and the address waiter is never woken.
  - Fixed in the model: address-phase writes queue their own completion (a data byte merges into the
    pending transfer). Each queued transfer carries its own `SDP1406I2cXfer` snapshot of `rd_arm`,
    `eep_sel` and `sii_sel`, so a queued write address no longer overwrites a pending read's state.
    Tests: `test_address_written_during_pending_read_gets_own_completion`,
    `test_data_write_during_transfer_merges_into_it`,
    `test_queued_write_address_keeps_pending_read_data`.
  - Effect: CH:4 `0xa0` timeouts remain. A/B on 60 s to 120 s boots showed a small improvement in
    bus-idle timeouts and no clear change in CH:4 `i2c fail` counts (within noise).
  - Open: the reviewer's claims that LRB is never set and that one IRQ per DS byte is expected are
    untested. Test them before acting on them.
  - Next: trace the CH:4 `0xa0` wait with a file trace in a scratch build, to see whether a START is
    still merged or whether the driver's wait is too short.

### EEPROM and MAC address

- Current boot (20 s, README command): `eth0` does appear in serial. The name is set by
  `vendor-config`, which logs `Set MAC Address of eth0 [00:00:00:00:00:00]`. The kernel rejects
  that address: `sdp-gmac 120000.ethernet: MAC Address is not valid! requested 00:00:00:00:00:00`.
  So the interface exists, but without a usable MAC.
- Fixed: the driver read MAC_Address0 (`0x040`/`0x044`), which the model did not implement. Reads
  returned 0 and writes were dropped. The model now backs those registers with the NIC address
  (qtests `test_mac_address_*`). The kernel still reports the zero address after the fix, so this
  was not the cause of the zero MAC.
- Cause: `vendor-config` reads the MAC from the EEPROM with `eeprom_read_mac` (offset `0x1e04`).
  The EEPROM is `s24c512c@50` on CH:4 (see item 1).
- Fixed: the model has a 64 KiB EEPROM slave on CH:4 `0xa0` (see DEVELOP.md). It holds a
  synthetic MAC `02:53:44:14:06:01` at `0x1e04`. A 20 s boot shows `Set MAC Address of eth0
  [02:53:44:14:06:01]`, and the kernel no longer reports an invalid address.
- Open: the EEPROM content is synthetic. Replace the MAC with the real device value if it is
  available (no EEPROM dump exists in the extracted tree).
- Open: the EEPROM WP polarity is inferred from the kernel driver's behaviour and a runtime trace.
  It is modelled as bit 6 high = writes allowed, reset unprotected (`hw/arm/sdp1406.c`,
  `SDP1406_EEP_WP_OPEN`). Check it against the real chip or a second device trace if one becomes
  available.
- Not yet checked: the `Clk ... IRQ is` line (absent from this boot).

## 2. Sii9777 HDMI bridge on CH:7 (modelled; residual CH:4 and CH:10 timeouts open)

- Done: the Sii9777 is modelled at DS byte `0x40` on CH:7 as a 64 KiB register file (16-bit
  sub-address, high byte first, no page wrap). The NACK is gone. The 90 s README boot no longer
  logs the 120 CH:7 `incomplete xfer (-6)` errors, and `Sii9777 Instance creation SUCCESS` appears.
  Tests: `Sdp1406SiiTests` in `tests/test_qemu_sdp1406_i2c.py` (35 i2c tests pass).
- Done: the IRQ latch is a per-channel count, so queued completions are not lost. This cut CH:10
  `0xc8` timeouts from 20 to 3 in six parallel 120 s boots. Residual CH:4 and CH:10 timeouts remain.
- Open: the Sii9777 driver's device-ID and firmware-version checks use register values we do
  not model yet. Check the next boot log for `Sii9777-I2C` errors and disassemble
  `tztv_hdmi_switch_api.ko` if any appear.

## 3. SELP hold and RCU stalls (partly resolved)

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

## 4. TrustZone SMC (open)

- `tzdev.ko` issues SMCs from `tzdev_ipc_thread` (module offset `0x1b30`) and `0x664`. The first
  has `r0=0x14`. The loop waits with `tzdev:tzio_exchange_message: tzdaemon thread has been
  waiting 19 > 5 s`. `/usr/bin/tzdaemon` is present, but its start is not in the log.
  Re-verified on `boot-a0.log`: the wait count reads `7 > 5 s`, not 19. The count varies by run.
- The monitor stub returns `r0=0`. Open: whether the guest expects `0`, and whether the daemon
  is the real blocker.
- Boot-path caveat: the bootloader path faults on the SMC at `IFAR 0x200008` (see
  `freesung-bootloader/TODO.md`). The stub runs only on the control path.

## 5. Card p26 (`/mnt/drm`) superblock (open, runs conflict)

- Host-side `mkfs.vdfs` on a carved p26 writes `VDFS1002` at offset 1024.
  Re-verified: the shipped `hawkm-card-root.img` has p26 at LBA 5062241 with `VDFS1002` at byte 1024.
  This is the host image, not the state after a guest mkfs.
- One run reported a guest mkfs superblock after the first failed mount. Another run, on a writable
  copy, found p26 still zero after the guest mkfs, and `Cannot restore /mnt/drm`.
- `psa_mount.sh` does not log the mkfs exit status, so a failing exit is not visible.
- The `non-auth` denials for `ld.so.preload`/`ld.so.cache` (`check_execution_available`,
  `0xC01759D4`) are intermittent and not explained.
- Next: re-run one boot from a fresh copy, check the superblock, and log the mkfs status.
  Update: `mkcard.py --firmware-root` now formats p26 host-side (item 13); the guest mkfs question is still open.

## 6. `mali.ko` and device nodes (open)

- `rc.sysinit` loads `/lib/modules/mali.ko`, but the real path is `soc/mali.ko`.
- `rc.sysinit` chmods `/dev/mali`, which the driver never creates (it creates `/dev/mali0`).
- The `chmod /dev/dri/*` line runs before `sdp_drm` loads, so its error is a timing artifact.
- Re-verified in `rc.sysinit`: line 39 loads `/lib/modules/mali.ko`, which is not at that path
  (the module is `kernel/soc/mali.ko`). Line 48 chmods `/dev/mali0`. Line 153 chmods `/dev/mali`.
  The `/dev/mali` line is the one the driver does not create.
- These are scratch-image observations. Check the production script before changing the model.

## 7. Initrd `/init` cannot be executed (open, low priority)

- The kernel reports `Failed to execute /init` with no errno, for both the probe init and a
  freestanding one.
- This route is only needed for a root shell. Production runs systemd, and `console-shell.service`
  (`WantedBy=getty.target`) is not pulled in by the default target. The route would need a
  `bootmode.target.wants` link in a bind-mounted `/etc/systemd/system`.
- Next: find the initramfs or ELF exec failure before using this route.

## 8. Secondary-core path (open)

- All cores run `_start` in the bare-metal UART program. It parks cores other than CPU 0, but the
  Linux secondary-core path is not checked.

## 9. Clock registration error (explained, runtime not confirmed)

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

## 10. Micom device registration (drop explained, judged benign; not the TCON/scanout blocker)

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
  (device-3 handler treats `0x29` as a no-op) fits. Item 10 is not the black-framebuffer blocker.
  Item 10 remains open only for the registration order, which is a model timing detail.

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
- The kind-dispatch breakpoint (`0xc0265cc0`) never fired, so the kind-`0xc6` attribution (`0xc026636c`) is not supported.
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
    `0xc025952c`, `0xc0259a88`.
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
- Item 10 stays open. No `0x96` producer exists in QEMU, so the `state[100]` branch is still unreachable in the model.

## 11. `system_server` aborts on the TV power path (open)

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

## 12. Vendor-config WIFI_VENDOR read fails at boot (open, lead for net-config watchdog)

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
- Open: the link to the `net-config` watchdog kill (item 10 / coredump trace) is not confirmed. The
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

## 13. Black scanout: micom and panel identity chain (open lead, not proven)

### Symptom and panel identity

- A 90 s SDL boot (README command) with a monitor `screendump` at 60 s is 1920x1080 all zero.
  Xorg and CoreGL start, and `ECHO-FP` reads a 1920x1080 panel, but nothing is scanned out.
- Panel identity is never set: `tztv_get_svc_panel_year` returns -1, `tztv_get_panel_type` prints
  garbage, and `detect_tcon` logs `Unknown TCON type`, so `tcon_loaded=[0]`.
- The year and type come from `kfactoryd` (`/dev/kfactory`), not from micom. `tztv_kf_get_svc_panel_year`
  requests key `0x51f` (1311, `FIXED/SVC_PANEL`), and `tztv_kf_get_panel_type` requests `0xc20` (3104, not in
  the index). `kfactory_drv_get_data` waits up to 5000 ms, then calls `kfactory_restore_user_app`.
- Micom: `msg-wt61p807` times out on several acks (`[30]`, `[68]`, `[84]`, `[98]`, `[AC]`, `[B3]`, `[D7]`), and
  `tzdaemon` reports `thread has been waiting` many times. The HDMI switch reports `Boot failure... [JackBoston]`
  and `HDMI BKSV is still INVALID`. `fb0` is never registered (item 14).
- Micom `0xc6` (product info, 5-byte payload written to `micom_prd_info`, byte 3 read by
  `tztv_kf_get_graphic_mix_spec`) has no model entry and gets the default reply. A zero-payload echo changed
  nothing (150 s, `-snapshot`), so an echo alone does not reach the waiter. The real payload is unknown, so a
  non-zero payload is untested.

### Factory store and import

- Store: `/opt/dbspace/.factory.db`, built by `FCStorageBase::Import_FactoryData` from
  `/usr/share/factory-data/Factory_Part1.dat` (17.6 MB) and `Factory_Part2.dat`. The import is attempted at boot
  (`factory-init` `main` calls the gate at `0x19180`, then `Import_FactoryData` at `0x217f8`). A gate return of 2
  still reaches the import. The boot log has no import output.
- Gate `CheckEEPromForAutoImport` (`factory-init` `0x19180`) needs all of: (a) `/opt/usr/factory_backup_flag`
  absent; (b) EEPROM `0xf212` == 1 (the model sets this); (c) EEPROM `0x5671` == `0x63` (`m_ReadEEprom(0x1871)`
  plus `0x3e00`; the model holds `0xff`); (d) `/opt/usr/apps/factory-service/data/auto_imported` absent.
- The import fills in-memory item lists (`ReadFactoryDataFile`, `m_ConstructFactoryItemList`,
  `m_AllocateFactoryItemList`, `TList::Add`). Fixed-table rows (including `TYPE` and `SW_MODEL`) go to a list
  other than the main one. Whether those rows reach `.factory.db` is unverified. The level return values are
  not a usable error signal.
- `Factory_Part1.dat` holds `SVC_PANEL` at byte 17532304 with value `0x66e` (record +264). The card DB has `-1`.
- The shipped p24 contains a live `factory_backup_flag` and `auto_imported`, which block gates (a) and (d).
  `build-backup-data.sh` touches the flag before `mkfs.vdfs` and removes it after, so the seed keeps it.
  Nothing on the device creates or removes either flag at boot.

### Card and gate experiments (scratch, not in the repo)

- Control (pristine card, 150 s): `tztv_get_svc_panel_year: [-1]`, `Unknown TCON type`, `tcon_loaded=[0]` at ~4.8 s.
- No-flag seed (`/tmp/fsqemu/seed/noflag.img`, rebuilt with `vdfs-unpack` and `mkfs.vdfs` under `qemu-arm -L`;
  the name set matches the original except `factory_backup_flag`), `0x5671` unset: no import, `detect_tcon`
  never runs, and the panel line reads `NOT BOE panel!(NONE_15)`.
- No-flag seed plus `0x5671 = 0x63`: the import runs and creates `auto_imported`. The panel line reads
  `NOT BOE panel!(T�)`, and `detect_tcon` still does not run. Boot order differs from the control, so this is not
  a like-for-like TCON comparison.
- The same run on a persistent card: `kfactoryd` crashes (SIGSEGV) at ~15 s. `vdfs-unpack` then reports 64
  failed objects, including `.factory.db` (object 1660). The `-snapshot` run reproduces the crash at ~17 s
  (`can not run multi process!!` twice), so the crash does not depend on persistent card state.
- Writable-card boot (no `snapshot=on`, 170 s): the guest writes 996 blocks in the `/opt` slice, including
  `.factory.db` pages. Only three FIXED/FAC values change (keys 281, 46, FAC 24). `SVC_PANEL` stays `-1`. That
  run printed `non-factory production mode`, so EEPROM `0xf212` read `0xff` (before the START fix in item 1).
- Kernel route (not done): the VDFS CRC check (`CRC missmatch`) in the uImage would let a renamed-flag seed mount.

Open leads for item 13:
- The key 17/18 lookup returns `-1` although the sorted index has valid entries. Unknown whether the FIXED rows
  reach `.factory.db` before the first lookup.
- The `kfactoryd` SIGSEGV (about 15–22 s) has no captured faulting copy yet.
- `0x5671 = 0x63` is not in the model. The gate needs it, and the retail value is unknown.
- Whether `SVC_PANEL` imports after a `[1]` boot is untested.
- UART RX: socket input never raises `UTRSTAT` bit 0 (`hw/char/exynos4210_uart.c:559`), so typed guest input is blocked.
- Dumping object 883 offline needs the VDFS extent fix (`vdfs1002/image.rs:278`), or another route.
- The black scanout is not explained.

## 14. No framebuffer device is registered (open, current firstscreen blocker)

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

## 15. `fastvideo` framebuffer pointer (open)

- The MFC firmware fault is fixed (RAM2). `fastvideo` runs `FB:INIT`.
- Re-verified: `0xd6c00000` is not present in `usr/bin/fastvideo` as a raw little-endian word or
  as ASCII (it is not in the ELF symbol table). The pointer is
  computed at runtime, or lives in a shared library. Open: which register or buffer it reads, and
  why it faulted there.

## 16. Scanout stays black (open)

- Scanout `0x3c500000`, 1920x1080 XRGB8888. Screendumps at about 140 s are all zero.
- Fixed: the Xorg cursor abort at about 37 s (store to PA `0x22600000`). The model maps
  `0x22600000`-`0x227fffff` (see DEVELOP.md).
- Blocked by items 19 (firstscreen wait, resolved) and 14 (no fbdev). Recheck the scanout after both.

## 17. Bluetooth controller in the model (device added; firmware path open)

- `btapp` launches through `btapp-service.service` (`WantedBy=starter.target`, user unit), which
  is not reached in a 20 s boot.
- The DTB (`05-dtb.bin`) has no Bluetooth node (`bluetooth`, `hci`, `bcm` absent). A model device
  would not bind in the guest kernel.
- Transport is USB, not UART. The kernel module
  `lib/modules/3.10.30/kernel/drivers/brcm/btusb/btusb.ko` is the Broadcom USB driver. It binds
  to class `E0/01/01` (wireless controller) for any vendor, and to vendor `0A5C`. It has no
  stock `bluetooth`/`hci` core module. It exposes HCI through the char device `/dev/btusb0`
  (`btusb_lite_*`, `btusb_lite_ipc_hci_cmd_hndl`).
- Firmware download: `usr/bin/btdl-1.0.0` runs `btdl --device /dev/btusb0 --minidrv usb_single.hex
  --image btchipfw_single.hex` (or `btchipfw_combo.hex`). The three images are in
  `usr/apps/bluetooth-frwk-service/data/`. This matches the Broadcom "minidriver, RAM image,
  launch" flow.
- Userspace: `bt-core`, `bt-service`, and `bt-monitor` in `usr/bin/` open `/dev/btusb0`.
  `btapp` (`usr/apps/org.tizen.btapp/bin/btapp`) only drives the SmartRC remote firmware update
  from `usr/apps/org.tizen.btapp/fwdir/SC_BT_Y15_*.hex`. It is not the TV's chip firmware.
- `libbt-vendor.so` (`usr/lib/`) is a Bluedroid UART vendor library (`BCM4325`/`BCM43241`,
  `/dev/ttyHS99`, rfkill). It appears unused for this USB transport. Its `ttyHS99` path cannot be
  created by this kernel: the `sdp-uart` driver names ports `ttyS%d`, and no `ttyHS` string
  exists in the kernel or modules.
- Model: `usb-bcm-btusb` (`hw/usb/dev-bcm-btusb.c`, patch `0004`) is on the EHCI slot 0 root hub at
  high speed, with VID:PID `0a5c:21e8` and class `E0/01/01`. It answers HCI Reset, Write Scan
  Enable, Read Local Version/Features and Read BD_ADDR, and acknowledges the vendor commands
  `0xFC2E`, `0xFC4C`, `0xFC4E`. The Broadcom H2 framing is EP0 class request `0x20`, events on
  EP1 IN, ACL on EP2 IN/OUT.
- Open leads for the device:
  - RAM image contents are not kept, so the chip never runs the uploaded firmware. Confirm whether
    `btdl` or `btusb` checks the launch result before it reports the controller as ready.
  - The unknown vendor commands (`fc1c`, `fc1e`, `fc4d`, `fc6e`, `fc79`, `fcb7`, `fcc2`, `fcc3`,
    `fcfa`) need implementing or a decision that they can fail. Check the status the guest
    expects.
  - The guest sends no `Read BD_ADDR` (`1009`). Find out why, then decide whether the device must
    answer it.
  - The VID:PID is a guess. Check `btusb.ko` (or the `btdl` firmware table) for the ID list.
  - The BD_ADDR is a placeholder. SCO is not modelled.
  - `TIFSDaemon` still crashes (above), so `btapp` cannot start in a normal boot.
- Guest-side result (200 s boot, `btdl`/`bt-service` active): the guest sends 37 HCI commands as
  EP0 class `0x20` requests, and the device answers each one on EP1 IN. The sequence runs Reset
  (`0c03`), Read Local Version (`1001`), Supported Commands (`1002`), Features (`1004`), Buffer
  Size (`1005`), LE and page/inquiry setup, Write Local Name (`0c13`), and Write Scan Enable
  (`0c1a`). Vendor commands `fc1c`, `fc1e`, `fc4d`, `fc6e`, `fc79`, `fcb7`, `fcc2`, `fcc3`, and
  `fcfa` are sent too, and most get the unknown-command status. `Read BD_ADDR` (`1009`) is not
  sent. No `hci0` appears in the log, and the guest never opens `/dev/btusb0`, because the stack
  uses the btusb control path. `btusb` `usb_register_dev` never logs "attached to minor".
- Guest bring-up check: `Sdp1406BtGuestBringUpTests` (`test_qemu_sdp1406_usb.py`, patch `0005`)
  boots with `-trace bcm_btusb_hci_cmd` and asserts that Reset (`0c03`), Read Local Version
  (`1001`) and Write Scan Enable (`0c1a`) arrive in that order. It passes in about 20 s with the
  kernel artifacts. The trace shows 37 HCI commands on the same boot, matching the sequence above.
- Open (pre-existing, not from the BT change): `Sdp1406LinuxSerialTests` fails with the artifacts.
  No `Linux version ` line reaches the UART within 60 s, though `BTPCM` logs on the console. The
  same test also fails on the build before patch `0005`.
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
  boot. This is the same `0x29`/`0x2a` reply tag question (item 10).
- No device has been added. Adding one needs the mapping above confirmed.

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

## 19. Firstscreen waits for the kdbus bus (resolved)

- The 240 s baseline on the fixed build prints `Kdbus system bus found!` and launches
  `org.volt.firstscreen`. The wait no longer blocks; see item 14 for the next blocker.

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
