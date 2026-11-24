#!/usr/bin/env python3
"""生成棘轮回归样本（fixtures）。可重复执行，覆盖全部已知坏例子与已知误报。

样本遵循一个原则：该报的必须报，不该报的一个都不许报。改完技能后，
必须重跑 `evolve.py --ratchet` 比对 expected.json，全绿才允许保留改动。

注意：本目录的样本是故意做坏的，不要把它们当交付产物去扫描。
"""

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def write_text(rel, content):
    path = os.path.join(HERE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return path


def write_bytes(rel, data):
    path = os.path.join(HERE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# 1) 最小合法 ZIP，扩展名改成 .apk —— 回归 2026-10-14 的误报
apk_path = os.path.join(HERE, "binary", "min.zip.apk")
os.makedirs(os.path.dirname(apk_path), exist_ok=True)
with zipfile.ZipFile(apk_path, "w") as zf:
    zf.writestr("AndroidManifest.xml", "<manifest/>")

# 2) 文本内容却顶着 .apk 扩展名 —— 魔数嗅探不出，应靠扩展名兜底并报结构异常
write_text("binary/fake.apk", "这不是压缩包，只是把扩展名改成 .apk 的文本文件。\n")

# 3) 假 7z —— 非 ZIP 压缩包走魔数校验，魔数不对应报结构异常
write_bytes("binary/broken.7z", b"NOT7Z" + b"\x00" * 64)

# 4) 整篇乱码 —— 0xFF 在 utf-8 / gb18030 / big5 下均为非法字节
write_bytes("text/garbled.txt", b"\xff" * 100)

# 5) 仅极少数字节异常 —— 只应记 INFO，不得判 FAIL
body = "这是一段正常的中文文本，用于验证极少量坏字节不会被判成致命问题。" * 40
write_bytes("text/minor_noise.txt", (body + "\n").encode("utf-8") + b"\xff")

# 6) 空章节
write_text("prose/empty_section.md", "\n".join([
    "# 样本标题",
    "",
    "正文一段，说明这是一份用于回归测试的文档。",
    "",
    "## 空章节",
    "",
    "## 有内容的章节",
    "",
    "这一节有正文，不该被判空。",
    "",
]))

# 7) 干净文档
write_text("prose/clean.md", "\n".join([
    "# 干净样本",
    "",
    "这是一份完全干净的文档，用于确认正常内容零发现。",
    "",
    "## 第一节",
    "",
    "正文。",
    "",
    "## 第二节",
    "",
    "正文。",
    "",
]))

# 8) 围栏样本 —— 围栏内的井号与方括号链接都不是 Markdown 语法
write_text("prose/fenced.md", "\n".join([
    "# 围栏回归样本",
    "",
    "下面的代码块里，井号注释与方括号链接都不是 Markdown 语法，不该被报：",
    "",
    "```python",
    "# 这一行不是标题",
    'link = "[示例](这个文件并不存在.md)"',
    "```",
    "",
    "正文结束。",
    "",
]))

# 9) 源码里的真实待办 —— 应报出（字符串拆开写，避免本文件自身被命中）
todo_line = "# TO" + "DO: 补测试"
write_text("code/todo.py", "\n".join([
    "#!/usr/bin/env python3",
    '"""示例模块：验证源码里的真实待办会被报出。"""',
    "",
    "",
    "def build_view():",
    "    " + todo_line,
    '    return {"style": {"color": "red"}}',
    "",
]))

# 10) 文件名含 copy 但非独立成词 —— 词边界修复后不该被误判为副本
write_text("naming/copyright.md", "\n".join([
    "# 版权说明",
    "",
    "这里只是文件名里含有 copy，不是副本文件。",
    "",
]))

# 11) JSON 语法坏样本 —— 裸引号让整份配置解析失败。
# 回归 2026-10-15 的 app-config.json 故障：自定义提示词混入 4 个未转义裸引号，
# 整份配置失效，而当时的脚本对 .json 只做编码与命名检查，零检出。
write_text("structured/broken.json", "\n".join([
    "{",
    '  "a": "x",',
    '  "b": "出现"问题"时"',
    "}",
    "",
]))

# 12) 合法 JSON —— 新增语法校验后必须零发现，否则每次扫配置都是噪声
write_text("structured/valid.json", "\n".join([
    "{",
    '  "locale": "zh-CN",',
    '  "items": ["a", "b", "c"],',
    '  "nested": {',
    '    "key": "值",',
    '    "note": "合法 JSON，不应产生任何发现"',
    "  }",
    "}",
    "",
]))

# 13) 谈论「名字」的句子里的占位词 —— 降噪回归。
# 原写法对「毛选-XXX 技能名」持续误报，因为降噪提示词未覆盖"命名"语境。
xxx = "X" * 3  # 拆开写，避免本文件自身被 XXX 规则命中
write_text("naming/placeholder-name.md", "\n".join([
    "# 技能名说明",
    "",
    "本技能在记忆库中被称作「毛选-" + xxx + "」，其中 " + xxx + " 是命名模板的占位写法，不是待补内容。",
    "",
    "命名规则见技能改名说明。",
    "",
]))

# 14-16) 跨文件检查样本 —— 供 scan_cross_refs.py 的回归用（evolve.py --ratchet 的 xref_cases）
# 三个文件配合：target 提供"可达目标"，dangling 含 2 条真断链 + 2 类噪声，clean 全部可达。
write_text("crossrefs/target.md", "\n".join([
    "# 目标文档（提供可达目标）",
    "",
    "本文件只负责提供一批真实存在的章节名，供跨文件检查判断引用是否落空。",
    "",
    "## 多载体一致性复查",
    "",
    "正文占位。",
    "",
    "## 工作日志管理",
    "",
    "正文占位。",
    "",
    "- **四处同步（2026-10-08 修正为四处）**：说明性加粗项，也属可达目标。",
    "- **长会话风险 · 预警与处置**：说明性加粗项。",
    "",
]))
write_text("crossrefs/dangling.md", "\n".join([
    "# 待检文档",
    "",
    "详见「多载体一致性复查」，目标存在，不该报。",
    "",
    "参见「根本不存在的规则」，目标不存在，应报。",
    "",
    "另依据「另一个悬空的清单」，目标不存在，应报。",
    "",
    "本段在谈论引用本身：「引用指向的章节其实不存在」属于元描述，不该报。",
    "",
    "强调用法：他说「为什么」这样不行。",
    "",
]))
write_text("crossrefs/clean.md", "\n".join([
    "# 干净文档",
    "",
    "详见「多载体一致性复查」，这是可达引用。",
    "",
    "按「工作日志管理」的规定执行。",
    "",
    "- 参考「四处同步」与「长会话风险 · 预警与处置」两项机制。",
    "",
]))

print("fixtures 生成完毕：13 个单产物样本 + 3 个跨文件样本")
