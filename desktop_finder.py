#!/usr/bin/env python3
"""
智能桌面文件查找工具（自展示增强版，含 Markdown / JSON 导出）
- 默认列出桌面顶层文件/文件夹
- --tree 树形展示完整结构
- --search 关键词搜索匹配
- --interactive 交互式搜索
- --info 终端展示脚本自身结构、功能与健康检查
- --export-md 将自检报告保存为 Markdown 文件
- --json     输出 JSON 格式健康报告（用于其他工具调用）
"""

import os
import sys
import argparse
import ast
import json
from typing import List, Dict, Any, Optional

# 尝试导入 RunnableChecker（若项目中有）
try:
    from checker import RunnableChecker
    _runnable_available = True
except ImportError:
    _runnable_available = False


# ------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------
def get_desktop_path() -> str:
    """跨平台获取桌面目录路径"""
    for var in ['XDG_DESKTOP_DIR', 'USERPROFILE', 'HOME']:
        val = os.environ.get(var)
        if val:
            desktop = os.path.join(val, 'Desktop')
            if os.path.isdir(desktop):
                return desktop
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        return desktop
    desktop = os.path.join(os.path.expanduser("~"), "桌面")
    if os.path.isdir(desktop):
        return desktop
    return os.path.expanduser("~")


def print_tree(directory: str, prefix: str = "", search: str = None):
    """递归打印目录树，支持搜索过滤"""
    try:
        entries = sorted(os.listdir(directory))
    except PermissionError:
        return

    filtered = []
    for e in entries:
        full = os.path.join(directory, e)
        is_dir = os.path.isdir(full)
        if search is None or search.lower() in e.lower():
            filtered.append((e, is_dir, True))
        elif is_dir and _contains_match(full, search):
            filtered.append((e, is_dir, False))

    for i, (name, is_dir, _) in enumerate(filtered):
        is_last = (i == len(filtered) - 1)
        connector = "└── " if is_last else "├── "
        next_prefix = prefix + ("    " if is_last else "│   ")
        print(prefix + connector + name + ("/" if is_dir else ""))
        if is_dir:
            print_tree(os.path.join(directory, name), next_prefix, search)


def _contains_match(directory: str, search: str) -> bool:
    """检查目录下是否存在匹配文件/文件夹"""
    try:
        for entry in os.listdir(directory):
            if search.lower() in entry.lower():
                return True
            full = os.path.join(directory, entry)
            if os.path.isdir(full) and _contains_match(full, search):
                return True
    except PermissionError:
        pass
    return False


# ------------------------------------------------------------
# 静态缺陷检测
# ------------------------------------------------------------
def _check_naked_except(func_node: ast.FunctionDef) -> List[str]:
    issues = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(f"函数 {func_node.name} 使用了裸 except: ，可能隐藏严重错误")
            elif isinstance(node.type, ast.Name) and node.type.id == 'Exception':
                issues.append(f"函数 {func_node.name} 捕获了通用 Exception ，建议指定具体异常类型")
    return issues


def _check_file_resource_management(func_node: ast.FunctionDef) -> List[str]:
    issues = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'open':
            parent = node
            in_with = False
            while hasattr(parent, 'parent'):
                parent = parent.parent
                if isinstance(parent, ast.With):
                    in_with = True
                    break
            if not in_with:
                has_close = False
                for n in ast.walk(func_node):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                        if n.func.attr == 'close':
                            has_close = True
                            break
                if not has_close:
                    issues.append(f"函数 {func_node.name} 打开文件但未使用 'with' 语句，且未找到显式 close() 调用，可能导致资源泄漏")
    return issues


def _check_function_length(func_node: ast.FunctionDef, max_lines: int = 50) -> List[str]:
    if func_node.end_lineno and func_node.lineno:
        length = func_node.end_lineno - func_node.lineno + 1
        if length > max_lines:
            return [f"函数 {func_node.name} 过长 ({length} 行)，建议拆分以提高可维护性"]
    return []


def _check_complexity(func_node: ast.FunctionDef, max_complexity: int = 10) -> List[str]:
    complexity = 1
    for node in ast.walk(func_node):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
    if complexity > max_complexity:
        return [f"函数 {func_node.name} 圈复杂度为 {complexity}，超过阈值 {max_complexity}，建议重构"]
    return []


def static_analysis(source: str) -> List[str]:
    issues = []
    tree = ast.parse(source)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            issues.extend(_check_naked_except(node))
            issues.extend(_check_file_resource_management(node))
            issues.extend(_check_function_length(node))
            issues.extend(_check_complexity(node))
    return issues


def _calculate_avg_complexity(source: str) -> float:
    tree = ast.parse(source)
    complexities = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            complexities.append(_get_complexity(node))
    return sum(complexities) / len(complexities) if complexities else 0.0


def _get_complexity(func_node: ast.FunctionDef) -> int:
    complexity = 1
    for node in ast.walk(func_node):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
    return complexity


# ------------------------------------------------------------
# 运行时错误检测
# ------------------------------------------------------------
def runtime_test(source: str) -> List[str]:
    if not _runnable_available:
        return ["RunnableChecker 不可用（未找到 checker 模块），跳过运行时检测"]

    try:
        checker = RunnableChecker(checks_dir='checks')
        context = checker.check(source, filename="<self>", mock_missing=True)
        failures = [r for r in context.results if not r.passed]
        if not failures:
            return []
        return [f"函数 {r.function_name}: {r.message}" for r in failures]
    except Exception as e:
        return [f"运行时检测异常: {e}"]


# ------------------------------------------------------------
# 完全重复代码检测
# ------------------------------------------------------------
def _normalize_source(source: str) -> str:
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            lines.append(stripped)
    return '\n'.join(lines)


def duplicate_detection(source: str) -> List[str]:
    tree = ast.parse(source)
    func_bodies = {}
    duplicates = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            func_source = ast.get_source_segment(source, node)
            if func_source is None:
                continue
            normalized = _normalize_source(func_source)
            if normalized in func_bodies:
                first_func = func_bodies[normalized]
                duplicates.append(f"函数 '{node.name}' 与 '{first_func}' 实现完全相同")
            else:
                func_bodies[normalized] = node.name

    return duplicates


# ------------------------------------------------------------
# JSON 报告导出（供 plan_executor 等工具调用）
# ------------------------------------------------------------
def export_json_report(target_path: Optional[str] = None, output_path: Optional[str] = None) -> None:
    """将健康检查结果输出为 JSON（到文件或 stdout）"""
    if target_path:
        with open(target_path, 'r', encoding='utf-8') as f:
            source = f.read()
        filename = target_path
    else:
        with open(__file__, 'r', encoding='utf-8') as f:
            source = f.read()
        filename = os.path.abspath(__file__)

    report = {
        "file": filename,
        "static_issues": static_analysis(source),
        "runtime_errors": runtime_test(source),
        "duplicate_functions": duplicate_detection(source),
        "avg_complexity": round(_calculate_avg_complexity(source), 2),
        "function_count": len([node for node in ast.iter_child_nodes(ast.parse(source)) if isinstance(node, ast.FunctionDef)]),
        "overall_healthy": not any([static_analysis(source), runtime_test(source), duplicate_detection(source)])
    }

    json_output = json.dumps(report, indent=2, ensure_ascii=False)

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(json_output)
        print(f"✅ JSON 报告已保存至: {output_path}")
    else:
        print(json_output)


# ------------------------------------------------------------
# Markdown 导出功能（保留）
# ------------------------------------------------------------
def export_md_info(output_path: str):
    """将自检报告保存为 Markdown 文件"""
    with open(__file__, 'r', encoding='utf-8') as f:
        source = f.read()

    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or "(无模块文档)"

    lines = []
    lines.append("# 脚本自身信息报告\n")
    lines.append(f"**文件路径**: `{os.path.abspath(__file__)}`  \n")
    lines.append(f"**Python 版本**: {sys.version}  \n\n")

    lines.append("## 模块功能说明\n")
    lines.append(module_doc + "\n\n")

    lines.append("## 函数列表\n")
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            args_info = []
            for arg in node.args.args:
                ann = ""
                if arg.annotation:
                    ann = ": " + ast.get_source_segment(source, arg.annotation)
                args_info.append(arg.arg + ann)
            sig = f"{name}({', '.join(args_info)})"
            if node.returns:
                ret_ann = ast.get_source_segment(source, node.returns)
                sig += f" -> {ret_ann}"
            doc = ast.get_docstring(node) or "(无文档)"
            lines.append(f"### 🔹 `{sig}`\n")
            lines.append(f"```text\n{doc}\n```\n\n")

        elif isinstance(node, ast.ClassDef):
            lines.append(f"### 📦 类: {node.name}\n")
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    method_name = item.name
                    doc = ast.get_docstring(item) or "(无文档)"
                    lines.append(f"- **{method_name}()**: {doc}\n")
            lines.append("\n")

    lines.append("## 静态缺陷检测\n")
    issues = static_analysis(source)
    if issues:
        lines.append(f"发现 **{len(issues)}** 个潜在问题：\n\n")
        for i, issue in enumerate(issues, 1):
            lines.append(f"{i}. {issue}\n")
    else:
        lines.append("✅ 未发现明显结构性缺陷\n")
    lines.append("\n")

    lines.append("## 运行时错误检测\n")
    rt_msgs = runtime_test(source)
    for msg in rt_msgs:
        lines.append(f"- {msg}\n")
    lines.append("\n")

    lines.append("## 完全重复代码检测\n")
    dup_msgs = duplicate_detection(source)
    for msg in dup_msgs:
        lines.append(f"- {msg}\n")
    lines.append("\n")

    lines.append("## 命令行参数\n")
    parser = _build_argparser()
    help_text = parser.format_help()
    lines.append("```text\n" + help_text + "```\n")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print(f"✅ Markdown 报告已保存至: {output_path}")


# ------------------------------------------------------------
# 终端自展示功能
# ------------------------------------------------------------
def show_self_info():
    with open(__file__, 'r', encoding='utf-8') as f:
        source = f.read()

    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or "(无模块文档)"

    print("=" * 60)
    print("📄 脚本自身信息")
    print("=" * 60)
    print(f"文件路径: {os.path.abspath(__file__)}")
    print(f"Python 版本: {sys.version}")

    print("\n【模块功能说明】")
    print(module_doc)

    print("\n【函数列表】")
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            args_info = []
            for arg in node.args.args:
                ann = ""
                if arg.annotation:
                    ann = ": " + ast.get_source_segment(source, arg.annotation)
                args_info.append(arg.arg + ann)
            sig = f"{name}({', '.join(args_info)})"
            if node.returns:
                ret_ann = ast.get_source_segment(source, node.returns)
                sig += f" -> {ret_ann}"
            doc = ast.get_docstring(node) or "(无文档)"
            print(f"\n🔹 {sig}")
            print(f"   {doc}")

        elif isinstance(node, ast.ClassDef):
            print(f"\n📦 类: {node.name}")
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    method_name = item.name
                    doc = ast.get_docstring(item) or "(无文档)"
                    print(f"   - {method_name}(): {doc}")

    print("\n【静态缺陷检测】")
    issues = static_analysis(source)
    if issues:
        print(f"发现 {len(issues)} 个潜在问题：")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    else:
        print("✅ 未发现明显结构性缺陷")

    print("\n【运行时错误检测】")
    rt_msgs = runtime_test(source)
    for msg in rt_msgs:
        print(f"  {msg}")

    print("\n【完全重复代码检测】")
    dup_msgs = duplicate_detection(source)
    for msg in dup_msgs:
        print(f"  {msg}")

    print("\n【命令行参数】")
    parser = _build_argparser()
    parser.print_help()

    print("\n✅ 自检完成")


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="智能桌面文件查找工具 - 树形浏览、关键词搜索、交互模式、自检与报告导出",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python %(prog)s                    列出桌面顶层文件\n"
               "  python %(prog)s -t                 树形展示\n"
               "  python %(prog)s -s '报告'          搜索含'报告'的文件\n"
               "  python %(prog)s -t -s '.py'        树形搜索 .py 文件\n"
               "  python %(prog)s -i                 交互式搜索\n"
               "  python %(prog)s --info              终端显示自检报告\n"
               "  python %(prog)s --export-md report.md  将自检报告保存为 Markdown\n"
               "  python %(prog)s --json              输出 JSON 格式健康报告（默认检查自身）\n"
               "  python %(prog)s --json --target myfile.py 检查指定文件并输出 JSON"
    )
    parser.add_argument("-t", "--tree", action="store_true", help="树形展示目录结构")
    parser.add_argument("-s", "--search", type=str, default=None, help="按文件名关键词过滤")
    parser.add_argument("-i", "--interactive", action="store_true", help="进入交互式搜索模式")
    parser.add_argument("-d", "--desktop", type=str, default=None, help="自定义扫描目录")
    parser.add_argument("--info", action="store_true", help="终端显示脚本自身的结构、功能与全面健康检查")
    parser.add_argument("--export-md", type=str, metavar="FILE", help="将自检报告导出为 Markdown 文件")
    parser.add_argument("--json", nargs="?", const=None, metavar="OUTPUT_FILE",
                        help="输出 JSON 格式健康报告（可选指定输出文件，默认输出到 stdout）")
    parser.add_argument("--target", type=str, metavar="TARGET_FILE",
                        help="指定要检查的 Python 脚本文件（用于 --json 模式）")
    return parser


# ------------------------------------------------------------
# 主逻辑
# ------------------------------------------------------------
def main():
    parser = _build_argparser()
    args = parser.parse_args()

    # 处理 JSON 导出（互斥于文件浏览等操作）
    if args.json is not None:
        export_json_report(target_path=args.target, output_path=args.json)
        return

    # 处理 Markdown 导出
    if args.export_md:
        export_md_info(args.export_md)
        return

    # 处理终端自检
    if args.info:
        show_self_info()
        return

    # 文件浏览模式
    target = args.desktop or get_desktop_path()
    if not os.path.isdir(target):
        print(f"❌ 目录不存在: {target}")
        sys.exit(1)

    if args.interactive:
        print(f"📂 当前目录: {target}")
        print("输入关键词实时搜索（输入 'exit' 退出）\n")
        while True:
            try:
                kw = input("🔍 搜索关键词: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n👋 退出")
                break
            if kw.lower() == "exit":
                break
            if not kw:
                continue
            print()
            if args.tree:
                print(target)
                print_tree(target, search=kw)
            else:
                try:
                    items = sorted(os.listdir(target))
                except PermissionError:
                    print("⛔ 无权限")
                    continue
                matches = [i for i in items if kw.lower() in i.lower()]
                for i in matches:
                    full = os.path.join(target, i)
                    icon = "📁 " if os.path.isdir(full) else "📄 "
                    print(f"{icon}{i}")
                if not matches:
                    print("(无匹配)")
            print()
    else:
        print(f"📂 扫描目录: {target}\n")
        if args.tree:
            print(target)
            print_tree(target, search=args.search)
        else:
            try:
                items = sorted(os.listdir(target))
            except PermissionError:
                print("⛔ 无权限")
                return
            if args.search:
                items = [i for i in items if args.search.lower() in i.lower()]
            for i in items:
                full = os.path.join(target, i)
                icon = "📁 " if os.path.isdir(full) else "📄 "
                print(f"{icon}{i}")
        print("\n✅ 完成")


if __name__ == "__main__":
    main()