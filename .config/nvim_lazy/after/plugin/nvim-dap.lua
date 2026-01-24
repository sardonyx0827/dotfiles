--- @diagnostic disable: undefined-global
-- requirements: apt install python3 python3.x-venv, pip3 install debugpy
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
      type = 'codelldb', -- the type here established the link to the adapter definition: `dap.adapters.python`
      request = 'launch',
      name = "Launch file",

      -- Options below are for debugpy, see https://github.com/microsoft/debugpy/wiki/Debug-configuration-settings for supported options
      program = function()
        return vim.fn.input('Path to executable: ', vim.fn.getcwd() .. '/a.out', 'file')
      end,
      cwd = '${workspaceFolder}',
      stopOnEntry = false,
    }
  }
}

-- open and close dap-ui after debug is started and stopped
local dapui = require("dapui")
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

vim.api.nvim_set_keymap('n', '<leader>bp', ':DapToggleBreakpoint<CR>', { silent = true, desc = "Toggle Breakpoint." })
vim.api.nvim_set_keymap('n', '<leader>be', ':lua require("dap").clear_breakpoints()<CR>',
  { silent = true, desc = "Clear All Breakpoint." })
vim.api.nvim_set_keymap('n', '<F5>', ':DapContinue<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F10>', ':DapStepOver<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F11>', ':DapStepInto<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F12>', ':DapStepOut<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<S-F5>', '<cmd>lua require("dap").disconnect({ terminateDebuggee = true })<CR>'
  .. '<cmd>lua require("dap").close()<CR>', { silent = true })
