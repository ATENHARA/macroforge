# Compatibility report - build 1.60.1 (toc 16001, build 70334)
_Generated from `/mf probe` at 2026-10-09 16:44:42. Re-run on each client update; results are for this build only._

Control (made-up conditional) gave pos=`Y` neg=`N`.

## Slash commands from the wiki page
| command | on this client |
|---|---|
| /assist | yes |
| /cancelaura | yes |
| /cancelform | yes |
| /cancelqueuedspell | yes |
| /cast | yes |
| /castrandom | yes |
| /castsequence | yes |
| /clearfocus | yes |
| /cleartarget | yes |
| /click | yes |
| /console | yes |
| /dismount | yes |
| /dump | yes |
| /emote | yes |
| /equip | yes |
| /equipset | yes |
| /equipslot | yes |
| /focus | yes |
| /guild | yes |
| /logout | yes |
| /officer | yes |
| /party | yes |
| /petaggressive | yes |
| /petassist | yes |
| /petattack | yes |
| /petautocastoff | yes |
| /petautocaston | yes |
| /petautocasttoggle | yes |
| /petdefensive | yes |
| /petdismiss | yes |
| /petfollow | yes |
| /petmoveto | yes |
| /petpassive | yes |
| /petstay | yes |
| /raid | yes |
| /random | yes |
| /readycheck | yes |
| /reload | yes |
| /run | yes |
| /say | yes |
| /script | yes |
| /startattack | yes |
| /stopattack | yes |
| /stopcasting | yes |
| /stopmacro | yes |
| /stopspelltarget | yes |
| /target | yes |
| /targetenemy | yes |
| /targetenemyplayer | yes |
| /targetexact | yes |
| /targetfriend | yes |
| /targetfriendplayer | yes |
| /targetlastenemy | yes |
| /targetlastfriend | yes |
| /targetlasttarget | yes |
| /targetmarker | yes |
| /targetparty | yes |
| /targetraid | yes |
| /use | yes |
| /userandom | yes |
| /usetoy | yes |
| /whisper | yes |
| /worldmarker | yes |
| /yell | yes |

306 other slash commands exist (game and addons; not listed).

## Conditionals
`[X]` / `[noX]` are the live answers right now; `[X,noX]` is the state-independent test (N = real conditional, Y = ignored by the parser).

| conditional | result | [X] | [noX] | [X,noX] |
|---|---|---|---|---|
| `actionbar:1` | supported | Y | N | N |
| `advflyable` | supported | N | Y | N |
| `bar:1` | supported | Y | N | N |
| `bonusbar` | supported | N | Y | N |
| `btn:1` | supported | Y | N | N |
| `button:2` | supported | N | Y | N |
| `canexitvehicle` | supported | N | Y | N |
| `channeling` | supported | N | Y | N |
| `combat` | supported | N | Y | N |
| `cursor` | supported | N | Y | N |
| `dead` | supported | N | Y | N |
| `equipped:Shields` | supported | N | Y | N |
| `exists` | supported | N | Y | N |
| `extrabar` | supported | N | Y | N |
| `flyable` | supported | N | Y | N |
| `flying` | supported | N | Y | N |
| `form:1` | supported | N | Y | N |
| `group` | supported | N | Y | N |
| `group:party` | supported | N | Y | N |
| `group:raid` | supported | N | Y | N |
| `harm` | supported | N | Y | N |
| `help` | supported | N | Y | N |
| `house:inside` | supported | N | Y | N |
| `indoors` | supported | N | Y | N |
| `known:Attack` | supported | Y | N | N |
| `mod:alt` | supported | N | Y | N |
| `mod:ctrl` | supported | N | Y | N |
| `mod:shift` | supported | N | Y | N |
| `modifier` | supported | N | Y | N |
| `mounted` | supported | N | Y | N |
| `outdoors` | supported | Y | N | N |
| `overridebar` | supported | N | Y | N |
| `party` | supported | N | Y | N |
| `pet` | supported | N | Y | N |
| `petbattle` | supported | N | Y | N |
| `possessbar` | supported | N | Y | N |
| `pvpcombat` | supported | N | Y | N |
| `raid` | supported | N | Y | N |
| `resting` | supported | N | Y | N |
| `shapeshift` | supported | N | Y | N |
| `spec:1` | supported | Y | N | N |
| `stance:1` | supported | N | Y | N |
| `stealth` | supported | N | Y | N |
| `swimming` | supported | N | Y | N |
| `talent:1/1` | supported | N | Y | N |
| `unithasvehicleui` | supported | N | Y | N |
| `vehicleui` | supported | N | Y | N |
| `worn:Shields` | supported | N | Y | N |

## Target forms (`[@unit]`)
| unit | result |
|---|---|
| @cursor | Y@cursor |
| @focus | Y@focus |
| @mouseover | Y@mouseover |
| @none | Y@none |
| @party1 | Y@party1 |
| @pet | Y@pet |
| @player | Y@player |
| @target | Y@target |
| @targettarget | Y@targettarget |

## API presence
| name | type |
|---|---|
| C_AddOns | table |
| C_Item | table |
| C_Macro | table |
| C_Spell | table |
| C_SpellBook | table |
| C_Timer | table |
| CreateMacro | function |
| DeleteMacro | function |
| EditMacro | function |
| GetBuildInfo | function |
| GetMacroInfo | function |
| GetNumMacros | function |
| GetNumSpellTabs | nil |
| GetSpellBookItemName | nil |
| GetSpellInfo | nil |
| GetSpellTabInfo | nil |
| GetSpellTexture | nil |
| GetUnitName | function |
| IsPlayerSpell | function |
| IsSpellKnown | function |
| MAX_ACCOUNT_MACROS | nil |
| MAX_CHARACTER_MACROS | nil |
| SecureCmdList | nil |
| SecureCmdOptionParse | function |
| UnitFullName | function |
| hash_SlashCmdList | table |
