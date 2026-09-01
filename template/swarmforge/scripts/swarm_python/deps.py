import shutil
import sys

from .ansi import RED, RESET


def command_exists(name):
    return shutil.which(name) is not None


def require(command):
    if not command_exists(command):
        print(
            f"{RED}Error:{RESET} '{command}' is required but not installed.",
            file=sys.stderr,
        )
        sys.exit(1)


def check_backends(roles):
    seen = set()
    for r in roles:
        if r.agent not in seen:
            require(r.agent)
            seen.add(r.agent)
