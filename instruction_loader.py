#!/usr/bin/env python3
"""
指令库加载器
- 读取开发指令库文本文件，解析为字典 {编号: 完整指令文本}
- 生成指令摘要供 AI 选择
- 根据编号列表拼接规范文本
"""

def load_instructions(filepath: str) -> dict:
    """
    从文本文件加载指令库。
    每行格式: #编号|分类|关键词|指令内容
    返回: { '#1': '指令内容', ... }
    """
    instructions = {}
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith('#'):
                continue
            # 按竖线分割，最多分4段
            parts = line.split('|', 3)
            if len(parts) >= 4:
                num = parts[0].strip()
                instruction_text = parts[3].strip()
                instructions[num] = instruction_text
    return instructions

def generate_summary(instructions: dict, max_len: int = 60) -> str:
    """
    生成指令库的简短摘要，供 AI 选择用。
    格式: #编号: 简要说明
    """
    lines = []
    for num, text in instructions.items():
        summary = text[:max_len] + ('...' if len(text) > max_len else '')
        lines.append(f"{num}: {summary}")
    return '\n'.join(lines)

def build_norm_text(rule_numbers: list, instructions: dict) -> str:
    """
    根据编号列表，拼接出完整的规范文本块。
    返回的文本可以直接附加到提示词末尾。
    """
    texts = []
    for num in rule_numbers:
        if num in instructions:
            texts.append(f"{num} {instructions[num]}")
    return '\n'.join(texts)