"""Display names for app-authored text. Stored user content is never rewritten."""
from typing import Dict


def get_identity() -> Dict[str, str]:
    from db import db
    return db.get_app_identity()


def names_in_template(template: str) -> str:
    """Render only trusted, app-authored prompt or UI templates."""
    identity = get_identity()
    return (template
            .replace("Maverick", "\u0000ASSISTANT_FULL\u0000")
            .replace("Mav", "\u0000ASSISTANT\u0000")
            .replace("岚", "\u0000USER\u0000")
            .replace("\u0000ASSISTANT_FULL\u0000", identity["assistant_name"])
            .replace("\u0000ASSISTANT\u0000", identity["assistant_name"])
            .replace("\u0000USER\u0000", identity["user_name"]))


def names_in_prompt(prompt: str, protected_values: list[str]) -> str:
    """Keep quoted conversation and other stored material byte-for-byte intact."""
    masked = prompt
    kept = []
    for value in protected_values:
        if value:
            location = masked.rfind(value)
            if location < 0:
                continue
            marker = f"\u0000PROTECTED_{len(kept)}\u0000"
            masked = masked[:location] + marker + masked[location + len(value):]
            kept.append(value)
    rendered = names_in_template(masked)
    for index, value in enumerate(kept):
        rendered = rendered.replace(f"\u0000PROTECTED_{index}\u0000", value)
    return rendered
