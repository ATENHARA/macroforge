# Security

MacroForge is installed by other people, so this page says what it can touch, what it trusts, and what is
deliberately not protected. If you find a problem, please open an issue on the repository (for anything
sensitive, describe it without a working exploit first).

## What it can touch
| Place | When | Why |
|---|---|---|
| the MacroForge folder | always | `macros.json`, `config.json` (also written by `setup`), `data/`, `build/`, `reference/compat-*.md` |
| `<wow>/Interface/AddOns/MacroForge` and `MacroForge_S###` | `install`, `slots`, `setup` | the addon and its slot addons; links are refused; `setup` stops with a hint if the folder is not writable |
| `<wow>/Screenshots` | the watcher | reads new `.tga` request pictures; deletes the ones it handled |
| `<wow>/WTF/Account/<acct>/.../macros-cache.txt` | only `cache --write`, game closed | merged macros; the old file is copied to `.bak` first |
| `%APPDATA%\...\Startup\MacroForge Watcher.vbs` | only `startup` and `setup` (if you accept) | launcher for the watcher; `startup --remove` deletes only this file |
| `%LOCALAPPDATA%\macroforge-agent\runs\` | the watcher | throwaway per-request workspaces, deleted afterwards |
| network | `fetch` and `setup` only | https to `wago.tools`, `api.github.com`, `github.com`, `githubusercontent.com` |

The addon has no network access (the game does not allow it) and never runs a macro. It calls
`CreateMacro`/`EditMacro`/`DeleteMacro` for the macros named in its data file and `LoadAddOn` for its own
slot addons. For `/mf ask` it also draws a strip of colored squares, calls `Screenshot` (briefly switching
the `screenshotFormat` setting) and `PlaySound`; the window's Reload UI button calls `ReloadUI`. Its
`remove` list deletes every in-game macro with a listed name, which is why the watcher only accepts
`remove` entries for macros that are already in `macros.json`.

Folders that are links (symlinks or junctions) are refused for the AddOns folder and its parent, the
`MacroForge` and slot folders, and the `macros-cache.txt` files and their `.bak`.

## Trust boundaries
| Input | Source | Handling |
|---|---|---|
| `macros.json` | you (or Claude) | JSON shape and types; name <= 16, body <= 255 **bytes**; no control, invisible (zero-width, bidi) or line-separator characters and no `\|` escape sequences; unique names; slot limits; every string written to the Lua data file is escaped (only escapes valid in the game's Lua 5.1) |
| `config.json` | you | typed and validated; unknown keys reported; `claude_path` must be an existing file and **is executed** |
| saved variables (`MacroForge.lua`) | the game, in a user-writable folder | hardened parser: size, depth and number-length caps, only `ValidationError` on bad input, fuzz-tested. Anything else that goes wrong is logged and the watcher keeps running |
| request screenshots | the game's `Screenshots` folder (user-writable) | only `.tga` files from the last 10 minutes; picture size capped (40 million pixels); accepted only if a strip with the right header, length and checksum is found. The decoded fields then get the same validation as any request. Handled pictures (and a JPEG written within 5 seconds after one) are deleted; a locked file is retried, then reported, never fatal |
| requests | whatever `/mf ask` drew or saved | `id`, `level`, `char` and `class` validated (class from a fixed list, so it can never become a path); text capped and stripped of control characters; a bad request is answered or ignored, never fatal |
| agent output | the Claude CLI | untrusted: parsed as JSON and validated like `macros.json`. Agent-written macros may only use a **fixed allowlist** (the wiki's command list plus a few aliases, minus the blocked commands, and only real conditionals); a client probe can narrow the list, never widen it. New `remove` entries must name macros already in `macros.json`. The reply sent to the game starts with a change summary **computed from the real diff**, followed by the model's text reduced to ASCII with `\|` removed |
| downloads | the network | https only, host allowlist (also for redirects), size caps, content checks (CSV header), `.part` file renamed only on success |
| data and slot files read by the addon | `install` | validated again in Lua: types, lengths, numeric ids; one bad entry cannot stop the others; text shown in chat has `\|` doubled |

## The agent sandbox (in-game requests)
For each request the watcher runs `claude -p` with:
- `--restricted`: no code-running tools or web fetch, personal settings files ignored, and the file tools
  **confined to the working folder** (verified with the real CLI, see below);
- `--tools Read,Glob,Grep,Edit`, `--permission-mode acceptEdits`, no MCP servers, no skills, no saved
  session, a per-request spend cap and a time limit (the whole process tree is killed on timeout);
- a fresh folder holding only copies of `macros.json`, `agent-brief.md`, the class spell table, the pet
  table and the icon list; the real project is never visible to it;
- an allowlisted environment (`ENV_ALLOW` in `mforge/agent.py`): system and profile folders, proxy
  settings and an explicit `ANTHROPIC_API_KEY`. Other tokens in your environment are not passed on.

The result is accepted only if it validates; then `macros.json` is replaced atomically after a backup.
There is a single-instance lock, a persisted hourly rate limit and an audit log (`data/watch.log`).

**Verified:** `tests/test_live.py` (opt-in) runs the real CLI with a neutral prompt that asks it to read a
file outside the workspace and edit another one. The CLI refused ("outside ... `--restricted` confines the
file tools") and the files were untouched.

## Your data
`data/watch.log`, `data/watcher.log`, `data/replies.json` and `data/backups/` hold your requests, replies,
character names and costs. They stay in the MacroForge folder and are excluded from git. Requests, your
spell list and the macros go to the Claude service under your own account; nothing is sent anywhere else.
The repository's tests refuse commits that contain personal details (`tests/test_privacy.py`).

## Residual risks (accepted)
- The model can still write a macro you would not want within these rules, for example one that spams
  chat with `/say`, whispers someone or rewrites a macro you use. A macro only does something when *you*
  press it. The reply starts with a summary of the real change, so an edit to an existing macro is always
  visible, and `rollback` restores the previous `macros.json` (it cannot bring back in-game macros that a
  `remove` entry already deleted).
- The blocked-command list (`/script`, `/run`, `/dump`, `/console`, `/tinspect`, `/click`, `/logout`,
  `/quit`, guild administration and a few more; see `mforge/resources/defaults.toml`) is a denylist on top
  of the allowlist, and applies only to agent-written macros. Whether every dev-tool command on the Forever
  client evaluates Lua is not known; the allowlist is what keeps them out.
- Third-party data (the wago.tools tables and the wow-listfile icon names) is shown to the agent. It is
  size-limited and format-checked but not signed, so a poisoned download could try to steer the model
  (prompt injection); the sandbox and the validation above limit the damage. The host allowlist also
  matches subdomains, including user-content hosts under `githubusercontent.com`.
- Anything running as your user can edit `macros.json`, `config.json`, the saved variables or the
  `Screenshots` folder. These are protected against *malformed* content (a forged request picture is
  validated and rate limited like any request), not against someone who already controls your account.
- Deleting handled request pictures also deletes any JPEG screenshot the game wrote within 5 seconds
  after one, so a screenshot of your own taken in that window can be lost (`delete_request_shots`
  turns the cleanup off). The request is visible as colored squares for about two seconds.
- `claude_path` is executed; only set it to a program you trust.
- The game's chat window and addon API can change in a client update. The Lua checks are defensive but
  are only fully exercised in the game (see `docs/TESTING.md`).

## Removing everything
`python macroforge.py startup --remove`, delete the `MacroForge*` folders in your AddOns folder, delete
`%LOCALAPPDATA%\macroforge-agent`, and delete the MacroForge folder.
