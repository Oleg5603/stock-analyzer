"""Local, read-only stock analysis backend."""

from .models import AnalysisResult, Company, PortfolioPosition
from .rules import analyze_company

__all__ = ["AnalysisResult", "Company", "PortfolioPosition", "analyze_company"]
__version__ = "0.1.0"
