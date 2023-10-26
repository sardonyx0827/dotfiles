local lsp = require("lsp-zero")

lsp.preset("recommended")

-- https://github.com/williamboman/mason-lspconfig.nvim
--lsp.ensure_installed({
--  "lua_ls",
--  "jedi_language_server",
--  "jsonls",
--  "dockerls",
--  "docker_compose_language_service",
--  "yamlls",
--})
-- Fix Undefined global "vim"
lsp.nvim_workspace()

local cmp = require("cmp")
local cmp_select = { behavior = cmp.SelectBehavior.Select }
local cmp_mappings = lsp.defaults.cmp_mappings({
  ["<C-p>"] = cmp.mapping.select_prev_item(cmp_select),
  ["<C-n>"] = cmp.mapping.select_next_item(cmp_select),
  ["<C-y>"] = cmp.mapping.confirm({ select = true }),
  ["<C-u>"] = cmp.mapping.scroll_docs(-4),
  ["<C-d>"] = cmp.mapping.scroll_docs(4),
  ["<C-Space>"] = cmp.mapping.complete(),
})

cmp_mappings["<Tab>"] = nil
cmp_mappings["<S-Tab>"] = nil

lsp.setup_nvim_cmp({
  mapping = cmp_mappings
})

lsp.set_preferences({
  suggest_lsp_servers = false,
  sign_icons = {
    error = "E",
    warn = "W",
    hint = "H",
    info = "I"
  }
})

lsp.on_attach(function(client, bufnr)
  -- jump to definition
  vim.keymap.set("n", "<C-t>", function() vim.lsp.buf.definition() end, { buffer = bufnr, remap = false, desc = "jump to definition" })
  -- show definition in a split
  vim.keymap.set("n", "K", function() vim.lsp.buf.hover() end, { buffer = bufnr, remap = false, desc = "show definition" })
  vim.keymap.set("n", "<leader>ca", function() vim.lsp.buf.code_action() end, { buffer = bufnr, remap = false, desc = "code action" })
  vim.keymap.set("n", "<leader>ff", function() vim.lsp.buf.format { async = true } end, { buffer = bufnr, remap = false, desc = "format this file" })
  vim.keymap.set("n", "<leader>ra", function() vim.lsp.buf.rename() end, {desc = "rename all file in workspace"})
end)

-- disable when using coc-nvim
lsp.setup()

vim.diagnostic.config({
  virtual_text = true
})
