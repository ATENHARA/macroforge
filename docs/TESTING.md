# Testing

## Automated (no game, no network, no real Claude)
```
python -m unittest discover -s tests          # everything: about 570 tests, about two and a half minutes
python -m unittest tests.test_validate -v     # one module
```
Only the standard library is used (the Lua tests also need LuaJIT, below).

| Module | Covers |
|---|---|
| `test_cli_golden` | the command line against a fake game folder; generated addon data, slot files and cache files compared with golden files in `tests/golden/` |
| `test_validate` | request, macro and reply validation, including hostile and malformed input |
| `test_lua_savedvars` | Lua string escaping round trips, the saved-variables parser, depth and size limits, a seeded 2,000-case fuzz test |
| `test_units` | settings, atomic writes, the checker, the cache writer, install (including removing stale addon files) and slots, the probe report |
| `test_agent_watcher` | the watcher and agent end to end with a fake `claude` process: good and hostile results, timeouts, login errors, rate limit, lock, environment allowlist, sandbox cleanup, replies and acknowledgements |
| `test_shots` | requests arriving as screenshots: scanning, deleting handled pictures (including locked files), the detached watcher launch |
| `test_strip` | the request strip: encoder/decoder round trips, the TGA reader, and the Lua encoder producing the same cells as the Python one |
| `test_pets` | the pet-ability table built from the game data and handed to the agent |
| `test_accounts` | several WoW accounts: the watcher reads every account's saved data, picks the export of the requesting character, and the raw cache route refuses to guess |
| `test_setup` | the setup wizard against a fake game folder: finding the game and account, menus, data download or refresh, unwritable AddOns folder, optional watcher setup, choosing Claude, GPT or a local model |
| `test_keystore` | the Credential Manager wrapper (a real save, read, overwrite and delete of a throwaway entry on Windows) and how the chat backend finds a key |
| `test_llm` | the OpenAI-style backend against a fake chat server on this PC: prompt contents, JSON answers and repair retries, merge rules, HTTP errors, timeouts, redirects, key handling and redaction, settings validation, `llm-test` |
| `test_fetch_startup_cli` | downloads from a local server (oversize, wrong content, hostile redirect), the startup launcher, command-line smoke tests |
| `test_review_findings` | one regression test per finding of the independent security review |
| `test_luacheck` | the Lua lexer, parser and script checker: accepted and refused scripts, bypass attempts, a seeded fuzz test, parity with LuaJIT's own compiler |
| `test_luacheck_bypass` | one regression test per script-checker bypass found by the adversarial review, plus the ordinary scripts that must stay allowed, parser parity (`break`, nested `[[`) and the `/run` line rules the addon shares |
| `test_hardening` | robustness findings of the review: hostile agent or endpoint output, files that are not text, the watcher's catch-all reply, name matching, launcher encoding, linear-time parsing and picture scanning |
| `test_script_mode` | request mode end to end: payload, validation, the two-way gate in the watcher, spec entries, replies |
| `test_script_prompts` | what the AI is told for scripts (both backends), workspace files, context-file setting |
| `test_script_examples` | every vetted example fits a macro, passes the checker and runs against a fake game under LuaJIT |
| `test_suggest` | the macro ideas and script examples: every entry validated against the class spell data, the commands and the script checker; rendering into the generated Lua; the shipped file (3 per class) |
| `test_script_probe` | the game's function list from the probe: loading, report counts, checking against it |
| `test_lua_addon` | the Lua addon against a fake WoW API, and the Lua 5.1 syntax scan |
| `test_style` | line length, docstrings, unused imports, no `SystemExit` or import-time I/O in the library |
| `test_privacy` | no emails, Windows user paths, account folder names or other personal details in tracked files (plus a private list in `.git/pii-patterns.txt`, see `AGENTS.md`) |

To change a golden file on purpose: `set UPDATE_GOLDEN=1`, run the tests once, review the diff, commit.

## The Lua addon, offline (LuaJIT)
`tests/test_lua_addon.py` runs the real addon files, including the window, against a fake copy of the WoW
API (`tests/lua/wow_stub.lua`) through `tests/lua/run.lua`. About 60 behavior tests cover: macros created,
updated, removed and de-duplicated; surnames and character matching; combat; malformed and hostile data;
chat escapes; replies; `/mf live` with slots; `/mf ask` (screenshot, acknowledgement, slot polling, sound,
timeout); the minimap button and window (clicks, dragging, every label inside the window); export and
probe. A second test feeds the addon the real files the Python tool generates (quotes, backslashes,
`]]`, accented and Chinese text, newlines, `%`) and checks that exactly the right macros come out.

Install LuaJIT once: `winget install DEVCOM.LuaJIT` (or set the `LUAJIT` environment variable to
`luajit.exe`). Without it these tests are skipped and everything else still runs.

Limits:
- LuaJIT reports Lua 5.1 but also accepts syntax the game does not (`goto`, `\x41` and `\z` escapes, 64-bit
  literals). `tests/lua51.py` scans every addon file and generated data file for those.
- The fake API is only as accurate as its documented assumptions (macro index ranges, the trailing
  newline the game adds, unknown conditionals counting as true). It finds logic and crash bugs, not
  differences in how the real client behaves or looks.

## Opt-in live tests (real Claude CLI, a few cents)
```
set MACROFORGE_LIVE_TESTS=1
python -m unittest tests.test_live -v
```
Needs the standalone CLI installed and signed in. They check that `--restricted` mode works with your
login, that the agent produces a valid macro, and that its file tools cannot leave the workspace even when
the model is willing.

## In the real game
Only the real client shows how the window looks, how chat renders text and how `LoadAddOn` behaves. Run
this after changing the addon (`addon/MacroForge/*.lua`) and after a client update. Last run in full on
2026-10-09 (build 70334); the in-game request and the window were rechecked on 2026-10-10 (build 70338).

1. **Install:** `python macroforge.py build`, `install`, then `/reload`. No Lua errors, and a round button
   appears on the minimap.
2. **Apply:** `/mf live` says `no new macros`. Add a macro to `macros.json`, `build`, `install`,
   `/mf live` -> `1 created` and it is in `/macro`. Put it in `remove` and repeat -> `1 removed`.
3. **Export and probe:** `/mf export`, `/reload`, `python macroforge.py spellbook` shows your spells, pet and
   identity. After a client update also `/mf probe`, `/reload`, `python macroforge.py report`.
4. **In-game request** (watcher running: `python macroforge.py startup --now`): click the minimap button,
   type a request, Send. Colored squares flash in the top-left corner; within about 12 seconds the status
   says the PC is working on it; then the reply prints, the macro appears and the ready-check sound plays,
   with no reload. `data/watch.log` has the request, reply, model and cost, and the request pictures are gone
   from the `Screenshots` folder. With the watcher stopped you should see "has not picked up the request"
   after about 12 seconds, and starting it within ten minutes still answers (fetch with `/mf live`).
5. **Window:** left-click opens the simple view, right-click opens it with Advanced showing, the button
   drags around the minimap, Esc closes the window, "Show minimap button" and `/mf button` hide and show
   it. Look for overlapping text and cut-off buttons.
6. **Scripts:** `/mf probe`, `/reload`, `python macroforge.py report` (the report shows the script API
   counts). `/mf script print my coordinates` (or the Script button): the script appears, the one-time
   notice explains Blizzard's warning, the warning appears on the first press (note its exact wording), and
   the macro prints the coordinates. Press each example in `reference/script-examples.md` once; any that
   errors in game must be fixed or removed there.
7. **Ideas and history:** click **Macro ideas**: a panel opens beside the window (on the left if there is no
   room) with three cards for your class, each with its icon, a short explanation and the macro text. **Add**
   puts the macro on this character and the button changes to **Added**; press it in game and note that it works
   (several ideas assume Forever-specific spell and stance behavior). Switch to **Script** and open **Script
   examples**; add one. **History** shows your last requests and replies; **Use again** fills the request box.
   Check that the panel text does not overlap and that Esc closes the panel, then the window.
8. **Questions:** the empty request box shows a grey hint that disappears when you type. Ask "what does
   [mod:shift] do in a macro?": the chat shows an answer starting with `[No macro changes]` and the status line says
   nothing was changed (not "your macro is ready"). Ask something off-topic: it is declined in a line.
9. **Bulk changes:** ask for "delete all my macros" when you have 3 or more: the reply lists them and says to add
   CONFIRM, and nothing changes. Repeat with "confirm: delete all my macros" (use a throwaway character).
10. **Hostile text:** put `|cffff0000RED|r |Hitem:19019|h[X]|h` into `data/replies.json` as a reply, `build`,
   `install`, `/mf live`: the chat shows the text with the pipes visible, no color and no link.

Before sharing a new version, also try a **fresh install** on another PC (the real download and the Windows
prompts cannot be tested here): unzip the repository and double-click `setup.cmd`. Python missing should
show the download message; a game in `Program Files` without administrator rights should show the "run as
administrator" message; a normal run ends with the "fully quit" reminder; after restarting the game
`/mf live` says `no new macros`, and `/mf ask` works if the Claude CLI was set up.
