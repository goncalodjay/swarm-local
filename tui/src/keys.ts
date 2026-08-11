import type { Key } from "./types.ts";

const KEY_SEQUENCES: Record<string, Key> = {
  "\u001b[A": "up",
  "\u001b[B": "down",
  "\r": "enter",
  "\n": "enter",
  q: "quit",
};

export function parseKey(data: string): Key | null {
  return KEY_SEQUENCES[data] ?? null;
}