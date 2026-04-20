#!/usr/bin/env python3
"""
RimMind Mod - AI 辅助 Release 分析脚本（汇总版）
调用 DeepSeek API 汇总分析多个 PR，决定版本号升级类型并生成 Release Notes
"""

import argparse
import json
import os
import re
import sys
import time

try:
    from openai import OpenAI, AuthenticationError, APIError
except ImportError:
    print("::error::缺少 openai 依赖，请安装: pip install openai")
    sys.exit(1)


def parse_version(v: str) -> tuple[int, int, int]:
    """解析 SemVer 字符串，支持 v前缀"""
    v = v.strip().lstrip("v")
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        raise ValueError(f"无法解析版本号: {v}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump_version(current: str, bump: str) -> str:
    """根据 bump 类型计算新版本号"""
    major, minor, patch = parse_version(current)
    if bump == "major":
        return f"v{major + 1}.0.0"
    elif bump == "minor":
        return f"v{major}.{minor + 1}.0"
    elif bump == "patch":
        return f"v{major}.{minor}.{patch + 1}"
    else:
        return current


def build_prompt(mod_name: str, prs: list[dict], current_version: str, manual_bump: str) -> str:
    """构建发送给 AI 的 Prompt"""

    pr_summaries = []
    for i, pr in enumerate(prs, 1):
        title = pr.get("title", "")
        body = pr.get("body", "") or "(无描述)"
        labels = pr.get("labels", [])
        author = pr.get("author", "unknown")
        files = pr.get("files", [])

        file_list = []
        for f in files:
            path = f.get("path", "")
            change_type = f.get("changeType", "modified")
            file_list.append(f"    [{change_type}] {path}")

        file_str = "\n".join(file_list) if file_list else "    (无变更文件信息)"

        pr_summaries.append(f"""
PR #{i}:
  标题: {title}
  作者: {author}
  标签: {', '.join(labels) if labels else '(无)'}
  描述: {body[:500]}{'...' if len(body) > 500 else ''}
  变更文件:
{file_str}
""")

    all_prs_str = "\n---\n".join(pr_summaries)
    manual_hint = ""
    if manual_bump != "auto":
        manual_hint = f"\n【重要】维护者手动指定了版本号升级为: {manual_bump}。请尽量遵循此指定，但如果你发现指定不合理（如指定 patch 但实际有 breaking change），请在 reason 中说明并使用你认为正确的版本。"

    prompt = f"""你是 RimWorld Mod 发布管理员，专门负责汇总分析多个 Pull Request 并决定版本号升级策略。

## 当前信息

- **Mod 名称**: {mod_name}
- **当前版本**: {current_version}
- **待汇总 PR 数量**: {len(prs)}
{manual_hint}

## 待汇总 PR 列表

{all_prs_str}

## 版本号判断规则（严格执行）

你必须根据以下规则判断整体版本号升级类型，考虑所有 PR 的综合影响：

1. **MAJOR** (X.y.z → X+1.0.0)
   - 任何一个 PR 破坏了存档兼容性或其他 mod 的依赖
   - 删除/修改了公共 API、Harmony Patch 的 target method 签名
   - 修改了 `Defs/` 下的核心 XML 结构可能导致旧存档不兼容
   - 有 PR 标签含 `major` 或 `breaking`

2. **MINOR** (x.Y.z → x.Y+1.0)
   - 新增了功能、新事件、新配置选项
   - 新增了新的 AI 行为/意图类型
   - 新增了新的对话/故事类型
   - 有显著的功能性变更但没有破坏性
   - 有 PR 标签含 `feature` 或 `minor`

3. **PATCH** (x.y.Z → x.y.Z+1)
   - 仅修复 bug、崩溃、错误行为
   - 性能优化（不影响功能）
   - 文档更新、本地化文件修复
   - 代码重构不改变外部行为
   - 所有 PR 都是小修补级别

4. **NONE** (不升级)
   - 仅修改 CI/CD 配置、`.github/` 目录
   - 无实质性变更
   - 只有文档格式化或注释修改

## 特殊判断规则

- 如果多个 PR 中有任何一个是 breaking change，整体必须升 MAJOR
- 如果有新功能且没有 breaking change，升 MINOR
- 如果所有 PR 都是 bugfix/优化/文档，升 PATCH
- 如果仅是 CI 或无实质变更，返回 NONE
- 如果维护者手动指定了版本号，优先遵循手动指定（但你可以在 reason 中说明是否合理）

## 输出格式

你必须输出一个 JSON 对象，不要包含任何 markdown 代码块标记，不要包含任何解释性文字，只返回 JSON：

{{
  "bump": "minor",
  "reason": "本次发布包含3个 PR：2个 bugfix + 1个新功能（情绪记忆系统），无破坏性变更，建议升 minor",
  "release_notes_zh": "### 新增\\n- 情绪记忆模块...\\n\\n### 修复\\n- 修复了 XXX 崩溃...",
  "release_notes_en": "### Added\\n- Emotion memory module...\\n\\n### Fixed\\n- Fixed XXX crash...",
  "confidence": 0.9
}}

其中 `confidence` 是你对判断的自信度（0-1）。
"""
    return prompt.strip()


def call_deepseek(prompt: str) -> dict:
    """调用 DeepSeek API，带重试机制"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise AuthenticationError("DEEPSEEK_API_KEY 环境变量未设置")

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是 RimWorld Mod 发布管理员，专门负责汇总分析多个 Pull Request 并决定版本号升级策略。请严格按要求返回 JSON，不要有任何额外文字。"},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=4096,
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except AuthenticationError as e:
            print(f"::error::API 认证失败: {e}")
            print("::error::请检查 DEEPSEEK_API_KEY 是否有效")
            raise
        except APIError as e:
            print(f"::warning::API 错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"::notice::等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                raise
        except Exception as e:
            print(f"::warning::未知错误 (尝试 {attempt + 1}/{max_retries}): {e}")
            raise


def set_output(name: str, value: str):
    """设置 GitHub Actions 输出"""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def generate_fallback_notes(prs: list) -> tuple[str, str, str, str]:
    """当 API 失败时生成简单的发布说明"""
    lines_zh = []
    lines_en = []
    has_feature = False
    has_fix = False

    for pr in prs:
        title = pr.get("title", "无标题")
        labels = pr.get("labels", [])
        body = pr.get("body", "") or ""

        if any(l in labels for l in ["feature", "minor", "enhancement"]):
            has_feature = True
        if any(l in labels for l in ["bugfix", "patch", "fix", "bug"]):
            has_fix = True

        lines_zh.append(f"- {title}")
        lines_en.append(f"- {title}")

    if has_feature:
        bump = "minor"
    elif has_fix:
        bump = "patch"
    else:
        bump = "patch"

    notes_zh = "### 变更\n" + "\n".join(lines_zh) if lines_zh else "### 变更\n- 常规更新"
    notes_en = "### Changes\n" + "\n".join(lines_en) if lines_en else "### Changes\n- Routine updates"

    return bump, "API 认证失败，使用简单格式替代", notes_zh, notes_en


def main():
    parser = argparse.ArgumentParser(description="汇总分析多个 PR 并决定 Release 版本号")
    parser.add_argument("--mod-name", required=True, help="Mod 名称")
    parser.add_argument("--prs-file", required=True, help="PR 数据 JSON 文件路径")
    parser.add_argument("--current-version", required=True, help="当前版本号")
    parser.add_argument("--manual-bump", default="auto", choices=["auto", "major", "minor", "patch", "none"],
                        help="手动指定的版本号升级策略")
    args = parser.parse_args()

    with open(args.prs_file, "r", encoding="utf-8") as f:
        prs = json.load(f)

    current_version = args.current_version
    manual_bump = args.manual_bump

    print(f"::group::汇总分析 {len(prs)} 个 PR")
    for pr in prs:
        print(f"  PR #{pr.get('number', '?')}: {pr.get('title', '')}")
    print(f"当前版本: {current_version}")
    print(f"手动指定: {manual_bump}")
    print("::endgroup::")

    if not prs:
        print("没有发现待处理的 PR，跳过发布")
        set_output("bump", "none")
        set_output("new_version", current_version)
        set_output("reason", "没有发现合并的 PR")
        sys.exit(0)

    # 构建 prompt 并调用 AI
    prompt = build_prompt(args.mod_name, prs, current_version, manual_bump)

    try:
        result = call_deepseek(prompt)
        bump = result.get("bump", "none").lower()
        reason = result.get("reason", "AI 未提供原因")
        release_notes_zh = result.get("release_notes_zh", "")
        release_notes_en = result.get("release_notes_en", "")
        confidence = result.get("confidence", 0.5)
    except AuthenticationError:
        print("::warning::API 认证失败，使用备用方案生成简单发布说明")
        bump, reason, release_notes_zh, release_notes_en = generate_fallback_notes(prs)
        confidence = 0.5
    except Exception as e:
        print(f"::warning::API 调用失败: {e}，使用备用方案")
        bump, reason, release_notes_zh, release_notes_en = generate_fallback_notes(prs)
        confidence = 0.5

    # 如果维护者手动指定了版本号，以手动指定为准
    if manual_bump != "auto" and manual_bump != "none":
        if bump != manual_bump:
            reason += f" (维护者手动覆盖: {manual_bump})"
        bump = manual_bump

    # 计算新版本号
    if bump in ("major", "minor", "patch"):
        try:
            new_version = bump_version(current_version, bump)
        except ValueError as e:
            print(f"::warning::{e}，默认从 v0.0.0 开始")
            new_version = bump_version("v0.0.0", bump)
    else:
        new_version = current_version

    # 组合 release notes
    combined_notes = f"""## Release Notes

{release_notes_en}

---

## 发布说明

{release_notes_zh}

---

*本次发布共汇总 {len(prs)} 个 PR，分析结果: bump={bump}, confidence={confidence}*
"""

    print(f"::group::汇总分析结果")
    print(f"  bump: {bump}")
    print(f"  new_version: {new_version}")
    print(f"  confidence: {confidence}")
    print(f"  reason: {reason}")
    print(f"::endgroup::")

    # 输出结果
    set_output("bump", bump)
    set_output("new_version", new_version)
    set_output("release_notes", combined_notes.replace("\n", "%0A"))
    set_output("reason", reason)

    # 保存 release notes 到文件供后续使用
    with open("/tmp/release_notes.md", "w", encoding="utf-8") as f:
        f.write(combined_notes)

    if bump == "none":
        print("未检测到需要升级的变更，跳过 release 创建")
        sys.exit(0)


if __name__ == "__main__":
    main()
