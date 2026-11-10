"""Configuración pública y no sensible del extractor de incurridos."""

from __future__ import annotations


JIRA_DASHBOARD_URL = "https://umane.emeal.nttdata.com/jiraito/secure/Dashboard.jspa"
JIRA_ALLOWED_ORIGIN = "https://umane.emeal.nttdata.com"
TIMESHEET_ENDPOINT = "/jiraito/rest/scriptrunner/latest/custom/getReportTimesheet"
EXTRACTOR_VERSION = "2.0.0"
MODAL_ID = "incBBVA"
REQUEST_DELAY_MS = 150
DATE_COLUMN_INDEX = 15

DEFAULT_PROJECTS = (
    "BBVACS",
    "BBVAALPHA",
    "BBVAVALORE",
    "BBVAGDV",
    "BBVACIB",
    "BBVACIBGM",
)

REQUEST_HEADERS = {
    "Content-Type": "application/json",
    "X-Atlassian-Token": "no-check",
    "X-Requested-With": "XMLHttpRequest",
}
REQUEST_BODY = {"users": []}

