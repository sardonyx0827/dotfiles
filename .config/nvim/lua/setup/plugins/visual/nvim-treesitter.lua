--- @diagnostic disable: undefined-global
-- Highlitght colors, Indents, etc
return {
  "nvim-treesitter/nvim-treesitter",
  event = "BufRead",
  dependencies = {
    -- show context
    { "nvim-treesitter/nvim-treesitter-context", },
  },
  config = function()
    -- Workaround: Neovim 0.12.0 treesitter nil node in _get_injections
    -- https://github.com/neovim/neovim/issues (treesitter.lua:196 get_range nil)
    local orig_get_node_text = vim.treesitter.get_node_text
    vim.treesitter.get_node_text = function(node, source, opts)
      if node == nil then
        return ""
      end
      local ok, result = pcall(orig_get_node_text, node, source, opts)
      if ok then
        return result
      end
      return ""
    end

    require("nvim-treesitter.configs").setup {
      -- a list of parser names, or "all"
      --ensure_installed = { "vimdoc", "javascript", "typescript", "c", "lua", "rust" },
      ensure_installed = "all",
      ignore_install = { "ipkg" },
      -- install parsers synchronously (only applied to `ensure_installed`)
      sync_install = false,
      -- automatically install missing parsers when entering buffer
      -- recommendation: set to false if you don"t have `tree-sitter` cli installed locally
      auto_install = true,

      highlight = {
        -- `false` will disable the whole extension
        enable = true,
        -- setting this to true will run `:h syntax` and tree-sitter at the same time.
        -- set this to `true` if you depend on "syntax" being enabled (like for indentation).
        -- using this option may slow down your editor, and you may see some duplicate highlights.
        -- instead of true it can also be a list of languages
        additional_vim_regex_highlighting = false,
      },
    }
  end,
}
