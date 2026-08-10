export function moveSelection(current: number, key: "up" | "down", count: number): number {
  if (count <= 0) return 0;
  if (key === "up") return Math.max(0, current - 1);
  return Math.min(count - 1, current + 1);
}
