--- @diagnostic disable: undefined-global
--- @diagnostic disable: different-requires

local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.loop.fs_stat(lazypath) then
  vim.fn.system({
    "git",
    "clone",
    "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git",
    "--branch=stable", -- latest stable release
    lazypath,
  })
end
vim.opt.rtp:prepend(lazypath)

vim.g.mapleader = ","

-- Auto-load all plugin configurations
local function load_plugins()
  local plugins = {}
  local plugin_path = vim.fn.stdpath("config") .. "/lua/setup/plugins"

  -- Helper function to scan directory recursively
  local function scan_dir(dir)
    local handle = vim.loop.fs_scandir(dir)
    if not handle then return end

    while true do
      local name, type = vim.loop.fs_scandir_next(handle)
      if not name then break end

      local full_path = dir .. "/" .. name

      if type == "directory" then
        scan_dir(full_path)
      elseif type == "file" and name:match("%.lua$") then
        -- Convert file path to module path
        local module = full_path:gsub(vim.fn.stdpath("config") .. "/lua/", "")
                                 :gsub("%.lua$", "")
                                 :gsub("/", ".")
        local ok, plugin = pcall(require, module)
        if ok and plugin then
          table.insert(plugins, plugin)
        end
      end
    end
  end

  scan_dir(plugin_path)
  return plugins
end

local plugins = load_plugins()

local lazy = require("lazy")
local opts = {}
lazy.setup(plugins, opts)
