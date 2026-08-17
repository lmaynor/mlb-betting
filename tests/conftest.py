"""
tests/conftest.py -- session-wide test setup.

Eagerly import native-extension-backed libraries (xgboost, sklearn.metrics)
before pytest collects any test module. Observed on this repo's test venv
(an ad hoc, unpinned Python 3.14 environment used because Python 3.14 has no
wheels for the pinned numpy==1.26.4/pandas==2.2.3/scikit-learn==1.6.1 --
production itself runs Python 3.11 with those exact pins and does not hit
this): collecting the full test suite could intermittently leave
`sklearn.metrics` in a partially-initialized state by the time a later test
module tried `from sklearn.metrics import log_loss`, raising `ImportError:
cannot import name 'log_loss' from '<unknown module name>'` -- a collection-
order-dependent failure that never reproduced when the same module was
imported alone or via a plain (non-pytest) import of every test file in
order. Importing these up front, once, before collection begins, avoids it.
"""
import sklearn.metrics  # noqa: F401
import xgboost  # noqa: F401
