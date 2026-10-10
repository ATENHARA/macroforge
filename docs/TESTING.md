# Testing

## Automated (no game, no network, no real Claude)
```
python -m unittest discover -s tests          # all of it (about 25 seconds)
python -m unittest tests.test_validate -v     # one module
```
Only the standard library is used. What is covered:

| Module | Covers |
|---|---|
| `test_cli_golden.py` | the command line against a fake game folder; generated addon data, slot files and cache files compared with golden files in `tests/golden/` |
| `test_validate.py` | request, macro and reply validation (hostile and malformed inputs) |
| `test_lua_savedvars.py` | Lua string escaping round trips, the saved-variables parser, depth and size limits, a seeded 2,000-case fuzz test |
| `test_units.py` | settings, atomic writes, the checker, the cache writer, install and slots, the probe report |
| `test_agent_watcher.py` | the watcher and agent end to end with a fake `claude` process: good and hostile results, timeouts, login errors, rate limit, lock, environment allowlist, sandbox cleanup |
| `test_fetch_startup_cli.py` | downloads from a local server (oversize, wrong content, hostile redirect), the startup launcher, command-line smoke tests |
| `test_style.py` | line length, docstrings, unused imports, no `SystemExit` or import-time I/O in the library |
| `test_review_findings.py` | one regression test per finding of the independent security review |
| `test_lua_addon.py` | the Lua addon against a fake WoW API (needs LuaJIT, skipped otherwise) and the Lua 5.1 syntax scan |

To change a golden file on purpose: `set UPDATE_GOLDEN=1`, run the tests once, review the diff, commit.

## The Lua addon, offline (LuaJIT)
The addon files are run against a fake copy of the WoW API (`tests/lua/wow_stub.lua`), by
`tests/lua/run.lua`, through `tests/test_lua_addon.py`. About 35 behavior tests cover: macros created,
updated, removed and de-duplicated; surnames and character matching; combat; malformed and hostile data;
chat escapes; replies; `/mf live` with slots (fresh, stale, missing, used up, damaged); `/mf ask`;
`/mf export`, `probe` and `petcheck`. A second test feeds the addon the real files the Python tool
generates (with quotes, backslashes, `]]`, accented and Chinese text, newlines and `%` in the macros)
and checks that exactly the right macros come out.

Install LuaJIT once: `winget install DEVCOM.LuaJIT` (or set the `LUAJIT` environment variable to
`luajit.exe`). Without it these tests are skipped and everything else still runs.

Limits you should know about:
- LuaJIT is the closest interpreter available (it reports Lua 5.1) but it also accepts syntax the real
  game does not (`goto`, `\x41` and `\z` escapes, 64-bit literals). `tests/lua51.py` scans every addon
  file and every generated data file for those constructs.
- The fake API is only as accurate as its documented assumptions (macro index ranges, the trailing
  newline the game adds, unknown conditionals counting as true). It finds logic and crash bugs, not
  differences in how the real client behaves.

## Opt-in live tests (real Claude CLI, a few cents)
```
set MACROFORGE_LIVE_TESTS=1
python -m unittest tests.test_live -v
```
Needs the standalone CLI installed and signed in. They check that `--restricted` mode works with your
login, that the agent produces a valid macro, and that its file tools cannot leave the workspace even
when the model is willing.

## In-game checklist (what only the real client can show)
The offline tests above cover the addon's logic. Do this after changing `addon/MacroForge/*.lua` or after
a client update, to confirm the real client agrees with the fake one.

1. `python macroforge.py build` and `install`, then `/reload`. No Lua errors; `/mf` prints nothing or a
   count line.
2. `/mf live` -> `no new macros (rev ...)`. Add a macro to `macros.json`, `build`, `install`, `/mf live`
   -> `1 created`; it appears in `/macro`.
3. Remove it (add to `remove`), `build`, `install`, `/mf live` -> `1 removed`.
4. Break a data entry on purpose (set a macro name over 64 characters in `build/MacroForge/MacroForgeData.lua`
   and copy it over): `/mf` reports `skipped a malformed macro entry` and the other macros still apply.
5. `/mf export` then `/reload`, `python macroforge.py spellbook` shows your spells, pet and identity.
6. `/mf probe`, `/reload`, `python macroforge.py report`: all conditionals classified.
7. In-game requests: start the watcher (`python macroforge.py startup --now`), `/mf ask make a macro that
   ...`. Colored squares flash in the top-left corner and the chat says the request was sent. Within about
   12 seconds: "Your PC has the request and is working on it." Then the reply prints, the macro appears,
   and you hear the ready-check sound; no reload and no `/mf live`. `data/watch.log` has the request,
   reply and cost, and the request pictures are gone from the `Screenshots` folder.
   With the watcher stopped you should instead see "Your PC has not picked up the request yet" after about
   12 seconds, and the request is still answered if you start the watcher within ten minutes (use `/mf live`).
8. Hostile reply text: put `|cffff0000RED|r |Hitem:19019|h[X]|h` into `data/replies.json` as a reply, run
   `build` and `install`, `/mf live`: the chat shows the text with the pipes visible, no color or link.
11. Window and minimap button (the fake client cannot show how it looks): after `/reload` a round button
    appears on the minimap and can be dragged around it; left-click opens the window, right-click opens it
    with Advanced showing; Send, Apply new macros, Export, Probe and Reload UI each do what their
    caption says; the status line follows a request; Esc closes the window; "Show minimap button" and
    `/mf button` hide and show the button. Look for layout problems (overlapping text, cut-off buttons).
10. Fresh install (needs a second PC or a friend; cannot be tested on the dev machine): unzip the repo,
    double-click `setup.cmd`. Check: Python missing gives the message with the
    download link; a game in `Program Files` without administrator rights gives the "run as administrator"
    message; a normal run ends with the "fully quit" reminder; after restarting the game `/mf live` says
    `no new macros` and `/mf ask` works if the Claude CLI was set up. The automated tests cover the wizard
    against a fake game folder (`tests/test_setup.py`) but not the real download or the Windows prompts.
9. Slots: use `/mf live` repeatedly until it says all slots are used, then `/reload` and confirm it works
   again.
