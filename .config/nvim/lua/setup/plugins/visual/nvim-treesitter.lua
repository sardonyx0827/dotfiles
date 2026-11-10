-- Highlight colors, Indents, etc
--
-- Tracks the `main` branch of nvim-treesitter. The `master` branch is frozen and its
-- README says "Neovim 0.12 is not supported": its injection handling crashed on 0.12,
-- and the workaround (a global patch of vim.treesitter.get_node_text that swallowed
-- the error and returned "") made language injection fail silently instead -- a
-- ```lua fence in Markdown just lost its highlighting.
--
-- `main` is a ground-up rewrite, so none of master's setup carries over:
--   * no `nvim-treesitter.configs` and no modules (ensure_installed / auto_install /
--     highlight): Neovim's own treesitter does the work, this plugin only supplies
--     parsers and queries;
--   * highlighting is switched on per buffer with vim.treesitter.start();
--   * it does not support lazy-loading, hence `lazy = false`;
--   * it needs the tree-sitter CLI (>= 0.26.1), a C compiler, tar and curl to build
--     a parser (see its README, "Requirements").
local SKIP = {
  -- master's `ignore_install`. main no longer ships this parser, so this only keeps
  -- it out should it come back.
  ipkg = true,
}

return {
  "nvim-treesitter/nvim-treesitter",
  branch = "main",
  lazy = false,
  -- Parsers must match the queries the plugin ships: rebuild them on every update.
  build = ":TSUpdate",
  dependencies = {
    -- show context (uses Neovim's core treesitter; independent of this plugin's API)
    { "nvim-treesitter/nvim-treesitter-context", },
  },
  config = function()
    -- Languages whose install was already attempted this session: a parser that fails
    -- to build must not be retried every time a buffer of that filetype is opened.
    local attempted = {}
    -- Set of installable parser names, resolved on first use.
    local available

    local function installable(lang)
      if SKIP[lang] then
        return false
      end
      if not available then
        available = {}
        for _, name in ipairs(require("nvim-treesitter").get_available()) do
          available[name] = true
        end
      end
      return available[lang] == true
    end

    -- Parsers are compiled locally; without these every install would just fail
    -- (and on every filetype).
    local function can_build()
      for _, exe in ipairs({ "tree-sitter", "tar", "curl" }) do
        if vim.fn.executable(exe) ~= 1 then
          return false
        end
      end
      return true
    end

    -- Switch highlighting on. Not enabling regex syntax alongside it
    -- (additional_vim_regex_highlighting = false on master) is vim.treesitter.start()'s
    -- own behaviour, so nothing more is needed. Neovim's bundled ftplugins (lua,
    -- markdown, ...) start it themselves; `ts_highlight` is what start() leaves behind.
    local function start(buf)
      if vim.api.nvim_buf_is_valid(buf) and not vim.b[buf].ts_highlight then
        pcall(vim.treesitter.start, buf)
      end
    end

    local function start_all(lang)
      for _, buf in ipairs(vim.api.nvim_list_bufs()) do
        if
          vim.api.nvim_buf_is_loaded(buf)
          and vim.treesitter.language.get_lang(vim.bo[buf].filetype) == lang
        then
          start(buf)
        end
      end
    end

    -- Replaces master's `ensure_installed = "all"` + `auto_install = true`. Building
    -- every one of ~300 parsers up front is a slow first start that is retried on each
    -- launch for any grammar that fails, so install on demand instead: the first
    -- buffer of a filetype that has a parser available but not yet built installs it
    -- in the background, then highlighting starts without a restart.
    vim.api.nvim_create_autocmd("FileType", {
      group = vim.api.nvim_create_augroup("setup_treesitter", { clear = true }),
      callback = function(args)
        local lang = vim.treesitter.language.get_lang(args.match)
        if not lang then
          return
        end
        -- Already on the runtimepath: built by this plugin, or bundled with Neovim.
        if vim.treesitter.language.add(lang) then
          start(args.buf)
          return
        end
        if attempted[lang] or not installable(lang) or not can_build() then
          return
        end
        attempted[lang] = true
        require("nvim-treesitter").install({ lang }):await(function()
          -- Runs from a libuv callback; the editor API is only safe once scheduled.
          vim.schedule(function()
            start_all(lang)
          end)
        end)
      end,
    })
  end,
}
