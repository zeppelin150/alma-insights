"""
Alma Insights — Startup check implementations.

Each module exports a single `check_*()` function returning a CheckResult.
The splash wires these into a Checker in the documented order:

     1. integrity.check_integrity        (added Phase 4)
     2. environment.check_environment
     3. credentials.check_credentials
     4. gemini.check_gemini_oauth
     5. updates.check_for_update
     6. airgap.check_environment_guard
     7. hardware.check_hardware
     8. model.check_embedding_model
     9. database.check_database
    10. config.check_config
"""

from src.startup.checks.airgap import check_environment_guard
from src.startup.checks.config import check_config
from src.startup.checks.credentials import check_credentials
from src.startup.checks.database import check_database
from src.startup.checks.environment import check_environment
from src.startup.checks.gemini import check_gemini_oauth
from src.startup.checks.hardware import check_hardware
from src.startup.checks.integrity import check_integrity
from src.startup.checks.model import check_embedding_model
from src.startup.checks.updates import check_for_update


DEFAULT_CHECKS = [
    ("integrity",        check_integrity),
    ("environment",      check_environment),
    ("credentials",      check_credentials),
    ("gemini_oauth",     check_gemini_oauth),
    ("update",           check_for_update),
    ("env_guard",        check_environment_guard),
    ("hardware",         check_hardware),
    ("embedding_model",  check_embedding_model),
    ("database",         check_database),
    ("config",           check_config),
]
