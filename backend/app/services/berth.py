"""泊位计划业务规则：字段完整性、水深/长度/吨位校验、靠泊时段冲突与占用口径都收在这里。

约定：
- 泊位主数据存数值（泊位长度：米，水深条件：米，可停吨位：吨），缺失时为 None。
- 一条泊位可以挂多个已排的「分配明细」（allocations），占用一律按明细重算，
  列表、明细、总览、占用看板读的是同一份口径。
- 分配靠泊采用「先校验、后落库」：任何一条校验不过都不动主数据与已有时段；
  同一泊位同一船同一时段的重复提交只认第一次（幂等）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "berth"
REQUIRED_FIELDS = ["泊位编号", "泊位长度", "水深条件"]
STATUS_ORDER = ["空闲", "已靠泊", "维护中", "不可用"]
ACTION_RULES = {"分配靠泊": "已靠泊", "释放泊位": "空闲", "登记维护": "维护中"}
NEGATIVE_ACTIONS = []

# 展示列：主数据列 + 由分配明细派生的列（前端表格按此顺序渲染）
COLUMN_FIELDS = ["泊位编号", "泊位长度", "水深条件", "泊位类型", "可停吨位",
                 "靠泊船名", "靠泊时段", "离泊时段", "泊位状态"]
DEEP_WATER_DRAFT = 15.0  # 水深达到 15 米视为深水泊位


def _to_float(value: Any) -> float | None:
    """把 '14.5'、'14.5米'、80000 这类写法解析成数值；解析不了视为缺失。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    number = ""
    for char in text:
        if char.isdigit() or char == ".":
            number += char
        elif number:
            break
    try:
        return float(number) if number else None
    except ValueError:
        return None


def _parse_time(value: Any) -> datetime | None:
    """解析靠泊/离泊时点；兼容 'YYYY-MM-DD HH:mm'、'YYYY-MM-DDTHH:mm' 与纯日期。

    跨天时段（结束时点晚于开始时点、落到第二天）在这里天然支持，不做当天截断。
    """
    if value is None:
        return None
    text = str(value).strip().replace("T", " ")
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _format_time(value: Any) -> str:
    moment = _parse_time(value)
    if moment is None:
        return ""
    return moment.strftime("%Y-%m-%d %H:%M")


def _windows_overlap(start_a: datetime, end_a: datetime,
                     start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


class BerthService:
    def __init__(self) -> None:
        # 幂等令牌 -> 已生效的分配结果；只在分配成功后登记
        self._allocation_tokens: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ 读取
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._present(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("泊位编号", ""))]
        if status:
            rows = [row for row in rows if row.get("泊位状态") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._present(row) if row is not None else None

    def occupancy_summary(self) -> dict[str, Any]:
        """占用看板口径：完全由当前泊位主数据与分配明细重算。

        列表、单条明细、导出与运营概览共用本方法，保证两处读到的占用一致。
        """
        rows = store.rows(MODULE)
        presented = [self._present(row) for row in rows]
        occupied = [row for row in presented if row["当前船名"]]
        deep_total = sum(1 for row in presented if row["泊位类型"] == "深水泊位")
        return {
            "泊位总数": len(presented),
            "占用泊位数": len(occupied),
            "空闲泊位数": sum(1 for row in presented if not row["当前船名"]
                          and row["泊位状态"] not in ("维护中", "不可用")),
            "维护泊位数": sum(1 for row in presented if row["泊位状态"] == "维护中"),
            "不可用泊位数": sum(1 for row in presented if row["泊位状态"] == "不可用"),
            "深水泊位数": deep_total,
            "普通泊位数": sum(1 for row in presented if row["泊位类型"] == "普通泊位"),
            "占用率": f"{round(len(occupied) / len(presented) * 100, 1)}%" if presented else "0%",
            "占用明细": [
                {"泊位编号": row["泊位编号"], "靠泊船名": row["当前船名"]}
                for row in occupied
            ],
        }

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        # 泊位编号按非空判；长度/水深按数值判，解析不了（如“待补充”）也算缺失
        missing: list[str] = []
        if not str(values.get("泊位编号") or "").strip():
            missing.append("泊位编号")
        for numeric_field in ("泊位长度", "水深条件"):
            if _to_float(values.get(numeric_field)) is None:
                missing.append(numeric_field)
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry["泊位编号"] = str(values.get("泊位编号")).strip()
        entry["泊位长度"] = _to_float(values.get("泊位长度"))
        entry["水深条件"] = _to_float(values.get("水深条件"))
        entry["可停吨位"] = _to_float(values.get("可停吨位"))
        entry["allocations"] = []
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._present(entry), []

    # ------------------------------------------------------------------ 动作
    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """执行泊位动作。

        返回 (明细, 消息, 是否幂等命中)。任何失败路径都不修改数据——
        即「恢复到原来的泊位占用」，已排好的靠泊时段原样保留。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"泊位 {entry_id} 不存在或已归档", False
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于泊位计划可执行范围", False

        if action == "分配靠泊":
            return self._allocate(entry, values or {}, request_id=request_id)

        if action == "释放泊位":
            allocations = list(entry.get("allocations") or [])
            entry["allocations"] = []
            entry["status"] = "空闲"
            entry["pending"] = True
            entry["abnormal"] = False
            if allocations:
                names = "、".join(dict(item).get("船名", "") for item in allocations)
                return self._present(entry), f"泊位已释放，已清除 {names} 的靠泊安排", False
            return self._present(entry), "泊位已释放", False

        # 登记维护：维护中的泊位保留已排时段，但不再允许新分配
        entry["status"] = "维护中"
        entry["pending"] = False
        entry["abnormal"] = False
        return self._present(entry), "泊位已登记维护，已排靠泊时段保留", False

    # ------------------------------------------------------------------ 分配
    def _allocate(
        self,
        entry: dict[str, Any],
        values: dict[str, Any],
        *,
        request_id: str | None,
    ) -> tuple[dict[str, Any] | None, str, bool]:
        vessel = str(values.get("船名") or values.get("靠泊船名") or "").strip()
        draft = _to_float(values.get("吃水") or values.get("船舶吃水"))
        length = _to_float(values.get("船长") or values.get("船舶长度"))
        tonnage = _to_float(values.get("吨位") or values.get("船舶吨位"))
        start_raw = values.get("靠泊开始") or values.get("靠泊时段")
        end_raw = values.get("离泊结束") or values.get("离泊时段")

        # 幂等：同一泊位 + 同一船 + 同一时段只按第一次提交算
        window_key = f"{_format_time(start_raw)}|{_format_time(end_raw)}"
        for item in entry.get("allocations") or []:
            if item.get("船名") == vessel and item.get("window_key") == window_key and vessel:
                return self._present(entry), f"该靠泊安排已提交过，按首次提交生效（{vessel}）", True
        if request_id and request_id in self._allocation_tokens:
            snapshot = self._allocation_tokens[request_id]
            if int(snapshot.get("泊位ID", -1)) == int(entry.get("id", 0)):
                return self._present(entry), f"该靠泊安排已提交过，按首次提交生效（{vessel}）", True

        # 1) 基础状态
        if entry.get("status") in ("维护中", "不可用"):
            return None, f"泊位 {entry.get('泊位编号')} 当前为{entry.get('status')}，不可分配靠泊", False

        # 2) 必填信息（缺哪项点明哪项）
        missing: list[str] = []
        if not vessel:
            missing.append("靠泊船名")
        if draft is None:
            missing.append("船舶吃水")
        start_at = _parse_time(start_raw)
        end_at = _parse_time(end_raw)
        if start_at is None:
            missing.append("靠泊开始时间")
        if end_at is None:
            missing.append("离泊结束时间")
        if missing:
            return None, "分配信息不完整，缺少：" + "、".join(missing), False
        if start_at is not None and end_at is not None and end_at <= start_at:
            return None, "离泊结束时间必须晚于靠泊开始时间，请检查是否跨天填反", False

        # 3) 深水 / 普通泊位冲突：水深条件先判
        berth_depth = _to_float(entry.get("水深条件"))
        is_deep = berth_depth is not None and berth_depth >= DEEP_WATER_DRAFT
        if is_deep and draft is not None and draft < 5.0:
            return None, (
                f"{entry.get('泊位编号')} 为深水泊位（水深 {berth_depth:g} 米），"
                f"该船吃水仅 {draft:g} 米，应安排普通泊位，深水泊位不接纳浅水船舶"
            ), False
        if not is_deep:
            if berth_depth is None:
                return None, f"泊位 {entry.get('泊位编号')} 水深条件待补充，无法判定是否满足靠泊", False
            if draft is not None and draft > berth_depth:
                return None, (
                    f"水深条件不足：船舶吃水 {draft:g} 米，"
                    f"泊位 {entry.get('泊位编号')} 水深仅 {berth_depth:g} 米，差 {draft - berth_depth:g} 米"
                ), False

        # 4) 长度 / 吨位
        berth_length = _to_float(entry.get("泊位长度"))
        if length is not None and berth_length is not None and length > berth_length:
            return None, (
                f"泊位长度不足：船舶长度 {length:g} 米，泊位 {entry.get('泊位编号')} "
                f"长度仅 {berth_length:g} 米，差 {length - berth_length:g} 米"
            ), False
        capacity = _to_float(entry.get("可停吨位"))
        if tonnage is not None and capacity is not None and tonnage > capacity:
            return None, (
                f"可停吨位不足：船舶吨位 {tonnage:g} 吨，泊位 {entry.get('泊位编号')} "
                f"仅可停 {capacity:g} 吨，差 {tonnage - capacity:g} 吨"
            ), False

        # 5) 时段冲突（支持跨天，按起止时点重叠判定）
        assert start_at is not None and end_at is not None
        for item in entry.get("allocations") or []:
            other_start = _parse_time(item.get("靠泊开始"))
            other_end = _parse_time(item.get("离泊结束"))
            if other_start and other_end and _windows_overlap(start_at, end_at, other_start, other_end):
                return None, (
                    f"靠泊时段冲突：{entry.get('泊位编号')} 在 "
                    f"{_format_time(other_start)} ~ {_format_time(other_end)} "
                    f"已安排给 {item.get('船名')}，请改排其他时段"
                ), False

        # 全部通过后才落库：此前任何 return 都未改过数据
        allocation = {
            "船名": vessel,
            "吃水": draft,
            "靠泊开始": start_at.strftime("%Y-%m-%d %H:%M"),
            "离泊结束": end_at.strftime("%Y-%m-%d %H:%M"),
            "window_key": window_key,
        }
        entry.setdefault("allocations", []).append(dict(allocation))
        entry["status"] = "已靠泊"
        entry["pending"] = True
        entry["abnormal"] = False
        if request_id:
            self._allocation_tokens[request_id] = {"泊位ID": entry.get("id"), **allocation}
        return self._present(entry), f"靠泊分配成功：{vessel} 已安排到 {entry.get('泊位编号')}", False

    # ------------------------------------------------------------------ 派生
    def _present(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把存储行转成页面明细：缺失字段显示「待补充」并点明缺项，占用按明细派生。"""
        allocations = list(entry.get("allocations") or [])
        latest = allocations[-1] if allocations else None

        length = _to_float(entry.get("泊位长度"))
        depth = _to_float(entry.get("水深条件"))
        tonnage = _to_float(entry.get("可停吨位"))

        missing: list[str] = []
        if not str(entry.get("泊位编号") or "").strip():
            missing.append("泊位编号")
        if length is None:
            missing.append("泊位长度")
        if depth is None:
            missing.append("水深条件")
        if tonnage is None:
            missing.append("可停吨位")

        berth_type = "待补充"
        if depth is not None:
            berth_type = "深水泊位" if depth >= DEEP_WATER_DRAFT else "普通泊位"

        raw_status = entry.get("status") or "空闲"
        if raw_status in ("维护中", "不可用"):
            display_status = raw_status
        elif latest:
            display_status = "已靠泊"
        else:
            display_status = "空闲"

        return {
            "id": entry.get("id"),
            "泊位编号": str(entry.get("泊位编号") or "").strip() or "待补充",
            "泊位长度": f"{length:g} 米" if length is not None else "待补充",
            "水深条件": f"{depth:g} 米" if depth is not None else "待补充",
            "可停吨位": f"{tonnage:g} 吨" if tonnage is not None else "待补充",
            "泊位类型": berth_type,
            "靠泊时段": _format_time(latest.get("靠泊开始")) if latest else "待补充",
            "离泊时段": _format_time(latest.get("离泊结束")) if latest else "待补充",
            "靠泊船名": latest.get("船名") if latest else "待补充",
            "泊位状态": display_status,
            # 供页面做精细展示
            "缺失字段": missing,
            "待补充": bool(missing),
            "当前船名": latest.get("船名") if latest else "",
            "分配明细": [dict(item) for item in allocations],
            "分配数量": len(allocations),
            "status": display_status,
            "pending": bool(entry.get("pending")),
            "abnormal": bool(entry.get("abnormal")),
        }


service = BerthService()
