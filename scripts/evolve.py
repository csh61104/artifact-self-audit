#!/usr/bin/env python3
"""技能的自我进化引擎：留痕统计（--report）、棘轮回归（--ratchet）、契约检查（--contract）。

设计立场：本脚本只做「诊断」与「验证」，绝不自动修改技能本体。
所有修订以提案文件形式落到 logs/proposals/，须经用户确认后才由人（AI）动手改。
理由：一个自审技能若能自动改自己，等于自己给自己发合格证——那正是本技能明令禁止的伪造行为。

用法:
    python evolve.py --report                    查看历史留痕统计与进化建议
    python evolve.py --ratchet                   跑 fixtures 回归，全绿才允许保留改动
    python evolve.py --contract                  检查 SKILL.md 是否仍保留五项对外契约
    python evolve.py --append --date ... ...     向 logs/audit-log.tsv 追加一条自审记录

退出码: 0=通过/正常, 1=发现问题, 2=用法或环境错误
只读之外唯一的写操作是 --append（仅追加，不覆盖）。
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
FIXTURES_DIR = os.path.join(SKILL_ROOT, "fixtures")
LOGS_DIR = os.path.join(SKILL_ROOT, "logs")
AUDIT_LOG = os.path.join(LOGS_DIR, "audit-log.tsv")
PROPOSALS_DIR = os.path.join(LOGS_DIR, "proposals")
SKILL_MD = os.path.join(SKILL_ROOT, "SKILL.md")

sys.path.insert(0, HERE)
try:
    import scan_artifact as sa
except ImportError:  # pragma: no cover
    sa = None

try:
    import scan_cross_refs as scr
except ImportError:  # pragma: no cover
    scr = None

# audit-log.tsv 的列顺序，改动需同步 SKILL.md 与 references/evolution.md
LOG_COLUMNS = ["日期", "产物", "档位", "轮次", "FAIL", "WARN", "INFO",
               "得分", "判定", "误报", "漏报", "卡点关卡", "备注"]

# 五项对外契约：任何人（含未来的自我进化）都不得改动的接口
CONTRACT_ITEMS = [
    ("技能名", "name: artifact-self-audit"),
    ("六关-需求对齐", "需求对齐"),
    ("六关-事实核验", "事实核验"),
    ("六关-完整性", "完整性"),
    ("六关-格式与可读性", "格式与可读性"),
    ("六关-交付方式", "交付方式"),
    ("六关-风险与合规", "风险与合规"),
    ("档位-轻", "轻"),
    ("档位-常规", "常规"),
    ("档位-重", "重"),
    ("标记-前缀", "[自审]"),
    ("标记-扫描计数", "扫描:"),
    ("标记-六关结论", "六关:"),
    ("引用-六关规则文件", "references/audit-rules.md"),
    ("引用-确定性扫描脚本", "scripts/scan_artifact.py"),
]


def read_log():
    """读取 audit-log.tsv，返回记录列表；文件不存在或只有表头时返回空列表。"""
    if not os.path.exists(AUDIT_LOG):
        return []
    with open(AUDIT_LOG, encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    if len(lines) <= 1:
        return []
    rows = []
    for ln in lines[1:]:
        cells = ln.split("\t")
        cells += [""] * (len(LOG_COLUMNS) - len(cells))
        rows.append(dict(zip(LOG_COLUMNS, cells)))
    return rows


def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def cmd_append(args):
    """追加一条自审记录（仅追加，永不覆盖）。"""
    os.makedirs(LOGS_DIR, exist_ok=True)
    row = [args.date or "", args.artifact or "", args.tier or "",
           str(args.rounds if args.rounds is not None else ""),
           str(args.fail if args.fail is not None else ""),
           str(args.warn if args.warn is not None else ""),
           str(args.info if args.info is not None else ""),
           args.score or "", args.verdict or "",
           str(args.fp), str(args.miss), args.stuck or "", args.note or ""]
    need_header = not os.path.exists(AUDIT_LOG) or os.path.getsize(AUDIT_LOG) == 0
    with open(AUDIT_LOG, "a", encoding="utf-8", newline="\n") as fh:
        if need_header:
            fh.write("\t".join(LOG_COLUMNS) + "\n")
        fh.write("\t".join(row) + "\n")
    print("已追加 1 条记录到 %s" % AUDIT_LOG)
    print("当前累计：" + str(len(read_log())) + " 条")
    return 0


def cmd_report(args):
    """读历史留痕，输出统计与进化建议。只给建议，不改任何文件。"""
    rows = read_log()
    print("自我进化 · 留痕统计")
    print("-" * 60)
    if not rows:
        print("尚无记录。每次自审后按 SKILL.md「留痕」一节追加一行，累积后再来统计。")
        print("（轻档一次通过不记；只记重档、出现修复、出现误报的场次）")
        return 0

    total = len(rows)
    tiers = {}
    scores = []
    stuck_counter = {}
    fp_total = 0
    miss_total = 0
    fail_total = 0
    warn_total = 0
    for r in rows:
        tiers[r["档位"]] = tiers.get(r["档位"], 0) + 1
        if r["得分"]:
            try:
                scores.append(float(r["得分"]))
            except ValueError:
                pass
        if r["卡点关卡"]:
            for k in r["卡点关卡"].replace("，", ",").split(","):
                k = k.strip()
                if k:
                    stuck_counter[k] = stuck_counter.get(k, 0) + 1
        fp_total += to_int(r["误报"])
        miss_total += to_int(r["漏报"])
        fail_total += to_int(r["FAIL"])
        warn_total += to_int(r["WARN"])

    print("记录总数：%d 条" % total)
    print("档位分布：" + ("、".join("%s %d" % (k, v) for k, v in sorted(tiers.items()))
                          if tiers else "—"))
    if scores:
        print("得分：平均 %.1f，最近一次 %.1f，最低 %.1f" % (
            sum(scores) / len(scores), scores[-1], min(scores)))
    print("机器可检项累计：FAIL %d / WARN %d" % (fail_total, warn_total))
    print("人工判定：误报 %d 次，漏报 %d 次" % (fp_total, miss_total))
    if stuck_counter:
        top = sorted(stuck_counter.items(), key=lambda kv: -kv[1])
        print("高频卡点：" + "、".join("%s×%d" % (k, v) for k, v in top))

    print("-" * 60)
    print("进化建议（仅提示，改不改由你拍板）：")
    advice = []
    noise = fail_total + warn_total
    if noise and fp_total / noise > 0.2:
        advice.append("误报率 %.0f%%（>20%%）：先查脚本白名单/降噪阈值，而不是继续加规则。"
                      % (fp_total / noise * 100))
    if miss_total:
        advice.append("出现 %d 次漏报：优先级最高，属规则盲区，应补检查项。" % miss_total)
    if top_stuck := [k for k, v in stuck_counter.items() if v >= 3]:
        advice.append("关卡「%s」累计卡壳 ≥3 次：复核该关判定口径是否过严。"
                      % "」「".join(top_stuck))
    if total >= 10:
        advice.append("记录已累积 %d 条，可跑一次完整进化评估（见 references/evolution.md）。" % total)
    if not advice:
        advice.append("数据看起来健康，暂无需改动。")
    for i, line in enumerate(advice, 1):
        print("  %d. %s" % (i, line))
    print("-" * 60)
    print("提示：本脚本只诊断不开方。任何修订都须写成提案（logs/proposals/）并经用户确认。")
    return 0


def cmd_ratchet(args):
    """跑 fixtures 回归：全部样本符合期望才算通过，否则说明改动弄坏了既有能力。"""
    if sa is None:
        print("环境错误：无法导入 scan_artifact.py，棘轮无法运行。")
        return 2
    exp_path = os.path.join(FIXTURES_DIR, "expected.json")
    if not os.path.exists(exp_path):
        print("环境错误：缺少 fixtures/expected.json")
        return 2
    with open(exp_path, encoding="utf-8") as fh:
        spec = json.load(fh)

    print("自我进化 · 棘轮回归")
    print("基线：" + str(spec.get("baseline", "—")))
    print("-" * 60)
    passed = 0
    failed = []
    cases = spec["cases"]
    xref_cases = spec.get("xref_cases", [])
    total = len(cases) + len(xref_cases)
    for case in cases:
        path = os.path.join(FIXTURES_DIR, case["path"])
        if not os.path.exists(path):
            failed.append((case["id"], ["样本文件缺失：" + case["path"]]))
            print("[缺失] %-26s %s" % (case["id"], case["path"]))
            continue
        res = sa.scan_file(path)
        counts = {"FAIL": 0, "WARN": 0, "INFO": 0}
        checks = []
        for f in res["findings"]:
            counts[f["level"]] = counts.get(f["level"], 0) + 1
            checks.append(f["check"])

        problems = []
        exp = case["expect"]
        if "fail" in exp and counts["FAIL"] != exp["fail"]:
            problems.append("FAIL 期望 %d 实得 %d" % (exp["fail"], counts["FAIL"]))
        if "warn" in exp and counts["WARN"] != exp["warn"]:
            problems.append("WARN 期望 %d 实得 %d" % (exp["warn"], counts["WARN"]))
        for name in exp.get("checks_include", []):
            if name not in checks:
                problems.append("缺少期望检查项「%s」" % name)
        for name in exp.get("checks_exclude", []):
            if name in checks:
                problems.append("出现应排除项「%s」" % name)

        if problems:
            failed.append((case["id"], problems))
            print("[不合规] %-24s FAIL=%d WARN=%d INFO=%d" % (
                case["id"], counts["FAIL"], counts["WARN"], counts["INFO"]))
            for p in problems:
                print("         · %s" % p)
        else:
            passed += 1
            print("[通过] %-26s FAIL=%d WARN=%d INFO=%d" % (
                case["id"], counts["FAIL"], counts["WARN"], counts["INFO"]))
    # 跨文件检查回归（2026-10-15 新增）：单产物棘轮管不到 scan_cross_refs.py，
    # 而它的降噪判据本身很敏感（上线当天连踩两次：筛子过激致漏报、否定词误伤致漏报），
    # 因此必须有自己的样本锁住"该报的报、不该报的一个都不报"。
    for case in xref_cases:
        if scr is None:
            failed.append((case["id"], ["无法导入 scan_cross_refs.py，跨文件回归未运行"]))
            print("[缺环境] %-24s" % case["id"])
            continue
        paths = [os.path.join(FIXTURES_DIR, f) for f in case["files"]]
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            failed.append((case["id"], ["样本文件缺失：" + os.path.basename(missing[0])]))
            print("[缺失] %-26s %s" % (case["id"], os.path.basename(missing[0])))
            continue
        res = scr.analyze(paths, case.get("keywords"))
        got = len(res["dangling"])
        exp = case["expect_dangling"]
        if got != exp:
            detail = "、".join(d["ref"] for d in res["dangling"]) or "无"
            failed.append((case["id"], ["悬空数期望 %d 实得 %d（%s）" % (exp, got, detail)]))
            print("[不合规] %-24s 悬空=%d 期望=%d" % (case["id"], got, exp))
        else:
            passed += 1
            print("[通过] %-26s 悬空=%d（跳过 %d 条非引用）" % (
                case["id"], got, res["skipped_non_reference"]))

    print("-" * 60)
    print("棘轮结果：%d/%d 通过" % (passed, total))
    if failed:
        print("结论：未通过——改动破坏了既有能力，必须回滚后再改。")
        return 1
    print("结论：全部通过，既有能力未被破坏，允许保留本轮改动。")
    return 0


def cmd_contract(args):
    """检查 SKILL.md 是否仍保留五项对外契约，防止「自我进化」悄悄改坏接口。"""
    if not os.path.exists(SKILL_MD):
        print("环境错误：找不到 SKILL.md -> %s" % SKILL_MD)
        return 2
    with open(SKILL_MD, encoding="utf-8") as fh:
        text = fh.read()

    print("自我进化 · 契约检查")
    print("目标：" + SKILL_MD)
    print("-" * 60)
    missing = []
    for label, needle in CONTRACT_ITEMS:
        ok = needle in text
        print("[%s] %s" % ("OK" if ok else "缺失", label))
        if not ok:
            missing.append(label)
    print("-" * 60)
    if missing:
        print("结论：契约缺失 %d 项 —— %s" % (len(missing), "、".join(missing)))
        print("这些接口被外部引用（记忆库 · 自定义提示词），缺失即视为接口破坏，必须修复。")
        return 1
    print("结论：%d 项契约全部在位，对外接口未破坏。" % len(CONTRACT_ITEMS))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="产物自审技能的自我进化引擎：留痕统计 / 棘轮回归 / 契约检查。"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true", help="统计历史留痕并给建议")
    group.add_argument("--ratchet", action="store_true", help="跑 fixtures 回归")
    group.add_argument("--contract", action="store_true", help="检查 SKILL.md 契约")
    group.add_argument("--append", action="store_true", help="追加一条自审记录")

    parser.add_argument("--date", help="--append 用：日期 YYYY-MM-DD")
    parser.add_argument("--artifact", help="--append 用：产物名称或路径")
    parser.add_argument("--tier", help="--append 用：档位（轻/常规/重）")
    parser.add_argument("--rounds", type=int, help="--append 用：轮次")
    parser.add_argument("--fail", type=int, help="--append 用：扫描 FAIL 数")
    parser.add_argument("--warn", type=int, help="--append 用：扫描 WARN 数")
    parser.add_argument("--info", type=int, help="--append 用：扫描 INFO 数")
    parser.add_argument("--score", help="--append 用：六关得分")
    parser.add_argument("--verdict", help="--append 用：判定")
    parser.add_argument("--fp", type=int, default=0, help="--append 用：误报次数")
    parser.add_argument("--miss", type=int, default=0, help="--append 用：漏报次数")
    parser.add_argument("--stuck", help="--append 用：卡住的关卡")
    parser.add_argument("--note", help="--append 用：备注")
    args = parser.parse_args()

    if args.report:
        return cmd_report(args)
    if args.ratchet:
        return cmd_ratchet(args)
    if args.contract:
        return cmd_contract(args)
    return cmd_append(args)


if __name__ == "__main__":
    sys.exit(main())
