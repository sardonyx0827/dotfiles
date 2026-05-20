local wezterm = require 'wezterm'

local config = {}

if wezterm.config_builder then
  config = wezterm.config_builder()
end

config.use_ime = true
-- Acceptable values are SteadyBlock, BlinkingBlock, SteadyUnderline, BlinkingUnderline, SteadyBar, and BlinkingBar.
config.default_cursor_style = 'BlinkingBlock'
-- font (macos: brew install --cask font-ubuntu-mono)
config.font = wezterm.font("Ubuntu Mono", { weight = "Medium", stretch = "Normal", style = "Normal" })
config.font = wezterm.font_with_fallback {
  'Ubuntu Mono',
  'Hiragino Sans'
}
-- font-size (default: 12)
config.font_size = 14
-- color scheme
config.color_scheme = 'rose-pine'
-- When set to true, if a glyph cannot be found for a given codepoint, then the configuration error window will be shown with a pointer to the font configuration docs (default: true)
config.warn_about_missing_glyphs = false
-- window (default: "TITLE | RESIZE")
--config.window_decorations = "TITLE"
-- remove tab bar
config.hide_tab_bar_if_only_one_tab = true
-- background_opacity (no bg image)
config.window_background_opacity = 0.9
-- key bindings
config.keys = {
  -- input backslash (macos: option + ¥ -> \)
  { key = "¥", mods = "",    action = wezterm.action.SendString("\\") },
  { key = "¥", mods = "OPT", action = wezterm.action.SendString("¥") },
  { key = 'F10', mods = 'OPT', action = wezterm.action.ToggleFullScreen,
  },
}
return config
