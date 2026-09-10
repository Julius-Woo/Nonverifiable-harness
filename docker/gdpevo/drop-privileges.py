"""Drop every capability and switch to an unprivileged solver UID."""

import ctypes
import os

libc = ctypes.CDLL(None, use_errno=True)
# PR_SET_NO_NEW_PRIVS; then remove all bounding-set capabilities.
if libc.prctl(38, 1, 0, 0, 0):
    raise OSError(ctypes.get_errno(), "prctl NO_NEW_PRIVS")
for capability in range(41):
    if libc.prctl(24, capability, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "prctl CAPBSET_DROP")
os.setgroups([])
os.setgid(1000)
os.setuid(1000)
os.execvp("sleep", ["sleep", "infinity"])
