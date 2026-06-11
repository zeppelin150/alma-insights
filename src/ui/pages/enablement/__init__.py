"""Enablement Workbench UI package.

Four pages under the Enablement tab (plan: ~/.claude/plans/guru-enablement-redesign.md):
    workbench.py — card render canvas + action-taking chat sidebar (the hero)
    tasks.py     — running task list with subtasks + scratch pad
    calendar.py  — due-date calendar
    settings.py  — ETL source config (Asana/Drive/Guru)
"""

from src.ui.pages.enablement.calendar import CalendarPage
from src.ui.pages.enablement.page import EnablementPage
from src.ui.pages.enablement.settings import SettingsPage
from src.ui.pages.enablement.tasks import TasksPage
from src.ui.pages.enablement.workbench import WorkbenchPage

__all__ = ["EnablementPage", "WorkbenchPage", "TasksPage", "CalendarPage", "SettingsPage"]
