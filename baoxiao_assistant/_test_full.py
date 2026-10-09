"""青禾报销助手 - 三角色完整业务流程测试
   小林（员工）→ 周敏（财务）→ 乔姐（行政）
"""
import requests, json

BASE = "http://localhost:8000"

def banner(title):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print('='*55)

# ═══════════════════════════════════════════════════
#  第一幕：小林（员工）— 查制度 → 提交报销 → 查状态
# ═══════════════════════════════════════════════════
banner("第一幕：小林（员工）")

emp = requests.Session()
r = emp.post(f"{BASE}/login", data={"username": "xiaolin"}, allow_redirects=False)
print(f"✅ 登录员工: {r.status_code} → {r.headers.get('location','')}")

r = emp.post(f"{BASE}/api/policy/query", json={"query": "出差住宿标准是多少"})
print(f"\n📖 制度查询 RAG:")
print(f"  {r.json()['answer'][:250]}")

r = emp.post(f"{BASE}/api/reimburse/submit", data={
    "category": "差旅",
    "amount": "1680.00",
    "purpose": "3月上海客户拜访住宿",
})
data = r.json()
tracking = data["tracking_no"]
print(f"\n📝 提交报销: {tracking} | 状态={data['status']}")
print(f"   材料审核Agent: {data.get('decision')} → {data.get('reason','')[:150]}")

r = emp.get(f"{BASE}/api/reimburse/{tracking}")
d = r.json()
print(f"\n🔍 查状态: {d['status']}")
print(f"   Agent回答: {d['answer'][:180]}")

# ═══════════════════════════════════════════════════
#  第二幕：周敏（财务）— 核对 → 通过 → 标记已付
# ═══════════════════════════════════════════════════
banner("第二幕：周敏（财务专员）")

fin = requests.Session()
r = fin.post(f"{BASE}/login", data={"username": "zhoumin"}, allow_redirects=False)
print(f"✅ 登录财务: {r.status_code} → {r.headers.get('location','')}")

r = fin.post(f"{BASE}/api/finance/review/{tracking}", data={"action": "advise"})
print(f"\n🤖 财务Agent建议: {r.json() if r.ok else r.text}")

r = fin.post(f"{BASE}/api/finance/review/{tracking}", data={"action": "approve", "comment": "业务事实已核对无误"})
print(f"✅ 财务通过: {r.json()}")

r = fin.post(f"{BASE}/api/finance/review/{tracking}", data={"action": "paid", "comment": "2025-03-20 银行转账完成"})
print(f"💰 标记已付: {r.json()}")

# ═══════════════════════════════════════════════════
#  第三幕：小林再查 → 看到已完成状态
# ═══════════════════════════════════════════════════
banner("第三幕：小林再次查询（看到最终状态）")

r = emp.get(f"{BASE}/api/reimburse/{tracking}")
d = r.json()
print(f"📋 {tracking} 最终状态: **{d['status']}**")
print(f"\nAgent回答:\n{d['answer']}")
print(f"\n审计日志 ({len(d['logs'])} 条):")
for log in d['logs']:
    print(f"  [{log['time']}] {log['action']}: {log['comment'][:60]}")

# ═══════════════════════════════════════════════════
#  第四幕：乔姐（行政）— 维护制度文档 + 动态重建索引
# ═══════════════════════════════════════════════════
banner("第四幕：乔姐（行政专员）— 动态制度管理")

adm = requests.Session()
r = adm.post(f"{BASE}/login", data={"username": "qiaojie"}, allow_redirects=False)
print(f"✅ 登录行政: {r.status_code} → {r.headers.get('location','')}")

r = adm.post(f"{BASE}/api/admin/policy/add", data={
    "title": "2025年新版招待费标准",
    "category": "招待",
    "content": "2025年起执行新招待标准：一线城市单次人均不超过150元，其他城市不超过120元。单次超2000元须分管副总审批。",
})
print(f"\n📄 新增制度 + 重建RAG索引: {r.json()}")

r = emp.post(f"{BASE}/api/policy/query", json={"query": "2025年招待费新标准"})
print(f"\n🔎 小林查询新制度:")
print(f"  {r.json()['answer'][:300]}")

# ═══════════════════════════════════════════════════
#  第五幕：异常场景 — 超标准报销被拒
# ═══════════════════════════════════════════════════
banner("第五幕：异常场景（超标准被拒）")

r = emp.post(f"{BASE}/api/reimburse/submit", data={
    "category": "差旅",
    "amount": "12000.00",
    "purpose": "上海一晚五星级酒店住宿",
})
print(f"小林提交 12000 元住宿: {r.json()['tracking_no']} | {r.json()['status']}")
print(f"Agent → {r.json().get('decision')}: {r.json().get('reason','')[:200]}")

# ═══════════════════════════════════════════════════
banner("🎉 全部业务流程测试完成！")