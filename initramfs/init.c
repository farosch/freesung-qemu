/*
 * Freestanding ARM init for the sdp1406 QEMU display probe.
 *
 * It loads the staged HawkM display modules with finit_module(2) and prints
 * each return value to /dev/console. The kernel log then shows whether the
 * drivers probed their DTB nodes. It never exits, so the kernel stays up.
 */

static long sys3(long nr, long a, long b, long c)
{
    register long r7 __asm__("r7") = nr;
    register long r0 __asm__("r0") = a;
    register long r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c;

    __asm__ volatile("svc #0"
                     : "+r"(r0)
                     : "r"(r7), "r"(r1), "r"(r2)
                     : "memory");
    return r0;
}

#define NR_WRITE         4
#define NR_OPEN          5
#define NR_CLOSE         6
#define NR_NANOSLEEP   162
#define NR_FINIT_MODULE 379

static unsigned long str_len(const char *s)
{
    unsigned long n = 0;

    while (s[n]) {
        n++;
    }
    return n;
}

static void put(const char *s)
{
    sys3(NR_WRITE, 1, (long)s, (long)str_len(s));
}

static void put_num(long v)
{
    char buf[16];
    int i = sizeof(buf) - 1;
    unsigned long u = v < 0 ? -v : v;

    buf[i] = '\0';
    do {
        buf[--i] = '0' + (u % 10);
        u /= 10;
    } while (u);
    if (v < 0) {
        buf[--i] = '-';
    }
    put(&buf[i]);
}

static long load_module(const char *path, const char *name)
{
    long fd;
    long ret;

    put("init: ");
    put(name);
    put(" ");
    fd = sys3(NR_OPEN, (long)path, 0 /* O_RDONLY */, 0);
    if (fd < 0) {
        put("open failed ");
        put_num(fd);
        put("\n");
        return fd;
    }
    ret = sys3(NR_FINIT_MODULE, fd, (long)"", 0);
    sys3(NR_CLOSE, fd, 0, 0);
    put("finit_module=");
    put_num(ret);
    put("\n");
    return ret;
}

void main(void)
{
    /* sdp-drm first: it owns the mixer; hdmi and the bootlogo follow. */
    load_module("/modules/sdp-drm.ko", "sdp-drm");
    load_module("/modules/sdp_hdmi.ko", "sdp_hdmi");
    load_module("/modules/sdp-logo.ko", "sdp-logo");
    put("init: done, idling\n");

    for (;;) {
        long ts[2] = { 60, 0 };
        sys3(NR_NANOSLEEP, (long)ts, 0, 0);
    }
}

__asm__(".section .text._start, \"ax\"\n"
        ".global _start\n"
        "_start:\n"
        "    bl main\n"
        "1:  b 1b\n");
