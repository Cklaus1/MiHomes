"""Native in-app chat services (SPEC-012).

Importing the package installs the after-commit event publisher (`events`), so any code that
writes chat rows through these services gets live delivery without a separate setup call.
"""

from mihomes.services.chat import events  # noqa: F401  (installs the after_commit listener)
