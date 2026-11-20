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
  - All 58 I2C qtests pass on the rebuilt 10.2.1 QEMU.
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
- Latest control: rebuilt QEMU passes all 57 I2C qtests, including CH:5, CH:6,
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
- New qtests cover CH:5 and CH:6 IRQ/SPI mapping; all 57 I2C qtests pass.

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
- `SELP_ENABLE=00000001` avoids the initial spin but stalls RCU on CPU 1.
  It is not a valid fix. A direct 140 s stock-card reproduction disproved
  the RCU-specific interpretation: CPU 1 was running `tvs-main-loop` through
  `c00148f0` and emitted MMC warnings in softirq context before it reached
  the same T32 spin. No RCU stall banner or RCU backtrace was present.
- Static caller: production kernel code at `0xc00148f0`.
- Direct disassembly of `03-uImage` confirms:
  - `0xc0014dfc` prints `[SELP] while loop ... please attach T32...`.
  - `0xc0014e04` is an unconditional branch to itself.
  - `0xc0014e08` resumes only after the status word at
    `SELP_PTR_ADDR + 0x18` equals r2.
  - The pointer literal is `0xc0014ee0`, and the status block is
    `0xc051c338` in the stock test.
- A full `-S` stock-root control boot hit thread 4 at `0xc0014e04`.
  `tools/t32_attach.py` read r2 as `0`, changed the status word from
  `0xffffffff` to `0`, wrote PC `0xc0014e08`, and detached successfully.
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
- `tzdev.ko` export of tzmem fails at boot.
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

Open.

- `system_server` aborts on the TV power path.
- The link to `net-config` watchdog SIGABRT is not confirmed.
- Determine whether this is a real missing service, a process lifecycle issue,
  or a result of factory-store failure.

Next:
- Disassemble and trace the power-path abort from the first invalid call.
- Compare with a boot that does not reach SELP.

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

Resolved for framebuffer path closure.

- Mixer `+0x30` and OSD `0x005D4030` update the display plane source.
- `Sdp1406PlaneScanoutTests` verifies source switching and PPM output.
- Final live PPM contained 2,226,576 nonzero RGB bytes.
- `/dev/logo` and `sdp-logo.ko` loaded.

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

Open.

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

## 18. Stub windows

Open, low priority.

The following are log-only unless a guest driver proves a semantic value:

- `0x5c0000`, `0x5e0000`, `0x790000`
- `0x900000`, `0x930000`, `0x170000`
- `0xb50000`, `0xb60000`, `0xb70000`, `0xb74000`
- `0xa00000`, `0xe00000`

Tests verify only read-zero/write-discard behavior.
