# TODO

Open investigation leads for the SDP1406 (HawkM) QEMU board. Use the production kernel
`03-uImage` only. Current verified behaviour is in [DEVELOP.md](DEVELOP.md).

Items are listed in the order they should be investigated. Earlier items unblock later ones.

## 1. i2c timeouts (CH:7 fixed; CH:4 and CH:10 timeouts residual)

- Current model (`hw/arm/sdp1406.c`, qtests in `tests/test_qemu_sdp1406_i2c.py`, see DEVELOP.md):
  - `+0x18` is a channel IRQ mask (set bit = masked). Inferred from the driver's `bic`/`orr` pattern,
    not verified on hardware.
  - Each transfer completes on `xfer_timer` (100 us virtual). Completions are counted per channel in
    `irq_latched[bit]`, so queued completions are kept.
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
  - Parallel A/B (six 120 s boots): before the latch fix 20 CH:10 and 19 CH:4 timeouts; after it 3 and 7.
    Five of six fixed boots still log a timeout.
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
  - Whether the driver's second ack of bit 4 (while a second completion is latched) is legitimate.
  - CH:7 failure: log the mask state in `sdp1406_i2c_xfer_done`, and map guest timestamps to virtual time
    to find the completion of the failing transfer.
  - CH:4: trace the `0xa0` wait in an instrumented build to see whether a START is still merged or the driver's
    wait is too short. Candidates: a START issued while the model is busy (dropped), and the STAT
    `0xd0`/`0x90` STOP writes (`0xc0293854`).
  - WP helper B: the mapping function `0xc001983c` is unidentified, so the mapped base is not confirmed.
    The timeout path `0xc0294470` also clears bit `0x40` (via `[r6+44]`), probably the same register.
  - Reviewer claims still untested: that LRB is never set.
  - CH:5 and CH:6 have no surviving log evidence; the FRC driver's read sequence is unchecked.
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
- Next: find the initramfs or ELF exec failure before using this route.

## 8. Secondary-core path (open)

- All cores run `_start` in the bare-metal UART program. It parks cores other than CPU 0, but the
  Linux secondary-core path is not checked.

## 9. Clock registration error (explained, benign)

- `sdp_clk_register_fixed_div: failed to register clock emac_clk` at boot.
- Cause (static): the vendor fixed-div table (kernel file offset `0x4f6134`) lists `emac_clk`
  twice, with different parents. The second registration fails the duplicate-name check with
  `-EEXIST`, and the first registration stands. The kernel does not print the errno.
- The duplicate is in the vendor kernel table, not the QEMU model. No model change is needed.
- Open: not confirmed at runtime. Breakpoints must use virtual addresses while the MMU is on.
  Trace the errno only if this error ever blocks a boot step.

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

## 14. No framebuffer device is registered (open, current firstscreen blocker)

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
  missing node is not a model gap.
- libEGL (`usr/lib/driver/libEGL.so`) opens `$FRAMEBUFFER` (default `/dev/fb0`) in
  `__egl_platform_display_search_and_create_map` (`0x28fec`) and returns NULL on failure. A bare `mknod`
  would pass `open` but fail `FBIOGET_VSCREENINFO`, so a node alone is not a fix. Production uses the
  DRM/X11 path.
- The shipped `hawkm-card-root.img` has a stale `/opt` seed in p24 that does not match
  `usr/data_backup.img`. The regenerated card's p24 matches it. Regenerate the card before boot tests.

Open leads for item 14:
- Whether production firstscreen's Cogl failure, if it occurs, is an X connection or an EGL/fb0 failure.
  The production Xorg log is unread.
- Firstscreen's `[FB]` and launch lines never appear on serial, so its start is not visible. Its output
  goes to log devices (item 19).
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
- Blocked by item 14 (no fbdev path in production). Item 19 is resolved.
- Boot-logo path (`fastlogo` draws the logo; no model change yet):
  - The `bootlogo` DTB node (`samsung,sdp1406-bootlogo`) maps the GA/OSD/mixer ranges. The
    `sdp-logo.ko` module is the GA fill/copy driver. `usr/apps/fastboot/bin/fb.sh` starts
    `/usr/bin/fastlogo&`.
  - `fastlogo` reads the logo-enable value from vconf key `db/menu/system/general/boot_logo`. The key
    has no default and comes from `/opt`. The shipped `hawkm-card-root.img` fails the `/opt` VDFS check
    ("METADATA PAGE VERSION MISMATCH", 142.11 vs 142.12), so `/opt` goes read-only and the key is never
    written. Regenerating the card with `freesung-tools/freesung-probe-artifacts.py --output <dir>`
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
- Read the `boot_logo` value from the regenerated card's `/opt` seed (`usr/data_backup.img`), then
  check whether the logo-on branch is taken.
- Whether the `/dev/logo` open, access, and ioctl calls succeed and write the GA/OSD/mixer.
  Per-process gdb breakpoints are unreliable here; use QMP `gva2gpa`, `x`, or `pmemsave` instead.
- Whether a mixer vsync line (gpio 73, SPI 105) matches production. Only the reverted experiment exists.

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

## 19. Firstscreen waits for the kdbus bus (resolved)

- `org.volt.firstscreen` polled `/dev/kdbus/0-kdbus-system/bus` in a 50 ms loop and never
  reached EGL. The 240 s baseline on the fixed build now prints `Kdbus system bus found!` and
  launches firstscreen. The wait no longer blocks; the next blocker is item 14.
- Production `init.wrapper` runs systemd with `DBUS_SYSTEM_BUS_ADDRESS=kdbus:...`, and
  `dbus.service` runs `dbus-daemon --address=kdbus:`. Boot with the production systemd path.
