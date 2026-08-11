# mutation-stamp: sha256=cd0d04a4e4f65226e2853e8ffd4858decf729dec0bc58a97c07c2ca1d231318a
# acceptance-mutation-manifest-begin
# {"version":1,"tested_at":"2026-08-11T17:40:37Z","feature_name":"swarm-tui-navigation","feature_path":"features/swarm-tui-navigation.feature","background_hash":"7a925b72fbdf38be1ef001303447a26e1c90f13739252ce306ea7407c19e552b","implementation_hash":"unknown","scenarios":[{"index":3,"name":"swarm-tui-navigation 4: tab cycles focus through panels","scenario_hash":"5d56c89cf0fa8d31d9b1fff484a81d3648279743387eda10e85e5ced5dda5292","mutation_count":8,"result":{"Total":8,"Killed":8,"Survived":0,"Errors":0},"tested_at":"2026-08-11T17:39:13Z"},{"index":4,"name":"swarm-tui-navigation 5: vim motions move selection","scenario_hash":"5c0ba212e3cb807a096c62c4ad0665106f5b98b76d9b0803565200c3895d53b2","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-11T17:39:13Z"},{"index":5,"name":"swarm-tui-navigation 6: jump to first and last agent","scenario_hash":"af07b59eb25f3624ea63191ff68ed126380451832064f08de5ff4eecf0699bd2","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-11T17:39:13Z"},{"index":6,"name":"swarm-tui-navigation 7: menu bar navigation wraps","scenario_hash":"2c9087006501ce6db9e4cd1879999a32a686791bd839671a492c72b42ef3696d","mutation_count":12,"result":{"Total":12,"Killed":12,"Survived":0,"Errors":0},"tested_at":"2026-08-11T17:39:13Z"},{"index":7,"name":"swarm-tui-navigation 8: disabled menu item shows hint","scenario_hash":"922392b46d1720f274832bee2dddd1dde9ea074966a4360e16f3debcd1e62d38","mutation_count":2,"result":{"Total":2,"Killed":2,"Survived":0,"Errors":0},"tested_at":"2026-08-11T17:39:13Z"},{"index":9,"name":"swarm-tui-navigation 10: help overlay lists keybindings","scenario_hash":"3e15cdf88efef1cedd554cbfdfa0e8f7d9dcc97003ba7eb944799bc697a5f217","mutation_count":5,"result":{"Total":5,"Killed":5,"Survived":0,"Errors":0},"tested_at":"2026-08-11T17:35:26Z"}]}
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

# swarm-tui-navigation 4: tab cycles focus through panels
Scenario Outline: swarm-tui-navigation 4: tab cycles focus through panels
  Given the TUI is showing the dashboard
  And I press ctrl+k
  When I press tab <presses> times
  Then the focus is on <panel>

Examples:
  | presses | panel   |
  | 1       | agents  |
  | 2       | detail  |
  | 3       | menu    |
  | 4       | agents  |

# swarm-tui-navigation 5: vim motions move selection
Scenario Outline: swarm-tui-navigation 5: vim motions move selection
  Given the selection is on <from_agent>
  When I press <key>
  Then the selection moves to <to_agent>

Examples:
  | from_agent | key | to_agent  |
  | specifier  | j   | coder     |
  | coder      | k   | specifier |
  | architect  | j   | architect |
  | specifier  | k   | specifier |

# swarm-tui-navigation 6: jump to first and last agent
Scenario Outline: swarm-tui-navigation 6: jump to first and last agent
  Given the selection is on <from_agent>
  When I press <key>
  Then the selection moves to <to_agent>

Examples:
  | from_agent | key  | to_agent  |
  | architect  | Home | specifier |
  | architect  | g    | specifier |
  | specifier  | End  | architect |
  | specifier  | G    | architect |

# swarm-tui-navigation 7: menu bar navigation wraps
Scenario Outline: swarm-tui-navigation 7: menu bar navigation wraps
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

# swarm-tui-navigation 8: disabled menu item shows hint
Scenario Outline: swarm-tui-navigation 8: disabled menu item shows hint
  Given the TUI is showing the dashboard
  And the focus is on menu
  And the menu focus is on <item>
  When I press enter
  Then a hint "<item>: not implemented" is shown in the footer

Examples:
  | item  |
  | logs  |
  | costs |

# swarm-tui-navigation 9: help overlay opens and closes
Scenario: swarm-tui-navigation 9: help overlay opens and closes
  Given the TUI is showing the dashboard
  When I press ctrl+k
  And I press ?
  Then the help overlay is shown
  When I press q
  Then the help overlay is closed

# swarm-tui-navigation 10: help overlay lists keybindings
Scenario Outline: swarm-tui-navigation 10: help overlay lists keybindings
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
