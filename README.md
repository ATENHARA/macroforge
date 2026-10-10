# MacroForge

Describe a macro in plain English and have it appear in your WoW macro list, with the right icon, no
copy-pasting and no reload. Built for **WoW Forever**; works for any class.

- **In game:** click the minimap button, type what you want, press Send. A few seconds later the macro
  is there (needs the optional watcher, see [In-game requests](#in-game-requests-optional)).
- **Or** describe it to an AI assistant (Claude Code, Codex, ...) or edit `macros.json` yourself, then
  `build` and `install`; type `/mf live` in game.
- Checks every macro before it reaches the game: length limits, real commands and conditionals, spell names.

Pure Python standard library, nothing to pip install.

## Requirements
- Windows, WoW Forever installed (the beta, or the live game once it launches).
- **Python 3.11 or newer** (`setup.cmd` tells you where to get it if it is missing).
- Optional, only for the in-game request feature: an AI to answer it: the **Claude Code** command-line
  program (signed in), or an OpenAI-style API key, or a model on your PC. See
  [In-game requests](#in-game-requests-optional).

## Setup (once)
Put this folder somewhere permanent and double-click **`setup.cmd`** (the game can be running). It finds
your game, downloads the game data (about 150 MB, first run only), installs the addon and asks only what
it cannot work out. If WoW was running, fully quit and
restart it afterwards, then type `/mf live` in game: it should say `no new macros`.

Run `setup.cmd` again any time; it keeps your `macros.json` and `config.json`. To update, unzip the new
version over the old folder and run it again.

<details><summary>Manual setup</summary>

1. Copy `config.example.json` to `config.json` and set `wow_dir` to your client folder (the one with
   `Interface` and `WTF`). See [Configuration](#configuration).
2. `python macroforge.py fetch` (game data; a few minutes), `python macroforge.py slots`,
   `python macroforge.py build`, `python macroforge.py install`.
3. Fully restart WoW, then `/mf live` should say `no new macros`.
</details>

## Everyday workflow
1. Describe a macro to Claude (Claude Code or the Claude desktop app, opened in this folder), or edit
   `macros.json` yourself. `AGENTS.md` (read by Claude Code and other assistants) explains the loop.
2. `python macroforge.py build` validates the macros (length, icons, unknown commands, conditionals and
   spell names) and generates the addon data. `python macroforge.py install` copies it into the game.
3. In game type **`/mf live`**: the macros appear with no reload (about 100 uses per session; `/reload`
   resets them). If it reports no slots, or after a client update, use `/reload`.

## macros.json
Works for any class. Your `macros.json` is personal and untracked; the first run creates it from
`macros.example.json`.
```json
{
  "classes": { "Firstname Surname": "mage" },
  "remove": [],
  "macros": [
    { "name": "Frostbolt MO", "char": "Firstname Surname", "icon": "spell:Frostbolt",
      "body": "#showtooltip Frostbolt\n/cast [@mouseover,harm,nodead][@target,harm,nodead] Frostbolt" },
    { "name": "Auto Attack", "icon": "spell:Attack", "body": "#showtooltip Attack\n/startattack" }
  ]
}
```
- `char`: the full character name (spaces, hyphens and case are ignored). Omit it for an account-wide macro.
- `icon`: a number, `"spell:Name"`, `"icon:file_name"`, or omit it (question mark: with `#showtooltip` the
  client shows the spell's own icon).
- `classes` maps a character to its class so `check` can verify spell names (`/mf ask` fills it in).
- `remove` lists macros to delete: `{ "name": "OldMacro", "char": "Firstname Surname" }`.
- Limits (checked on every build): name 16 characters, body 255 **bytes**, 120 account and 18 per-character
  macros (configurable). Control characters and `|` escape sequences are rejected.

## Commands
| Command | Does |
|---|---|
| `fetch [--build B] [--no-icons]` | download the game tables and icon list for your client build (run after every client update) |
| `slots [--count N]` | create the load-on-demand slot addons used by `/mf live` and `/mf ask` (default 120; restart WoW once afterwards) |
| `build` | `macros.json` -> `build/MacroForge/`, then check the macros |
| `install` | copy the built addon into the game's AddOns folder and refresh the slots |
| `check` | validate commands, conditionals and spell names |
| `icon <words>` / `spell <name>` | look up icon ids |
| `spells <class> [--max-level N] [--find text]` | every spell a class can learn |
| `report` | turn `/mf probe` results into `reference/compat-<toc>.md` |
| `spellbook` | print the saved variables the addon wrote |
| `setup` | guided first-time setup (what `setup.cmd` runs) |
| `shotcheck` | diagnostic: decode the request strip in the newest TGA screenshot |
| `cache [--write]` | raw-file route: merge macros into `macros-cache.txt` (game closed; `--write` backs up first) |
| `watch`, `login`, `startup [--remove] [--now]`, `rollback` | the optional in-game request feature |

## In-game commands
| Command | Does |
|---|---|
| `/mf` | apply the macros now (also runs at login and `/reload`) |
| `/mf live` | apply the newest macros **without `/reload`** (uses one of the slots) |
| `/mf export` | save your spellbook, macros, pet and identity to the saved variables |
| `/mf probe` | read-only capability test: slash commands, conditionals and API presence of this client |
| `/mf ui` | open the window (also the minimap button): ask for a macro, apply new macros, advanced tools |
| `/mf ask <text>` | optional: send a plain-English request to the watcher; the macro is added automatically (no reload) |

After `export` or `probe`, run `/reload` so the game writes the file, then use the matching command above.

## In-game requests (optional)
Not required. This lets you type a request in the game instead of switching to Claude. It needs a small
program running on your PC (a game cannot start programs) and uses your own AI account (Claude by default): a request
costs a few cents of usage.

**One-time:** install the standalone Claude Code CLI (PowerShell: `irm https://claude.ai/install.ps1 | iex`,
or `winget install Anthropic.ClaudeCode`, or `npm install -g @anthropic-ai/claude-code`), then run
`python macroforge.py login` and type `/login`. Being signed in to the desktop app is not enough; if the
desktop app is a packaged (Microsoft Store style) install, its bundled copy is not visible outside it.

**Run:** `python macroforge.py watch` (leave it open), or `python macroforge.py startup --now` to start it
hidden now and at every Windows login (log: `data/watcher.log`; `startup --remove` undoes it).
Then in game click the minimap button (or `/mf ui`), type what you want and press Send. Or type
`/mf ask a macro that heals my mouseover, else my target, else me`. No reload and nothing to type
afterwards:

1. The addon draws your request as a small strip of colored squares in the top-left corner for about two
   seconds and takes a screenshot (it switches the screenshot format to TGA for that moment).
2. The watcher reads the picture from the game's `Screenshots` folder, tells the game "got it", and runs the
   agent. About 10 to 40 seconds later it writes the reply and macros into the addon data.
3. Meanwhile the addon loads a spare slot every 10 seconds (the first check is after 12 seconds). When the
   reply for your request is there, it creates the macros, prints the reply and plays the ready-check sound.
   If the watcher is not running you are told after about 12 seconds; if nothing arrives within three
   minutes it stops; `/mf live` fetches a late reply. Each check uses one slot; `/reload` resets them.

Request pictures are deleted after they are handled (the setting `delete_request_shots`; if the game still
holds a file the watcher retries for a minute and then logs which file to delete by hand). Every request is
recorded in `data/watch.log` before the picture goes.

**Using GPT or a local model instead of Claude:** choose it in `setup.cmd` (or run
`python macroforge.py setup --backend openai` or `--backend local`), or set it in `config.json`:
```json
{ "agent_backend": "openai", "agent_model": "your-model-name", "llm_base_url": "https://api.openai.com/v1" }
```
Any service with an OpenAI-style chat endpoint works: OpenAI, OpenRouter, Ollama
(`http://localhost:11434/v1`), LM Studio (`http://localhost:1234/v1`) and others. Setup lets you keep your API
key in Windows Credential Manager (recommended: Windows' encrypted vault for saved passwords) or in an
environment variable you set yourself (`MACROFORGE_API_KEY`, takes priority); it is never in `config.json`.
Test the connection with `python macroforge.py llm-test`. Small local models make more mistakes: use an
instruct model of about 7-8B or larger with a context of at least 8192 tokens (Ollama:
`OLLAMA_CONTEXT_LENGTH=8192`). The rules file the AI reads can be swapped with `agent_context_file`.
Requests then go to that provider (or stay on your PC for a local model); there is no cost cap, only the
hourly request limit.

**Safety:** the agent runs in a throwaway folder with copies of a few files, no shell and no web access.
Its result is used only if it validates (safe command list, real conditionals, size limits). `macros.json`
is backed up before every request (`rollback` restores it) and every request is logged in `data/watch.log`
with its cost. Details: [docs/SECURITY.md](docs/SECURITY.md).

## Configuration
`config.json` (all keys optional; unknown keys are reported):

| Key | Default | Meaning |
|---|---|---|
| `wow_dir` | `C:\Program Files (x86)\World of Warcraft\_classic_beta_` | the client folder containing `Interface` and `WTF` |
| `account` | auto-detected | folder under `WTF/Account` (needed only with several accounts) |
| `product` | derived from `wow_dir` | product name in `.build.info` (`wow_classic_beta`) |
| `max_account_macros`, `max_char_macros` | 120, 18 | macro slot limits |
| `claude_path` | auto | full path of `claude.exe` (executed as-is; must exist) |
| `agent_backend` | `claude` | `claude`, or `openai` for any OpenAI-style endpoint (GPT, Ollama, LM Studio, ...) |
| `agent_model` | CLI default (Sonnet 5.5 so far) | model for in-game requests (required for `openai`); `claude-haiku-5-5` used about 15x less in our test, with slightly less careful macros |
| `llm_base_url`, `llm_api_key_env`, `llm_max_tokens`, `llm_json_mode`, `llm_retries`, `agent_context_file` | see `config.example.json` | settings for `agent_backend: openai` and a custom rules file |
| `agent_max_budget_usd` | 0.5 | spend cap per request (0 disables) |
| `agent_timeout_seconds` | 300 | time limit per request |
| `delete_request_shots` | true | delete the request screenshots (and the JPEG written just after) once handled |
| `max_requests_per_hour` | 10 | hourly cap (kept across restarts) |
| `max_request_chars` | 500 | longest accepted request text |
| `blocked_commands` | script, run, dump, console, tinspect, click, logout, guild admin, ... (see `mforge/resources/defaults.toml`) | slash commands the agent may never write |
| `fetch_allowed_hosts` | wago.tools, api.github.com, github.com, githubusercontent.com | hosts `fetch` may contact |
| `fetch_allow_http`, `fetch_wago_url`, `fetch_listfile_api` | https defaults | advanced; used by the tests |

## After a client update
```
python macroforge.py fetch            # new game data (add --no-icons to skip the big icon list)
python macroforge.py build
python macroforge.py install
```
Then in game `/mf probe`, `/reload`, and `python macroforge.py report` to refresh the compatibility report.
Slot addons normally survive updates; if `/mf live` says they are missing, run `slots` again and restart WoW.

## Security
MacroForge writes only inside its own folder, the addon's folders in your AddOns directory
(`MacroForge` and `MacroForge_S###`) and, only with `cache --write`, the game's `macros-cache.txt`
(backed up first). Everything read from the game or the network is validated. See
[docs/SECURITY.md](docs/SECURITY.md) for the threat model and what is and is not protected.
Written to follow Blizzard's addon policy (free, readable code, no ads); the addon creates macros and
does not automate gameplay. Not affiliated with or endorsed by Blizzard Entertainment. Game data (spell
tables, icon names) is downloaded from wago.tools and the wow-listfile on your PC; none of it is bundled.

## Development and tests
```
python -m unittest discover -s tests          # ~340 tests, no game or network needed
                                              # (the Lua addon tests need LuaJIT: winget install DEVCOM.LuaJIT)
MACROFORGE_LIVE_TESTS=1 python -m unittest tests.test_live -v    # optional, calls the real Claude CLI
```
Layout: `macroforge.py` (entry), `mforge/` (library; `resources/` holds the prompt and command lists),
`addon/MacroForge/` (Lua), `tests/`, `reference/` (macro syntax notes), `docs/`.
See [docs/TESTING.md](docs/TESTING.md), including the in-game checklist for the Lua addon.

## License
MIT, see [LICENSE](LICENSE).

## Known gaps
- The Lua addon is tested offline against a fake WoW API (optional LuaJIT install, see
  [docs/TESTING.md](docs/TESTING.md)); only the real game shows how the real client behaves, so the
  in-game checklist is still needed after addon changes and client updates.
- Talents are not in the spell table (only trainable class spells).
- The defaults point at the beta client folder (`_classic_beta_`). `setup` finds other client folders
  by itself, and the defaults will be updated when the game launches.
- Forever's real macro limits are inferred (120/18) from where character macros start.
