"""Privileged, original-image mount helper; invoked only by the controller.

Imports and runtime directories come from the original image before setns.
The control directory is opened before chroot and never mounted in the solver.
"""

import ctypes
import glob
import json
import os
import re
import signal
import stat
import time


def main():
    control = os.open("/nvh-control", os.O_RDONLY | os.O_DIRECTORY)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0):  # PR_SET_DUMPABLE
        raise OSError(ctypes.get_errno(), "Protect trusted helper process")
    mount = libc.mount
    mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_char_p,
    ]
    audit = {"kept_services": [], "stopped_processes": [], "mounts": []}
    stopped, mounted = [], []
    initial_pids = set()
    marker = "NVH_GRADER_ONLY_" + os.urandom(16).hex()

    def save(name, value):
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, dir_fd=control
        )
        os.fchown(fd, os.fstat(control).st_uid, os.fstat(control).st_gid)
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)

    def kill_verifier_processes():
        for _ in range(5):
            alive = False
            for proc in glob.glob("/proc/[0-9]*"):
                pid = int(proc.rsplit("/", 1)[1])
                if pid not in initial_pids:
                    try:
                        state = (
                            open(proc + "/stat")
                            .read()
                            .rsplit(")", 1)[1]
                            .split()[0]
                        )
                        if state != "Z":
                            os.kill(pid, signal.SIGKILL)
                            alive = True
                    except (ProcessLookupError, FileNotFoundError):
                        pass
            if not alive:
                return
            time.sleep(0.02)
        raise RuntimeError("Verifier processes did not terminate")

    def bind(source, target):
        if libc.syscall(429, source, b"", -100, target.encode(), 4):
            raise OSError(ctypes.get_errno(), "bind " + target)
        mounted.append(target)
        if mount(None, target.encode(), None, 4096 | 32 | 1, None):
            raise OSError(ctypes.get_errno(), "readonly " + target)
        audit["mounts"].append({"target": target, "readonly": True})

    # Resolve merged-/usr symlinks in the pristine root, never the solver root.
    paths = ["/bin", "/usr", "/sbin"] + glob.glob("/lib*")
    paths += glob.glob("/etc/ld.so*")
    paths += json.loads(os.environ.get("NVH_INTERPRETERS", "[]"))
    aliases = {p: os.path.realpath(p) for p in paths if os.path.exists(p)}
    paths = sorted(set(aliases.values()))
    paths = [
        p
        for p in paths
        if not any(p.startswith(q + "/") for q in paths if q != p)
    ]

    def clone(path):
        fd = libc.syscall(428, -100, path.encode(), 1 | os.O_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "open_tree " + path)
        return fd

    sources = [(p, clone(p)) for p in paths]
    empty = clone("/dev/null")
    deps = clone("/nvh-deps") if os.path.isdir("/nvh-deps") else None
    pattern = os.environ.get("NVH_SERVICE_PATTERN", "")
    original_root = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    namespace = os.open("/proc/1/ns/mnt", os.O_RDONLY)
    try:
        # A stable process sweep stops forks before exposing any hidden input.
        for _ in range(20):
            listeners = set()
            for protocol in ("tcp", "tcp6", "udp", "udp6"):
                for line in open("/proc/1/net/" + protocol).readlines()[1:]:
                    fields = line.split()
                    if fields[3] == "0A" or protocol.startswith("udp"):
                        listeners.add(fields[9])
            for line in open("/proc/1/net/unix").readlines()[1:]:
                fields = line.split()
                if fields[3] == "00010000":
                    listeners.add(fields[6])
            found = False
            for proc in glob.glob("/proc/[0-9]*"):
                pid = int(proc.rsplit("/", 1)[1])
                if pid == os.getpid() or pid in stopped:
                    continue
                try:
                    command = (
                        open(proc + "/cmdline", "rb")
                        .read()
                        .replace(b"\0", b" ")
                        .decode(errors="replace")
                    )
                    sockets = []
                    for fd in glob.glob(proc + "/fd/*"):
                        try:
                            link = os.readlink(fd)
                            if (
                                link.startswith("socket:[")
                                and link[8:-1] in listeners
                            ):
                                sockets.append(link)
                        except OSError:
                            pass
                    service = bool(
                        sockets or (pattern and re.search(pattern, command))
                    )
                    item = {
                        "pid": pid,
                        "command": command,
                        "listening_sockets": sockets,
                    }
                    if service:
                        if not any(
                            r["pid"] == pid for r in audit["kept_services"]
                        ):
                            audit["kept_services"].append(item)
                    else:
                        os.kill(pid, signal.SIGSTOP)
                        stopped.append(pid)
                        audit["stopped_processes"].append(item)
                        found = True
                except (ProcessLookupError, FileNotFoundError):
                    continue
            if not found:
                break
        else:
            raise RuntimeError("Process sweep did not stabilize")
        initial_pids = {
            int(p.rsplit("/", 1)[1]) for p in glob.glob("/proc/[0-9]*")
        }
        if libc.setns(namespace, 0):
            raise OSError(ctypes.get_errno(), "setns")
        os.chroot("/proc/1/root")
        os.chdir("/")
        for alias, resolved in aliases.items():
            if os.path.realpath(alias) != resolved:
                raise RuntimeError("Runtime alias was replaced: " + alias)
        for target, fd in sources:
            # Refuse replaced runtime paths.
            if os.path.islink(target):
                raise RuntimeError(
                    "Runtime target replaced with symlink: " + target
                )
            bind(fd, target)
        # glibc's optional preload file may be absent in the original image.
        preload = "/etc/ld.so.preload"
        if os.path.lexists(preload):
            if os.path.islink(preload):
                raise RuntimeError("Preload target is a symlink")
            bind(empty, preload)
        for target in ("/tests", "/logs/verifier"):
            if os.path.islink(target):
                raise RuntimeError("Verifier target is a symlink: " + target)
            os.makedirs(target, mode=0o700, exist_ok=True)
            if mount(
                b"tmpfs",
                target.encode(),
                b"tmpfs",
                2 | 4,
                b"size=1536m,mode=0700,uid=0,gid=0",
            ):
                raise OSError(ctypes.get_errno(), "tmpfs " + target)
            mounted.append(target)
        if deps is not None:
            os.mkdir("/tests/.deps", mode=0o700)
            bind(deps, "/tests/.deps")
        os.chown("/tests", 0, 0)
        os.chmod("/tests", 0o700)
        with open("/tests/nvh-isolation-canary.txt", "w") as handle:
            handle.write(marker)
        audit.update(
            ready=True,
            live_state=True,
            hidden_tests_tmpfs=True,
            canary=marker,
            sensitive_service_fds=[],
        )
        save("runtime.json", audit)
        quiesced = None
        while True:
            try:
                fd = os.open("quiesce", os.O_RDONLY, dir_fd=control)
                with os.fdopen(fd) as handle:
                    request = handle.read()
                if request != quiesced:
                    kill_verifier_processes()
                    quiesced = request
                    save("quiesced.json", {"request": request})
            except FileNotFoundError:
                pass
            try:
                os.stat("release", dir_fd=control)
                break
            except FileNotFoundError:
                for service in audit["kept_services"]:
                    for fd in glob.glob(
                        "/proc/" + str(service["pid"]) + "/fd/*"
                    ):
                        try:
                            target = os.readlink(fd)
                        except OSError:
                            continue
                        if target.startswith(("/tests/", "/logs/verifier/")):
                            item = {"pid": service["pid"], "path": target}
                            if item not in audit["sensitive_service_fds"]:
                                audit["sensitive_service_fds"].append(item)
                time.sleep(0.1)
    except BaseException as exc:
        audit.update(error=type(exc).__name__ + ": " + str(exc))
        save("runtime.json", audit)
        raise
    finally:
        # A timed-out docker exec client does not kill container children.
        # Drain verifier descendants before hidden mounts disappear or solver
        # processes resume. Original services retain their original PIDs.
        if initial_pids:
            kill_verifier_processes()
        if audit.get("ready"):
            leaks, scanned, truncated = [], 0, False
            for directory in ("/app", "/tmp", "/root"):
                for parent, dirs, files in os.walk(
                    directory, followlinks=False
                ):
                    dirs[:] = [
                        d
                        for d in dirs
                        if not os.path.islink(os.path.join(parent, d))
                    ]
                    for name in files:
                        path = os.path.join(parent, name)
                        try:
                            info = os.stat(path, follow_symlinks=False)
                            if not stat.S_ISREG(info.st_mode):
                                continue
                            size = info.st_size
                            if os.path.islink(path) or size > 5 * 1024**2:
                                continue
                            if scanned + size > 128 * 1024**2:
                                truncated = True
                                continue
                            with open(path, "rb") as handle:
                                if marker.encode() in handle.read():
                                    leaks.append(path)
                            scanned += size
                        except (OSError, ValueError):
                            continue
            audit["canary_leaks"] = leaks
            audit["canary_scan_bytes"] = scanned
            audit["canary_scan_truncated"] = truncated
        for target in reversed(mounted):
            if libc.umount2(target.encode(), 2):
                audit.setdefault("cleanup_errors", []).append(target)
        # Return to the helper root before any final imports or audit I/O.
        os.fchdir(original_root)
        os.chroot(".")
        for pid in stopped:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        audit["released"] = True
        save("runtime.json", audit)


if __name__ == "__main__":
    main()
