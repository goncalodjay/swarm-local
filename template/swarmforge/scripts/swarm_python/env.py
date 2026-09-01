import os
import shlex


def shell_quote(value):
    return shlex.quote(str(value))


def env_long(name, default):
    value = os.environ.get(name)
    if value is not None and value.isdigit():
        return int(value)
    return default
