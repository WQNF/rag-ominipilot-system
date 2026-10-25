#!/usr/bin/env python3
"""
数据库状态检查脚本
检查 Milvus 向量库和 PostgreSQL 中是否有数据，及数据量
用法: python valuation/check_db.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

def check_milvus():
    """检查 Milvus 向量数据库"""
    from pymilvus import connections, Collection, utility

    host = os.getenv("MILVUS_HOST", "localhost")
    port = int(os.getenv("MILVUS_PORT", "19530"))

    print("=" * 60)
    print("🔍 Milvus 向量数据库检查")
    print(f"   连接地址: {host}:{port}")
    print("=" * 60)

    try:
        connections.connect(host=host, port=port)
        print("   ✅ 连接成功\n")
    except Exception as e:
        print(f"   ❌ 连接失败: {e}")
        print("   请确保 Docker 容器已启动: docker compose up -d postgres etcd minio milvus\n")
        return

    collections_to_check = ["knowledge_base", "user_knowledge_base"]

    total_all = 0
    for col_name in collections_to_check:
        if utility.has_collection(col_name):
            col = Collection(col_name)
            col.load()
            num = col.num_entities
            total_all += num
            print(f"   📦 集合 '{col_name}': {num} 条向量记录")
        else:
            print(f"   📦 集合 '{col_name}': ⚠️ 不存在（尚未初始化）")

    print(f"\n   📊 Milvus 总计: {total_all} 条记录")
    print()

    # 如果有 knowledge_base，展示文件分布
    if utility.has_collection("knowledge_base"):
        col = Collection("knowledge_base")
        col.load()
        # 按 file_type 统计
        try:
            print("   📂 按文件类型分布:")
            for ftype in ["word", "pdf", "excel"]:
                try:
                    results = col.query(
                        expr=f'file_type == "{ftype}"',
                        output_fields=["id"],
                        limit=1,
                    )
                    # query 只返回少量，用 num_entities 近似；改用统计方式
                except Exception:
                    pass
            # 用更可靠的方式：按 file_name 分组
            print("   (使用更详细的分布统计...)")
            # 抽样展示文件名
            try:
                samples = col.query(
                    expr="id >= 0",
                    output_fields=["file_name", "file_type"],
                    limit=20,
                )
                seen_files = set()
                file_type_count = {}
                for s in samples:
                    fname = s.get("file_name", "unknown")
                    ftype = s.get("file_type", "unknown")
                    if fname not in seen_files:
                        seen_files.add(fname)
                        file_type_count[ftype] = file_type_count.get(ftype, 0) + 1
                        print(f"      - [{ftype}] {fname}")
                if file_type_count:
                    print(f"\n   📂 抽样文件类型分布: {file_type_count}")
                if len(seen_files) >= 20:
                    print(f"   ... 还有更多文件未列出")
            except Exception as e:
                print(f"   ⚠️ 无法查询文件分布: {e}")
        except Exception as e:
            print(f"   ⚠️ 分布查询出错: {e}")

def check_postgresql():
    """检查 PostgreSQL 数据库"""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "user": os.getenv("POSTGRES_USER", "omnipilot"),
        "password": os.getenv("POSTGRES_PASSWORD", "omnipilot123"),
        "database": os.getenv("POSTGRES_DB", "omnipilot"),
    }

    print("=" * 60)
    print("🐘 PostgreSQL 数据库检查")
    print(f"   连接地址: {db_config['host']}:{db_config['port']}/{db_config['database']}")
    print("=" * 60)

    try:
        conn = psycopg2.connect(**db_config)
        print("   ✅ 连接成功\n")
    except Exception as e:
        print(f"   ❌ 连接失败: {e}")
        print("   请确保 Docker 容器已启动: docker compose up -d postgres\n")
        return

    cursor = conn.cursor()

    # 检查所有表
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        print("   ⚠️ 数据库中没有任何表\n")
    else:
        print(f"   📋 数据库中的表 ({len(tables)} 个):")
        total_rows = 0
        for table in tables:
            cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
            count = cursor.fetchone()[0]
            total_rows += count
            print(f"      - {table}: {count} 条记录")
        print(f"\n   📊 PostgreSQL 总计: {total_rows} 条记录")

    # 检查 pgvector 扩展
    cursor.execute("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'")
    pgv = cursor.fetchone()
    if pgv:
        print(f"   ✅ pgvector 扩展已安装 (版本 {pgv[1]})")
    else:
        print("   ⚠️ pgvector 扩展未安装")

    cursor.close()
    conn.close()
    print()

def main():
    print("\n" + "=" * 60)
    print("🗄️  数据库状态全景检查")
    print("=" * 60 + "\n")

    check_milvus()
    check_postgresql()

    print("=" * 60)
    print("✅ 检查完成")
    print("=" * 60)
    print()
    print("💡 提示:")
    print("   - 如果 knowledge_base 为空，需要运行知识库初始化脚本")
    print("   - 如果 Milvus 连接失败，运行: cd docker && docker compose up -d etcd minio milvus")
    print("   - 如果 PostgreSQL 连接失败，运行: cd docker && docker compose up -d postgres")

if __name__ == "__main__":
    main()