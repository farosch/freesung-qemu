/*
 * Broadcom btusb-style Bluetooth HCI controller for the SDP1406 model.
 *
 * HCI runs over USB (H2 framing, interface class E0/01/01): commands arrive
 * as class requests 0x20/0 on EP0, events leave on EP1 IN, ACL uses EP2
 * IN/OUT. The Broadcom vendor download commands follow btdl: 0xFC2E starts a
 * download, 0xFC4C writes RAM (4-byte address, then data), and 0xFC4E launches
 * RAM (4-byte address). RAM is a 1 MiB window; launch succeeds only at an
 * address that was written. SCO is not modelled.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/bswap.h"
#include "qapi/error.h"
#include "hw/usb.h"
#include "migration/vmstate.h"
#include "desc.h"
#include "qom/object.h"
#include "trace.h"

#define TYPE_USB_BCM_BTUSB "usb-bcm-btusb"
#define BCM_BT_EVQ_LEN     8
#define BCM_BT_EVT_MAX     258
#define BCM_BT_RAM_SIZE    0x100000
#define BCM_BT_RAM_ADDR_MASK 0x00ffffff
#define BCM_BT_RAM_GRAIN   1024

typedef struct USBBcmBtState {
    USBDevice dev;
    USBEndpoint *intr;
    uint8_t evq[BCM_BT_EVQ_LEN][BCM_BT_EVT_MAX];
    uint16_t evq_len[BCM_BT_EVQ_LEN];
    uint16_t evq_off;
    unsigned evq_head;
    unsigned evq_count;
    uint8_t ram[BCM_BT_RAM_SIZE];
    uint64_t ram_written[BCM_BT_RAM_SIZE / BCM_BT_RAM_GRAIN / 64];
} USBBcmBtState;

OBJECT_DECLARE_SIMPLE_TYPE(USBBcmBtState, USB_BCM_BTUSB)

#define BCM_BT_HCI_COMMAND_REQ ((USB_DIR_OUT | USB_TYPE_CLASS | \
                                 USB_RECIP_DEVICE) << 8)
#define BT_HCI_RESET               0x0c03
#define BT_HCI_WRITE_SCAN_ENABLE   0x0c1a
#define BT_HCI_READ_LOCAL_VERSION  0x1001
#define BT_HCI_READ_LOCAL_FEATURES 0x1003
#define BT_HCI_READ_BD_ADDR        0x1009
#define BT_VS_DOWNLOAD_MINIDRIVER  0xfc2e
#define BT_VS_WRITE_RAM            0xfc4c
#define BT_VS_LAUNCH_RAM           0xfc4e
#define BT_HCI_EVT_COMMAND_COMPLETE 0x0e
#define BT_HCI_STATUS_SUCCESS      0x00
#define BT_HCI_STATUS_UNKNOWN_CMD  0x01
#define BT_HCI_STATUS_INVALID_PARAMS 0x12

static const uint8_t bcm_bt_bd_addr[6] = { 0x00, 0xc3, 0x8c, 0xef, 0xbe, 0xef };

static void bcm_bt_push_event(USBBcmBtState *s, uint8_t code,
                              const uint8_t *params, uint8_t plen)
{
    unsigned idx;

    if (s->evq_count == BCM_BT_EVQ_LEN) {
        return;
    }
    idx = (s->evq_head + s->evq_count) % BCM_BT_EVQ_LEN;
    s->evq[idx][0] = code;
    s->evq[idx][1] = plen;
    memcpy(&s->evq[idx][2], params, plen);
    s->evq_len[idx] = plen + 2;
    s->evq_count++;
    usb_wakeup(s->intr, 0);
}

static void bcm_bt_command_complete(USBBcmBtState *s, uint16_t opcode,
                                    const uint8_t *ret, uint8_t rlen)
{
    uint8_t p[3 + 16];

    p[0] = 1;
    stw_le_p(&p[1], opcode);
    memcpy(&p[3], ret, rlen);
    bcm_bt_push_event(s, BT_HCI_EVT_COMMAND_COMPLETE, p, 3 + rlen);
}

static void bcm_bt_ram_reset(USBBcmBtState *s)
{
    memset(s->ram_written, 0, sizeof(s->ram_written));
}

/* Params: 4-byte LE address (top byte selects the memory region), then data. */
static uint8_t bcm_bt_ram_write(USBBcmBtState *s, const uint8_t *p, uint8_t plen)
{
    uint32_t offset;
    unsigned len, first, last, g;

    if (plen < 4) {
        return BT_HCI_STATUS_INVALID_PARAMS;
    }
    offset = ldl_le_p(p) & BCM_BT_RAM_ADDR_MASK;
    len = plen - 4;
    if (offset > BCM_BT_RAM_SIZE || len > BCM_BT_RAM_SIZE - offset) {
        return BT_HCI_STATUS_INVALID_PARAMS;
    }
    if (!len) {
        return BT_HCI_STATUS_SUCCESS;
    }
    memcpy(&s->ram[offset], p + 4, len);
    first = offset / BCM_BT_RAM_GRAIN;
    last = (offset + len - 1) / BCM_BT_RAM_GRAIN;
    for (g = first; g <= last; g++) {
        s->ram_written[g / 64] |= 1ull << (g % 64);
    }
    return BT_HCI_STATUS_SUCCESS;
}

/* Params: 4-byte LE address. Launch is accepted only where RAM was written. */
static uint8_t bcm_bt_ram_launch(USBBcmBtState *s, const uint8_t *p, uint8_t plen)
{
    uint32_t offset;
    unsigned g;

    if (plen != 4) {
        return BT_HCI_STATUS_INVALID_PARAMS;
    }
    offset = ldl_le_p(p) & BCM_BT_RAM_ADDR_MASK;
    if (offset >= BCM_BT_RAM_SIZE) {
        return BT_HCI_STATUS_INVALID_PARAMS;
    }
    g = offset / BCM_BT_RAM_GRAIN;
    if (!(s->ram_written[g / 64] & (1ull << (g % 64)))) {
        return BT_HCI_STATUS_INVALID_PARAMS;
    }
    return BT_HCI_STATUS_SUCCESS;
}

static void bcm_bt_hci_command(USBBcmBtState *s, const uint8_t *cmd)
{
    uint16_t opcode = lduw_le_p(cmd);
    uint8_t plen = cmd[2];
    const uint8_t *params = &cmd[3];
    uint8_t ret[16] = { BT_HCI_STATUS_SUCCESS };
    uint8_t rlen = 1;

    trace_bcm_btusb_hci_cmd(opcode);
    switch (opcode) {
    case BT_HCI_RESET:
    case BT_HCI_WRITE_SCAN_ENABLE:
        break;
    case BT_VS_DOWNLOAD_MINIDRIVER:
        bcm_bt_ram_reset(s);
        break;
    case BT_VS_WRITE_RAM:
        ret[0] = bcm_bt_ram_write(s, params, plen);
        break;
    case BT_VS_LAUNCH_RAM:
        ret[0] = bcm_bt_ram_launch(s, params, plen);
        break;
    case BT_HCI_READ_LOCAL_VERSION:
        ret[1] = 0x06;                 /* HCI version 4.0 */
        stw_le_p(&ret[2], 0);          /* HCI revision */
        ret[4] = 0x06;                 /* LMP version */
        stw_le_p(&ret[5], 0x000f);     /* company: Broadcom */
        stw_le_p(&ret[7], 0);          /* LMP subversion */
        rlen = 9;
        break;
    case BT_HCI_READ_LOCAL_FEATURES:
        memset(&ret[1], 0, 8);
        rlen = 9;
        break;
    case BT_HCI_READ_BD_ADDR:
        memcpy(&ret[1], bcm_bt_bd_addr, sizeof(bcm_bt_bd_addr));
        rlen = 1 + sizeof(bcm_bt_bd_addr);
        break;
    default:
        ret[0] = BT_HCI_STATUS_UNKNOWN_CMD;
        break;
    }
    bcm_bt_command_complete(s, opcode, ret, rlen);
}

static void usb_bcm_btusb_handle_reset(USBDevice *dev)
{
    USBBcmBtState *s = USB_BCM_BTUSB(dev);

    s->evq_head = 0;
    s->evq_count = 0;
    s->evq_off = 0;
}

static void usb_bcm_btusb_handle_control(USBDevice *dev, USBPacket *p,
                                         int request, int value, int index,
                                         int length, uint8_t *data)
{
    USBBcmBtState *s = USB_BCM_BTUSB(dev);
    int ret;

    ret = usb_desc_handle_control(dev, p, request, value, index, length, data);
    if (ret >= 0) {
        return;
    }
    if (request != BCM_BT_HCI_COMMAND_REQ || length < 3 ||
        length < 3 + data[2]) {
        p->status = USB_RET_STALL;
        return;
    }
    bcm_bt_hci_command(s, data);
}

static void usb_bcm_btusb_handle_data(USBDevice *dev, USBPacket *p)
{
    USBBcmBtState *s = USB_BCM_BTUSB(dev);
    uint8_t devep = p->ep->nr;
    unsigned idx;
    size_t left;

    switch (p->pid) {
    case USB_TOKEN_IN:
        if (devep == 1) {
            if (!s->evq_count) {
                p->status = USB_RET_NAK;
                return;
            }
            idx = s->evq_head;
            left = s->evq_len[idx] - s->evq_off;
            left = MIN(left, p->iov.size);
            usb_packet_copy(p, &s->evq[idx][s->evq_off], left);
            s->evq_off += left;
            if (s->evq_off == s->evq_len[idx]) {
                s->evq_off = 0;
                s->evq_head = (s->evq_head + 1) % BCM_BT_EVQ_LEN;
                s->evq_count--;
            }
            return;
        }
        if (devep == 2) {
            p->status = USB_RET_NAK;
            return;
        }
        break;
    case USB_TOKEN_OUT:
        if (devep == 2) {
            /* ACL data from the host is accepted and dropped. */
            p->actual_length = p->iov.size;
            return;
        }
        break;
    default:
        break;
    }
    p->status = USB_RET_STALL;
}

static void usb_bcm_btusb_realize(USBDevice *dev, Error **errp)
{
    USBBcmBtState *s = USB_BCM_BTUSB(dev);

    usb_desc_init(dev);
    s->intr = usb_ep_get(dev, USB_TOKEN_IN, 1);
}

enum {
    STR_BCM_BT_MANUFACTURER = 1,
    STR_BCM_BT_PRODUCT,
    STR_BCM_BT_SERIALNUMBER,
};

static const USBDescStrings desc_strings_bcm_bt = {
    [STR_BCM_BT_MANUFACTURER]   = "Broadcom Corp",
    [STR_BCM_BT_PRODUCT]        = "Bluetooth USB Device (QEMU model)",
    [STR_BCM_BT_SERIALNUMBER]   = "1",
};

static const USBDescIface desc_iface_bcm_bt = {
    .bInterfaceNumber       = 0,
    .bNumEndpoints          = 3,
    .bInterfaceClass        = 0xe0,
    .bInterfaceSubClass     = 0x01,
    .bInterfaceProtocol     = 0x01,
    .eps = (USBDescEndpoint[]) {
        {
            .bEndpointAddress = USB_DIR_IN | 0x01,
            .bmAttributes     = USB_ENDPOINT_XFER_INT,
            .wMaxPacketSize   = 16,
            .bInterval        = 1,
        }, {
            .bEndpointAddress = USB_DIR_IN | 0x02,
            .bmAttributes     = USB_ENDPOINT_XFER_BULK,
            .wMaxPacketSize   = 64,
        }, {
            .bEndpointAddress = USB_DIR_OUT | 0x02,
            .bmAttributes     = USB_ENDPOINT_XFER_BULK,
            .wMaxPacketSize   = 64,
        },
    }
};

static const USBDescDevice desc_device_bcm_bt = {
    .bcdUSB                 = 0x0200,
    .bMaxPacketSize0        = 64,
    .bNumConfigurations     = 1,
    .confs = (USBDescConfig[]) {
        {
            .bNumInterfaces      = 1,
            .bConfigurationValue = 1,
            .bmAttributes        = USB_CFG_ATT_ONE,
            .bMaxPower           = 0,
            .nif = 1,
            .ifs = &desc_iface_bcm_bt,
        },
    },
};

static const USBDescIface desc_iface_bcm_bt_high = {
    .bInterfaceNumber       = 0,
    .bNumEndpoints          = 3,
    .bInterfaceClass        = 0xe0,
    .bInterfaceSubClass     = 0x01,
    .bInterfaceProtocol     = 0x01,
    .eps = (USBDescEndpoint[]) {
        {
            .bEndpointAddress = USB_DIR_IN | 0x01,
            .bmAttributes     = USB_ENDPOINT_XFER_INT,
            .wMaxPacketSize   = 16,
            .bInterval        = 1,
        }, {
            .bEndpointAddress = USB_DIR_IN | 0x02,
            .bmAttributes     = USB_ENDPOINT_XFER_BULK,
            .wMaxPacketSize   = 512,
        }, {
            .bEndpointAddress = USB_DIR_OUT | 0x02,
            .bmAttributes     = USB_ENDPOINT_XFER_BULK,
            .wMaxPacketSize   = 512,
        },
    }
};

static const USBDescDevice desc_device_bcm_bt_high = {
    .bcdUSB                 = 0x0200,
    .bMaxPacketSize0        = 64,
    .bNumConfigurations     = 1,
    .confs = (USBDescConfig[]) {
        {
            .bNumInterfaces      = 1,
            .bConfigurationValue = 1,
            .bmAttributes        = USB_CFG_ATT_ONE,
            .bMaxPower           = 0,
            .nif = 1,
            .ifs = &desc_iface_bcm_bt_high,
        },
    },
};

static const USBDesc desc_bcm_bt = {
    .id = {
        .idVendor          = 0x0a5c,
        .idProduct         = 0x21e8,
        .bcdDevice         = 0x0100,
        .iManufacturer     = STR_BCM_BT_MANUFACTURER,
        .iProduct          = STR_BCM_BT_PRODUCT,
        .iSerialNumber     = STR_BCM_BT_SERIALNUMBER,
    },
    .full = &desc_device_bcm_bt,
    .high = &desc_device_bcm_bt_high,
    .str  = desc_strings_bcm_bt,
};

static const VMStateDescription vmstate_usb_bcm_btusb = {
    .name = TYPE_USB_BCM_BTUSB,
    .unmigratable = 1,
};

static void usb_bcm_btusb_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    USBDeviceClass *uc = USB_DEVICE_CLASS(klass);

    uc->realize        = usb_bcm_btusb_realize;
    uc->product_desc   = "Bluetooth USB Device (QEMU model)";
    uc->usb_desc       = &desc_bcm_bt;
    uc->handle_reset   = usb_bcm_btusb_handle_reset;
    uc->handle_control = usb_bcm_btusb_handle_control;
    uc->handle_data    = usb_bcm_btusb_handle_data;
    dc->vmsd = &vmstate_usb_bcm_btusb;
    set_bit(DEVICE_CATEGORY_MISC, dc->categories);
}

static const TypeInfo usb_bcm_btusb_info = {
    .name          = TYPE_USB_BCM_BTUSB,
    .parent        = TYPE_USB_DEVICE,
    .instance_size = sizeof(USBBcmBtState),
    .class_init    = usb_bcm_btusb_class_init,
};

static void usb_bcm_btusb_register_types(void)
{
    type_register_static(&usb_bcm_btusb_info);
}

type_init(usb_bcm_btusb_register_types)
