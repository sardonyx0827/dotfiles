local wezterm = require 'wezterm'

local config = {}

if wezterm.config_builder then
  config = wezterm.config_builder()
end

-- font
config.font = wezterm.font("Ubuntu Mono", {weight="Medium", stretch="Normal", style="Normal"})
-- font-size (default: 12)
config.font_size = 12
-- window (default: "TITLE | RESIZE")
config.window_decorations = "RESIZE"
-- remove tab bar
config.hide_tab_bar_if_only_one_tab = true
-- background_opacity (no bg image)
config.window_background_opacity = 0.8

return config
