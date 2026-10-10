# Slash commands

Source: warcraft.wiki.gg Macro_commands. Command names are not case sensitive. Several commands can sit in one macro, one per line. Forever availability is `[?]` for everything until `compat-16001.md` exists; the wiki page gives no per-version notes.

## Casting and items
| Command | Does | Notes |
|---|---|---|
| `/cast` | Cast a spell or use an item | name conflict resolves to the **spell** |
| `/use` | Use an item or spell | name conflict resolves to the **item** |
| `/castsequence` | Cast spells in order | `reset=` options in basics-and-api.md |
| `/castrandom`, `/userandom` | Random spell/item from a list | |
| `/usetoy` | Use a toy | retail feature `[?]` |
| `/cancelaura` | Remove a buff you have | |
| `/cancelform` | Leave your shapeshift form | |
| `/cancelqueuedspell` | Cancel the queued spell | |
| `/stopcasting` | Stop casting/channeling | |
| `/stopspelltarget` | Cancel a spell awaiting a ground target | |
| `/startattack`, `/stopattack` | Auto-attack on/off | |
| `/dismount` | Dismount | |

## Equipment
`/equip <item>`, `/equipslot <slot#> <item>`, `/equipset <name>` (Equipment Manager `[?]` on Classic).

## Targeting and focus
| Command | Does |
|---|---|
| `/target <name>`, `/targetexact <name>` | by name / exact name |
| `/targetenemy`, `/targetenemyplayer` | cycle hostile units / players |
| `/targetfriend`, `/targetfriendplayer` | cycle friendly units / players |
| `/targetparty`, `/targetraid` | cycle party / raid members |
| `/targetlasttarget`, `/targetlastenemy`, `/targetlastfriend` | previous selections |
| `/assist [unit]` | target that unit's target |
| `/cleartarget` | clear target |
| `/focus`, `/clearfocus` | set/clear focus `[?]` focus exists in Classic Era 1.15+ |

## Pets
`/petattack`, `/petfollow`, `/petstay`, `/petmoveto`, `/petpassive`, `/petdefensive`, `/petassist`, `/petdismiss`, `/petautocaston|off|toggle <spell>`.
`/petaggressive` is listed as disabled (replaced by assist in 4.2) but an older note says Classic keeps Aggressive. Test with the probe.

## Party / raid / PvP
`/invite`, `/uninvite`, `/promote`, `/readycheck`, `/maintank`, `/maintankoff`, `/mainassist`, `/mainassistoff`, `/targetmarker <n>`, `/worldmarker`, `/clearworldmarker`, `/raidinfo`, loot: `/ffa`, `/group`, `/master`, `/threshold`.

## Chat
`/say`, `/yell`, `/party`, `/raid`, `/rw` (raid warning), `/guild`, `/officer`, `/whisper` (alias `/tell`), `/reply`, `/emote`, `/afk`, `/dnd`, `/battleground`, channels: `/join`, `/leave`, `/csay`, `/chatinvite`, `/chatlist`, `/ban`, `/unban`, `/mute`, `/unmute`, `/moderator`, `/unmoderator`, `/owner`, `/password`, `/announce`, `/ckick`, `/chatlog`, `/combatlog`, `/resetchat`, `/chathelp`.
Chat messages can use chat substitutions (e.g. `%t` for target name).

## Emotes
Large list (`/wave`, `/dance`, `/cheer`, ...). Not worth enumerating by hand: the probe's "other slash commands" section lists exactly what this client has.

## Script and system
| Command | Does |
|---|---|
| `/run`, `/script` | run one line of Lua (cannot cast, use items or change targets) |
| `/dump <expr>` | print a value (handy: `/dump GetBuildInfo()`) |
| `/stopmacro [cond]` | end the macro early |
| `/click <Frame>` | click a UI button |
| `/console`, `/reload`, `/logout`, `/quit`, `/random [x [y]]`, `/played`, `/time`, `/who`, `/help`, `/macrohelp` | misc |
| `/enableaddons`, `/disableaddons` | toggle all addons |

## Developer tools
`/api`, `/tableinspect`, `/eventtrace`, `/framestack`, `/dump`.

## UI windows
`/macro`, `/achievements`, `/calendar`, `/guildfinder`, `/dungeonfinder`, `/raidfinder` (`/lfr`), `/loot`, `/stopwatch`.

## Metacommands (first lines of a macro)
`#show` (icon), `#showtooltip` (icon + tooltip). Unknown `#` lines are silently ignored.

## Disabled
`/usetalents` (does nothing), `/petaggressive` (see Pets).

## Retail-only, assume absent on Forever until probed
`/summonpet`, `/dismisspet`, `/randomfavoritepet` (battle pets), `/share`, `/usetoy`, `/dungeonfinder` and `/raidfinder` as modern UIs.
