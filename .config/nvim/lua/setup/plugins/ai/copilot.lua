return {
  "zbirenbaum/copilot.lua",
  event = "InsertEnter",
  cmd = "Copilot",
  dependencies = {
    "copilotlsp-nvim/copilot-lsp", -- (optional) for NES functionality
    init = function()
      vim.g.copilot_nes_debounce = 500
    end,
  },
  config = function()
    -- Coarse, path-based guard for Copilot completion.
    -- Copilot's inline/panel/NES suggestions send buffer context to GitHub via
    -- its LSP, and there is NO content-level pre-send hook to scan for secrets
    -- the way the custom AI backend does (see functions/ai/backend.lua, which
    -- runs every payload through scripts/secret_scan.py). The best available
    -- mitigation is to refuse to attach Copilot to buffers whose *path* is
    -- obviously sensitive. This does not scan for a secret pasted into an
    -- ordinary file -- an accepted limitation of Copilot completion.
    local SENSITIVE_PATH = {
      "%.env$", "%.env%.", "%.envrc$",
      "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
      "%.pem$", "%.key$", "%.p12$", "%.pfx$", "%.jks$", "%.keystore$", "%.ppk$",
      "/%.ssh/", "/%.aws/", "/%.gnupg/", "/%.azure/", "/%.kube/", "/gcloud/",
      "%.netrc$", "%.npmrc$", "%.pypirc$", "%.pgpass$", "%.my%.cnf$",
      "kubeconfig", "%.tfstate", "%.tfvars", "%.dockercfg",
      "docker/config%.json$",
      -- cloud service-account / admin JSON keys (arbitrary basenames)
      "service.?account", "adminsdk", "%-key%.json$",
      "credentials", "secrets?", "password",
    }
    local function is_sensitive_path(bufname)
      local lower = bufname:lower()
      for _, pat in ipairs(SENSITIVE_PATH) do
        if lower:find(pat) then
          return true
        end
      end
      return false
    end

    require("copilot").setup({
      -- Preserve copilot's default attach guards (skip unlisted/special
      -- buffers), then add the sensitive-path refusal on top.
      should_attach = function(bufnr, bufname)
        if not vim.bo[bufnr].buflisted then
          return false
        end
        if vim.bo[bufnr].buftype ~= "" then
          return false
        end
        return not is_sensitive_path(bufname or "")
      end,

      suggestion = {
        enabled = false,
        auto_trigger = false,
        debounce = 75,
        keymap = {
          accept = "<TAB>",
          accept_word = false,
          accept_line = false,
          next = "<c-j>",
          prev = "<c-k>",
          dismiss = "<C-]>",
        },
      },

      panel = {
        enabled = true,
        auto_refresh = true,
        keymap = {
          jump_prev = "[[",
          jump_next = "]]",
          accept = "<CR>",
          refresh = "gr",
          open = "<M-CR>"
        },
        layout = {
          position = "right", -- | top | left | right
          ratio = 0.5
        },
      },

      filetypes = {
        yaml = true,
        markdown = true,
        help = true,
        gitcommit = true,
        gitrebase = true,
        hgcommit = true,
      },

      nes = {
        enabled = true,
        keymap = {
          accept_and_goto = "<leader>p",
          accept = false,
          dismiss = "<Esc>",
        },
      },
    })

    vim.keymap.set("n", "<leader>ce", function()
      require("copilot.command").toggle()
    end, { desc = "Copilot: toggle enable/disable" })

  end
}
