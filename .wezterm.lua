local wezterm = require 'wezterm'

local config = {}

if wezterm.config_builder then
  config = wezterm.config_builder()
end

-- Theme
-- config.color_scheme = 'AdventureTime'
config.window_background_opacity = 0.85
config.font = wezterm.font("Ubuntu Mono", {weight="Medium", stretch="Normal", style="Normal"})




return config
