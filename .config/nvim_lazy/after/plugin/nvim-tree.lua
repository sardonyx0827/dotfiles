--- @diagnostic disable: undefined-global
-- is nvim-tree already opened?
local function is_opend()
  local wins = vim.api.nvim_list_wins()

  for _, w in ipairs(wins) do
    local bufname = vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(w))
    if bufname:match("NvimTree_") ~= nil then
      return true
    end
  end

  return false
end
-- if nvim-tree is already opened, focus it
local function focus_tree()
  local wins = vim.api.nvim_list_wins()

  for _, w in ipairs(wins) do
    local bufname = vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(w))
    if bufname:match("NvimTree_") ~= nil then
      vim.api.nvim_set_current_win(w)
      return
    end
  end
end
local function toggle_tree_focus()
  if is_opend() then
    focus_tree()
    vim.cmd("NvimTreeFocus")
  else
    vim.cmd("NvimTreeOpen")
  end
end

vim.keymap.set("n", "<leader>e", toggle_tree_focus,
  { noremap = true, silent = true, desc = "NvimTree - Toggle and focus" })
vim.keymap.set("n", "<leader>te", ":lua require('nvim-tree.api').tree.expand_all()<CR>",
  { noremap = true, silent = true, desc = "NvimTree - expand all" })

-- set termguicolors to enable highlight groups
vim.opt.termguicolors = true

local function move_l()
  vim.cmd("wincmd l")
end
local function tree_on_attach(bufnr)
  local api = require "nvim-tree.api"
  local function opts(desc)
    return { desc = "nvim-tree: " .. desc, buffer = bufnr, noremap = true, silent = true, nowait = true }
  end
  -- default mappings
  api.config.mappings.default_on_attach(bufnr)
  -- float preview
  local FloatPreview = require("float-preview")
  FloatPreview.attach_nvimtree(bufnr)
  -- custom mappings
  vim.keymap.set('n', 'l', api.node.open.edit, opts('Open'))
  vim.keymap.set('n', '<C-l>', api.tree.change_root_to_node, opts('CD'))
  vim.keymap.set('n', '<C-h>', api.tree.change_root_to_parent, opts('Up'))
  --vim.keymap.set('n', '<leader>e', api.tree.close, opts('Close'))
  vim.keymap.set('n', '<leader>e', move_l, opts('Close'))
end

--setup with some options
require("nvim-tree").setup({
  sort_by = "case_sensitive",
  actions = {
    open_file = {
      quit_on_open = true,
    }
  },
  view = {
    relativenumber = true,
    width = 50,
    -- float = { enable = true },
    --side = "left",
  },
  renderer = {
    group_empty = true,
    icons = {
      glyphs = {
        git = {
          unstaged = "!",
          renamed = "»",
          untracked = "?",
          deleted = "✘",
          staged = "✓",
          unmerged = "",
          ignored = "◌",
        },
      },
    },
  },
  filters = {
    dotfiles = false,
    custom = { "node_modules", ".git", ".DS_Store" },
  },
  update_focused_file = {
    enable = true
  },
  -- sync open/close with other tabs
  tab = {
    sync = {
      open = true,
      close = true,
      ignore = { "toggleterm", "NeogitStatus", "DiffviewFilePanel" },
    },
  },

  on_attach = tree_on_attach,
})

local menuCommand = {}
local function actionsMenu(nd)
  local default_options = {
    results_title = "NvimTree",
    finder = require("telescope.finders").new_table {
      results = menuCommand,
      entry_maker = function(menu_item)
        return {
          value = menu_item,
          ordinal = menu_item.name,
          display = menu_item.name,
        }
      end,
    },
    sorter = require("telescope.sorters").get_generic_fuzzy_sorter(),
    attach_mappings = function(prompt_buffer_number)
      local actions = require "telescope.actions"
      -- On item select
      actions.select_default:replace(function()
        -- Closing the picker
        actions.close(prompt_buffer_number)
        -- Executing the callback
        require("telescope.actions.state").get_selected_entry().value.handler(nd)
      end)
      return true
    end,
  }

  -- Opening the menu
  require("telescope.pickers")
      .new({ prompt_title = "Command", layout_config = { width = 0.3, height = 0.5 } }, default_options)
      :find()
end

local api = require "nvim-tree.api"
local tree, fs, node = api.tree, api.fs, api.node

local command = {
  { "<C-]>", tree.change_root_to_node,       "CD" },
  { "<C-e>", node.open.replace_tree_buffer,  "Open: In Place" },
  { "<C-k>", node.show_info_popup,           "Info" },
  { "<C-r>", fs.rename_sub,                  "Rename: Omit Filename" },
  { "<C-t>", node.open.tab,                  "Open: New Tab" },
  { "<C-v>", node.open.vertical,             "Open: Vertical Split" },
  { "<C-x>", node.open.horizontal,           "Open: Horizontal Split" },
  { "<BS>",  node.navigate.parent_close,     "Close Directory" },
  { "<CR>",  node.open.edit,                 "Open" },
  { "<Tab>", node.open.preview,              "Open Preview" },
  { ">",     node.navigate.sibling.next,     "Next Sibling" },
  { "<",     node.navigate.sibling.prev,     "Previous Sibling" },
  { ".",     node.run.cmd,                   "Run Command" },
  { "-",     tree.change_root_to_parent,     "Up" },
  { "a",     fs.create,                      "Create File" },
  { "bd",    api.marks.bulk.delete,          "Delete Bookmarked" },
  { "bt",    api.marks.bulk.trash,           "Trash Bookmarked" },
  { "bmv",   api.marks.bulk.move,            "Move Bookmarked" },
  { "B",     tree.toggle_no_buffer_filter,   "Toggle No Buffer" },
  { "c",     fs.copy.node,                   "Copy" },
  { "C",     tree.toggle_git_clean_filter,   "Toggle Git Clean" },
  { "[c",    node.navigate.git.prev,         "Prev Git" },
  { "]c",    node.navigate.git.next,         "Next Git" },
  { "d",     fs.remove,                      "Delete" },
  { "D",     fs.trash,                       "Trash" },
  { "E",     tree.expand_all,                "Expand All" },
  { "e",     fs.rename_basename,             "Rename: Basename" },
  { "]e",    node.navigate.diagnostics.next, "Next Diagnostic" },
  { "[e",    node.navigate.diagnostics.prev, "Prev Diagnostic" },
  { "F",     api.live_filter.clear,          "Clean Filter" },
  { "f",     api.live_filter.start,          "Filter" },
  { "g?",    tree.toggle_help,               "Help" },
  { "gy",    fs.copy.absolute_path,          "Copy Absolute Path" },
  { "H",     tree.toggle_hidden_filter,      "Toggle Dotfiles" },
  { "I",     tree.toggle_gitignore_filter,   "Toggle Git Ignore" },
  { "J",     node.navigate.sibling.last,     "Last Sibling" },
  { "K",     node.navigate.sibling.first,    "First Sibling" },
  { "m",     api.marks.toggle,               "Toggle Bookmark" },
  { "o",     node.open.edit,                 "Open" },
  { "O",     node.open.no_window_picker,     "Open: No Window Picker" },
  { "p",     fs.paste,                       "Paste" },
  { "P",     node.navigate.parent,           "Parent Directory" },
  { "q",     tree.close,                     "Close" },
  { "r",     fs.rename,                      "Rename" },
  { "R",     tree.reload,                    "Refresh" },
  { "s",     node.run.system,                "Run System" },
  { "S",     tree.search_node,               "Search" },
  { "U",     tree.toggle_custom_filter,      "Toggle Hidden" },
  { "W",     tree.collapse_all,              "Collapse" },
  { "x",     fs.cut,                         "Cut" },
  { "y",     fs.copy.filename,               "Copy Name" },
  { "Y",     fs.copy.relative_path,          "Copy Relative Path" },
}

local function createTreeActions()
  for _, cmd in pairs(command) do
    table.insert(menuCommand, { name = cmd[3], handler = cmd[2] })
  end
end

createTreeActions()
vim.keymap.set("n", "<leader>ta", actionsMenu, { desc = "NvimTree - action menu" })

local M = {}

function M.on_attach(bufnr)
  local opts = function(desc)
    return { desc = "nvim-tree: " .. desc, buffer = bufnr, nowait = true }
  end
  for _, cmd in pairs(command) do
    if (string.len(cmd[1]) > 0) then
      vim.keymap.set("n", cmd[1], cmd[2], opts(cmd[3]))
    end
  end
end

return M
