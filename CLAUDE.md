# MacroForge: instructions for Claude

## When the user asks for a macro (or to change or remove one)
1. Read `reference/agent-brief.md` (syntax and spec rules) and `macros.json`. Use `reference/` and
   `data/spells_<class>.json` for details. The player can be any class.
2. Edit `macros.json` only. `char` is the full character name; omit it for an account-wide macro; use the
   `remove` list to delete.
3. Run `python macroforge.py build` (validates length, icons, commands, conditionals, spell names; fix every
   warning) and then `python macroforge.py install`.
4. Tell the user to type `/mf live` in game (no reload). If slots are unavailable, `/reload` works instead.
5. Say what you are unsure of. Syntax and pet behavior are only confirmed where `reference/` says so.

Do not use `/script`, `/run`, `/dump` or `/console` in macros. The in-game request feature (`/mf ask` +
`watch`) is optional; do not set it up unless asked.

## When changing the code
- Layout: `macroforge.py` is a thin entry point; the library is the `mforge/` package (one job per module;
  `/mf ask` requests arrive as screenshots: `stripcodec.py` decodes them, `shots.py` finds and cleans up
  the pictures, the addon's `Codec.lua` draws them, `UI.lua` is the minimap button and window; `setup.py` is the first-run wizard behind `setup.cmd`);
  prompt text and command/conditional lists live in `mforge/resources/`, not in code.
- Run the tests before and after: `python -m unittest discover -s tests` (about 25 seconds, no game needed).
  `tests/test_style.py` enforces the style rules below.
- Rules: library code raises `MacroForgeError` subclasses (only `cli.py` turns them into exit codes); no
  file, network or environment access at import time (call `Settings.load()` explicitly); everything that
  crosses a trust boundary (requests, agent output, saved variables, downloads, macro text) goes through
  `validate.py` or the hardened parsers; writes go through `fsutil.atomic_write_*`; lines <= 100 characters
  (120 in tests); docstrings on public functions; Python 3.11+, standard library only.
- Security-relevant changes need a test that fails without the fix; see `docs/SECURITY.md` for the model.
- The Lua addon is tested offline with LuaJIT against `tests/lua/wow_stub.lua` (skipped if LuaJIT is not
  installed: `winget install DEVCOM.LuaJIT`). Add a Lua test for every addon change, and still follow the
  in-game checklist in `docs/TESTING.md`: the fake API is not the real client. Addon code must stay valid
  Lua 5.1 (no `goto`, `\x` or `\z` escapes); `tests/lua51.py` scans for that.
- Never commit personal or machine-specific details: real names, emails, character names, account folder
  ids, local paths. Commit with the GitHub noreply identity in this repo's git config. A pre-commit hook and
  `tests/test_privacy.py` enforce it (private strings go in `.git/pii-patterns.txt`, never in the repo).
- Use the `fetch` command after a client update; the opt-in live tests (`MACROFORGE_LIVE_TESTS=1`) call the
  real Claude CLI and cost a few cents.
