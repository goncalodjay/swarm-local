import type { Role } from "./types.ts";
import { child } from "./logger.ts";

const log = child("roles");

export function parseRoles(text: string): Role[] {
  const roles: Role[] = [];
  for (const [lineIndex, rawLine] of text.split("\n").entries()) {
    const line = rawLine.trim();
    if (line === "" || line.startsWith("#")) continue;
    const fields = line.split("\t");
    const [role, worktreeName, worktreePath, session, displayName, agent, receiveMode, , workspaceId] = fields;
    if (!role || !worktreePath || !session) {
      log.warn(
        { event: "roles_line_skipped", line: lineIndex + 1, line: line },
        "skipping malformed roles.tsv line",
      );
      continue;
    }
    roles.push({
      role,
      worktreeName: worktreeName ?? "",
      worktreePath,
      session,
      workspaceId: workspaceId ?? "",
      displayName: displayName !== undefined && displayName !== "" ? displayName : role,
      agent: agent ?? "",
      receiveMode: receiveMode ?? "task",
    });
  }
  return roles;
}