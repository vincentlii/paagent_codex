"""User-authorized price-grid arithmetic, separate from model-owned proposals."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy

PRICE_FIELDS = ("entry_price", "stop_loss_price", "take_profit_price", "take_profit_price_2")


class ExecutionPricePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["execution_price_grid_v1"] = "execution_price_grid_v1"
    venue: Literal["OKX_DEMO"] = "OKX_DEMO"
    inst_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP", "XAU-USDT-SWAP"]
    tick_size: str | None

    @field_validator("tick_size", mode="before")
    @classmethod
    def validate_tick(cls, value):
        return FixedRiskRewardPolicy(tick_size=value).model_dump(mode="json")["tick_size"]

    def prices(self, decision: dict, take_profit_policy=None) -> dict:
        if self.tick_size is None:
            raise ValueError("execution_price_tick_unavailable")
        if decision.get("order_type") != "限价单":
            raise ValueError("execution_price_order_type_unsupported")
        direction = decision.get("order_direction")
        if direction not in {"做多", "做空"}:
            raise ValueError("execution_price_direction_invalid")
        tick = Decimal(self.tick_size)
        long = direction == "做多"
        with localcontext() as ctx:
            ctx.prec = 64

            def rounded(value, mode):
                if value is None:
                    return None
                if isinstance(value, bool):
                    raise ValueError("execution_price_invalid")
                try:
                    price = Decimal(str(value))
                except InvalidOperation as exc:
                    raise ValueError("execution_price_invalid") from exc
                if not price.is_finite() or not 0 < price <= Decimal("1e30"):
                    raise ValueError("execution_price_invalid")
                return format((price / tick).to_integral_value(rounding=mode) * tick, "f")

            prices = {
                "entry_price": rounded(decision.get("entry_price"), ROUND_FLOOR if long else ROUND_CEILING),
                "stop_loss_price": rounded(decision.get("stop_loss_price"), ROUND_FLOOR if long else ROUND_CEILING),
                "take_profit_price": rounded(decision.get("take_profit_price"), ROUND_FLOOR if long else ROUND_CEILING),
                # TP2 remains research-only; it never sets executable TP1.
                "take_profit_price_2": decision.get("take_profit_price_2"),
            }
            if take_profit_policy is not None:
                policy = FixedRiskRewardPolicy.model_validate(take_profit_policy)
                if policy.tick_size != tick:
                    raise ValueError("execution_price_target_tick_mismatch")
                try:
                    prices["take_profit_price"] = str(policy.target(
                        prices["entry_price"], prices["stop_loss_price"], direction))
                except ValueError as exc:
                    if str(exc) != "fixed_rr_invalid_geometry":
                        raise
                    prices["take_profit_price"] = None  # Collapsed risk is suppressed, never padded.
        return prices

    def adjustment(self, decision: dict, take_profit_policy=None) -> dict:
        return {
            "policy": self.model_dump(mode="json"),
            "take_profit_policy": take_profit_policy.model_dump(mode="json") if take_profit_policy else None,
            "original_prices": {key: decision.get(key) for key in PRICE_FIELDS},
            "effective_prices": self.prices(decision, take_profit_policy),
        }

    def guidance(self):
        return (f"执行价格规则 execution_price_grid_v1：{self.inst_id} tick={self.tick_size or 'unavailable'}。"
                "模型给结构原价；Host仅对限价单生成独立执行价格：买入向下/卖出向上取整，止损向结构外取整，"
                "PA目标向入场方向取整；Entry/SL及PA目标各自调整不足一tick，fixed_rr目标按调整后的风险重算。"
                "不改Setup、方向或概率。"
                "Host按执行价格复算方程，价格重合/几何无效则不执行；缺tick不猜测。")


def frame_price_policy(frame):
    value = (getattr(frame, "data_metadata", {}) or {}).get("execution_price_policy")
    return ExecutionPricePolicy.model_validate(value) if value is not None else None


def adjusted_decision(stage2: dict) -> dict:
    """Recompute the Host projection at every consumer; never trust stored effective prices."""
    decision = stage2.get("decision") or {}
    adjustment = (stage2.get("execution_disposition") or {}).get("price_adjustment")
    if adjustment is None:
        return dict(decision)
    policy = ExecutionPricePolicy.model_validate(adjustment["policy"])
    fixed = adjustment.get("take_profit_policy")
    fixed = FixedRiskRewardPolicy.model_validate(fixed) if fixed is not None else None
    expected = policy.adjustment(decision, fixed)
    if adjustment != expected:
        raise ValueError("execution_price_projection_mismatch")
    return {**decision, **expected["effective_prices"]}
