import type { Key } from "./types.ts";
import { child } from "./logger.ts";

const log = child("keys");

const KEY_SEQUENCES: Record<string, Key> = {
  "\u001b[A": "up",
  "\u001b[B": "down",
  "\u001b[C": "right",
  "\u001b[D": "left",
  "\u001b[H": "home",
  "\u001b[F": "end",
  "\u001b": "esc",
  "\r": "enter",
  "\n": "enter",
  "\t": "tab",
  "\u000b": "ctrl+k",
  q: "quit",
  j: "j",
  k: "k",
  g: "g",
  G: "G",
  "?": "?",
};

export function parseKey(data: string): Key | null {
  const key = KEY_SEQUENCES[data];
  if (!key) {
    log.debug({ event: "key_unknown", bytes: data.length, hex: toHex(data) }, "unmapped key sequence");
  }
  return key ?? null;
}

function toHex(data: string): string {
  return Array.from(data)
    .map((c) => c.charCodeAt(0).toString(16).padStart(2, "0"))
    .join(" ");
}
/**
 * A key event as OpenTUI reports it.
 *
 * Only the fields the TUI needs are declared, so this stays decoupled from
 * the renderer's own richer type.
 */
export interface ParsedKeyEvent {
  name: string;
  ctrl: boolean;
  shift: boolean;
  sequence?: string;
}

/** Map an OpenTUI key event onto an application key. */
export function keyFromParsed(event: ParsedKeyEvent): Key | null {
  const { name, ctrl, shift } = event;
  if (ctrl) return name === "k" ? "ctrl+k" : null;
  switch (name) {
    case "up":
    case "down":
    case "left":
    case "right":
    case "home":
    case "end":
    case "tab":
      return name;
    case "escape":
      return "esc";
    case "return":
    case "enter":
      return "enter";
    case "q":
      return "quit";
    case "j":
      return "j";
    case "k":
      return "k";
    case "g":
      return shift ? "G" : "g";
    case "G":
      return "G";
    case "?":
      return "?";
    case "/":
      return shift ? "?" : null;
    default:
      log.debug({ event: "key_unmapped", name, ctrl, shift }, "unmapped parsed key");
      return null;
  }
}
