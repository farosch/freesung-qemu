/*
 * Samsung SDP1406 (HawkM) board for analysis runs.
 *
 * The RAM ranges and the GIC base addresses follow the 1G device tree in
 * 05-dtb.bin. The device tree is loaded unchanged: QEMU's generic memory
 * fixup is skipped so the three RAM banks stay as the tree declares them.
 *
 * Samsung peripherals are not modelled. Their MMIO windows are stubs that
 * return zero and log each access under "-d unimp".
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "hw/boards.h"
#include "hw/arm/machines-qom.h"
#include "hw/sysbus.h"
#include "net/net.h"
#include "system/dma.h"
#include "block/block.h"
#include "system/block-backend.h"
#include "qemu/bswap.h"
#include "hw/usb/hcd-ehci.h"
#include "hw/usb/hcd-ohci.h"
#include "hw/usb/hcd-xhci-sysbus.h"
#include "hw/arm/boot.h"
#include "target/arm/cpu.h"
#include "hw/arm/exynos4210.h"
#include "hw/loader.h"
#include "hw/qdev-properties.h"
#include "hw/intc/arm_gic.h"
#include "hw/irq.h"
#include "ui/console.h"
#include "hw/display/framebuffer.h"
#include "system/system.h"
#include "system/reset.h"
#include "system/memory.h"
#include "system/address-spaces.h"
#include "qemu/log.h"
#include "qemu/timer.h"
#include "qemu/error-report.h"
#include "qemu/units.h"
#include "system/device_tree.h"
#include "system/block-backend.h"
#include "system/block-backend-global-state.h"
#include <libfdt.h>
#include <zlib.h>

#define SDP1406_RAM0_BASE   0x01000000ULL
#define SDP1406_RAM0_SIZE   0x1f000000ULL
#define SDP1406_RAM1_BASE   0x22800000ULL
#define SDP1406_RAM1_SIZE   0x15000000ULL
#define SDP1406_RAM2_BASE   0x40000000ULL
/* /memory lists only the first 112 MiB of this bank. The DTB sdp_mmap
 * carve-outs above it (mfc-b at 0x47200000, hen-b, dp1, align1) run up to
 * 0x50000000, and the kernel maps them, so the whole 256 MiB is backed. */
#define SDP1406_RAM2_SIZE   0x10000000ULL
/* DTB sdp_mmap carve-outs below RAM1: cursor, dp-ttx-vbi and align2. They are
 * absent from /memory, so the kernel does not manage them, but the DRM cursor
 * BO is backed here. */
#define SDP1406_SDP_MMAP_BASE 0x22600000ULL
#define SDP1406_SDP_MMAP_SIZE 0x00200000ULL

#define SDP1406_GICD_BASE   0x00781000ULL
#define SDP1406_GICC_BASE   0x00782000ULL
#define SDP1406_GIC_IRQS    160
/* The kernel's GIC CPU-mask probe needs a multi-CPU GIC: QEMU's GIC makes
 * ITARGETSR read-only zero when num-cpu is 1. The kernel also tries CPU1-3. */
#define SDP1406_MAX_CPUS    4

/* Generic timer output indices (target/arm/gtimer.h) and the PPI numbers the
 * DTB gives them. */
#define SDP1406_GTIMER_PHYS 0
#define SDP1406_GTIMER_SEC  3
#define SDP1406_PPI_NS_PHYS 30
#define SDP1406_PPI_SEC_PHYS 29

/* The *_IRQ defines are DTB interrupts cells (SPI number minus 32). QEMU's GIC
 * takes the cell as its gpio input index; the GIC IRQ is cell + 32.
 * ttyS0 in the DTB: "samsung,sdp-uart" at 0x00190a00, cell 56 (GIC IRQ 88).
 * The register layout is assumed to match the S5P-style UART that QEMU models;
 * the console output is the check. */
#define SDP1406_UART0_BASE  0x00190a00ULL
#define SDP1406_UART0_IRQ   56
/* DTB "samsung,sdp-uart" at 0x00190a80, cell 58 (GIC IRQ 90). The rootfs
 * /etc/inittab runs agetty on ttySAC2, which the Samsung serial driver
 * assigns by probe order: the third sdp-uart node. Second -serial chardev. */
#define SDP1406_TTYSAC2_BASE 0x00190a80ULL
#define SDP1406_TTYSAC2_IRQ  58
#define SDP1406_UART_FIFO   16
/* DTB "samsung,sdp1304-uart-micom" at 0x00190ac0. The legacy UART micom
 * protocol is not modelled; production micom traffic uses the messagebox. */
#define SDP1406_MICOM_BASE  0x00190ac0ULL
/* sdp_messagebox@800100 in the DTB: cell 83 (GIC IRQ 115). */
#define SDP1406_MBOX_BASE   0x00800000ULL
#define SDP1406_MBOX_IRQ    83

/* USB host controllers from the DTB. Each slot n is 0x10000 apart: EHCI at
 * 0x500000 + n*0x10000 (cell 11 + 2n) and OHCI at 0x508000 + n*0x10000 (cell
 * 12 + 2n). */
#define SDP1406_USB_EHCI_BASE  0x00500000ULL
#define SDP1406_USB_OHCI_BASE  0x00508000ULL
#define SDP1406_USB_HC_STRIDE  0x10000ULL
#define SDP1406_USB_HC_COUNT   4
#define SDP1406_USB_EHCI_IRQ(n) (11 + 2 * (n))
#define SDP1406_USB_OHCI_IRQ(n) (12 + 2 * (n))
/* DTB /xhci@00400000 (samsung,xhci-hcd): cell 10, reg size 0xc700. The rest of
 * the 1 MiB window up to 0x500000 stays read-zero. */
#define SDP1406_XHCI_BASE      0x00400000ULL
#define SDP1406_XHCI_IRQ       10
#define SDP1406_XHCI_SIZE      0x0000c700ULL
#define SDP1406_XHCI_WINDOW    0x00100000ULL

/* The kernel is linked at 0x80008000 and its .init pages are freed at
 * 0x804CF000, so the bank the kernel runs from is added to the DTB. The
 * DTB's own bank list starts at 0x01000000 and omits it. The DTB sits in
 * that bank at 0x88000000: 0x8e000000 was tested and stops the console. */
#define SDP1406_KERNEL_RAM_BASE 0x80000000ULL
#define SDP1406_KERNEL_RAM_SIZE 0x10000000ULL
#define SDP1406_DTB_ADDR    0x88000000ULL
/* The initrd is placed by the board, clear of the DTB, instead of QEMU's
 * default of 0x88000000. */
#define SDP1406_INITRD_ADDR 0x8a000000ULL

/* Display scanout: the DTB "sdp_mmap" samsung,dp0 range (base, size). It lies
 * in the gap between the RAM1 and RAM2 banks, so it aliases the bottom of RAM2
 * and is not listed in /memory. The pixel format is XRGB8888 (the fourcc
 * appears in sdp-drm.ko). */
#define SDP1406_SCANOUT_BASE 0x3c500000ULL
#define SDP1406_SCANOUT_SIZE 0x024c0000ULL
#define SDP1406_FB_BASE     SDP1406_SCANOUT_BASE
#define SDP1406_FB_WIDTH    1920
#define SDP1406_FB_HEIGHT   1080
#define SDP1406_FB_BPP      4

static uint64_t sdp1406_stub_read(void *opaque, hwaddr addr, unsigned size)
{
    qemu_log_mask(LOG_UNIMP, "%s: read 0x%" HWADDR_PRIx " (%u bytes)\n",
                  (const char *)opaque, addr, size);
    return 0;
}

static void sdp1406_stub_write(void *opaque, hwaddr addr, uint64_t value,
                               unsigned size)
{
    qemu_log_mask(LOG_UNIMP,
                  "%s: write 0x%" HWADDR_PRIx " = 0x%" PRIx64 " (%u bytes)\n",
                  (const char *)opaque, addr, value, size);
}

static const MemoryRegionOps sdp1406_stub_ops = {
    .read = sdp1406_stub_read,
    .write = sdp1406_stub_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void sdp1406_add_stub(MemoryRegion *sysmem, const char *name,
                             hwaddr base, uint64_t size)
{
    MemoryRegion *mr = g_new0(MemoryRegion, 1);

    memory_region_init_io(mr, NULL, &sdp1406_stub_ops, (void *)name, name,
                          size);
    memory_region_add_subregion(sysmem, base, mr);
}

/* Audio DSP status words polled by devAe_WatchDog in sdp_alsa.ko at media
 * offsets 0x4010-0x401c and 0x8010-0x801c. The driver builds these offsets
 * as immediates, so there is no literal-pool constant to match. Reported as
 * 1 as an experiment. */
static uint64_t sdp1406_dsp_status_read(void *opaque, hwaddr addr,
                                        unsigned size)
{
    qemu_log_mask(LOG_UNIMP, "sdp1406.dsp-status: read 0x%" HWADDR_PRIx "\n",
                  addr);
    return 1;
}

static void sdp1406_dsp_status_write(void *opaque, hwaddr addr,
                                     uint64_t value, unsigned size)
{
    qemu_log_mask(LOG_UNIMP,
                  "sdp1406.dsp-status: write 0x%" HWADDR_PRIx " = 0x%" PRIx64
                  "\n", addr, value);
}

static const MemoryRegionOps sdp1406_dsp_status_ops = {
    .read = sdp1406_dsp_status_read,
    .write = sdp1406_dsp_status_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void sdp1406_add_dsp_status(MemoryRegion *sysmem)
{
    MemoryRegion *mr;

    mr = g_new0(MemoryRegion, 1);
    memory_region_init_io(mr, NULL, &sdp1406_dsp_status_ops, NULL,
                          "sdp1406.dsp-status0", 0x10);
    memory_region_add_subregion_overlap(sysmem, 0x00a04010, mr, 1);

    mr = g_new0(MemoryRegion, 1);
    memory_region_init_io(mr, NULL, &sdp1406_dsp_status_ops, NULL,
                          "sdp1406.dsp-status1", 0x10);
    memory_region_add_subregion_overlap(sysmem, 0x00a08010, mr, 1);
}

/* Mali-400 MP4 GPU at 0x00600000-0x00613fff. The layout comes from the
 * static mali_gpu_resources table in mali.ko, which has no DTB node. The
 * probe checks each core's IRQ: it writes the core's raw-interrupt register
 * and waits for the line, then reads the status and clears it. This model
 * raises the core's SPI on that write and lowers it on the clear. The
 * register offsets are the utgard ones the probe uses. Rendering is not
 * modelled. The IRQ values below are Linux IRQ n minus 32 (the DTB cell and
 * gpio index), as for the DTB nodes; Linux IRQ n is GIC interrupt ID n. */
#define SDP1406_MALI_BASE 0x00600000ULL
#define SDP1406_MALI_SIZE 0x00014000ULL

typedef struct SDP1406MaliCore {
    hwaddr base;
    hwaddr raise;
    hwaddr clear;
    hwaddr status;
    qemu_irq irq;
    uint32_t raw;
} SDP1406MaliCore;

typedef struct SDP1406Mali {
    SDP1406MaliCore cores[10];
    uint32_t regs[SDP1406_MALI_SIZE / 4];
} SDP1406Mali;

/* GP: raise 0x24, clear 0x28, status 0x30. MMU: raise 0x14, clear 0x18,
 * status 0x20. PP: raise 0x1020, clear 0x1024, status 0x102c. */
#define SDP1406_MALI_GP(b)  { b, 0x24, 0x28, 0x30, NULL, 0 }
#define SDP1406_MALI_MMU(b) { b, 0x14, 0x18, 0x20, NULL, 0 }
#define SDP1406_MALI_PP(b)  { b, SDP1406_MALI_PP_RAW, 0x1024, 0x102c, NULL, 0 }

/* PP soft reset: the driver writes SOFT_RESET to the control register and
 * polls RAWSTAT for RESET_COMPLETED. */
#define SDP1406_MALI_PP_RAW         0x1020
#define SDP1406_MALI_PP_CTRL        0x100c
#define SDP1406_MALI_PP_SOFT_RESET  0x80
#define SDP1406_MALI_PP_RESET_DONE  (1u << 12)

/* GP soft reset: the driver writes GP_CMD_SOFT_RESET to the management
 * command register (0x20) and polls RAWSTAT (0x24) for RESET_COMPLETED. */
#define SDP1406_MALI_GP_CMD         0x20
#define SDP1406_MALI_GP_CMD_SOFT_RESET 0x400
#define SDP1406_MALI_GP_RESET_DONE  (1u << 19)

/* MMU COMMAND register (base + 0x08) value for HARD_RESET. */
#define SDP1406_MALI_MMU_HARD_RESET 6

/* Mali-400 product IDs in the upper half of the GP (0x6c) and PP (0x1000)
 * version registers. */
#define SDP1406_MALI_GP_VERSION     0x6c
#define SDP1406_MALI_PP_VERSION     0x1000
#define SDP1406_MALI_GP_ID          0x0b070000u
#define SDP1406_MALI_PP_ID          0xcd070000u /* Mali-400 MP (0xc807 is Mali-200) */

/* Per-core IRQ is the DTB-style cell (Linux IRQ - 32), as for the other devices. */
static const struct {
    SDP1406MaliCore core;
    int irq;
} sdp1406_mali_cores[] = {
    { SDP1406_MALI_GP(0x600000), 0x7e - 32 },
    { SDP1406_MALI_MMU(0x603000), 0x7d - 32 },
    { SDP1406_MALI_MMU(0x604000), 0x85 - 32 },
    { SDP1406_MALI_MMU(0x605000), 0x83 - 32 },
    { SDP1406_MALI_MMU(0x606000), 0x81 - 32 },
    { SDP1406_MALI_MMU(0x607000), 0x7f - 32 },
    { SDP1406_MALI_PP(0x608000), 0x86 - 32 },
    { SDP1406_MALI_PP(0x60a000), 0x84 - 32 },
    { SDP1406_MALI_PP(0x60c000), 0x82 - 32 },
    { SDP1406_MALI_PP(0x60e000), 0x80 - 32 },
};

static SDP1406MaliCore *sdp1406_mali_core_at(SDP1406Mali *s, hwaddr addr,
                                             hwaddr *reg)
{
    size_t i;

    for (i = 0; i < ARRAY_SIZE(s->cores); i++) {
        SDP1406MaliCore *c = &s->cores[i];

        if (addr == c->base + c->raise) {
            *reg = c->raise;
            return c;
        }
        if (addr == c->base + c->clear) {
            *reg = c->clear;
            return c;
        }
        if (addr == c->base + c->status) {
            *reg = c->status;
            return c;
        }
    }
    return NULL;
}

static uint64_t sdp1406_mali_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406Mali *s = opaque;
    hwaddr reg;
    SDP1406MaliCore *c = sdp1406_mali_core_at(s, addr + SDP1406_MALI_BASE, &reg);

    if (c && (reg == c->status || reg == c->raise)) {
        return c->raw;
    }
    return s->regs[addr / 4];
}

static void sdp1406_mali_write(void *opaque, hwaddr addr, uint64_t value,
                               unsigned size)
{
    SDP1406Mali *s = opaque;
    hwaddr reg;
    SDP1406MaliCore *c;

    /* MMU DTE_ADDR holds a 4 KiB aligned page directory; bits 11:0 read back as 0. */
    for (size_t i = 0; i < ARRAY_SIZE(s->cores); i++) {
        if (s->cores[i].raise == 0x14 &&
            addr + SDP1406_MALI_BASE == s->cores[i].base) {
            value &= ~0xfffULL;
        }
    }
    s->regs[addr / 4] = value;
    /* MMU HARD_RESET clears DTE_ADDR; the driver polls it back to 0. */
    for (size_t i = 0; i < ARRAY_SIZE(s->cores); i++) {
        if (s->cores[i].raise == 0x14 && value == SDP1406_MALI_MMU_HARD_RESET &&
            addr + SDP1406_MALI_BASE == s->cores[i].base + 0x08) {
            s->regs[(s->cores[i].base - SDP1406_MALI_BASE) / 4] = 0;
        }
    }
    addr += SDP1406_MALI_BASE;
    for (size_t i = 0; i < ARRAY_SIZE(s->cores); i++) {
        if (s->cores[i].raise == SDP1406_MALI_PP_RAW &&
            addr == s->cores[i].base + SDP1406_MALI_PP_CTRL &&
            (value & SDP1406_MALI_PP_SOFT_RESET)) {
            s->cores[i].raw = SDP1406_MALI_PP_RESET_DONE;
            qemu_set_irq(s->cores[i].irq, s->cores[i].raw != 0);
        }
        if (s->cores[i].base == SDP1406_MALI_BASE &&
            addr == SDP1406_MALI_BASE + SDP1406_MALI_GP_CMD &&
            value == SDP1406_MALI_GP_CMD_SOFT_RESET) {
            s->cores[i].raw |= SDP1406_MALI_GP_RESET_DONE;
            qemu_set_irq(s->cores[i].irq, s->cores[i].raw != 0);
        }
    }
    c = sdp1406_mali_core_at(s, addr, &reg);
    if (!c || reg == c->status) {
        return;
    }
    if (reg == c->raise) {
        c->raw |= value;
    } else {
        c->raw &= ~(uint32_t)value;
    }
    qemu_set_irq(c->irq, c->raw != 0);
}

static const MemoryRegionOps sdp1406_mali_ops = {
    .read = sdp1406_mali_read,
    .write = sdp1406_mali_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void sdp1406_add_mali(MemoryRegion *sysmem, DeviceState *gicdev)
{
    SDP1406Mali *s = g_new0(SDP1406Mali, 1);
    MemoryRegion *mr = g_new0(MemoryRegion, 1);
    size_t i;

    for (i = 0; i < ARRAY_SIZE(sdp1406_mali_cores); i++) {
        s->cores[i] = sdp1406_mali_cores[i].core;
        s->cores[i].irq =
            qdev_get_gpio_in(gicdev, sdp1406_mali_cores[i].irq);
        /* Identity registers the driver checks during probe. */
        if (s->cores[i].raise == SDP1406_MALI_PP_RAW) {
            s->regs[(s->cores[i].base - SDP1406_MALI_BASE +
                     SDP1406_MALI_PP_VERSION) / 4] = SDP1406_MALI_PP_ID;
        } else if (s->cores[i].base == SDP1406_MALI_BASE) {
            s->regs[SDP1406_MALI_GP_VERSION / 4] = SDP1406_MALI_GP_ID;
        }
    }
    memory_region_init_io(mr, NULL, &sdp1406_mali_ops, s, "sdp1406.mali",
                          SDP1406_MALI_SIZE);
    /* Overlays the "sdp1406.unnamed" stub, which covers this range. */
    memory_region_add_subregion_overlap(sysmem, SDP1406_MALI_BASE, mr, 1);
}

/* Mixer register file (DTB "mixer@005DC000", 0x4000 bytes). The named offsets
 * are the ones mixer_modeset_init() in sdp-drm.ko writes. The 0x20 value is
 * width << 16 | height of the main active area (the 4K branch writes
 * 0x0f000870, the 1080p branch 0x07800438, the 1366x768 branch 0x05560300).
 * The model stores every write and returns the last value, so the driver reads
 * back what it set. Unnamed offsets are logged (LOG_UNIMP) and stored. The
 * meaning of the other fields and the branch the board takes are open. */
#define SDP1406_MIXER_BASE 0x005DC000ULL
#define SDP1406_MIXER_SIZE 0x4000
#define SDP1406_MIXER_04   0x04
#define SDP1406_MIXER_10   0x10
#define SDP1406_MIXER_18   0x18
#define SDP1406_MIXER_1C   0x1c
#define SDP1406_MIXER_20   0x20
#define SDP1406_MIXER_24   0x24

typedef struct SDP1406Mixer {
    uint32_t regs[SDP1406_MIXER_SIZE / 4];
} SDP1406Mixer;

static bool sdp1406_mixer_named(hwaddr addr)
{
    switch (addr) {
    case SDP1406_MIXER_04:
    case SDP1406_MIXER_10:
    case SDP1406_MIXER_18:
    case SDP1406_MIXER_1C:
    case SDP1406_MIXER_20:
    case SDP1406_MIXER_24:
        return true;
    default:
        return false;
    }
}

static uint64_t sdp1406_mixer_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406Mixer *s = opaque;

    if (!sdp1406_mixer_named(addr)) {
        qemu_log_mask(LOG_UNIMP, "sdp1406.mixer: read 0x%" HWADDR_PRIx "\n",
                      addr);
    }
    return s->regs[addr >> 2];
}

static void sdp1406_mixer_write(void *opaque, hwaddr addr, uint64_t value,
                                unsigned size)
{
    SDP1406Mixer *s = opaque;

    if (!sdp1406_mixer_named(addr)) {
        qemu_log_mask(LOG_UNIMP,
                      "sdp1406.mixer: write 0x%" HWADDR_PRIx " = 0x%" PRIx64
                      "\n", addr, value);
    }
    s->regs[addr >> 2] = value;
}

static const MemoryRegionOps sdp1406_mixer_ops = {
    .read = sdp1406_mixer_read,
    .write = sdp1406_mixer_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static void sdp1406_add_mixer(MemoryRegion *sysmem)
{
    SDP1406Mixer *s = g_new0(SDP1406Mixer, 1);
    MemoryRegion *mr = g_new0(MemoryRegion, 1);

    memory_region_init_io(mr, NULL, &sdp1406_mixer_ops, s, "sdp1406.mixer",
                          SDP1406_MIXER_SIZE);
    memory_region_add_subregion(sysmem, SDP1406_MIXER_BASE, mr);
}

/* sdp-i2c controllers from the DTB: 16 channels in two groups of 8. Group 0
 * is at 0x00190100 (CH:0-7) and group 1 at 0x00191000 (CH:8-15), with 0x20
 * bytes per channel. Channel N raises SPI 40 + N. The driver writes STAT
 * (+0x04) with bit 0x20 (busy) to start a transfer, and each data byte
 * written to DS (+0x0c) is completed the same way. The controller clears busy
 * and sets the channel's bit in the group's shared IRQ status word when the
 * transfer finishes. The shared status (+0x14) and mask (+0x18) words sit in
 * the window of the group's last channel: 0x001901f4/0x001901f8 for CH:7 and
 * 0x001910f4/0x001910f8 for CH:15. The bit for channel N is N & 7. A set mask
 * bit masks the channel's line.
 *
 * The driver's transfer code reads the byte counter before it waits, so
 * completion must not happen during the start write: it is delayed by
 * SDP1406_I2C_XFER_NS of virtual time. STAT bit 0 is the last received bit,
 * 0 for ACK. The driver writes STAT with busy clear before the slave address,
 * and the first DS byte after that is the address. Every address is ACKed.
 * Addresses without a model return zero data, except the EEPROM on CH:4 and
 * the Sii9777 on CH:7 (see below). A read address queues one completion per
 * data byte, and the driver's clearing of CON ACK (0x100)
 * before its last byte queues the final one. The windows overlay the
 * sdp1406.periph stub.
 */
#define SDP1406_I2C_GROUP_BASE0  0x00190100ULL
#define SDP1406_I2C_GROUP_BASE1  0x00191000ULL
#define SDP1406_I2C_CHANNELS     8
#define SDP1406_I2C_SIZE         0x20
#define SDP1406_I2C_CON          0x00
#define SDP1406_I2C_STAT         0x04
#define SDP1406_I2C_DS           0x0c
#define SDP1406_I2C_IRQ_STAT     0x14
#define SDP1406_I2C_IRQ_MASK     0x18
#define SDP1406_I2C_STAT_BUSY    0x20
#define SDP1406_I2C_STAT_LRB     0x01
#define SDP1406_I2C_CON_ACK      0x100
#define SDP1406_I2C_IRQ_BASE     40
#define SDP1406_I2C_XFER_NS      100000

/* The Sii9777 HDMI switch sits on CH:7. The firmware's Sii9777 driver writes
 * the DS address byte 0x40 for it (read is 0x41), as the NACK path used to
 * match, and its errors name SlaveAddr 0x40. The EEPROM convention is the
 * same: the DS byte is the address with the R/W bit in bit 0. The DTB does not
 * list it, so it is not a DT child. Its registers read back what was written;
 * unwritten registers read 0. */
#define SDP1406_I2C_SII_CH       7
#define SDP1406_I2C_SII_ADDR     0x40
#define SDP1406_I2C_SII_SIZE     0x10000

/* The DTB puts an sii,s24c512c (64 KiB, 128-byte pages, 16-bit word address)
 * at 7-bit 0x50 on CH:4 (0x00190180). The guest's /dev/eeprom reads go through
 * this slave. Erased bytes read 0xff. The contents are synthetic: the MAC at
 * 0x1e04 is a locally administered address, because no EEPROM dump is
 * available. */
#define SDP1406_I2C_EEPROM_CH    4
#define SDP1406_I2C_EEPROM_ADDR  0xa0
#define SDP1406_I2C_EEPROM_SIZE  0x10000
#define SDP1406_I2C_EEPROM_PAGE  0x80
#define SDP1406_I2C_EEPROM_MAC   0x1e04
/* automation-helper reads this flag; 0x01 selects the factory production path. */
#define SDP1406_I2C_EEPROM_FLAG  0xf212
static const uint8_t sdp1406_i2c_eeprom_mac[6] = {
    0x02, 0x53, 0x44, 0x14, 0x06, 0x01,
};

typedef struct SDP1406I2cGroup {
    uint32_t irq_status;
    uint32_t irq_mask;
    uint8_t irq_latched[SDP1406_I2C_CHANNELS];
    qemu_irq irq[SDP1406_I2C_CHANNELS];
} SDP1406I2cGroup;

typedef struct SDP1406I2c {
    uint32_t regs[SDP1406_I2C_SIZE / 4];
    SDP1406I2cGroup *group;
    unsigned bit;
    bool owns_irq_regs;
    QEMUTimer *xfer_timer;
    bool addr_phase;
    bool rd_phase;
    bool rd_arm;
    bool data_pending;
    uint8_t xfer_queued;
    /* Set by a DS write that starts a transfer, until the START write or the
     * end of the queue. */
    bool xfer_from_ds;
    uint8_t *eeprom;
    bool eep_sel;
    bool eep_rd;
    unsigned eep_wr_bytes;
    uint16_t eep_ptr;
    uint8_t *sii;
    bool sii_sel;
    unsigned sii_wr_bytes;
    uint16_t sii_ptr;
} SDP1406I2c;

/* +0x18 is a mask, not an enable: the driver clears the channel bit when it
 * starts a transfer (bic) and sets it again when the transfer ends (orr). A
 * set bit masks the line, so the completion of a transfer started with the
 * bit clear must still raise its SPI. Treating the bit as an enable drops that
 * completion and the driver times out. */
static void sdp1406_i2c_update_irq(SDP1406I2cGroup *g)
{
    for (unsigned i = 0; i < SDP1406_I2C_CHANNELS; i++) {
        uint32_t bit = 1u << i;
        bool pending = g->irq_status & bit;
        bool masked = g->irq_mask & bit;

        qemu_set_irq(g->irq[i], pending && !masked);
    }
}

/* The shared words live in the group's last channel window only. */
static uint32_t *sdp1406_i2c_reg(SDP1406I2c *s, hwaddr reg)
{
    if (s->owns_irq_regs && reg == SDP1406_I2C_IRQ_STAT) {
        return &s->group->irq_status;
    }
    if (s->owns_irq_regs && reg == SDP1406_I2C_IRQ_MASK) {
        return &s->group->irq_mask;
    }
    return &s->regs[reg >> 2];
}

/* A transfer started while another is still pending is queued behind it, so
 * each transfer gets its own completion and IRQ. */
static void sdp1406_i2c_start_xfer(SDP1406I2c *s)
{
    if (timer_pending(s->xfer_timer)) {
        if (s->xfer_queued < UINT8_MAX) {
            s->xfer_queued++;
        }
        return;
    }
    timer_mod(s->xfer_timer,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + SDP1406_I2C_XFER_NS);
}

static void sdp1406_i2c_arm(SDP1406I2c *s)
{
    s->regs[SDP1406_I2C_STAT >> 2] |= SDP1406_I2C_STAT_BUSY;
    sdp1406_i2c_start_xfer(s);
}

static void sdp1406_i2c_eeprom_init(SDP1406I2c *s)
{
    s->eeprom = g_malloc(SDP1406_I2C_EEPROM_SIZE);
    memset(s->eeprom, 0xff, SDP1406_I2C_EEPROM_SIZE);
    memcpy(s->eeprom + SDP1406_I2C_EEPROM_MAC, sdp1406_i2c_eeprom_mac,
           sizeof(sdp1406_i2c_eeprom_mac));
    s->eeprom[SDP1406_I2C_EEPROM_FLAG] = 0x01;
}

/* The first two data bytes after a write address set the word address, high
 * byte first. Later bytes are stored, and the address wraps within its page. */
static void sdp1406_i2c_eeprom_write(SDP1406I2c *s, uint8_t byte)
{
    if (s->eep_wr_bytes == 0) {
        s->eep_ptr = (uint16_t)byte << 8;
    } else if (s->eep_wr_bytes == 1) {
        s->eep_ptr |= byte;
    } else {
        s->eeprom[s->eep_ptr] = byte;
        s->eep_ptr = (s->eep_ptr & ~(SDP1406_I2C_EEPROM_PAGE - 1)) |
                     ((s->eep_ptr + 1) & (SDP1406_I2C_EEPROM_PAGE - 1));
    }
    if (s->eep_wr_bytes < 2) {
        s->eep_wr_bytes++;
    }
}

/* The Sii9777 takes the same 16-bit sub-address as the EEPROM, but its
 * register file does not wrap within a page: the address just counts up. */
static void sdp1406_i2c_sii_write(SDP1406I2c *s, uint8_t byte)
{
    if (s->sii_wr_bytes == 0) {
        s->sii_ptr = (uint16_t)byte << 8;
    } else if (s->sii_wr_bytes == 1) {
        s->sii_ptr |= byte;
    } else {
        s->sii[s->sii_ptr++] = byte;
    }
    if (s->sii_wr_bytes < 2) {
        s->sii_wr_bytes++;
    }
}

/* A read returns the byte at the current address, which then advances. Only
 * one slave is selected per channel, so at most one of the two branches
 * applies. */
static uint32_t sdp1406_i2c_eeprom_read(SDP1406I2c *s)
{
    uint32_t val;

    if (s->sii_sel && s->eep_rd) {
        return s->sii[s->sii_ptr++];
    }
    if (!s->eeprom || !s->eep_sel || !s->eep_rd) {
        return 0;
    }
    val = s->eeprom[s->eep_ptr];
    s->eep_ptr = (s->eep_ptr + 1) & (SDP1406_I2C_EEPROM_SIZE - 1);
    return val;
}

/* A read data byte is clocked in on its own completion, which leaves DS
 * zero. The driver's IRQ handler may still be running when a completion
 * fires. Each such completion is queued per channel and raised again, one
 * per ack, when the driver acks the channel's bit. */
static void sdp1406_i2c_xfer_done(void *opaque)
{
    SDP1406I2c *s = opaque;

    if (s->data_pending) {
        s->data_pending = false;
        s->regs[SDP1406_I2C_DS >> 2] = sdp1406_i2c_eeprom_read(s);
    }
    if (s->xfer_queued) {
        /* The next queued transfer keeps BUSY and its own completion. */
        s->xfer_queued--;
        timer_mod(s->xfer_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + SDP1406_I2C_XFER_NS);
    } else {
        s->xfer_from_ds = false;
        s->regs[SDP1406_I2C_STAT >> 2] &= ~SDP1406_I2C_STAT_BUSY;
        if (s->rd_arm) {
            s->rd_arm = false;
            s->data_pending = true;
            sdp1406_i2c_arm(s);
        }
    }
    if (s->group->irq_status & (1u << s->bit)) {
        if (s->group->irq_latched[s->bit] < UINT8_MAX) {
            s->group->irq_latched[s->bit]++;
        }
    } else {
        s->group->irq_status |= 1u << s->bit;
    }
    sdp1406_i2c_update_irq(s->group);
}

static uint64_t sdp1406_i2c_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406I2c *s = opaque;
    uint32_t val = *sdp1406_i2c_reg(s, addr & ~3ULL);

    if ((addr & ~3ULL) == SDP1406_I2C_DS && s->rd_phase &&
        (s->regs[SDP1406_I2C_CON >> 2] & SDP1406_I2C_CON_ACK) &&
        !s->data_pending &&
        !(s->regs[SDP1406_I2C_STAT >> 2] & SDP1406_I2C_STAT_BUSY)) {
        s->data_pending = true;
        sdp1406_i2c_arm(s);
    }
    return extract32(val, (addr & 3) * 8, size * 8);
}

static void sdp1406_i2c_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size)
{
    SDP1406I2c *s = opaque;
    hwaddr reg = addr & ~3ULL;
    unsigned shift = (addr & 3) * 8;
    uint32_t mask = (size == 4) ? ~0u : ((1u << (size * 8)) - 1) << shift;
    bool addr_byte = false;
    uint32_t wval = ((uint32_t)value << shift) & mask;
    uint32_t *word = sdp1406_i2c_reg(s, reg);

    switch (reg) {
    case SDP1406_I2C_STAT:
        if (!(wval & SDP1406_I2C_STAT_BUSY)) {
            s->addr_phase = true;
        }
        *word = (*word & ~mask) | wval;
        if (*word & SDP1406_I2C_STAT_BUSY) {
            /* A DS write that already started this transfer is followed by
             * the START write for the same transfer, so the START is not a
             * second one. */
            if (s->xfer_from_ds) {
                s->xfer_from_ds = false;
            } else {
                sdp1406_i2c_start_xfer(s);
            }
        }
        break;
    case SDP1406_I2C_DS:
        *word = (*word & ~mask) | wval;
        addr_byte = s->addr_phase;
        if (s->addr_phase) {
            s->addr_phase = false;
            s->rd_phase = wval & 1;
            s->rd_arm = s->rd_phase;
            s->eep_sel = s->eeprom &&
                         (wval & 0xfe) == SDP1406_I2C_EEPROM_ADDR;
            s->eep_rd = s->rd_phase;
            s->eep_wr_bytes = 0;
            s->sii_sel = s->sii &&
                         (wval & 0xfe) == SDP1406_I2C_SII_ADDR;
            s->sii_wr_bytes = 0;
        } else {
            if (s->eep_sel && !s->eep_rd) {
                sdp1406_i2c_eeprom_write(s, wval & 0xff);
            }
            if (s->sii_sel && !s->eep_rd) {
                sdp1406_i2c_sii_write(s, wval & 0xff);
            }
        }
        if (addr_byte) {
            s->xfer_from_ds = true;
            sdp1406_i2c_arm(s);
        } else {
            /* A data byte written during a transfer merges into it. Only an
             * address write starts a transfer of its own. */
            s->regs[SDP1406_I2C_STAT >> 2] |= SDP1406_I2C_STAT_BUSY;
            timer_mod(s->xfer_timer,
                      qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      SDP1406_I2C_XFER_NS);
        }
        break;
    case SDP1406_I2C_IRQ_STAT:
        *word &= ~wval;
        {
            unsigned ch;

            for (ch = 0; ch < SDP1406_I2C_CHANNELS; ch++) {
                uint32_t bit = 1u << ch;

                if ((wval & bit) && !(s->group->irq_status & bit) &&
                    s->group->irq_latched[ch]) {
                    s->group->irq_latched[ch]--;
                    s->group->irq_status |= bit;
                }
            }
        }
        sdp1406_i2c_update_irq(s->group);
        break;
    case SDP1406_I2C_CON:
        /* Clearing the ACK bit makes the driver NAK the last byte it reads.
         * The final byte then completes like any other. */
        if (s->rd_phase && (*word & SDP1406_I2C_CON_ACK) &&
            !(wval & SDP1406_I2C_CON_ACK) &&
            !(s->regs[SDP1406_I2C_STAT >> 2] & SDP1406_I2C_STAT_BUSY)) {
            s->data_pending = true;
            sdp1406_i2c_arm(s);
        }
        *word = (*word & ~mask) | wval;
        break;
    case SDP1406_I2C_IRQ_MASK:
        *word = (*word & ~mask) | wval;
        sdp1406_i2c_update_irq(s->group);
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      "sdp1406.i2c: write 0x%" HWADDR_PRIx " = 0x%" PRIx64
                      "\n", addr, value);
        *word = (*word & ~mask) | wval;
        break;
    }
}

static const MemoryRegionOps sdp1406_i2c_ops = {
    .read = sdp1406_i2c_read,
    .write = sdp1406_i2c_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = {
        .min_access_size = 1,
        .max_access_size = 4,
    },
    .impl = {
        .min_access_size = 1,
        .max_access_size = 4,
    },
};

static void sdp1406_add_i2c_group(MemoryRegion *sysmem, DeviceState *gicdev,
                                  hwaddr base, unsigned first_ch)
{
    SDP1406I2cGroup *g = g_new0(SDP1406I2cGroup, 1);

    for (unsigned i = 0; i < SDP1406_I2C_CHANNELS; i++) {
        g->irq[i] = qdev_get_gpio_in(gicdev,
                                     SDP1406_I2C_IRQ_BASE + first_ch + i);
    }
    for (unsigned i = 0; i < SDP1406_I2C_CHANNELS; i++) {
        unsigned ch = first_ch + i;
        SDP1406I2c *s = g_new0(SDP1406I2c, 1);
        MemoryRegion *mr = g_new0(MemoryRegion, 1);
        char name[32];

        s->group = g;
        s->bit = i;
        s->owns_irq_regs = (i == SDP1406_I2C_CHANNELS - 1);
        if (ch == SDP1406_I2C_EEPROM_CH) {
            sdp1406_i2c_eeprom_init(s);
        }
        if (ch == SDP1406_I2C_SII_CH) {
            s->sii = g_malloc0(SDP1406_I2C_SII_SIZE);
        }
        s->xfer_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                     sdp1406_i2c_xfer_done, s);
        snprintf(name, sizeof(name), "sdp1406.i2c-ch%u", ch);
        memory_region_init_io(mr, NULL, &sdp1406_i2c_ops, s, name,
                              SDP1406_I2C_SIZE);
        memory_region_add_subregion_overlap(sysmem,
                                            base + i * SDP1406_I2C_SIZE,
                                            mr, 1);
    }
}

static void sdp1406_add_i2c(MemoryRegion *sysmem, DeviceState *gicdev)
{
    sdp1406_add_i2c_group(sysmem, gicdev, SDP1406_I2C_GROUP_BASE0, 0);
    sdp1406_add_i2c_group(sysmem, gicdev, SDP1406_I2C_GROUP_BASE1,
                          SDP1406_I2C_CHANNELS);
}

/* Gmac model: DesignWare MAC and DMA on a NIC. The sdp-gmac driver uses the
 * MII registers: the address register at 0x10 (PA [15:11], GR [10:6], GW bit
 * 1, GB busy bit 0) and the data register at 0x14. One PHY sits at address 1.
 * Its ID is 0xabcd0000 (the probe kernel's "SDP-EPHY" driver entry, mask
 * 0xfffffff0). BMSR 0x782d is the standard 10/100 capability set. Its link
 * and autoneg bits follow the netdev link state. Absent addresses read
 * 0xffff, as on an empty MDIO bus. The version register at 0x20 reads 0x37:
 * sdpGmac_reset() accepts low bytes 0x34, 0x36 and 0x37.
 *
 * DMA rings use the standard DesignWare layout. Descriptors are 16 bytes
 * (TDES/RDES 0-3) plus DSL words from bus mode bits 6:2. The production
 * driver writes six words per descriptor and sets DSL 2 (bus mode 0x10808),
 * so the stride is 24 bytes. Word 1 holds the buffer size in bits 12:0 and
 * RER in bit 15. Frames are single buffer, and a frame longer than its buffer
 * is truncated. The TX ring is
 * walked on a poll demand, and RX is delivered through the NIC receive
 * callback. The gmac interrupt is DTB cell 5 (SPI 5, GIC IRQ 37).
 *
 * MAC_Address0 (0x040 high, 0x044 low) holds the NIC address, which is read
 * at probe. The high word reads with AE (bit 31) set, as DesignWare does. */
#define TYPE_SDP1406_GMAC     "sdp1406.gmac"
#define SDP1406_GMAC_BASE      0x00120000ULL
#define SDP1406_GMAC_SIZE      0x4000
#define SDP1406_GMAC_IRQ       5
#define SDP1406_GMAC_MAC_CONFIG    0x000
#define SDP1406_GMAC_MAC_INTR_MASK 0x03c
#define SDP1406_GMAC_MAC_ADDR_HI   0x040
#define SDP1406_GMAC_MAC_ADDR_LO   0x044
#define SDP1406_GMAC_MAC_ADDR_AE   0x80000000u
#define SDP1406_GMAC_MII_ADDR  0x10
#define SDP1406_GMAC_MII_DATA  0x14
#define SDP1406_GMAC_VERSION   0x20
#define SDP1406_GMAC_VER_VALUE 0x37
#define SDP1406_GMAC_DMA_BUS_MODE 0x1000
#define SDP1406_GMAC_DMA_TX_POLL  0x1004
#define SDP1406_GMAC_DMA_RX_POLL  0x1008
#define SDP1406_GMAC_DMA_RX_BASE  0x100c
#define SDP1406_GMAC_DMA_TX_BASE  0x1010
#define SDP1406_GMAC_DMA_STATUS   0x1014
#define SDP1406_GMAC_DMA_OP_MODE  0x1018
#define SDP1406_GMAC_DMA_INT_EN   0x101c
#define SDP1406_GMAC_DMA_SWR      0x1
#define SDP1406_GMAC_DMA_OP_SR    0x2
#define SDP1406_GMAC_DMA_OP_ST    0x2000
#define SDP1406_GMAC_ST_TI        0x1
#define SDP1406_GMAC_ST_RI        0x40
#define SDP1406_GMAC_DES_OWN      0x80000000u
#define SDP1406_GMAC_TDES0_IC     0x40000000u
#define SDP1406_GMAC_TDES0_TER    0x00200000u
#define SDP1406_GMAC_RDES0_FS     0x200
#define SDP1406_GMAC_RDES0_LS     0x100
#define SDP1406_GMAC_RDES1_RER    0x8000
#define SDP1406_GMAC_DES_LEN_MASK 0x1fff
#define SDP1406_GMAC_MII_BUSY  0x1
#define SDP1406_GMAC_MII_WRITE 0x2
#define SDP1406_GMAC_PHY_ADDR  1
/* BMSR link status (bit 2) and autoneg complete (bit 5). */
#define SDP1406_GMAC_BMSR_LINK 0x0024
/* Upper bound on one poll, so a ring that never releases OWN cannot hang. */
#define SDP1406_GMAC_DESC_MAX  1024

typedef struct SDP1406Gmac {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq irq;
    NICConf conf;
    NICState *nic;
    bool link_up;
    uint32_t mac_config;
    uint32_t mac_intr_mask;
    uint32_t mii_addr;
    uint32_t mii_data;
    uint16_t phy_regs[32];
    uint32_t bus_mode;
    uint32_t tx_base;
    uint32_t rx_base;
    uint32_t tx_cur;
    uint32_t rx_cur;
    uint32_t status;
    uint32_t op_mode;
    uint32_t int_enable;
} SDP1406Gmac;

OBJECT_DECLARE_SIMPLE_TYPE(SDP1406Gmac, SDP1406_GMAC)

static void sdp1406_gmac_tx_poll(SDP1406Gmac *s);
static void sdp1406_gmac_rx_flush(SDP1406Gmac *s);
static void sdp1406_gmac_update_irq(SDP1406Gmac *s);

static void sdp1406_gmac_phy_reset(SDP1406Gmac *s)
{
    memset(s->phy_regs, 0, sizeof(s->phy_regs));
    s->phy_regs[2] = 0xabcd;
    s->phy_regs[3] = 0x0000;
    s->phy_regs[1] = 0x782d;
}

static uint16_t sdp1406_gmac_phy_read(SDP1406Gmac *s, unsigned gr)
{
    uint16_t v = s->phy_regs[gr];

    if (gr == 1 && !s->link_up) {
        v &= ~SDP1406_GMAC_BMSR_LINK;
    }
    return v;
}

static uint64_t sdp1406_gmac_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406Gmac *s = opaque;

    switch (addr) {
    case SDP1406_GMAC_MII_ADDR:
        return s->mii_addr;
    case SDP1406_GMAC_MII_DATA:
        return s->mii_data;
    case SDP1406_GMAC_MAC_CONFIG:
        return s->mac_config;
    case SDP1406_GMAC_MAC_INTR_MASK:
        return s->mac_intr_mask;
    case SDP1406_GMAC_MAC_ADDR_HI:
        return SDP1406_GMAC_MAC_ADDR_AE | s->conf.macaddr.a[4] |
               (s->conf.macaddr.a[5] << 8);
    case SDP1406_GMAC_MAC_ADDR_LO:
        return s->conf.macaddr.a[0] | (s->conf.macaddr.a[1] << 8) |
               (s->conf.macaddr.a[2] << 16) |
               ((uint32_t)s->conf.macaddr.a[3] << 24);
    case SDP1406_GMAC_DMA_BUS_MODE:
        return s->bus_mode;
    case SDP1406_GMAC_DMA_RX_BASE:
        return s->rx_base;
    case SDP1406_GMAC_DMA_TX_BASE:
        return s->tx_base;
    case SDP1406_GMAC_DMA_STATUS:
        return s->status;
    case SDP1406_GMAC_DMA_OP_MODE:
        return s->op_mode;
    case SDP1406_GMAC_DMA_INT_EN:
        return s->int_enable;
    case SDP1406_GMAC_VERSION:
        return SDP1406_GMAC_VER_VALUE;
    default:
        qemu_log_mask(LOG_UNIMP, "sdp1406.gmac: read 0x%" HWADDR_PRIx
                      " (%u bytes)\n", addr, size);
        return 0;
    }
}

static void sdp1406_gmac_write(void *opaque, hwaddr addr, uint64_t value,
                               unsigned size)
{
    SDP1406Gmac *s = opaque;
    unsigned pa, gr;

    switch (addr) {
    case SDP1406_GMAC_MII_ADDR:
        pa = (value >> 11) & 0x1f;
        gr = (value >> 6) & 0x1f;
        s->mii_addr = value & ~SDP1406_GMAC_MII_BUSY;
        if (value & SDP1406_GMAC_MII_BUSY) {
            if (pa == SDP1406_GMAC_PHY_ADDR) {
                if (value & SDP1406_GMAC_MII_WRITE) {
                    /* BMSR and the PHY ID (regs 1-3) are read-only. */
                    if (gr != 1 && gr != 2 && gr != 3) {
                        s->phy_regs[gr] = s->mii_data;
                    }
                } else {
                    s->mii_data = sdp1406_gmac_phy_read(s, gr);
                }
            } else if (!(value & SDP1406_GMAC_MII_WRITE)) {
                s->mii_data = 0xffff;
            }
        }
        break;
    case SDP1406_GMAC_MAC_CONFIG:
        s->mac_config = value;
        break;
    case SDP1406_GMAC_MAC_INTR_MASK:
        s->mac_intr_mask = value;
        break;
    case SDP1406_GMAC_MAC_ADDR_HI:
        s->conf.macaddr.a[4] = value & 0xff;
        s->conf.macaddr.a[5] = (value >> 8) & 0xff;
        break;
    case SDP1406_GMAC_MAC_ADDR_LO:
        s->conf.macaddr.a[0] = value & 0xff;
        s->conf.macaddr.a[1] = (value >> 8) & 0xff;
        s->conf.macaddr.a[2] = (value >> 16) & 0xff;
        s->conf.macaddr.a[3] = (value >> 24) & 0xff;
        break;
    case SDP1406_GMAC_DMA_BUS_MODE:
        if (value & SDP1406_GMAC_DMA_SWR) {
            /* The software reset self-clears at once, so the driver's poll
             * on the bit ends on its first read. */
            s->status = 0;
            s->op_mode = 0;
            s->int_enable = 0;
            s->tx_base = s->rx_base = s->tx_cur = s->rx_cur = 0;
            sdp1406_gmac_update_irq(s);
        }
        s->bus_mode = value & ~SDP1406_GMAC_DMA_SWR;
        break;
    case SDP1406_GMAC_DMA_TX_POLL:
        if (s->op_mode & SDP1406_GMAC_DMA_OP_ST) {
            sdp1406_gmac_tx_poll(s);
        }
        break;
    case SDP1406_GMAC_DMA_RX_POLL:
        sdp1406_gmac_rx_flush(s);
        break;
    case SDP1406_GMAC_DMA_RX_BASE:
        s->rx_base = s->rx_cur = value;
        break;
    case SDP1406_GMAC_DMA_TX_BASE:
        s->tx_base = s->tx_cur = value;
        break;
    case SDP1406_GMAC_DMA_STATUS:
        /* Write-1-to-clear. */
        s->status &= ~value;
        sdp1406_gmac_update_irq(s);
        break;
    case SDP1406_GMAC_DMA_OP_MODE:
        s->op_mode = value;
        if (value & SDP1406_GMAC_DMA_OP_SR) {
            sdp1406_gmac_rx_flush(s);
        }
        break;
    case SDP1406_GMAC_DMA_INT_EN:
        s->int_enable = value;
        sdp1406_gmac_update_irq(s);
        break;
    case SDP1406_GMAC_MII_DATA:
        s->mii_data = value;
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "sdp1406.gmac: write 0x%" HWADDR_PRIx
                      " = 0x%" PRIx64 " (%u bytes)\n", addr, value, size);
        break;
    }
}

static const MemoryRegionOps sdp1406_gmac_ops = {
    .read = sdp1406_gmac_read,
    .write = sdp1406_gmac_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static uint32_t sdp1406_gmac_desc_stride(SDP1406Gmac *s)
{
    /* DSL (bus mode bits 6:2) counts the extra 32-bit words between
     * descriptors, beyond the 16-byte TDES/RDES block. */
    return 16 + 4 * ((s->bus_mode >> 2) & 0x1f);
}

static uint32_t sdp1406_gmac_ld32(hwaddr addr)
{
    uint32_t v = 0;

    dma_memory_read(&address_space_memory, addr, &v, sizeof(v),
                    MEMTXATTRS_UNSPECIFIED);
    return le32_to_cpu(v);
}

static void sdp1406_gmac_st32(hwaddr addr, uint32_t v)
{
    v = cpu_to_le32(v);
    dma_memory_write(&address_space_memory, addr, &v, sizeof(v),
                     MEMTXATTRS_UNSPECIFIED);
}

static void sdp1406_gmac_update_irq(SDP1406Gmac *s)
{
    qemu_set_irq(s->irq, (s->status & s->int_enable) != 0);
}

static void sdp1406_gmac_tx_poll(SDP1406Gmac *s)
{
    uint32_t stride = sdp1406_gmac_desc_stride(s);
    unsigned i;

    for (i = 0; i < SDP1406_GMAC_DESC_MAX; i++) {
        hwaddr desc = s->tx_cur;
        uint32_t des0 = sdp1406_gmac_ld32(desc);
        uint32_t len = sdp1406_gmac_ld32(desc + 4) & SDP1406_GMAC_DES_LEN_MASK;
        hwaddr buf = sdp1406_gmac_ld32(desc + 8);

        if (!(des0 & SDP1406_GMAC_DES_OWN)) {
            break;
        }
        if (len) {
            uint8_t *frame = g_malloc(len);

            dma_memory_read(&address_space_memory, buf, frame, len,
                            MEMTXATTRS_UNSPECIFIED);
            qemu_send_packet(qemu_get_queue(s->nic), frame, len);
            g_free(frame);
        }
        des0 &= ~SDP1406_GMAC_DES_OWN;
        sdp1406_gmac_st32(desc, des0);
        if (des0 & SDP1406_GMAC_TDES0_IC) {
            s->status |= SDP1406_GMAC_ST_TI;
        }
        if (des0 & SDP1406_GMAC_TDES0_TER) {
            s->tx_cur = s->tx_base;
        } else {
            s->tx_cur += stride;
        }
    }
    sdp1406_gmac_update_irq(s);
}

static bool sdp1406_gmac_rx_ready(SDP1406Gmac *s)
{
    return (s->op_mode & SDP1406_GMAC_DMA_OP_SR) && s->rx_base &&
           (sdp1406_gmac_ld32(s->rx_cur) & SDP1406_GMAC_DES_OWN);
}

static void sdp1406_gmac_rx_flush(SDP1406Gmac *s)
{
    qemu_flush_queued_packets(qemu_get_queue(s->nic));
}

static bool sdp1406_gmac_can_receive(NetClientState *nc)
{
    return sdp1406_gmac_rx_ready(qemu_get_nic_opaque(nc));
}

static ssize_t sdp1406_gmac_receive(NetClientState *nc, const uint8_t *buf,
                                    size_t size)
{
    SDP1406Gmac *s = qemu_get_nic_opaque(nc);
    hwaddr desc = s->rx_cur;
    uint32_t des1;
    hwaddr buf_addr;
    size_t n;

    if (!sdp1406_gmac_rx_ready(s)) {
        return 0;
    }
    des1 = sdp1406_gmac_ld32(desc + 4);
    buf_addr = sdp1406_gmac_ld32(desc + 8);
    n = MIN(size, des1 & SDP1406_GMAC_DES_LEN_MASK);

    dma_memory_write(&address_space_memory, buf_addr, buf, n,
                     MEMTXATTRS_UNSPECIFIED);
    /* Hand the descriptor back: OWN clear, frame length in bits 29:16. */
    sdp1406_gmac_st32(desc, ((uint32_t)n << 16) |
                            SDP1406_GMAC_RDES0_FS | SDP1406_GMAC_RDES0_LS);
    s->status |= SDP1406_GMAC_ST_RI;
    if (des1 & SDP1406_GMAC_RDES1_RER) {
        s->rx_cur = s->rx_base;
    } else {
        s->rx_cur += sdp1406_gmac_desc_stride(s);
    }
    sdp1406_gmac_update_irq(s);
    return size;
}

static void sdp1406_gmac_set_link(NetClientState *nc)
{
    SDP1406Gmac *s = qemu_get_nic_opaque(nc);

    s->link_up = !nc->link_down;
}

static NetClientInfo net_sdp1406_gmac_info = {
    .type = NET_CLIENT_DRIVER_NIC,
    .size = sizeof(NICState),
    .can_receive = sdp1406_gmac_can_receive,
    .receive = sdp1406_gmac_receive,
    .link_status_changed = sdp1406_gmac_set_link,
};

static void sdp1406_gmac_reset(DeviceState *dev)
{
    SDP1406Gmac *s = SDP1406_GMAC(dev);

    s->mac_config = 0;
    s->mac_intr_mask = 0;
    s->mii_addr = 0;
    s->mii_data = 0;
    s->bus_mode = 0;
    s->tx_base = s->rx_base = s->tx_cur = s->rx_cur = 0;
    s->status = 0;
    s->op_mode = 0;
    s->int_enable = 0;
    sdp1406_gmac_phy_reset(s);
    s->link_up = true;
    if (s->nic) {
        s->link_up = !qemu_get_queue(s->nic)->link_down;
    }
    sdp1406_gmac_update_irq(s);
}

static void sdp1406_gmac_realize(DeviceState *dev, Error **errp)
{
    SDP1406Gmac *s = SDP1406_GMAC(dev);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);

    memory_region_init_io(&s->mmio, OBJECT(dev), &sdp1406_gmac_ops, s,
                          "sdp1406.gmac", SDP1406_GMAC_SIZE);
    sysbus_init_mmio(sbd, &s->mmio);
    sysbus_init_irq(sbd, &s->irq);

    qemu_macaddr_default_if_unset(&s->conf.macaddr);
    s->nic = qemu_new_nic(&net_sdp1406_gmac_info, &s->conf,
                          TYPE_SDP1406_GMAC, dev->id,
                          &dev->mem_reentrancy_guard, s);
    qemu_format_nic_info_str(qemu_get_queue(s->nic), s->conf.macaddr.a);
}

static void sdp1406_gmac_unrealize(DeviceState *dev)
{
    SDP1406Gmac *s = SDP1406_GMAC(dev);

    qemu_del_nic(s->nic);
}

static const Property sdp1406_gmac_properties[] = {
    DEFINE_NIC_PROPERTIES(SDP1406Gmac, conf),
};

static void sdp1406_gmac_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    set_bit(DEVICE_CATEGORY_NETWORK, dc->categories);
    dc->desc = "SDP1406 gmac (DesignWare MAC and DMA)";
    dc->realize = sdp1406_gmac_realize;
    dc->unrealize = sdp1406_gmac_unrealize;
    device_class_set_legacy_reset(dc, sdp1406_gmac_reset);
    device_class_set_props(dc, sdp1406_gmac_properties);
}

static const TypeInfo sdp1406_gmac_info = {
    .name = TYPE_SDP1406_GMAC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(SDP1406Gmac),
    .class_init = sdp1406_gmac_class_init,
};

static void sdp1406_gmac_register_types(void)
{
    type_register_static(&sdp1406_gmac_info);
}

type_init(sdp1406_gmac_register_types);

static void sdp1406_add_gmac(DeviceState *gicdev)
{
    DeviceState *dev = qdev_new(TYPE_SDP1406_GMAC);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);

    qemu_configure_nic_device(dev, true, NULL);
    sysbus_realize_and_unref(sbd, &error_fatal);
    sysbus_mmio_map(sbd, 0, SDP1406_GMAC_BASE);
    sysbus_connect_irq(sbd, 0, qdev_get_gpio_in(gicdev, SDP1406_GMAC_IRQ));
}

/* Messagebox model. A write to the send register (0x480) fills the RX record
 * with the reply frame for the request in the TX record (see
 * sdp1406_mbox_reply) and raises the reply SPI.
 * The handler reads the pending status at 0x440 and clears bits by writing
 * 0x444; the SPI follows the pending bits. The TX busy flag at 0x4c0 is set by
 * the kick write and cleared by the ack. */
#define SDP1406_MBOX_STATUS 0x440
#define SDP1406_MBOX_CLEAR  0x444
#define SDP1406_MBOX_SEND   0x480
#define SDP1406_MBOX_READY  0x590
#define SDP1406_MBOX_TX_BUSY 0x4c0
#define SDP1406_MBOX_REPLY  0x1
/* RX record: length at 0x400, 9-byte frame at 0x404. The frame is
 * `ff ff TAG 00 00 00 00 00 TAG`: the command byte is at wire byte 2, and the
 * checksum (sum of bytes 2..7) equals TAG. The waiter for each request matches
 * the TAG it expects. The TX frame (0x484) carries the request command in its
 * byte 2. Requests 0x18 expect 0x29; 0x60 expects 0x50 and 0x61 expects 0x51.
 * Other commands get the 0x29 default, which is verified only for 0x18. */
#define SDP1406_MBOX_RX_LEN  0x400
#define SDP1406_MBOX_RX_DATA 0x404
#define SDP1406_MBOX_RX_TAG  0x29
#define SDP1406_MBOX_TX      0x484
#define SDP1406_MBOX_CMD_60  0x60
#define SDP1406_MBOX_CMD_61  0x61
#define SDP1406_MBOX_TAG_60  0x50
#define SDP1406_MBOX_TAG_61  0x51

typedef struct SDP1406Mbox {
    qemu_irq irq;
    uint32_t pending;
    uint32_t busy;
    uint32_t regs[0x1000 / 4];
} SDP1406Mbox;

/* ACK tag for a request, as the production driver matches it (wire byte 2,
 * see TODO.md). Only commands verified against the driver are listed. */
static uint8_t sdp1406_mbox_ack_for(uint8_t cmd)
{
    switch (cmd) {
    case 0x10: return 0x20;  /* POWERON_MODE, sdp_micom_send_cmd_ack caller 0xc0259200 */
    case 0x18: return SDP1406_MBOX_RX_TAG;  /* device 3 registration, caller 0xc0259db0 */
    case 0x19: return 0x2a;  /* caller 0xc0258ef8 (0xc0258e6c) */
    case 0x1a: return 0x2b;  /* caller 0xc0258ef8 (0xc0258e38) */
    /* Expect an echo of the command (the send passes ack == cmd). Callers:
     * 0x6e from 0xc026653c, 0x6a from a module (0xbeae8ebc). */
    case 0x6a: return 0x6a;
    case 0x6e: return 0x6e;
    /* jackident_thread in tztv-jackident.ko calls sdp_micom_send_cmd_ack
     * with cmd == ack == 0xa0 (module offsets 0xeb8-0xee0). */
    case 0xa0: return 0xa0;
    /* SW_INVERTER from tztv_frc_tcon.ko. The RX completion check at
     * sdp_micom dispatch 0xc0265e34 needs the reply tag to equal the pending
     * command (ctx+20), so the reply must echo 0xd1. */
    case 0xd1: return 0xd1;
    case SDP1406_MBOX_CMD_60: return SDP1406_MBOX_TAG_60;
    case SDP1406_MBOX_CMD_61: return SDP1406_MBOX_TAG_61;
    default: return SDP1406_MBOX_RX_TAG;
    }
}

static void sdp1406_mbox_reply(SDP1406Mbox *s)
{
    uint32_t cmd = (s->regs[SDP1406_MBOX_TX / 4] >> 16) & 0xff;
    uint8_t tag = sdp1406_mbox_ack_for(cmd);
    uint8_t frame[9] = {0xff, 0xff, 0, 0, 0, 0, 0, 0, 0};
    uint32_t words[3] = {0, 0, 0};
    int i;

    frame[2] = tag;
    frame[8] = tag;
    for (i = 0; i < sizeof(frame); i++) {
        words[i / 4] |= (uint32_t)frame[i] << (8 * (i % 4));
    }
    s->regs[SDP1406_MBOX_RX_LEN / 4] = sizeof(frame);
    for (i = 0; i < 3; i++) {
        s->regs[SDP1406_MBOX_RX_DATA / 4 + i] = words[i];
    }
}

static uint64_t sdp1406_mbox_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406Mbox *s = opaque;

    if (addr == SDP1406_MBOX_TX_BUSY) {
        return s->busy;
    }
    if (addr == SDP1406_MBOX_STATUS) {
        return s->pending;
    }
    if (addr == SDP1406_MBOX_READY) {
        return s->pending ? 1 : 0;
    }
    return s->regs[addr / 4];
}

static void sdp1406_mbox_write(void *opaque, hwaddr addr, uint64_t value,
                               unsigned size)
{
    SDP1406Mbox *s = opaque;

    s->regs[addr / 4] = value;
    if (addr == SDP1406_MBOX_SEND) {
        sdp1406_mbox_reply(s);
        s->pending |= SDP1406_MBOX_REPLY;
    } else if (addr == SDP1406_MBOX_CLEAR) {
        s->pending &= ~(uint32_t)value;
        s->busy = 0;
    } else if (addr == SDP1406_MBOX_TX_BUSY) {
        s->busy = value & 1;
        return;
    } else {
        return;
    }
    qemu_set_irq(s->irq, s->pending != 0);
}

static const MemoryRegionOps sdp1406_mbox_ops = {
    .read = sdp1406_mbox_read,
    .write = sdp1406_mbox_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void sdp1406_add_mbox(MemoryRegion *sysmem, DeviceState *gicdev)
{
    SDP1406Mbox *s = g_new0(SDP1406Mbox, 1);
    MemoryRegion *mr = g_new0(MemoryRegion, 1);

    s->irq = qdev_get_gpio_in(gicdev, SDP1406_MBOX_IRQ);
    memory_region_init_io(mr, NULL, &sdp1406_mbox_ops, s, "sdp1406.messagebox",
                          0x1000);
    memory_region_add_subregion(sysmem, SDP1406_MBOX_BASE, mr);
}

/* DTB "ga@00660000" (samsung,sdp1406-ga, graphics accelerator, 0xca00 bytes),
 * interrupts = <0 104 0>, i.e. cell 104 (GIC IRQ 136). The driver (ga_irq_handler and
 * wait_till_ga_complete in sdp-drm.ko) writes 1 to +0x00 to start an operation,
 * then sleeps until the IRQ handler clears its done flag. Completion sets
 * status bit 0 at +0x0c. That register is write-1-to-clear, and the handler
 * reads and writes it back. +0x10 is the IRQ mask (1 enables; 0 is used in the
 * TrustZone path). The line is status & mask. Operations complete at once. */
#define SDP1406_GA_BASE   0x00660000ULL
#define SDP1406_GA_SIZE   0x0000ca00ULL
#define SDP1406_GA_IRQ    104
#define SDP1406_GA_START  0x00
#define SDP1406_GA_STATUS 0x0c
#define SDP1406_GA_MASK   0x10
#define SDP1406_GA_DONE   0x1

typedef struct SDP1406Ga {
    qemu_irq irq;
    uint32_t status;
    uint32_t mask;
    uint32_t regs[SDP1406_GA_SIZE / 4];
} SDP1406Ga;

static void sdp1406_ga_update_irq(SDP1406Ga *s)
{
    qemu_set_irq(s->irq, (s->status & s->mask) != 0);
}

static uint64_t sdp1406_ga_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406Ga *s = opaque;

    if (addr == SDP1406_GA_STATUS) {
        return s->status;
    }
    if (addr == SDP1406_GA_MASK) {
        return s->mask;
    }
    return s->regs[addr / 4];
}

static void sdp1406_ga_write(void *opaque, hwaddr addr, uint64_t value,
                             unsigned size)
{
    SDP1406Ga *s = opaque;

    if (addr == SDP1406_GA_START) {
        if (value & 1) {
            s->status |= SDP1406_GA_DONE;
        }
    } else if (addr == SDP1406_GA_STATUS) {
        s->status &= ~(uint32_t)value;
    } else if (addr == SDP1406_GA_MASK) {
        s->mask = value;
    } else {
        s->regs[addr / 4] = value;
        return;
    }
    sdp1406_ga_update_irq(s);
}

static const MemoryRegionOps sdp1406_ga_ops = {
    .read = sdp1406_ga_read,
    .write = sdp1406_ga_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void sdp1406_add_ga(MemoryRegion *sysmem, DeviceState *gicdev)
{
    SDP1406Ga *s = g_new0(SDP1406Ga, 1);
    MemoryRegion *mr = g_new0(MemoryRegion, 1);

    s->irq = qdev_get_gpio_in(gicdev, SDP1406_GA_IRQ);
    memory_region_init_io(mr, NULL, &sdp1406_ga_ops, s, "sdp1406.ga",
                          SDP1406_GA_SIZE);
    memory_region_add_subregion(sysmem, SDP1406_GA_BASE, mr);
}

/* DTB "unzip@00300000" (size 0x200): the DMA gzip/DEFLATE engine that
 * sdp_unzip.c drives. Register offsets follow the driver. CMD bit 0 starts a
 * decode that completes at once. The engine reads IN_BUF_SIZE bytes at
 * IN_BUF_ADDR, inflates them (gzip or raw DEFLATE), and writes each output
 * page of OUT_BUF_SIZE bytes to OUT_BUF_ADDR and then to the ADDR_LIST1 entries.
 * ISIZE holds the output byte count. IRQ bit 0 is done and bit 3 is error, with
 * the code in ERR_CODE. IRQ is write-1-to-clear and drives SPI 2 (GIC IRQ 34)
 * while status & mask is non-zero. The driver lists only npages output pages,
 * so the engine cannot see the list length; writes past the list are not
 * detected. */
#define SDP1406_UNZIP_BASE       0x00300000ULL
#define SDP1406_UNZIP_SIZE       0x200
#define SDP1406_UNZIP_IRQ        2
#define SDP1406_UNZIP_R_IRQ      0x00
#define SDP1406_UNZIP_R_IRQ_MASK 0x04
#define SDP1406_UNZIP_R_CMD      0x08
#define SDP1406_UNZIP_R_IN_ADDR  0x0c
#define SDP1406_UNZIP_R_IN_SIZE  0x10
#define SDP1406_UNZIP_R_OUT_ADDR 0x18
#define SDP1406_UNZIP_R_OUT_SIZE 0x1c
#define SDP1406_UNZIP_R_ERR      0x3c
#define SDP1406_UNZIP_R_ISIZE    0x50
#define SDP1406_UNZIP_R_LIST1    0x58
#define SDP1406_UNZIP_IN_LIMIT   0x4000000u
#define SDP1406_UNZIP_MAX_PAGES  33
#define SDP1406_UNZIP_IRQ_DONE   0x1u
#define SDP1406_UNZIP_IRQ_ERR    0x8u

enum {
    SDP1406_UNZIP_OK = 0,
    SDP1406_UNZIP_ERR_INPUT = 1,
    SDP1406_UNZIP_ERR_DATA = 2,
    SDP1406_UNZIP_ERR_TRUNC = 3,
    SDP1406_UNZIP_ERR_OUTPUT = 4,
};

typedef struct SDP1406Unzip {
    qemu_irq irq;
    uint32_t regs[SDP1406_UNZIP_SIZE / 4];
    uint32_t status;
    uint32_t err;
    uint32_t isize;
} SDP1406Unzip;

static void sdp1406_unzip_update(SDP1406Unzip *s)
{
    qemu_set_irq(s->irq,
                 (s->status & s->regs[SDP1406_UNZIP_R_IRQ_MASK / 4]) != 0);
}

static int sdp1406_unzip_decode(SDP1406Unzip *s, uint32_t *isize)
{
    AddressSpace *as = &address_space_memory;
    uint32_t in_addr = s->regs[SDP1406_UNZIP_R_IN_ADDR / 4];
    uint32_t in_size = s->regs[SDP1406_UNZIP_R_IN_SIZE / 4];
    uint32_t page_size = s->regs[SDP1406_UNZIP_R_OUT_SIZE / 4];
    uint8_t *in = NULL;
    uint8_t *page = NULL;
    z_stream zs;
    uint32_t total = 0;
    int rc = SDP1406_UNZIP_OK;
    int wbits;
    int ret = Z_OK;
    unsigned idx;

    *isize = 0;
    if (page_size == 0 || page_size > 0x10000) {
        page_size = 4096;
    }
    if (in_size == 0 || in_size > SDP1406_UNZIP_IN_LIMIT) {
        return SDP1406_UNZIP_ERR_INPUT;
    }
    in = g_malloc(in_size);
    page = g_malloc(page_size);
    if (address_space_read(as, in_addr, MEMTXATTRS_UNSPECIFIED, in, in_size)
        != MEMTX_OK) {
        rc = SDP1406_UNZIP_ERR_INPUT;
        goto out;
    }

    /* gzip magic selects the gzip wrapper; anything else is raw DEFLATE. */
    wbits = (in_size >= 2 && in[0] == 0x1f && in[1] == 0x8b) ? 15 + 16 : -15;
    memset(&zs, 0, sizeof(zs));
    if (inflateInit2(&zs, wbits) != Z_OK) {
        rc = SDP1406_UNZIP_ERR_DATA;
        goto out;
    }
    zs.next_in = in;
    zs.avail_in = in_size;

    for (idx = 0;; idx++) {
        uint32_t paddr;
        uint32_t produced;

        if (idx >= SDP1406_UNZIP_MAX_PAGES) {
            rc = SDP1406_UNZIP_ERR_OUTPUT;
            break;
        }
        paddr = idx == 0 ? s->regs[SDP1406_UNZIP_R_OUT_ADDR / 4]
                         : s->regs[SDP1406_UNZIP_R_LIST1 / 4 + idx - 1];
        zs.next_out = page;
        zs.avail_out = page_size;
        do {
            ret = inflate(&zs, Z_NO_FLUSH);
        } while (ret == Z_OK && zs.avail_out > 0);

        produced = page_size - zs.avail_out;
        if (produced && address_space_write(as, paddr, MEMTXATTRS_UNSPECIFIED,
                                            page, produced) != MEMTX_OK) {
            rc = SDP1406_UNZIP_ERR_OUTPUT;
            break;
        }
        total += produced;
        if (ret == Z_STREAM_END) {
            break;
        }
        if (ret != Z_OK) {
            rc = (ret == Z_BUF_ERROR && zs.avail_in == 0)
                 ? SDP1406_UNZIP_ERR_TRUNC : SDP1406_UNZIP_ERR_DATA;
            break;
        }
    }
    inflateEnd(&zs);
    *isize = total;

out:
    g_free(in);
    g_free(page);
    return rc;
}

static uint64_t sdp1406_unzip_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406Unzip *s = opaque;

    switch (addr) {
    case SDP1406_UNZIP_R_IRQ:
        return s->status;
    case SDP1406_UNZIP_R_ERR:
        return s->err;
    case SDP1406_UNZIP_R_ISIZE:
        return s->isize;
    default:
        return s->regs[addr / 4];
    }
}

static void sdp1406_unzip_write(void *opaque, hwaddr addr, uint64_t value,
                                unsigned size)
{
    SDP1406Unzip *s = opaque;

    if (addr == SDP1406_UNZIP_R_IRQ) {
        s->status &= ~(uint32_t)value;
    } else {
        s->regs[addr / 4] = value;
        if (addr == SDP1406_UNZIP_R_CMD && (value & 1)) {
            int rc = sdp1406_unzip_decode(s, &s->isize);

            s->err = rc;
            s->status |= rc == SDP1406_UNZIP_OK ? SDP1406_UNZIP_IRQ_DONE
                                                : SDP1406_UNZIP_IRQ_ERR;
        }
    }
    sdp1406_unzip_update(s);
}

static const MemoryRegionOps sdp1406_unzip_ops = {
    .read = sdp1406_unzip_read,
    .write = sdp1406_unzip_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void sdp1406_add_unzip(MemoryRegion *sysmem, DeviceState *gicdev)
{
    SDP1406Unzip *s = g_new0(SDP1406Unzip, 1);
    MemoryRegion *mr = g_new0(MemoryRegion, 1);

    s->irq = qdev_get_gpio_in(gicdev, SDP1406_UNZIP_IRQ);
    memory_region_init_io(mr, NULL, &sdp1406_unzip_ops, s, "sdp1406.unzip",
                          SDP1406_UNZIP_SIZE);
    memory_region_add_subregion(sysmem, SDP1406_UNZIP_BASE, mr);
}

/* eMMC host model (DesignWare-style register layout, as the sdp-mmc driver
 * uses it). Offsets: INTMASK 0x24, RINTSTS 0x44, CMD 0x2c, CMDARG 0x28,
 * RESP0-3 0x30-0x3c, BMOD 0x80, DBADDR 0x88, IDSTS 0x90, IDINTEN 0x94 and
 * DSCADDR 0x98 (64-bit addressing map, as the sdp-mmc driver is built). A blank eMMC card
 * answers the init sequence (CMD1 OCR, CMD2 CID, CMD9 CSD) and every other
 * response-bearing command with R1 status 0x900. A data command runs the
 * IDMAC descriptor chain at DBADDR. CMD completes with CMD_DONE, and data
 * completes with DTO. RINTSTS and IDSTS are write-1-to-clear, and the CTRL and
 * BMOD reset bits self-clear. Register 0x70 reports 64-bit MMCIF addressing
 * support, which the probe requires. */
#define SDP1406_MMC_INTMASK  0x24
#define SDP1406_MMC_MINTSTS  0x40
#define SDP1406_MMC_RINTSTS  0x44
#define SDP1406_MMC_CTRL     0x00
#define SDP1406_MMC_CMD      0x2c
#define SDP1406_MMC_BMOD     0x80
#define SDP1406_MMC_HCON     0x70
#define SDP1406_MMC_CTRL_RESET   0x7u
#define SDP1406_MMC_BMOD_SWR     0x1u
#define SDP1406_MMC_CMD_START    (1u << 31)
#define SDP1406_MMC_CMD_UPD_CLK  (1u << 21)
#define SDP1406_MMC_INT_CMD_DONE (1u << 2)
#define SDP1406_MMC_HCON_64BIT   (1u << 27)
#define SDP1406_MMC_BYTCNT       0x20
#define SDP1406_MMC_CMDARG       0x28
#define SDP1406_MMC_RESP0        0x30
#define SDP1406_MMC_DBADDR       0x88
#define SDP1406_MMC_DBADDRU      0x8c
#define SDP1406_MMC_IDSTS        0x90
#define SDP1406_MMC_IDINTEN      0x94
#define SDP1406_MMC_DSCADDR      0x98
#define SDP1406_MMC_DSCADDRU     0x9c
#define SDP1406_MMC_CMD_IDX_MASK 0x3fu
#define SDP1406_MMC_CMD_RESP_EXP (1u << 6)
#define SDP1406_MMC_CMD_DATA_EXP (1u << 9)
#define SDP1406_MMC_CMD_WRITE    (1u << 10)
#define SDP1406_MMC_BMOD_DE      (1u << 7)
#define SDP1406_MMC_INT_DTO      (1u << 3)
#define SDP1406_MMC_IDSTS_TI     (1u << 0)
#define SDP1406_MMC_IDSTS_DU     (1u << 4)
#define SDP1406_MMC_IDSTS_AIS    (1u << 9)
#define SDP1406_MMC_IDSTS_NIS    (1u << 8)
#define SDP1406_MMC_DESC_OWN     (1u << 31)
#define SDP1406_MMC_DESC_LD      (1u << 2)
#define SDP1406_MMC_DESC_CH      (1u << 4)
#define SDP1406_MMC_DESC_SIZE    0x1fffu
/* 64-bit addressing descriptor: status, reserved, length, reserved, buffer1
 * (low, high), buffer2 or next descriptor (low, high). */
#define SDP1406_MMC_DESC_BYTES   32
#define SDP1406_MMC_DESC_LEN_OFF 8
#define SDP1406_MMC_DESC_BUF_OFF 16
#define SDP1406_MMC_DESC_NEXT_OFF 24
#define SDP1406_MMC_CARD_OCR     0xc0ff8080u
#define SDP1406_MMC_CARD_R1      0x00000900u
#define SDP1406_MMC_CARD_RCA     0x0001u
#define SDP1406_MMC_BLOCK        512u
/* Capacity of a blank card: 1 GiB in 512-byte sectors. */
#define SDP1406_MMC_BLANK_SECTORS (1u << 21)
/* DTB "sdp-mmc" interrupts <0 3 0>: SPI 3, GIC IRQ 35. */
#define SDP1406_MMC_IRQ 3

typedef struct SDP1406Mmc {
    uint32_t rintsts;
    uint32_t regs[0x1000 / 4];
    /* Optional read-only card image (-drive if=none,id=sdp1406-mmc). */
    BlockBackend *blk;
    /* Byte offset in the card image for the next data block. */
    uint64_t pos;
    /* The current data transfer is SEND_EXT_CSD rather than card data. */
    bool ext_csd;
    qemu_irq irq;
} SDP1406Mmc;

/* A 128-bit card register is kept as four words; word 0 is RESP0. */
static void sdp1406_mmc_put(uint32_t w[4], unsigned lsb, unsigned width,
                            uint64_t value)
{
    unsigned i;

    for (i = 0; i < width; i++) {
        if ((value >> i) & 1) {
            unsigned bit = lsb + i;
            w[bit / 32] |= 1u << (bit % 32);
        }
    }
}

/* Blank eMMC card (MMC structure 3, spec 4.x). CSD gives 1 GiB: C_SIZE 0xfff,
 * C_SIZE_MULT 7, READ/WRITE_BL_LEN 9. */
static void sdp1406_mmc_card_cid(uint32_t w[4])
{
    sdp1406_mmc_put(w, 120, 8, 0x11);
    sdp1406_mmc_put(w, 104, 16, 0x4f53);
    sdp1406_mmc_put(w, 56, 48, 0x4f53454f4e47ull);
    sdp1406_mmc_put(w, 48, 8, 0x10);
    sdp1406_mmc_put(w, 16, 32, 0x4f534e47);
    sdp1406_mmc_put(w, 8, 8, 0x12);
    sdp1406_mmc_put(w, 0, 1, 1);
}

static void sdp1406_mmc_card_csd(uint32_t w[4])
{
    sdp1406_mmc_put(w, 126, 2, 3);
    sdp1406_mmc_put(w, 122, 4, 4);
    sdp1406_mmc_put(w, 112, 8, 0x27);
    sdp1406_mmc_put(w, 96, 8, 0x32);
    sdp1406_mmc_put(w, 84, 12, 0xf5);
    sdp1406_mmc_put(w, 80, 4, 9);
    sdp1406_mmc_put(w, 62, 12, 0xfff);
    sdp1406_mmc_put(w, 59, 3, 7);
    sdp1406_mmc_put(w, 56, 3, 7);
    sdp1406_mmc_put(w, 53, 3, 7);
    sdp1406_mmc_put(w, 50, 3, 7);
    sdp1406_mmc_put(w, 47, 3, 7);
    sdp1406_mmc_put(w, 42, 5, 31);
    sdp1406_mmc_put(w, 37, 5, 31);
    sdp1406_mmc_put(w, 32, 5, 31);
    sdp1406_mmc_put(w, 31, 1, 1);
    sdp1406_mmc_put(w, 26, 3, 2);
    sdp1406_mmc_put(w, 22, 4, 9);
    sdp1406_mmc_put(w, 0, 1, 1);
}

/* Build the 512-byte EXT_CSD an eMMC returns for CMD8. The card reports its
 * capacity here (CSD structure 3), so SEC_COUNT follows the attached image. */
static void sdp1406_mmc_ext_csd(SDP1406Mmc *s, uint8_t out[SDP1406_MMC_BLOCK])
{
    uint32_t sectors = SDP1406_MMC_BLANK_SECTORS;

    if (s->blk && blk_getlength(s->blk) > 0) {
        sectors = blk_getlength(s->blk) / SDP1406_MMC_BLOCK;
    }
    memset(out, 0, SDP1406_MMC_BLOCK);
    out[192] = 6;
    out[196] = 0x03;
    stl_le_p(out + 212, sectors);
}

/* Copy the next `len` bytes of card data to guest memory at `buf`. Bytes
 * past the end of the image, or with no image attached, read as zero. The
 * EXT_CSD read (CMD8) comes from a synthesized register block instead. */
static void sdp1406_mmc_fill(SDP1406Mmc *s, hwaddr buf, uint32_t len)
{
    AddressSpace *as = &address_space_memory;
    uint8_t *tmp = g_malloc0(len);
    int64_t size = s->blk ? blk_getlength(s->blk) : 0;

    if (s->ext_csd) {
        uint8_t ext[SDP1406_MMC_BLOCK];

        sdp1406_mmc_ext_csd(s, ext);
        if (s->pos < SDP1406_MMC_BLOCK) {
            memcpy(tmp, ext + s->pos,
                   MIN(len, SDP1406_MMC_BLOCK - s->pos));
        }
    } else if (size > 0 && s->pos < (uint64_t)size) {
        uint32_t avail = MIN((uint64_t)size - s->pos, len);

        if (blk_pread(s->blk, s->pos, avail, tmp, 0) < 0) {
            memset(tmp, 0, len);
        }
    }
    address_space_write(as, buf, MEMTXATTRS_UNSPECIFIED, tmp, len);
    s->pos += len;
    g_free(tmp);
}

/* Copy one descriptor buffer from guest memory to the card image. Writes
 * past the end of the image are dropped. The position advances as for reads. */
static void sdp1406_mmc_store(SDP1406Mmc *s, hwaddr buf, uint32_t len)
{
    AddressSpace *as = &address_space_memory;
    uint8_t *tmp = g_malloc0(len);
    int64_t size = s->blk ? blk_getlength(s->blk) : 0;

    address_space_read(as, buf, MEMTXATTRS_UNSPECIFIED, tmp, len);
    if (!s->ext_csd && size > 0 && s->pos < (uint64_t)size) {
        uint32_t avail = MIN((uint64_t)size - s->pos, len);

        blk_pwrite(s->blk, s->pos, avail, tmp, 0);
    }
    s->pos += len;
    g_free(tmp);
}

/* Walk the IDMAC descriptor chain at DBADDRU:DBADDR and move `bytes` of card
 * data. Reads fill the buffers from the attached card image (zeros with no
 * card). Writes copy the buffers to the card image. Only the low word of each
 * buffer address is used. A descriptor the host does not own stops the
 * transfer with the descriptor-unavailable status. */
static void sdp1406_mmc_idmac(SDP1406Mmc *s, uint32_t bytes, bool write)
{
    AddressSpace *as = &address_space_memory;
    hwaddr desc = ((hwaddr)s->regs[SDP1406_MMC_DBADDRU / 4] << 32) |
                  s->regs[SDP1406_MMC_DBADDR / 4];

    while (bytes) {
        uint32_t des0 = ldl_le_phys(as, desc);
        uint32_t size, chunk;
        hwaddr buf;

        s->regs[SDP1406_MMC_DSCADDR / 4] = desc;
        if (!(des0 & SDP1406_MMC_DESC_OWN)) {
            s->regs[SDP1406_MMC_IDSTS / 4] |=
                SDP1406_MMC_IDSTS_DU | SDP1406_MMC_IDSTS_AIS;
            return;
        }
        size = ldl_le_phys(as, desc + SDP1406_MMC_DESC_LEN_OFF) &
               SDP1406_MMC_DESC_SIZE;
        buf = ldl_le_phys(as, desc + SDP1406_MMC_DESC_BUF_OFF);
        chunk = MIN(size, bytes);
        if (chunk) {
            if (write) {
                sdp1406_mmc_store(s, buf, chunk);
            } else {
                sdp1406_mmc_fill(s, buf, chunk);
            }
        }
        bytes -= chunk;
        stl_le_phys(as, desc, des0 & ~SDP1406_MMC_DESC_OWN);
        s->regs[SDP1406_MMC_IDSTS / 4] |=
            SDP1406_MMC_IDSTS_TI | SDP1406_MMC_IDSTS_NIS;
        if (des0 & SDP1406_MMC_DESC_LD) {
            break;
        }
        desc = (des0 & SDP1406_MMC_DESC_CH)
               ? ldl_le_phys(as, desc + SDP1406_MMC_DESC_NEXT_OFF)
               : desc + SDP1406_MMC_DESC_BYTES;
    }
}

static void sdp1406_mmc_update_irq(SDP1406Mmc *s)
{
    qemu_set_irq(s->irq, (s->rintsts & s->regs[SDP1406_MMC_INTMASK / 4]) != 0);
}

/* Answer a command and, when it carries data, run the transfer. */
static void sdp1406_mmc_command(SDP1406Mmc *s, uint32_t cmd)
{
    uint32_t idx = cmd & SDP1406_MMC_CMD_IDX_MASK;
    uint32_t *resp = &s->regs[SDP1406_MMC_RESP0 / 4];
    uint32_t w[4] = { 0 };

    s->rintsts |= SDP1406_MMC_INT_CMD_DONE;
    if (cmd & SDP1406_MMC_CMD_RESP_EXP) {
        switch (idx) {
        case 1:
            resp[0] = SDP1406_MMC_CARD_OCR;
            break;
        case 2:
            sdp1406_mmc_card_cid(w);
            memcpy(resp, w, sizeof(w));
            break;
        case 9:
            sdp1406_mmc_card_csd(w);
            memcpy(resp, w, sizeof(w));
            break;
        default:
            resp[0] = SDP1406_MMC_CARD_R1;
            break;
        }
    }
    if (cmd & SDP1406_MMC_CMD_DATA_EXP) {
        /* Block-addressed card (OCR CCS set): CMDARG is a 512-byte LBA. */
        if (idx == 17 || idx == 18 || idx == 24 || idx == 25) {
            s->ext_csd = false;
            s->pos = (uint64_t)s->regs[SDP1406_MMC_CMDARG / 4] * 512;
        } else if (idx == 8) {
            s->ext_csd = true;
            s->pos = 0;
        }
        if (s->regs[SDP1406_MMC_BMOD / 4] & SDP1406_MMC_BMOD_DE) {
            sdp1406_mmc_idmac(s, s->regs[SDP1406_MMC_BYTCNT / 4],
                              cmd & SDP1406_MMC_CMD_WRITE);
        }
        s->rintsts |= SDP1406_MMC_INT_DTO;
    }
    sdp1406_mmc_update_irq(s);
}

static uint64_t sdp1406_mmc_read(void *opaque, hwaddr addr, unsigned size)
{
    SDP1406Mmc *s = opaque;

    if (addr == SDP1406_MMC_RINTSTS) {
        return s->rintsts;
    }
    if (addr == SDP1406_MMC_MINTSTS) {
        return s->rintsts & s->regs[SDP1406_MMC_INTMASK / 4];
    }
    if (addr == SDP1406_MMC_CMD) {
        return s->regs[addr / 4] & ~SDP1406_MMC_CMD_START;
    }
    if (addr == SDP1406_MMC_HCON) {
        return SDP1406_MMC_HCON_64BIT;
    }
    return s->regs[addr / 4];
}

static void sdp1406_mmc_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size)
{
    SDP1406Mmc *s = opaque;

    if (addr == SDP1406_MMC_RINTSTS) {
        s->rintsts &= ~(uint32_t)value;
        sdp1406_mmc_update_irq(s);
        return;
    }
    if (addr == SDP1406_MMC_IDSTS) {
        s->regs[addr / 4] &= ~(uint32_t)value;
        return;
    }
    s->regs[addr / 4] = value;
    if (addr == SDP1406_MMC_CTRL) {
        s->regs[addr / 4] &= ~SDP1406_MMC_CTRL_RESET;
    } else if (addr == SDP1406_MMC_BMOD) {
        s->regs[addr / 4] &= ~SDP1406_MMC_BMOD_SWR;
    }
    if (addr == SDP1406_MMC_CMD && (value & SDP1406_MMC_CMD_START) &&
        !(value & SDP1406_MMC_CMD_UPD_CLK)) {
        sdp1406_mmc_command(s, value);
    }
    sdp1406_mmc_update_irq(s);
}

static const MemoryRegionOps sdp1406_mmc_ops = {
    .read = sdp1406_mmc_read,
    .write = sdp1406_mmc_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void sdp1406_add_mmc(MemoryRegion *sysmem, DeviceState *gicdev)
{
    SDP1406Mmc *s = g_new0(SDP1406Mmc, 1);
    MemoryRegion *mr = g_new0(MemoryRegion, 1);

    s->irq = qdev_get_gpio_in(gicdev, SDP1406_MMC_IRQ);

    /* The board has no SD bus, so the card image is attached as
     * -drive if=none,id=sdp1406-mmc,file=... */
    s->blk = blk_by_name("sdp1406-mmc");
    /* No device is attached to the backend, so it has only the default
     * read permission. Ask for write unless the drive is read-only. */
    if (s->blk && !bdrv_is_read_only(blk_bs(s->blk))) {
        blk_set_perm(s->blk, BLK_PERM_CONSISTENT_READ | BLK_PERM_WRITE,
                     BLK_PERM_ALL, &error_fatal);
    }
    memory_region_init_io(mr, NULL, &sdp1406_mmc_ops, s, "sdp1406.mmc", 0x1000);
    memory_region_add_subregion(sysmem, 0x00100000, mr);
}

static MemoryRegion *sdp1406_add_ram(MemoryRegion *sysmem, const char *name,
                                     hwaddr base, uint64_t size)
{
    MemoryRegion *mr = g_new0(MemoryRegion, 1);

    memory_region_init_ram(mr, NULL, name, size, &error_fatal);
    memory_region_add_subregion(sysmem, base, mr);
    return mr;
}

typedef struct SDP1406Display {
    MemoryRegion *sysmem;
    QemuConsole *con;
    MemoryRegionSection fbsection;
    bool invalidate;
} SDP1406Display;

static void sdp1406_fb_draw_line(void *opaque, uint8_t *d, const uint8_t *s,
                                 int width, int deststep)
{
    for (int x = 0; x < width; x++) {
        memcpy(d, s, SDP1406_FB_BPP);
        s += SDP1406_FB_BPP;
        d += deststep;
    }
}

static void sdp1406_fb_invalidate(void *opaque)
{
    SDP1406Display *s = opaque;

    s->invalidate = true;
}

static void sdp1406_fb_update(void *opaque)
{
    SDP1406Display *s = opaque;
    DisplaySurface *surface = qemu_console_surface(s->con);
    int first = 0;
    int last = -1;

    framebuffer_update_memory_section(&s->fbsection, s->sysmem,
                                      SDP1406_FB_BASE, SDP1406_FB_HEIGHT,
                                      SDP1406_FB_WIDTH * SDP1406_FB_BPP);
    framebuffer_update_display(surface, &s->fbsection,
                               SDP1406_FB_WIDTH, SDP1406_FB_HEIGHT,
                               SDP1406_FB_WIDTH * SDP1406_FB_BPP,
                               surface_stride(surface), SDP1406_FB_BPP,
                               s->invalidate, sdp1406_fb_draw_line, NULL,
                               &first, &last);
    s->invalidate = false;
    if (first >= 0) {
        dpy_gfx_update(s->con, 0, first, SDP1406_FB_WIDTH,
                       last - first + 1);
    }
}

static const GraphicHwOps sdp1406_fb_ops = {
    .invalidate = sdp1406_fb_invalidate,
    .gfx_update = sdp1406_fb_update,
};

static void sdp1406_display_init(MemoryRegion *sysmem)
{
    SDP1406Display *s = g_new0(SDP1406Display, 1);

    s->sysmem = sysmem;
    s->con = graphic_console_init(NULL, 0, &sdp1406_fb_ops, s);
    qemu_console_resize(s->con, SDP1406_FB_WIDTH, SDP1406_FB_HEIGHT);
}

/*
 * Loads the board DTB at SDP1406_DTB_ADDR. The kernel's 0x80000000 bank is
 * appended to the DTB's single /memory node, as one DRAM map would list it.
 * The DTB and initrd are reserved with /memreserve so the kernel does not
 * allocate them. QEMU's arm_load_dtb() is not used because it would also add
 * a PSCI node, which changes the SMP path.
 */
static void sdp1406_load_dtb_with_initrd(const char *dtb_path,
                                         hwaddr initrd_start,
                                         hwaddr initrd_size)
{
    int dtb_file_size;
    int mem_off;
    int reg_len;
    const fdt32_t *reg;
    fdt32_t *new_reg;
    void *fdt;

    fdt = load_device_tree(dtb_path, &dtb_file_size);
    if (!fdt) {
        error_report("could not load DTB %s", dtb_path);
        exit(1);
    }
    mem_off = fdt_path_offset(fdt, "/memory");
    if (mem_off < 0) {
        error_report("DTB %s has no /memory node", dtb_path);
        exit(1);
    }
    reg = fdt_getprop(fdt, mem_off, "reg", &reg_len);
    if (!reg || reg_len % 4) {
        error_report("DTB %s: /memory has no usable reg", dtb_path);
        exit(1);
    }
    /* Append one <base size> pair, with 1 address cell and 1 size cell, the
     * same layout as the existing banks. */
    new_reg = g_malloc(reg_len + 8);
    memcpy(new_reg, reg, reg_len);
    new_reg[reg_len / 4] = cpu_to_fdt32(SDP1406_KERNEL_RAM_BASE);
    new_reg[reg_len / 4 + 1] = cpu_to_fdt32(SDP1406_KERNEL_RAM_SIZE);
    if (fdt_setprop(fdt, mem_off, "reg", new_reg, reg_len + 8)) {
        error_report("DTB %s: could not extend /memory reg", dtb_path);
        exit(1);
    }
    g_free(new_reg);

    if (initrd_size) {
        qemu_fdt_add_path(fdt, "/chosen");
        qemu_fdt_setprop_sized_cells(fdt, "/chosen", "linux,initrd-start", 1,
                                     initrd_start);
        qemu_fdt_setprop_sized_cells(fdt, "/chosen", "linux,initrd-end", 1,
                                     initrd_start + initrd_size);
        if (fdt_add_mem_rsv(fdt, initrd_start, initrd_size)) {
            error_report("DTB %s: could not reserve the initrd", dtb_path);
            exit(1);
        }
    }
    /* load_device_tree() returns a blob grown to about 1 MB, and a packed
     * blob has no room to grow. Pack, copy into a buffer with one extra
     * reservation entry (16 bytes), reserve the DTB at its final size, and
     * pack again. The entry adds exactly 16 bytes to the packed size. */
    size_t packed;
    void *resv;

    if (fdt_pack(fdt)) {
        error_report("DTB %s: could not pack", dtb_path);
        exit(1);
    }
    packed = fdt_totalsize(fdt);
    resv = g_malloc0(packed + sizeof(struct fdt_reserve_entry));
    if (fdt_open_into(fdt, resv, packed + sizeof(struct fdt_reserve_entry)) ||
        fdt_add_mem_rsv(resv, SDP1406_DTB_ADDR,
                        packed + sizeof(struct fdt_reserve_entry)) ||
        fdt_pack(resv) ||
        fdt_totalsize(resv) != packed + sizeof(struct fdt_reserve_entry)) {
        error_report("DTB %s: could not reserve the DTB", dtb_path);
        exit(1);
    }
    rom_add_blob_fixed("sdp1406.dtb", resv, fdt_totalsize(resv),
                       SDP1406_DTB_ADDR);
    g_free(resv);
    g_free(fdt);
}

/* Physical address of the uImage's holding pen (virtual 0xc03f11c8). It
 * spins on pen_release until it equals the CPU number, then branches to
 * secondary_startup. Only valid for this kernel build. */
#define SDP1406_SMP_PEN_PHYS 0x803f11c8ULL

/* Secure monitor for the guest's SMC calls. No firmware exists on this board,
 * so SMC returns r0 = 0 to the caller. The other vectors spin so an unexpected
 * trap stops visibly. The SMC vector branches to the handler at offset 0x20. */
#define SDP1406_MONITOR_BASE 0x00200000ULL
#define SDP1406_MONITOR_SIZE 0x1000ULL

static const uint32_t sdp1406_monitor_vectors[] = {
    0xeafffffe, /* reset: spin */
    0xeafffffe, /* undef: spin */
    0xea000004, /* smc: b to the handler at 0x20 */
    0xeafffffe, /* prefetch abort: spin */
    0xeafffffe, /* data abort: spin */
    0xeafffffe, /* reserved: spin */
    0xeafffffe, /* irq: spin */
    0xeafffffe, /* fiq: spin */
    0xe3a00000, /* smc handler: mov r0, #0 */
    0xe1b0f00e, /* movs pc, lr: return to the caller */
};

static void sdp1406_add_monitor(MemoryRegion *sysmem)
{
    MemoryRegion *mr = g_new0(MemoryRegion, 1);
    uint32_t vectors[ARRAY_SIZE(sdp1406_monitor_vectors)];
    int i;

    memory_region_init_ram(mr, NULL, "sdp1406.monitor",
                           SDP1406_MONITOR_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, SDP1406_MONITOR_BASE, mr);
    for (i = 0; i < ARRAY_SIZE(vectors); i++) {
        vectors[i] = cpu_to_le32(sdp1406_monitor_vectors[i]);
    }
    address_space_write(&address_space_memory, SDP1406_MONITOR_BASE,
                        MEMTXATTRS_UNSPECIFIED, vectors, sizeof(vectors));
}

/* Reset clears the CPU's MVBAR, so point it at the monitor again on each reset. */
static void sdp1406_monitor_reset(void *opaque)
{
    ARMCPU *cpu = opaque;

    cpu->env.cp15.mvbar = SDP1406_MONITOR_BASE;
}

/* The kernel supplies the holding pen, so QEMU's stub is not written. */
static void sdp1406_write_secondary(ARMCPU *cpu,
                                    const struct arm_boot_info *info)
{
}

static void sdp1406_reset_secondary(ARMCPU *cpu,
                                    const struct arm_boot_info *info)
{
    cpu_set_pc(CPU(cpu), SDP1406_SMP_PEN_PHYS);
}

static void sdp1406_add_usb_hc(MemoryRegion *sysmem, DeviceState *gicdev,
                               const char *type, hwaddr base, int irq)
{
    DeviceState *dev = qdev_new(type);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);

    sysbus_realize_and_unref(sbd, &error_fatal);
    sysbus_connect_irq(sbd, 0, qdev_get_gpio_in(gicdev, irq));
    memory_region_add_subregion(sysmem, base, sysbus_mmio_get_region(sbd, 0));
}

static void sdp1406_add_usb(MemoryRegion *sysmem, DeviceState *gicdev)
{
    for (int n = 0; n < SDP1406_USB_HC_COUNT; n++) {
        sdp1406_add_usb_hc(sysmem, gicdev, TYPE_PLATFORM_EHCI,
                           SDP1406_USB_EHCI_BASE + n * SDP1406_USB_HC_STRIDE,
                           SDP1406_USB_EHCI_IRQ(n));
        sdp1406_add_usb_hc(sysmem, gicdev, TYPE_SYSBUS_OHCI,
                           SDP1406_USB_OHCI_BASE + n * SDP1406_USB_HC_STRIDE,
                           SDP1406_USB_OHCI_IRQ(n));
    }
}

static void sdp1406_add_xhci(MemoryRegion *sysmem, DeviceState *gicdev)
{
    DeviceState *dev = qdev_new(TYPE_XHCI_SYSBUS);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);

    sysbus_realize_and_unref(sbd, &error_fatal);
    sysbus_connect_irq(sbd, 0, qdev_get_gpio_in(gicdev, SDP1406_XHCI_IRQ));
    memory_region_add_subregion(sysmem, SDP1406_XHCI_BASE,
                                sysbus_mmio_get_region(sbd, 0));
    sdp1406_add_stub(sysmem, "sdp1406.xhci-tail",
                     SDP1406_XHCI_BASE + SDP1406_XHCI_SIZE,
                     SDP1406_XHCI_WINDOW - SDP1406_XHCI_SIZE);
}

static void sdp1406_init(MachineState *machine)
{
    MemoryRegion *sysmem = get_system_memory();
    ARMCPU *cpu;
    ARMCPU *cpus[SDP1406_MAX_CPUS];
    int i;
    DeviceState *gicdev;
    DeviceState *uartdev;
    DeviceState *ttysac2dev;
    MemoryRegion *ram2;
    MemoryRegion *scanout;
    SysBusDevice *gicbus;
    const char *initrd_filename;
    /* QEMU keeps this pointer for the CPU reset hook, so it must outlive
     * machine init. */
    struct arm_boot_info *bootinfo = g_new0(struct arm_boot_info, 1);

    /* The RAM map below is fixed, so -m cannot change it; say so. */
    if (machine->ram_size != SDP1406_RAM0_SIZE) {
        warn_report("-m %" PRIu64 " MiB is ignored: the sdp1406 board has "
                    "fixed RAM of %" PRIu64 " MiB",
                    (uint64_t)(machine->ram_size / MiB),
                    (uint64_t)(SDP1406_RAM0_SIZE / MiB));
    }

    for (i = 0; i < machine->smp.cpus; i++) {
        cpus[i] = ARM_CPU(cpu_create(machine->cpu_type));
    }
    cpu = cpus[0];

    sdp1406_add_ram(sysmem, "sdp1406.ram0", SDP1406_RAM0_BASE,
                    SDP1406_RAM0_SIZE);
    sdp1406_add_ram(sysmem, "sdp1406.ram1", SDP1406_RAM1_BASE,
                    SDP1406_RAM1_SIZE);
    ram2 = sdp1406_add_ram(sysmem, "sdp1406.ram2", SDP1406_RAM2_BASE,
                           SDP1406_RAM2_SIZE);
    sdp1406_add_ram(sysmem, "sdp1406.sdp-mmap", SDP1406_SDP_MMAP_BASE,
                    SDP1406_SDP_MMAP_SIZE);
    sdp1406_add_monitor(sysmem);
    /* DTB sdp_hwmem blocks at 0x00f00000 and 0x00f20000 have no RAM bank. */
    sdp1406_add_ram(sysmem, "sdp1406.hwmem", 0x00f00000ULL, 0x00040000ULL);
    /* Gap between RAM1 and RAM2 below the scanout alias; the kernel stores here. */
    sdp1406_add_ram(sysmem, "sdp1406.gap", 0x37800000ULL, 0x04d00000ULL);
    /* The scanout range is an alias of the bottom of RAM2, not its own RAM
     * block: a separate block at this address aborts QEMU in
     * tlb_reset_dirty_range_all() once the display logs its framebuffer. The
     * bottom sits below the kernel's CMA reserve at 0x43000000; the top did
     * not. */
    scanout = g_new0(MemoryRegion, 1);
    memory_region_init_alias(scanout, NULL, "sdp1406.scanout", ram2, 0,
                             SDP1406_SCANOUT_SIZE);
    memory_region_add_subregion(sysmem, SDP1406_SCANOUT_BASE, scanout);

    sdp1406_display_init(sysmem);

    gicdev = qdev_new(gic_class_name());
    qdev_prop_set_uint32(gicdev, "revision", 2);
    qdev_prop_set_uint32(gicdev, "num-cpu", machine->smp.cpus);
    qdev_prop_set_uint32(gicdev, "num-irq", SDP1406_GIC_IRQS);
    gicbus = SYS_BUS_DEVICE(gicdev);
    sysbus_realize_and_unref(gicbus, &error_fatal);
    sysbus_mmio_map(gicbus, 0, SDP1406_GICD_BASE);
    sysbus_mmio_map(gicbus, 1, SDP1406_GICC_BASE);
    /* The DTB's arch-timer lists PPI 30 (non-secure physical, "1 14") and
     * PPI 29 (secure physical, "1 13"). The GIC's per-CPU PPI inputs follow
     * the SPI inputs: CPU n's PPI p is input (SDP1406_GIC_IRQS - 32) + 32 * n
     * + p. */
    for (i = 0; i < machine->smp.cpus; i++) {
        sysbus_connect_irq(gicbus, i,
                           qdev_get_gpio_in(DEVICE(cpus[i]), ARM_CPU_IRQ));
        qdev_connect_gpio_out(DEVICE(cpus[i]), SDP1406_GTIMER_PHYS,
                              qdev_get_gpio_in(gicdev,
                                               SDP1406_GIC_IRQS - 32 + 32 * i +
                                               SDP1406_PPI_NS_PHYS));
        qdev_connect_gpio_out(DEVICE(cpus[i]), SDP1406_GTIMER_SEC,
                              qdev_get_gpio_in(gicdev,
                                               SDP1406_GIC_IRQS - 32 + 32 * i +
                                               SDP1406_PPI_SEC_PHYS));
    }

    /* Windows from the DTB: chip ID, clock and pinctrl, peripheral block,
     * and the external-interrupt and extra register ranges. */
    sdp1406_add_stub(sysmem, "sdp1406.chipid", 0x00180000, 0x1000);
    sdp1406_add_stub(sysmem, "sdp1406.periph", 0x00190000, 0x2000);
    /* DTB sdp-i2c controllers 0x00190100-0x001901ff (CH:0-7) and
     * 0x00191000-0x001910ff (CH:8-15): overlay the periph stub above. */
    sdp1406_add_i2c(sysmem, gicdev);
    /* DTB /xhci@00400000: real sysbus xHCI, see sdp1406_add_xhci(). */
    sdp1406_add_xhci(sysmem, gicdev);
    /* 0x005c2000-0x005cffff has no DTB node; the drm/HDMI init stores into
     * it (fault at 0xfe4c6018 = phys 0x005c6018), so it is covered here. */
    sdp1406_add_stub(sysmem, "sdp1406.clkpin", 0x005c0000, 0x10000);
    sdp1406_add_stub(sysmem, "sdp1406.osd", 0x005d0000, 0xc000);
    /* No DTB node: the kernel touches 0x00609028 (PP0 mask register; the Mali
     * probe's stray access) before sdp1406_add_mali() overlays this range. */
    sdp1406_add_stub(sysmem, "sdp1406.unnamed", 0x005e0000, 0x80000);
    sdp1406_add_usb(sysmem, gicdev);
    /* Media blocks (MFC, JPEG, HEN) between the DTB nodes; mapped by the kernel. */
    sdp1406_add_stub(sysmem, "sdp1406.media", 0x00a00000, 0x00100000);
    sdp1406_add_dsp_status(sysmem);
    /* DTB /demux@00900000 (transport-stream demux) and its 0x930000 block. */
    sdp1406_add_stub(sysmem, "sdp1406.demux", 0x00900000, 0x00011f00);
    sdp1406_add_stub(sysmem, "sdp1406.demux2", 0x00930000, 0x00001000);
    /* DTB demux@00900000 "ci" range (reg size 0x134). The sdp_ci driver
     * reads virt 0xfe070134, one register past that size, so the stub maps
     * a full page. */
    sdp1406_add_stub(sysmem, "sdp1406.demux_ci", 0x00170000, 0x00001000);
    /* sdp_tsd.ko sdp_ci_probe() maps these four windows by absolute address
     * (not from the DTB) and writes/reads them. Backed as plain RAM so the
     * probe completes; register semantics are not modelled. */
    sdp1406_add_ram(sysmem, "sdp1406.ci-probe-50000", 0x00050000, 0x1000);
    sdp1406_add_ram(sysmem, "sdp1406.ci-probe-58000", 0x00058000, 0x8000);
    sdp1406_add_ram(sysmem, "sdp1406.ci-probe-90000", 0x00090000, 0x1000);
    sdp1406_add_ram(sysmem, "sdp1406.ci-probe-98000", 0x00098000, 0x8000);
    /* DTB sdp_ddrbus block at 0x00e01000; the range below hwmem RAM is stubbed. */
    sdp1406_add_stub(sysmem, "sdp1406.ddrbus", 0x00e00000, 0x000f0000);
    /* DTB /ga@00660000 (graphics accelerator) block, 0xca00 bytes. */
    sdp1406_add_ga(sysmem, gicdev);
    /* Mali-400 MP4 GPU (no DTB node; see sdp1406_add_mali()). */
    sdp1406_add_mali(sysmem, gicdev);
    sdp1406_add_stub(sysmem, "sdp1406.extin", 0x00b60000, 0x1000);
    /* No DTB node: the kernel touches 0x00b57058 (driver window below extin). */
    sdp1406_add_stub(sysmem, "sdp1406.unnamed2", 0x00b50000, 0x10000);
    /* No DTB node: the audio driver (auddec_init_tas) touches 0x00b74004. */
    sdp1406_add_stub(sysmem, "sdp1406.unnamed3", 0x00b74000, 0x0c000);
    sdp1406_add_stub(sysmem, "sdp1406.extin2", 0x00b70000, 0x4000);
    /* DTB /sdp_uddec@00b00000 (regs up to 0xb28000+0x300). The audio codec
     * (sdp_codec.ko) takes a data abort on 0x00b28004 without this block. */
    sdp1406_add_stub(sysmem, "sdp1406.uddec", 0x00b00000, 0x00030000);
    /* Not in the DTB: the uImage ioremap()s 0x00790000 (size 0x400) from a
     * hard-coded address (0xc03f1538) and stores to offset 0x3e0 + index * 4
     * (0xc03f15a0). The stub only logs the stores; their meaning is open. */
    sdp1406_add_stub(sysmem, "sdp1406.hard790", 0x00790000, 0x1000);
    /* sdp_messagebox@800100 in the DTB (reg size 0x500). It is mapped as a
     * full 0x1000 page. The kernel's status poll at 0xc025ab74 reads its register at +0x40 through the page
     * ioremap()ed at 0xfe700000 (phys 0x00800000). */
    sdp1406_add_mbox(sysmem, gicdev);
    sdp1406_add_unzip(sysmem, gicdev);
    /* DTB "ethernet" reg entries 0x120000-0x123fff. The MAC, MII and DMA
     * registers are modelled in sdp1406_add_gmac(); the rest of the window
     * logs unimp. */
    sdp1406_add_gmac(gicdev);
    /* DTB ethernet "pad_ctrl_reg" (0x800 bytes), also used by the eMMC
     * "initregs". Faulting virt 0xfe030000 translates to phys 0x130000. */
    sdp1406_add_stub(sysmem, "sdp1406.padctrl", 0x00130000, 0x1000);
    /* DTB "mmc" reg 0x100000 0x1000, a DesignWare-style host with no card.
     * The sdp-gmac probe faults on virt 0xfe0000a8, which translates to
     * phys 0x1000a8. */
    sdp1406_add_mmc(sysmem, gicdev);
    /* DTB "mixer@005DC000" (size 0x4000). Register file, see
     * sdp1406_add_mixer(). */
    sdp1406_add_mixer(sysmem);
    /* DTB "sdp_dp@00988000" MEM ranges, in the index order that dpreg_init()
     * in sdp-drm.ko maps them. The 4-byte clock ranges fall in clkpin. */
    sdp1406_add_stub(sysmem, "sdp1406.dp0", 0x00c00000, 0x000a0000);
    sdp1406_add_stub(sysmem, "sdp1406.dp1", 0x00cb0000, 0x00020000);
    sdp1406_add_stub(sysmem, "sdp1406.dp2", 0x00d00000, 0x00050000);
    sdp1406_add_stub(sysmem, "sdp1406.dp3", 0x00d80000, 0x00030000);
    sdp1406_add_stub(sysmem, "sdp1406.dp4", 0x00dc0000, 0x00020000);
    sdp1406_add_stub(sysmem, "sdp1406.dp5", 0x00980000, 0x00038000);
    sdp1406_add_stub(sysmem, "sdp1406.dp6", 0x00b90000, 0x00010000);
    sdp1406_add_stub(sysmem, "sdp1406.dp7", 0x00b80000, 0x00010000);
    /* DTB "hdmi@580000" (size 0x3ffff). Faulting virt 0xfe490000 is phys 0x590000. */
    sdp1406_add_stub(sysmem, "sdp1406.hdmi", 0x00580000, 0x00040000);

    /* The UART is mapped with a higher overlap priority than the periph stub
     * that covers its window. */
    uartdev = exynos4210_uart_create(-1, SDP1406_UART_FIFO, 0, serial_hd(0),
                                     qdev_get_gpio_in(gicdev,
                                                      SDP1406_UART0_IRQ));
    memory_region_add_subregion_overlap(sysmem, SDP1406_UART0_BASE,
                                        sysbus_mmio_get_region(
                                            SYS_BUS_DEVICE(uartdev), 0), 1);

    /* Legacy UART micom window: inert, so a driver access is not an abort. */
    sdp1406_add_stub(sysmem, "sdp1406.micom-uart", SDP1406_MICOM_BASE, 0x100);

    /* ttySAC2 (agetty in the rootfs): second -serial chardev (serial_hd(1)). */
    ttysac2dev = exynos4210_uart_create(-1, SDP1406_UART_FIFO, 2, serial_hd(1),
                                        qdev_get_gpio_in(gicdev,
                                                         SDP1406_TTYSAC2_IRQ));
    memory_region_add_subregion_overlap(sysmem, SDP1406_TTYSAC2_BASE,
                                        sysbus_mmio_get_region(
                                            SYS_BUS_DEVICE(ttysac2dev), 0), 1);

    /* The bootloader's DTB is loaded by this board; QEMU's memory fixup
     * would replace the three-bank /memory node with one range. */
    /* The RAM bank the kernel runs from. It is linked for 0x80008000. */
    sdp1406_add_ram(sysmem, "sdp1406.kernel", 0x80000000ULL, 0x10000000ULL);

    bootinfo->ram_size = 0x10000000ULL;
    bootinfo->loader_start = 0x80000000ULL;
    bootinfo->board_id = -1;
    bootinfo->dtb_start = SDP1406_DTB_ADDR;
    bootinfo->skip_dtb_autoload = true;
    bootinfo->primary_cpu = cpu;
    bootinfo->write_secondary_boot = sdp1406_write_secondary;
    bootinfo->secondary_cpu_reset_hook = sdp1406_reset_secondary;
    /* Take the initrd away from arm_load_kernel() and load it here. */
    initrd_filename = machine->initrd_filename;
    machine->initrd_filename = NULL;
    arm_load_kernel(cpu, machine, bootinfo);
    /* Registered after arm_load_kernel(), whose reset handlers clear MVBAR. */
    for (i = 0; i < machine->smp.cpus; i++) {
        cpus[i]->env.cp15.mvbar = SDP1406_MONITOR_BASE;
        qemu_register_reset(sdp1406_monitor_reset, cpus[i]);
    }

    if (machine->dtb) {
        if (initrd_filename) {
            ssize_t initrd_size = load_image_targphys(initrd_filename,
                                                      SDP1406_INITRD_ADDR,
                                                      -1, &error_fatal);
            sdp1406_load_dtb_with_initrd(machine->dtb, SDP1406_INITRD_ADDR,
                                         initrd_size);
        } else {
            sdp1406_load_dtb_with_initrd(machine->dtb, 0, 0);
        }
    }
}

static void sdp1406_machine_init(MachineClass *mc)
{
    mc->desc = "Samsung SDP1406 (HawkM) analysis board";
    mc->init = sdp1406_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a15");
    mc->max_cpus = SDP1406_MAX_CPUS;
    mc->default_cpus = SDP1406_MAX_CPUS;
    mc->default_ram_size = SDP1406_RAM0_SIZE;
    mc->block_default_type = IF_SCSI;
}

DEFINE_MACHINE_ARM("sdp1406", sdp1406_machine_init)
