# mutation-stamp: sha256=31dd5906cf5f6cae9f354fa3e2e0f253964565ce22185fc853082072774dfb02
# acceptance-mutation-manifest-begin
# {"version":1,"tested_at":"2026-08-11T17:40:45Z","feature_name":"swarm-tui-resilience","feature_path":"features/swarm-tui-resilience.feature","background_hash":"74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b","implementation_hash":"unknown","scenarios":[{"index":2,"name":"swarm-tui-resilience 3: terminal too small","scenario_hash":"9a15ecc3186b817abdcb0bb103f6e245c06aceb3c6d05bfb84c9ac7deda6da5d","mutation_count":6,"result":{"Total":6,"Killed":6,"Survived":0,"Errors":0},"tested_at":"2026-08-11T04:01:06Z"}]}
# acceptance-mutation-manifest-end

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

# swarm-tui-resilience 6: agent session ends unexpectedly while attached
Scenario: swarm-tui-resilience 6: agent session ends unexpectedly while attached
  Given a running swarm with the configured roles
  And the TUI is attached to the tmux session swarmforge-coder
  When the tmux session swarmforge-coder ends with the message "server disconnected unexpectedly"
  Then the TUI renders the dashboard
  And an error message shows "server disconnected unexpectedly"
