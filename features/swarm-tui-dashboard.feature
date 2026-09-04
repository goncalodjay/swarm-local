# acceptance-mutation-manifest-begin
# {"version":1,"tested_at":"2026-08-12T21:51:31Z","feature_name":"swarm-tui-dashboard","feature_path":"features/swarm-tui-dashboard.feature","background_hash":"7a925b72fbdf38be1ef001303447a26e1c90f13739252ce306ea7407c19e552b","implementation_hash":"unknown","scenarios":[{"index":4,"name":"swarm-tui-dashboard 5: keyboard selection","scenario_hash":"fa99476118c471424e8e0fd5861ea4e3b671f431dbcbb71cdb2a820597020c17","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-12T21:51:31Z"},{"index":1,"name":"swarm-tui-dashboard 2: agent status markers","scenario_hash":"060e76fa1ba08042f133569ff5d7e7d52bd95c95167c37180770f08b3455a6ca","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-11T04:00:59Z"}]}
# acceptance-mutation-manifest-end

Feature: swarm-tui-dashboard

Background:
  Given a running swarm with the configured roles

# swarm-tui-dashboard 1: roster and chrome
Scenario: swarm-tui-dashboard 1: roster and chrome
  When the TUI starts
  Then the agents panel lists the configured roles in order
  And the menu bar shows entries for dashboard, each role, logs and costs
  And the logs and costs entries are shown as disabled
  And the footer shows the available keybindings

# swarm-tui-dashboard 2: agent status markers
Scenario Outline: swarm-tui-dashboard 2: agent status markers
  Given the agent <agent> has the handoff state <state>
  When the TUI renders the agents panel
  Then the <agent> row shows the <marker> marker

Examples:
  | agent      | state                                 | marker  |
  | specifier  | one handoff in process                | spinner |
  | coder      | completed tasks and nothing pending   | dot     |
  | reviewer | nothing completed and nothing pending | blank   |
  | architect  | a pending note to the user            | !       |

# swarm-tui-dashboard 3: working agent shows current task
Scenario: swarm-tui-dashboard 3: working agent shows current task
  Given the agent coder has an in-process handoff for task fix-login
  When the TUI renders the agents panel
  Then the coder row shows the task name fix-login

# swarm-tui-dashboard 4: selected agent session info
Scenario: swarm-tui-dashboard 4: selected agent session info
  Given the agent coder has an in-process handoff for task fix-login
  And the selection is on coder
  When the TUI renders the detail pane
  Then the detail pane shows the task fix-login
  And the detail pane shows the current state
  And the detail pane shows the handoff timestamps
  And the detail pane shows the recent handoff events

# swarm-tui-dashboard 5: keyboard selection
Scenario Outline: swarm-tui-dashboard 5: keyboard selection
  Given the selection is on <from_agent>
  When I press <key>
  Then the selection moves to <to_agent>

Examples:
  | from_agent | key  | to_agent  |
  | specifier  | down | coder     |
  | architect  | down | architect |
  | coder      | up   | specifier |
  | specifier  | up   | specifier |

# swarm-tui-dashboard 6: enter attaches to the selected agent
Scenario: swarm-tui-dashboard 6: enter attaches to the selected agent
  Given the selection is on coder
  And the focus is on agents
  When I press enter
  Then the terminal attaches to the tmux session swarmforge-coder on the swarm socket

# swarm-tui-dashboard 7: detach returns to the dashboard
Scenario: swarm-tui-dashboard 7: detach returns to the dashboard
  Given the TUI is attached to the tmux session swarmforge-coder
  When the tmux client detaches
  Then the TUI renders the dashboard
  And the selection is on coder

# swarm-tui-dashboard 8: quit restores the terminal
Scenario: swarm-tui-dashboard 8: quit restores the terminal
  When I press q
  Then the TUI exits
  And the terminal is restored to its previous state

# swarm-tui-dashboard 9: status refresh follows the poll interval
Scenario: swarm-tui-dashboard 9: status refresh follows the poll interval
  Given the status poll interval is 1 second
  And the agent coder is idle
  When a handoff for task fix-login lands in the coder in-process inbox
  Then the next status poll renders the coder row with a spinner and the task fix-login
