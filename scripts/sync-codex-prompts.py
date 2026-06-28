#!/usr/bin/env python3
"""Generate Codex custom prompts from AI Berkshire skills.

Codex custom prompts are deprecated in favor of skills, but they provide the
slash-command style entry point that Claude Code users expect.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILLS = ROOT / "skills"
CODEX_PROMPTS = ROOT / "codex-prompts"


def split_frontmatter(text: str):
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, text
    return text[4:end], text[end + 5 :].lstrip("\n")


def first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def yaml_quote(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def prompt_for(source: Path) -> str:
    name = source.stem
    source_text = source.read_text(encoding="utf-8")
    _, body = split_frontmatter(source_text)
    title = first_heading(body, name)
    description = f"AI Berkshire slash entry for {title}."
    return (
        "---\n"
        f"description: {yaml_quote(description)}\n"
        "argument-hint: $ARGUMENTS\n"
        "---\n\n"
        f"Use the installed AI Berkshire Codex skill `{name}` for this request.\n\n"
        f"If the skill is not already loaded, read and follow "
        f"`~/ai-berkshire/codex-skills/{name}/SKILL.md`.\n\n"
        "User arguments:\n"
        "$ARGUMENTS\n"
    )


def main() -> None:
    CODEX_PROMPTS.mkdir(exist_ok=True)
    count = 0
    for source in sorted(CLAUDE_SKILLS.glob("*.md")):
        target = CODEX_PROMPTS / source.name
        target.write_text(prompt_for(source), encoding="utf-8")
        count += 1
    print(f"Generated {count} Codex prompts in {CODEX_PROMPTS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
