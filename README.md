# artifact-self-audit · 产物自审官

> AI agent 交付前的六关自审技能：让 agent 在把产物交给你之前，自己先挑一遍毛病。

An agent skill for pre-delivery self-audit: deterministic artifact scanning + a six-gate review, so an AI agent fixes problems *before* showing you the result.

## 这是什么

AI agent 产出的东西（文档、代码、表格、网页、方案清单……）在交付给用户之前，先用**确定性脚本扫描**找硬伤，再按**六关**（需求对齐 / 事实核验 / 完整性 / 格式可读性 / 交付方式 / 风险合规）逐条审查；不合格就自己修，修完**从头重审**，四轮仍不达标就停下上报而不是降标准放行。

一句话：**没查过不交付，没复查不交付。**

## 功能

- **确定性扫描**（`scripts/scan_artifact.py`，只读）：
  - 占位符与待办残留、空章节、编码乱码（按占比分级）、断链引用、命名规范
  - JSON / YAML **语法合法性**校验（解析失败即 FAIL，给行列位置）
  - 二进制文档魔数识别（ZIP/PDF/OLE 头校验），可挡住改名伪装的文件
  - 代码围栏降噪、Markdown 待办清单白名单等误报抑制
  - 支持 `--json` 机器可读输出；传目录即批量扫描
- **跨文件检查**（`scripts/scan_cross_refs.py`，只读）：
  - 引用可达性：Markdown「」章节引用是否悬空，带多层噪声过滤
  - 同主题表述对照：多文件原句并排，供人工裁决口径矛盾
- **六关审查 + 档位门槛**：轻 / 常规 / 重三档，档位由交付对象决定；每关 16.67 分，总分决定可交付 / 需修复 / 禁止交付
- **自审卡**：输出带可定位证据（章节名、行号、原句摘录）的审查记录，拒绝"看起来没问题"
- **自我进化（提案制，三环）**：
  1. 留痕：每次自审追加一行流水（`evolve.py --append`）
  2. 诊断：统计得分趋势 / 误报率 / 高频卡点，给进化建议（`--report`）
  3. 棘轮：15 个固定坏样本回归，全绿才允许保留对技能本体的改动（`--ratchet`）
  - **只出提案、绝不自动修改技能本体**——自审技能改自己等于自己给自己发合格证

## 安装

把整个目录放进你的 agent 技能目录即可，无第三方依赖（Python ≥ 3.8 标准库）：

```bash
# WorkBuddy / 类似 agent 的技能目录
cp -r artifact-self-audit ~/.workbuddy/skills/

# 或任意位置，按绝对路径调用脚本
python /path/to/artifact-self-audit/scripts/scan_artifact.py <产物路径>
```

## 用法

### 单产物扫描

```bash
python scripts/scan_artifact.py report.md          # 单文件
python scripts/scan_artifact.py ./output/          # 目录批量
python scripts/scan_artifact.py config.json --json # 机器可读输出
```

### 跨文件检查

```bash
# --files 必传：你的记忆文件 / 技能文件 / 配置文件等载体
python scripts/scan_cross_refs.py --files MEMORY.md SKILL.md app-config.json \
  --keywords 版本号 自审
```

### 自我进化

```bash
python scripts/evolve.py --append --date 2026-11-24 --artifact "报告" \
  --tier 常规 --rounds 1 --fail 0 --warn 1 --info 0 --score 100 --verdict 可交付 \
  --fp 0 --miss 0 --stuck "" --note "备注"
python scripts/evolve.py --report    # 诊断
python scripts/evolve.py --ratchet   # 15 样本回归
python scripts/evolve.py --contract  # 契约完整性检查
```

## 六关

| 关卡 | 查什么 |
| :-- | :-- |
| 1 需求对齐 | 原始需求是否逐条覆盖，有无漏项、擅自加戏 |
| 2 事实核验 | 数据 / 尺寸 / 参数 / 引用 / 结论能否溯源 |
| 3 完整性 | 占位符残留、空章节、断链引用、文件能否打开 |
| 4 格式与可读性 | 命名规范、单位一致、标题层级、乱码 |
| 5 交付方式 | 是否落在约定目录、只展示最新产物 |
| 6 风险与合规 | 个人文件先确认、密钥与证件号、版权与学术诚信 |

## 术语适配

本技能源自作者的中文 agent 环境，正文中的「记忆库」「自定义提示词」「阅读版」等词指作者本地环境的记忆文件与配置文件，请替换为你自己环境中的对应物——详见 `SKILL.md` 的「术语适配」一节。`scan_cross_refs.py` 不内置任何文件路径，一律用 `--files` 传入。

## 目录结构

```
artifact-self-audit/
├── SKILL.md                  # 技能本体（给 agent 读的完整指令）
├── CHANGELOG.md              # 版本历史（v1.0.0 起）
├── references/
│   ├── audit-rules.md        # 六关 PASS 底线、熔断分级、降级矩阵
│   └── evolution.md          # 自我进化三环的完整规则
├── scripts/
│   ├── scan_artifact.py      # 单产物确定性扫描（757 行）
│   ├── scan_cross_refs.py    # 跨文件引用可达性 + 表述对照
│   └── evolve.py             # 留痕 / 诊断 / 棘轮 / 契约
└── fixtures/                 # 15 个棘轮回归样本（含 5 个故意做坏的）
```

## 版本

当前 v1.5.1，版本历史见 [CHANGELOG.md](CHANGELOG.md)。改动契约红线（六关名称与顺序、三档门槛、标记格式、脚本 CLI）须走 major 并跑双验证（`--ratchet` + `--contract`）。

## License

MIT
