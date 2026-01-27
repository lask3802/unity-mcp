"""Tool to read MCP resources by URI."""
from __future__ import annotations

import inspect
import re
from typing import Annotated, Any
from urllib.parse import urlparse, parse_qs

from fastmcp import Context

from services.registry import mcp_for_unity_tool, get_registered_resources
from services.tools.utils import parse_json_payload


def _compile_uri_template(template: str) -> re.Pattern:
    """Compile a resource URI template into a regex pattern."""
    # Strip any template query placeholders (e.g., {?unity_instance})
    base = template.split("{?")[0]

    # Escape and replace {param} with named groups
    pattern = re.escape(base)
    pattern = re.sub(r"\\\{([a-zA-Z_][a-zA-Z0-9_]*)\\\}", r"(?P<\1>[^/]+)", pattern)
    return re.compile(rf"^{pattern}$")


def _normalize_params(params: Any) -> dict[str, Any]:
    parsed = parse_json_payload(params)
    if isinstance(parsed, dict):
        return parsed
    return {}


@mcp_for_unity_tool(
    description=(
        "Read an MCP resource by URI (e.g., mcpforunity://scene/gameobject/{id}/components). "
        "Use this to access resource endpoints from tools when the client cannot read resources directly."
    )
)
async def read_resource(
    ctx: Context,
    uri: Annotated[str, "Resource URI to read (e.g., mcpforunity://scene/gameobject/123/components)"] ,
    params: Annotated[dict[str, Any] | str | None, "Optional params dict/JSON string to pass to the resource"] = None,
) -> dict[str, Any]:
    if not uri:
        return {"success": False, "message": "Missing required parameter 'uri'."}

    # Prepare params from input and URI query string
    merged_params: dict[str, Any] = {}
    merged_params.update(_normalize_params(params))

    parsed = urlparse(uri)
    if parsed.query:
        q = parse_qs(parsed.query, keep_blank_values=True)
        for key, values in q.items():
            if values:
                # use the last value if repeated
                merged_params.setdefault(key, values[-1])

    # Find matching resource by URI template
    resources = get_registered_resources()
    for resource_info in resources:
        template = resource_info.get("uri")
        func = resource_info.get("func")
        if not template or func is None:
            continue

        regex = _compile_uri_template(template)
        match = regex.match(uri.split("?")[0])
        if not match:
            continue

        # Merge path params from URI template
        path_params = match.groupdict()
        merged_params = {**merged_params, **path_params}

        # Filter params to function signature
        sig = inspect.signature(func)
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        call_kwargs = {}
        for name, param in sig.parameters.items():
            if name == "ctx":
                continue
            if name in merged_params:
                call_kwargs[name] = merged_params[name]
            elif param.default is inspect.Parameter.empty:
                # Required parameter missing
                return {
                    "success": False,
                    "message": f"Missing required resource parameter '{name}' for uri '{template}'."
                }

        # If the function accepts **kwargs, include any extra params
        if accepts_kwargs:
            extras = {k: v for k, v in merged_params.items() if k not in call_kwargs}
            call_kwargs.update(extras)

        try:
            response = await func(ctx, **call_kwargs)
            # MCPResponse is Pydantic model; normalize to dict for tool output
            if hasattr(response, "model_dump"):
                return response.model_dump()
            if isinstance(response, dict):
                return response
            return {"success": True, "data": response}
        except Exception as e:  # pragma: no cover - defensive
            return {"success": False, "message": f"Error reading resource '{template}': {e!s}"}

    return {"success": False, "message": f"No matching resource found for uri '{uri}'."}
