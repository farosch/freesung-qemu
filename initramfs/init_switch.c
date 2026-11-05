/*
 * Freestanding ARM initramfs /init: mount the VDFS root, load the DRM stack from it,
 * then switch root and exec the real /sbin/init.
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

static long sys5(long nr, long a, long b, long c, long d, long e)
{
    register long r7 __asm__("r7") = nr;
    register long r0 __asm__("r0") = a;
    register long r1 __asm__("r1") = b;
    register long r2 __asm__("r2") = c;
    register long r3 __asm__("r3") = d;
    register long r4 __asm__("r4") = e;

    __asm__ volatile("svc #0"
                     : "+r"(r0)
                     : "r"(r7), "r"(r1), "r"(r2), "r"(r3), "r"(r4)
                     : "memory");
    return r0;
}

#define NR_WRITE         4
#define NR_OPEN          5
#define NR_CLOSE         6
#define NR_EXECVE       11
#define NR_CHDIR        12
#define NR_MKNOD        14
#define NR_MOUNT        21
#define NR_MKDIR        39
#define NR_CHROOT       61
#define NR_NANOSLEEP   162
#define NR_FINIT_MODULE 379

#define MS_RDONLY     1
#define MS_MOVE    8192
#define S_IFBLK  0x6000

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

static void idle(void)
{
    for (;;) {
        long ts[2] = { 60, 0 };
        sys3(NR_NANOSLEEP, (long)ts, 0, 0);
    }
}

void main(void)
{
    static const char *const argv[] = { "/sbin/init", 0 };
    static const char *const envp[] = { "PATH=/sbin:/bin:/usr/sbin:/usr/bin", "HOME=/", 0 };
    long ret;

    /* mmcblk0 partition 22 is major 179, minor 22 (new-style dev_t). */
    sys3(NR_MKNOD, (long)"/dev/mmcblk0p22", S_IFBLK | 0600, (179 << 8) | 22);
    sys3(NR_MKDIR, (long)"/newroot", 0755, 0);
    ret = sys5(NR_MOUNT, (long)"/dev/mmcblk0p22", (long)"/newroot", (long)"vdfs", MS_RDONLY, 0);
    if (ret < 0) {
        put("init: root mount failed ");
        put_num(ret);
        put("\n");
        idle();
    }
    sys3(NR_MKDIR, (long)"/newroot/dev", 0755, 0);
    sys5(NR_MOUNT, (long)"devtmpfs", (long)"/newroot/dev", (long)"devtmpfs", 0, 0);

    /* Dependency order: kfactory_drv depends on kfactory_drv_api, sdp-drm on the rest. */
    load_module("/newroot/lib/modules/3.10.30/kernel/kfactory_drv_api.ko", "kfactory_drv_api");
    load_module("/newroot/lib/modules/3.10.30/kernel/kfactory_drv.ko", "kfactory_drv");
    load_module("/newroot/lib/modules/3.10.30/kernel/soc/mali.ko", "mali");
    load_module("/newroot/lib/modules/3.10.30/kernel/soc/sdp-drm.ko", "sdp-drm");

    sys3(NR_CHDIR, (long)"/newroot", 0, 0);
    sys5(NR_MOUNT, (long)".", (long)"/", 0, MS_MOVE, 0);
    sys3(NR_CHROOT, (long)".", 0, 0);
    sys3(NR_CHDIR, (long)"/", 0, 0);
    put("init: switching root to /sbin/init\n");
    ret = sys3(NR_EXECVE, (long)"/sbin/init", (long)argv, (long)envp);
    put("init: execve failed ");
    put_num(ret);
    put("\n");
    idle();
}

__asm__(".section .text._start, \"ax\"\n"
        ".global _start\n"
        "_start:\n"
        "    bl main\n"
        "1:  b 1b\n");
