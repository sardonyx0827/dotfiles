return {
  "nvim-tree/nvim-tree.lua",
  lazy = false,
  tag = "nvim-tree-v1.15.0",
  cmd = "NvimTreeToggle",
  dependencies = {
    -- show icons with Nerd Font
    "nvim-tree/nvim-web-devicons",
    {
      "JMarkin/nvim-tree.lua-float-preview",
      lazy = false,
      -- default
      opts = {
        -- wrap nvimtree commands
        wrap_nvimtree_commands = true,
        -- lines for scroll
        scroll_lines = 20,
        -- window config
        window = {
          style = "minimal",
          relative = "win",
          border = "rounded",
          wrap = false,
        },
        mapping = {
          -- scroll down float buffer
          down = { "<C-d>" },
          -- scroll up float buffer
          up = { "<C-e>", "<C-u>" },
          -- enable/disable float windows
          toggle = { "<C-p>" },
        },
        -- hooks if return false preview doesn't shown
        hooks = {
          pre_open = function(path)
            -- if file > 5 MB or not text -> not preview
            local size = require("float-preview.utils").get_size(path)
            if type(size) ~= "number" then
              return false
            end
            local is_text = require("float-preview.utils").is_text(path)
            return size < 5 and is_text
          end,
          post_open = function(_)
            return true
          end,
        },
      },
    },
  },
  -- Setup, on_attach mappings and the Telescope action menu formerly lived in
  -- after/plugin/nvim-tree.lua. nvim-tree renders eagerly (lazy = false).
  config = function()
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
    vim.keymap.set("n", "<leader>ct", ":lua require('nvim-tree.api').tree.open({ path = vim.fn.expand('%:p:h') })<CR>",
      { noremap = true, silent = true, desc = "NvimTree - change directory and open nvim-tree" })

    local function diff_with_current_buffer()
      local node = require("nvim-tree.api").tree.get_node_under_cursor()
      if not node or node.type == "directory" then
        vim.notify("Please select a file", vim.log.levels.WARN)
        return
      end
      local tree_file = node.absolute_path

      local current_buf_file = nil
      for _, w in ipairs(vim.api.nvim_list_wins()) do
        local bufname = vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(w))
        if bufname:match("NvimTree_") == nil and bufname ~= "" then
          current_buf_file = bufname
          break
        end
      end

      if not current_buf_file then
        vim.notify("No buffer to diff with", vim.log.levels.WARN)
        return
      end

      require("nvim-tree.api").tree.close()
      vim.cmd("tabnew " .. vim.fn.fnameescape(current_buf_file))
      vim.cmd("diffthis")
      vim.cmd("vsplit " .. vim.fn.fnameescape(tree_file))
      vim.cmd("diffthis")
    end

    local function move_l()
      vim.cmd("wincmd l")
    end

    -- Open every regular file directly under a directory as (unloaded, listed)
    -- buffers. Directory node -> that directory; file node -> its parent
    -- directory. Only direct children are opened (no recursion). The cursor
    -- stays in the tree; `badd` just populates the bufferline (barbar).
    -- Ask for confirmation when CONFIRM_THRESHOLD or more files would open.
    local CONFIRM_THRESHOLD = 10
    local function open_dir_files_as_buffers()
      local api = require("nvim-tree.api")
      local node = api.tree.get_node_under_cursor()
      if not node then
        return
      end

      local dir = node.type == "directory" and node.absolute_path
          or vim.fn.fnamemodify(node.absolute_path, ":h")
      local label = vim.fn.fnamemodify(dir, ":t")

      local uv = vim.uv or vim.loop
      local handle = uv.fs_scandir(dir)
      if not handle then
        vim.notify("nvim-tree: cannot read " .. dir, vim.log.levels.WARN)
        return
      end

      local files = {}
      while true do
        local name, t = uv.fs_scandir_next(handle)
        if not name then
          break
        end
        if name ~= ".DS_Store" then
          local full = dir .. "/" .. name
          if t == "file" then
            files[#files + 1] = full
          elseif t ~= "directory" then
            -- symlink / unknown: resolve target type (fs_stat follows links)
            local st = uv.fs_stat(full)
            if st and st.type == "file" then
              files[#files + 1] = full
            end
          end
        end
      end

      if #files == 0 then
        vim.notify("nvim-tree: no files in " .. label, vim.log.levels.INFO)
        return
      end

      if #files >= CONFIRM_THRESHOLD then
        -- noice.nvim dedups identical consecutive messages (noice/ui/state.lua),
        -- which drops the confirm prompt text on a repeated `L` over the same
        -- dir. Clear the cached msg_show state so the message shows every time.
        pcall(function()
          require("noice.ui.state").clear("msg_show")
        end)
        local choice = vim.fn.confirm(
          ("Open %d files from %s?"):format(#files, label), "&Yes\n&No", 2)
        if choice ~= 1 then
          return
        end
      end

      table.sort(files)
      for _, f in ipairs(files) do
        vim.cmd("badd " .. vim.fn.fnameescape(f))
      end

      vim.notify(
        ("nvim-tree: opened %d file(s) from %s"):format(#files, label),
        vim.log.levels.INFO
      )
    end

    local function tree_on_attach(bufnr)
      local api = require "nvim-tree.api"
      local function opts(desc)
        return { desc = "nvim-tree: " .. desc, buf = bufnr, noremap = true, silent = true, nowait = true }
      end
      -- default mappings
      api.config.mappings.default_on_attach(bufnr)
      -- float preview
      local FloatPreview = require("float-preview")
      FloatPreview.attach_nvimtree(bufnr)
      -- custom mappings
      vim.keymap.set('n', 'l', api.node.open.edit, opts('Open'))
      vim.keymap.set('n', 'L', open_dir_files_as_buffers, opts('Open dir files as buffers'))
      vim.keymap.set('n', 'h', function()
        local node = api.tree.get_node_under_cursor()
        if node and node.type == "directory" and node.open then
          api.node.open.edit()
        else
          api.node.navigate.parent_close()
        end
      end, opts('Close Directory'))
      vim.keymap.set('n', '<C-l>', api.tree.change_root_to_node, opts('CD'))
      vim.keymap.set('n', '<C-h>', api.tree.change_root_to_parent, opts('Up'))
      vim.keymap.set('n', '<C-s>', api.node.open.horizontal, opts('Open: horizontal Split'))
      --vim.keymap.set('n', '<leader>e', api.tree.close, opts('Close'))
      vim.keymap.set('n', '<leader>e', move_l, opts('Close'))
      vim.keymap.set('n', '<leader>df', diff_with_current_buffer, opts('Diff with current buffer'))
      vim.keymap.set('n', ']g', api.node.navigate.git.next, opts('Next Git'))
      vim.keymap.set('n', '[g', api.node.navigate.git.prev, opts('Prev Git'))
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
              unmerged = "",
              ignored = "◌",
            },
          },
        },
      },
      filters = {
        dotfiles = false,
        custom = { "node_modules", "^\\.DS_Store$", "^\\.git$", "^\\.ruff_cache$" },
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
      { "<C-s>", node.open.horizontal,           "Open: Horizontal Split" },
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
      { "[g",    node.navigate.git.prev,         "Prev Git" },
      { "]g",    node.navigate.git.next,         "Next Git" },
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
  end,
}
