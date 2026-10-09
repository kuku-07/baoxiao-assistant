from database import init_db
init_db(reset=True)
print('DB initialized')

from rag_engine import PolicyRAG
rag = PolicyRAG()
n = rag.rebuild_index()
print(f'Index rebuilt: {n} chunks')

print()
print('=== Test queries ===')
for q in ['出差住宿标准', '会议费报销流程', '交通费凭证要求']:
    print(f'Q: {q}')
    for r in rag.search(q)[:2]:
        sc = r['score']
        ti = r['title']
        tx = r['text'][:80].replace('\n', ' ')
        print(f'  [{sc:.3f}] {ti} | {tx}...')
    print()