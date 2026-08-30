# Contributing

Thanks for your interest in python-can-helix!

- **Bug reports and feature requests**: please open an issue on GitHub.
  Include your python-can version, Python version, and (for connection
  issues) debug logs with `can_helix` loggers at DEBUG level.
  Never include passwords or session tokens in issues or logs.
- **Code contributions**: day-to-day development happens in an internal
  repository; this repository receives curated release snapshots. Small
  merge requests (typo fixes, docs) are welcome and will be ported into
  the internal tree. For larger changes, please open an issue first so
  we can discuss the approach before you invest time.
- **Testing**: `pip install -e .[dev]`, then `pytest`. Hardware is not
  required for the test suite.
