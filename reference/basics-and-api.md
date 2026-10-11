# Basics, icons and API

## Hard limits
- Body 255 characters, name 16 characters.
- **Forever (confirmed in game):** 120 account macros + 18 per character (character macros start at index 121).
- Creating, editing and deleting macros through the API is blocked in combat.
- No delays: the whole macro runs at once. Only one global-cooldown spell can fire per click.
- `/run` and `/script`: one line each; they cannot cast spells, use items or change targets.

## `/castsequence`
`/castsequence [conds] reset=<opts> A, B, C`. Options: `reset=30` (seconds), `target`, `combat`, `shift`, `alt`, `ctrl`, combined with `/` (e.g. `reset=combat/30`). The sequence advances only when a cast succeeds.

## Icons and tooltips
- `#showtooltip` (optionally `#showtooltip Spell` or with conditionals) shows that spell's tooltip, cooldown and icon. Must be the first line, lowercase.
- With the question-mark icon (134400) the button shows the spell's icon automatically, so a fixed icon is only needed for non-spell macros or to override.
- Icons are numeric FileDataIDs. `python macroforge.py spell "Fear"` and `python macroforge.py icon skull` look them up.

## Writing compact macros (fewer lines, fewer characters)
The 255-character limit is usually the real constraint, so prefer one command with several groups over repeated commands.

- **One `/cast`, many spells.** Each `[group]` carries its own action, and the first true group wins:
  `/cast [mod:shift] Spell A; [pet:Imp] Spell B; Spell C`
  beats three separate `/cast` lines, saves the repeated `/cast ` text, and cannot fire two spells by accident.
- **Don't repeat what the default already does.** With no `@unit` the action uses your target, so `[harm,nodead]` needs no `@target`. (Exception: pet spells, where an explicit `@target` fallback is needed; see Pet macros.)
- **Share conditions with `/`.** `[pet:Incubus/Succubus]`, `[mod:shift/ctrl]`, `[stance:1/2]` replace separate groups.
- **Fallback with `[]` or a bare spell.** `/cast [@mouseover,harm,nodead][] Spell` means "mouseover enemy, else whatever my target is". Use it when a wrong target is acceptable; drop it when it is not.
- **Drop conditions that add nothing.** `harm` already implies the unit exists; `nodead` is only needed if casting on a corpse matters. Each dropped word saves characters.
- **Use `#showtooltip` bare** (no spell name) to let the client pick the icon and tooltip from the first castable spell; it costs 12 characters and often replaces a hand-set icon. It shows "?" for pet spells.
- **Let `/castsequence` hold sequences** instead of several `/cast` lines: `/castsequence reset=combat A, B, C`.
- **`/stopmacro [cond]`** ends a macro early so you don't wrap every later line in the same condition.
- **Check the count:** `python macroforge.py build` rejects anything over 255 and `check` warns about unknown commands, conditionals and spell names.

## Macro API (used by the addon)
```lua
CreateMacro(name, iconFileID, body, perCharacter)  -- returns index; limits above
name, iconFileID, body, isLocal = GetMacroInfo(index)
EditMacro(index, name, icon, body)
DeleteMacro(index)
GetNumMacros()  -- numAccount, numCharacter
```

## Getting data out of the game: what was tried (2026-10-09)
- **SavedVariables:** written only on `/reload`, logout or exit. This is the channel in use.
- **Chat log** (`Logs\WoWChatLog.txt`, switched on by an addon with `LoggingChat(true)`): works, and a
  whisper to your own character is logged with both lines, but the game **writes the file only when
  the session closes**. A say message, loot and a `/reload` produced nothing for six minutes; the whole
  log appeared at once at logout. Not usable for requests while playing.
- **Self-whispers** work on the Forever client (`SendChatMessage(text, "WHISPER", nil, "First Last")`
  from a typed command), but they pass through Blizzard's chat servers and the policy bans "excessive"
  chat use, so MacroForge does not send chat messages.
- **Screenshots (game to PC): works.** `Screenshot()` writes the picture to `Screenshots\` immediately.
  Setting `screenshotFormat` to `tga`, waiting 0.5 s and then calling `Screenshot()` writes one TGA and
  nothing else (checked 2026-10-10 by watching the folder for several requests, including the first after a
  `/reload`; an earlier note of a second JPEG was wrong, those were the player's own screenshots); the
  format was `jpeg` before and is restored afterwards.
  A strip of pure-color 8-pixel squares (3 bits each) in the top-left corner decoded exactly in five of
  five tries at 3840x2160 (`mforge/stripcodec.py`, `addon/MacroForge/Codec.lua`). This is the request
  channel for `/mf ask`: no reload.
- **Sound-file signal (PC to game): does not work live.** `PlaySoundFile(path)` returns whether the file
  exists (an empty file counts, a missing one gives nil), but the game only refreshes its view of the
  files after `/reload`: a file created or deleted while playing did not show up. Dropped.
- **Timer-driven slot load: works.** `C_Timer.After` followed by `LoadAddOn` of a fresh slot needs no key
  press, so the addon polls spare slots for the reply (`/mf ask` waits 12 s, then checks every 10 s for
  up to three minutes, one slot per check). A `/reload` resets the slots.

## Real client vs the wiki: `GetMacroInfo`
The wiki says `GetMacroInfo(index)` returns `name, icon, body, isLocal`. On the Forever client a real
export shows **no 4th value**: the per-character flag is absent. Account macros are indices 1..120 and
character macros 121..138, so MacroForge decides by `index > number of account macros`.

## Confirmed on the Forever beta (toc 16001; first seen on build 1.60.1.70245, unchanged on 70291)
- `CreateMacro`, `EditMacro`, `DeleteMacro`, `GetMacroInfo` (4th return = isLocal) work as above.
- The game stores each body with a **trailing newline**; compare trimmed text.
- Macros are saved to `WTF/Account/<acct>/macros-cache.txt` (account) and `.../<realm>/<Name-Surname>/macros-cache.txt` (character). Format: `VER 3 <16-hex id> "name" "iconID"`, body lines, `END`. Account ids start at `0000000000000001`, character ids at `0100000000000001`.
- Characters have surnames: `UnitName("player")` returns first name then surname as the second value; `GetUnitName("player", true)` returns "Firstname Surname".
- Slash command `/dump select(4, GetBuildInfo())` returns toc `16001`.

## Live macro updates (confirmed 2026-10-07)
- A load-on-demand addon that has never been loaded is read fresh from disk by `C_AddOns.LoadAddOn`, so data written after the game started reaches the addon with no `/reload`. The addon folder must exist at **launch**; each such addon loads once per UI session; `/reload` resets them.
- `CreateMacro`/`EditMacro`/`DeleteMacro` work from that path (confirmed: a macro was created and one was removed live).
- Getting a request *out* of the game still needs `/reload`/logout (SavedVariables) or the pixel-capture trick used by wow-ai; typed-outside-the-game requests need neither.

## Client engine (from the first probe, 2026-10-07)
- Forever runs on a **modern client engine** with Classic content. Present: `C_Spell`, `C_SpellBook`, `C_AddOns`, `C_Item`, `C_Macro`, `C_Timer`, `hash_SlashCmdList`, `SecureCmdOptionParse`. Absent: the old `GetSpellInfo`, `GetSpellTexture`, `GetSpellTabInfo`, `GetSpellBookItemName`, and `SecureCmdList`. Addon code written for Classic Era's spell API will not work; use `C_Spell`/`C_SpellBook`.
- The client defines 369 slash commands, including every command on the wiki list.
- The macro parser **treats a conditional it does not recognise as true** and prints `Unknown macro option: <name>` in chat. So a typo like `[harmm]` still runs the macro, just without that filter (it will cast on the wrong target). Watch chat after first use of a new macro; `macroforge.py check` also catches typos before you ever log in. (Seeing a few "Unknown macro option: zzbogus" lines when running `/mf probe` is expected: that is the deliberate control test.)
- All 48 conditionals probed on 2026-10-07 were recognised (`compat-16001.md`), including retail ones (`advflyable`, `house:inside`, `petbattle`, `pvpcombat`, `spec`, `talent`). Recognised means the shared parser knows the name, not that it can ever be true in Classic content.

## Pet macros (confirmed 2026-10-07 on warlock demons; other pet classes untested)
- `/cast <pet spell>` works for pet abilities (Fire Shield on an Imp, Seduction on an Incubus). Pet spell names equal the names on the pet bar (`/mf export` records them under `pet.actions`).
- `[pet:X]` matches the pet's **creature family or its own name**; `/` means "any of": `[pet:Incubus/Succubus]` works. Family names confirmed: `Imp`, `Incubus`, `Felhunter`, `Succubus`, `Voidwalker`.
- One `/cast` can hold a different spell per group: `/cast [pet:A,...] Spell 1; [pet:B,...] Spell 2`. The first true group wins, which is the way to save characters.
- Commanding a pet to cast at a living enemy also sends it to attack, even if the spell cannot work on that target type (e.g. a non-humanoid). Conditionals cannot see creature type, so a macro cannot prevent this.
- `#showtooltip` shows a "?" for macros whose spells are pet spells (they are not in the player's spellbook). Set a fixed icon instead.
- Keep an explicit `@target` in the fallback group (`[@mouseover,...][@target,...]`): the version without it stopped casting. (Not isolated by a clean test, but the working macro has it.)

## To confirm with the probe
Which slash commands, conditionals and APIs exist on this build. Run `/mf probe`, `/reload`, then `python macroforge.py report`.
