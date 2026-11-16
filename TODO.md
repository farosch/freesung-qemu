# TODO

Open investigation leads for the SDP1406 (HawkM) QEMU board. Use the production kernel
`03-uImage` only. Current verified behaviour is in [DEVELOP.md](DEVELOP.md).

Items are listed in the order they should be investigated. Earlier items unblock later ones.

## 1. i2c timeouts (CH:7 fixed; CH:4 and CH:10 timeouts residual)

- Current model (`hw/arm/sdp1406.c`, qtests in `tests/test_qemu_sdp1406_i2c.py`, see DEVELOP.md):
  - `+0x18` is a channel IRQ mask (set bit = masked). Inferred from the driver's `bic`/`orr` pattern,
    not verified on hardware.
  - Each transfer completes on `xfer_timer` (100 us virtual). A completion waits while its channel's
    IRQ status bit is still set (back-pressure, see DEVELOP.md). The driver reads the byte and acks the
    bit in its IRQ handler, then the completion raises on the next step.
  - Address-phase writes queue their own completion, and each queued transfer keeps its own
    `rd_arm`/`eep_sel`/`sii_sel` snapshot (`test_address_written_during_pending_read_gets_own_completion`,
    `test_data_write_during_transfer_merges_into_it`, `test_queued_write_address_keeps_pending_read_data`).
  - A START written after a DS-initiated address completes is ignored (commit `78b5f97`).
  - EEPROM write-protect: word `0x005c1164`, bit `0x40` (`SDP1406_EEP_WP_REG`, `SDP1406_EEP_WP_OPEN`).
  - All 16 DTB controllers are modelled in two groups.
- Driver facts (kernel `samsung,sdp-i2c`, virtual base `0xC0008000`, `03-uImage`):
  - Transfer kick-off `0xc02942d0` (sets state 1); transfer function `0xc0293edc`.
  - Waiter `0xc0294330` is a bounded `schedule_timeout` of `2*len+250` jiffies. Timeout print
    `[i2c fail] CH:%d Device ID:0x%x timeout` at `0xc0294428`.
  - IRQ handler `0xc02938fc` decrements the message count per completion. DS byte read at `0xc0293ba0`.
    Exit ack is write-1-to-clear.
  - `[r4,#12]` is the `struct i2c_msg` cursor. `[r4,#48]` is the shared IRQ block (status +0, mask +4).
- Residual timeouts (latest status):
  - CH:4 `0xa0` (EEPROM) and CH:10 `0xc8` (`panel@64`, FRC TCON; the model ACKs `0x64` with no device).
  - Back-pressure build vs baseline, 8 parallel 200 s boots each (same host, batches run back to back):
    - Baseline (latch): 13 timeouts in 7 of 8 boots. CH:4 `0xa0` 7 times in 6 boots, CH:5 4 times in 2
      boots, CH:6 2 times in 1 boot.
    - Back-pressure: 17 timeouts in 3 of 8 boots. CH:4: none. CH:5 14 times in 3 boots, CH:6 once.
    - Reading: the CH:4 `0xa0` stall went away in this sample, and the CH:5 timeouts (also present in the
      baseline) went up. The totals are not a clear improvement, and the sample is small and load-dependent.
      The fix is kept because it follows the driver's one-IRQ-per-byte handling, but it is not shown to fix
      the timeouts. Next: determine whether the CH:5 timeouts come from a deferred completion that the driver
      does not ack for a long time.
  - Latest (four fresh-card boots, WP word `0x005c1164`, 150 s): 2 of 4 show `CH:4 Device ID:0xa0 timeout`
    at about 20 s. The WP correction alone does not remove it.
  - IRQ-level instrumented run (4 boots, not committed): one `CH:7 Device ID:0x40` timeout at 37.6 s. CH:7
    completions kept arriving afterwards, so this is not a full controller stall. The mask state is not
    logged at completion, so this is a lead, not a cause.
  - The stall is timing-sensitive: tracing or instrumenting every STAT read hides it. Parallel load may
    matter. A freshly copied card still shows it, so it does not need a reused image.
- Ruled out (do not re-test without a new reason):
  - Poll-budget race: completions land about 110 us after arming.
  - Per-byte timing: `SDP1406_I2C_XFER_NS` at 22500, 90000 and 100000 still times out.
  - One IRQ per DS byte: `test_sequential_read_completes_one_irq_per_byte` passes.
  - Driver NAK ordering: the NAK write after the DS read is a no-op and the final completion still fires.
    The model's NAK path is self-consistent (qtest probes).
  - Lost completion from state 0: the kernel never stores 0 into the state word; 0 is only the reset value.
  - Micom as trigger: the first CH:4 timeout came at 17.6 s, before the first micom error at 87 s.
  - `-icount shift=auto`: removed timeouts only in a small sample (6 runs); not conclusive.
- Open:
  - Resolved: the group IRQ_STAT ack is in the handler's exit path (`0xc0293cd4` in `03-uImage`). It reads
    group `+0` and writes back only the channel's own bit (W1C). The mask at group `+4` is set at transaction
    end (`0xc0293c5c`) and cleared under spinlock at kick-off (`0xc0294340`).
  - Refuted: the masked-completion hypothesis. Six 120 s parallel boots (back-pressure build) had 2 CH:5
    timeouts, and the traced runs below had 4 more. None of the stalls had a masked completion before them.
  - Open, CH:5 stall shape (instrumented build copy, `TRACE` lines on CH:5 CON/STAT and on group IRQ
    mask/status writes; 6 parallel 120 s boots, 2 with timeouts, 1 boot failed early and was dropped):
    - Every CH:5 timeout sits inside a silence of about 1 s with no CH:5 register writes and no CH:5 completions.
      Serial and virtual time align (for example 26.55 s inside the gap 25.76-26.84 s).
    - Each such silence starts right after a read tail: the NAK CON write `0x0`, then exactly two completions
      about 0.3 ms apart, both `masked=0 pending=1`. Four of four silences with two tail completions had a timeout.
      Silences with no tail completions had no timeout.
    - Normal transfers also show two completions after the read START `0xb0`, and the next CH:5 write follows
      in 0.3-0.6 ms. So the tail shape alone does not separate the stall from normal traffic. The difference is
      what the driver does after its second completion, which is not observed.
    - Open: whether the driver's waiter misses its wakeup (a host-timing race) or waits for a completion the
      model never raises after the second tail completion. Next: read the driver's message count at the
      timeout, or add a CH:5 DS-read trace to see whether the driver read the second byte.
  - CH:7 failure: map guest timestamps to virtual time to find the completion of the failing transfer.
    Done: `sdp1406_i2c_xfer_done` logs every completion under `-d unimp` with channel, virtual time,
    mask bit and pending bit (`sdp1406.i2c: CH:%u completion`). Next: run a long boot with
    `-d unimp` and check whether the CH:7 mask bit is set at the 37.6 s timeout.
  - CH:4: trace the `0xa0` wait in an instrumented build to see whether a START is still merged or the driver's
    wait is too short. Candidates: a START issued while the model is busy (dropped), and the STAT
    `0xd0`/`0x90` STOP writes (`0xc0293854`).
    Traced run (1 of 4 parallel boots, `-d unimp` plus CH:4 register and IRQ-line trace, build copy only):
    - Normal transfers poll STAT (`0x90`) and DS about every 100 us, one completion per step.
    - At 46.13 s two completions were latched on bit 4. The driver acked twice (`pending` 1, then 0), and
      read STAT `0xd0` each time. No CH:4 STAT, DS, CON or IRQ-line change follows for 2.33 s.
    - The driver's next CH:4 write is CON `0x100` at 48.17 s, after the `0xa0` timeout at 47.9 s.
    - So the driver waited for a completion the model never raised. The model has no transfer pending at 46.13 s.
    - Open: which driver step expects a further completion after the second latched ack.
    - Second traced set (4 parallel boots, `-d unimp,guest_errors`, CH:4 and CH:5 traces): 3 of 4 runs had
      timeouts (CH:4 at 35.8 s, CH:7 at 26.6 s, CH:4 at 33, 38 and 70 s). No `QUEUE FULL` drops and no guest errors.
      In the CH:4 run, the model finished the transfer before the timeout: BUSY cleared and IRQ acked at 33.91 s.
      The next CH:4 START came at 35.96 s, after the 2.05 s gap. So the gap is between a completed transfer and
      the next START, which points at the guest waiter or scheduling, not at a dropped model completion.
      Open: whether the timeouts need host load (4 parallel boots); a single unloaded boot is the control.
      Correction: the completion at 33.91 s left `latched=1` on bit 4 with the bit pending. A completion was therefore
      owed to the driver and not raised before the 2.05 s gap, so the conclusion above (not a dropped completion)
      does not hold. Untested: whether the driver masks bit 4 before acking that latched completion, which would hide
      it until the next START clears the mask at 35.96 s.
      Fix applied (back-pressure, see DEVELOP.md): a completion waits while its bit is pending, so the
      owed completion is raised after the driver acks. Measured as above. Whether it removes the stall
      needs the CH:5 follow-up.
    - CH:7 deadlock found with the DS and IRQ traces (`/tmp/i2crun5` run 6): the CH:7 completion at 36.907 s
      was `masked=1 pending=1`. The mask bit `0x80` was never dropped before the next completion at 37.992 s,
      so the driver's handler could not ack it. The model held that completion on the unacked bit, and the
      driver's waiter timed out at 37.78 s first. The driver only acks after it unmasks, and it unmasks at the
      next kick-off, which waits on this transfer. So the masked bit deadlocked the transfer.
      Fix applied: only unmasked, unacked bits hold a completion (`sdp1406_i2c_xfer_done`). Test:
      `test_completion_during_masked_unacked_bit_is_not_deferred` (fails on the previous condition, passes now).
      Open risk: a completion that lands on a masked bit is ORed in, so two completions can merge into one
      unread DS byte. Whether the production hardware overwrites, holds or merges in that case is unverified.
    - Measured after the masked-bit fix (repo build, 8 parallel boots killed at 230 s, `/tmp/i2crun6`): 12 timeouts in
      3 of 8 boots. CH:5 `0xce`/`0xc2` 8 times, CH:6 `0x54` 3 times, CH:7 `0x40` once. Load-dependent and
      small. The masked-bit deadlock did not account for the remaining CH:5 timeouts, so the cause of those is
      still open.
    - CH:5 follow-up (no logic established yet):
      - CH:5 timeouts seen in this follow-up are on `0xc2` and `0xce` (`0x61`, `0x67`), and once on `0x14` (`0x0a`, the
        HDMI switch, which logs `[HdmiSw][m_WaitBootDone] ERR : Boot failure`). The two addresses from earlier
        runs (`0x61`, `0x67`) are not in the DTB under CH:5.
      - Refuted: a START written while a completion is deferred is dropped (`START_DROP`), which would leave the
        transfer with no completion. In 8 traced boots, START_DROP happened 41 times on CH:4 and 0 times on CH:5,
        and none of those drops caused a timeout. So dropped STARTs do not explain the CH:5 timeouts.
      - Timing sensitivity: an untraced control batch (8 boots, `/tmp/i2crun10`) had 4 CH:5 timeouts (`0xc2`, `0xce`
        at 115–122 s). Two traced batches with `-d unimp -D <log>` had 0 CH:5 timeouts in 16 boots (one CH:10
        `0xc8` in the second batch, the known FRC TCON residual). The log I/O appears to change the timing, so the
        trace hides the bug. Any CH:5 trace must be lighter than a full `-D` log, such as an in-memory ring buffer
        flushed on the timeout, before the cause can be found.
    - Control runs with the repo `sdp1406.c` (no traces): 3 unloaded boots had 0 timeouts. Four parallel
      boots had 1 timeout (CH:4 at 21.6 s, the run with the fewest CH:4 completions). So the stall
      reproduces only under host load, and the fix must not rely on a fixed transfer timing.
      The raw logs were lost when `/tmp` was cleared, so the unloaded runs are not repeatable from the record.
  - WP helper B: the mapping function `0xc001983c` is unidentified, so the mapped base is not confirmed.
    The timeout path `0xc0294470` also clears bit `0x40` (via `[r6+44]`), probably the same register.
  - Reviewer claims still untested: that LRB is never set.
  - CH:5 owner (static config checked; no owner found). The DTB node `i2c@001901a0` (SPI 45, affinity 3)
    has no child devices. The 7-bit addresses logged on CH:5 (`0x61`, `0x62`, `0x67`, `0x0a`) are not
    children of it. `0x61`/`0x62` are children of CH:8 (`i2c@00191000`) and CH:10 (`i2c@00191040`).
    `0x67` and `0x0a` are not in the DTB at all. The `tztv_audio_amp` module opens buses 6 (main/sub) and
    8 (bt/top), read from its `.data` at offsets `0x0c`-`0x12` (disassembled). No bus 5. The root
    filesystem has no userspace on `i2c-5` (udev and smack only name `i2c-6`/`i2c-7`).
    Open: whether a built-in kernel path opens adapter 5 at runtime (not checked in the uImage), and
    whether the model's `CH:N` labels match the kernel's `i2c-N` numbering.
  - Synthetic EEPROM: WP polarity is inferred (bit 6 high = writes allowed, reset unprotected).
    Check it against the real chip or a second trace.

## 2. Sii9777 HDMI bridge on CH:7 (modelled)

- The Sii9777 is modelled at DS byte `0x40` on CH:7 as a 64 KiB register file (16-bit sub-address, high
  byte first, no page wrap). The NACK is gone, the 120 `incomplete xfer (-6)` errors no longer appear,
  and `Sii9777 Instance creation SUCCESS` is logged. Tests: `Sdp1406SiiTests` in
  `tests/test_qemu_sdp1406_i2c.py`.
- Open: the Sii9777 driver's device-ID and firmware-version checks read registers that are not modelled.
  If `Sii9777-I2C` errors appear, disassemble `tztv_hdmi_switch_api.ko`.

## 3. SELP hold and RCU stalls (partly resolved)

- Resolved for the default `SELP_ENABLE=00000000`: the `[SELP] ... please attach T32` hold came
  from an unbacked CI probe write at `0x90000` (`sdp_ci_probe`). Backing the four CI probe windows
  as RAM removes the abort and the hold in 20 s boots (see DEVELOP.md).
- Open: boots longer than 20 s are not yet checked on this build.
- Open: `SELP_ENABLE=00000001` avoids the spin but stalls RCU on CPU 1 after about 70 s, so it is
  not a fix. What the SELP check tests, and whether `1` is correct for this device, is unknown.
  Next: static read of the code at `0xc00148f0`.

## 4. TrustZone SMC (open)

- `tzdev.ko` issues SMCs from `tzdev_ipc_thread`. The monitor stub at `0x00200000` returns `0`
  for every SMC, and `tzdev_ipc_thread` accepts `0` (only `-11` retries), so the SMC itself does
  not stall.
- The client stall (`tzdaemon thread has been waiting N > 5 s`) comes from the missing secure
  reply. Nothing writes a completed reply record to the shared page, so the waiting client never
  wakes. The stub is the model gap, not a crash.
- Shared-page layout (decoded from `tzio_pop_message`): records are `{u32 type; u32 id; u32 len}`
  followed by `len` bytes. Type 1 is a reply matched by `id`, type 2 walks the pending list, type 3
  is a notification. The valid byte count is at `+0x7fc`. The request ring starts at `+0x800`, with
  its write index at `+0xffc`.
- Request path: `tzdev_get_phys_addr` passes the page's physical address in `r0`. Page registration
  uses `ip = 0x0e000000`, and submission uses `r1 = 7`.
- The stub logs `r0`, `r1` and `ip` to the `sdp1406.smc-log` device (`sdp1406_add_smc_log`) and
  returns 0. Its trace is visible under `-d unimp`.
- Open: map the submitting channel to its registered page. In the current trace the first four
  registered pages all read zero records when the request fires.
- Open decision: what a type-1 reply payload should contain. An empty payload could let secure
  storage accept an empty key, so the stub should not fabricate success. Options are a logging-only
  trap, or an error-status reply whose format is still unknown.
- Boot-path caveat: the bootloader faults on the SMC at `IFAR 0x200008`; see
  `freesung-bootloader/TODO.md`. The stub runs only on the control path.

## 5. Card p26 (`/mnt/drm`) superblock (open, runs conflict)

- Host side is resolved: `mkcard.py --firmware-root` formats p26 as VDFS (`VDFS1002` at byte 1024)
  when the image is built (commit `91ee18e`).
- Open: in one run a guest-side mkfs superblock appeared after the first failed mount. In another,
  p26 stayed zero after the guest mkfs and `Cannot restore /mnt/drm` was logged.
- Open: `psa_mount.sh` does not log the mkfs exit status.
- Open: intermittent `non-auth` denials for `ld.so.preload`/`ld.so.cache`
  (`check_execution_available`) are not explained.
- Next: re-run a boot from a fresh copy, check the superblock, and log the mkfs status.

## 6. `mali.ko` and device nodes (open)

- `rc.sysinit` loads `/lib/modules/mali.ko` (line 39), but the module is `kernel/soc/mali.ko`.
- `rc.sysinit` chmods `/dev/mali` (line 153), which the driver never creates. The driver creates
  `/dev/mali0`, which line 48 chmods.
- The `chmod /dev/dri/*` error is a timing artifact: it runs before `sdp_drm` loads.
- Check the production script before changing the model.

## 7. Initrd `/init` cannot be executed (open, low priority)

- The kernel reports `Failed to execute /init` with no errno, for both a probe init and a
  freestanding one.
- Only needed for a root-shell route. Production runs systemd, and `console-shell.service` is not
  pulled in by the default target.
- The initrd is only accepted at `0x86000000` in the DTB `/chosen` (`linux,initrd-start` and
  `linux,initrd-end`, patched with `fdtput` on a scratch copy). `0x44000000` and `0x24000000` are
  rejected with "not a memory region". Without the DTB properties the kernel never unpacks the
  cpio, and `-initrd` does not set them with this DTB.
- With the initrd present, `/init` still fails at about 0.6 s. A real ARM binary from the card
  rootfs (`usr/bin/btdl-1.0.0`, EABI5, soft-float) fails the same way, so the freestanding ELF is
  not the cause. The exec failure is in the kernel's initramfs or exec path. Its message has no
  errno, so the cause is still unknown.
- Errno found with gdb (kernel VA = PA + 0x40000000). `do_execve` (`0xc00d5fe4`) runs its binfmt
  loop at `0xc00d5004`. Before that loop, `0xc00d5034` calls the vendor exec policy
  `0xc01acf00` (`0x801acf00` in PA). It maps the result of `0x801ac9ec` through the mask `0x13`
  and returns `-EPERM` (`-1`) for those values. The policy walks a table and calls per-entry
  hooks. The `-1` comes back up through `r9` and `r7` and produces `Failed to execute /init`.
  The policy is probably a vendor binary or signature check, so the initramfs binary is denied
  before any format handler runs.
- Next: identify what the `0x801ac9ec` table hooks check. The goal is to match the original
  hardware, so keep this denial in the emulated kernel and do not patch the exec policy. Only
  revisit it if the real device is shown to allow the exec.
- Tried for the BT probe (section 17): the card root (p22, VDFS1002) is RSA-signed in its
  superblock. A repacked root made with `mkfs.vdfs` without the vendor private key fails at boot
  with `bad superblock hash!!!` and `Unable to mount root fs`. The probe could not run. The
  original card was not modified.
- Checked the personal keys in `freesung-tools/extract/keys/` for this repack. They are 13 hex-text
  files (16, 32, or 64 characters, e.g. `HawkM-line71.key`, `msm-replay.key`). They are symmetric
  material, not RSA private keys, so they cannot produce the superblock signature. Nothing was
  repacked with them. Open options: (a) patch the emulated kernel's superblock check (QEMU only),
  (b) generate a new RSA key pair and replace the trust anchor in the emulated kernel or DTB, or
  (c) get the RSA signing key file. Choose one before continuing.
- Superblock hash check (resolved, supersedes the earlier "trust anchor" framing): the
  `bad superblock hash` check is in the kernel verify function `0xc03f6344`. It computes a
  reflected CRC32 (seed 0, poly `0xEDB88320`, no final inversion) over bytes 0..507 of the
  512-byte superblock and compares it with the 32-bit word at offset 508. This is a plain
  checksum, not a key-based check, so no trust anchor is involved. Confirmed on a live breakpoint
  dump of the in-memory superblock and on all five on-disk `VDFS1002` superblocks of
  `hawkm-card-root.img` (first at byte offset 64029184).
- The unmodified original card root boots in this QEMU machine to userspace (`[FB:INIT] ...
  excuted /usr/sbin/vendor-config!!`), so the earlier `bad superblock hash` came from my repack,
  not from the original image. The remaining question is whether a repacked root that recomputes
  this CRC then passes the separate RSA signature check. That check is still not located or
  tested. Option (b) (replacing a trust anchor) is not needed for the CRC step.
- Layout note: the p22 partition starts at byte 64028160 of `hawkm-card-root.img`. Its block origin
  is 1024. The offset 1024 header is a leftover `mkfs.vdfs -i -H /tmp/prkey_2048...` header and
  is invalid. The valid superblock is at 1536, the extended record at 2560, and the `CoWB` base
  table at origin + 6×4096 = 25600. `vdfs-unpack` now discovers this layout (`LAYOUTS` in
  `src/vdfs1002/format.rs`). Verified on the real p22 slice: `--list` gives 136,703 records and
  `--extract` on `partition.txt` returns the original `v4` contents.
- Mount read path (2026-11-16): a traced boot of the original card (`-trace blk_co_preadv`,
  `snapshot=on`, `timeout 90`) shows the kernel's first p22 reads are 4096 bytes at partition offset
  1024 and 4096 bytes at 5120. There is no read at 1536. The 4096-byte read at origin 1024 contains
  the valid superblock at 512 within the block, and the serial log shows no superblock error. The
  kernel therefore uses the 1536 copy and ignores the 1024 mkfs header. This is inferred from the
  block trace, not from kernel symbols.
- Test (no repack): one byte changed in an uncompressed text file in p22 (`/etc/include/partition.txt`,
  `v4` to `v5`, superblock untouched). The guest boots as the original does, with no integrity or
  signature error. So data files are not checked on read. Executables in the image (for example
  `vendor-config`) are stored compressed, so their strings cannot be patched in place. Checking exec
  signatures needs a real rebuild.
- Lead (open, blocked): the 21 `UTV_QTERICS_*.key` files in `freesung-tools/extract/keys/` are
  PKCS#8 RSA private keys, but encrypted (`EncryptedPrivateKeyInfo`). `openssl pkey` needs a passphrase
  and the repo has none. Not decrypted, not used to sign. Open: obtain the passphrase through an
  authorized route, then compare each key's modulus with the public key the card root's superblock
  check uses (kernel or DTB). Only if one matches is re-signing the emulated root worth trying.
  Scope to confirm first: emulated `sdp1406` only, not a real device.

## 8. Secondary-core path (open)

- All cores run `_start` in the bare-metal UART program. It parks cores other than CPU 0, but the
  Linux secondary-core path is not checked.

## 10. Micom device registration (benign; not the TCON/scanout blocker)

- Every boot logs `device3 did not registered completely` once, at about 0.23 s. The first micom
  frame for device 3 (`ontimer-wt61p807`) arrives before the slot is filled. Device 3 registers
  right after, and later lookups succeed.
- The dropped frame carries the default `0x29` tag. Its device-3 handler treats `0x29` as a no-op,
  so the drop changes no state. Changing the first replies' tags did not remove the line, so the
  line is about registration order, not the tag.
- The TCON result is unchanged: `detect_tcon` still reports `Unknown TCON type` with or without the
  line. Item 10 is not the black-framebuffer blocker.
- Resolved: the `0xa0` reply tag was `0x29`, so the RX completion check (kernel `0xc0265e34`,
  reply byte 6 must equal the pending tag) rejected it and the `0xa0` request timed out.
  `sdp1406_mbox_ack_for` now echoes `0xa0`. A 20 s boot no longer logs `Timeout error - command 0xa0`.
  Open: the `device3` line above remains.

- Open: `0x96` is never produced by the model, so the poll loop's `state[100]` branch (kernel
  `0xc0259d68`) is unreachable in QEMU. No `0x96` appears in `hw/arm/sdp1406.c` or the tests.
- Untracked commands `0x20` (micom version) and `0xac` (Bluetooth RC pairing) are sent by libmicom
  `micom_send_msg` through ioctl `0xc00cb301`. The kernel item path completes on RX length, not on
  the reply tag. The model answers them with the default `0x29` tag and a zero payload. The payload
  is what matters. This comes from disassembly and is not validated on the device.
- Open: no in-kernel path reads the `0x29` reply payload, and module callers are not confirmed.
- Send-and-wait (`sdp_micom_send_cmd`, kernel `0xc02662a0`): sends `ff ff cmd payload checksum`
  (checksum in byte 8), stores the expected reply tag in `ctx+20`, and waits up to 5 tries of 400
  ticks. A frame is copied and wakes the waiter only when its byte 6 equals `ctx+20`. Otherwise it
  is dropped as unsolicited, so the reply is consumed only inside a pending request window.
- The `0xa0` sender is `jackident_thread` in `tztv-jackident.ko`, which calls `sdp_micom_send_cmd_ack`
  with `r0 = r1 = 0xa0`. The fix above covers it.
- Open: `0xd1` goes through `sdp_micom_send_cmd_ack` and the mailbox, not UART. Boots still log micom
  `Timeout error` lines with the mailbox replies modelled.
- Open: the timing between the mailbox write (`sdp1406_mbox_write`, `hw/arm/sdp1406.c`) and the guest
  IRQ path is not traced under a fixed schedule. This is the likely cause of the run-to-run order.
- Open: which micom frame arrives first and whether it lands before or after the pending tag is set.
  Two orders have been observed. Not reconciled.

## 11. `system_server` aborts on the TV power path (open)

- Sequence: the frc_tcon submicom `0xd1` command gets retries, then `-512` (interrupted wait) on
  repeated tries. The process then aborts with `[VDLP COREDUMP] SIGNR:6`.
- Abort site: `TDsSystem::SetPower` in `libtvsystem.so.0.1`, reached from `tv_system_set_power`.
- Static: the micom sender restarts an interrupted wait on `-512` and does not take its failure
  exit. So `-512` points to a pending signal on the waiting thread, not a rejected `0xd1` reply.
  This is a hypothesis. The signal source is not found, and no freeze-for-suspend string is in the rootfs.
- Open: the `-512` storm did not reproduce in a later 170 s trace (one `0xd1` send, no retry, no
  `SIGNR:6`). It is not yet a reproducible case.
- Open: the repo has no micom responder, so the `-512` path cannot be removed or tested yet. A
  healthy-micom control is needed first.

## 12. Vendor-config WIFI_VENDOR read fails at boot (open, lead for net-config watchdog)

- `vendor-config` fails to read `SYSTEM_INFO_KEY_CHECK_WIFI_VENDOR` at about 8 s, then reports
  `Failed to get system-info`. The key is `db/factory/FC_STORE_LEVEL_FIXED/WIFI_VENDOR`, read by
  `libcapi-system-info`.
- Resolved (cause): `automation-helper` reads the factory flag at EEPROM byte `0xf212` through
  `/dev/eeprom` ioctl `0xf212` (the flag is at `0xf200` plus `0x12`). A value of `0xff` selects
  `non-factory production mode`, so `kfactoryd` runs with unset values and WIFI_VENDOR is not loaded.
- Changed: the synthetic EEPROM holds `0x01` at `0xf212` (`SDP1406_I2C_EEPROM_FLAG` in
  `hw/arm/sdp1406.c`), which selects the production branch. Test: `test_factory_flag_at_0xf212_is_0x01`
  in `tests/test_qemu_sdp1406_i2c.py`. The real retail value is unknown.
- Boot with the flag set: `FP_NVRAM_FASTBOOT_IN_PRODUCTION [1]` replaces `[ff]`, but the WIFI_VENDOR
  error remains. Not yet verified on a full 20 s boot.
- Open: why `kfactory` does not load the factory record. `/opt/dbspace/.factory.db` is absent from the
  rootfs, so the store must be built at runtime. Whether the value in `usr/share/factory-data/Factory_Part1.dat` is valid is unknown.
- Open: the link to the `net-config` watchdog SIGABRT is not confirmed. The abort is a systemd watchdog
  SIGABRT while `net-config` is idle in `__poll`. The WIFI_VENDOR failure precedes it, and
  `msg-wt61p807 ack[30] timed out` is the micom command the read depends on.
- Negative result: echoing `0x30` as the reply tag did not clear `ack[30] timed out`, so the change was
  reverted. The driver's expected tag for `0x30` is unknown.
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
  - Runtime (virtual breakpoints on the wait loop): the wait loop `0xc0259a9c` fired
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
  - Runtime (breakpoints at `0xc02af248` and `0xc02af26c` after each
    callback): neither fired in a 170 s boot, and the `0x30` request timed out before the copy/check.
    So the builder's callbacks never ran for the wait that times out. The fault is upstream of the
    reply byte: the micom reply for `0x30` never reaches the waiter.
  - Open: why the `0x30` request never gets a reply through the mailbox (reply frame `0x404`–`0x41c`,
    pending `0x440`, clear `0x444`). The echo change for `0x30` did not fix it, so the next lead is
    the completion/interrupt path the wait loop depends on, not the byte value.

- Factory-store chain (rootfs `usr/bin/TIFSDaemon`, `usr/apps/fastboot/bin/fb.sh`, `usr/bin/kfactoryd`):
  - `fb.sh` takes the production branch when `/tmp/FACTORY_PRODCUTION` exists (sic). The boot prints
    `Production mode`, so the branch is taken. It starts `factory-init.sh&` and `TIFADaemon` there.
  - `system_info` reads go to `TIFSDaemon`, which opens `/opt/dbspace/.factory.db`. That file is not in
    the rootfs. Import is `FCStorageBase::m_ImportFactoryDataToFileSystemEj` from `Factory_Part1.dat`.
  - Boot 170 s trace (second run, 60 s): `TIFSDaemon` page-faults at `0x1e1` at about 41 s, after the
    8 s `WIFI_VENDOR` failure. `factory-init` and `TIFADaemon` never log in the boot.
  - `kfactoryd` prints `can not run multi process!!` (`org_pid:243`) twice. Its lock is `/tmp/kfactoryd_pid`.
    `kfactoryd.sh` also guards on `ps`. The first instance is the one that stays running.
  - Open: whether `factory-init` runs at all in the boot, which starts `TIFSDaemon`, and who should create
    the store. The EEPROM flag fix alone does not reach the store.

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

### Experiments

- Pristine card (150 s): `tztv_get_svc_panel_year: [-1]`, `Unknown TCON type`, `tcon_loaded=[0]`.
- Seed with `factory_backup_flag` removed and `0x5671` unset: no import, `detect_tcon` never runs, and
  the panel line reads `NOT BOE panel!(NONE_15)`.
- The same seed with `0x5671 = 0x63`: the import runs and creates `auto_imported`, but `detect_tcon`
  still does not run. Boot order differs from the control, so this is not a like-for-like comparison.
- Persistent card: `kfactoryd` crashes (SIGSEGV) at about 15 s. The crash also happens with `-snapshot`,
  so it does not depend on card state. `vdfs-unpack` reports 64 failed objects, including `.factory.db`.
- Writable-card boot (170 s): the guest writes `/opt` blocks, including `.factory.db` pages. Only three
  FIXED/FAC values change, and `SVC_PANEL` stays `-1`. EEPROM `0xf212` read `0xff` in that run (before
  the item 1 START fix), so the guest printed `non-factory production mode`.
- Kernel route (not done): the VDFS CRC check (`CRC missmatch`) in the uImage would let a renamed-flag
  seed mount.

Open leads for item 13:
- The key 17/18 lookup returns `-1` although the sorted index has valid entries. Unknown whether the FIXED
  rows reach `.factory.db` before the first lookup.
- The `kfactoryd` SIGSEGV (about 15–22 s) has no captured faulting copy yet.
- `0x5671 = 0x63` is not in the model. The gate needs it, and the retail value is unknown.
- Whether `SVC_PANEL` imports after a `[1]` boot is untested.
- UART RX: patch `0003` (`patches/`, applied in the QEMU tree) acks RX via UTRSTAT, so the older "never
  raises bit 0" blocker is stale. Open: confirm at runtime that typed guest input reaches the driver.
- Dumping object 883 offline needs the VDFS extent fix (`vdfs-unpack/src/vdfs1002/image.rs:278`), or another route.
- The black scanout is not explained.

## 14. No framebuffer device is registered (closed: not a model gap)

- Firstscreen aborts with `Clutter-CRITICAL: Cogl-Renderer could NOT connect`, and `firstscreen.sh`
  relaunches it. That was seen on an older card (`card-own.img`, not in the repo; the probe tool does not
  produce it). It is **not reproduced** on the current build: the regenerated card (`hawkm-card-root.img`)
  reaches Xorg ready at about 30 s with `[CoreGL] Driver GL version 2.0` and no Cogl or `XOpenDisplay`
  error through 150 s. The shipped card showed no Cogl error at 200 s. Treat the Cogl abort as
  unconfirmed on this build.
- The earlier claim that the abort is masked on the squashfs root (missing Smack labels) is unverified.
  Re-test at 120 s or more before relying on it.
- Firstscreen targets X11 (`DISPLAY=:0`), not fbdev. Xorg needs `/dev/dri/card0` and `card1`.
  `sdp_drm` is inserted by `fastinit` at about 25–35 s, and Xorg reaches `/tmp/xorg_ready` at 31–45 s
  with no fatal Xorg, DRM, or EGL error. The model already has the scanout (`SDP1406_FB_BASE`,
  1920x1080 XRGB8888).
- Runtime: `/dev/fb0` and `/dev/fb1` return ENOENT, `/proc/fb` and `/sys/class/graphics` are empty, and
  `register_framebuffer` (`c01f67c8`) is never hit. `rc.sysinit` chowns `/dev/fb0` but never creates it,
  so that chown is dead.
- Static: `register_framebuffer` has one caller, `drm_fb_helper_initial_config` (`c0221654`), reached only
  from `drm_fbdev_cma_init` (`c0223d38`), which has no caller. None of the 160 `.ko` files under
  `lib/modules/3.10.30` imports `register_framebuffer`, `drm_fbdev_cma_init`,
  `drm_fb_helper_initial_config`, or `framebuffer_alloc`. So production never registers `fb0`, and a
  missing node is not a model gap. Re-verified: the kernel image (`03-uImage`) carries the fbdev helper
  names (`drm_fbdev_cma_init`, `register_framebuffer`) as built-in exports, but no caller of
  `drm_fbdev_cma_init` exists in the kernel or the modules. Item 14 is closed as "not a model gap";
  the remaining question is item 16 (the logo and scanout writes).
- libEGL (`usr/lib/driver/libEGL.so`) opens `$FRAMEBUFFER` (default `/dev/fb0`) in
  `__egl_platform_display_search_and_create_map` (`0x28fec`) and returns NULL on failure. A bare `mknod`
  would pass `open` but fail `FBIOGET_VSCREENINFO`, so a node alone is not a fix. Production uses the
  DRM/X11 path.
- The `hawkm-card-root.img` in `freesung-tools/extract/T-HKMFDEUC/probe-artifacts/mmc/` was replaced with the
  regenerated card. Its p24 matches `usr/data_backup.img`. The stale card is kept at
  `/tmp/fsbuild/stale-card-backup/` (scratch, not in the repo). The `probe-artifacts.json` hash was updated to match.
  Regenerate the card again with the generator if `probe-artifacts/` is rebuilt.

Open leads for item 14:
- Whether production firstscreen's Cogl failure, if it occurs, is an X connection or an EGL/fb0 failure.
  The production Xorg log is unread.
- Firstscreen's `[FB]` and launch lines never appear on serial, so its start is not visible. Its output
  goes to log devices, not the serial console.
- `tzdaemon` reports `thread has been waiting 35–131 > 5 s` in `tzio_exchange_message` on each run
  (item 4). This is a separate candidate for the missing firstscreen start.
- `org.tizen.tv.factory-service` dies with SIGSEGV about every 10 s from 48 s on the regenerated card, and
  is relaunched each time. Check whether it blocks any boot step.
- `smack-watchdog` aborted at about 64 s (`pure virtual method called`) in one earlier run, and not in
  the 150 s or 200 s runs. Unverified whether it matters.
- Many `could not load module /lib/modules/3.10.30/kernel/...` lines (evdev, snd, regmap, hid). This may
  be a card or rootfs issue, not a model issue.

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
- Blocked by item 14 (no fbdev path in production).
- Boot-logo path (`fastlogo` draws the logo; no model change yet):
  - The `bootlogo` DTB node (`samsung,sdp1406-bootlogo`) maps the GA/OSD/mixer ranges. The
    `sdp-logo.ko` module is the GA fill/copy driver. `usr/apps/fastboot/bin/fb.sh` starts
    `/usr/bin/fastlogo&`.
  - `fastlogo` reads the logo-enable value from vconf key `db/menu/system/general/boot_logo`. The key
    has no default and comes from `/opt`. The card in `probe-artifacts/mmc/` was the stale one, which fails the `/opt` VDFS check
    ("METADATA PAGE VERSION MISMATCH", 142.11 vs 142.12). That card is now replaced by the regenerated one, and a
    boot to the logo phase no longer shows the mismatch. Regenerating the card with `freesung-tools/freesung-probe-artifacts.py --output <dir>`
    removes these errors. The scanout is still black on the regenerated card.
  - In `fastlogo` `main`, a failed menu-config read is fatal: the branch at `0x968c` goes to the error
    path ("error to get bootlogo config"), which terminates the logo without drawing.
  - The fade step blocks in the kernel. `sdp_logo_fade` loops over `sdp_logo_wait_v_sync`, which sleeps
    until a vsync IRQ sets its flag. The vsync line is most likely GIC SPI 105 (gpio 73). This is
    inferred from DTB table order, not confirmed.
  - Experiment (reverted): a 60 Hz timer on gpio 73 with a mixer `+0x08` write as the ack gives about
    10k acks in 90 s, so the fade loop advances. The scanout still has 0 non-zero bytes, so the fade alone
    does not draw.
  - A 336 MB RAM1 and 256 MB RAM2 `pmemsave` dump at 80 s has no 64-byte chunk of any
    `bootlogo*.out` file, and `sdp1406.ga` (`0x00660000`) is never accessed. So `fastlogo` never copies
    the image into guest RAM. `fastlogo` prints nothing on serial, because `fb_log` does not reach it.
  - Prefetch aborts at the vsync handler address (IFSR 0x5) are transient, before the vmalloc entry is
    synced. The earlier vmalloc L1 hypothesis is superseded.
  - Disproven: the EEPROM as the blocker. The guest selects the `s24c512c` at `0x50` 162 times in a
    90 s boot, and an all-`0xff` read still lets `fb_nv_find_latest_storage` succeed. The NV load
    (`fb_nv_load_menu_config`) also returns 0 at its return site.

Open leads for item 16:
- Which `/fb_logo_mq` command `fastlogo` waits for. `fastinit` sends commands 101–108, and `[FB:INIT]`
  sends 104. No send is yet traced to `fastlogo`'s `mq_receive`.
- Untraced boot (230 s, `/tmp/item16/serial.log`): `fastinit` sends `cmd(108)`, `cmd(104)`, `cmd(107)`, and
  `cmd(102)`, and logs `fl_callback_driver: pipe was not set up` once, at about 3 s. No `fastlogo` line
  appears on serial, so its state is still unobserved. The queue each command goes to is not yet read from
  the `fi_util_mq_send_cmd` code.
- Read the `boot_logo` value from the regenerated card's `/opt` seed (`usr/data_backup.img`), then
  check whether the logo-on branch is taken.
- Whether the `/dev/logo` open, access, and ioctl calls succeed and write the GA/OSD/mixer.
  Per-process gdb breakpoints are unreliable here; use QMP `gva2gpa`, `x`, or `pmemsave` instead.

Scanout check (regenerated card, QMP `pmemsave` of `0x3c500000`, 1920×1080×4 = 8294400 bytes, after 90 s):
all bytes zero, so the logo path leaves no pixels at the scanout address. The region is an alias
(see the scanout defines in `hw/arm/sdp1406.c`), so this does not yet rule out a write to another
address. Next: check item 14 (`/dev/fb0` registration), then trace the fade `ioctl` writes.

Correction to the scanout check: `sdp-logo.ko` does not write pixels to the scanout. `sdp_logo_fade`
(`0x640`) writes one value to offset `+0x58` of a device struct, then calls the vsync wait. Pixels come
from the buffer that `logo_mmap` (`0x14`) maps with `remap_pfn_range`, whose physical base comes from the
device struct at `r0+0x84`. The scanout dump is therefore the wrong place to look.
The DTB `bootlogo` node (`05-dtb.bin`) lists the blocks the driver uses: `0x5d0000`, `0x5d4000`, `0x5d8000`,
`0x5dc000` (mixer, modeled), `0x660000` (GA, modeled), `0x980000` (modeled only as the log-only `sdp1406.dp5`
stub, `hw/arm/sdp1406.c:2728`, so writes there go nowhere), and `0xc00000` (size `0x20000`, not found in the
model). A boot with `-d guest_errors,unimp` showed no unassigned-access messages, so this is not conclusive.
Logo buffer and plane base (static, from `sdp-logo.ko` and the DTB):
- `sdp_logo_platform_probe` reads the DTB `sdp_mmap` node property `samsung,gfx1` (`<0 0x38800000 0x1000000>`).
  It stores start = `buf[1] + buf[2] - 0x73000` and size = `buf[2]`. The logo buffer is therefore
  `0x3978D000`, size `0x73000` (470400 bytes). This is the tail of gfx1 and is backed RAM.
- `sdp_logo_register_mem_info` (`0x404`) saves that base to `[struct+0xc8]`. `sdp_logo_set_plane` (`0x954`)
  writes base + offset to the plane register at `[struct+4]+0x30`. Plane 0 therefore scans out from
  `0x3978D000`, not from `0x3c500000`.
- The scanout `0x3c500000` is only the display's read-side address. The plane's source is `0x3978D000`.

Probe result (regenerated card, QMP `pmemsave` after about 100 s): `0x3978D000` (`0x73000` bytes) and
`0x3c500000` (`0x73000` bytes) are both all zero. The logo never reaches the plane buffer.

`fastlogo` logo-on path (`0x9832`–`0x9928`, static):
- It runs only if `r7` (the first word of the `boot_logo` value, `0x2b`) is non-zero and the mode switch
  keeps `r4 == 1`. Otherwise it jumps to `0x9b08`, the logo-off branch, which writes no pixels.
- It builds the image path in a BSS buffer at `0x13aec` at runtime, then calls `fopen(path, "rb")` at
  `0x98a2`. If `fopen` fails, it logs "logo not found(mode=%d). set default" (`0x9ba4`) and writes nothing.
- On success it `fread`s the file into the `/dev/logo` mmap (`fl_get_logo_buf`), calls `fl_plane_setting`
  (`0xa328`), then issues the fade ioctl `0xc00c4c0a` (`0x9904`).
- The zero dumps mean that one of these failed or was skipped: the branch, `fopen`, `fl_get_logo_buf`, or
  `fread`. This is not yet decided.

Why the logo file is never read (static, `fastlogo` `0x9688`–`0x98a2`, objdump `arm-linux-gnueabi-objdump`):
- `fastlogo` calls `fb_nv_load_menu_config` (`0x9688`) into `[sp+32]`. It only uses the first word `r7` as a
  gate (`0x9692`, `cbz r7` skips the logo-on path). It does not supply the filename.
- The filename mode comes from `fb_nv_load_logomode` (`0x9776`). `[sp+32]` is zeroed first (`0x9772`), then
  `snprintf("/usr/share/boot-logo/bootlogo%d.out", mode)` (`0x97c6`) and `fopen` (`0x97d4`). The logomode record
  is separate from the menu record. The earlier claim that the menu word sets the mode was wrong.
- The card root holds `bootlogo.out`, `bootlogo1.out`, `bootlogo2.out`, `bootlogo3.out` (verified with
  `vdfs-unpack --list` on the carved p22 partition). Mode 0 takes a separate default copy path (`0x9abc`).
  Any mode outside that set fails `fopen` (`0x9cd8`), and nothing is drawn.
- The logomode record is written only by `libfastlogo_dataprovider` (`fb_nv_init_check`, store at `0x24ba`/`0x2562`,
  `fb_nv_store_logomode` at `0x1904`) and `pxutil-daemon`/`libpxutil`. No `fb_nv_store_menu_config` exists anywhere in
  the tree, so the menu record has no production writer here.
- `fb_nv_init_check` (dataprovider) loads a magic byte (`0xbd` means already initialized, `0x238a`). If it is
  not `0xbd`, it loads logomode and clamps values above 3 to 0 (`0x23b0`–`0x23b4`) before writing. On an erased EEPROM
  (`0xff`) this path runs, so the stored mode should end up in 0–3, not 0xff. That weakens the 0xff hypothesis from the
  previous note. The runtime value is still unread.
- The real EEPROM contents are not in the extract, so the factory values cannot be checked against device data.

Runtime check (temporary EEPROM read/write logging in a build-tree copy, reverted; regenerated card, 130 s boot):
- 20,352 EEPROM reads. Every byte read is `0xff` except the MAC bytes at `0x1e04`–`0x1e09` and the factory flag
  `0xf212` (`0x01`). No NV record that the logomode or menu loaders read is present.
- 0 EEPROM writes during boot. So `fb_nv_init_check` in the dataprovider never runs, and nothing writes a default
  logomode before `fastlogo`.
- Consequence: `fastlogo`'s `fb_nv_load_logomode` returns `0xff`, so the path is `bootlogo255.out`, `fopen` fails
  (`0x9cd8`), and nothing is drawn. This fits the zero logo and scanout buffers. The `[sp+32]` value itself was
  inferred from the EEPROM reads, not read from guest memory.
Firmware default (dataprovider `fb_nv_init_check`, `libfastlogo_dataprovider`): on the first-boot path (magic `0xbd`
missing), `r4` is `0` (`0x2368`, `0x240a`), so `[sp+52]` is `0` (`0x246a`). With `system_info` bool `1428` false, the
logomode becomes `0` (`0x257e`–`0x2598`). So the firmware's own default logomode is `0`. That is device-code evidence,
not a guess.
- NV layout (mapped): `fb_nv_load_data(offset, buf, len)` (dataprovider `0x1288`, same in `libfbclient` at
  `0x47d9afdc`) reads EEPROM `0x2500 + offset` and rejects offsets above `0xa00`. Logomode is item 6, so EEPROM
  `0x2506`. The magic byte is offset 1 (`0x2501`, `0xbd` = initialized).
- Model fix (kept): `SDP1406_I2C_EEPROM_LOGOMODE` (`0x2506`) is seeded to `0` in `sdp1406_i2c_eeprom_init`, the
  firmware default above. Qtest: `test_boot_logo_mode_at_0x2506_defaults_to_0` (46 I2C tests pass).
- Result after the seed (regenerated card, QMP `pmemsave` at about 150 s, one process, killed after): the logo
  buffer `0x3978D000` now holds 131,917 non-zero bytes (it was all zero before). The scanout `0x3c500000` is still
  all zero, and the screendump is all black. So the logo is now drawn into the plane buffer, and the scanout is
  still not fed from it. The serial log has no `fastlogo` error. `kfactoryd` takes an unhandled page fault at
  about 16.5 s (`0xee4fe7f8`). This is not investigated and may be unrelated.
- The magic byte (`0x2501`) is not seeded. The `[sp+32]` value is inferred, not read from guest memory.

Display-path check (boot with `-d unimp,guest_errors`, about 200 s; logs in a scratch dir, removed):
- The display driver writes the scanout address `0x3c500000` to the `sdp1406.dp0` (offsets `0x0`, `0x60`, plus
  `0x3b0`/`0x3b8` as `0x3c500001`), `sdp1406.dp2` (`0x0`) and `sdp1406.dp7` (`0x110`) stubs. Those stubs are log-only,
  so the model ignores these writes. The display always reads `SDP1406_FB_BASE` (`0x3c500000`,
  `sdp1406_fb_update`). It never reads a plane register, so the logo plane cannot reach the screen even when filled.
- The logo driver's plane writes do not appear in the log. `sdp_logo_set_plane` (`0x954`) writes to `[struct+4]` at
  offsets `0x10`, `0x14`, `0x20`, `0x24` and to `[struct+12]+0x30`. The value `0x3978D000` is never logged. The `0x30`
  write is `0x230008c`/`0x31101ee`, which is not a plane base. So the logo block is mapped to a region the model accepts
  silently, or it is a block the log does not show. Not yet mapped.
- Next: find which DTB range `[struct+4]` and `[struct+12]` map to (the probe's `ioremap` arguments in `sdp-logo.ko`).
  Then decide whether the display should follow the logo plane, or whether the logo is meant for a different output.

Result of the stub check (this session):
- `sdp-logo.ko` `sdp_logo_platform_probe` stores `ioremap` of DTB resource N at `struct+4*N`. So `[struct+4]` is
  resource 1 = `0x5d4000` and `[struct+12]` is resource 3 = `0x5dc000` (mixer). `0x5d4000` lies inside the
  `sdp1406.osd` stub window (`0x5d0000`, `0xc000`).
- Under `-d unimp` the OSD stub logs writes to `0x5d4000`-based offsets (`0x4000`, `0x4010`–`0x4030`) and to
  `0x10`–`0x24`. It never logs `0x4000 = 0x3978d000`. Two boots were run: 220 s and 600 s. Neither reached the
  base write. The serial log shows `sdp_logo mod ld` and an LVDS line, but no logo draw.
- So the "stub discards the base" theory is NOT confirmed. The base write is not reached in these runs, so
  nothing shows the plane base is dropped. The scanout stays black for a reason not yet established.
- Scanout is still hardwired to `0x3c500000` (`sdp1406_fb_update`), so the plane path is not the scanout source
  in the current model.

Serial check (400 s boot, no `-d unimp`): the log shows `[FB ] fastvideo start` and the `[FB:INIT]` driver
loads, but no `fastlogo` line, no fade message, and no `sdp_logo` plane message after `sdp_logo mod ld`. A
`TIFSDaemon: unhandled page fault (11) at 0x000001e1` appears at about 36 s. That fault is not yet linked to the logo path.

Open leads for item 16 (next):
- Launch confirmed (gdb, `-gdb tcp::1234`, hardware breakpoints at `fastlogo` `0x94c0` and `0x9520`; `fastlogo`
  is a non-PIE `ET_EXEC`, so the addresses are fixed). `main` is hit 145 times across several stack frames, and
  the first `fb_log_type` call (`0x9520`) is hit once. The serial log shows `[FB:INIT] fi_util_mq_create:
  name(/fb_logo_mq)` and the LVDS read `lvds format reg 0xffffffff`. The `[FB] fastlogo start` text never reaches
  serial, so `fb_log_type` output goes somewhere other than the console. Logs: `/tmp/fsbuild/logo-check/gdb-hits.log`,
  `serial-gdb.log`. The script is `/tmp/fsbuild/fastlogo-gdb.txt`. `fb.sh` line 4 `libavePreload` is missing from
  `/opt` and does print an error, which is separate from this path.
  - Next: find where `fastlogo` stops after `0x9520`: `fb_log_init`, `mq_open` of `/fb_logo_mq`, and the
    `/dev/logo` fade ioctl.
- Second gdb pass (port 1235; port 1234 was held by another agent's QEMU, PID 141108, which I left alone).
  Breakpoints on the `fastlogo` PLT stubs: `fb_log_init` `0x9338`, `ioctl` `0x9344`, `open` `0x938c`,
  `mq_open` `0x9380`, `mq_receive` `0x9438`. Log: `/tmp/fsbuild/logo-check/gdb-hits2.log`.
  - Start of the main path: `mq_open` of `/fb_video_mq` (`0x9572`, `.rodata` `0xb088`) and `/fb_sbb_mq`
    (`0x9584`, `0xb098`). Both queues are created at boot (serial shows `mq_create` for `/fb_video_mq`,
    `/fb_sbb_mq`, `/fb_logo_mq`, `/fb_init_mq`), so these opens should not fail.
  - Hit counts are dominated by repeated `fb_log_init` and `ioctl` from the `0x9333`/`0x9345` loop, plus
    `mq_open` and `mq_receive` in other frames. The `ioctl` calls have `fd` `r0=1`, which is unexplained and
    needs a check. The trace is interleaved across threads, so the order is not reliable yet.
  - Still open: whether `fastlogo` reaches the `/dev/logo` fade ioctl (`LOGO_IOCTL` family) or stays in the loop.
- Third gdb pass (port 1236): breakpoints on the PLT stubs (`0x9344` ioctl, `0x9333` fb_log_init, `0x9438`
  mq_receive) do not give reliable return addresses. `lr` reads `0x9345`, inside the stub itself, and `r1` is a
  stack pointer, so the earlier "loop" is probably a breakpoint artifact. Do not conclude from PLT-stub hits.
  Next: break on the caller functions (`fastlogo` `main` `0x94c0` and the `fb_nv_load_logomode` call site
  `0x9776`), or on `fb_log_init`'s internal code, and read `lr` from the caller frame. Log: `gdb-hits3.log`.
- Fourth gdb pass (port 1237, breakpoints on the real `ioctl`/`mq_open` call instructions, not the PLT stubs).
  Log: `/tmp/fsbuild/logo-check/gdb-hits4.log`. Hits: `main` 121, `mq_receive` 78, `ioctl` at `0xa1a8` 19,758,
  `0xa1bc` 2,189, `mq_open` at `0xaa8e` 1,935.
  - `0xa188` is `fl_plane_enable`. It issues `ioctl(…, 0xc0084c02)` at `0xa1a8` and `ioctl(…, 0xc0044c00)` at
    `0xa1bc`. The `r1` values at `0xa1a8` read back as `0xe0b0`, which does not match the request set just before
    the call. So these hit counts are not trustworthy yet, and the loop is not confirmed.
  - Next: confirm the `r1` value at `0xa1a8` with a single step, or read the caller frame, before any conclusion.
- The item 16 model change is still undecided. Do not change the model until the fade ioctl and the plane path are confirmed.
- Fifth gdb pass (port 1238, `hbreak` at `0xa1a8` only): 20,431 hits. Most have `r0=-1` (invalid fd), `r1=0x11`,
  and `r2=0x10`, with `lr=0xa1a5`. Only a few have a valid fd. So the hot loop is `fl_plane_enable` retrying an
  ioctl on a device it failed to open, not a normal request. The `r1` request value I expected (`0xc0084c02`)
  does not appear. Next: find the `open` that feeds `fl_plane_enable`'s fd, and why it returns -1.
- Static: `fastlogo` `main` opens `/dev/logo` at `0x987a` (string `0xaeec`). If the open fails, `main` jumps to
  the error path at `0x9bfe`. So a valid `fl_plane_enable` call from `main` has a non-negative fd. The `-1` loop
  must come from a different call path, or the register reads are wrong.
- Sixth/seventh gdb attempts (ports 1239, 1240). `hbreak` at `fl_plane_enable` entry (`0xa188`) gave an fd
  of 82780 and `lr` values that are not instruction boundaries. These readings come from other guest processes
  that share the VA, so they are not usable. `hbreak` after the `open` call (`0x987e`, the real instruction in
  `main`) gave 438 hits with fds such as 7, 512, and 66128. These are a mix of paths and are not clean either.
  The `/dev/logo` open result is still unconfirmed. Dynamic register reads in this multi-process guest are
  unreliable without filtering by process (for example by `$sp` range or `info threads` per thread).
- Logo driver: `sdp-logo.ko` initializes without errors in the serial log, so the `logo` class and device
  should exist. Confirm `/dev/logo` exists at runtime (for example with an init-time `ls /dev` in a debug boot).
- Target: the emulated `sdp1406` QEMU machine only, not a real device (user confirmed). Keys in
  `freesung-tools/extract/keys/` are still not used. Key contents stay out of the repo.
  - The loader is not the cause: all NEEDED libraries (`libTIFactoryService`, `libTIFactoryData`,
    `libTIFactoryDataWrite`, `libmicom`, `libeeprom-map`, `libsys-assert`, `libfbclient`) exist in the rootfs.
  - `fastlogo` checks `/tmp/FACTORY_PRODCUTION` and skips the AVOC hide handshake in production mode. The
    serial log shows `factory production mode` at line 20, so that branch is taken and AVOC is not a blocker.
  - The TIFSDaemon crash (item 17, `GetModelStr` with `0x1e1`) is still unrelated to `fastlogo` unless
    `fastlogo` calls into the TIFactory libraries before its first print. Not verified.
- The LVDS ioctl at `fastlogo` `0x9822` reads `0xffffffff` because the model has no device at `0x980f64`
  (`sdp-logo.ko` `sdp_logo_dp_lvds_set` reads `[struct+20]+0xf64`). A failure only logs `lvds format setting error!!`
  and continues (`0x9b76` to `0x982c`), so it is not fatal on its own.
- Find why the fastlogo draw and `sdp_logo_set_plane` do not appear in the serial log, even though the logo buffer
  is filled after the logomode seed. Check whether the fade ioctl is gated on an event that never fires.
- Check whether the `TIFSDaemon` page fault at about 36 s blocks the logo path.
- Confirm whether production scans the logo plane or `0x3c500000`. Do not model the `0x5d4000` plane until this is known.
- Whether `kfactoryd`'s page fault (16.5 s) affects the logo path.
- Confirm `[sp+32]` in `fastlogo` reads `0x00` at `0x977c` (QMP `gva2gpa` plus `x`), to close the inference.

- Do not drive a trace with `while ... continue` in a gdb script. It aborts with
  "Cannot access memory" and ends the run early. Use one `hbreak` per address with a `commands N ... continue end`
  block instead (`silent`, `printf`, `continue`), and finish with a bare `continue`.
- Do not dereference guest memory in breakpoint commands (for example `*(unsigned *)($sp+32)`). The
  read can fail and abort the script. Print registers and `$sp` only.
- `fastlogo` text is shared by many concurrent processes (`fastinit`, `fastvideo`, other `fastlogo`
  instances). Group hits by `$sp` of the `main` frame (`0x94c0` entry). Hits from other `sp`
  values are other processes, not the boot-logo run.
- `$r0` at `0x9888`, `0x98a6`, `0x98d0`, `0x990a` is the return value of the call just before it. Read it only there.
- The guest is multi-threaded. gdb prints `[Switching to Thread 1.N]` between hits, so `$r0` at a
  breakpoint can belong to another thread. Values such as `buf=0x3` or `fp=0x6` are not pointers. Do
  not trust a register value unless it is a plausible pointer and the thread is checked.
- Run gdb with `timeout N gdb-multiarch -q -batch -x <script>`, and kill QEMU by PID afterwards.
  The bash tool rejects `pkill`/`killall`.
- Whether a mixer vsync line (gpio 73, SPI 105) matches production. Only the reverted experiment exists.

## 17. Bluetooth controller in the model (device added; firmware path open)

- Open: the guest never sends the download opcodes (`fc2e`, `fc4c`, `fc4e`, `0x1009`) in a 120 s
  boot, so the firmware path is unverified. The model acks `fc2e`/`fc4c`/`fc4e` without storing
  RAM or checking launch.
- Blocked: a runtime probe needs a modified root (see item 7). The root superblock is signed, so
  it cannot be repacked without the vendor key. Next option: an initramfs `/init` that mounts the
  unmodified card root read-only, after item 7 is solved.

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
  - RAM download is modelled (`0xFC2E`/`0xFC4C`/`0xFC4E`, 1 MiB map, launch needs a written
    granule). It is not covered by an automated test. The qtest harness cannot send USB control
    transfers, and the guest never reaches `btdl` at boot. Confirm the launch entry address that
    `btdl` passes (parsed from the minidriver hex) and whether the real chip re-enumerates with a
    new PID after launch. The model does neither.
  - The update decision is made outside `btdl`. `btdl`'s `hci_cmd_read_fw_version` only checks for
    a negative return, so it does not compare versions. `bt-service` `_bt_is_required_firmware_update`
    (`0xb0900`) compares the chip version with `0x231` (561) for chip type 0. The vendor callback
    that supplies that version is not identified. `bt-service` does not import `btif_read_version`.
    `libbluetooth.default.so` `btif_read_version` (`0x4b96c`) decodes the `BTM_ReadLocalVersion`
    result: the 12-bit field it stores at output `+4` comes from the 16-bit value at output `+2`,
    which looks like the HCI revision. The model reports revision 0, which would be below 561 if
    that field is the one used. This is unverified. The download path is not reached in a 120 s
    boot. It is blocked by the signed card root and the initramfs `/init` exec failure (item 7).
  - The unknown vendor commands (`fc1c`, `fc1e`, `fc4d`, `fc6e`, `fc79`, `fcb7`, `fcc2`, `fcc3`,
    `fcfa`) need implementing or a decision that they can fail. Check the status the guest
    expects.
  - The guest sends no `Read BD_ADDR` (`1009`). Find out why, then decide whether the device must
    answer it.
  - The VID:PID is not constrained by the driver. `btusb.ko` aliases are `usb:v0A5Cp*` (any
    product) and `usb:v*pd*dcE0dsc01dp01` (any vendor, class `E0/01/01`). `0a5c:21e8` is therefore
    a valid match, though the real PID is still unknown.
  - BD_ADDR: the model now answers `Read BD_ADDR` with `EF:BE:EF:8C:C3:00`. This is the 6-byte
    item tagged `0x40 0x06` at offset `0x12` of the decoded `btchipfw_single.hex` Static Section
    (`01 08` header at offset 0). It is the only such item in the image, and `btdl --bdaddr`
    rewrites it. The value `ef be ef` looks like a fill pattern, so treat it as unverified until
    the device's real address is confirmed. SCO is not modelled.
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
- Long boots: 40 s and 50 s serial logs show no `ttyS` line and no starter or
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
- Trace: VDFS-root boot for 5, 10 and 15 s shows no
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
