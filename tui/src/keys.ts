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