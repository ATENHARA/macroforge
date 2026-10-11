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
| `<wow>/WTF/Account/<acct>/SavedVariables/MacroForge.lua` | the watcher, `spellbook`, `report` | read only: the addon's saved data (your exported spell list, pet and name, probe results), for every account folder unless `account` is set |
| `<wow>/WTF/Account/<acct>/.../macros-cache.txt` | only `cache --write`, game closed | merged macros; the old file is copied to `.bak` first |
| `%APPDATA%\...\Startup\MacroForge Watcher.vbs` | only `startup` and `setup` (if you accept) | launcher for the watcher; `startup --remove` deletes only this file |
| `%LOCALAPPDATA%\macroforge-agent\runs\` | the watcher | throwaway per-request workspaces, deleted afterwards |
| Windows Credential Manager | `setup`, only if you pick an online AI | one entry `MacroForge:<variable name>` holding your API key; delete it in Control Panel > Credential Manager |
| network | `fetch` and `setup`; the watcher if you use `agent_backend: openai` | `fetch`/`setup`: https to `wago.tools`, `api.github.com`, `github.com`, `githubusercontent.com`. The watcher: only the `llm_base_url` you configured |

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
| agent output | the Claude CLI or the chat model | untrusted: parsed as JSON and validated like `macros.json`. Agent-written macros may only use a **fixed allowlist** (the wiki's command list plus a few aliases, minus the blocked commands, and only real conditionals); a client probe can narrow the list, never widen it. New `remove` entries must name macros already in `macros.json`. The reply sent to the game starts with a change summary **computed from the real diff**, followed by the model's text reduced to ASCII with `\|` removed |
| downloads | the network | https only, host allowlist (also for redirects), size caps, content checks (CSV header), `.part` file renamed only on success |
| data and slot files read by the addon | `install` | validated again in Lua: types, lengths, numeric ids; one bad entry cannot stop the others; text shown in chat has `\|` doubled |

## The agent sandbox (in-game requests)
For each request the watcher runs `claude -p` with:
- `--restricted`: no code-running tools or web fetch, personal settings files ignored, and the file tools
  **confined to the working folder** (verified with the real CLI, see below);
- `--tools Read,Glob,Grep,Edit`, `--permission-mode acceptEdits`, no MCP servers, no skills, no saved
  session, a per-request spend cap and a time limit (the whole process tree is killed on timeout);
- a fresh folder holding only copies of `macros.json`, `agent-brief.md`, the class spell table, the pet
  table and the icon list (script requests: the script brief, the examples, the icon list and, if the
  probe ran, the game's function names); the real project is never visible to it;
- an allowlisted environment (`ENV_ALLOW` in `mforge/agent.py`): system and profile folders, proxy
  settings and an explicit `ANTHROPIC_API_KEY`. Other tokens in your environment are not passed on.

The result is accepted only if it validates; then `macros.json` is replaced atomically after a backup.
There is a single-instance lock, a persisted hourly rate limit and an audit log (`data/watch.log`).

**Verified:** `tests/test_live.py` (opt-in) runs the real CLI with a neutral prompt that asks it to read a
file outside the workspace and edit another one. The CLI refused ("outside ... `--restricted` confines the
file tools") and the files were untouched.

### Bulk changes
A request that would remove or edit `confirm_changes_at` (default 3) or more macros at once is refused unless
the player's own request contains the word CONFIRM. The text comes from the player, not the model, so the model
cannot confirm for them. `rollback` restores `macros.json`, but cannot bring back macros already deleted in
game.

### Ideas and history in the window
The macro ideas and script examples are fixed text shipped with MacroForge, not AI output. They are checked at build
time with the same rules as AI macros (and scripts with the script checker); an entry that fails is left out. **Add**
never overwrites a macro you already have with that name. The history is kept in your own saved variables
(`MacroForgeHistory`, newest 30 requests) and never leaves your PC; text shown from it is escaped so it cannot render
colors or links.

### Script requests (`/mf script`)
A script is a macro whose body is `/run <one line of Lua>`. Lua is real code, so scripts get their own
mode, their own AI instructions (`reference/script-brief.md`) and their own checker (`mforge/luacheck.py`,
rules in `mforge/resources/script_rules.toml`). A request carries its mode; a script request may only add or
change scripts and a macro request may never contain `/run`, `/script` or a script entry.

The checker parses the Lua (the same grammar as the game's Lua 5.1, with parity tested against LuaJIT)
and refuses, with a reason shown to the player:
- dynamic access (`_G`, `loadstring`, `getfenv`, `pcall`, `getglobal`, computed names like
  `["Send".."Chat"]`), `os`, `io`, `debug` and the like, so a denied function cannot be reached indirectly;
- the game's command dispatchers (`SlashCmdList`, `RunMacroText`, ...) and families of functions: chat and
  social, mail, trade, auction, guild and party administration, item and gold actions, casting and
  targeting, accepting or confirming prompts, movement, logout and session, and changing macros, bindings
  or addons; a list of settings (CVars) that `SetCVar` may not touch;
- **calls that cannot be traced to a name**: only a named function can be called, never the result of
  another call or an expression. A function stored in a variable can only be called if it was written in
  the script, and the only methods allowed on any object (including locals and call results) are reading
  ones (`Get...`, `Is...`, `Has...`), `AddMessage` and string methods. Through a game table (other than a
  `C_` namespace) only reading functions can be called. Functions are declared with `local function`, never
  stored in tables, and cannot call each other or themselves;
- timers, frames, hooks and events, `while`/`repeat`/`goto`, and global variables that do not start with
  `MF_`;
- loops that could freeze the game: loop ranges and steps must be plain whole numbers (or `#list`), loops
  nest at most 2 deep, the total of loop steps is bounded (a function's loops count every time it is
  called), `for ... in` only uses the game's own `pairs`, `ipairs` and `gmatch`;
- strings that could grow without limit: no `string.rep`, a variable joined with itself, `gsub` in a
  loop, or a loop that feeds a string back into itself more than once;
- more than `script_max_actions` (default 1) state-changing calls, or one inside a loop or function.

Everything else the client exposes is allowed. With probe data (`/mf probe`, then `report`) a function that
does not exist in this game is also refused; only names Blizzard defined are recorded (never other addons').
The addon compiles each script with `loadstring` before installing it (it is never run by MacroForge).

This is a static check, not a sandbox: it makes the harmful scripts the AI could plausibly write hard to
produce, and a determined author may still find a way around it, so the real protection is that a script
only runs when *you* press the macro and that the game's own taint rules block protected functions. An
independent adversarial review found and fixed a number of bypasses (calls through parentheses, method
calls on locals, dispatcher tables, endless loops); every one of them is a regression test in
`tests/test_luacheck_bypass.py`.

**Blizzard's rules, honestly:** the game shows "You are attempting to run a custom script..." for every
custom script; it is a warning, not a penalty. Blizzard's add-on policy does not mention macros or
`/run`. The [End User License Agreement](https://www.blizzard.com/en-us/legal/fba4d00f-c7e4-4883-b8b9-1b4500a402ea/blizzard-end-user-license-agreement)
prohibits bots and other software that automates control of the game. "One key press, one action" is the
community and moderator reading, not Blizzard text, so MacroForge is *designed to stay within* these
rules, which is not a guarantee or approval from Blizzard. A script is tainted code and cannot call
protected functions; such calls fail harmlessly in game. Read what a script does before you accept the
warning.

### Other AIs (OpenAI-style endpoints)
With `agent_backend: openai` the watcher sends one HTTPS request to the address in `llm_base_url` instead of
running the CLI. The model has **no tools at all**: it can only return text, which is parsed as a change-set,
merged into `macros.json` and then validated exactly like agent output above (one repair retry at most).
- **What is sent:** your request, the character's name, class, level, known spells and pet, your macros for
  that character and the account-wide ones, and the rules text. A cloud provider receives this under your
  account; a model on this PC (Ollama, LM Studio) keeps it on the PC.
- **The key:** `setup` offers two places, with the vault first. Windows Credential Manager (recommended)
  is encrypted by Windows for your user account, with no registry edit, no file and no presence in any
  program's environment. Or an environment variable that you set yourself (plain text, readable by every
  program you start); setup changes nothing for that choice. A variable named in `llm_api_key_env` overrides
  the vault. The key is never stored in `config.json`, logs, replies or error messages
  (a key echoed by a server is redacted), is never printed, and is sent only to your `llm_base_url`. Any
  program that runs as your Windows user could still ask Credential Manager for it, as with any saved
  password, so treat a compromised user account as lost.
- **Address rules:** https only; plain http only for `localhost`, `127.0.0.1` or `::1`; no user name or
  password in the URL; redirects are never followed; response size and time are capped.
- **No dollar cap:** providers do not report cost, so only the hourly request limit and your provider's own
  limits protect your bill.

## Your data
`data/watch.log`, `data/watcher.log`, `data/replies.json` and `data/backups/` hold your requests, replies,
character names and costs. They stay in the MacroForge folder and are excluded from git. Requests, your
spell list and the macros go to the AI you chose (Claude under your own account, or the endpoint you configured);
nothing is sent anywhere else.
The repository's tests refuse commits that contain personal details (`tests/test_privacy.py`).

## Residual risks (accepted)
- The model can still write a macro you would not want within these rules, for example one that spams
  chat with `/say`, whispers someone or rewrites a macro you use. A macro only does something when *you*
  press it. The reply starts with a summary of the real change, so an edit to an existing macro is always
  visible, and `rollback` restores the previous `macros.json` (it cannot bring back in-game macros that a
  `remove` entry already deleted).
- The blocked-command list (`/script`, `/run`, `/dump`, `/console`, `/tinspect`, `/click`, `/logout`,
  `/quit`, guild administration and a few more; see `mforge/resources/defaults.toml`) is a denylist on top
  of the allowlist, and applies only to what a request adds or changes (not to your own macros). The wiki
  command list itself contains these commands, so it is this denylist (`blocked_commands` in `config.json`)
  that keeps them out of agent macros, and clearing it removes that protection. Whether every dev-tool
  command on the Forever client evaluates Lua is not known.
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
