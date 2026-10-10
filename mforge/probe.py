"""Turn the results of the in-game ``/mf probe`` into a compatibility report."""
from __future__ import annotations

from dataclasses import dataclass

from .assets import load_toml
from .errors import ValidationError


def wiki_commands() -> set[str]:
    """Slash commands from the wiki's command list (no per-version information)."""
    return set(load_toml("commands.toml")["commands"])


def command_aliases() -> set[str]:
    """Short forms of wiki commands that agent-written macros may also use."""
    return set(load_toml("commands.toml")["aliases"])


def wiki_conditionals() -> set[str]:
    """Macro conditionals from the wiki's list."""
    return set(load_toml("conditionals.toml")["conditionals"])


def classify(cond: dict, control: dict) -> str:
    """Return ``supported``, ``unsupported``, ``unconfirmed`` or ``error`` for a probed conditional.

    ``[X,noX]`` can never be true for a real conditional, but the parser ignores unknown ones and
    treats them as true, so that result separates the two regardless of game state.
    """
    pair, control_pair = (cond["pos"], cond["neg"]), (control["pos"], control["neg"])
    if "ERROR" in pair:
        return "error"
    if "both" in cond:
        return {"N": "supported", "Y": "unsupported"}.get(cond["both"], "error")
    if pair != control_pair:
        return "supported"
    # identical to an unknown conditional: it can only be called unsupported if the control can
    # never be true; otherwise "false right now" looks the same and we cannot tell
    return "unsupported" if "Y" not in control_pair else "unconfirmed"


@dataclass(frozen=True)
class Report:
    """A rendered compatibility report and the numbers printed after it."""

    toc: int
    text: str
    slash_count: int
    supported: int
    total: int


def render_report(probe: dict) -> Report:
    """Build ``reference/compat-<toc>.md`` from the ``MacroForgeProbe`` saved variable."""
    try:
        build = probe["build"]
        toc = int(build["toc"])
        control = probe["control"]
        slash = {str(c).lstrip("/") for c in probe["slash"]}
        conditionals = probe["conditionals"]
        stamp = probe["time"]
        targets, api = probe["targets"], probe["api"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"probe data is incomplete or malformed: {exc!r}") from exc
    wiki = wiki_commands()
    lines = [
        f"# Compatibility report - build {build['version']} (toc {toc}, build {build['build']})",
        f"_Generated from `/mf probe` at {stamp}. Re-run on each client update; "
        "results are for this build only._", "",
        f"Control (made-up conditional) gave pos=`{control['pos']}` neg=`{control['neg']}`.", "",
        "## Slash commands from the wiki page", "| command | on this client |", "|---|---|"]
    for command in sorted(wiki):
        lines.append(f"| /{command} | {'yes' if command in slash else '**NO**'} |")
    extra = sorted(slash - wiki)
    # only a count: the other commands include those of the player's installed addons
    lines += ["", f"{len(extra)} other slash commands exist (game and addons; not listed).",
              "", "## Conditionals",
              "`[X]` / `[noX]` are the live answers right now; `[X,noX]` is the "
              "state-independent test (N = real conditional, Y = ignored by the parser).", "",
              "| conditional | result | [X] | [noX] | [X,noX] |", "|---|---|---|---|---|"]
    for token, cond in sorted(conditionals.items()):
        lines.append(f"| `{token}` | {classify(cond, control)} | {cond['pos']} | {cond['neg']} | "
                     f"{cond.get('both', '-')} |")
    lines += ["", "## Target forms (`[@unit]`)", "| unit | result |", "|---|---|"]
    lines += [f"| @{unit} | {result} |" for unit, result in sorted(targets.items())]
    lines += ["", "## API presence", "| name | type |", "|---|---|"]
    lines += [f"| {name} | {kind} |" for name, kind in sorted(api.items())]
    supported = sum(classify(c, control) == "supported" for c in conditionals.values())
    return Report(toc=toc, text="\n".join(lines) + "\n", slash_count=len(slash),
                  supported=supported, total=len(conditionals))
