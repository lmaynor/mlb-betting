"""Scheduler dispatcher (OPS-15 Tranche 2): fires the low-frequency tail
of Cloud Run jobs from one checked-in table instead of dozens of
per-definition Cloud Scheduler entries. See run.py for the design."""
