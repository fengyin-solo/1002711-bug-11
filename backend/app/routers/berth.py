"""泊位计划接口：维护泊位，覆盖分配靠泊、释放泊位、登记维护等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.berth import BerthService

router = APIRouter(prefix="/api/berth", tags=["泊位计划"])

service = BerthService()

LIST_FIELDS = ["泊位编号", "泊位长度", "水深条件", "可停吨位", "靠泊时段", "离泊时段", "靠泊船名", "泊位状态"]
STATUSES = ["空闲", "已靠泊", "维护中", "不可用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按泊位编号检索"),
    status: str | None = Query(default=None, description="空闲、已靠泊、维护中、不可用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按泊位编号与状态过滤泊位计划列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/overview")
def berth_overview() -> dict[str, Any]:
    """泊位总览：占用数随分配明细实时重算，与分配明细页共用同一份口径。"""
    return service.overview()


# 注意：/export 必须排在 /{entry_id} 之前，否则会被当成泊位 id 匹配
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出泊位计划清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "berth", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条泊位明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"泊位 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条泊位，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="泊位已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条泊位执行分配靠泊、释放泊位、登记维护；不允许的动作会被拦下并说明原因。

    排靠泊所需的船名、吃水、时段等随 values 提交；服务端校验失败时原文回传说明，
    且不会改动泊位原有占用。
    """
    action = str(payload.values.get("action") or "").strip()
    values = {key: value for key, value in payload.values.items() if key != "action"}
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
