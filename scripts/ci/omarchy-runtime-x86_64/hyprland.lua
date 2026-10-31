-- Minimal Hyprland 0.57 Lua config for the hosted x86_64 runtime probe (#63).
-- Supplying any config stops Hyprland from autogenerating one and launching the floating
-- hyprland-welcome window, which covered the test window in iteration 3 (run 41032400147).
-- Scale 1 keeps the vkms output's logical and physical pixels equal for grim captures.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "1" })

hl.config({
    misc = {
        disable_hyprland_logo = true,
        disable_splash_rendering = true,
    },
    animations = {
        enabled = false,
    },
})
