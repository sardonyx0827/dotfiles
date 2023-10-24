-- This module contains a number of default definitions

local rainbow_delimiters = require("rainbow-delimiters")

-- Loops through the tree and counts the number or errors.
local function give_up()
  print("give_up")
  local parser = vim.treesitter.get_parser()
  local errors = 0
  parser:for_each_tree(function(tree)
    if tree:root():has_error() then
      errors = errors + 1
    end
  end)
  if errors > 10 then
    return nil
  end
  return rainbow_delimiters.strategy.global
end

vim.g.rainbow_delimiters = {
  strategy = {
    [""] = rainbow_delimiters.strategy["global"],
    --vim = give_up,
  },
  query = {
    [""] = "rainbow-delimiters",
    lua = "rainbow-blocks",
  },
  highlight = {
    "RainbowDelimiterYellow",
    "RainbowDelimiterBlue",
    "RainbowDelimiterOrange",
    "RainbowDelimiterGreen",
    "RainbowDelimiterViolet",
    "RainbowDelimiterCyan",
    --"RainbowDelimiterRed",
  },
}
