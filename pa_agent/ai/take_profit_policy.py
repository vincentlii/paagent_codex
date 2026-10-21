"""Frozen, deterministic fixed-R target policy; no market interpretation."""

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

RewardRiskRatio = Literal["1", "1.5", "2", "2.5"]


class FixedRiskRewardPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    protocol_version: Literal["fixed_rr_v1"] = "fixed_rr_v1"
    reward_risk_ratio: RewardRiskRatio = "1.5"
    tick_size: Annotated[Decimal, Field(gt=0, le=1_000_000_000, max_digits=30, decimal_places=18)] | None = None
    rounding: Literal["toward_entry"] = "toward_entry"
    target_origin: Literal["program_fixed_rr"] = "program_fixed_rr"

    @field_validator("tick_size", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("fixed_rr_invalid_tick")
        return value

    @field_serializer("tick_size")
    def serialize_tick(self, value):
        return format(value.normalize(), "f") if value is not None else None

    def target(self, entry, stop, direction) -> Decimal:
        if self.tick_size is None:
            raise ValueError("fixed_rr_target_tick_unavailable")
        if direction not in {"做多", "做空"} or isinstance(entry, bool) or isinstance(stop, bool):
            raise ValueError("fixed_rr_invalid_geometry")
        try:
            entry, stop = Decimal(str(entry)), Decimal(str(stop))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("fixed_rr_invalid_geometry") from exc
        if not all(v.is_finite() and 0 < v <= Decimal("1e30") for v in (entry, stop)):
            raise ValueError("fixed_rr_invalid_geometry")
        long = direction == "做多"
        with localcontext() as context:
            context.prec = 64
            risk = entry - stop if long else stop - entry
            if risk <= 0:
                raise ValueError("fixed_rr_invalid_geometry")
            reward = risk * Decimal(self.reward_risk_ratio)
            raw = entry + reward if long else entry - reward
            target = (raw / self.tick_size).to_integral_value(
                rounding=ROUND_FLOOR if long else ROUND_CEILING
            ) * self.tick_size
        if target <= 0 or (target <= entry if long else target >= entry):
            raise ValueError("fixed_rr_invalid_geometry")
        return target

    def guidance(self) -> str:
        tick = self.model_dump(mode="json")["tick_size"]
        return (
            "本轮退出协议 fixed_rr_v1：固定盈亏比，止损:止盈=1:"
            f"{self.reward_risk_ratio}。模型负责 Setup、entry、结构 stop、是否交易及主观胜率；"
            "不输出 take_profit_price / take_profit_price_2。Host 根据计划入场价计算 R=abs(entry-stop)，"
            "做多 TP=entry+k*R，做空 TP=entry-k*R，再向入场方向取整到冻结目标交易所 tick "
            f"{tick if tick is not None else 'unavailable'}；最终 TP2=null。"
            "estimated_win_rate 必须独立评估‘计划入场成交后，该固定目标先于结构止损达到’的概率，"
            "不是挂单成交概率、盈亏平衡概率或原结构目标的胜率。它仍是未校准的主观先验。"
            "教材中的结构目标用于分析路径障碍与机会，不要求另找结构 TP 覆盖本轮固定目标；"
            "不得为凑倍数修改结构止损或入场位置。保留反证，可以等待或不交易，不强制开单。"
            "真实 tick 未交付时不得猜测，Host 将拒绝可执行候选；合法 No Trade 不受影响。"
        )


def resolve_take_profit_policy(profile, *, tick_size=None) -> FixedRiskRewardPolicy | None:
    """Absent/legacy profile fields always mean the original PA target policy."""
    if profile.take_profit_mode == "pa":
        return None
    return FixedRiskRewardPolicy(
        reward_risk_ratio=profile.fixed_reward_risk_ratio, tick_size=tick_size
    )
