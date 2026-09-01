Feature: swarm-init-tui

# swarm-init-tui 1: copies the TUI bundle to the target project
Scenario: swarm-init-tui 1: copies the TUI bundle to the target project
  Given a project directory without SwarmForge installed
  And the swarm-local TUI bundle exists at tui/dist/swarm-tui.js
  When I run swarm-init in the project directory
  Then the file .swarmforge/tui/swarm-tui.js exists in the project directory

# swarm-init-tui 2: ./swarm tui launches the installed bundle
Scenario: swarm-init-tui 2: ./swarm tui launches the installed bundle
  Given a project directory without SwarmForge installed
  And the swarm-local TUI bundle exists at tui/dist/swarm-tui.js
  And SwarmForge is initialized in the project directory
  When I run ./swarm tui
  Then the installed TUI bundle is executed

# swarm-init-tui 3: fails when the TUI bundle is missing
Scenario: swarm-init-tui 3: fails when the TUI bundle is missing
  Given a project directory without SwarmForge installed
  And the swarm-local TUI bundle does not exist
  When I run swarm-init in the project directory
  Then swarm-init fails
  And the error reports that the TUI bundle is missing

# swarm-init-tui 4: refuses to overwrite an existing TUI bundle
Scenario: swarm-init-tui 4: refuses to overwrite an existing TUI bundle
  Given a project directory without SwarmForge installed
  And the swarm-local TUI bundle exists at tui/dist/swarm-tui.js
  And a TUI bundle already exists at .swarmforge/tui/swarm-tui.js
  When I run swarm-init in the project directory
  Then swarm-init fails
  And the existing TUI bundle remains unchanged
