
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


def generate_fallback_notes(prs: list, commits: str) -> tuple[str, str, str, str]:
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

    return bump, "API 调用失败，使用简单格式替代", notes_zh, notes_en


def main():
    parser = argparse.ArgumentParser(description="汇总分析多个 PR 并决定 Release 版本号")
    parser.add_argument("--mod-name", required=True, help="Mod 名称")
    parser.add_argument("--prs-file", required=True, help="PR 数据 JSON 文件路径")
    parser.add_argument("--commits-txt", required=True, help="Commit 历史 TXT 文件路径") # [新增] 参数
    parser.add_argument("--current-version", required=True, help="当前版本号")
    parser.add_argument("--manual-bump", default="auto", choices=["auto", "major", "minor", "patch", "none"],
                        help="手动指定的版本号升级策略")
    args = parser.parse_args()

    # 1. 加载数据
    with open(args.prs_file, "r", encoding="utf-8") as f:
        prs = json.load(f)
    
    commits_content = ""
    if os.path.exists(args.commits_txt):
        with open(args.commits_txt, "r", encoding="utf-8") as f:
            commits_content = f.read()

    current_version = args.current_version
    manual_bump = args.manual_bump

    # 2. 【增强】超级详细的 Debug 输出
    print(f"::group::🔍 [Debug] 脚本输入数据核查")
    print(f"当前版本: {current_version}")
    print(f"手动指定: {manual_bump}")
    print(f"Mod 名称: {args.mod_name}")
    print("-" * 30)
    print(f"PR 文件路径: {args.prs_file}")
    print(f"PR 数量: {len(prs)}")
    for pr in prs:
        print(f"  -> PR #{pr.get('number', '?')}: {pr.get('title', '')} (Files: {len(pr.get('files', []))})")
    print("-" * 30)
    print(f"Commit 文件路径: {args.commits_txt}")
    print(f"Commit 内容预览:\n{commits_content[:500]}...")
    print("::endgroup::")

    if not prs:
        print("没有发现待处理的 PR，跳过发布")
        set_output("bump", "none")
        set_output("new_version", current_version)
        set_output("reason", "没有发现合并的 PR")
        sys.exit(0)

    # 3. 构建 prompt 并调用 AI
    prompt = build_prompt(args.mod_name, prs, commits_content, current_version, manual_bump)

    try:
        result = call_deepseek(prompt)
        bump = result.get("bump", "none").lower()
        reason = result.get("reason", "AI 未提供原因")
        release_notes_zh = result.get("release_notes_zh", "")
        release_notes_en = result.get("release_notes_en", "")
        confidence = result.get("confidence", 0.5)
    except AuthenticationError:
        print("::warning::API 认证失败，使用备用方案生成简单发布说明")
        bump, reason, release_notes_zh, release_notes_en = generate_fallback_notes(prs, commits_content)
        confidence = 0.5
    except Exception as e:
        print(f"::warning::API 调用失败: {e}，使用备用方案")
        bump, reason, release_notes_zh, release_notes_en = generate_fallback_notes(prs, commits_content)
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

**分析原因**: {reason}
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