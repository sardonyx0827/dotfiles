-- DAP for Debugging (config + dap-ui formerly in after/plugin/nvim-dap*.lua)
-- requirements: apt install python3 python3.x-venv, pip3 install debugpy
return {
  'mfussenegger/nvim-dap',
  lazy = true,
  dependencies = {
    'rcarriga/nvim-dap-ui',
    'jay-babu/mason-nvim-dap.nvim',
    "nvim-neotest/nvim-nio",
    'mfussenegger/nvim-dap-python',
  },
  keys = {
    { "<leader>bp", ":DapToggleBreakpoint<CR>",                    silent = true, desc = "Toggle Breakpoint." },
    { "<leader>be", ':lua require("dap").clear_breakpoints()<CR>', silent = true, desc = "Clear All Breakpoint." },
    { "<leader>du", ':lua require("dapui").toggle()<CR>',          silent = true, desc = "Toggle DAP UI - Debug Windows." },
    { "<F5>",       ":DapContinue<CR>",                            silent = true },
    { "<F9>",       ":DapStepOver<CR>",                            silent = true },
    { "<F10>",      ":DapStepInto<CR>",                            silent = true },
    { "<F12>",      ":DapStepOut<CR>",                             silent = true },
    {
      "<S-F5>",
      '<cmd>lua require("dap").disconnect({ terminateDebuggee = true })<CR>'
      .. '<cmd>lua require("dap").close()<CR>',
      silent = true,
    },
  },
  config = function()
    local dap = require('dap')
    dap.adapters = {
      debugpy = {
        type = 'server',
        port = '${port}',
        executable = {
          command = vim.fn.stdpath('data') .. '/mason/packages/debugpy/debugpy-adapter',
          args = { '--port', '${port}' }
        }
      },
      node_debug2_adapter = {
        type = 'executable',
        port = '${port}',
        command = vim.fn.stdpath('data') .. '/mason/packages/node-debug2-adapter/node-debug2-adapter',
      },
      codelldb = {
        type = 'server',
        port = '${port}',
        executable = {
          command = vim.fn.stdpath('data') .. '/mason/packages/codelldb/extension/adapter/codelldb',
          args = { '--port', '${port}' }
        }
      },
    }

    dap.configurations = {
      python = {
        {
          -- The first three options are required by nvim-dap
          type = 'debugpy', -- the type here established the link to the adapter definition: `dap.adapters.python`
          request = 'launch',
          name = "Launch file(no pipenv)",

          -- Options below are for debugpy, see https://github.com/microsoft/debugpy/wiki/Debug-configuration-settings for supported options
          program = "${file}", -- This configuration will launch the current file if used.
          stopOnEntry = false
        }
      },
      javascript = {
        {
          type = 'node_debug2_adapter',
          request = 'launch',
          name = 'Launch file',
          program = '${file}',
        },
      },
      cpp = {
        {
          -- The first three options are required by nvim-dap
          type = 'codelldb', -- the type here established the link to the adapter definition: `dap.adapters.codelldb`
          request = 'launch',
          name = "Launch file",

          -- Options below are for codelldb, see https://github.com/vadimcn/codelldb/blob/master/MANUAL.md for supported options
          program = function()
            return vim.fn.input('Path to executable: ', vim.fn.getcwd() .. '/a.out', 'file')
          end,
          cwd = '${workspaceFolder}',
          stopOnEntry = false,
        }
      }
    }

    -- dap-ui (formerly after/plugin/nvim-dap-ui.lua)
    local dapui = require("dapui")
    dapui.setup({
      icons = { expanded = "", collapsed = "" },
      layouts = {
        {
          elements = {
            { id = "watches",     size = 0.20 },
            { id = "stacks",      size = 0.20 },
            { id = "breakpoints", size = 0.20 },
            { id = "scopes",      size = 0.40 },
          },
          size = 64,
          position = "right",
        },
        {
          elements = {
            "repl",
            "console",
          },
          size = 0.20,
          position = "bottom",
        },
      },
    })

    -- open dap-ui automatically once a debug session starts
    dap.listeners.after.event_initialized["dapui_config"] = function()
      dapui.open()
    end

    -- for using pipenv or any other virtualenv
    local venv = os.getenv('VIRTUAL_ENV')
    if venv ~= nil then
      local command = string.format('%s/bin/python', venv)
      require('dap-python').setup(command)
    else
      require('dap-python').setup()
    end
  end,
}
