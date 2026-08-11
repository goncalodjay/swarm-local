import type { Role } from "./types.ts";

export const MENU_DASHBOARD = "dashboard";
export const MENU_LOGS = "logs";
export const MENU_COSTS = "costs";

export function menuItems(roles: Role[]): string[] {
  return [MENU_DASHBOARD, ...roles.map((role) => role.role), MENU_LOGS, MENU_COSTS];
}

export function isDisabledMenuItem(item: string): boolean {
  return item === MENU_LOGS || item === MENU_COSTS;
}

export function moveMenuFocus(current: number, key: "left" | "right", count: number): number {
  if (count <= 0) return 0;
  if (key === "left") return (current - 1 + count) % count;
  return (current + 1) % count;
}

export function menuItemIndex(items: string[], item: string): number {
  const index = items.indexOf(item);
  return index >= 0 ? index : 0;
}
