"""Job titles suggested from the master resume, merged into the roles the user keeps."""

from uuid import uuid4


ROLE_METADATA = ("family", "fit", "reason", "origin")


def merge_roles(state: dict, roles: list) -> int:
    """Show the complete suggested list; returns how many titles are new.

    Titles still suggested are refreshed, new ones are added unchecked, and
    unchecked suggestions that no longer fit are dropped. Checked roles, roles
    the user added, and roles the user removed (never suggested again) are respected.
    """
    dismissed = {name.casefold() for name in state.setdefault("dismissed_positions", [])}
    suggested = {}
    for role in roles:
        key = role.title.strip().casefold()
        if key and key not in dismissed:
            suggested.setdefault(key, role)
    kept = []
    for item in state.get("positions", []):
        role = suggested.pop(item["name"].casefold(), None)
        if role is not None:
            item.update(family=role.family, fit=role.fit, reason=role.reason, origin=item.get("origin") or "suggestion")
        elif item.get("origin") == "suggestion" and not item.get("confirmed"):
            continue
        kept.append(item)
    new = [{"id": uuid4().hex, "name": role.title.strip(), "confirmed": False, "origin": "suggestion",
            "family": role.family, "fit": role.fit, "reason": role.reason} for role in suggested.values()]
    state["positions"] = kept + new
    return len(new)
