-- example
-- write "find * -type f -name "*.lua"
-- and execute this command ":.!sh"
local load_buffers_from_file_list = function()

  local buf = vim.api.nvim_get_current_buf()
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)

  for _, line in ipairs(lines) do
    -- if a buffer is already open with the same name, don't open it again
    local tmp_bufnr = vim.fn.bufnr(line)
    local opened_buffer_list = vim.api.nvim_list_bufs()
    local is_opened = false

    for _, opened_bufnr in ipairs(opened_buffer_list) do
      if opened_bufnr == tmp_bufnr then
      is_opened = true
      break
      end
    end

    if is_opened then
      goto continue
    end

    local bufnr = vim.api.nvim_create_buf(true, false)
    vim.api.nvim_buf_set_name(bufnr, line)
    vim.api.nvim_buf_call(bufnr, vim.cmd.edit)

    ::continue::
  end
end

vim.keymap.set("n", "<leader>bl", load_buffers_from_file_list, { desc = 'load buffers from file list' })

