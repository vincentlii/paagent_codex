"""Explicit Stage-1 pattern tags used by the Brooks knowledge router."""
from __future__ import annotations

from typing import Any

STAGE1_DETECTED_PATTERNS_GUIDE = """
## detected_patterns 判定表（由 LLM 结合上下文填写）

`detected_patterns` 只承载阶段一已经明确识别的形态 key。程序只做小写化和去重，
不会从 `cycle_position`、`entry_setup_type`、关键词或数值阈值推断或补写标签。
证据不足时填 `[]`；先描述事实与位置，再使用形态名称。

| key | 识别重点 | 阶段二加载 |
|-----|----------|------------|
| wedge | 三次推进、幅度/斜率变化、位置与后续反应 | 文件14 |
| reversal_attempt / mtr | 反转尝试或主要趋势反转组件 | 文件15、文件25 |
| final_flag | 趋势末段旗形及失败风险 | 文件24 |
| h1 / h2 / l1 / l2 | Brooks 计数入场结构 | 文件19 |
| breakout_test / breakout_pullback | 越界后的回测与再次延续 | 文件18 |
| breakout_failure / failed_breakout | 越界后缺乏接受并回到结构内 | 文件18、文件22 |
| always_in / ail / ais / 20gb / gap_bar | Always In 与均线缺口背景 | 文件20 |
| barbwire / wire / overlap / middle_range | 高重叠或区间中部风险 | 文件21 |
| failed_signal / magnet / trapped_traders | 信号失败与磁力效应 | 文件22 |
| ascending_triangle / descending_triangle / symmetrical_triangle / expanding_triangle | 三角形或收敛/扩张结构 | 文件27 |
| double_top_bottom | 双顶底及微型双顶底 | 文件28 |

阶段二常驻文件23（Measured Move）。Router 只依据本字段及阶段一明确输出的周期/方向加载知识，
不生成第二套交易判断。
""".strip()

STAGE1_PATTERN_BRIEFS_BLOCK = """
## 特殊形态阶段一速查

**Evidence first, label last。** 先说明 K 线、位置、测试与后续接受/拒绝，再决定是否使用标签。

- **wedge**：三推只是必要线索；还需比较推进幅度、斜率、重叠、所在背景及第三推后的反应。
- **breakout_test / breakout_failure**：越过价位不等于有效突破；必须区分越界事实与后续接受/收回。
- **reversal_attempt / mtr**：反转组件可共存，但第一次反转尝试不自动成为可交易 MTR。
- **h1/h2/l1/l2**：计数依赖当前回撤腿和趋势背景，不能仅凭单根高低点机械编号。
- **barbwire / overlap / middle_range**：重叠比例只是事实；是否构成铁丝网取决于持续性、位置和可交易空间。
- **always_in / 20gb**：需要综合连续压力、回撤质量和市场对价格的接受，不由 EMA 一侧占比单独决定。
""".strip()


def normalize_detected_patterns(value: Any) -> list[str]:
    """Normalize only tags explicitly supplied by the model."""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        key = str(item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result
