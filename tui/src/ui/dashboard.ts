/**
 * OpenTUI view layer.
 *
 * Takes a `FrameModel` and builds the renderable tree. This is the only
 * module that knows about OpenTUI, and the only one that applies color.
 * Every color comes from a theme token; there are no literals here.
 */

import { BoxRenderable, TextAttributes, TextRenderable, type Renderable } from "@opentui/core";
import type { CliRenderer } from "@opentui/core";
import {
  APP_TITLE,
  detailLines,
  detailTitle,
  headerSegments,
  helpLines,
  menuEntries,
  PANEL_WIDTH,
  renderAgentRow,
  renderError,
  renderFooter,
  renderLegend,
  renderTooSmall,
  type DetailLine,
  type FrameModel,
} from "../render.ts";
import { statusColor, type Theme } from "../theme.ts";

/** Minimum content the notice views need, in columns. */
const NOTICE_WIDTH = 60;

interface TextSpec {
  id: string;
  content: string;
  fg: string;
  attributes?: number;
}

function text(renderer: CliRenderer, spec: TextSpec): TextRenderable {
  return new TextRenderable(renderer, {
    id: spec.id,
    content: spec.content,
    fg: spec.fg,
    ...(spec.attributes === undefined ? {} : { attributes: spec.attributes }),
  });
}

/**
 * Border color for a panel: the accent when focused, the idle border
 * otherwise. Focus is never signalled by color alone; the panel title is
 * also emphasised.
 */
function borderColorFor(theme: Theme, focused: boolean): string {
  return focused ? theme.borderFocus : theme.border;
}

function titleColorFor(theme: Theme, focused: boolean): string {
  return focused ? theme.accentPrimary : theme.fgMuted;
}

function buildHeader(renderer: CliRenderer, theme: Theme, model: FrameModel): Renderable {
  const seg = headerSegments(model);
  const row = new BoxRenderable(renderer, {
    id: "header",
    flexDirection: "row",
    height: 1,
    backgroundColor: theme.bgBase,
  });
  const title = text(renderer, {
    id: "header-title",
    content: APP_TITLE,
    fg: theme.accentPrimary,
    attributes: TextAttributes.BOLD,
  });
  title.flexShrink = 0;
  row.add(title);

  // The socket path is the only segment allowed to shrink. Without this,
  // an over-long header steals columns from whichever segment flex picks,
  // which eats the separators and runs the words together.
  const socket = text(renderer, {
    id: "header-socket",
    content: ` · ${seg.socket}`,
    fg: theme.fgMuted,
  });
  socket.flexShrink = 1;
  row.add(socket);

  const tail: Array<[string, string, string]> = [
    ["header-poll", ` · ${seg.poll}`, theme.fgMuted],
    ["header-herdr", ` · ${seg.herdr}`, seg.herdrDegraded ? theme.statusWarning : theme.fgMuted],
    ["header-size", ` · ${seg.size}`, theme.fgMuted],
  ];
  for (const [id, content, fg] of tail) {
    const segment = text(renderer, { id, content, fg });
    segment.flexShrink = 0;
    row.add(segment);
  }
  return row;
}

function buildMenuBar(renderer: CliRenderer, theme: Theme, model: FrameModel): Renderable {
  const row = new BoxRenderable(renderer, {
    id: "menu",
    flexDirection: "row",
    height: 1,
    gap: 1,
    backgroundColor: theme.bgBase,
  });
  for (const [index, entry] of menuEntries(model.roles, model.focus, model.menuFocus).entries()) {
    let fg = theme.accentPrimary;
    let attributes: number | undefined;
    if (entry.active) {
      // The focused entry is the brightest thing on the bar.
      fg = theme.fgEmphasis;
      attributes = TextAttributes.BOLD;
    } else if (entry.disabled) {
      fg = theme.fgMuted;
      attributes = TextAttributes.DIM;
    } else if (entry.isDashboard) {
      attributes = TextAttributes.BOLD;
    }
    row.add(text(renderer, { id: `menu-${index}`, content: entry.text, fg, ...(attributes === undefined ? {} : { attributes }) }));
  }
  return row;
}

function buildAgentsPanel(renderer: CliRenderer, theme: Theme, model: FrameModel): Renderable {
  const focused = model.focus === "agents";
  const box = new BoxRenderable(renderer, {
    id: "agents",
    width: PANEL_WIDTH,
    border: true,
    borderColor: borderColorFor(theme, focused),
    title: "Agents",
    titleColor: titleColorFor(theme, focused),
    backgroundColor: theme.bgSurface,
    flexDirection: "column",
    paddingLeft: 1,
    paddingRight: 1,
  });
  for (const [index, agent] of model.agents.entries()) {
    const selected = index === model.selection;
    const row = new BoxRenderable(renderer, {
      id: `agent-row-${index}`,
      height: 1,
      backgroundColor: selected ? theme.bgSelection : theme.bgSurface,
    });
    row.add(text(renderer, {
      id: `agent-${index}`,
      content: renderAgentRow(agent, selected),
      fg: selected ? theme.fgEmphasis : theme.fgDefault,
      ...(selected ? { attributes: TextAttributes.BOLD } : {}),
    }));
    box.add(row);
  }
  return box;
}

function detailLineColor(theme: Theme, line: DetailLine): string {
  if (line.tone === "status" && line.status !== undefined) return statusColor(theme, line.status);
  if (line.tone === "muted") return theme.fgMuted;
  return theme.fgDefault;
}

function buildDetailPanel(renderer: CliRenderer, theme: Theme, model: FrameModel): Renderable {
  const focused = model.focus === "detail";
  const agent = model.agents[model.selection] ?? null;
  const box = new BoxRenderable(renderer, {
    id: "detail",
    flexGrow: 1,
    border: true,
    borderColor: borderColorFor(theme, focused),
    title: detailTitle(agent),
    titleColor: titleColorFor(theme, focused),
    backgroundColor: theme.bgSurface,
    flexDirection: "column",
    paddingLeft: 1,
    paddingRight: 1,
  });
  for (const [index, line] of detailLines(agent).entries()) {
    box.add(text(renderer, {
      id: `detail-${index}`,
      content: line.text,
      fg: detailLineColor(theme, line),
      ...(line.tone === "muted" ? { attributes: TextAttributes.DIM } : {}),
    }));
  }
  return box;
}

function buildFooter(renderer: CliRenderer, theme: Theme, model: FrameModel): Renderable {
  const box = new BoxRenderable(renderer, {
    id: "footer",
    flexDirection: "column",
    backgroundColor: theme.bgBase,
  });
  if (model.attachError) {
    box.add(text(renderer, {
      id: "attach-error",
      content: model.attachError,
      fg: theme.statusError,
      attributes: TextAttributes.BOLD,
    }));
  }
  box.add(text(renderer, {
    id: "legend",
    content: renderLegend(),
    fg: theme.fgMuted,
    attributes: TextAttributes.DIM,
  }));
  box.add(text(renderer, {
    id: "footer-keys",
    content: renderFooter(model.mode, model.hint, model.helpOpen),
    fg: model.hint === null ? theme.fgDefault : theme.accentSecondary,
  }));
  return box;
}

const HELP_WIDTH = 46;

function buildHelpOverlay(renderer: CliRenderer, theme: Theme, model: FrameModel): Renderable {
  const lines = helpLines(model.focus);
  const height = lines.length + 2;
  const width = Math.min(HELP_WIDTH, model.terminalSize.cols - 4);
  const box = new BoxRenderable(renderer, {
    id: "help",
    position: "absolute",
    left: Math.max(0, Math.floor((model.terminalSize.cols - width) / 2)),
    top: Math.max(0, Math.floor((model.terminalSize.rows - height) / 2)),
    width,
    height,
    zIndex: 100,
    border: true,
    borderColor: theme.accentPrimary,
    title: "Keys",
    titleColor: theme.accentPrimary,
    backgroundColor: theme.bgOverlay,
    flexDirection: "column",
    paddingLeft: 1,
    paddingRight: 1,
  });
  for (const [index, line] of lines.entries()) {
    box.add(text(renderer, {
      id: `help-${index}`,
      content: line,
      fg: index === 0 ? theme.accentPrimary : theme.fgDefault,
      ...(index === 0 ? { attributes: TextAttributes.BOLD } : {}),
    }));
  }
  return box;
}

function buildNotice(
  renderer: CliRenderer,
  theme: Theme,
  title: string,
  body: string[],
  accent: string,
): Renderable {
  const box = new BoxRenderable(renderer, {
    id: "notice",
    width: NOTICE_WIDTH,
    border: true,
    borderColor: accent,
    title,
    titleColor: accent,
    backgroundColor: theme.bgSurface,
    flexDirection: "column",
    paddingLeft: 1,
    paddingRight: 1,
  });
  for (const [index, line] of body.entries()) {
    box.add(text(renderer, { id: `notice-${index}`, content: line, fg: theme.fgDefault }));
  }
  box.add(text(renderer, {
    id: "notice-footer",
    content: renderFooter("normal", null, false),
    fg: theme.fgMuted,
    attributes: TextAttributes.DIM,
  }));
  return box;
}

/**
 * Build the whole frame for a model.
 *
 * The caller owns the returned renderable and must destroy it before
 * building the next frame.
 */
export function buildFrame(renderer: CliRenderer, theme: Theme, model: FrameModel): Renderable {
  if (model.view === "error") {
    const [title, , ...rest] = renderError(model.errorMessage);
    return buildNotice(renderer, theme, title ?? "Error", rest, theme.statusError);
  }
  if (model.view === "too-small") {
    const [title, , ...rest] = renderTooSmall(model.terminalSize, model.requiredSize);
    return buildNotice(renderer, theme, title ?? "Too small", rest, theme.statusWarning);
  }

  const root = new BoxRenderable(renderer, {
    id: "frame",
    flexGrow: 1,
    flexDirection: "column",
    backgroundColor: theme.bgBase,
    paddingLeft: 1,
    paddingRight: 1,
  });
  root.add(buildHeader(renderer, theme, model));
  root.add(buildMenuBar(renderer, theme, model));

  const body = new BoxRenderable(renderer, {
    id: "body",
    flexDirection: "row",
    flexGrow: 1,
    gap: 1,
    backgroundColor: theme.bgBase,
  });
  body.add(buildAgentsPanel(renderer, theme, model));
  body.add(buildDetailPanel(renderer, theme, model));
  root.add(body);

  root.add(buildFooter(renderer, theme, model));

  if (model.helpOpen) root.add(buildHelpOverlay(renderer, theme, model));
  return root;
}

/**
 * Owns the single frame currently mounted on the renderer root.
 *
 * OpenTUI is retained mode, so the previous frame is destroyed before the
 * next one is mounted. The TUI repaints once per poll and once per
 * keypress, which is far below the rate where rebuilding would matter.
 */
export class FrameMount {
  private current: Renderable | null = null;

  constructor(
    private readonly renderer: CliRenderer,
    private readonly theme: Theme,
  ) {}

  update(model: FrameModel): void {
    const next = buildFrame(this.renderer, this.theme, model);
    const previous = this.current;
    this.renderer.root.add(next);
    this.current = next;
    if (previous) this.detach(previous);
  }

  destroy(): void {
    if (!this.current) return;
    this.detach(this.current);
    this.current = null;
  }

  /**
   * Unmount a frame.
   *
   * The renderer detaches its own children while shutting down, so the
   * frame may already be gone by the time this runs. Removing it again
   * would log a spurious warning, hence the membership check.
   */
  private detach(frame: Renderable): void {
    if (this.renderer.root.getChildren().includes(frame)) {
      this.renderer.root.remove(frame);
    }
    frame.destroyRecursively();
  }
}
