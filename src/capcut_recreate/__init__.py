"""capcut-recreate: deterministic slot filling for CapCut drafts.

The LLM (if any) only produces a plan. Everything that touches a draft file
goes through capcut-cli, and every write is followed by our own validation.
"""

__version__ = "0.1.0"
