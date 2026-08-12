# acceptance-mutation-manifest-begin
# {"version":1,"tested_at":"2026-08-12T21:51:30Z","feature_name":"swarm-tui-navigation","feature_path":"features/swarm-tui-navigation.feature","background_hash":"7a925b72fbdf38be1ef001303447a26e1c90f13739252ce306ea7407c19e552b","implementation_hash":"unknown","scenarios":[{"index":5,"name":"swarm-tui-navigation 6: vim motions move selection","scenario_hash":"ac337947ec3dd4a88f7ad322d7512a2626a7596ea21679ca7b731bb9684d9a3c","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-12T21:51:30Z"},{"index":6,"name":"swarm-tui-navigation 7: jump to first and last agent","scenario_hash":"e1ce734fb43b250a158d405a1ec16e30d166bdbd777a41e8653cf7488a88eb5a","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-12T21:51:30Z"},{"index":7,"name":"swarm-tui-navigation 8: menu bar navigation wraps","scenario_hash":"0a5cd7c1384adf0909839f881db06120b4593275df4fb90ed84e20ef3b17e1ab","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-12T21:51:30Z"},{"index":8,"name":"swarm-tui-navigation 9: disabled menu item shows hint","scenario_hash":"792a0876a389bdf417eec7d5595d557b64261120e8c431aa78e373bfa099ff87","mutation_count":2,"result":{"Total":2,"Killed":2,"Survived":0,"Errors":0},"tested_at":"2026-08-12T21:51:30Z"},{"index":10,"name":"swarm-tui-navigation 11: help overlay lists keybindings","scenario_hash":"4f7255bfc592aeaced126285fcc00b3d9a615e2df988f2b719b10cf70924cbd1","mutation_count":5,"result":{"Total":5,"Killed":5,"Survived":0,"Errors":0},"tested_at":"2026-08-12T21:51:30Z"}]}
# acceptance-mutation-manifest-end

Feature: swarm-tui-navigation

Background:
  Given a running swarm with the configured roles

# swarm-tui-navigation 1: normal mode footer
Scenario: swarm-tui-navigation 1: normal mode footer
  When the TUI is showing the dashboard
  Then the footer shows "Ctrl+k menu"
  And the footer shows "↑/↓ select"
  And the footer shows "Enter attach"
  And the footer shows "q quit"

# swarm-tui-navigation 2: prefix mode footer
Scenario: swarm-tui-navigation 2: prefix mode footer
  Given the TUI is showing the dashboard
  When I press ctrl+k
  Then the footer shows "Tab cycle focus"
  And the footer shows "? help"
  And the footer shows "q quit"
  And the footer shows "Esc cancel"

# swarm-tui-navigation 3: escape cancels prefix mode
Scenario: swarm-tui-navigation 3: escape cancels prefix mode
  Given the TUI is showing the dashboard
  And I press ctrl+k
  When I press esc
  Then the footer shows "Ctrl+k menu"

# swarm-tui-navigation 4: startup focus is on the agents panel
Scenario: swarm-tui-navigation 4: startup focus is on the agents panel
  When the TUI starts
  Then the focus is on agents

# swarm-tui-navigation 5: tab cycles focus through panels
Scenario Outline: swarm-tui-navigation 5: tab cycles focus through panels
  Given the TUI is showing the dashboard
  And I press ctrl+k
  When I press tab <presses> times
  Then the focus is on <panel>

Examples:
  | presses | panel   |
  | 1       | detail  |
  | 2       | menu    |
  | 3       | agents  |
  | 4       | detail  |

# swarm-tui-navigation 6: vim motions move selection
Scenario Outline: swarm-tui-navigation 6: vim motions move selection
  Given the selection is on <from_agent>
  When I press <key>
  Then the selection moves to <to_agent>

Examples:
  | from_agent | key | to_agent  |
  | specifier  | j   | coder     |
  | coder      | k   | specifier |
  | architect  | j   | architect |
  | specifier  | k   | specifier |

# swarm-tui-navigation 7: jump to first and last agent
Scenario Outline: swarm-tui-navigation 7: jump to first and last agent
  Given the selection is on <from_agent>
  When I press <key>
  Then the selection moves to <to_agent>

Examples:
  | from_agent | key  | to_agent  |
  | architect  | Home | specifier |
  | architect  | g    | specifier |
  | specifier  | End  | architect |
  | specifier  | G    | architect |

# swarm-tui-navigation 8: menu bar navigation wraps
Scenario Outline: swarm-tui-navigation 8: menu bar navigation wraps
  Given the TUI is showing the dashboard
  And the focus is on menu
  And the menu focus is on <from_item>
  When I press <key>
  Then the menu focus is on <to_item>

Examples:
  | from_item  | key   | to_item    |
  | dashboard  | left  | costs      |
  | dashboard  | right | specifier  |
  | costs      | right | dashboard  |
  | specifier  | left  | dashboard  |

# swarm-tui-navigation 9: disabled menu item shows hint
Scenario Outline: swarm-tui-navigation 9: disabled menu item shows hint
  Given the TUI is showing the dashboard
  And the focus is on menu
  And the menu focus is on <item>
  When I press enter
  Then a hint "<item>: not implemented" is shown in the footer

Examples:
  | item  |
  | logs  |
  | costs |

# swarm-tui-navigation 10: help overlay opens and closes
Scenario: swarm-tui-navigation 10: help overlay opens and closes
  Given the TUI is showing the dashboard
  When I press ctrl+k
  And I press ?
  Then the help overlay is shown
  When I press q
  Then the help overlay is closed

# swarm-tui-navigation 11: help overlay lists keybindings
Scenario Outline: swarm-tui-navigation 11: help overlay lists keybindings
  Given the TUI is showing the dashboard
  And I press ctrl+k
  And I press ?
  Then the help overlay shows "<key>"

Examples:
  | key     |
  | Ctrl+k  |
  | Tab     |
  | ?       |
  | j/k     |
  | g/G     |
