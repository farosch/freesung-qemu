    .section .text
    .global _start
_start:
    ldr   r0, =0x00190a00          @ UART base (ttyS0 in the DTB)
    mov   r1, #0x3                 @ ULCON: 8N1
    str   r1, [r0, #0x00]
    mov   r1, #0x5                 @ UCON: polled TX and RX
    str   r1, [r0, #0x04]
    ldr   r2, =msg
1:
    ldrb  r1, [r2], #1
    cmp   r1, #0
    beq   2f
    ldr   r3, [r0, #0x10]          @ UTRSTAT
    tst   r3, #0x2                 @ transmitter empty
    beq   1b
    str   r1, [r0, #0x20]          @ UTXH
    b     1b
2:
    b     2b

    .section .rodata
msg:
    .asciz "SDP1406 UART OK\n"
