/*
 * Log guest stores into the sdp1406 sys_mem record table and report when
 * execution reaches the sdp_sparsemem handler's udf (BUG) at 0xc04d70e0.
 *
 * Build (QEMU 10.2.1 source tree, plugins enabled):
 *   clang -shared -fPIC -I<qemu>/include/qemu $(pkg-config --cflags glib-2.0) \
 *       -o sysmem_trace.so sysmem_trace.c
 * Run with: -plugin sysmem_trace.so 2>trace.err
 */
#include <stdio.h>
#include <stdint.h>
#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define TBL_BASE 0xc0542b94ULL
#define TBL_SIZE 0x200ULL
#define UDF_PC   0xc04d70e0ULL
#define BSS_CLR_PC 0xc04cf2e0ULL

static uint64_t hits, clr_lo, clr_hi;

static void on_store(unsigned int vcpu, qemu_plugin_meminfo_t info,
                     uint64_t vaddr, void *udata)
{
    (void)vcpu; (void)info;
    if (vaddr < TBL_BASE || vaddr >= TBL_BASE + TBL_SIZE) {
        return;
    }
    if ((uintptr_t)udata == BSS_CLR_PC) {
        if (!clr_lo || vaddr < clr_lo) {
            clr_lo = vaddr;
        }
        if (vaddr > clr_hi) {
            clr_hi = vaddr;
        }
        return;
    }
    hits++;
    fprintf(stderr, "TBL store pc=%#llx addr=%#llx n=%llu\n",
            (unsigned long long)(uintptr_t)udata,
            (unsigned long long)vaddr, (unsigned long long)hits);
}

static void on_udf(unsigned int vcpu, void *udata)
{
    (void)vcpu; (void)udata;
    fprintf(stderr, "BSS-clear stores span %#llx..%#llx\n",
            (unsigned long long)clr_lo, (unsigned long long)clr_hi);
    fprintf(stderr, "UDF reached pc=%#llx tbl_stores=%llu\n",
            (unsigned long long)UDF_PC, (unsigned long long)hits);
}

static void on_tb(qemu_plugin_id_t id, struct qemu_plugin_tb *tb)
{
    (void)id;
    size_t n = qemu_plugin_tb_n_insns(tb);
    for (size_t i = 0; i < n; i++) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        uint64_t pc = qemu_plugin_insn_vaddr(insn);
        qemu_plugin_register_vcpu_mem_cb(insn, on_store,
                                         QEMU_PLUGIN_CB_NO_REGS,
                                         QEMU_PLUGIN_MEM_W,
                                         (void *)(uintptr_t)pc);
        if (pc == UDF_PC) {
            qemu_plugin_register_vcpu_insn_exec_cb(insn, on_udf,
                                                   QEMU_PLUGIN_CB_NO_REGS, NULL);
        }
    }
}

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
                                           const qemu_info_t *info,
                                           int argc, char **argv)
{
    (void)info; (void)argc; (void)argv;
    qemu_plugin_register_vcpu_tb_trans_cb(id, on_tb);
    return 0;
}
