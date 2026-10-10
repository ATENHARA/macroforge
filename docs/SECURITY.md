# Security

MacroForge is meant to be installed by other people, so this page says what it can touch, what it
trusts, and what is deliberately not protected. If you find a problem, please open an issue on the
repository (for anything sensitive, describe it without a working exploit first).

## What it can touch
| Place | When | Why |
|---|---|---|
| the MacroForge project folder | always | `macros.json`, `config.json`, `data/`, `build/`, `reference/compat-*.md` |
| `<wow>/Interface/AddOns/MacroForge` and `MacroForge_S###` | `install`, `slots` | the addon and its slot addons; links are refused |
| `<wow>/WTF/Account/<acct>/.../macros-cache.txt` | only `cache --write`, game closed | merged macros; the old file is copied to `.bak` first |
| `%APPDATA%\...\Startup\MacroForge Watcher.vbs` | only `startup` | launcher for the watcher; `startup --remove` deletes only this file |
| `%LOCALAPPDATA%\macroforge-agent\runs\` | the watcher | throwaway per-request workspaces, deleted afterwards |
| network | `fetch` only | https to `wago.tools`, `api.github.com`, `github.com`, `githubusercontent.com` |

The addon itself has no network access (the game does not allow it) and never runs a macro. It calls
`CreateMacro`/`EditMacro`/`DeleteMacro` for the macros named in its data file, `LoadAddOn` for its own
slot addons, and (only for `/mf ask`) `Screenshot`, a short switch of the `screenshotFormat` setting and
`PlaySound`. Its `remove` list deletes every in-game macro with a
listed name, which is why the watcher only accepts `remove` entries for macros that are already in
`macros.json` (see below).

Folders that are links (symlinks or junctions) are refused for: the AddOns folder and its parent, the
`MacroForge` and slot folders, and the `macros-cache.txt` files and their `.bak`.

## Trust boundaries and what is checked
| Input | Where it comes from | Handling |
|---|---|---|
| `macros.json` | you (or Claude) | JSON shape and types; name <= 16, body <= 255 **bytes**; no control, invisible (zero-width, bidi) or line-separator characters (including U+2028, U+2029 and NEL) and no `\|` escape sequences; unique names; slot limits; every string written to the Lua data file is fully escaped (only escapes valid in the game's Lua 5.1) |
| `config.json` | you | typed and validated; unknown keys reported; `claude_path` must be an existing file and **is executed** |
| saved variables (`MacroForge.lua`) | the game, in a user-writable folder | parsed by a hardened parser (size, depth and number-length caps; bad input raises only `ValidationError`; fuzz-tested including long digit runs). Anything else that goes wrong while processing the file is logged and the watcher keeps running |
| screenshots | the game's `Screenshots` folder (user-writable) | only `.tga` files modified in the last 10 minutes are read; the reader caps the picture size (40 million pixels) and a request is accepted only if a strip with the right header, length and checksum is found. The decoded fields then get exactly the same validation as any other request. Handled request pictures, and a JPEG written within 5 seconds after one, are deleted; a locked file is retried and then reported, never fatal |
| in-game requests | whatever `/mf ask` saved or drew | `id`, `level`, `char` and `class` are validated (class from a fixed list, so it can never become a path); text is capped and stripped of control characters; a bad request is answered or ignored, never fatal |
| agent output | the Claude CLI | treated as untrusted: parsed as JSON and validated like `macros.json`. Agent-written macros may only use a **fixed allowlist** (the wiki's command list plus a few aliases, minus the blocked commands, and only real conditionals); a client probe can narrow that list but never widen it, so other addons' commands are never accepted. New `remove` entries must name macros already in `macros.json`. The reply sent to the game starts with a change summary **computed from the real diff** (added, edited, removed, dropped), followed by the model's text reduced to ASCII with `\|` removed |
| downloads | the network | https only, host allowlist (also for redirects), size caps, content checks (CSV header), `.part` file renamed only on success |
| the data/slot files read by the addon | `install` | validated again in Lua: types, lengths, numeric ids; one bad entry cannot stop the others; text shown in chat has `\|` doubled |

## The agent sandbox (in-game requests)
The watcher runs `claude -p` for each request with:
- `--restricted`: removes the code-running tools and web fetch, ignores your personal settings files, and
  **confines the file tools to the working folder** (verified with the real CLI, see below);
- `--tools Read,Glob,Grep,Edit`, `--permission-mode acceptEdits`, no MCP servers, no skills, no session
  saved, a per-request spend cap and a time limit (the whole process tree is killed on timeout);
- a fresh folder holding only copies of `macros.json`, `agent-brief.md`, the class spell table and the
  icon list; the real project is never visible to it;
- an allowlisted environment: only `PATH`, system and profile folders, proxy settings and an explicit
  `ANTHROPIC_API_KEY`. Other tokens in your environment are not passed on.

The result is accepted only if it validates; then `macros.json` is replaced atomically (after a backup).
There is a single-instance lock, a persisted hourly rate limit, and an audit log (`data/watch.log`).

**Verified:** `tests/test_live.py` (opt-in) runs the real CLI with a neutral prompt that asks it to read a
file outside the workspace and edit another one. The CLI refused ("outside ... `--restricted` confines the
file tools"), and the files were untouched.

## Residual risks (accepted)
- The model can still write a macro you would not want within the rules above (for example one that spams
  chat with `/say`, whispers someone, or rewrites a macro you use often). A macro only does something when
  *you* press it. The reply begins with a summary built from the actual change, not from what the model
  claims, so an edit to an existing macro is always visible; `rollback` restores the previous
  `macros.json` (it cannot bring back in-game macros that a `remove` entry already deleted).
- The blocked-command list (default: `/script`, `/run`, `/dump`, `/console`, `/tinspect`, `/click`,
  `/logout`, `/quit`, guild administration, and a few more, see `mforge/resources/defaults.toml`) is a
  denylist on top of the allowlist. Whether every dev-tool command on the Forever client evaluates Lua is
  not known; the allowlist is what keeps them out.
- Third-party data (`icons.csv`, the spell table) is shown to the agent. A poisoned download could contain
  text that tries to steer the model (prompt injection). The agent's tools, folder and validation limit the
  damage, as above, but this path exists. Downloads come only from the allowed hosts; note that the
  allowlist also matches subdomains, including user-content hosts under `githubusercontent.com`.
- Blocked commands apply only to agent-written macros. Macros you write yourself are not restricted.
- Anything running as your user can edit `macros.json`, `config.json` or the saved variables. Those files
  are protected against *malformed* content, not against someone who already controls your account.
- `claude_path` is executed. Only set it to a program you trust.
- Requests and character data you send are processed by the Claude service under your own account.
- The request is visible as colored squares for about two seconds, and anyone who can write to the
  `Screenshots` folder can forge a request picture; it is validated like any request (and rate limited).
  Cleanup deletes only pictures that decoded as MacroForge requests, but also any JPEG screenshot the
  game wrote within 5 seconds after one, so a screenshot of your own taken in that window can be lost.
- Third-party data (wago.tools tables, the wowdev listfile) is trusted for being correct, not malicious: it
  is size-limited and format-checked, but there is no signature to verify.
- The game's chat window and addon API can change in a client update; the Lua checks are defensive but are
  only exercised in the game (see the checklist in `docs/TESTING.md`).

## Removing everything
`python macroforge.py startup --remove`, delete the `MacroForge*` folders in your AddOns folder, delete
`%LOCALAPPDATA%\macroforge-agent`, and delete the project folder.
