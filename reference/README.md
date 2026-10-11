# Macro reference

Written for WoW: Forever (Classic+; the client build and TOC number are in the newest `compat-<toc>.md`) but sourced from the community wiki, which documents all versions together and does **not** say which command or conditional exists on which client.

## How versions are handled
1. The files below are the wiki-derived reference. Status marks: `[?]` = unconfirmed on Forever, `[ok]` = confirmed by in-game test.
2. `/mf probe` in game asks the client what it really supports (slash commands, conditionals, API presence) and stamps the result with `GetBuildInfo` (version, build, TOC).
3. `python macroforge.py report` turns that into `compat-<toc>.md`. That file is the source of truth for a given build; regenerate it after each client update.
4. `python macroforge.py check` (also run by `build`) validates macros against the probe data when present, otherwise against the wiki list.

## Files
| File | What's in it |
|---|---|
| [commands.md](commands.md) | Slash commands grouped by purpose |
| [conditionals.md](conditionals.md) | `[bracket]` syntax and every conditional |
| [basics-and-api.md](basics-and-api.md) | Limits, icons, `#showtooltip`, macro API, Forever findings |
| `compat-<toc>.md` | Generated per build by `report` (appears after the first probe) |

## Sources
- https://warcraft.wiki.gg/wiki/Macro_commands (the maintained home of https://wowpedia.fandom.com/wiki/Macro_commands, which blocks automated fetching)
- https://warcraft.wiki.gg/wiki/Macro_conditionals
- https://warcraft.wiki.gg/wiki/Making_a_macro
- https://warcraft.wiki.gg/wiki/Slash_commands
- https://warcraft.wiki.gg/wiki/API_CreateMacro

## Rules of the road
Addon policy (https://eu.forums.blizzard.com/en/wow/t/wow-user-interface-add-on-development-policy/1642): free, readable code, no ads, no harm to realms. The probe is read-only introspection and MacroForge never automates gameplay.
