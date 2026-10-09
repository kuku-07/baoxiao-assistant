import httpx, asyncio, json

BASE = "http://localhost:8000"

async def main():
    async with httpx.AsyncClient(timeout=10) as c:
        # 1. 首页
        r = await c.get(f"{BASE}/")
        print(f"GET /            → {r.status_code}  len={len(r.text)}")

        # 2. 查制度（RAG 核心）
        r = await c.post(f"{BASE}/api/policy/ask", json={"query": "出差住宿标准", "role": "employee"})
        print(f"POST /api/policy → {r.status_code}")
        if r.status_code == 200:
            print("   answer:", json.loads(r.text).get("answer", "")[:150])

        # 3. 登录员工
        r = await c.post(f"{BASE}/api/auth/login", json={"username": "xiaolin", "password": ""})
        print(f"POST login       → {r.status_code}  {r.text[:120]}")

        # 4. 查报销单
        r = await c.get(f"{BASE}/api/reimbursements?user_id=1")
        print(f"GET reimbursements → {r.status_code}  {r.text[:120]}")

asyncio.run(main())