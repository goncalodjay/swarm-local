import os
import shlex


# Force UTF-8 for every Python process the swarm starts (daemon, agent panes):
# on Windows the locale codec (cp1252) cannot decode some tool output.
UTF8_ENV = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def utf8_environ(base=None):
    """Copy of `base` (default os.environ) with the UTF-8 variables set."""
    env = dict(os.environ if base is None else base)
    env.update(UTF8_ENV)
    return env


def shell_quote(value):
    return shlex.quote(str(value))


def env_long(name, default):
    value = os.environ.get(name)
    if value is not None and value.isdigit():
        return int(value)
    return default
