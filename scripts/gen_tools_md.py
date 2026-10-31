"""Generate docs/TOOLS.md from the live MCP server definition (so the doc can't drift from the code).

    uv run python scripts/gen_tools_md.py          # rewrite docs/TOOLS.md
    uv run python scripts/gen_tools_md.py --check  # exit 1 if docs/TOOLS.md is out of date
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client

ROOT = Path(__file__).parents[1]
OUT = ROOT / "docs" / "TOOLS.md"


def _type(s: dict[str, Any]) -> str:
    if "enum" in s:
        return " \\| ".join(f"`{v}`" for v in s["enum"])
    if "anyOf" in s:
        return " or ".join(_type(x) for x in s["anyOf"] if x.get("type") != "null") + " (optional)"
    if "$ref" in s:
        return s["$ref"].rsplit("/", 1)[-1]
    if s.get("type") == "array":
        return f"list of {_type(s.get('items', {}))}"
    if s.get("type") == "object" and "additionalProperties" in s:
        return f"object of {_type(s['additionalProperties'])}"
    return str(s.get("type", "any"))


async def render() -> str:
    from contextbridge.core.app import open_app
    from contextbridge.mcp.server import build_server

    with tempfile.TemporaryDirectory() as d:
        app = open_app(d, log_to_stderr=False)
        try:
            async with Client(build_server(app)) as c:
                tools = (await c.list_tools()).tools
                prompts = (await c.list_prompts()).prompts
                resources = (await c.list_resources()).resources
                templates = (await c.list_resource_templates()).resource_templates
        finally:
            app.close()
    out = ["# ContextBridge MCP reference", "",
           "_Generated from the server by `scripts/gen_tools_md.py`; a test fails if this file is out of date._", "",
           f"{len(tools)} tools, {len(prompts)} prompts, {len(resources) + len(templates)} resources. Every "
           "project-scoped tool takes an explicit `project` slug and returns a `checkpoint_status` block. Errors "
           "come back as `isError: true` with a JSON body `{code, message, hint}`; codes: `not_found`, "
           "`invalid_input`, `conflict`, `provenance_violation`, `too_large`, `confirm_required`, `db_busy`, "
           "`internal`.", "", "## Tools", ""]
    for t in tools:
        a = t.annotations
        flags = [f for f, on in (("read-only", a and a.read_only_hint), ("destructive", a and a.destructive_hint),
                                 ("idempotent", a and a.idempotent_hint)) if on]
        out += [f"### `{t.name}`" + (f" ({', '.join(flags)})" if flags else ""), "", (t.description or "").strip(), ""]
        props = t.input_schema.get("properties", {})
        req = set(t.input_schema.get("required", []))
        if props:
            out += ["| Parameter | Type | Required | Notes |", "|---|---|---|---|"]
            for name, s in props.items():
                note = (s.get("description") or "").replace("|", "\\|").replace("\n", " ")
                if "default" in s and s["default"] not in (None, [], {}):
                    note += f" Default: `{s['default']}`."
                out.append(f"| `{name}` | {_type(s)} | {'yes' if name in req else ''} | {note.strip()} |")
            out.append("")
        defs = t.input_schema.get("$defs", {})
        for dname, d in defs.items():
            out += [f"<details><summary><code>{dname}</code> fields</summary>", "",
                    "| Field | Type | Required | Notes |", "|---|---|---|---|"]
            dreq = set(d.get("required", []))
            for fname, fs in d.get("properties", {}).items():
                note = (fs.get("description") or "").replace("|", "\\|").replace("\n", " ")
                out.append(f"| `{fname}` | {_type(fs)} | {'yes' if fname in dreq else ''} | {note} |")
            out += ["", "</details>", ""]
    out += ["## Prompts", ""]
    for p in prompts:
        args = ", ".join(f"`{x.name}`" for x in p.arguments or [])
        out.append(f"- **`{p.name}`** ({args}): {p.description}")
    out += ["", "## Resources", ""]
    for r in resources:
        out.append(f"- `{r.uri}`: {r.description}")
    for rt in templates:
        out.append(f"- `{rt.uri_template}`: {rt.description}")
    return "\n".join(out) + "\n"


def main() -> int:
    text = asyncio.run(render())
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("docs/TOOLS.md is out of date: run `uv run python scripts/gen_tools_md.py`", file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
