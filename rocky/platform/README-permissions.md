# Permissions Rocky needs

Rocky runs inside your terminal app (Terminal, iTerm, Warp, VS Code, Cursor ...), so the operating system
asks you to trust that terminal app; "Rocky" itself never appears in the lists. Grant them once; `rocky --doctor` shows what is still missing.

## macOS

| Permission | Why | Where to grant |
|---|---|---|
| Accessibility | Reading the screen (the numbered items) and sending clicks and keystrokes | System Settings > Privacy & Security > Accessibility > add or tick your terminal app |
| Microphone | Hearing you | System Settings > Privacy & Security > Microphone > tick your terminal app (macOS asks the first time Rocky opens the mic) |
| Input Monitoring | The hold-to-talk key (Right Option) is read with a listen-only event tap | System Settings > Privacy & Security > Input Monitoring > tick your terminal app |
| Speech Recognition | Only with the `apple` STT backend (the default on a Mac) | macOS asks the first time; later under Privacy & Security > Speech Recognition |

After ticking a box, quit and reopen the terminal app so the new permission applies. Nothing persistent is
installed: no LaunchAgent, no key remap, no helper process.

## Windows

| Permission | Why | Where to grant |
|---|---|---|
| Microphone | Hearing you | Settings > Privacy & security > Microphone: turn on "Microphone access" and "Let desktop apps access your microphone" |

UI Automation (reading the screen) and SendInput (clicks and keystrokes) need no special permission for a
normal desktop app. Windows applications running as administrator only accept input from another
administrator process, so run Rocky elevated if you need to drive one of those.

The Windows platform is UNTESTED on a real Windows machine as of 2026-09-20.
