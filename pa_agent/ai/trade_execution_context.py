"""Frozen execution facts for Stage 2; no market interpretation or model authority."""
from __future__ import annotations

import json
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Instrument = Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP", "XAU-USDT-SWAP"]
Timestamp = Annotated[int, Field(strict=True, gt=0)]
Rate = Annotated[Decimal, Field(ge=-1, le=1, allow_inf_nan=False)]


class FeeSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    inst_id: Instrument
    maker: Rate
    taker: Rate
    observed_at_ms: Timestamp

    @field_validator("maker", "taker", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("fee_rate_boolean")
        return value

    @field_validator("maker", "taker")
    @classmethod
    def canonical_rate(cls, value):
        return value.normalize()

    @property
    def commission_rate(self):
        # OKX negative means commission, positive means rebate. A plain limit
        # may cross the book; neither maker fills nor rebates are guaranteed.
        return max(Decimal(0), -self.maker, -self.taker)


class TradeExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol_version: Literal["trade_execution_context_v1"] = "trade_execution_context_v1"
    inst_id: Instrument
    source_close_ms: Timestamp
    limit_validity_hours: Annotated[int, Field(strict=True, ge=1, le=24)]
    expires_at_ms: Timestamp
    frozen_at_ms: Timestamp
    fee_schedule: FeeSchedule | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.expires_at_ms != self.source_close_ms + self.limit_validity_hours * 3_600_000:
            raise ValueError("trade_context_expiry_mismatch")
        if self.frozen_at_ms < self.source_close_ms:
            raise ValueError("trade_context_before_close")
        if self.fee_schedule is not None and (
            self.fee_schedule.inst_id != self.inst_id
            or not 0 <= self.frozen_at_ms - self.fee_schedule.observed_at_ms <= 300_000
        ):
            raise ValueError("trade_context_fee_identity_or_freshness")
        return self

    def identity(self):
        """Rates/TTL change treatment; refreshing the same rate is not a new sample."""
        value = self.model_dump(mode="json", exclude={"frozen_at_ms"})
        if value["fee_schedule"] is not None:
            value["fee_schedule"].pop("observed_at_ms")
        return value

    def treatment(self):
        value = self.identity()
        for key in ("source_close_ms", "expires_at_ms"):
            value.pop(key)
        return value

    def commission_check(self, decision):
        if self.fee_schedule is None:
            return {"passed": False, "status": "unavailable", "scope": "commission_only",
                    "uncovered": ["slippage", "funding"]}
        with localcontext() as ctx:
            ctx.prec = 64
            entry, stop, target, p = (Decimal(str(decision[k])) for k in (
                "entry_price", "stop_loss_price", "take_profit_price", "estimated_win_rate"))
            rate = self.fee_schedule.commission_rate
            risk, reward = abs(entry - stop), abs(target - entry)
            win_fee, loss_fee = rate * (entry + target), rate * (entry + stop)
            net_reward, net_risk = reward - win_fee, risk + loss_fee
            expectancy = (p * net_reward - (100 - p) * net_risk) / 100
            denominator = net_risk + net_reward
            breakeven = str(net_risk / denominator * 100) if denominator > 0 else None
            return {"passed": net_reward > 0 and expectancy > 0, "status": "available",
                    "scope": "commission_only", "uncovered": ["slippage", "funding"],
                    "rate_assumption": "max_commission_no_rebate_both_legs",
                    "breakeven_win_rate": breakeven,
                    **{k: str(v) for k, v in dict(rate=rate, win_fee=win_fee, loss_fee=loss_fee,
                       gross_risk=risk, gross_reward=reward,
                       gross_expectancy=(p*reward-(100-p)*risk)/100,
                       win_fee_risk_multiple=win_fee/risk, loss_fee_risk_multiple=loss_fee/risk,
                       net_reward=net_reward, net_risk=net_risk, net_expectancy=expectancy,
                       ).items()}}

    def guidance(self):
        return ("冻结交易约束（仅本轮Stage 2，时间为UTC epoch毫秒）："
                + json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
                + "。有效期从source_close_ms起算，只撤未成交余量，不是持仓时限；分析交付也消耗可挂单时间。"
                "在已有entry_reachability中判断本期限内的路径及反证，区分触达可能性与成交后TP先于SL的概率，"
                "不编造精确成交率，不强迫每次有候选。当前Demo只执行普通限价；不为适配执行器强造限价。"
                "Host保留毛方程，并按两边较高收费、不计返佣复算赢/亏两种佣金后的方程；"
                "费率不可用时保留分析但不执行新候选。滑点与资金费未知，佣金后通过不等于总成本后必有优势。"
                "佣金检查由Host执行，不必为费用另造wait理由；按证据给出原始候选与未校准概率，"
                "不得为了通过净方程扩大结构SL、目标或上调概率；不改变旧单、金额或本轮到期时间。")


def frame_trade_context(frame):
    value = (getattr(frame, "data_metadata", {}) or {}).get("trade_execution_context")
    if value is None:
        return None
    context = TradeExecutionContext.model_validate(value)
    from pa_agent.execution.demo_contracts import MAPPINGS
    if (getattr(frame, "timeframe", None) != "1h"
        or MAPPINGS.get(getattr(frame, "symbol", None)) != context.inst_id
        or not frame.bars
        or int(frame.bars[0].ts_open) + 3_600_000 != context.source_close_ms):
        raise ValueError("trade_context_frame_mismatch")
    return context
