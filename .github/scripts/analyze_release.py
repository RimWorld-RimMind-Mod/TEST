#!/usr/bin/env python3
"""
AI Release Analyzer
分析 PR 变更并决定版本号升级策略
"""

import argparse
import json
import os
import re
import sys
from typing import Optional

from openai import OpenAI


def parse_version(version: str) -> tuple:
    """解析版本号 v1.2.3 -> (1, 2, 3)"""
    match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', version)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())


def bump_version(current: str, bump_type: str) -> str:
    """根据升级类型计算新版本号"""
    major, minor, patch = parse_version(current)

    if bump_type == 'major':
        return f"v{major + 1}.0.0"
    elif bump_type == 'minor':
        return f"v{major}.{minor + 1}.0"
    elif bump_type == 'patch':
        return f"v{major}.{minor}.{patch + 1}"
    else:
        return current


def analyze_prs_with_ai(
    client: OpenAI,
    mod_name: str,
    prs: list,
    current_version: str,
    manual_bump: Optional[str] = None
) -> dict:
    """使用 AI 分析 PR 并决定版本号"""

    # 构建提示词
    prs_text = json.dumps(prs, indent=2, ensure_ascii=False) if prs else "没有新的 PR"

    prompt = f"""你是一个专业的软件版本管理助手。请分析以下 GitHub PR 列表，决定版本号升级策略。

Mod 名称: {mod_name}
当前版本: {current_version}
手动指定升级类型: {manual_bump if manual_bump and manual_bump != 'auto' else '无（自动判断）'}

PR 列表 (JSON 格式):
{prs_text}

请根据以下语义化版本规范分析：
- MAJOR: 不兼容的 API 修改、重大功能变更、破坏性更新
- MINOR: 向下兼容的功能新增、新特性添加
- PATCH: 向下兼容的问题修复、bug 修复、小优化

请严格按以下 JSON 格式输出，不要包含任何其他内容：
{{
  "bump": "patch|minor|major|none",
  "new_version": "vX.Y.Z",
  "reason": "简短说明原因（中文）",
  "release_notes": "Markdown 格式的发布说明，包含：\\n## 变更概要\\n\\n### 新功能\\n- ...\\n\\n### 修复\\n- ...\\n\\n### 其他\\n- ..."
}}

要求：
1. 如果没有实质性变更（只有文档更新、格式化等），返回 "bump": "none"
2. release_notes 使用 Markdown 格式，按类别分组
3. 每个 PR 的标题应该被概括到相应的类别中
4. 只输出 JSON，不要有任何解释性文字
"""

    try:
        response = client.chat.completions.create(
            model=os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'),
            messages=[
                {"role": "system", "content": "你是一个专业的软件版本管理助手，擅长语义化版本控制(SemVer)和生成发布说明。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2000
        )

        content = response.choices[0].message.content.strip()

        # 尝试解析 JSON
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # 尝试从 markdown 代码块中提取
            match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if match:
                result = json.loads(match.group(1))
            else:
                raise

        # 如果手动指定了 bump 类型，覆盖 AI 的决定
        if manual_bump and manual_bump != 'auto':
            result['bump'] = manual_bump
            result['new_version'] = bump_version(current_version, manual_bump)
            result['reason'] = f"手动指定为 {manual_bump} 升级"

        return result

    except Exception as e:
        print(f"AI 分析失败: {e}", file=sys.stderr)
        # 默认降级为 patch
        return {
            "bump": "patch" if prs else "none",
            "new_version": bump_version(current_version, "patch"),
            "reason": f"AI 分析失败，默认升级: {str(e)}",
            "release_notes": "## 变更概要\n\n" + "\n".join([f"- #{pr['number']}: {pr['title']}" for pr in prs]) if prs else "无变更"
        }


def set_github_output(outputs: dict):
    """设置 GitHub Actions 输出"""
    github_output = os.getenv('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a') as f:
            for key, value in outputs.items():
                # 处理多行值
                if '\n' in str(value):
                    f.write(f"{key}<<EOF\n{value}\nEOF\n")
                else:
                    f.write(f"{key}={value}\n")

    # 同时打印到日志
    for key, value in outputs.items():
        print(f"::set-output name={key}::{value}")
        print(f"{key}={value}")


def main():
    parser = argparse.ArgumentParser(description='AI Release Analyzer')
    parser.add_argument('--mod-name', required=True, help='Mod 名称')
    parser.add_argument('--prs-file', required=True, help='PR 列表 JSON 文件路径')
    parser.add_argument('--current-version', required=True, help='当前版本号')
    parser.add_argument('--manual-bump', default='auto', help='手动指定升级类型')

    args = parser.parse_args()

    # 读取 PR 列表
    try:
        with open(args.prs_file, 'r') as f:
            content = f.read()
            # 处理可能的转义
            content = content.replace('\\"', '"')
            prs = json.loads(content) if content.strip() else []
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"读取 PR 文件失败: {e}", file=sys.stderr)
        prs = []

    print(f"读取到 {len(prs)} 个 PR")

    # 初始化 OpenAI 客户端
    client = OpenAI(
        api_key=os.getenv('DEEPSEEK_API_KEY'),
        base_url=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')
    )

    # 分析
    result = analyze_prs_with_ai(
        client=client,
        mod_name=args.mod_name,
        prs=prs,
        current_version=args.current_version,
        manual_bump=args.manual_bump
    )

    print(f"分析结果:")
    print(f"  bump: {result['bump']}")
    print(f"  new_version: {result['new_version']}")
    print(f"  reason: {result['reason']}")

    # 设置输出
    set_github_output({
        'bump': result['bump'],
        'new_version': result['new_version'],
        'reason': result['reason'],
        'release_notes': result['release_notes']
    })

    # 如果无需升级，返回非零退出码
    if result['bump'] == 'none':
        sys.exit(0)


if __name__ == '__main__':
    main()
