# Swarm TUI — Navigation Polish (F2)

## Goal and context

F2 makes the existing dashboard keyboard-fluent without changing its layout or
adding dependencies. The dashboard from F1 already shows the agents panel,
detail pane, menu bar and footer. F2 adds a transient prefix mode, multi-panel
focus, extended motion keys, a navigable menu bar, and a help overlay.

Design guidance is taken from the `tui-design` skill: spatial consistency,
progressive disclosure of keybindings, keyboard-first input, contextual
intelligence in the footer, and graceful handling of unknown keys.

## Decisions settled with the user

### Prefix mode

- `Ctrl+k` enters a **transient prefix mode** (tmux-style). The next key is
  interpreted as a command and then the TUI returns to normal mode.
- `Esc` cancels prefix mode without executing anything. Any unmapped key also
  cancels silently.
- Prefix mode is required because the user runs the TUI inside WezTerm
  (`Ctrl+Tab` / `Ctrl+Shift+Tab` are used by the terminal) and inside `herdr`
  (`Ctrl+b` is the tool prefix). `Ctrl+k` is free in both environments.

### Keys available inside prefix mode

| Key     | Action                                              |
| ------- | --------------------------------------------------- |
| `Tab`   | Cycle focus forward through agents → detail → menu  |
| `?`     | Open the full keybinding help overlay               |
| `q`     | Quit the TUI and restore the terminal               |
| `Esc`   | Cancel prefix mode                                  |

### Normal-mode motion keys

| Key              | Action                                               |
| ---------------- | ---------------------------------------------------- |
| `↑` / `↓`        | Move selection up/down in the agents panel (L0)      |
| `j` / `k`        | Move selection up/down in the agents panel (L1)      |
| `Home` / `g`     | Jump to the first agent                              |
| `End` / `G`      | Jump to the last agent                               |
| `Enter`          | Attach to the selected agent                         |
| `q`              | Quit the TUI                                         |

### Focus model

- Three focus targets: **agents panel**, **detail pane**, **menu bar**.
- The detail pane and menu bar are read-only/navigation targets in F2; their
  content is still rendered the same way.
- Only the agents panel can be the source of an `Enter` attach. If the menu bar
  has focus and the user presses `Enter` on a role item, the TUI moves focus to
  the agents panel and selects that role (attaching still requires a second
  `Enter` in the agents panel).
- Menu bar navigation uses `←` / `→` when the menu has focus. Navigation wraps
  around from the first item to the last and vice versa.
- Disabled menu items (`logs`, `costs`) show a transient footer hint such as
  `"logs: not implemented"` when activated, then return to normal mode.

### Footer

- Normal mode footer: `"↑/↓ select · Enter attach · Ctrl+k menu · q quit"`.
- Prefix mode footer: `"[Tab] cycle focus · [?] help · [q] quit · [Esc] cancel"`.
- Help overlay footer: `"q / Esc close help"`.
- Footer updates are contextual: the TUI shows only the actions available in
  the current mode.

### Help overlay

- Triggered by `Ctrl+k` then `?`.
- Centers a bordered panel over the dashboard.
- Lists L0 and L1 keys, prefix-mode keys, and the current focus target.
- Closes with `q`, `Esc`, or `?`.

### Visual focus indicator

- The focused panel's title/header is rendered with `style.bold(style.cyan(...))`
  (already used for the selected agents-panel header).
- Unfocused panels keep their default header styling.
- The selected agent row inside the agents panel keeps the `>` marker.

## Edge cases and error handling

- Unknown key while in prefix mode: cancel prefix mode, leave the dashboard
  unchanged, restore normal footer.
- Prefix mode with no agents configured: `Tab` still cycles through the three
  panels; agents panel is empty but focusable.
- Menu bar with all items disabled: `←` / `→` still navigate; `Enter` on any
  item shows the not-implemented hint.
- Terminal too small while help overlay is open: keep the help overlay hidden
  or switch to the too-small screen; the brief specifies that the too-small
  screen takes precedence over any dashboard view.
- `Ctrl+k` pressed while attached: ignored; attachment uses tmux keys (`C-b d`).

## Non-functional constraints

- Zero runtime dependencies; TypeScript executed by Node's native type-stripping.
- Tests with the built-in `node:test` runner.
- Keyboard-only input.
- Respect `NO_COLOR` and work in 16-color mode.
- Must not capture `Ctrl+Tab`, `Ctrl+Shift+Tab`, or `Ctrl+b`.
- Must work inside tmux / zellij and over SSH.
- Minimum terminal size remains 100x30; the too-small screen still applies.

## Non-goals (F2)

- Mouse support.
- Search or filter (`/`).
- Command palette (`:`).
- Direct role jump keys (`1`-`4`).
- Responsive layout changes or panel collapsing.
- New views (logs, costs).
- Changing the attach/detach mechanics.
- Animations or transitions.

## Open questions

- None. All scope and keybinding decisions were settled with the user.
