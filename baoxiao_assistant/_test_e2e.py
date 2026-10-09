"""端到端测试：模拟小林（员工）登录 → 查制度 → 提交报销 → 查状态"""
import requests, json

BASE = "http://localhost:8000"
s = requests.Session()   # 自动管理 cookie

# ---- 1. 登录（员工 xiaolin）----
r = s.post(f"{BASE}/login", data={"username": "xiaolin"}, allow_redirects=False)
print(f"1. 登录: {r.status_code}  Location={r.headers.get('location','')}")

# ---- 2. 查制度（RAG 核心）----
r = s.post(f"{BASE}/api/policy/query", json={"query": "出差住宿标准是多少"})
print(f"2. 制度查询: {r.status_code}")
if r.ok:
    data = r.json()
    print(f"   answer: {data['answer'][:200]}")

# ---- 3. 提交报销单 ----
r = s.post(f"{BASE}/api/reimburse/submit", data={
    "category": "差旅",
    "amount": "1680.00",
    "purpose": "3月上海客户拜访住宿",
})
print(f"3. 提交报销: {r.status_code}")
if r.ok:
    data = r.json()
    print(f"   tracking_no={data['tracking_no']}  status={data['status']}")
    print(f"   agent_decision={data.get('decision')}  reason={data.get('reason','')[:150]}")
    tracking = data["tracking_no"]

    # ---- 4. 查状态 ----
    r2 = s.get(f"{BASE}/api/reimburse/{tracking}")
    print(f"4. 查状态: {r2.status_code}")
    if r2.ok:
        d = r2.json()
        print(f"   {json.dumps(d, ensure_ascii=False)[:300]}")

# ---- 5. 查我的全部 ----
r = s.get(f"{BASE}/api/reimburse/mine")
print(f"5. 我的报销单: {r.status_code}")
if r.ok:
    for item in r.json():
        print(f"   {item['tracking_no']} {item['category']} ¥{item['amount']} → {item['status']}")