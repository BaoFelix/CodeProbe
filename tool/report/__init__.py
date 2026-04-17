"""report — HTML report generation package (three-layer diagnostic architecture)."""
from .generator import generate_html_report
from .focus_report import generate_focus_report

__all__ = ['generate_html_report', 'generate_focus_report']
