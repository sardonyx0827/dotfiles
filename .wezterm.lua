local wezterm = require 'wezterm'

local config = {}

if wezterm.config_builder then
  config = wezterm.config_builder()
end

config.use_ime = true
-- Acceptable values are SteadyBlock, BlinkingBlock, SteadyUnderline, BlinkingUnderline, SteadyBar, and BlinkingBar.
config.default_cursor_style = 'BlinkingBlock'
-- font (macos: brew install --cask font-ubuntu-mono)
config.font = wezterm.font("Ubuntu Mono", {weight="Medium", stretch="Normal", style="Normal"})
-- font-size (default: 12)
config.font_size = 14
-- window (default: "TITLE | RESIZE")
--config.window_decorations = "TITLE"
-- remove tab bar
config.hide_tab_bar_if_only_one_tab = true
-- background_opacity (no bg image)
config.window_background_opacity = 0.8

return config
