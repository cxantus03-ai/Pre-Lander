from decimal import Decimal

from app.core.arbitrage import Leg
from app.core.domain import FeeKind, FeeSpec, PriceLevel, Side

ZERO_FEE = FeeSpec(FeeKind.ZERO, verified=True, source="test")
KALSHI_FEE = FeeSpec(FeeKind.KALSHI_QUADRATIC, rate=Decimal("0.07"), verified=True, source="test")


def lv(*pairs):
    return [PriceLevel(Decimal(str(p)), Decimal(str(s))) for p, s in pairs]


def leg(exchange, side, levels, fee=ZERO_FEE, inc="1", min_size="1", label=""):
    return Leg(
        leg_id="",
        exchange=exchange,
        market_key=f"{exchange}:m",
        side=Side(side),
        asks=lv(*levels),
        fee=fee,
        qty_increment=Decimal(inc),
        min_order_size=Decimal(min_size),
        label=label or f"{exchange} {side}",
    )
