#!/usr/bin/env python3
"""跨文件检查：引用可达性 + 同主题表述对照。

补 `scan_artifact.py` 查不出的两类跨文件问题：
  1. 引用可达性——「章节名」/「条款名」指向的目标是否真实存在（悬空引用）
  2. 同主题表述对照——同一关键词在各载体的原句并排，供人工判定口径矛盾

为什么要单独做：`scan_artifact.py` 是单产物视角，只审一个文件本身干不干净；
而「A 文件说必须加载、B 文件说不自动加载」这类矛盾，天生要跨文件并排比对才看得见。

噪声控制（关键）：中文里「」有两种用法——**章节引用**（可达性有意义）与**强调性引号**
（如「为什么」「继续执行」，不是引用）。实测语料里大量「」只是强调用法，直接拿全部
「」去比对会被噪声淹没。本脚本只把「含结构词的「」」判为章节引用候选，避免满屏假悬空。

用法:
    python scan_cross_refs.py --files A B C ... [--keywords 词1 词2 ...] [--json]

本脚本不带任何内置默认文件——你的记忆文件、技能文件、agent 配置文件路径因人而异，
一律用 --files 显式传入（路径不存在的文件自动跳过）。
只读操作：不写入、不删除、不联网。
退出码: 0=未发现问题, 1=存在悬空引用或矛盾提示, 2=用法或环境错误
"""

import argparse
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 默认载体集已移除：你的记忆文件 / 技能文件 / 配置文件路径因人而异，
# 请一律通过 --files 显式传入。此处保留空值仅为兼容旧调用方式。
# 也可用环境变量 SELF_AUDIT_DEFAULT_FILES（冒号分隔）提供默认文件列表。
DEFAULT_FILES = [
    p for p in os.environ.get("SELF_AUDIT_DEFAULT_FILES", "").split(":") if p.strip()
]

# 结构词：出现在「」里，说明这多半是"章节/条款引用"而不是强调性引号。
# 判据来自实测——对全部「」做比对会产出几十条假悬空，加上这道筛子后只剩真候选。
# 收窄原则（2026-10-15 二次实测）：只留"章节名专用"的词，剔除"说明 / 要求 / 方法 /
# 方案 / 路径 / 步骤"等宽泛词——它们会把「疑似规则说明或举例」这类"引述脚本输出"的句子
# 误判成章节引用，凭空造出假悬空。
STRUCT_HINT_RE = re.compile(
    r"机制|规则|清单|流程|纪律|条款|章节|原则|分级|策略|守则|约定"
    r"|条例|准则|边界|职责|偏好|索引|分工"
)

# 引用内容过长时基本是"引述一整句话"，不是章节名——章节名很少超过这个长度。
MAX_REF_LEN = 18

# 含中文逗号/句号/分号的「」几乎都是引述整句（如脚本自身的输出文本），章节名不会这么写。
SENTENCE_PUNCT = "，。；"

# 引路指示词：紧跟在「」前面的这些词，说明后面是真正在指路，置信度最高——
# 此时即便同行有否定词也照查（"详见「X」，不得遗漏"是典型写法）。
LEAD_RE = re.compile(r"(详见|参见|见下|同上|依据|参照|遵循|根据)\s*[:：]?\s*$")
LEAD_WINDOW = 14

# 否定词：所在行若含否定语气，多半是在描述问题而非指路（如"未查出「技能加载约定自相矛盾」"）。
NEGATION_RE = re.compile(r"不|未|无|勿|非|禁|别|莫")

HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
BOLD_RE = re.compile(r"\*\*([^*\n]{2,80})\*\*")
REF_RE = re.compile(r"「([^」\n]{2,40})」")


def read_text(path):
    """读文件为纯文本；.json 递归取出所有字符串值，保证配置文件里的文本也在扫描范围内。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError):
        return None
    if ext != ".json":
        return raw
    try:
        data = json.loads(raw)
    except Exception:
        return raw  # 语法坏了也要能扫，交给 scan_artifact.py 报语法错误
    chunks = []

    def walk(node):
        if isinstance(node, str):
            chunks.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return "\n".join(chunks)


def collect_reachable(text):
    """收集"可达目标"集合：全部标题 + 全部加粗项名。"""
    pool = set()
    for line in text.split("\n"):
        m = HEADING_RE.match(line.strip())
        if m:
            pool.add(m.group(2).strip())
    for m in BOLD_RE.finditer(text):
        pool.add(m.group(1).strip())
    return pool


def is_reachable(ref, pool):
    """引用名是否指向某个真实存在的目标。

    允许三种匹配——完全相等、引用是目标的子串（引用写简称）、目标是引用的子串（引用带后缀）。
    另外按「·」「/」「|」「、」把引用拆成片段，**任一片段可达即算可达**：
    像「技能操作纪律 · 失败一次即熔断」这类"章节 + 加粗项"的复合引用是常见写法，
    整体比对必然落空，拆分后才不会把它误报成断链。
    """
    parts = [p.strip() for p in re.split(r"[·・/｜|、]", ref) if p.strip()]
    if not parts:
        parts = [ref.strip()]
    for part in parts:
        for target in pool:
            if part == target or part in target or target in part:
                return True
    return False


def check_refs(files, texts, pools):
    """扫描全部「」引用，只对"含结构词的引用"做可达性判定。"""
    dangling = []
    skipped = 0
    for path in files:
        text = texts.get(path)
        if text is None:
            continue
        # 可达池取全部文件的并集：跨文件引用（如指向档案层章节）也应判为可达
        union = pools["__union__"]
        for m in REF_RE.finditer(text):
            ref = m.group(1).strip()
            if len(ref) > MAX_REF_LEN or any(p in ref for p in SENTENCE_PUNCT):
                skipped += 1
                continue
            if not STRUCT_HINT_RE.search(ref):
                skipped += 1
                continue
            # 降噪（2026-10-15 实测补充，专治"谈论引用"的自指噪声）：
            # ① 反引号包裹的是示例占位，不是在引用真实章节
            if m.start() > 0 and text[m.start() - 1] == "`" and text[m.end()] == "`":
                skipped += 1
                continue
            # ② 紧跟引路指示词的引用置信度最高，无条件检查（"详见「X」，不得遗漏"是典型写法）
            lead = text[max(0, m.start() - LEAD_WINDOW):m.start()]
            has_lead = bool(LEAD_RE.search(lead))
            if not has_lead:
                ls = text.rfind("\n", 0, m.start()) + 1
                le = text.find("\n", m.end())
                line = text[ls: le if le != -1 else len(text)]
                # ③ 所在行出现"引用"二字 = 行文在谈论引用本身，不是在指路
                if "引用" in line:
                    skipped += 1
                    continue
                # ④ 所在行含否定语气 = 多半在描述问题，不是在指路
                #    （"两轮检查均未查出「技能加载约定自相矛盾」"——它是个被讨论的短语，不是章节引用）
                #    注意：必须先剔除引用自身的内容再判——否则引用名里的"不"（如「根本不存在的章节名」）
                #    会把自己判成"描述问题"，直接漏报（2026-10-15 实测踩到）。
                if NEGATION_RE.search(REF_RE.sub("", line)):
                    skipped += 1
                    continue
            if is_reachable(ref, union):
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            dangling.append({
                "file": path,
                "line": line_no,
                "ref": ref,
                "snippet": text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ").strip(),
            })
    return dangling, skipped


def contrast_keywords(files, texts, keywords):
    """同主题表述对照：把各载体中含关键词的原句并排列出，标出疑似否定表述。"""
    table = []
    for kw in keywords:
        rows = []
        for path in files:
            text = texts.get(path)
            if text is None:
                continue
            for i, line in enumerate(text.split("\n"), 1):
                if kw in line:
                    stmt = line.strip()
                    if not stmt:
                        continue
                    rows.append({
                        "file": os.path.basename(path),
                        "line": i,
                        "negation": bool(NEGATION_RE.search(stmt)),
                        "text": stmt[:160],
                    })
        table.append({"keyword": kw, "rows": rows})
    return table


def analyze(files, keywords=None):
    """核心入口：读入载体 → 查引用可达性 → 生成同主题对照。

    抽成函数供外部复用（`evolve.py --ratchet` 的跨文件回归用例直接调它）。
    返回 dict：files / dangling / skipped_non_reference / contrast。
    """
    files = [f for f in files if os.path.isfile(f)]
    texts = {}
    pools = {"__union__": set()}
    for path in files:
        text = read_text(path)
        if text is None:
            continue
        texts[path] = text
        pools[path] = collect_reachable(text)
        pools["__union__"] |= pools[path]

    scan_files = [f for f in files if f in texts]
    dangling, skipped = check_refs(scan_files, texts, pools)
    table = contrast_keywords(scan_files, texts, keywords) if keywords else []
    return {
        "files": scan_files,
        "dangling": dangling,
        "skipped_non_reference": skipped,
        "contrast": table,
    }


def main():
    parser = argparse.ArgumentParser(
        description="跨文件检查：引用可达性 + 同主题表述对照（只读）"
    )
    parser.add_argument("--files", nargs="*", default=None,
                        help="要扫描的文件（记忆文件/技能文件/配置文件等载体），必传或用环境变量提供")
    parser.add_argument("--keywords", nargs="*", default=None,
                        help="同主题对照的关键词，如 --keywords 四处同步 版本号")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    files = args.files if args.files else DEFAULT_FILES
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print("用法错误：没有任何可扫描的文件。请用 --files 指定，或确认默认载体集存在。")
        return 2

    keywords = args.keywords or ["版本号", "自审", "载体", "同步"]
    result = analyze(files, keywords)
    scan_files = result["files"]
    dangling = result["dangling"]
    skipped = result["skipped_non_reference"]
    table = result["contrast"]

    if args.json:
        print(json.dumps({
            "files": scan_files,
            "dangling": dangling,
            "skipped_non_reference": skipped,
            "contrast": table,
        }, ensure_ascii=False, indent=2))
        return 1 if dangling else 0

    print("跨文件检查 · 引用可达性与同主题对照")
    print("扫描文件：%d 个" % len(scan_files))
    for f in scan_files:
        print("  - %s" % f)
    print("-" * 66)
    print("【一】引用可达性")
    print("  检出「」引用中，判为章节引用候选而目标不存在的：%d 条" % len(dangling))
    print("  （另有 %d 条「」属强调性引号用法，按设计跳过，不计入悬空）" % skipped)
    if dangling:
        for d in dangling:
            print("    [悬空] %s 第 %d 行：「%s」" % (
                os.path.basename(d["file"]), d["line"], d["ref"]))
            print("           %s" % d["snippet"])
    else:
        print("     无悬空引用")
    print("-" * 66)
    print("【二】同主题表述对照（供人工判定口径是否矛盾）")
    for block in table:
        rows = block["rows"]
        print("  ● 关键词「%s」命中 %d 处" % (block["keyword"], len(rows)))
        for r in rows:
            flag = "  ⚠含否定词" if r["negation"] else ""
            print("      %s:%d%s" % (r["file"], r["line"], flag))
            print("        %s" % r["text"])
    print("-" * 66)
    if dangling:
        print("结论：存在 %d 条悬空引用 —— 引用指向的章节/条款并不存在，须修正或补建。" % len(dangling))
        return 1
    print("结论：未发现悬空引用。同主题对照需人工判读，脚本只并排、不替人下结论。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
