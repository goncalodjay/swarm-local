import type { Status } from "./types.ts";

const ANSI_SEQUENCE = /\x1b\[[0-9;]*m/g;

export interface StyleOptions {
  colors: boolean;
}

export interface Style {
  bold: (text: string) => string;
  dim: (text: string) => string;
  cyan: (text: string) => string;
  green: (text: string) => string;
  yellow: (text: string) => string;
  red: (text: string) => string;
  magenta: (text: string) => string;
  statusColor: (status: Status) => (text: string) => string;
}

export function createStyle({ colors }: StyleOptions): Style {
  function wrap(code: string, text: string): string {
    if (!colors) return text;
    return `\x1b[${code}m${text}\x1b[0m`;
  }
  const bold = (text: string): string => wrap("1", text);
  const dim = (text: string): string => wrap("2", text);
  const cyan = (text: string): string => wrap("36", text);
  const green = (text: string): string => wrap("32", text);
  const yellow = (text: string): string => wrap("33", text);
  const red = (text: string): string => wrap("31", text);
  const magenta = (text: string): string => wrap("35", text);
  return {
    bold,
    dim,
    cyan,
    green,
    yellow,
    red,
    magenta,
    statusColor: (status: Status) => {
      switch (status) {
        case "working":
          return yellow;
        case "needs-human":
          return red;
        case "finished-idle":
          return green;
        default:
          return dim;
      }
    },
  };
}

export function detectColors(): boolean {
  return typeof process === "undefined" || process.env.NO_COLOR === undefined;
}

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
