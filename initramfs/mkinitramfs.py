#!/usr/bin/env python3
"""Pack a newc initramfs for the sdp1406 display probe.

Usage: mkinitramfs.py OUT.cpio INIT_ELF [MODULE.ko ...]

The archive holds INIT_ELF as /init, the modules under /modules/, and a
/dev/console character node (5:1), which the kernel needs before it can
open the console. Device nodes are written without root.
"""

import os
import stat
import sys


def newc_entry(name, mode, data=b"", rdev=(0, 0)):
    name_bytes = name.encode() + b"\0"
    fields = [
        0,              # ino
        mode,           # mode
        0,              # uid
        0,              # gid
        1,              # nlink
        0,              # mtime
        len(data),      # filesize
        0,              # devmajor
        0,              # devminor
        rdev[0],        # rdevmajor
        rdev[1],        # rdevminor
        len(name_bytes),
        0,              # check
    ]
    header = b"070701" + b"".join(b"%08x" % f for f in fields)
    out = header + name_bytes
    out += b"\0" * ((4 - len(out) % 4) % 4)
    out += data
    out += b"\0" * ((4 - len(out) % 4) % 4)
    return out


def main(argv):
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    out_path, init_path, module_paths = argv[1], argv[2], argv[3:]

    entries = [
        newc_entry("dev", stat.S_IFDIR | 0o755),
        newc_entry("dev/console", stat.S_IFCHR | 0o600, rdev=(5, 1)),
        newc_entry("modules", stat.S_IFDIR | 0o755),
    ]
    with open(init_path, "rb") as f:
        entries.append(newc_entry("init", stat.S_IFREG | 0o755, f.read()))
    for path in module_paths:
        with open(path, "rb") as f:
            data = f.read()
        name = "modules/" + os.path.basename(path)
        entries.append(newc_entry(name, stat.S_IFREG | 0o644, data))
    entries.append(newc_entry("TRAILER!!!", 0))

    with open(out_path, "wb") as f:
        for entry in entries:
            f.write(entry)
    print(f"wrote {out_path} ({os.path.getsize(out_path)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
