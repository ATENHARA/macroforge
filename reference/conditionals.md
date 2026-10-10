# Conditionals

Source: warcraft.wiki.gg Macro_conditionals. The wiki does not mark Classic vs retail for individual conditionals, so Forever status is `[?]` until `compat-16001.md` confirms it.

## Syntax
```
/cast [cond1,cond2] Spell A; [cond3] Spell B; Spell C
```
- Comma = AND (all must be true).
- Separate `[...]` groups = OR; the first true group wins.
- A final action with no brackets, or an empty `[]`, is the fallback.
- `no` prefix negates (`nodead`, `nocombat`, `nomod`).
- `@unit` and `target=unit` mean the same; they apply to that single action only.
- Several values with `/`: `[mod:shift/ctrl]`, `[stance:1/2]`, `[btn:1/2]` mean "any of".

## Target selection
`@player`, `@target`, `@focus`, `@mouseover`, `@pet`, `@targettarget`, `@party1`..., `@cursor` (ground target under the mouse), `@none` (interrupt auto-self-cast).

## About the target (use with `@unit`)
| Conditional | True when |
|---|---|
| `exists` | unit exists |
| `help` / `harm` | can be helped / attacked |
| `dead` / `nodead` | unit dead / alive |
| `party` / `raid` | unit is in your party / raid |
| `unithasvehicleui` | unit is in a vehicle |

## About you
| Conditional | True when |
|---|---|
| `combat` | in combat |
| `stealth` | stealthed |
| `mounted`, `flying`, `swimming` | movement/mount state |
| `indoors` / `outdoors` | location |
| `resting` | in a rested area |
| `channeling[:spell]` | channeling (that spell) |
| `group[:party/raid]` | in a group |
| `pet[:name/family]` | have that pet |
| `form:n` / `stance:n` | in that shapeshift form / stance (number is class-specific) |
| `equipped:type` / `worn:type` | item type equipped (e.g. `Shields`, `Daggers`) |
| `known:spell` | spell is known (retail 10.0.2+, probably absent in Classic Era `[?]`) |
| `spec:n`, `talent:` | specs/talents: retail, assume absent (`talent` was removed in 10.0.0) |
| `flyable`, `advflyable`, `petbattle`, `pvpcombat`, `canexitvehicle`, `house:*` | retail-only, assume absent |

## Input and UI
| Conditional | True when |
|---|---|
| `mod[:shift/ctrl/alt]`, `modifier` | modifier key held |
| `btn:n` / `button:n` | mouse button (1 left, 2 right, 3 middle, 4-5 extra) |
| `actionbar:n` / `bar:n` | that action bar page showing |
| `bonusbar[:n]`, `extrabar`, `overridebar`, `possessbar`, `shapeshift`, `vehicleui` | special bars active |
| `cursor` | dragging something on the cursor |

## Patterns worth knowing
- Mouseover with fallback to target: `/cast [@mouseover,harm,nodead][] Spell`
- Self-cast on friendly click: `/cast [@mouseover,help,nodead][@target,help][@player] Spell`
- Modifier variants: `/cast [mod:shift] Spell B; Spell A`
- Stop a macro early: `/stopmacro [combat]`

## History
`talent` conditional removed in 10.0.0; `known` added 10.0.2; `advflyable` 10.0.7; `house` 11.2.7. All retail; use the probe for Forever.
