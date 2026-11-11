"""Minutes-restriction program (NBA return-from-injury workload bands).

This package is shared by parallel lanes. Lane 2 owns the structured arm
(Arm S); this lane (Lane 4) owns the text arm (Arm T) only:

- ``decisions_client`` -- Google Decisions API band classifier (fail closed).
- ``arm_t`` -- loaders, decision-time blurb slicing, calibration, band-mapped
  predictions, and the S+T combiner.
- ``grade_t`` -- pre-registered kill-bar grading of Arm T vs Arm S.

Nothing in this package is re-exported at package level on purpose: the lanes
merge independently, and a shared ``__init__`` that re-exported names would be
a merge-collision surface.
"""
