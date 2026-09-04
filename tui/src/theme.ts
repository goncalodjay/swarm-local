/**
 * SwarmForge TUI theme.
 *
 * This is the ONLY file you need to touch to restyle the TUI.
 *
 * How it is organised:
 *
 *   1. PALETTE  - the raw colors. Named by what they look like.
 *   2. TOKENS   - semantic slots. Named by what they mean.
 *   3. THEME    - the object the UI reads. It only ever reads tokens.
 *
 * Widgets never reference the palette directly. They ask for a token like
 * `theme.accentPrimary`, so changing the accent in one place restyles every
 * widget that means "this is interactive".
 *
 * To change the main color, edit PALETTE.violet300 below. Nothing else.
 */

/* ------------------------------------------------------------------ *
 * 1. PALETTE - raw colors
 * ------------------------------------------------------------------ */

const PALETTE = {
  // The main accent: a light violet. This drives the menu, focus rings
  // and every interactive affordance.
  violet300: "#b9a3f5",
  violet400: "#9d80ee",
  violet200: "#d3c4fa",

  // Supporting accent, used where a second interactive color is needed.
  blue300: "#8fb8f9",

  // Neutral ramp, darkest to lightest. Depth comes from these steps.
  ink900: "#16161e",
  ink800: "#1c1c26",
  ink700: "#262633",
  ink600: "#34304a",
  ink400: "#2f2f3f",
  slate400: "#6b7194",
  slate200: "#c8d0e8",
  slate100: "#e8ecfb",

  // Status colors. Meaning, not decoration.
  green: "#9ece6a",
  yellow: "#e0af68",
  red: "#f7768e",
  cyan: "#7dcfff",

  white: "#ffffff",
  gray: "#a0a0a0",
  dimGray: "#707070",
} as const;

/* ------------------------------------------------------------------ *
 * 2. TOKENS - semantic slots
 * ------------------------------------------------------------------ */

export interface Theme {
  /** Page background. */
  bgBase: string;
  /** Panel and widget background, one step above the page. */
  bgSurface: string;
  /** Popups and overlays, one step above panels. */
  bgOverlay: string;
  /** Background of the selected row. */
  bgSelection: string;

  /** Body text. */
  fgDefault: string;
  /** Secondary text: metadata, timestamps, hints. */
  fgMuted: string;
  /** Headers and the focused item. */
  fgEmphasis: string;

  /** Interactive elements and focus. This is the menu color. */
  accentPrimary: string;
  /** Softer variant of the accent, for large fills and titles. */
  accentPrimarySoft: string;
  /** Stronger variant of the accent, for pressed or active states. */
  accentPrimaryStrong: string;
  /** Supporting interactive color. */
  accentSecondary: string;

  /** Idle panel borders. */
  border: string;
  /** Border of the focused panel. */
  borderFocus: string;

  statusSuccess: string;
  statusWarning: string;
  statusError: string;
  statusInfo: string;
}

/** The default dark theme. Light violet accent. */
export const darkTheme: Theme = {
  bgBase: PALETTE.ink900,
  bgSurface: PALETTE.ink800,
  bgOverlay: PALETTE.ink700,
  bgSelection: PALETTE.ink600,

  fgDefault: PALETTE.slate200,
  fgMuted: PALETTE.slate400,
  fgEmphasis: PALETTE.slate100,

  accentPrimary: PALETTE.violet300,
  accentPrimarySoft: PALETTE.violet200,
  accentPrimaryStrong: PALETTE.violet400,
  accentSecondary: PALETTE.blue300,

  border: PALETTE.ink400,
  borderFocus: PALETTE.violet300,

  statusSuccess: PALETTE.green,
  statusWarning: PALETTE.yellow,
  statusError: PALETTE.red,
  statusInfo: PALETTE.cyan,
};

/**
 * Monochrome theme, used when NO_COLOR is set.
 *
 * The interface must stay usable without color, so meaning is carried by
 * glyphs, labels and layout. This theme only removes hue; it keeps the
 * light/dark contrast steps that make the panels readable.
 */
export const monochromeTheme: Theme = {
  bgBase: PALETTE.ink900,
  bgSurface: PALETTE.ink800,
  bgOverlay: PALETTE.ink700,
  bgSelection: PALETTE.ink600,

  fgDefault: PALETTE.gray,
  fgMuted: PALETTE.dimGray,
  fgEmphasis: PALETTE.white,

  accentPrimary: PALETTE.white,
  accentPrimarySoft: PALETTE.gray,
  accentPrimaryStrong: PALETTE.white,
  accentSecondary: PALETTE.gray,

  border: PALETTE.dimGray,
  borderFocus: PALETTE.white,

  statusSuccess: PALETTE.gray,
  statusWarning: PALETTE.gray,
  statusError: PALETTE.white,
  statusInfo: PALETTE.gray,
};

/* ------------------------------------------------------------------ *
 * 3. THEME selection
 * ------------------------------------------------------------------ */

export interface ThemeEnv {
  NO_COLOR?: string | undefined;
}

/**
 * Pick the theme for an environment.
 *
 * Honors NO_COLOR (https://no-color.org): any value, including an empty
 * string, disables color.
 */
export function selectTheme(env: ThemeEnv): Theme {
  return env.NO_COLOR === undefined ? darkTheme : monochromeTheme;
}

import type { Status } from "./types.ts";

/** The color that carries the meaning of an agent status. */
export function statusColor(theme: Theme, status: Status): string {
  switch (status) {
    case "working":
      return theme.statusWarning;
    case "needs-human":
      return theme.statusError;
    case "finished-idle":
      return theme.statusSuccess;
    default:
      return theme.fgMuted;
  }
}
