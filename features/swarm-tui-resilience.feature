Feature: swarm-tui-resilience

# swarm-tui-resilience 1: swarm unavailable at startup
Scenario: swarm-tui-resilience 1: swarm unavailable at startup
  Given the swarm socket does not exist
  When the TUI starts
  Then an error screen reports that the swarm is unavailable
  And the TUI does not crash

# swarm-tui-resilience 2: swarm socket lost while running
Scenario: swarm-tui-resilience 2: swarm socket lost while running
  Given a running swarm with the configured roles
  And the TUI is showing the dashboard
  When the swarm socket becomes unreadable
  Then an error screen reports that the swarm is unavailable

# swarm-tui-resilience 3: terminal too small
Scenario Outline: swarm-tui-resilience 3: terminal too small
  Given a running swarm with the configured roles
  And the terminal size is <cols> columns by <rows> rows
  When the TUI renders
  Then a terminal-too-small screen shows the current size <cols>x<rows> and the required size 100x30

Examples:
  | cols | rows |
  | 80   | 24   |
  | 99   | 30   |
  | 100  | 29   |

# swarm-tui-resilience 4: resizing to a valid size restores the dashboard
Scenario: swarm-tui-resilience 4: resizing to a valid size restores the dashboard
  Given a running swarm with the configured roles
  And the terminal size is 80 columns by 24 rows
  And the TUI shows the terminal-too-small screen
  When the terminal is resized to 120 columns by 40 rows
  Then the TUI renders the dashboard

# swarm-tui-resilience 5: missing or malformed agent state
Scenario: swarm-tui-resilience 5: missing or malformed agent state
  Given a running swarm with the configured roles
  And the state files for the agent coder are missing or malformed
  When the TUI renders the agents panel
  Then the coder row shows a blank status
  And the TUI does not crash
