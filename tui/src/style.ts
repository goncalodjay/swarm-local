import type { Status } from "./types.ts";

const NO_COLOR = typeof process !== "undefined" && process.env.NO_COLOR !== undefined;

function wrap(code: string, text: string): string {
  if (NO_COLOR) return text;
  return `\x1b[${code}m${text}\x1b[0m`;
}

export const ansi = {
  bold: (text: string): string => wrap("1", text),
  dim: (text: string): string => wrap("2", text),
  cyan: (text: string): string => wrap("36", text),
  green: (text: string): string => wrap("32", text),
  yellow: (text: string): string => wrap("33", text),
  red: (text: string): string => wrap("31", text),
  magenta: (text: string): string => wrap("35", text),
};

export function statusColor(status: Status): (text: string) => string {
  switch (status) {
    case "working":
      return ansi.yellow;
    case "needs-human":
      return ansi.red;
    case "finished-idle":
      return ansi.green;
    default:
      return ansi.dim;
  }
}

const ANSI_SEQUENCE = /\x1b\[[0-9;]*m/g;

export function visibleLength(text: string): number {
  return text.replace(ANSI_SEQUENCE, "").length;
}

export function padVisible(text: string, width: number): string {
  const visible = visibleLength(text);
  if (visible >= width) return truncateVisible(text, width);
  return text + " ".repeat(width - visible);
}

export function truncateVisible(text: string, width: number): string {
  const plain = text.replace(ANSI_SEQUENCE, "");
  if (plain.length <= width) return text;
  if (width <= 0) return "";
  return plain.slice(0, Math.max(0, width - 1)) + "…";
}
