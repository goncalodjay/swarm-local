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
