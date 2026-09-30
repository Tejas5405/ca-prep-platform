"""Fast unit tests that need neither PostgreSQL nor Redis.

Separate from ``tests/`` root and from ``tests/integration/`` for the same reason
the integration package is: what a test needs to run should be visible from where
it lives, and a reader should be able to tell "this proves a rule" from "this
proves the schema is real" without reading the test body.
"""
