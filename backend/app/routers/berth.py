"""泊位计划接口：维护泊位，覆盖分配靠泊、释放泊位、登记维护与占用看板。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.berth import service

router = APIRouter(prefix="/api/berth", tags=["泊位计划"])

STATUSES = ["空闲", "已靠泊", "维护中", "不可用"]


class AllocationPayload(BaseModel):
    """分配靠泊提交体：船舶资料 + 时段 + 幂等令牌。"""

    values: dict[str, Any]
    request_id: str | None = None


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


@router.get("/occupancy")
def occupancy() -> dict[str, Any]:
    """泊位占用看板：占用随分配明细实时重算，与列表/明细/概览同一口径。"""
    return service.occupancy_summary()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出泊位计划清单：返回当前全量明细（含派生占用）。"""
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
def run_action(entry_id: int, payload: AllocationPayload) -> ActionResult:
    """对单条泊位执行分配靠泊、释放泊位、登记维护。

    服务端校验不通过时原样返回中文说明；分配失败不动占用、不删已排时段。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message, deduped = service.run_action(
        entry_id, action, payload.values, request_id=payload.request_id
    )
    if entry is None:
        return ActionResult(ok=False, message=message)
    if deduped:
        return ActionResult(ok=True, message=f"（重复提交已忽略）{message}", entry=entry)
    return ActionResult(ok=True, message=message, entry=entry)
