#!/usr/bin/env python3
"""
精确替换 Python 源文件中指定函数的实现，保持原有缩进。
用法: python fix_function.py <源文件> <函数名> <新代码文件>
"""
import sys

def find_function_end(lines, start_idx):
    """从函数定义行开始，通过缩进级别找到函数体的结束行（返回0‑indexed结束行号）"""
    # 获取起始行的缩进（def 前的空格数）
    def_line = lines[start_idx]
    base_indent = len(def_line) - len(def_line.lstrip())
    # 函数体第一行应该在 start_idx+1，缩进大于 base_indent
    idx = start_idx + 1
    while idx < len(lines):
        line = lines[idx]
        if line.strip() == '':        # 空行跳过
            idx += 1
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= base_indent:
            # 遇到缩进小于等于 base_indent 的行，说明函数体结束
            break
        idx += 1
    return idx - 1  # 结束行是最后一个缩进大于 base_indent 的行

def fix_function(source_path: str, func_name: str, new_code_path: str):
    # 读取源文件所有行（保留换行符）
    with open(source_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 简单遍历，找到目标函数的定义行
    func_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        # 匹配 "def funcname(" 开头
        if stripped.startswith('def ') and stripped.split('(')[0][4:].strip() == func_name:
            func_start = i
            break

    if func_start is None:
        print(f"❌ 在源文件中未找到函数: {func_name}")
        sys.exit(1)

    # 计算原函数缩进空格数
    def_line = lines[func_start]
    indent_spaces = len(def_line) - len(def_line.lstrip())

    # 找到函数体结束行
    func_end = find_function_end(lines, func_start)

    # 读取新代码文件
    with open(new_code_path, 'r', encoding='utf-8') as f:
        new_code_raw = f.read()

    # 保证新代码末尾有换行
    if not new_code_raw.endswith('\n'):
        new_code_raw += '\n'

    new_lines = new_code_raw.splitlines(keepends=True)

    # 给新代码每一行添加原缩进
    indent_prefix = ' ' * indent_spaces
    indented_new_lines = []
    for line in new_lines:
        if line.strip():          # 非空行加缩进
            indented_new_lines.append(indent_prefix + line)
        else:                     # 空行保留原样（即空行）
            indented_new_lines.append(line)

    # 组装新内容
    new_contents = (
        lines[:func_start] +
        indented_new_lines +
        lines[func_end + 1:]     # 注意：func_end+1 是函数结束后的下一行
    )

    # 写回源文件
    with open(source_path, 'w', encoding='utf-8') as f:
        f.writelines(new_contents)

    print(f"✅ 已成功将函数 '{func_name}' 替换为 {new_code_path} 中的新实现")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("用法: python fix_function.py <源文件> <函数名> <新代码文件>")
        sys.exit(1)

    source = sys.argv[1]
    func = sys.argv[2]
    new_code = sys.argv[3]
    fix_function(source, func, new_code)