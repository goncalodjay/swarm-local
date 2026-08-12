Feature: swarm-tui-attach-diagnostics

Background:
  Given a running swarm with the configured roles

# swarm-tui-attach-diagnostics 1: log file is created on startup
Scenario: swarm-tui-attach-diagnostics 1: log file is created on startup
  When the TUI starts
  Then the file .swarmforge/logs/tui.log exists
  And the log contains a tui_start event

# swarm-tui-attach-diagnostics 2: attach start is logged
Scenario: swarm-tui-attach-diagnostics 2: attach start is logged
  Given the selection is on coder
  And the focus is on agents
  When I press enter
  Then the log contains an attach_start event for session swarmforge-coder

# swarm-tui-attach-diagnostics 3: clean detach is logged
Scenario: swarm-tui-attach-diagnostics 3: clean detach is logged
  Given the TUI is attached to the tmux session swarmforge-coder
  When the tmux client detaches
  Then the log contains an attach_end event for session swarmforge-coder with reason ""

# swarm-tui-attach-diagnostics 4: unexpected session end is logged
Scenario: swarm-tui-attach-diagnostics 4: unexpected session end is logged
  Given the TUI is attached to the tmux session swarmforge-coder
  When the tmux session swarmforge-coder ends with the message "server disconnected unexpectedly"
  Then the log contains an attach_end event for session swarmforge-coder with reason "server disconnected unexpectedly"

# swarm-tui-attach-diagnostics 5: socket loss is logged
Scenario: swarm-tui-attach-diagnostics 5: socket loss is logged
  Given the TUI is showing the dashboard
  When the swarm socket becomes unreadable
  Then the log contains a socket_check event reporting unavailable

# swarm-tui-attach-diagnostics 6: dashboard shows persistent attach error banner
Scenario: swarm-tui-attach-diagnostics 6: dashboard shows persistent attach error banner
  Given the TUI is attached to the tmux session swarmforge-coder
  When the tmux session swarmforge-coder ends with the message "server disconnected unexpectedly"
  Then the dashboard shows the error "server disconnected unexpectedly"
  And the error banner remains after the next poll

# swarm-tui-attach-diagnostics 7: error banner clears on escape
Scenario: swarm-tui-attach-diagnostics 7: error banner clears on escape
  Given the TUI is showing the dashboard
  And an attach error "server disconnected unexpectedly" is displayed
  When I press esc
  Then the dashboard does not show the error "server disconnected unexpectedly"

# swarm-tui-attach-diagnostics 8: successful attach clears previous error banner
Scenario: swarm-tui-attach-diagnostics 8: successful attach clears previous error banner
  Given the TUI is showing the dashboard
  And an attach error "server disconnected unexpectedly" is displayed
  And the selection is on coder
  And the focus is on agents
  When I press enter
  Then the dashboard does not show the error "server disconnected unexpectedly"
