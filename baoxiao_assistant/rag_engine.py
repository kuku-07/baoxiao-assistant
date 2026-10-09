"""RAG 引擎 - 轻量实现：jieba + 纯 Python TF-IDF 余弦相似度
   接口与原来的 FAISS 版完全一致（PolicyRAG.rebuild_index / search / build_context / get_rag），
   所以 main.py / agents.py 无需改动。
   如果以后装了 faiss + sentence-transformers + numpy，会自动切换到向量方案。
"""
import os
import json
import math
import sqlite3
from typing import List, Tuple, Optional

import jieba

# ---------- 可选的重型依赖 ----------
try:
    import numpy as np  # type: ignore
    import faiss  # type: ignore
    from sentence_transformers import SentenceTransformer  # type: ignore
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore
    HEAVY_OK = True
except Exception as _e:
    HEAVY_OK = False
    print(f"ℹ️  轻量模式启动（未检测到 faiss/transformers/numpy：{_e}）")

DB_PATH = os.path.join(os.path.dirname(__file__), "baoxiao.db")
INDEX_DIR = os.path.join(os.path.dirname(__file__), "faiss_index")
META_PATH = os.path.join(INDEX_DIR, "policy_meta.json")


# ============================================================
#  轻量 TF-IDF 检索（默认）
# ============================================================
def _tokenize(text: str) -> List[str]:
    return [w for w in jieba.lcut(text) if len(w.strip()) >= 2]


def _cosine(a: dict, b: dict) -> float:
    common = set(a.keys()) & set(b.keys())
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _tfidf_matrix(chunks: List[dict]) -> Tuple[List[dict], List[dict], dict]:
    """返回: (tfidf_vecs, 原始文本dict列表, idf)"""
    doc_tokens = [_tokenize(c["text"]) for c in chunks]
    df: dict = {}
    for tokens in doc_tokens:
        for w in set(tokens):
            df[w] = df.get(w, 0) + 1
    n = len(doc_tokens)
    idf = {w: math.log((n + 1) / (df[w] + 1)) + 1 for w in df}

    vecs = []
    for tokens in doc_tokens:
        tf = {}
        for w in tokens:
            tf[w] = tf.get(w, 0) + 1
        tfidf = {w: (c / len(tokens)) * idf[w] for w, c in tf.items()}
        vecs.append(tfidf)
    return vecs, chunks, idf


# ============================================================
#  PolicyRAG 主类
# ============================================================
class PolicyRAG:
    """制度文档 RAG 检索器（轻量 TF-IDF 或 重型 FAISS 自动切换）"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.chunks: List[dict] = []
        self._tfidf_vecs: List[dict] = []
        self._idf: dict = {}
        self._use_heavy = HEAVY_OK
        self._model = None
        self._index = None

    # ============= 索引构建 =============
    def rebuild_index(self) -> int:
        """从 SQLite policy_docs 表重新读取所有制度 → 切块 → 建索引"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, title, category, keywords, content FROM policy_docs"
        ).fetchall()
        conn.close()

        if not rows:
            print("⚠️ 制度表为空，跳过索引重建")
            return 0

        # 切块
        chunks = []
        if self._use_heavy:
            try:
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=400, chunk_overlap=50,
                    separators=["\n\n", "\n", "。", "；", "，"],
                )
            except Exception:
                splitter = None
        else:
            splitter = None

        for r in rows:
            prefix = f"【{r['category']}】{r['title']}\n关键词：{r['keywords'] or ''}\n"
            text = prefix + r["content"]
            if splitter:
                parts = splitter.split_text(text)
            else:
                parts = [text[i:i+350] for i in range(0, len(text), 280)]
            for i, part in enumerate(parts):
                chunks.append({
                    "doc_id": r["id"],
                    "title": r["title"],
                    "category": r["category"],
                    "chunk_index": i,
                    "text": part,
                })

        self.chunks = chunks

        if self._use_heavy:
            try:
                import faiss
                os.makedirs(INDEX_DIR, exist_ok=True)
                self._model = SentenceTransformer(self.model_name)
                vectors = self._model.encode(
                    [c["text"] for c in chunks],
                    convert_to_numpy=True, normalize_embeddings=True,
                ).astype("float32")
                dim = vectors.shape[1]
                self._index = faiss.IndexFlatIP(dim)
                self._index.add(vectors)
                faiss.write_index(self._index, os.path.join(INDEX_DIR, "policy.faiss"))
                with open(META_PATH, "w", encoding="utf-8") as f:
                    json.dump(self.chunks, f, ensure_ascii=False, indent=2)
                print(f"✅ [FAISS] 制度索引重建完成：{len(chunks)} 个切块")
                return len(chunks)
            except Exception as _e:
                print(f"⚠️ 重型方案失败（{_e}），降级到 TF-IDF")
                self._use_heavy = False

        # 轻量 TF-IDF
        self._tfidf_vecs, self.chunks, self._idf = _tfidf_matrix(self.chunks)
        print(f"✅ [TF-IDF] 制度索引重建完成：{len(self.chunks)} 个切块")
        return len(self.chunks)

    # ============= 加载已有索引（轻量模式每次内存重建，无需持久化） =============
    def load_index(self) -> bool:
        if self._use_heavy:
            import faiss
            if os.path.exists(os.path.join(INDEX_DIR, "policy.faiss")) and os.path.exists(META_PATH):
                self._index = faiss.read_index(os.path.join(INDEX_DIR, "policy.faiss"))
                with open(META_PATH, "r", encoding="utf-8") as f:
                    self.chunks = json.load(f)
                print(f"📂 [FAISS] 已加载制度索引：{len(self.chunks)} 个切块")
                return True
            return False
        # 轻量模式直接重建（数据来自 SQLite，最权威）
        if not self.chunks:
            self.rebuild_index()
        return bool(self.chunks)

    # ============= 检索 =============
    def search(self, query: str, top_k: int = 5) -> List[dict]:
        if self._use_heavy:
            return self._search_heavy(query, top_k)
        return self._search_light(query, top_k)

    def _search_light(self, query: str, top_k: int) -> List[dict]:
        if not self.chunks:
            return []
        if not self._tfidf_vecs:
            self._tfidf_vecs, self.chunks, self._idf = _tfidf_matrix(self.chunks)
        # 查询向量
        tokens = _tokenize(query)
        tf = {}
        for w in tokens:
            tf[w] = tf.get(w, 0) + 1
        q_vec = {w: (c / max(len(tokens), 1)) * self._idf.get(w, 0) for w, c in tf.items() if w in self._idf}
        scored = [(i, _cosine(q_vec, v)) for i, v in enumerate(self._tfidf_vecs)]
        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for i, s in scored[:top_k]:
            if s > 0:
                results.append({**self.chunks[i], "score": float(s)})
        return results

    def _search_heavy(self, query: str, top_k: int) -> List[dict]:
        if self._index is None:
            if not self.load_index():
                return []
        import numpy as np
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        qv = self._model.encode([query], convert_to_numpy=True, normalize_embeddings=True).astype("float32")
        scores, ids = self._index.search(qv, min(top_k, len(self.chunks)))
        results = []
        for s, idx in zip(scores[0], ids[0]):
            if 0 <= idx < len(self.chunks):
                results.append({**self.chunks[idx], "score": float(s)})
        return results

    # ============= 拼接上下文 =============
    def build_context(self, query: str, top_k: int = 5) -> str:
        results = self.search(query, top_k)
        if not results:
            return ""
        lines = []
        for r in results:
            lines.append(
                f"--- [{r['category']}] {r['title']} (相关度 {r['score']:.2f}) ---\n{r['text']}"
            )
        return "\n\n".join(lines)


# 全局单例
_rag_instance: Optional[PolicyRAG] = None


def get_rag() -> PolicyRAG:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = PolicyRAG()
        # 直接从 DB 建（轻量模式无需持久化索引文件）
        _rag_instance.rebuild_index()
    return _rag_instance


if __name__ == "__main__":
    from database import init_db
    init_db(reset=False)
    rag = PolicyRAG()
    n = rag.rebuild_index()
    print(f"\n🔍 共 {n} 个切块。测试查询：出差住宿标准")
    for r in rag.search("出差住宿标准")[:3]:
        print(f"  [{r['score']:.3f}] {r['title']} → {r['text'][:100]}...")