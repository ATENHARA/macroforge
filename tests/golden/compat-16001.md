# Compatibility report - build 1.60.1 (toc 16001, build 70291)
_Generated from `/mf probe` at 2026-10-08 17:38:18. Re-run on each client update; results are for this build only._

Control (made-up conditional) gave pos=`Y` neg=`N`.

## Slash commands from the wiki page
| command | on this client |
|---|---|
| /assist | **NO** |
| /cancelaura | **NO** |
| /cancelform | **NO** |
| /cancelqueuedspell | **NO** |
| /cast | yes |
| /castrandom | **NO** |
| /castsequence | **NO** |
| /clearfocus | **NO** |
| /cleartarget | **NO** |
| /click | **NO** |
| /console | **NO** |
| /dismount | **NO** |
| /dump | **NO** |
| /emote | **NO** |
| /equip | **NO** |
| /equipset | **NO** |
| /equipslot | **NO** |
| /focus | **NO** |
| /guild | **NO** |
| /logout | **NO** |
| /officer | **NO** |
| /party | **NO** |
| /petaggressive | **NO** |
| /petassist | **NO** |
| /petattack | **NO** |
| /petautocastoff | **NO** |
| /petautocaston | **NO** |
| /petautocasttoggle | **NO** |
| /petdefensive | **NO** |
| /petdismiss | **NO** |
| /petfollow | **NO** |
| /petmoveto | **NO** |
| /petpassive | **NO** |
| /petstay | **NO** |
| /raid | **NO** |
| /random | **NO** |
| /readycheck | **NO** |
| /reload | **NO** |
| /run | **NO** |
| /say | **NO** |
| /script | yes |
| /startattack | **NO** |
| /stopattack | **NO** |
| /stopcasting | **NO** |
| /stopmacro | **NO** |
| /stopspelltarget | **NO** |
| /target | **NO** |
| /targetenemy | **NO** |
| /targetenemyplayer | **NO** |
| /targetexact | **NO** |
| /targetfriend | **NO** |
| /targetfriendplayer | **NO** |
| /targetlastenemy | **NO** |
| /targetlastfriend | **NO** |
| /targetlasttarget | **NO** |
| /targetmarker | **NO** |
| /targetparty | **NO** |
| /targetraid | **NO** |
| /use | **NO** |
| /userandom | **NO** |
| /usetoy | **NO** |
| /whisper | **NO** |
| /worldmarker | **NO** |
| /yell | **NO** |

1 other slash commands exist (game and addons; not listed).

## Conditionals
`[X]` / `[noX]` are the live answers right now; `[X,noX]` is the state-independent test (N = real conditional, Y = ignored by the parser).

| conditional | result | [X] | [noX] | [X,noX] |
|---|---|---|---|---|
| `harm` | supported | N | Y | N |
| `madeupthing` | unsupported | Y | N | Y |
| `mod:shift` | supported | N | Y | N |

## Target forms (`[@unit]`)
| unit | result |
|---|---|
| @player | Y@player |

## API presence
| name | type |
|---|---|
| CreateMacro | function |
| GetSpellInfo | nil |
