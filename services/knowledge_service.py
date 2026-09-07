# services/knowledge_service.py

import numpy as np
import faiss
from typing import List, Dict
from db.db_router import DatabaseRouter
from .text_embedding import embed_input
import logging

logger = logging.getLogger(__name__)


def _rag_mcp_enabled() -> bool:
    """外部 RAG MCP 开关（延迟导入避免加重 knowledge_service 依赖）"""
    try:
        from services.mcp_rag_client import is_rag_mcp_enabled

        return is_rag_mcp_enabled()
    except Exception as e:
        logger.debug(f"读取 RAG MCP 开关失败（按关闭处理）: {e}")
        return False


class KnowledgeService:
    """知识库服务类 - 结合数据库存储和向量检索"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        # 使用统一的DatabaseRouter，符合架构设计
        self.db_router = DatabaseRouter(db_path)
        self.db = self.db_router.knowledge  # 通过router访问knowledge repository
        self.index = None
        self.document_ids = []  # 维护文档ID与索引位置的映射
        self.initialized = False
        
        # 默认知识库内容（安居家电售后知识）
        self.default_knowledge = [
            {
                "content": "安居家电提供空调、冰箱、洗衣机、热水器、净水器、油烟机燃气灶六大品类的上门维修与安装服务。售后工程师上门服务时间为每天上午9点到下午6点。报修可通过在线客服预约，工程师上门前会电话确认。",
                "category": "品牌服务",
                "keywords": ["安居家电", "服务范围", "上门维修", "报修", "品类", "服务时间"]
            },
            {
                "content": "保修政策：整机保修期为一年，主要部件（压缩机、电机等）保修三年。保修期内非人为损坏的故障可免费上门维修，免收上门费和维修费。人为损坏或私自拆修导致的问题不在保修范围内，需按收费标准付费维修。",
                "category": "保修政策",
                "keywords": ["保修", "保修期", "保修范围", "免费", "主要部件", "人为损坏"]
            },
            {
                "content": "超过保修期的产品提供付费维修服务，收费标准为：上门检测费50元/次；维修费按故障类型收取80-300元不等；需要更换的配件按配件价目表收费。维修完成提供90天维修质保，同一故障90天内复发免费返修。",
                "category": "收费标准",
                "keywords": ["收费", "价格", "上门费", "维修费", "配件", "质保", "超保"]
            },
            {
                "content": "空调常见故障排查：1)空调不制冷，先检查滤网是否积尘、模式是否为制冷、温度设置是否过低；2)不制热请确认电辅热已开启；3)漏水多为排水管堵塞或安装不平；4)异响可能是风叶松动或有异物。若断电重启后仍无法解决，请预约工程师上门检修。",
                "category": "故障排查",
                "keywords": ["空调", "不制冷", "不制热", "漏水", "异响", "加氟", "滤网"]
            },
            {
                "content": "冰箱常见故障排查：1)不制冷请确认温控档位是否正常、门封条是否变形漏气、冰箱是否靠墙过近散热不良；2)冷藏室结冰多为门封密封不严或温控器故障；3)噪音大可能是压缩机脚垫老化或摆放不平。断电清理后仍异常的，建议预约工程师上门检测。",
                "category": "故障排查",
                "keywords": ["冰箱", "不制冷", "结冰", "噪音", "门封", "压缩机", "冷藏", "冷冻"]
            },
            {
                "content": "洗衣机常见故障排查：1)不脱水先检查衣物是否分布不均、排水管是否堵塞；2)进水异常检查水龙头和水压；3)漏水多为门封圈老化或排水管破损；4)启动无反应检查电源和门锁是否关好。若故障灯常亮且重启无效，请预约上门维修。",
                "category": "故障排查",
                "keywords": ["洗衣机", "不脱水", "漏水", "异响", "进水", "门锁", "滚筒", "波轮"]
            },
            {
                "content": "退换货政策：自购买之日起15日内，非人为损坏且外观完好的产品支持无理由退换货；一年内同一故障经两次维修仍无法修复的，可凭维修记录申请换机。办理退换货需出示购买凭证（订单号或发票）。",
                "category": "退换货",
                "keywords": ["退换货", "退货", "换货", "换机", "退款", "订单号"]
            },
            {
                "content": "预约政策：如需取消或更改上门维修预约，请提前至少2小时通过在线客服或售后电话告知。工程师已出发后临时取消的，可能需要支付30元空跑费。上门时间段为每天9:00-18:00之间的整点或半点时段，每单预留2小时服务窗口。",
                "category": "预约政策",
                "keywords": ["取消", "更改", "改约", "上门", "预约", "时间", "空跑"]
            },
            {
                "content": "延保服务：产品在保修期内可购买延长保修服务，每次延长1年，最多延长2年。延保期内享受与整机保修相同的免费上门维修服务。购买延保需在整机保修到期前办理。",
                "category": "延保服务",
                "keywords": ["延保", "延长保修", "续保", "加保"]
            },
            {
                "content": "安装服务：空调、热水器、净水器、烟灶等产品支持上门安装，整机安装免费，但需加长管道、支架、打孔等辅材时按标准收取材料费。购买新机后可凭订单号在线预约安装，通常2个工作日内上门。",
                "category": "安装服务",
                "keywords": ["安装", "新机安装", "辅材", "打孔", "材料费"]
            },
            {
                "content": "保养建议：空调建议每年入夏前清洗滤网和蒸发器；洗衣机建议每2-3个月用桶自洁程序清洗内筒；冰箱门封条定期用湿布擦拭，背部散热区保持通风；净水器滤芯按提示周期更换，一般为6-12个月。定期保养可延长家电使用寿命并降低故障率。",
                "category": "保养服务",
                "keywords": ["保养", "清洗", "滤网", "滤芯更换", "寿命", "维护"]
            },
            {
                "content": "安全提示：发现家电有焦糊味、冒烟、漏电或电源线破损时，请立即断电停用，不要自行拆机检修，并及时通过在线客服或售后电话400-820-9000登记紧急维修。燃气类产品（热水器、燃气灶）检修需由持证工程师上门处理，切勿自行拆动燃气管道。",
                "category": "安全提示",
                "keywords": ["安全", "漏电", "冒烟", "焦糊味", "燃气", "紧急", "危险"]
            }
        ]

    async def initialize(self):
        """初始化知识库服务"""
        try:
            # 检查数据库中是否已有数据
            existing_docs = self.db.get_all_documents()
            
            if not existing_docs:
                logger.info("数据库为空，初始化默认知识库")
                await self._create_default_knowledge()
            else:
                logger.info(f"从数据库加载了 {len(existing_docs)} 条知识")
            
            # 构建向量索引
            await self._build_vector_index()
            self.initialized = True
            logger.info("知识库服务初始化完成")
            
        except Exception as e:
            logger.error(f"知识库服务初始化失败: {e}")
            raise

    async def _create_default_knowledge(self):
        """创建默认知识库（Embedding 服务不可用时降级为仅入库内容，索引后续重建）"""
        embedding_available = True
        for knowledge in self.default_knowledge:
            try:
                embedding = None
                if embedding_available:
                    try:
                        # 生成嵌入向量
                        text_for_embedding = f"{knowledge['content']} {' '.join(knowledge['keywords'])}"
                        embedding = embed_input(text_for_embedding)
                    except Exception:
                        # 网络/Key异常时不再逐条重试，剩余条目快速降级入库
                        logger.warning("Embedding 服务不可用，知识内容降级入库（向量索引将在服务可用后重建）")
                        embedding_available = False

                # 保存到数据库
                self.db.add_document(
                    content=knowledge['content'],
                    category=knowledge['category'],
                    keywords=knowledge['keywords'],
                    embedding=embedding
                )
                logger.debug(f"添加默认知识: {knowledge['content'][:50]}...")

            except Exception as e:
                logger.error(f"添加默认知识失败: {e}")

    async def _build_vector_index(self):
        """构建向量索引"""
        try:
            documents = self.db.get_all_documents()
            if not documents:
                logger.warning("没有文档可用于构建索引")
                return

            embeddings = []
            self.document_ids = []
            
            for doc in documents:
                if doc.get('embedding'):
                    embeddings.append(doc['embedding'])
                    self.document_ids.append(doc['id'])
                    continue

                # 如果没有嵌入向量，尝试生成（失败则中断，避免无Key时逐条重试拖慢启动）
                logger.warning(f"文档 {doc['id']} 缺少嵌入向量，正在生成...")
                try:
                    text_for_embedding = f"{doc['content']} {' '.join(doc.get('keywords', []))}"
                    embedding = embed_input(text_for_embedding)
                except Exception as e:
                    logger.warning(f"Embedding 服务不可用，跳过向量索引构建: {e}")
                    break

                # 更新数据库
                self.db.update_document(doc['id'], embedding=embedding)

                embeddings.append(embedding)
                self.document_ids.append(doc['id'])

            if embeddings:
                # 创建FAISS索引
                embeddings_array = np.array(embeddings).astype('float32')
                dimension = embeddings_array.shape[1]
                self.index = faiss.IndexFlatIP(dimension)  # 内积相似度
                self.index.add(embeddings_array)
                logger.info(f"构建向量索引完成，包含 {len(embeddings)} 个向量")
            else:
                logger.warning("没有有效的嵌入向量，无法构建索引")

        except Exception as e:
            logger.error(f"构建向量索引失败: {e}")
            raise

    async def search(self, query: str, top_k: int = 3, category: str = None) -> List[Dict]:
        """搜索相关文档

        外部 RAG MCP 开关开启且未限定分类时，优先委托外部 RAG 服务检索；
        无结果或调用异常时回退本地 FAISS 索引检索。
        """
        if category is None and _rag_mcp_enabled():
            try:
                # 方法内导入：RAG 客户端依赖 mcp SDK，隔离失败不波及本地检索
                from services.mcp_rag_client import get_rag_mcp_client

                client = await get_rag_mcp_client()
                docs = await client.search(query, top_k=top_k)
                if docs:
                    return docs
                logger.info("外部 RAG 检索无结果，回退本地检索")
            except Exception as e:
                logger.warning(f"外部 RAG 检索异常，回退本地检索: {e}")

        if not self.initialized or self.index is None:
            logger.warning("知识库服务未初始化或索引不可用")
            return []

        try:
            # 生成查询的嵌入向量
            query_embedding = embed_input(query)
            query_array = np.array([query_embedding]).astype('float32')
            
            # 向量搜索
            scores, indices = self.index.search(query_array, min(top_k * 2, len(self.document_ids)))  # 多检索一些候选
            
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < len(self.document_ids):
                    doc_id = self.document_ids[idx]
                    doc = self.db.get_document(doc_id)
                    
                    if doc:
                        # 如果指定了分类过滤
                        if category and doc.get('category') != category:
                            continue
                            
                        doc['score'] = float(score)
                        doc['rank'] = len(results) + 1
                        results.append(doc)
                        
                        # 达到所需数量就停止
                        if len(results) >= top_k:
                            break
            
            return results
            
        except Exception as e:
            logger.error(f"搜索知识库失败: {e}")
            return []

    async def add_document(self, content: str, category: str, keywords: List[str] = None) -> bool:
        """添加新文档"""
        try:
            if keywords is None:
                keywords = []
            
            # 生成嵌入向量
            text_for_embedding = f"{content} {' '.join(keywords)}"
            embedding = embed_input(text_for_embedding)
            
            # 保存到数据库
            doc_id = self.db.add_document(content, category, keywords, embedding)
            
            # 重建索引
            await self._build_vector_index()
            
            logger.info(f"成功添加文档 {doc_id}: {content[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            return False

    async def update_document(self, doc_id: int, content: str = None, category: str = None, keywords: List[str] = None) -> bool:
        """更新文档"""
        try:
            # 如果更新了内容或关键词，需要重新生成嵌入向量
            embedding = None
            if content is not None or keywords is not None:
                # 获取当前文档信息
                current_doc = self.db.get_document(doc_id)
                if not current_doc:
                    return False
                
                # 使用新值或保持原值
                final_content = content if content is not None else current_doc['content']
                final_keywords = keywords if keywords is not None else current_doc.get('keywords', [])
                
                # 生成新的嵌入向量
                text_for_embedding = f"{final_content} {' '.join(final_keywords)}"
                embedding = embed_input(text_for_embedding)
            
            # 更新数据库
            success = self.db.update_document(doc_id, content, category, keywords, embedding)
            
            if success and embedding is not None:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"更新文档失败: {e}")
            return False

    async def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """删除文档"""
        try:
            success = self.db.delete_document(doc_id, soft_delete)
            
            if success:
                # 重建索引
                await self._build_vector_index()
            
            return success
            
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return False

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict]:
        """获取所有文档"""
        return self.db.get_all_documents(include_inactive)

    def get_document(self, doc_id: int) -> Dict:
        """获取指定文档"""
        return self.db.get_document(doc_id)

    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        return self.db.get_all_categories()

    def get_documents_count(self) -> int:
        """获取文档总数"""
        return self.db.get_documents_count()

    def search_by_category(self, category: str) -> List[Dict]:
        """按分类搜索文档"""
        return self.db.search_documents_by_category(category)

    def search_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """按关键词搜索文档"""
        return self.db.search_documents_by_keywords(keywords)
