local dap = require('dap')
dap.adapters = {
  debugpy = {
    type = 'server',
    port = '${port}',
    executable = {

      --command = vim.fn.stdpath('data') .. '/mason/packages/debugpy/extension/adapter/debugpy',
      command = '/home/sardonyx0827/.local/share/nvim/mason/packages/debugpy/debugpy-adapter',

      args = {'--port', '${port}'}
    }
  }
}

dap.configurations = {
  python = {
    {
      -- The first three options are required by nvim-dap
      type = 'debugpy'; -- the type here established the link to the adapter definition: `dap.adapters.python`
      request = 'launch';
      name = "Launch file";

      -- Options below are for debugpy, see https://github.com/microsoft/debugpy/wiki/Debug-configuration-settings for supported options
      program = "${file}"; -- This configuration will launch the current file if used.
    }
  }
}

-- open and close dap-ui when debugging
local dapui = require("dapui")
dap.listeners.after.event_initialized["dapui_config"] = function()
  dapui.open()
end
dap.listeners.before.event_terminated["dapui_config"] = function()
  dapui.close()
end
dap.listeners.before.event_exited["dapui_config"] = function()
  dapui.close()
end

vim.api.nvim_set_keymap('n', '<leader>bp', ':DapToggleBreakpoint<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<leader>bc', ':lua require("dap").clear_breakpoints()<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F4>', ':lua require("dap").run_last()<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F5>', ':DapContinue<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F10>', ':DapStepOver<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F11>', ':DapStepInto<CR>', { silent = true })
vim.api.nvim_set_keymap('n', '<F12>', ':DapStepOut<CR>', { silent = true })

--vim.api.nvim_set_keymap('n', '<leader>B', ':lua require("dap").set_breakpoint(nil, nil, vim.fn.input("Breakpoint condition: "))<CR>', { silent = true })
--vim.api.nvim_set_keymap('n', '<leader>lp', ':lua require("dap").set_breakpoint(nil, nil, vim.fn.input("Log point message: "))<CR>', { silent = true })

