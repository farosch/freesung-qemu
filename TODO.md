# TODO

Open investigation leads for the SDP1406 (HawkM) QEMU board.
[DEVELOP.md](DEVELOP.md) contains verified behavior. Use the production kernel
`03-uImage` only.

## Status table

| Status | Lead |
|--------|------|
| Open | I2C CH:4/CH:5/CH:10 transaction tails |
| Open | TrustZone SMC/tzmem export |
| Closed | TIFSDaemon negative index and CZip mapping |
| Waiting on TIF | Factory-store/panel identity |
| Open | Scanout and visible video |
| Open | Bluetooth bring-up |
| Open, inconclusive | HDMI RX pointer fault |
| Open, highest-priority kernel lead | VDFS FSDaemon write-page BUG |
| Deferred | Mali-400 command/MMU detail |
| Deferred | Stub-only MMIO windows |
| Closed | SELP hold; secondary cores; Micom registration |
| Closed | p26 `/mnt/drm`; `/dev/fb0`; fastvideo/logo transport |
| Closed | `system_server` stock power-path hypothesis |
| Closed | WIFI_VENDOR MBOX `0x30` transport |

## 1. I2C CH:4/CH:5/CH:10 timeouts

**Open.** CH:7 and Sii9777 transport are resolved. CH:4 EEPROM, CH:5
panel/control traffic, and CH:10 FRC traffic retain residual completion and
slave-semantics gaps.

Confirmed:
- CH:4 is controller `0x00190180`, 8-bit EEPROM address `0xa0/0xa1`.
- CH:5 is `0x001901a0`; CH:6 is `0x001901c0`; CH:10 is `0x00191040`.
- CH:10 live 8-bit `0xc8` encodes 7-bit FRC address `0x64`.
- NACK sets `STAT_LRB`, and the following address phase clears stale LRB.
- CH:4 models 64 KiB storage, 16-bit pointers, 128-byte wrap, and 5 ms inferred
  write-cycle busy state.
- A busy-enabled live control reached CH:4 write failure at 19.195 s. Busy
  disabled did not produce the same diagnostic in two controls. The 5 ms
  behavior is not hardware-confirmed.

Remaining:
- Prove CH:4 STOP/repeated-START/final-NACK/write-poll framing.
- Add CH:5 variable read-length/NACK coverage for `0xc4`, `0xce`, and
  other observed addresses.
- Confirm CH:6 `0x44/0x54` endpoints and CH:10 repeated-START behavior.
- Correlate CH:5 `STAT/DS/IRQ` with guest-RAM ring `0x010e0000`.

Tests: `tests/test_qemu_sdp1406_i2c.py`.

## 2. TrustZone SMC and tzmem export

**Open.** The monitor stub currently returns `r0=0`; synthetic ring/export/TRM
tests pass, but the live first-SMC state remains unknown.

Next:
- Break at the first SMC and record SCTLR, TTBR, r0/r1/r12, CPU mode, and
  the export page address.
- Compare control and loader boots.
- Confirm whether the monitor page and MMU mapping differ.

Evidence remains inconclusive: the stock boot loaded `tzdev.ko` but reached
NULL and SELP faults before a reproducible tzmem allocation diagnostic.

Live control result (2026-11-21):
- A normal stock VDFS boot with QMP ran beyond `vdfs3` and was sampled through
  physical memory save at `0x00201000`.
- The 4096-byte SMC log/first ring slot region was all zero at the sampled
  point, so no export page or ring record was observed.
- The same run remained at the `vdfs3`/system-bus stage and did not emit
  `tzdev.ko`, `TEEC`, `tzmem_mmap`, or SELP diagnostics; it is negative timing
  evidence, not a reproduction of tzmem failure.
- The RSP/GDB controls reached a SELP stop or were cut off by host timeout
  before a confirmed monitor SMC stop. They do not supersede direct stock
  control logs that showed `tzdev.ko`.

Closure requires a live SMC call with `r1=4096`,
`r12=0x0e000005`, and a linear-map export VA, followed by a ring or TRM
reply exchange. Until that state is observed, the TrustZone export lead remains
inconclusive.

## 3. TIFSDaemon negative-index crash

**Closed for the synthetic isolated target.** The ELF guard, Samsung auth
bypass, CZip layout, and p24 seed corruption all have reproducible closure.

Confirmed:
- `GetModelStr` received `r1=-1` and indexed a 119-row table.
- The ELF guard bytes and `"NONE"` fallback are pinned and tested.
- A test-only auth patch repaired uImage CRC and removed the stock auth denial.
- A synthetic boot reached CZip wrong-magic/extents and later faulted at
  ELF `0x000d5124`; the original `0x1e1` fault was not reproduced.
- Zlib-wrapped CZip chunks decode in the QEMU unzip model.
- The synthetic p22 `--patch-tif` blob now declares CZip descriptor layout 5,
  matching the live VDFS4 layout-5 decoder; layout 4 was the previous
  synthetic-format mismatch and remains to be verified in a live boot.
- `kfactoryd` crashes in synthetic cards that rebuild p24 with
  `mkfs.vdfs -i -r`; audit reports 1887/1888 resolved forks and a
  `FactoryIndex_sorted.dat` extent crossing metadata.
- A layout-5 p22 card using the original compact p24 seed ran 40 s with no
  `kfactoryd`, CZip, negative-index, or TIF ELF fault.

Closure condition:
- Boot a valid authorized or isolated synthetic TIFSDaemon without `0x1e1`,
  CZip wrong-magic, or `0x000d5124` faults.
- Confirm `kfactoryd`, `TIFSDaemon`, `comss`, and `btapp` remain alive.

Tests: `tests/test_qemu_sdp1406_tif.py`.

## 4. Factory store and panel identity

**Waiting on TIFSDaemon/kfactoryd stability.**

Confirmed:
- Repaired p24 seed loads 119 factory rows.
- `SVC_PANEL` key `1311` returns `0x66e`.
- Panel type key `3104` is not confirmed and must not be guessed.

Next, after TIF is stable:
- Reproduce `GetIntDataFromDB(18)` and `GetStrDataFromDB` synthetic callers.
- Verify whether real panel data exists in `PanelIndex.dat`,
  `ModelIndex.dat`, or only `.factory.db`.
- Check the `comss` unchecked `index*28` lookup path if key `18` fails.

## 5. Scanout and visible video

**Open.**

Fresh 8/13/20/30 s stock timelines were fully black. Xorg and fastvideo startup
do not prove rendered scanout. A live OSD register write used
`0x005D4030 = 0x37b84000`, but that physical buffer and the static scanout
candidates sampled as zero.

CH:5 `0xc4/0xce` failures precede later holds and remain a possible panel/TCON
dependency, but no causality is confirmed.

2026-11-21 live controls:
- 150 s and 300 s stock/QMP runs remained at `vdfs3`/Kdbus and did not reach
  Xorg, DRM card creation, fastvideo, or an OSD plane commit.
- The 300 s control recorded mixer `+0x30 = 0` and 64 KiB samples containing
  no nonzero bytes at `0x3978d000`, `0x37b84000`, or `0x3c500000`.
- These runs establish negative timing evidence only: they do not reproduce a
  guest-visible frame or prove that mixer/OSD enable state is causal.

Closure condition:
- Capture a visibly nonzero guest memory region after Xorg/X11 starts.
- Identify the exact OSD/mixer base and pixel format.
- Reproduce the PPM/result across multiple stock-card boots.

## 6. Bluetooth

**Open.**

The BTUSB model answers reset, local-version, features, scan-enable, and
synthetic HCI/vendor commands. 37 commands have been observed.

Remaining:
- RAM-download launch and unknown vendor commands.
- BD_ADDR verification.
- `btdl`, firmware-version decisions, `/dev/btusb0`, and vendor download
  completion after TIF/factory services are stable.

## 7. HDMI RX NULL-pointer stop

**Open, inconclusive.** HDMI MMIO is now stateful; the original pointer
root cause is not confirmed.

The stock fault stopped CPU2 in `tHdmiRx01` at `0xc03fa01c` with FAR `0`
and an inconsistent `r0=0xbeba2b08`. CPU3's `insmod` stop was later and
SMP-secondary, not sufficient to identify the first fault.

Live MMIO tracing captured writes and reads in `0x00580000`; the stateful
model changed boot timing and prevented the original stop in one control, but
does not prove HDMI MMIO alone caused it.

Next:
- Break in userspace frame `beb76994` or the HDMI module I/O/ioctl path.
- Capture low-overhead guest memory/MMIO records during a 30 s control.
- Preserve negative and timing-sensitive GDB controls.

## 8. VDFS FSDaemon write-page BUG

**Open. Highest-priority kernel-oops lead after CH:5 traffic.**

Confirmed:
- CPU1 faults in `fs/vdfs/data.c:1330` at `c0182e8c`.
- `c0182e8c` is `udf #18` after a missing `PageLocked` invariant.
- Live bad-page descriptor was `cbf864c0`, pfn `fe5fc326`, flags `0`,
  mapping `null`.
- MMC writes reached sectors `0x4d3662` and `0x4d3661`.
- Sector `0x4d3661` is p25's first sector and p24's exact block boundary.
- p24's VDFS seed is sparse and has no overflow extents/objects.
- The MMC model forwards raw LBAs without GPT partition translation.

Not confirmed:
- Whether CH:5 traffic, VDFS allocation, MMC write state, or page-lock state
  caused the invariant failure.
- Whether the guest chose p24/p25 correctly.

2026-11-21 combined stock GDB control:
- Reproduced a hit at `FSD_UDF_PC=0xc0182e4c` during the stock VDFS mount
  path.
- The hit reported `r0=0xcb869b6c`, `r1=0`, `r4=0`, and `r6=1`; the
  `PageLocked` invariant was not satisfied.
- The repeat hit the same `r0=0xcb869b6c` and flags `0xc00a1df0`.
- The helper now dumps raw words at offsets `+4/+8/+12` until kernel layout
  symbols confirm exact `count/mapcount/mapping` offsets.

Next:
- Break at `c0182e44` and `c0182e8c`; capture `r0/r1/r4/r5/r6/r8/r9`,
  page descriptor/flags, and bdev/inode block mapping.
- Compare the requested block with p24's VDFS superblock limit.
- Add a synthetic unit test only after the missing invariant is reproduced.

The earlier `0x8a800001` interpretation was a frame-slot misreading and is
not a kernel page descriptor.

## 9. Mali-400 and GPU coverage

**Deferred.** Reset, MMU, GP/PP product IDs, and basic paths are modelled.

Expand only after an unmapped Mali register or MMU fault is reproduced.
Capture the fault and add one focused synthetic test before adding command,
interrupt, or memory-format semantics.

Tests: `Sdp1406MaliMmuTests` and Mali board tests.

## 10. Stub-only MMIO windows

**Deferred, test-only.** Do not expand these windows until a guest driver
proves that a register value affects behavior:

- `0x5c0000`, `0x5e0000`, `0x790000`
- `0x900000`, `0x930000`, `0x170000`
- `0xb50000`, `0xb60000`, `0xb70000`, `0xb74000`
- `0xa00000`, `0xe00000`

Tests verify read-zero/write-discard behavior only.

## Closed / resolved

### SELP hold and RCU stalls

The external T32 handshake is resolved. Production PC `0xc0014e04` enters
WFE; `0xc0014e0c` is the debugger-release fallthrough. The status block is
`0xc051c338+0x18`. No missing SELP register model or RCU stall is indicated.
The stock guest later encounters independent faults.

### Secondary-core startup

CPUs 1-3 enter the production holding pen. The CPU 3 `0x0c` loop was a
monitor-probe artifact; no `failed to boot: -38` remains.

### Micom registration

`device3 did not registered completely` is a registration-order line.
The dropped first `0x29` frame is handled by the registration logic.

### Card p26 `/mnt/drm`

p26 is a 6144K VDFS1002 partition mounted at `/mnt/drm`. The missing
securestorage backup warning is an unmatched shell glob/source absence, not an
EEPROM or block-device gap. No further QEMU block-model change is justified.

### `/dev/fb0` and fastvideo/logo startup

`/dev/fb0` is not the production display path. DRM/X11, mixer/OSD, `/dev/logo`,
`sdp-logo.ko`, logo EEPROM item `0x2506`, and LVDS format `0x1` are resolved.
Exact plane format/fade behavior remains presentation detail only.

### `system_server` power path

No stock boot reproduced `system_server` SIGABRT or an abort backtrace.
The extracted poweroff callback has no direct abort call and only needs its
real power-key/physical/interactive scenario to be exercised.

### WIFI_VENDOR MBOX 0x30

MBOX command `0x30` transport is resolved. The 9-byte reply is
`ff ff 30 00 00 00 00 00 30`; it is not the factory vendor value.
`WIFI_VENDOR` is system-info key `0x4e9`, read through TIF factory index
`0x33d`; it remains gated by TIF/kfactoryd but must not be synthesized from
the MBOX ACK.
