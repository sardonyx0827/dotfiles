--- @diagnostic disable: undefined-global
-- Alternate theme: installed and available in the <M-0> picker, but must NOT
-- self-activate. rose-pine is the sole startup colorscheme; a self-activating
-- eager theme races it (loads after rose-pine's config → wins nondeterministically).
return {
  'morhetz/gruvbox',
  event = "VeryLazy",
}
