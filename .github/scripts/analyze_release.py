#!/usr/bin/env python3
"""RimWorld Mod - AI Release Notes 生成脚本"""

import argparse
import json
import os
import sys

from openai import OpenAI


def build_prompt(mod_name: str, tag: str, commits: list, prs: list) -> str:
    """构建 AI Prompt"""

    def fmt_pr(pr: dict, idx: int) -> str:
        files = "\n".join(f"    [{f.get('changeType', 'M')}] {f.get('path', '')}" for f in pr.get("files", []))
        body = (pr.get("body", "") or "(无描述)")[:500]
        return f"""PR #{idx}:
  标题: {pr.get('title', '')}
  作者: {pr.get('author', 'unknown')}
  标签: {', '.join(pr.get('labels', [])) or '(无)'}
  描述: {body}{'...' if len(pr.get('body', '')) > 500 else ''}
  变更文件:\n{files or '    (无)'}"""

    prs_str = "\n---\n".join(fmt_pr(pr, i) for i, pr in enumerate(prs, 1))
    commits_str = "\n".join(f"  - {c}" for c in commits[:50])

    return f"""你是 RimWorld Mod 发布管理员，根据变更内容生成 Release Notes。

## 当前信息
- **Mod 名称**: {mod_name}
- **版本标签**: {tag}
- **PR 数量**: {len(prs)}
- **Commit 数量**: {len(commits)}

## Commit 列表
{commits_str}

## PR 列表
{prs_str if prs else '(无 PR)'}

## 分类规则
1. **Added/新增**: 新功能、新事件、新配置、新 AI 行为
2. **Changed/变更**: 重新平衡、修改现有行为
3. **Fixed/修复**: Bug 修复、性能优化
4. **Removed/移除**: 删除功能或内容
5. **Technical/技术**: CI/CD、重构、文档

## 输出格式 (纯 JSON)
{{"release_notes_en": "### Added\\n- ...\\n\\n### Fixed\\n- ...", "release_notes_zh": "### 新增\\n- ...\\n\\n### 修复\\n- ...", "summary": "一句话总结", "confidence": 0.9}}"""


def call_deepseek(prompt: str) -> dict:
    """调用 DeepSeek API"""
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    resp = client.chat.completions.create(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        messages=[
            {"role": "system", "content": "你是 RimWorld Mod 发布管理员，严格返回 JSON，无额外文字。"},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=4096,
    )
    return json.loads(resp.choices[0].message.content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mod-name", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commits-file", required=True)
    parser.add_argument("--prs-file", required=True)
    parser.add_argument("--output-file", required=True)
    args = parser.parse_args()

    with open(args.commits_file, "r", encoding="utf-8") as f:
        commits = [line.strip() for line in f if line.strip()]

    with open(args.prs_file, "r", encoding="utf-8") as f:
        prs = json.load(f)

    print(f"分析 {len(commits)} 条 commits, {len(prs)} 个 PR")

    if not commits and not prs:
        notes = f"## Release Notes\n\nNo significant changes.\n\n---\n\n## 发布说明\n\n无重大变更。"
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(notes)
        return

    result = call_deepseek(build_prompt(args.mod_name, args.tag, commits, prs))

    notes = f"""## Release Notes

{result.get('release_notes_en', '')}

---

## 发布说明

{result.get('release_notes_zh', '')}

---

*{len(commits)} commits, {len(prs)} PRs, confidence={result.get('confidence', 0.5)}*"""

    print(f"生成完成: {result.get('summary', '')}")

    with open(args.output_file, "w", encoding="utf-8") as f:
        f.write(notes)


if __name__ == "__main__":
    main()
