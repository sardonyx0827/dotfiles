return {
  "rebelot/kanagawa.nvim",
  -- 他のカラースキームと同じく遅延ロード。これが無いと lazy.nvim の
  -- デフォルト判定で起動時に即時ロードされる (rose-pine.lua のコメント
  -- 参照: 起動時に :colorscheme するのは rose-pine だけであるべき)。
  event = "VeryLazy",
  config = function()
    require("kanagawa").setup({
      colors = {
        theme = {
          -- change specific usages for a certain theme, or for all of them
          wave = {
            ui = {
              float = {
                bg = "none",
              },
            },
          },
          dragon = {
            syn = {
              parameter = "yellow",
            },
          },
          all = {
            ui = {
              bg_gutter = "none"
            }
          }
        }
      },
    })
  end
}
