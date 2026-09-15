import subprocess


def sh(args):
    return subprocess.run(args, capture_output=True, text=True)


def sh_ok(args):
    return subprocess.run(args, capture_output=True).returncode == 0


def sh_out(args):
    return sh(args).stdout.strip()
