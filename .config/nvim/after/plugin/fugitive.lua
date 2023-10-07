vim.keymap.set("n", "<leader>gs", vim.cmd.Git)

local Fugitive = vim.api.nvim_create_augroup("Fugitive", {})

local autocmd = vim.api.nvim_create_autocmd
vim.keymap.set("n", "<leader>gd", ":Gvdiffsplit<CR>");
autocmd("BufWinEnter", {
    group = Fugitive,
    pattern = "*",
    callback = function()
        if vim.bo.ft ~= "fugitive" then
            return
        end
        local bufnr = vim.api.nvim_get_current_buf()
        local opts = {buffer = bufnr, remap = false}
        -- NOTE: It allows me to easily set the branch i am pushing and any tracking
        -- needed if i did not set the branch up correctly
        vim.keymap.set("n", "<leader>gp", ":Git push -u origin ", opts);
    end,
})
