"""
动态事件数据结构 (问题3).

支持 4 类事件:
    - new_order:       新增订单 (来自原幽灵客户或客户复订单)
    - cancel_order:    取消订单 (从当前解中移除客户)
    - address_change:  地址变更 (修改客户坐标并更新距离矩阵行列)
    - tw_change:       时间窗调整
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Event:
    """单个动态事件的通用形式。"""
    time: float          # 事件发生时刻 (小时, 从 0 点算)
    etype: str           # new_order / cancel_order / address_change / tw_change
    cid: int             # 涉及的客户 ID
    payload: dict = field(default_factory=dict)

    def __repr__(self):
        return f"Event(t={self.time:.2f}, {self.etype}, c{self.cid}, {self.payload})"


# ========= 事件构造辅助 =========

def make_new_order(time: float, cid: int, demand_kg: float, demand_m3: float,
                   tw_start: float, tw_end: float) -> Event:
    return Event(
        time=time,
        etype="new_order",
        cid=cid,
        payload=dict(
            demand_kg=demand_kg,
            demand_m3=demand_m3,
            tw_start=tw_start,
            tw_end=tw_end,
        ),
    )


def make_cancel(time: float, cid: int) -> Event:
    return Event(time=time, etype="cancel_order", cid=cid)


def make_address_change(time: float, cid: int, new_x: float, new_y: float) -> Event:
    return Event(
        time=time,
        etype="address_change",
        cid=cid,
        payload=dict(x=new_x, y=new_y),
    )


def make_tw_change(time: float, cid: int,
                   tw_start: Optional[float] = None,
                   tw_end: Optional[float] = None) -> Event:
    return Event(
        time=time,
        etype="tw_change",
        cid=cid,
        payload=dict(tw_start=tw_start, tw_end=tw_end),
    )


@dataclass
class Scenario:
    """场景: 一组同一个时间点的事件 + 可读名称."""
    name: str
    events: List[Event] = field(default_factory=list)
    description: str = ""
