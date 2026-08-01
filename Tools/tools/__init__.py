"""Tools.tools -- individual tool plugin modules.

Each module here exposes a module-level `TOOL_SPEC` (see Tools/registry.py
for the ToolSpec shape) and is imported and appended to Tools/registry.py's
TOOLS list. This package intentionally has no other shared logic -- keeping
each tool self-contained is what makes "expandable" true in practice.
"""
