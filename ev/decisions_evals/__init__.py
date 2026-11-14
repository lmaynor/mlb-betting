"""Offline fixture-evaluation harness package for Decisions routes.

PURPOSE:
ev/decisions_evals/ is the offline fixture-evaluation harness package for
Decisions routes. It is the only place live Decisions API calls happen
outside production routes, and only when a human runs an eval module
explicitly with GEMINI_API_KEY set.

MANUAL EXECUTION:
Eval modules are run manually (python -m ev/decisions_evals.eval_<route>).

CACHING:
Eval modules cache raw answers next to the fixture so re-scoring after
a gate change does not re-call the API.

PYTEST ISOLATION:
Pytest never runs them. Nothing in the pytest suite may import the live
path in a way that makes a call during tests.
"""
