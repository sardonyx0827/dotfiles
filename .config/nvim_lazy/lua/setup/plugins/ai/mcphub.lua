return {
  "ravitemer/mcphub.nvim",
  dependencies = {
    "nvim-lua/plenary.nvim",
  },
  -- cmd = "MCPHub",                            -- lazy load by default
  build = "npm install -g mcp-hub@latest",   -- Installs globally
  config = function()
    require("mcphub").setup({
      auto_approve = true,
      extensions = {
        codecompanion = {
          -- Show the mcp tool result in the chat buffer
          show_result_in_chat = true,
          -- Make chat #variables from MCP server resources
          make_vars = true,
          -- Create slash commands for prompts
          make_slash_commands = true,
        }
      }
    })
  end,
}
