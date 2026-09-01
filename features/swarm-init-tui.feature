# mutation-stamp: sha256=d924724bdaf21f70672de1efd694460c8b25476368f63f741e4b161f3d510a88
# acceptance-mutation-manifest-begin
# {"version":1,"tested_at":"2026-09-01T03:09:13Z","feature_name":"swarm-init-tui","feature_path":"features/swarm-init-tui.feature","background_hash":"74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b","implementation_hash":"unknown","scenarios":[]}
# acceptance-mutation-manifest-end

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
