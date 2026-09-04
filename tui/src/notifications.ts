import { spawn } from "node:child_process";
import { child } from "./logger.ts";

const log = child("notifications");

export function isNotifyEnabled(): boolean {
  return process.env.SWARMTUI_NOTIFY !== "0";
}

export function ringBell(): void {
  try {
    process.stderr.write("\x07");
  } catch (err) {
    log.warn({ event: "bell_failed", err: errMessage(err) }, "cannot ring terminal bell");
  }
}

export function buildNotifyCommand(platform: NodeJS.Platform, title: string, body: string): string[] | null {
  if (platform === "darwin") {
    const script = `display notification ${osascriptQuote(body)} with title ${osascriptQuote(title)} sound name "Submarine"`;
    return ["osascript", "-e", script];
  }
  if (platform === "linux") {
    return ["notify-send", "-u", "critical", "-t", "0", title, body];
  }
  if (platform === "win32") {
    const ps =
      `Add-Type -AssemblyName System.Windows.Forms; ` +
      `Add-Type -AssemblyName System.Drawing; ` +
      `$n = New-Object System.Windows.Forms.NotifyIcon; ` +
      `$n.Icon = [System.Drawing.SystemIcons]::Warning; ` +
      `$n.Visible = $true; ` +
      `$n.ShowBalloonTip(5000, ${psQuote(title)}, ${psQuote(body)}, [System.Windows.Forms.ToolTipIcon]::Warning)`;
    return ["powershell", "-NoProfile", "-Command", ps];
  }
  return null;
}

export function systemNotify(title: string, body: string): void {
  if (!isNotifyEnabled()) {
    log.debug({ event: "notify_skipped", reason: "disabled" }, "notification disabled");
    return;
  }
  const cmd = buildNotifyCommand(process.platform, title, body);
  if (!cmd) {
    log.warn({ event: "notify_unsupported_platform", platform: process.platform }, "no notifier for platform");
    return;
  }
  try {
    const proc = spawn(cmd[0], cmd.slice(1), { stdio: "ignore", detached: true });
    proc.on("error", (err) => {
      log.warn({ event: "notify_spawn_error", cmd: cmd[0], err: errMessage(err) }, "notifier failed");
    });
    proc.unref();
  } catch (err) {
    log.warn({ event: "notify_spawn_failed", cmd: cmd[0], err: errMessage(err) }, "cannot spawn notifier");
  }
}

export function alertNeedsHuman(role: string): void {
  log.info({ event: "needs_human_alert", role }, "agent blocked, alerting operator");
  ringBell();
  systemNotify("swarm-tui", `${role} is waiting for your input`);
}

function osascriptQuote(value: string): string {
  return `"${value.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

function psQuote(value: string): string {
  return `'${value.replaceAll("'", "''")}'`;
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
