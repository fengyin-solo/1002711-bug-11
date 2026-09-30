"""泊位计划业务规则：字段补齐、排靠泊校验、失败回滚、占用重算都收在这里。

关键边界：
- 缺字段不静默丢弃：列表用 ``待补充`` 点出缺哪一项，排靠泊缺料直接拦下。
- 水深条件优先：同一艘船在深水/普通泊位之间冲突时，先按吃水判定，再谈时段冲突。
- 排靠泊全程快照：任一步校验失败都恢复原占用，已排的靠泊时段（分配明细）原样保留。
- 同一泊位重复排靠同一艘船按幂等处理，只算第一次。
- 占用数一律从分配明细（allocations 中状态为「生效」的记录）重算，杜绝两处口径不一致。
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta
from typing import Any

from app.store import store

MODULE = "berth"
REQUIRED_FIELDS = ["泊位编号", "泊位长度", "水深条件"]
# 泊位档案上任何时候都应该补齐的字段
BASE_FIELDS = ["泊位编号", "泊位长度", "水深条件", "可停吨位"]
# 排靠泊时必须随动作提交的字段
ALLOCATION_FIELDS = ["靠泊船名", "船舶吃水", "船舶长度", "船舶总吨", "靠泊时段", "离泊时段"]
STATUS_ORDER = ["空闲", "已靠泊", "维护中", "不可用"]
ACTION_RULES = {"分配靠泊": "已靠泊", "释放泊位": "空闲", "登记维护": "维护中"}
NEGATIVE_ACTIONS = []

ALLOCATION_ACTIVE = "生效"
ALLOCATION_RELEASED = "已释放"

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DATETIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


class BerthService:
    # ------------------------------------------------------------------ 读取
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._decorate(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("泊位编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._decorate(entry) if entry is not None else None

    def overview(self) -> dict[str, Any]:
        """泊位总览：占用数完全由分配明细重算，列表页与总览页共用这一份口径。"""
        rows = [self._decorate(row) for row in store.rows(MODULE)]
        occupied = [row for row in rows if row["status"] == "已靠泊"]
        total = len(rows)
        occupancy = round(len(occupied) * 100 / total, 1) if total else 0.0
        return {
            "总数": total,
            "占用数": len(occupied),
            "空闲数": sum(1 for row in rows if row["status"] == "空闲"),
            "维护数": sum(1 for row in rows if row["status"] == "维护中"),
            "不可用数": sum(1 for row in rows if row["status"] == "不可用"),
            "占用率": occupancy,
            "占用明细": [
                {
                    "泊位编号": row.get("泊位编号") or "",
                    "靠泊船名": row.get("靠泊船名") or "",
                    "靠泊时段": row.get("靠泊时段") or "",
                    "离泊时段": row.get("离泊时段") or "",
                }
                for row in occupied
            ],
        }

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if _blank(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in BASE_FIELDS:
            entry[field] = str(values.get(field) or "").strip()
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["allocations"] = []
        rows.append(entry)
        return self._decorate(entry), []

    # ------------------------------------------------------------------ 动作
    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"泊位 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于泊位计划可执行范围"

        snapshot = copy.deepcopy(entry)
        try:
            if action == "分配靠泊":
                result, message = self._allocate(entry, values or {})
            elif action == "释放泊位":
                result, message = self._release(entry)
            else:
                result, message = self._maintain(entry)
        except Exception as exc:  # 任何意外都不能留下半截占用
            entry.clear()
            entry.update(snapshot)
            return None, f"泊位计划处理失败：{exc}"

        if not result:
            # 恢复到动作前的泊位占用，已排好的靠泊时段随快照一并保住
            entry.clear()
            entry.update(snapshot)
            return None, message
        return self._decorate(entry), message

    # ------------------------------------------------------------- 排靠泊核心
    def _allocate(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[bool, str]:
        berth_no = str(entry.get("泊位编号") or entry.get("id"))

        # 1) 必填项先点名：缺哪一项就报哪一项，不留白、不猜值
        missing = [field for field in ALLOCATION_FIELDS if _blank(values.get(field))]
        if missing:
            return False, f"排靠泊资料不完整，缺少：{'、'.join(missing)}"

        vessel_name = str(values["靠泊船名"]).strip()

        # 2) 泊位自身状态先挡：维护/不可用泊位不进入条件校核
        current_status = _canonical_status(entry)
        if current_status == "维护中":
            return False, f"泊位「{berth_no}」正在维护，暂时不能安排靠泊"
        if current_status == "不可用":
            return False, f"泊位「{berth_no}」当前不可用，不能安排靠泊"

        # 3) 时段必须可解析；结束早于开始时按跨天顺延，避免跨天窗口卡死
        start, end, rolled = _parse_window(values["靠泊时段"], values["离泊时段"])
        if start is None:
            return False, str(end)  # end 位置承载错误说明
        start_text = start.strftime("%Y-%m-%d %H:%M")
        end_text = end.strftime("%Y-%m-%d %H:%M")
        cross_note = "（离泊时段已按跨到次日 %s 处理）" % end_text if rolled else ""

        # 4) 水深条件优先判定：泊位自身水深无法识别时也要点出来
        depth, depth_error = _as_number(entry.get("水深条件"), "泊位水深条件")
        draft, draft_error = _as_number(values.get("船舶吃水"), "船舶吃水")
        if depth is None:
            return False, depth_error
        if draft is None:
            return False, draft_error
        if depth < draft:
            return False, (
                f"水深条件不足：泊位「{berth_no}」水深 {_fmt_num(depth)} 米，"
                f"小于「{vessel_name}」吃水 {_fmt_num(draft)} 米，不能安排靠泊"
            )

        # 5) 长度、吨位依次核对
        berth_length, berth_length_error = _as_number(entry.get("泊位长度"), "泊位长度")
        vessel_length, vessel_length_error = _as_number(values.get("船舶长度"), "船舶长度")
        if berth_length is None:
            return False, berth_length_error
        if vessel_length is None:
            return False, vessel_length_error
        if berth_length < vessel_length:
            return False, (
                f"泊位长度不足：泊位「{berth_no}」长度 {_fmt_num(berth_length)} 米，"
                f"小于「{vessel_name}」长度 {_fmt_num(vessel_length)} 米"
            )

        capacity, capacity_error = _as_number(entry.get("可停吨位"), "泊位可停吨位")
        vessel_tonnage, vessel_tonnage_error = _as_number(values.get("船舶总吨"), "船舶总吨")
        if capacity is None:
            return False, capacity_error
        if vessel_tonnage is None:
            return False, vessel_tonnage_error
        if capacity < vessel_tonnage:
            return False, (
                f"可停吨位不足：泊位「{berth_no}」可停 {_fmt_num(capacity)} 吨，"
                f"小于「{vessel_name}」总吨 {_fmt_num(vessel_tonnage)} 吨"
            )

        # 6) 本泊位已占用：同船重复提交按幂等只算第一次，异船直接拦
        active = _active_allocation(entry)
        if active is not None:
            if str(active.get("靠泊船名") or "").strip() == vessel_name:
                return True, f"泊位「{berth_no}」已停靠「{vessel_name}」，同一泊位重复排靠只按第一次提交算，不重复占用"
            return False, (
                f"泊位「{berth_no}」当前已停靠「{active.get('靠泊船名')}」"
                f"（{active.get('靠泊时段')} 至 {active.get('离泊时段')}），需先释放再安排"
            )

        # 7) 同船在其他泊位的排靠时段重叠：水深已在前面判过，这里只谈时间冲突
        overlap = _find_overlap(vessel_name, start, end, exclude_entry=entry)
        if overlap is not None:
            other_no, other = overlap
            return False, (
                f"「{vessel_name}」在泊位「{other_no}」已有 "
                f"{other.get('靠泊时段')} 至 {other.get('离泊时段')} 的排靠，"
                f"与本次 {start_text} 至 {end_text} 时段重叠，不能同时占用两个泊位"
            )

        allocations = entry.setdefault("allocations", [])
        allocations.append({
            "id": max((int(item.get("id", 0)) for item in allocations), default=0) + 1,
            "靠泊船名": vessel_name,
            "船舶吃水": str(values["船舶吃水"]).strip(),
            "船舶长度": str(values["船舶长度"]).strip(),
            "船舶总吨": str(values["船舶总吨"]).strip(),
            "靠泊时段": start_text,
            "离泊时段": end_text,
            "状态": ALLOCATION_ACTIVE,
        })
        entry["status"] = "已靠泊"
        entry["pending"] = True
        entry["abnormal"] = False
        return True, f"「{vessel_name}」已安排到泊位「{berth_no}」{cross_note}"

    def _release(self, entry: dict[str, Any]) -> tuple[bool, str]:
        berth_no = str(entry.get("泊位编号") or entry.get("id"))
        active = _active_allocation(entry)
        if active is None:
            return False, f"泊位「{berth_no}」当前没有在停船舶，无需释放"
        active["状态"] = ALLOCATION_RELEASED
        entry["status"] = "空闲"
        entry["pending"] = True
        entry["abnormal"] = False
        return True, f"泊位「{berth_no}」已释放，「{active.get('靠泊船名')}」离泊"

    def _maintain(self, entry: dict[str, Any]) -> tuple[bool, str]:
        berth_no = str(entry.get("泊位编号") or entry.get("id"))
        active = _active_allocation(entry)
        if active is not None:
            return False, (
                f"泊位「{berth_no}」仍停靠「{active.get('靠泊船名')}」，"
                f"请先释放泊位再登记维护"
            )
        entry["status"] = "维护中"
        entry["pending"] = False
        entry["abnormal"] = False
        return True, f"泊位「{berth_no}」已登记维护"

    # ------------------------------------------------------------- 展示口径
    def _decorate(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把内部记录整理成页面口径：靠泊信息取生效明细，缺字段列进待补充。"""
        view = dict(entry)
        allocations = view.get("allocations")
        if not isinstance(allocations, list):
            allocations = []
            view["allocations"] = []

        active = _active_allocation(view)
        if active is not None:
            view["靠泊船名"] = active.get("靠泊船名")
            view["靠泊时段"] = active.get("靠泊时段")
            view["离泊时段"] = active.get("离泊时段")
        else:
            view.setdefault("靠泊船名", "")
            view.setdefault("靠泊时段", "")
            view.setdefault("离泊时段", "")

        missing = [field for field in BASE_FIELDS if _blank(view.get(field))]
        if active is not None:
            for field in ("靠泊船名", "靠泊时段", "离泊时段"):
                if _blank(view.get(field)) and field not in missing:
                    missing.append(field)
        view["待补充"] = missing
        view["status"] = _canonical_status(view)
        view["泊位状态"] = view["status"]
        return view


# ---------------------------------------------------------------------- 工具
def _blank(value: Any) -> bool:
    return value is None or not str(value).strip()


def _fmt_num(value: float) -> str:
    return ("%.1f" % value).rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def _as_number(value: Any, field: str) -> tuple[float | None, str | None]:
    """从「9.5米」这类写法里取数值；无法识别时返回错误说明，绝不静默当 0。"""
    if _blank(value):
        return None, f"{field}为空，无法完成校核，请先补齐"
    match = _NUMBER_RE.search(str(value))
    if match is None:
        return None, f"{field}「{str(value).strip()}」无法识别为数值，请核对后再提交"
    return abs(float(match.group())), None


def _parse_window(start_raw: Any, end_raw: Any) -> tuple[datetime | None, Any, bool]:
    """解析靠/离泊时段；结束不晚于开始时按跨天逐日顺延。失败时错误说明放在第二位。"""
    start = _parse_datetime(start_raw)
    if start is None:
        return None, f"靠泊时段「{start_raw}」格式无法识别，应形如 2026-09-30 22:00", False
    end = _parse_datetime(end_raw)
    if end is None:
        return None, f"离泊时段「{end_raw}」格式无法识别，应形如 2026-10-01 06:00", False
    rolled = False
    if end <= start:
        # 跨天排靠：结束时间按整天顺延到开始之后（22:00→次日06:00 这类写法）
        while end <= start:
            end += timedelta(days=1)
            rolled = True
    return start, end, rolled


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _active_allocation(entry: dict[str, Any]) -> dict[str, Any] | None:
    allocations = entry.get("allocations")
    if not isinstance(allocations, list):
        return None
    active = [item for item in allocations if item.get("状态") == ALLOCATION_ACTIVE]
    return active[-1] if active else None


def _canonical_status(entry: dict[str, Any]) -> str:
    """占用只认分配明细：有生效明细即「已靠泊」，否则沿用维护/不可用标记，空闲兜底。"""
    if _active_allocation(entry) is not None:
        return "已靠泊"
    status = entry.get("status")
    if status in ("维护中", "不可用"):
        return str(status)
    return "空闲"


def _find_overlap(
    vessel_name: str, start: datetime, end: datetime, *, exclude_entry: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    for row in store.rows(MODULE):
        if row is exclude_entry:
            continue
        for item in row.get("allocations", []) if isinstance(row.get("allocations"), list) else []:
            if item.get("状态") != ALLOCATION_ACTIVE:
                continue
            if str(item.get("靠泊船名") or "").strip() != vessel_name:
                continue
            other_start = _parse_datetime(item.get("靠泊时段"))
            other_end = _parse_datetime(item.get("离泊时段"))
            if other_start is None or other_end is None:
                continue
            if start < other_end and other_start < end:
                return str(row.get("泊位编号") or row.get("id")), item
    return None
