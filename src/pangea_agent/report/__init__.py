"""V1 Markdown and offline HTML report generation."""

from .html import render_html_report, reports_are_complete, write_reports
from .markdown import render_report

__all__ = ["render_html_report", "render_report", "reports_are_complete", "write_reports"]
