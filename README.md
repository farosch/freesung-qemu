# QEMU analysis board for HawkM (SDP1406)

Contents:

- `hw/arm/sdp1406.c`: the `sdp1406` machine. RAM banks and GIC addresses follow
  the 1G device tree in `05-dtb.bin`. The `ttyS0` UART (`0x00190a00`, DTB cell 56 = GIC IRQ 88) is
  QEMU's Exynos UART, wired to `-serial stdio`. Other Samsung peripherals are
  logging stubs. A 1280x720 XRGB8888 console reads guest RAM at the scanout base
  `0x3c500000` (the DTB `samsung,dp0` range, an alias of the bottom of RAM2) and
  shows it through `-display sdl`. The pixel format is XRGB8888 (fourcc in `sdp-drm.ko`).
- `patches/0001-hw-arm-wire-sdp1406-board.patch`: `Kconfig` and `meson.build`
  wiring for the board file. `SDP1406` selects `EXYNOS4` for the UART model.
- `patches/0002-hw-char-exynos4210-raise-tx-irq-only-on-write.patch`: stops the
  Exynos UART model from re-raising TX pending on every update, which made a
  guest `UINTP` clear ineffective and produced an IRQ storm on `ttyS0`.
- `patches/0003-hw-char-exynos4210-ack-rx-tx-events-via-utrstat.patch`: adds the
  `UTRSTAT` TX/RX event bits and the write-1-to-ack path that the guest driver uses.
  Without it the `ttyS0` TX interrupt (IRQ 88) is never acknowledged and CPU2 spins in
  the handler after the VDLP banner.
- `tests/uart/start.S`: bare-metal UART smoke test (prints `SDP1406 UART OK`).
- `plugins/sysmem_trace.c`: TCG plugin that logs stores to the sys_mem table and
  reports the `udf` hit. Build and run commands are in the file header.

## Building

QEMU source is not vendored. Use upstream 10.2.1. On Debian or Ubuntu, install the
build dependencies first (verified with meson 1.10.1, ninja 1.13.2, Ubuntu 24.04 packages):

```sh
sudo apt install meson ninja-build libfdt-dev libpixman-1-dev libsdl2-dev
```

```sh
curl -LO https://download.qemu.org/qemu-10.2.1.tar.xz
tar -xJf qemu-10.2.1.tar.xz
cd qemu-10.2.1
patch -p1 < /path/to/Freesung/freesung-qemu/patches/0001-hw-arm-wire-sdp1406-board.patch
patch -p1 < /path/to/Freesung/freesung-qemu/patches/0002-hw-char-exynos4210-raise-tx-irq-only-on-write.patch
patch -p1 < /path/to/Freesung/freesung-qemu/patches/0003-hw-char-exynos4210-ack-rx-tx-events-via-utrstat.patch
cp /path/to/Freesung/freesung-qemu/hw/arm/sdp1406.c hw/arm/
mkdir build && cd build
../configure --target-list=arm-softmmu --without-default-features \
    --disable-docs --disable-werror --disable-tools --disable-guest-agent \
    --disable-download --disable-tcg-interpreter --enable-fdt=system \
    --enable-sdl --enable-pixman
ninja qemu-system-arm
```

`libfdt` headers are required (`libfdt-dev`). `--enable-sdl` needs `libsdl2-dev`
and `--enable-pixman` needs `libpixman-1-dev` (the monitor's `screendump` is
compiled only with pixman). Check `./qemu-system-arm -machine help` for `sdp1406`.

## Status

Boot with the serial console on stdio (kernel console is `ttyS0`, `0x00190a00`).
The `loader` device places the sysmem table the kernel reads at `0x80542b94`;
boot is silent without it:

**Always use the official firmware kernel `hawkm-msdu10-payloads/03-uImage`.** The probe kernel
has been removed from the generator and must not be used. The official kernel boots
with the sysmem loader and reaches the micom, gmac and cpufreq messages. The board defaults
to 4 CPUs (the GIC needs more than one CPU for the mask probe). Secondaries start at the
kernel's holding pen (`0x803f11c8` for this uImage), so `boot_secondary` no longer returns
`-38`. Later output stops at PHY and micom timeouts:

```sh
./qemu-system-arm -M sdp1406 -display none -serial stdio \
    -kernel freesung-tools/extract/T-HKMFDEUC/probe-artifacts/hawkm-msdu10-payloads/03-uImage \
    -dtb freesung-tools/extract/T-HKMFDEUC/probe-artifacts/hawkm-msdu10-payloads/05-dtb.bin \
    -device loader,file=freesung-tools/extract/T-HKMFDEUC/probe-artifacts/sysmem/sysmem-table.bin,addr=0x80542b94,force-raw=on \
    -append "console=ttyS0,115200N8 earlyprintk"
```

Boot with the VDFS root (no initramfs). This gets past the klog open that
fails with the initramfs. Build the card from the extracted `platform.img` (p22) and the
`usr/data_backup.img` seed (p24, `/opt`), then boot with the official `03-uImage`, which mounts
`/dev/mmcblk0p22`. With this command the official kernel reaches userspace (`FB:INIT`, `vconf-init`)
and showed no SELP hold or panic in a 90 s headless run. Its `FB:INIT` thread still tries to mount
`/dev/mmcblk0p26` and reports `wrong fs type`; that is not yet resolved. `init.d/mount.sh` mounts `p24` as `/opt` from the seed. The board adds
a RAM bank at `0x80000000` to the DTB, which avoids a `free_initmem()` oops. The VDFS root
is decompressed by the `sdp1406.unzip` model (a DMA gzip/DEFLATE engine). The kernel
reaches `vdfs3` and userspace starts; `/opt` and `/home` resolve. Video output is not
reached yet:

```sh
python3 freesung-tools/freesung-probe-artifacts.py   # also writes mmc/hawkm-card-root.img
./qemu-system-arm -M sdp1406 -display none -serial stdio \
    -kernel freesung-tools/extract/T-HKMFDEUC/probe-artifacts/hawkm-msdu10-payloads/03-uImage \
    -dtb freesung-tools/extract/T-HKMFDEUC/probe-artifacts/hawkm-msdu10-payloads/05-dtb.bin \
    -device loader,file=freesung-tools/extract/T-HKMFDEUC/probe-artifacts/sysmem/sysmem-table.bin,addr=0x80542b94,force-raw=on \
    -drive if=none,id=sdp1406-mmc,format=raw,file=freesung-tools/extract/T-HKMFDEUC/probe-artifacts/mmc/hawkm-card-root.img \
    -append "root=/dev/mmcblk0p22 init=/sbin/init rootfstype=vdfs ro console=ttyS0,115200N8 SELP_ENABLE=00000000 rootwait no_console_suspend earlyprintk sdp_sparsemem vmalloc=550M quiet"
```

Show the framebuffer console in an SDL window (needs a display; use `-display none`
to skip it). Attach the VDFS card so the root mount does not stall:

```sh
./qemu-system-arm -M sdp1406 -display sdl -serial stdio \
    -kernel freesung-tools/extract/T-HKMFDEUC/probe-artifacts/hawkm-msdu10-payloads/03-uImage \
    -dtb freesung-tools/extract/T-HKMFDEUC/probe-artifacts/hawkm-msdu10-payloads/05-dtb.bin \
    -device loader,file=freesung-tools/extract/T-HKMFDEUC/probe-artifacts/sysmem/sysmem-table.bin,addr=0x80542b94,force-raw=on \
    -drive if=none,id=sdp1406-mmc,format=raw,file=freesung-tools/extract/T-HKMFDEUC/probe-artifacts/mmc/hawkm-card-root.img \
    -append "root=/dev/mmcblk0p22 init=/sbin/init rootfstype=vdfs ro console=ttyS0,115200N8 SELP_ENABLE=00000000 rootwait no_console_suspend earlyprintk sdp_sparsemem vmalloc=550M quiet"
```

The board models the Mali-400 GPU at `0x600000` and an HDMI stub, but `mali.ko` still fails
its probe, so `/dev/mali0` and `sdp-drm.ko` do not load yet (see the Mali section in `DEVELOP.md`).

The micom UART is wired to the second `-serial` chardev. Add `-serial file:micom-tx.bin` to capture
its TX. The guest currently sends nothing there (see `TODO.md`).

The gmac has a PHY model (see the Network section in `DEVELOP.md`). The boot then finds the
PHY and registers `eth%d` instead of logging `Could not find PHY`. The DMA engine moves
frames to and from a `-nic socket` netdev (see the Network section in `DEVELOP.md`).

The console stays black until the guest writes to `0x3c500000`. The base comes from
the DTB `samsung,dp0` property, traced through `sdp-drm.ko`. The unpatched `03-uImage`
logs micom, gmac and cpufreq errors and then an RCU stall on CPU 2 about 60 s in when no
SD card is attached. The stall is a busy-wait in the root-mount MMC path. With the VDFS
card (the `-drive` option below) the boot reaches `vdfs3` with no stall, but a screendump
at 120 s is still all zero, so the guest never writes the scanout region. To check the display path
without a guest, load a frame and take a headless screendump with the monitor
(`screendump file.ppm`); the board test does this.

The UART test (`tests/uart/start.S`) builds with
`clang --target=arm-none-eabi -march=armv7-a -mfpu=none -nostdlib -c start.S` and
`ld.lld -Ttext=0x80000000 -e _start start.o`. Run it with `-kernel uart.elf`.

Registration works. The board's boot-info struct is heap-allocated, because QEMU
keeps the pointer for the CPU reset hook. The kernel loads at `0x80008000` and
runs with the MMU on. The DTB sits at `0x88000000` (inside the kernel's
`0x80000000` linear map). See DEVELOP.md for the open questions.

Initramfs experiment (opt-in, `-initrd` only). `freesung-qemu/initramfs/init.c` is a
freestanding ARM init that loads the display modules with `finit_module`. Build
and pack it (no root needed):

```sh
MODS=freesung-tools/extract/T-HKMFDEUC/extracted/working/filesystem-6/root/lib/modules/3.10.30/kernel/soc
OUT=freesung-tools/extract/T-HKMFDEUC/initramfs
mkdir -p $OUT
clang --target=arm-linux-gnueabi -march=armv7-a -mfloat-abi=soft -marm -O2 \
    -ffreestanding -fno-builtin -nostdlib -static -fuse-ld=lld -Wl,-e,_start \
    -o $OUT/init freesung-qemu/initramfs/init.c
python3 freesung-qemu/initramfs/mkinitramfs.py $OUT/initramfs.cpio \
    $OUT/init $MODS/sdp-drm.ko $MODS/sdp_hdmi.ko $MODS/sdp-logo.ko
```

Add `-initrd freesung-tools/extract/T-HKMFDEUC/initramfs/initramfs.cpio -append "... rdinit=/init"`
to the run command. The board loads the initrd at `0x8a000000`, inside the extra
`/memory` bank. The initramfs route does not reach `/init`: the kernel oopses in
`free_initmem()` before it runs, and the klog open fails. See DEVELOP.md for the status.

The `free_initmem()` oops needs a RAM bank at `0x80000000` in the DTB `memory` node. The
shipped DTB lacks it, so the board model appends a `0x80000000` (`0x10000000`) bank to the
DTB's existing `/memory` node. The DTB and any initrd are reserved with `/memreserve`. This is
not a verified device memory map; see DEVELOP.md.

Board regression test (`tests/test_qemu_sdp1406_board.py`): starts the machine
paused with no kernel or DTB and checks the MMIO map, the GIC and the RAM banks
through the QEMU monitor. It needs `FREESUNG_QEMU=<path to qemu-system-arm>` (or
`qemu-system-arm` on PATH) and skips without an sdp1406-capable build:

```sh
FREESUNG_QEMU=/path/to/qemu-system-arm python3 -m unittest discover -s freesung-qemu/tests -p test_qemu_sdp1406_board.py -v
```

Mixer register test (`tests/test_qemu_sdp1406_mixer.py`): uses qtest to check that the
mixer register file stores and reads back values written by the driver. It uses the same
`FREESUNG_QEMU` variable:

```sh
FREESUNG_QEMU=/path/to/qemu-system-arm python3 -m unittest discover -s freesung-qemu/tests -p test_qemu_sdp1406_mixer.py -v
```
