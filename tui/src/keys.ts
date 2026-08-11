import type { Key } from "./types.ts";

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
  return KEY_SEQUENCES[data] ?? null;
}
