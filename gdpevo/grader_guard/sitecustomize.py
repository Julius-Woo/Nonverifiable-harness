"""Defense in depth for trusted, inspected host grader subprocesses."""

import sys


def guard(event, args):
    if event.startswith("socket."):
        raise RuntimeError("Grader network access disabled")


sys.addaudithook(guard)
