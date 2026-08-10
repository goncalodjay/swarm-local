import type { Role } from "./types.ts";

export function parseRoles(text: string): Role[] {
  const roles: Role[] = [];
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (line === "" || line.startsWith("#")) continue;
    const [role, worktreeName, worktreePath, session, displayName, agent, receiveMode] = line.split("\t");
    if (!role || !worktreePath || !session) continue;
    roles.push({
      role,
      worktreeName: worktreeName ?? "",
      worktreePath,
      session,
      displayName: displayName !== undefined && displayName !== "" ? displayName : role,
      agent: agent ?? "",
      receiveMode: receiveMode ?? "task",
    });
  }
  return roles;
}
