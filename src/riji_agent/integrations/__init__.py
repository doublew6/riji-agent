"""Self-contained adapters that connect external systems to riji-agent.

These modules run on the Hermes side and only ever speak to riji-agent over its
HTTP boundary. They never touch the vault, SQLite, the index or model keys.
"""
