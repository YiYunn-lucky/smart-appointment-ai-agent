"""
知识库管理API

提供知识条目的增删改查与语义搜索接口（写操作统一审计 + 幂等）
"""
from fastapi import APIRouter, HTTPException, Request
from typing import List, Optional
from pydantic import BaseModel

from services.audit_service import AuditService
from .audit_guard import replay_response, request_client_ip, request_idem_key

router = APIRouter(prefix="/api/knowledge", tags=["知识库管理"])


class KnowledgeItem(BaseModel):
    id: Optional[int] = None
    content: str
    category: str = "general"
    keywords: List[str] = []


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    category: Optional[str] = None


async def _get_knowledge_service():
    """获取已初始化的知识库服务"""
    from services.knowledge_service import KnowledgeService
    knowledge_service = KnowledgeService()
    if not knowledge_service.initialized:
        await knowledge_service.initialize()
    return knowledge_service


@router.get("/")
async def get_all_knowledge():
    """获取所有知识条目"""
    try:
        knowledge_service = await _get_knowledge_service()
        entries = knowledge_service.get_all_documents()

        # 安全获取categories，避免出错
        try:
            categories = knowledge_service.get_all_categories()
        except Exception as e:
            print(f"获取categories失败: {e}")
            categories = []

        return {
            "documents": entries or [],
            "categories": categories or [],
            "total_count": len(entries) if entries else 0,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取知识库失败: {str(e)}")


@router.post("/")
async def add_knowledge(item: KnowledgeItem, http_request: Request):
    """添加新的知识条目（写操作：审计 + 幂等）"""
    audit = AuditService()
    idem_key = request_idem_key(http_request)
    ip = request_client_ip(http_request)
    try:
        replay = audit.find_replay(idem_key)
        if replay:
            return replay_response(replay)

        knowledge_service = await _get_knowledge_service()
        doc_id = await knowledge_service.add_document(
            content=item.content,
            category=item.category,
            keywords=item.keywords
        )
        if not doc_id:
            raise HTTPException(status_code=500, detail="知识条目添加失败")
        audit.record(actor='operator', scene='knowledge_management', action='add_knowledge',
                     resource_type='knowledge_document', resource_id=str(doc_id),
                     risk_tier='write', result='ok',
                     detail=f"分类={item.category} 摘要={str(item.content)[:60]}",
                     idem_key=idem_key, ip=ip)
        return {
            "status": "success",
            "message": "知识条目添加成功",
            "data": {"id": doc_id}
        }
    except HTTPException as he:
        audit.record(actor='operator', scene='knowledge_management', action='add_knowledge',
                     risk_tier='write', result='error', detail=str(he.detail)[:200], ip=ip)
        raise
    except Exception as e:
        audit.record(actor='operator', scene='knowledge_management', action='add_knowledge',
                     risk_tier='write', result='error', detail=str(e)[:200], ip=ip)
        raise HTTPException(status_code=500, detail=f"添加知识条目失败: {str(e)}")


@router.get("/{knowledge_id}")
async def get_knowledge(knowledge_id: int):
    """获取特定知识条目"""
    try:
        knowledge_service = await _get_knowledge_service()
        entry = knowledge_service.get_document(knowledge_id)
        if not entry:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return {
            "status": "success",
            "data": entry
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取知识条目失败: {str(e)}")


@router.put("/{knowledge_id}")
async def update_knowledge(knowledge_id: int, item: KnowledgeItem, http_request: Request):
    """更新知识条目（写操作：审计 + 幂等；内容变化时自动重建向量）"""
    audit = AuditService()
    idem_key = request_idem_key(http_request)
    ip = request_client_ip(http_request)
    try:
        replay = audit.find_replay(idem_key)
        if replay:
            return replay_response(replay)

        knowledge_service = await _get_knowledge_service()
        result = await knowledge_service.update_document(
            doc_id=knowledge_id,
            content=item.content,
            category=item.category,
            keywords=item.keywords
        )
        if not result:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        audit.record(actor='operator', scene='knowledge_management', action='update_knowledge',
                     resource_type='knowledge_document', resource_id=str(knowledge_id),
                     risk_tier='write', result='ok',
                     detail=f"分类={item.category} 摘要={str(item.content)[:60]}",
                     idem_key=idem_key, ip=ip)
        return {
            "status": "success",
            "message": "知识条目更新成功",
            "data": result
        }
    except HTTPException as he:
        audit.record(actor='operator', scene='knowledge_management', action='update_knowledge',
                     resource_type='knowledge_document', resource_id=str(knowledge_id),
                     risk_tier='write', result='error', detail=str(he.detail)[:200], ip=ip)
        raise
    except Exception as e:
        audit.record(actor='operator', scene='knowledge_management', action='update_knowledge',
                     resource_type='knowledge_document', resource_id=str(knowledge_id),
                     risk_tier='write', result='error', detail=str(e)[:200], ip=ip)
        raise HTTPException(status_code=500, detail=f"更新知识条目失败: {str(e)}")


@router.delete("/{knowledge_id}")
async def delete_knowledge(knowledge_id: int, http_request: Request):
    """删除知识条目（写操作：审计 + 幂等；软删除）"""
    audit = AuditService()
    idem_key = request_idem_key(http_request)
    ip = request_client_ip(http_request)
    try:
        replay = audit.find_replay(idem_key)
        if replay:
            return replay_response(replay)

        knowledge_service = await _get_knowledge_service()
        result = await knowledge_service.delete_document(knowledge_id)
        if not result:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        audit.record(actor='operator', scene='knowledge_management', action='delete_knowledge',
                     resource_type='knowledge_document', resource_id=str(knowledge_id),
                     risk_tier='write', result='ok', detail="软删除", idem_key=idem_key, ip=ip)
        return {
            "status": "success",
            "message": "知识条目删除成功"
        }
    except HTTPException as he:
        audit.record(actor='operator', scene='knowledge_management', action='delete_knowledge',
                     resource_type='knowledge_document', resource_id=str(knowledge_id),
                     risk_tier='write', result='error', detail=str(he.detail)[:200], ip=ip)
        raise
    except Exception as e:
        audit.record(actor='operator', scene='knowledge_management', action='delete_knowledge',
                     resource_type='knowledge_document', resource_id=str(knowledge_id),
                     risk_tier='write', result='error', detail=str(e)[:200], ip=ip)
        raise HTTPException(status_code=500, detail=f"删除知识条目失败: {str(e)}")


@router.post("/search")
async def search_knowledge(request: SearchRequest):
    """搜索知识库"""
    try:
        knowledge_service = await _get_knowledge_service()
        results = await knowledge_service.search(
            request.query, top_k=request.top_k, category=request.category
        )
        results = results or []
        return {
            "status": "success",
            "query": request.query,
            "results": results,
            "total_found": len(results)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索知识库失败: {str(e)}")
