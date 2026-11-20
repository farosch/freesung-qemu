# TODO

Open investigation leads for the SDP1406 (HawkM) QEMU board. [DEVELOP.md](DEVELOP.md)
contains verified behaviour. Use the production kernel `03-uImage` only.

Items are ordered by boot impact.

## 1. I2C CH:4/CH:5/CH:10 timeouts

Current status: CH:7 and the Sii9777 model are resolved. CH:4/CH:5/CH:10
timeouts remain open. A confirmed protocol gap was fixed: NAK on a read sets
`STAT_LRB`, and the next address phase clears it.

- CH:4 `0xa0` EEPROM and CH:10 `0xc8` remain residual.
- EEPROM write-cycle implementation (2026-11-20):
  - Added 64 KiB S24C512C-compatible write-cycle state to CH:4:
    direct data writes, 128-byte pointer wrap, next-address-boundary commit,
    5 ms virtual busy timer, and `STAT_LRB` on EEPROM address polling.
  - The I2C suite passes on the rebuilt 10.2.1 QEMU.
  - First 30 s live control after implementation reproduced CH:4 `0xa0`
    `incomplete xfer (-6)` at 19.195 s, followed by
    `eeprom_dev_write failed` and `eep_write Write failed`. This is new
    write-path evidence; prior runs showed predominantly CH:4 read/EEP
    helper failures.
  - The write failure now narrows the investigation to guest write framing,
    STOP/repeated-START boundaries, ACK-poll timing, or the 5 ms busy model,
    not only EEPROM read completion.
- Live EEPROM write-cycle A/B (2026-11-20):
  - Busy-enabled 30 s stock control reached CH:4 `0xa0`
    `incomplete xfer (-6)` at 19.195 s, then `eeprom_dev_write`/
    `eep_write` write failure.
  - Two 30 s busy-disabled controls showed no CH:4/EEP diagnostic before
    timeout. This is suggestive, not confirmed: boot timing and later SELP/MMC
    activity differ across runs.
  - GDB ring/control trace at `0xc0294414` observed CH:4 controller
    `0xfe090180`, repeated `0xa0` waits, `STAT=0x91`, `DS=0xff`, and
    IRQ mask `0x10`/`0x50`/`0x70` states. The live result establishes a
    write or polling tail, but does not verify the inferred 5 ms cycle.
  - Negative evidence: busy-disabled does not prove write-cycle polling is
    absent; it only shows the current inferred `LRB` busy behavior is
    able to alter the live failure. No EEPROM write semantics are hardware-verified.
- Cross-agent synthesis (2026-11-20):
  - Protocol specialist: address setup, DS data arm, FIFO queue, W1C status,
    IRQ mask, and shared group IRQ behavior are the leading common state
    machine. The highest-risk gaps are repeated START/STOP, final-read NACK,
    write-cycle polling, and variable read length.
  - Reverse-engineering specialist: production kernel `0xc0293ed8` dispatches
    the core wait/poll transfer path; live CH:4/5/6/10 traces all enter it.
    `0xc0294414` is a completion/status recheck and `0xc0294258` is a timeout
    checkpoint; virtual-address mapping remains dependent on the absence of
    a supplied System.map/vmlinux.
  - Hardware/layout specialist: CH:4 is consistent with a 64 KiB EEPROM family
    but real write-cycle and pointer semantics are unknown. CH:6 `0x44/0x54`
    are consistent with separate amplifier/serdes endpoints. CH:10 `0xc8`
    is consistent with FRC 7-bit `0x64`, while `0x34` is unconfirmed.
    CH:5 has no DTB-confirmed owner and the `0xc0`-`0xce` cluster is consistent
    with display-control devices, but no address alone identifies its hardware.
  - Common leading hypothesis: a controller transfer completes only when the
    guest expected address/data/IRQ sequence matches the model. Universal or
    fixed ACK, fixed 100 us completion, no STOP state, and absent write-cycle
    polling can plausibly stall read tails differently on every channel.
  - Highest-priority synthetic closure order:
    1. CH:4 EEPROM exact read tail, repeated 16-bit pointer, and final NACK.
    2. CH:5 variable read-length/NACK matrix for observed addresses.
    3. CH:6 independent `0x44/0x54` endpoints with write/read and NACK cases.
    4. CH:10 FRC `0xc8/0xc9` repeated-START matrix and `0x34/0x35` differential.
    5. Cross-channel shared group IRQ ACK/queue ordering.
- 30 s stock-card GDB/disassembly control (2026-11-20):
  - `eep_reg_read` is downstream of the same I2C transfer timeout; the guest
    EEPROM call stack does not itself corrupt the controller request.
  - Virtual breakpoint `0xc0294414` hit CH:4/controller base
    `0xfe090180`, channel field `4`, while register `r9` was `0xa0`.
  - The same trace disassembled controller state fields: `+0x2c` was
    `0xfe090180`, `+0x30` was `0xfe0901f4`, and `+0x1e0` was `4`.
  - A second stock run at 30 s reproduced CH:4 `0xa0` at guest 4.217 s,
    10.569 s, and 16.694 s, followed by `eep_reg_read`,
    `eep_cache_read`, `eeprom_dev_read`, and `eep_read` read failures.
  - Disassembly of `0xc0293e98`/`0xc0294414` shows the controller read/IRQ
    polling path; `0xc0294258` is the no-completion timeout branch.
  - This does not prove an EEPROM slave content or timing defect. It reduces
    the immediate hypothesis to CH:4 address-phase/data completion/IRQ timing
    before `eep_reg_read` observes `-ETIMEDOUT`.
- CH:5 is timing/load-sensitive. Untraced and low-overhead ring runs remain the
  preferred evidence method; full `-d` logging changes timing.
- CH:5 is known to be `i2c@001901a0` (item 2). It has no DTB child owner.
  Logged addresses include `0xc2`, `0xce`, `0x61`, `0x67`, `0x14`, and `0x0a`.
- A fresh CH-specific GDB run broke the remaining assumptions down as:
  CH:4 `0xa0`, CH:5 `0xc4/0xca/0xce`, CH:6 `0x54`, and CH:10 `0xc8`
  all reached `c0294414`/the incomplete-transfer wait. No single channel
  or address had a reproducible unmapped-controller event; each hit had a
  live SDP1406 controller and status/state value. This is evidence against a
  missing controller-window or IRQ-line model, not against residual device/
  transaction behavior.
- Live LRB fix run: CH:5 `0xc4` and `0xce` still produced repeated
  `incomplete xfer (-6)` lines and SELP at 22 s. The stale-LRB defect is fixed,
  but unknown CH:5 slave/read behavior is not resolved.
- Confirmed model behavior:
  - Group IRQ acknowledgement is the one-channel W1C write in the IRQ handler.
  - Masked/unacked completions do not remain deferred.
  - Transfer queueing and address-phase selection are covered by qtests.
- Ruled out:
  - Poll-budget race.
  - Fixed transfer-duration variants.
  - START while queued being dropped.
  - CH:5 NACK-on-all-addresses.
- Next: correlate CH:5 STAT/DS/IRQ tails with the guest message cursor at the
  timeout, using the guest-RAM ring at `0x010e0000`.

Tests: `tests/test_qemu_sdp1406_i2c.py`.
- Latest control: rebuilt QEMU passes the I2C qtest suite, including CH:5, CH:6,
  and CH:10 trace coverage.
- A 105 s live GDB/virtual-breakpoint run reached SELP with no CH:4/CH:5/CH:10
  timeout text and no hit at `c0294414` or `c0293ed8`. This does not prove
  hardware behavior; it disproves a broad unconditional completion defect.

## 2. CH:10 FRC TCON and CH:5/CH:6 owner resolution

Resolved for controller ownership and CH numbering.

- The DTB has 16 `samsung,sdp-i2c` controllers in registration order:
  `i2c@00190100` through `i2c@001901e0` are CH:0-7, and
  `i2c@00191000` through `i2c@001910e0` are CH:8-15.
- CH:5 is `i2c@001901a0`, SPI 45, group-0 status bit 5 (`0x20`).
  It has no DTB child node, so no hardware-confirmed slave identity or
  register semantics can be inferred. Live addresses remain traffic evidence:
  `0x18`, `0x80`, `0xc0`, `0xc2`, `0xc4`, `0xcc`, and `0xce`.
- CH:6 is `i2c@001901c0`, SPI 46, group-0 status bit 6 (`0x40`).
  It also has no DTB child node. Live `0x44` and `0x54` traffic are
  controller observations, not enough to identify owner slaves.
- CH:10 is `i2c@00191040`, SPI 50, group-1 status bit 2 (`0x04`).
  The DTB lists two FRC SDP sub-device classes at this controller:
  7-bit `0x64` (`tztv-frc-sdp-main`) and 7-bit `0x60/0x61`
  (`tztv-frc-sdp-sub1/sub2`). Live 8-bit `0xc8` encodes 7-bit `0x64`
  (`0xc8 >> 1 = 0x64`) for the main FRC.
- The model ACKs CH:10 `0xc8` and has no real FRC register semantics. This is
  a limited transport model, not a confirmed FRC/TCON implementation.
- New qtests cover CH:5 and CH:6 IRQ/SPI mapping; the I2C suite passes.

## 3. TIFSDaemon negative-index crash

Open and highest-priority userspace blocker.

- GDB confirmed `ReadFactoryDataFile` builds `count=119`, `table=0x1a8150`.
- GDB repeatedly confirmed `GetModelStr` receiving `r1=-1`.
- It indexes the table with `r1` and passes a bad pointer to `strlen`,
  causing the `0x1e1` page fault.
- The 119-row repaired card disproves the empty-table explanation.
- A pinned ELF patch changes `cmp r1,#0` and branches to the existing `"NONE"`
  fallback. Its bytes and fallback route are covered by Python tests.
- The patch round-trips and VDFS CRC/audit pass, but Samsung execution
  authentication rejects the modified executable as `non-auth`.
- Synthetic closure probe: a disposable uImage patch changes
  `check_execution_available` at virtual `0xc01759d0` to return success.
  A live boot reached later startup with no `Try to execute non-auth file`
  log for TIFSDaemon. This bypass is test-only and not an authorized firmware
  or signed executable fix.
- Reusable live-control support (2026-11-20):
  - `tools/tif_synthetic_patch.py` validates the pinned auth-function bytes
    and repairs uImage payload/header CRC fields.
  - A 60 s stock-card control using the synthetic uImage showed no
    `Try to execute non-auth file` line for TIFSDaemon.
  - That run reached a separate kernel NULL-pointer fault at 29.088 s
    (`virtual address 0000000a`) and later the SELP T32 hold. It did not
    reproduce the original TIFSDaemon negative-index `0x1e1` fault, so this
    remains an auth-bypass closure step rather than proof of the ELF guard.
- CZip/TIF closure finding (2026-11-20):
  - The synthetic card is confirmed to contain the two-byte ELF guard:
    `cmp r1,#0` / BMI to `8bb00`.
  - An unpaused synthetic-auth 60 s boot launched TIFSDaemon repeatedly and
    reached unhandled page faults at ELF virtual address `0x000d5124`.
  - The kernel logged `Wrong magic in compressed extent: #61098 425696/425712`
    for TIFSDaemon before the page fault.
  - The QEMU `sdp1406.unzip` engine now recognizes zlib-wrapped CZip chunks;
    a focused zlib CZip qtest passes. Live wrong-magic faults remain open,
    so this is not yet closure of TIF or proof that the ELF guard alone fixes
    the original negative-index crash.
- Zlib decompression control (2026-11-20):
  - `Sdp1406UnzipRegisterTests.test_zlib_wrapped_czip_chunk_decodes` passes
    independently and verifies STATUS/ERR/ISIZE plus output bytes.
  - A final unpaused synthetic-auth/patched-p22 60 s control still logged
    `Wrong magic in compressed extent: #61098 425696/425712` and TIF still
    faulted at `0x000d5124`.
  - The remaining closure blocker is CZip descriptor/extent mapping or
    decompressed output-page behavior; it is not merely the two-byte ELF guard.

Closure condition:
- Obtain a valid signed/authorized TIFSDaemon variant or remove/disable the
  authentication check only in a clearly isolated synthetic target.
- Boot and confirm no `0x1e1` fault.
- Confirm `kfactoryd`, `TIFSDaemon`, `comss`, and `btapp` remain alive.
- `tests/test_qemu_sdp1406_tif.py` covers the pinned guard bytes, disassembly
  branch, and synthetic negative-index `"NONE"` fallback.

Files:
- `TIFSDaemon` `0x8ba78`
- D-Bus handler `0x7ace4`
- factory table reader `0x8e85c`
- probe patch support in `freesung-tools/freesung-probe-artifacts.py`

## 4. SELP hold and RCU stalls

Closed for the QEMU board model; external-debugger behavior confirmed.

- The default `SELP_ENABLE=00000000` hold is resolved: backing the
  `sdp_ci_probe` windows as RAM removes the unbacked CI probe abort.
- `SELP_ENABLE=00000001` avoids the initial spin but is not a valid fix.
  A direct 140 s stock-card reproduction disproved
  the RCU-specific interpretation: CPU 1 was running `tvs-main-loop` through
  `c00148f0` and emitted MMC warnings in softirq context before it reached
  the same T32 spin. No RCU stall banner or RCU backtrace was present.
- Static caller: production kernel code at `0xc00148f0`.
- Direct disassembly of `03-uImage` confirms:
  - `0xc0014dfc` prints `[SELP] while loop ... please attach T32...`.
  - `0xc0014e04` enters the WFE spin.
  - `0xc0014e08` branches back into the counter wait.
  - `0xc0014e0c` is the cleanup/fallthrough used for a debugger release.
  - The pointer literal is `0xc0014ee0`, and the status block is
    `0xc051c338` in the stock test.
- A full `-S` stock-root control boot hit thread 4 at `0xc0014e04`.
  `tools/t32_attach.py` read r2 as `0`, changed the status word from
  `0xffffffff` to `0`, and originally used PC `0xc0014e08`.
- Repeated-release correction (2026-11-20):
  - Disassembly shows `0xc0014e08` branches back to the counter wait, while
    `0xc0014e0c` is the cleanup/fallthrough after the WFE spin.
  - A corrected single software-breakpoint GDB control captured `r2=0`,
    status block `0xc051c338+0x18`, and fallthrough at `0xc0014e0c`.
  - An earlier repeated script used `0xc0014e08` incorrectly and generated
    278 artificial spins; it is not evidence of 278 independent kernel holds.
  - The RSP tool now uses a software breakpoint (the gdbstub rejected hardware
    breakpoints), supports `--repeat`, and pins fallthrough at `0xc0014e0c`.
  - RSP checksum handling also now encodes strings to ASCII before summing.
  - The repeated run reached 51 s; later internal/fault and paging results are
    independent follow-up bugs, not evidence of RCU or a missing SDP1406 SELP
    register model.
- The stock guest then continued far enough to encounter separate NULL-pointer
  and SMP-stop faults in `gdbus`/other processes, rather than a repeated
  SELP register-state failure. This is not evidence of a missing SDP1406
  SELP, RCU, or status-block model.
- `SELP_ENABLE=0` remains the valid unbacked-CI control; `SELP_ENABLE=1`
  remains invalid because the direct reproduction still reaches the T32
  SELP branch and reports unrelated CPU 1/MMC activity. It is not a clean
  RCU stall bypass.

Follow-up: investigate later NULL-pointer/SMP faults only if the stock
firmware must boot beyond this point. They are independent from the confirmed
SELP T32 handshake.

## 5. TrustZone SMC and tzmem export

Open.

- The monitor stub returns `r0=0` for SMCs.
- Normal control boot needs the stub.
- The loader boot reaches a fault at monitor vector `0x200008`.
- `tzdev.ko` export of tzmem was observed to fail in earlier historical logs;
  a 60 s stock control in 2026-11-20 loaded `tzdev.ko` but reached no
  reproducible `tzmem_mmap`/`tzmem_radix_insert` line before later faults.
- The loader's secure/secure-SVC and MMU mode has not been confirmed at the
  SMC call site.

Next:
- Dump registers and SCTLR/MMU state at the first SMC.
- Compare control boot and loader boot.
- Confirm whether the monitor page must be mapped or the loader must use a
  different CPU mode.
- 2026-11-20 synthetic export analysis:
  - `tzdev.ko` `tzmem_mmap` allocates and remaps pages, then calls
    `tzmem_export_region`.
  - `tzmem_export_region` (`0x2f78` module offset) and
    `tzmem_ioctl` (`0x3334`) copy 20-byte export request/response records.
  - The QEMU monitor currently accepts `r1=4096`, `ip=0x0e000005`, and
    linear-map VA/physical-page pairs, and rejects watch-page exports.
  - No live first-SMC register/MMU trace has disproved or confirmed the
    allocation failure. The next verification is a 30 s GDB run that breaks
    at the first SMC/monitor commit and records SCTLR, TTBR, r0/r1/r12, and
    the export page address.
- 2026-11-20 live 60 s stock control:
  - Serial log loaded `tzdev.ko` at 9.141 s.
  - No `tzmem_mmap`, `tzmem_radix_insert`, or tzmem allocation diagnostic
    appeared before the run ended.
  - The guest reached a NULL-pointer fault at virtual address `0x00000000`
    at 24.485 s and `[SELP] ... please attach T32` at 24.6797 s.
  - Therefore the historical 31 s tzmem line was not reproduced in this
    control, and it cannot be claimed that export allocation is currently
    accepted or remains broken. The earlier fault prevents this stock path
    from reaching a stable tzmem-export exchange.

## 6. Card p26 `/mnt/drm`

Resolved for VDFS format/card placement.

- p26 is `6144K` at GPT partition index 26 and is mounted as `/mnt/drm`.
- `mkcard.format_vdfs` writes a 6144K in-place VDFS1002 image using the
  extracted firmware `mkfs.vdfs`.
- Offline test: p26 has signature `VDFS1002`, the exact partition size, and
  a restorable root containing no files.
- Live boot: no `wrong fs type`, no p26 mount panic, no `Cannot restore
  /mnt/drm`, and no `psa_mount.sh` mkfs-failure line appeared. Only
  `/mnt/drm/securestorage/backup/*` was absent.
- Cause of the marked boot lines (2026-11-20):
  - `psa_mount.sh` mounts p26 as `-t vdfs -o dncs /dev/mmcblk0p26 /mnt/drm`.
  - It removes `/mnt/drm/securestorage/backup/*` and copies
    `/opt/usr/apps/securestorage/data/ssroot/security/*` into that directory.
  - The `No such file or directory` warning is the shell's result for an
    unmatched `rm`/copy glob, not evidence of an EEPROM mapping.
  - The boot card has no source tree at
    `/opt/usr/apps/securestorage/data/ssroot/security/*`, so the backup
    remains absent or empty.
  - These folders are VDFS filesystem state on eMMC p26, separate from CH:4
    EEPROM storage. No EEPROM offset or EEPROM partition evidence was found.

Remaining:
- Populate `/mnt/drm/securestorage/backup/*` or secure-storage data only if
  a specific guest service proves that files are required.
- No additional QEMU block-device model change is justified.

## 7. Mali-400 and GPU device coverage

Open.

- Mali reset, MMU, GP/PP product IDs, and basic register paths are modelled.
- Remaining GPU command, MMU, interrupt, and memory-format behavior is not
  hardware-confirmed.
- OpenCL/OpenGL and DRM bring-up failures may share unresolved device nodes.

Next:
- Capture the first unmapped Mali register or MMU fault.
- Add a focused synthetic test before expanding coverage.

Tests: `Sdp1406MaliMmuTests`, Mali board tests.

## 8. initramfs `/init`

Open, low priority.

- VDFS p22 root boots with `init=/sbin/init`.
- The initramfs `/init` cannot be executed in the probe boot.
- It is not needed for the current VDFS control path.

Next:
- Revisit only if VDFS boot and initd/systemd startup are complete.

## 9. Secondary-core startup

Resolved for the production control boot.

- CPUs 1-3 enter the kernel holding pen.
- The earlier CPU 3 `0x0c` loop is a monitor-probe artifact.
- No `failed to boot: -38` remains in the production control boot.

## 10. Micom registration

Benign and resolved.

- `device3 did not registered completely` is a registration-order line.
- The dropped first frame is tag `0x29` and no-op in the handler.
- Device 3 registers immediately afterward.

## 11. `system_server`/power path

Closed for the stock-control hypothesis.

- No captured stock boot showed `system_server` SIGABRT, `Aborted`, or a
  power-path backtrace. The executable's `poweroff_control_cb` reads
  `vconf_get_int`, normalizes values 2/3/4, writes `vconf_set_int`, and calls
  `poweroff`; it contains no direct `abort` call in that range.
- The earlier TODO claim is disproven for the investigated control logs; it
  was not linked to a reproducible live crash.
- `net-config` has an independent watchdog/power subscription path, but no
  captured watchdog SIGABRT confirms a `net-config` ↔ `system_server`
  dependency in the stock run.

Residual:
- A real power-key/standby/poweroff scenario was not exercised. The callback
  behavior remains unverified on a physical or interactive lab target.

## 12. Factory store and panel identity

Open.

- Repaired p24 seed loads 119 factory rows.
- `SVC_PANEL` key `1311` returns `0x66e`.
- Panel type key `3104` is not confirmed and must not be guessed.
- `TIFSDaemon` is still the primary blocker.
- `GetStrDataFromDB` callers can use unchecked indices; `comss` has a known
  bad `index*28` risk when lookup key `18` fails.

Next:
- Fix TIFSDaemon negative-index handling.
- Reproduce `GetIntDataFromDB(18)` and `GetStrDataFromDB` with synthetic callers.
- Confirm whether real panel data exists in `PanelIndex.dat`,
  `ModelIndex.dat`, or only in `.factory.db`.

## 13. Framebuffer device registration

Resolved.

- `/dev/fb0` is not a model gap.
- Production uses DRM/X11 and the logo/mixer path.

## 14. `fastvideo` and logo startup

Resolved enough to proceed.

- `fastvideo` reaches userspace.
- The boot-logo EEPROM item `0x2506` is modelled as `0`.
- `/dev/logo`, `sdp-logo.ko`, and plane source writes load successfully.
- LVDS format `0x1` reaches the logo path in successful boots.

Remaining:
- Exact plane format and live `0x397d9900` versus static `0x3978d000`.
- Fade/plane-enable behavior at the char-device ioctl level.

## 15. Scanout and visible video

Open. Earlier live PPM evidence is not reproducible as of 2026-11-20:

- Four stock-card timeline captures at 8, 13, 20, and 30 s were fully
  black (2,073,600 of 2,073,600 RGB pixels zero).
- Xorg readiness, `/dev/dri/card0`, `/dev/dri/card1`, and fastvideo startup
  are not sufficient proof of rendered scanout.
- The live OSD plane register `0x005D4030` was written with
  `0x37b84000`. At 14 s that physical buffer was sampled as zero, and the
  final screendump remained black.
- The static scanout alias `0x3c500000` and the earlier candidate
  `0x3978d000` were also zero in live memory samples.
- `/dev/logo` and `sdp-logo.ko` load, but no reproducible nonzero guest
  framebuffer has been confirmed in a fresh timeline run.
- Repeated CH:5 `0xc4/0xce` incomplete transfers occur before the final
  SELP hold and remain a leading unresolved dependency for panel/TCON setup.
- Synthetic regressions added:
  - `Sdp1406I2cIrqTests.test_live_ch5_panel_addresses_raise_spi_45`
  - `Sdp1406OsdLiveAddressScanoutTests.test_live_osd_address_switches_screendump_source`

Closure condition:
- Capture a guest memory region that is visibly nonzero after Xorg/X11
  starts, identify the exact OSD/mixer base and pixel format, and reproduce
  the PPM regression across multiple stock-card boots.
  The bare setuid warning is not a boot failure.

Residual:
- Exact plane offset/format remains open.
- The current boot can still enter later SELP/module faults after video output.

## 16. WIFI_VENDOR and micom 0x30

Partly resolved.

- Command `0x30` is mapped to ACK tag `0x30`.
- The live boot no longer logs `[30] timed out`.
- `WIFI_VENDOR` still depends on factory-store/factory-service behavior.

Next:
- Confirm the final `WIFI_VENDOR` reply after TIFSDaemon and kfactoryd are stable.

## 17. Bluetooth

Open.

- `usb-bcm-btusb` answers Reset, Read Local Version, Features, Scan Enable,
  and synthetic HCI/vendor commands.
- 37 HCI commands are observed at the control transport.
- BD_ADDR is synthetic and unverified.
- RAM-download launch and unknown vendor commands remain open.
- BTUSB bring-up tests pass.
- TIFSDaemon and factory-service stability block normal `btapp` startup.

Next:
- After item 16 is stable, confirm `btdl`, firmware-version decisions,
  `/dev/btusb0`, and vendor download completion.

## 19. HDMI RX NULL-pointer stop

Partially resolved: HDMI MMIO is now stateful; pointer/root cause remains open.

- Stock 60 s control: CPU2 process `tHdmiRx01` PID 601 stopped at
  `pc=0xc03fa01c`, `lr=0xbeb76994`, `r0=0xbeba2b08`, with the kernel
  reporting a NULL dereference at virtual address `0x00000000`.
- `0xc03fa01c` disassembles to a 16-bit load/add/store sequence around
  `[r0]`, not an obvious HDMI MMIO opcode.
- `sdp_hdmi.ko`, `tztv_hdmi_switch.ko`, and
  `tztv_hdmi_switch_api.ko` load before the stop. The board models HDMI
  MMIO only as the `0x00580000` read-zero/write-discard stub.
- CPU3 also stops in `insmod` at `0xc028f448`, in a generic module operation
  loop; it is an SMP stop following the primary fault, not enough to identify
  the first missing HDMI operation.
- The address report is inconsistent (reported FAR `0`, while `r0` is
  `0xbeba2b08`), so this is not yet a confirmed HDMI MMIO or NULL-HDL pointer
  defect.
- GDB trace attempts at `0xc03fa01c` were timing-sensitive and did not yield
  a valid target-range stop. Next: a virtual breakpoint in the userspace
  `beb76994` frame or the HDMI module's I/O/ioctl call, with a 30 s control,
  and low-overhead guest-RAM/MMIO trace rather than broad PC stop.
- Live MMIO trace (2026-11-20):
  - `-d unimp` captured repeated writes/reads in the `0x00580000` HDMI window,
    including `0x10000`, `0x10004`, `0x10040`, `0x30000-0x3000c`,
    `0x17100/0x18100/0x19100/0x1a100`, and port interrupt/status offsets.
  - The previous read-zero/write-discard stub had no readback state for these
    sequences.
  - A stateful register-file model is implemented. A 30 s control no longer
    hit the original `tHdmiRx01` stop and progressed until a later paging fault
    in unrelated user processes. This proves the register state gap, but not
    that missing HDMI semantics were the sole root of the original NULL stop.

## 18. Stub windows

Open, low priority.

The following are log-only unless a guest driver proves a semantic value:

- `0x5c0000`, `0x5e0000`, `0x790000`
- `0x900000`, `0x930000`, `0x170000`
- `0xb50000`, `0xb60000`, `0xb70000`, `0xb74000`
- `0xa00000`, `0xe00000`

Tests verify only read-zero/write-discard behavior.

## 20. VDFS FSDaemon write-page BUG

Open. Highest-priority kernel-oops lead after CH:5 I2C traffic.

- Live stock-card traces: CPU 1 process `FSDaemon` faults in
  `fs/vdfs/data.c:1330` at virtual address `c0182e8c`.
- Disassembly of the production `03-uImage` payload shows `c0182e8c` is
  `udf #18` in `c0182e04`. The preceding instruction tests `PageLocked`
  (`page->flags & 1`); the BUG fires when the page descriptor's locked bit
  is clear.
- Backtrace: `c018316c` calls `c0182e04`, then the VFS/MMC path reaches
  FSDaemon. The live stack is followed by synchronous MMC writes.
- The writes begin at global sector `0x4d3662` with 512 sectors, then
  `0x4d3661` with one sector. The `0x4d3661` sector is the first sector of
  GPT p25 (`5060193`) and is exactly one sector past p24's declared end.
  GPT p24 starts at `2789505` and contains `2270688` sectors.
- The p24 `usr/data_backup.img` seed is only 29,188,096 bytes (56,812
  sectors), much smaller than p24's declared 1.16 GiB, and the card places
  its VDFS superblock at p24 with the rest sparse.
- Offline VDFS audit of the original p24 seed confirms the sparse layout:
  `declared_blocks=283836`, `image_blocks=7126`, `overflow_extents=0`,
  `overflow_objects=0`, `max_node_block_end=7126`, and
  `meta_extent_end=12310`. This proves sparse metadata validity, but it
  also means p24 declares its full partition while the seed's on-image
  allocation range ends far below the partition tail.
- Sector `0x4d3661` is p24's next block boundary at GPT p24 block
  `283836` (`2789505 + 283836 * 8 = 5060193`). It is not simply p24 writing
  into p25: the absolute MMC request is p24's declared end, where p25 starts.
- The live page-lock BUG remains separate from this boundary arithmetic:
  the audit detects no overflow extents/objects in the seed, so it does not
  prove the seed itself caused the BUG.
- MMC model evidence:
  - `sdp1406_mmc_command()` maps block-addressed CMD17/18/24/25 directly to
    `CMDARG * 512`; there is no GPT partition offset or partition translation.
  - `sdp1406_mmc_store()` writes guest descriptor buffers at that raw byte
    position, drops writes only beyond the attached image length, and advances
    `pos` by descriptor length.
  - Therefore the live absolute sector `0x4d3661/0x4d3662` values are
    forwarded as raw LBAs, not shifted by p24/p25 partition handling.
  - This is negative evidence against a missing MMC partition translation or
    an incorrect GPT start offset. It does not test whether the guest chose
    the correct VDFS block.
- Independent live control after 25 s and 152 s observed CH:4 EEPROM timeout
  text but did not reproduce `fs/vdfs/data.c:1330` or its page-lock breakpoint.
  GDB breakpoints at `c0182e8c` and the write function did not hit in the
  detached paused/running controls. These are negative evidence, not proof
  that the stock BUG is nondeterministic.
- Synthetic confirmation status:
  - Built `/tmp/03-uImage-vdfs-page-lock-probe.bin` by replacing only the
    little-endian UDF bytes at uImage payload offset `0x17aecc` with ARM
    `mov r0,r0`, and recalculated the payload/header CRC fields.
  - Byte comparison shows only the header/data CRC fields and the four UDF
    bytes differ from the stock uImage.
  - A 65 s stock control and a 65 s probe control both stopped at the
    identical `vdfs3` mount line, without reaching FSDaemon or the stock
    22 s BUG.
  - This differential is inconclusive: it does not confirm or disprove that
    replacing the invariant changes writeback behavior.
  - The host-side code evidence confirms that `c0182e8c` is a guest kernel
    invariant failure, but the trigger is still not reproduced. Confirmed:
    a stock guest reached an unlocked-page UDF site in FSDaemon. Not
    confirmed: whether QEMU MMC, page-lock state, or VDFS allocation is the
    root cause.
- Reproductions: the same sector pair appears in the live GDB and stock,
  busy-enabled/busy-disabled, HDMI, SELP, and synthetic-TIFS traces. This
  is a VDFS/MMC boundary invariant, not a confirmed missing QEMU MMIO
  feature.

Trust assumption:
- VDFS block allocation and page locking must remain consistent, and no
  VDFS data block may escape p24.

Next:
- 2026-11-20 combined 65 s GDB control:
  - Stopped at FSDaemon VDFS UDF `0xc0182e4c`.
  - The old `0x8a800001` interpretation was a frame-slot misreading, not a
    confirmed kernel page descriptor.
  - Live stock logs reported a bad page at descriptor `cbf864c0`,
    pfn `fe5fc326`, flags `0x0`, mapping `null`, count and mapcount corrupted.
  - The same trace reached an internal/FSDaemon paging fault and synchronous
    MMC sectors `0x4d3662/0x4d3661`. No CH:5 ring record was captured at the
    UDF hit, so CH:5 is not confirmed as the trigger.
  - `tools/fsd_vdfs_trace.py` captures SELP release, UDF/write PCs, and
    CH:5 ring records for repeatable follow-up.
- Break on `c0182e44` and `c0182e8c`, recording `r0/r1/r4/r5/r6/r8/r9`, the page
  descriptor address and `page->flags`, plus the bdev/inode block mapping.
- Compare p24's VDFS superblock block limit and the requested block number.
- Confirm whether the request is p24-relative or incorrectly mapped into p25.
- Add a synthetic unit test only after the missing invariant is confirmed.

Negative evidence:
- A later MMC write is not proof of MMC corruption; it occurs after the
  kernel page-lock BUG and can be caused by the failed writeback path.
