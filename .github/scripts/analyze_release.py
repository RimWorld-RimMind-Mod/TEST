#!/usr/bin/env python3
"""
RimWorld Mod Release Analyzer
分析两个 tag 之间的所有 commits 和 PRs，调用 AI 生成 Release Notes
"""

import argparse
import json
import os
import sys
import time
from openai import OpenAI, APIError, AuthenticationError

def build_prompt(tag_name: str, prs: list, commits: str) -> str:
    """构建发送给 AI 的 prompt"""
    prs_text = []
    for pr in prs:
        prs_text.append(f"""
PR #{pr.get('number', 'N/A')}:
- 标题: {pr.get('title', 'N/A')}
- 作者: @{pr.get('author', 'N/A')}
- 标签: {', '.join(pr.get('labels', [])) or '无'}
- 描述: {(pr.get('body') or '无描述')[:500]}
""")
    prs_section = "\n".join(prs_text) if prs_text else "无 PR 数据"
    commits_section = commits if commits else "无 Commit 数据"

    prompt = f"""你是代码发布管理员。请分析以下自上个版本以来的 PR 和 Commit 数据，为版本 {tag_name} 生成专业的发布说明（Release Notes）。

## 本次发布的版本号
{tag_name}

## 原始 Commits
```text
{commits_section}
```

## 合并的 Pull Requests
{prs_section}

## 输出要求
1. 请分别生成中文和英文的发布说明。
2. 按照常见分类（如：🌟 新特性 / 🐛 Bug 修复 / 🛠️ 优化 / 📝 文档）进行组织。
3. 语气专业、简明扼要。如果 PR 包含标签，请结合标签推断改动性质。
4. 你必须输出一个严格的 JSON 对象，**不要包含任何 Markdown 代码块标记（如 ```json），不要包含任何解释性文字**。

格式如下：
{{
  "release_notes_zh": "### 🌟 新特性\\n- 实现了情绪记忆系统...\\n### 🐛 修复\\n- 修复了导致崩溃的寻路问题...",
  "release_notes_en": "### 🌟 Features\\n- Implemented emotion memory system...\\n### 🐛 Fixes\\n- Fixed a crash related to pathfinding..."
}}
"""
    return prompt.strip()

def call_deepseek(prompt: str) -> dict:
    """调用 DeepSeek API，带重试机制"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise AuthenticationError("DEEPSEEK_API_KEY 环境变量未设置")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "[https://api.deepseek.com](https://api.deepseek.com)")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"::debug::正在调用 AI 生成说明 (尝试 {attempt + 1}/{max_retries})...")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是资深的开源项目维护者，负责编写清晰易读的 Release Notes。请严格遵守 JSON 返回格式。"},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=4096,
            )
            return json.loads(response.choices[0].message.content)
        except AuthenticationError as e:
            print(f"::error::API 认证失败: {e}")
            raise
        except APIError as e:
            print(f"::warning::API 错误: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
        except Exception as e:
            print(f"::warning::未知错误: {e}")
            raise

def generate_fallback_notes(prs: list, commits: str) -> tuple:
    """API 失败时的备用生成逻辑"""
    lines = [f"- {pr.get('title', '无标题')}" for pr in prs]
    if not lines:
        lines = ["- 常规代码更新与维护"]
    
    notes_zh = "### 变更内容\n" + "\n".join(lines)
    notes_en = "### Changes\n" + "\n".join(lines)
    return notes_zh, notes_en

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="当前版本 Tag")
    parser.add_argument("--prs-file", required=True, help="PR JSON 数据路径")
    parser.add_argument("--commits-txt", required=True, help="Commit TXT 数据路径")
    args = parser.parse_args()

    # 1. 加载数据
    with open(args.prs_file, "r", encoding="utf-8") as f:
        prs = json.load(f)
    with open(args.commits_txt, "r", encoding="utf-8") as f:
        commits_content = f.read()

    print("::group::[Debug] AI 输入数据核查")
    print(f"目标 Tag: {args.tag}")
    print(f"提取到 PR 数量: {len(prs)}")
    print(f"Commit 字符总数: {len(commits_content)}")
    print("::endgroup::")

    if not prs and not commits_content.strip():
        print("::warning::没有发现任何 PR 或 Commit，生成默认说明。")
        notes_zh, notes_en = generate_fallback_notes(prs, commits_content)
    else:
        # 2. 构建 Prompt 并调用 AI
        prompt = build_prompt(args.tag, prs, commits_content)
        try:
            result = call_deepseek(prompt)
            notes_zh = result.get("release_notes_zh", "AI 未返回中文说明")
            notes_en = result.get("release_notes_en", "AI 未返回英文说明")
        except Exception as e:
            print(f"::error::AI 调用最终失败，使用备用方案生成。错误: {e}")
            notes_zh, notes_en = generate_fallback_notes(prs, commits_content)

    # 3. 组装最终 Markdown
    combined_notes = f"""## Release Notes

{notes_en}

---

## 发布说明

{notes_zh}
"""

    print("::group::[Debug] 生成的最终 Release Note 预览")
    print(combined_notes)
    print("::endgroup::")

    # 4. 写入文件供 Actions 截取
    with open("/tmp/release_notes.md", "w", encoding="utf-8") as f:
        f.write(combined_notes)

if __name__ == "__main__":
    main()