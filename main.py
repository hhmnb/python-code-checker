
"""
Python 脚本可运行性检测 + AI 自动修复工具（支持循环修复）
用法:
  python main.py <脚本路径> [选项]
  python main.py --function 'def 函数名(...): ...' [选项]

选项:
  -v, --verbose          详细输出
  -o, --output FILE      保存 JSON 报告
  --locate FILE          导出问题函数定位信息（含行号、缩进空格数、源码）
  --output-dir DIR       将所有输出文件保存到该目录下的时间戳子文件夹
  --no-mock-missing       禁止 Mock 缺失模块（默认会 Mock）
  --ai-fix               检测后自动通过 DeepSeek 网页修复失败函数（需 config.json）
  --loop                 循环修复直到所有函数通过（与 --ai-fix 配合使用）
  --max-loops N          最大循环修复次数（默认 5 次）
"""

import sys
import argparse
import json
import ast
import os
import re
import shutil
from datetime import datetime
from typing import List, Dict, Optional

from checker import RunnableChecker
from models import AnalysisContext
from builtin.quick_test import quick_test

# ------------------------------------------------------------
# 尝试导入 DeepSeek 网页操控模块（可选）
# ------------------------------------------------------------
try:
    from deepseek_sender import DeepSeekWebSender
    HAS_WEB_SENDER = True
except ImportError:
    HAS_WEB_SENDER = False


# ------------------------------------------------------------
# 输出目录管理（全局变量，简化函数签名）
# ------------------------------------------------------------
OUTPUT_DIR: Optional[str] = None  # 创建的时间戳目录路径
LOG_FILE_PATH: Optional[str] = None


def _ensure_output_dir(base_dir: str) -> str:
    """创建带时间戳的输出子文件夹并返回其路径"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(base_dir, f"output_{timestamp}")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _log(msg: str):
    """向日志文件和控制台同时输出"""
    print(msg)
    if LOG_FILE_PATH:
        try:
            with open(LOG_FILE_PATH, 'a', encoding='utf-8') as f:
                f.write(msg + '\n')
        except Exception:
            pass


# ------------------------------------------------------------
# 函数定位工具（内置）
# ------------------------------------------------------------
class FunctionLocation:
    def __init__(self, name, start_line, end_line, nest_level, indent_spaces, source):
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.nest_level = nest_level
        self.indent_spaces = indent_spaces  # def 前的空格数
        self.source = source

    def to_dict(self):
        return {
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "nest_level": self.nest_level,
            "indent_spaces": self.indent_spaces,
            "source": self.source
        }


def _walk_functions(node, depth=0):
    funcs = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef):
            start = child.lineno
            end = child.end_lineno
            indent = child.col_offset          # 缩进空格数
            funcs.append(FunctionLocation(child.name, start, end, depth, indent, ""))
            funcs.extend(_walk_functions(child, depth + 1))
        else:
            funcs.extend(_walk_functions(child, depth))
    return funcs


def analyze_functions(code: str) -> List[FunctionLocation]:
    tree = ast.parse(code)
    funcs = _walk_functions(tree, 0)
    lines = code.splitlines()
    for f in funcs:
        f.source = "\n".join(lines[f.start_line - 1 : f.end_line])
    return funcs


def locate_problem_functions(code: str, error_names: List[str]) -> List[Dict]:
    all_funcs = analyze_functions(code)
    name_map = {f.name: f for f in all_funcs}
    result = []
    for name in error_names:
        if name in name_map:
            result.append(name_map[name].to_dict())
        else:
            result.append({
                "name": name,
                "start_line": None,
                "end_line": None,
                "nest_level": None,
                "indent_spaces": None,
                "source": None,
                "note": "无法自动定位（可能为类方法或内建函数）"
            })
    return result


def export_locate_report(context: AnalysisContext, code: str, output_path: str):
    failed_names = [r.function_name for r in context.results if not r.passed]
    locations = locate_problem_functions(code, failed_names)
    report = {
        "file": context.filename,
        "failed_functions": locations
    }
    # 如果设置了全局输出目录，则将文件保存到输出目录下
    actual_path = output_path
    if OUTPUT_DIR:
        # 确保文件名不包含路径
        basename = os.path.basename(output_path) if output_path else "locate_report.json"
        actual_path = os.path.join(OUTPUT_DIR, basename)
    with open(actual_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    _log(f"📍 问题函数定位报告已保存至: {actual_path}")


# ------------------------------------------------------------
# 报告输出函数
# ------------------------------------------------------------
def print_simple_report(context: AnalysisContext):
    failed = [r for r in context.results if not r.passed]
    if not failed and not context.errors:
        _log("✅ 脚本可正常运行")
        return
    if context.errors:
        for e in context.errors:
            _log(f"⚠️  {e}")
    if failed:
        _log("❌ 脚本无法正常运行，失败函数:")
        for res in failed:
            _log(f"  - {res.function_name}: {res.message}")


def print_verbose_report(context: AnalysisContext):
    _log("\n======== 检测结果 ========")
    if context.errors:
        _log("⚠️  错误/警告:")
        for e in context.errors:
            _log(f"  - {e}")
    _log("\n📋 函数运行测试:")
    for res in context.results:
        status = "✅" if res.passed else "❌"
        _log(f"  {status} {res.function_name}: {res.message}")
    if not context.errors and all(r.passed for r in context.results):
        _log("\n🎉 脚本完全正常！")
    else:
        _log("\n💡 发现以上问题，请修复。")


def save_json_report(context: AnalysisContext, filepath: str, output_path: str):
    report = {
        "file": filepath,
        "errors": context.errors,
        "results": [
            {"function": r.function_name, "passed": r.passed, "message": r.message}
            for r in context.results
        ],
        "overall_success": not context.errors and all(r.passed for r in context.results)
    }
    actual_path = output_path
    if OUTPUT_DIR:
        basename = os.path.basename(output_path) if output_path else "report.json"
        actual_path = os.path.join(OUTPUT_DIR, basename)
    try:
        with open(actual_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        _log(f"📦 检测数据包已保存至: {actual_path}")
    except Exception as e:
        _log(f"❌ 无法保存 JSON 报告: {e}")


# ------------------------------------------------------------
# AI 修复功能（通过 DeepSeek 网页）
# ------------------------------------------------------------
def load_web_config() -> dict:
    """加载 config.json（包含网页坐标和运行参数）"""
    config_path = "config.json"
    if not os.path.exists(config_path):
        _log("❌ 缺少 config.json，请先运行 setup_wizard.py 生成配置。")
        return None
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_ai_fixes(reply: str) -> dict:
    """解析 AI 返回的修复代码，返回 {函数名: 修复代码 或 '需要重构'}"""
    fixes = {}
    pattern = r'## 函数名: (\w+)\s*\n```python(.*?)```|## 函数名: (\w+)\s*\n需要重构'
    for match in re.finditer(pattern, reply, re.DOTALL):
        if match.group(1):          # 有代码块
            func_name = match.group(1)
            code_block = match.group(2).strip()
            fixes[func_name] = code_block
        elif match.group(3):        # 需要重构
            func_name = match.group(3)
            fixes[func_name] = "需要重构"
    return fixes


def ai_fix_workflow(filepath: str, context: AnalysisContext):
    """
    对检测出的失败函数，通过 DeepSeek 网页请求修复，并写回源文件。
    如果设置了 OUTPUT_DIR，会在修复前备份原始文件到该目录。
    """
    if not HAS_WEB_SENDER:
        _log("❌ 缺少 deepseek_sender 模块，无法使用 AI 修复。请安装 pyautogui, pyperclip。")
        return

    failed_results = [r for r in context.results if not r.passed]
    if not failed_results:
        _log("✅ 所有函数已通过，无需 AI 修复。")
        return

    # 加载网页配置
    web_config = load_web_config()
    if web_config is None:
        return

    # 备份原始脚本（如果指定了输出目录）
    if OUTPUT_DIR and os.path.exists(filepath):
        backup_name = os.path.basename(filepath) + ".backup"
        backup_path = os.path.join(OUTPUT_DIR, backup_name)
        try:
            shutil.copy2(filepath, backup_path)
            _log(f"📋 已备份原始文件至: {backup_path}")
        except Exception as e:
            _log(f"⚠️ 备份文件失败: {e}")

    _log("🌐 正在连接 DeepSeek 网页...")
    try:
        sender = DeepSeekWebSender(web_config)
        sender.new_chat_with_expert()
    except Exception as e:
        _log(f"❌ 无法初始化 DeepSeek 网页会话: {e}")
        return

    # 读取目标脚本源码
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()

    # 构建错误列表
    error_info = "\n".join([f"- {r.function_name}: {r.message}" for r in failed_results])

    # 发送修复请求
    prompt = f"""你是一个 Python 代码修复专家。请修复以下脚本中失败的函数，输出每个修复后的完整函数代码（保持原有缩进）。

=== 脚本源码 ===
{code}

=== 错误信息 ===
{error_info}

要求：
1. 输出格式必须为：
## 函数名: xxx
```python
def xxx(...):
    ...
```
2. 每个函数独立输出，函数定义必须与原始缩进完全一致。
3. 如果函数无法修复（如缺少外部依赖），则输出：
## 函数名: xxx
需要重构
"""
    reply = sender.send(prompt)
    if not reply:
        _log("❌ 未获取到 AI 回复")
        return

    # 解析修复代码
    fixes = parse_ai_fixes(reply)
    if not fixes:
        _log("❌ AI 回复中未找到有效修复代码")
        return

    # 应用修复
    from fix_function import fix_function
    for func_name, new_code in fixes.items():
        if new_code == "需要重构":
            _log(f"⏭️ 跳过 {func_name}（标记为需要重构）")
            continue
        tmp_file = f"_fix_{func_name}.py"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            f.write(new_code)
        try:
            fix_function(filepath, func_name, tmp_file)
            _log(f"✅ 已修复: {func_name}")
        except Exception as e:
            _log(f"❌ 修复 {func_name} 失败: {e}")
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    # 再次检测验证
    _log("\n🔁 再次检测验证修复效果...")
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()
    checker2 = RunnableChecker(checks_dir='checks')
    context2 = checker2.check(code, filename=filepath, mock_missing=True)
    still_failed = [r for r in context2.results if not r.passed]
    if still_failed:
        _log(f"⚠️ 仍有 {len(still_failed)} 个函数未通过:")
        for r in still_failed:
            _log(f"   - {r.function_name}: {r.message}")
    else:
        _log("🎉 所有问题已修复！")


# ------------------------------------------------------------
# 主程序
# ------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Python 脚本可运行性检测 + AI 自动修复工具",
        usage="python main.py <脚本路径> [选项]  或  python main.py --function 'def 函数名(...): ...' [选项]"
    )
    parser.add_argument("target", nargs="?", help="要检测的 Python 脚本路径")
    parser.add_argument("--function", dest="func_code", help="直接测试一个函数代码字符串")
    parser.add_argument("--no-mock-missing", action="store_true",
                        help="关闭自动 Mock（默认会 Mock 缺失模块）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="显示详细检测报告")
    parser.add_argument("-o", "--output", dest="output", metavar="FILE",
                        help="将检测结果保存为 JSON 文件")
    parser.add_argument("--locate", dest="locate", metavar="FILE",
                        help="导出问题函数的定位信息（含行号、缩进空格数、源码）")
    parser.add_argument("--output-dir", dest="output_dir", metavar="DIR",
                        help="将所有输出文件整合到该目录下的时间戳子文件夹中")
    parser.add_argument("--ai-fix", action="store_true",
                        help="检测后自动通过 DeepSeek 网页修复失败函数（需 config.json）")
    parser.add_argument("--loop", action="store_true",
                        help="循环修复直到所有函数通过（与 --ai-fix 配合使用）")
    parser.add_argument("--max-loops", dest="max_loops", type=int, default=5, metavar="N",
                        help="最大循环修复次数（默认 5 次）")
    args = parser.parse_args()

    mock_missing = not args.no_mock_missing

    # 如果指定了输出目录，创建带时间戳的子文件夹并初始化全局变量
    if args.output_dir:
        OUTPUT_DIR = _ensure_output_dir(args.output_dir)
        LOG_FILE_PATH = os.path.join(OUTPUT_DIR, "run.log")
        _log(f"📁 输出目录已创建: {OUTPUT_DIR}")

    if args.func_code:
        result = quick_test(args.func_code, mock_missing=mock_missing)
        if args.verbose:
            _log(f"函数: {result.function_name}")
        if result.passed:
            _log("✅ 函数可正常运行")
        else:
            _log(f"❌ 函数无法运行: {result.message}")
        if args.output:
            single_report = {
                "function": result.function_name,
                "passed": result.passed,
                "message": result.message
            }
            actual_path = args.output
            if OUTPUT_DIR:
                basename = os.path.basename(args.output) if args.output else "single_report.json"
                actual_path = os.path.join(OUTPUT_DIR, basename)
            try:
                with open(actual_path, 'w', encoding='utf-8') as f:
                    json.dump(single_report, f, indent=2, ensure_ascii=False)
                _log(f"📦 单函数检测结果已保存至: {actual_path}")
            except Exception as e:
                _log(f"❌ 无法保存 JSON 报告: {e}")

    elif args.target:
        filepath = args.target
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                code = f.read()
        except FileNotFoundError:
            _log(f"❌ 文件不存在: {filepath}")
            sys.exit(1)

        # 如果指定了输出目录，备份目标脚本
        if OUTPUT_DIR:
            backup_name = os.path.basename(filepath) + ".original"
            backup_path = os.path.join(OUTPUT_DIR, backup_name)
            try:
                shutil.copy2(filepath, backup_path)
                _log(f"📋 已备份待检测脚本至: {backup_path}")
            except Exception as e:
                _log(f"⚠️ 备份待检测脚本失败: {e}")

        checker = RunnableChecker(checks_dir='checks')
        context = checker.check(code, filename=filepath, mock_missing=mock_missing)

        if args.verbose:
            print_verbose_report(context)
        else:
            print_simple_report(context)

        if args.output:
            save_json_report(context, filepath, args.output)

        if args.locate:
            export_locate_report(context, code, args.locate)

        # AI 修复
        if args.ai_fix:
            if args.loop:
                max_loops = args.max_loops
                for loop_num in range(1, max_loops + 1):
                    _log(f"\n{'='*40}")
                    _log(f"🔄 修复循环 第 {loop_num}/{max_loops} 次")
                    # 重新检测当前文件（可能已被修复）
                    with open(filepath, 'r', encoding='utf-8') as f:
                        current_code = f.read()
                    checker2 = RunnableChecker(checks_dir='checks')
                    current_context = checker2.check(current_code, filename=filepath, mock_missing=mock_missing)
                    failed = [r for r in current_context.results if not r.passed]
                    if not failed:
                        _log("✅ 所有函数通过测试，停止循环修复。")
                        break
                    # 执行一次修复
                    ai_fix_workflow(filepath, current_context)
                else:
                    _log(f"⚠️ 已完成 {max_loops} 轮修复，仍有函数未通过，请人工检查。")
            else:
                ai_fix_workflow(filepath, context)

    else:
        parser.print_help()
