from __future__ import annotations

# OpenAI-format tool definitions for the Theoretician and Supervisor agents.
# Each dict is passed directly to the `tools` parameter of chat completions.

PYTHON_CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "Python_code_interpreter",
        "description": "Execute python code and return the stdout/stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python script to execute.",
                }
            },
            "required": ["code"],
        },
    },
}


LOAD_SKILL_SPECS_TOOL = {
    "type": "function",
    "function": {
        "name": "load_skill_specs",
        "description": (
            "Load installed Codex-style skill files by skill name from SKILL.md. "
            "The returned text provides authoritative workflow guidance and should be treated as the primary source of truth for the selected skill(s). "
            "Returns plain text that contains a [SKILL FULL] header and one or more <SKILL_FULL> blocks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Non-empty list of installed skill names, e.g. ['openai-docs'].",
                }
            },
            "required": ["skill_names"],
            "additionalProperties": False,
        },
    },
}


LIBRARY_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "library_search",
        "description": (
            "Search arXiv for relevant physics papers. "
            "Returns titles, authors, abstracts, and PDF links. "
            "Supports field prefixes: ti: (title), au: (author), abs: (abstract), cat: (category). "
            "You can also look up a specific paper by its arXiv ID, e.g. query='2301.08727'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query or arXiv ID."},
                "top_k": {"type": "integer", "description": "Number of results to return.", "default": 5},
            },
            "required": ["query"],
        },
    },
}


PRIOR_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "prior_search",
        "description": "Search LANDAU prior knowledge base for relevant chunks.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "top_k": {
                    "type": "integer",
                    "description": "Number of chunks to return.",
                    "default": 3,
                },
                "expand_context": {
                    "type": "boolean",
                    "description": "Include prev/next chunks for context.",
                    "default": False,
                },
                "return_format": {
                    "type": "string",
                    "description": "Return text for prompt or raw JSON.",
                    "enum": ["text", "json"],
                    "default": "text",
                },
                "source_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional source file filters, e.g. ['bonnerot_2022.pdf'].",
                },
                "chapter": {
                    "type": "string",
                    "description": "Optional chapter filter.",
                },
                "section_prefix": {
                    "type": "string",
                    "description": "Optional section prefix filter, e.g. '2.1'.",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional keyword filters.",
                },
                "rewrite_query": {
                    "type": "boolean",
                    "description": "Whether to apply query rewrite/expansion before retrieval.",
                    "default": True,
                },
            },
            "required": ["query"],
        },
    },
}

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the general web for physics references, definitions, "
            "constants, or standard results. Use this when you need "
            "information beyond arXiv (e.g., Wikipedia, NIST, Physics "
            "Stack Exchange). Prefer `library_search` for research papers. "
            "Returns snippets with URLs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Short English search query using technical terms."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results.",
                    "default": 3
                },
            },
            "required": ["query"],
        },
    },
}


CODATA_TOOL = {
    "type": "function",
    "function": {
        "name": "codata_lookup",
        "description": (
            "Look up the exact value of a physical constant from the "
            "authoritative CODATA table. Always use this instead of "
            "recalling constants from memory. "
            "Examples: 'planck_constant', 'speed_of_light', "
            "'gravitational_constant', 'electron_mass'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "snake_case constant name."
                }
            },
            "required": ["name"],
        },
    },
}




# Bundles used in different agent contexts
THEORETICIAN_CORE_TOOLS = [PYTHON_CODE_TOOL, LOAD_SKILL_SPECS_TOOL]
LIBRARY_TOOLS = [LIBRARY_SEARCH_TOOL]
WEB_TOOLS = [WEB_SEARCH_TOOL, CODATA_TOOL]
