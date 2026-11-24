# fixtures 说明

本目录是**棘轮回归样本**，属技能内部测试数据，不是交付产物——不要拿本目录当交付物去扫描。

样本遵循「该报的必须报，不该报的一个都不许报」构造，其中 5 个是**故意做坏**的，扫描时出现 FAIL 属设计预期：

| 样本 | 扫描结果 | 是否预期 |
| :-- | :-- | :-- |
| `binary/fake.apk` | FAIL：压缩包结构异常 | 预期（故意用文本冒充压缩包） |
| `binary/broken.7z` | FAIL：压缩包结构异常 | 预期（故意写错魔数） |
| `prose/empty_section.md` | FAIL：空章节 | 预期（故意留空章节） |
| `text/garbled.txt` | FAIL：编码或乱码风险 | 预期（故意写满非法字节） |
| `structured/broken.json` | FAIL：JSON 语法错误 | 预期（故意留未转义的裸引号） |

**跨文件样本（`crossrefs/`）**：供 `scan_cross_refs.py` 的回归用，不参与单产物扫描。`target.md` 提供可达目标，`dangling.md` 埋 2 条真断链与 2 类噪声（元描述、强调引号），`clean.md` 全部可达。期望值写在 `expected.json` 的 `xref_cases`，由 `evolve.py --ratchet` 一并执行。

`expected.json` 与 `_generate.py` 中含样本标识符（如 `real-todo-in-source`、`code/todo.py`）。**v1.4.0 起不再误报**：英文标记词（TODO / FIXME / TBD）已加路径与连字符边界，标识符与文件名里的 todo 不再被当作待办；而 `# TODO: 补测试` 这类真待办照旧报出。

期望值见 `expected.json`，样本生成方式见 `_generate.py`，三环机制说明见 `../references/evolution.md`。
