export type MotionKey = "up" | "down" | "j" | "k" | "home" | "end" | "g" | "G";

export function moveSelection(current: number, key: MotionKey, count: number): number {
  if (count <= 0) return 0;
  switch (key) {
    case "up":
    case "k":
      return Math.max(0, current - 1);
    case "down":
    case "j":
      return Math.min(count - 1, current + 1);
    case "home":
    case "g":
      return 0;
    case "end":
    case "G":
      return count - 1;
    default:
      return current;
  }
}
