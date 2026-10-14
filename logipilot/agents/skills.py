"""Skill registry: one `skills/<intent>/SKILL.md` per intent, parsed once at startup.

The frontmatter is the routing and binding contract (Jev routes on `description`, the specialist may
only call `tools`, the master asks for `required_fields` before acting). The markdown body is the
specialist's playbook and becomes its system prompt.
"""

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tools: tuple[str, ...]
    required_fields: tuple[str, ...]
    examples: tuple[str, ...]
    playbook: str


def parse(path: Path) -> Skill:
    _, front, body = path.read_text(encoding="utf-8").split("---", 2)
    meta = yaml.safe_load(front)
    return Skill(
        name=meta["name"],
        description=meta["description"],
        tools=tuple(meta.get("tools") or ()),
        required_fields=tuple(meta.get("required_fields") or ()),
        examples=tuple(meta.get("examples") or ()),
        playbook=body.strip(),
    )


@functools.cache
def registry() -> dict[str, Skill]:
    skills = {s.name: s for s in map(parse, sorted(SKILLS_DIR.glob("*/SKILL.md")))}
    if "general" not in skills:
        raise RuntimeError(f"skills/general/SKILL.md is required; found {sorted(skills)}")
    return skills
