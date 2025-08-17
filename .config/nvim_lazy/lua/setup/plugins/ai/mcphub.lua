return {
  "ravitemer/mcphub.nvim",
  dependencies = {
    "nvim-lua/plenary.nvim",
  },
  build = "npm install -g mcp-hub@latest", -- Installs globally
  config = function()
    require("mcphub").setup({
      auto_approve = true,
      extensions = {
        avante = {
          enabled = true,
          make_slash_commands = true
        },
        codecompanion = {
          enabled = true,
          -- Show the mcp tool result in the chat buffer
          show_result_in_chat = true,
          -- Make chat #variables from MCP server resources
          make_vars = true,
          -- Create slash commands for prompts
          make_slash_commands = true,
        },
        copilotchat = {
          enabled = true,
          convert_tools_to_functions = true,
          convert_resources_to_functions = true,
          add_mcp_prefix = false,
        }
      }
    })
  end,
}
