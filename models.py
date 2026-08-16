from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class TestResult:
    function_name: str
    passed: bool
    message: str
    details: Optional[str] = None

@dataclass
class AnalysisContext:
    code: str
    filename: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    results: List[TestResult] = field(default_factory=list)
    missing_modules: List[str] = field(default_factory=list)   # 新增：缺失且无法自动安装的模块