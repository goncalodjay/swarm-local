# mutation-stamp: sha256=c02bda52e3e7d7c934bbb3584d3eb521c638e15257331df8a738d3385e64d31f
# acceptance-mutation-manifest-begin
# {"version":1,"tested_at":"2026-08-12T21:51:16Z","feature_name":"swarm-tui-attach-diagnostics","feature_path":"features/swarm-tui-attach-diagnostics.feature","background_hash":"7a925b72fbdf38be1ef001303447a26e1c90f13739252ce306ea7407c19e552b","implementation_hash":"unknown","scenarios":[]}
# acceptance-mutation-manifest-end

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

# swarm-tui-attach-diagnostics 9: generic exit with missing session reports root cause
Scenario: swarm-tui-attach-diagnostics 9: generic exit with missing session reports root cause
  Given the TUI is attached to the tmux session swarmforge-coder
  When the tmux client exits with code 1 and empty stderr and the session no longer exists
  Then the log contains an attach_end event for session swarmforge-coder with reason "tmux session swarmforge-coder no longer exists"
  And the log contains an attach_end event for session swarmforge-coder with field session_alive="false"

# swarm-tui-attach-diagnostics 10: generic exit with unavailable socket reports root cause
Scenario: swarm-tui-attach-diagnostics 10: generic exit with unavailable socket reports root cause
  Given the TUI is attached to the tmux session swarmforge-coder
  When the tmux client exits with code 1 and empty stderr and the swarm socket is unavailable
  Then the log contains an attach_end event for session swarmforge-coder with reason matching "tmux socket .* is unavailable"
  And the log contains an attach_end event for session swarmforge-coder with field socket_available="false"

# swarm-tui-attach-diagnostics 11: generic exit with alive session reports client exit status
Scenario: swarm-tui-attach-diagnostics 11: generic exit with alive session reports client exit status
  Given the TUI is attached to the tmux session swarmforge-coder
  When the tmux client exits with code 1 and empty stderr and the session still exists
  Then the log contains an attach_end event for session swarmforge-coder with reason "tmux client exited with status 1"
  And the log contains an attach_end event for session swarmforge-coder with field session_alive="true"
  And the log contains an attach_end event for session swarmforge-coder with field socket_available="true"
