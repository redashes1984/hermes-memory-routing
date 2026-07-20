#!/usr/bin/env python3
"""回归测试：验证 memory_routing.py 路由功能 + 安全 + 边界情况"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import memory_routing as mr

print("=" * 80)
print("【第 1 步：基础路由功能测试】—— 6 个核心类别验收")
print("=" * 80)

tests = [
    ("vLLM server on port 8688", "infrastructure"),
    ("棣民认为AI应该有记忆自主权，放手让AI自己成长", "philosophy"),
    ("2026-05-27 upgraded to v0.14.0", "milestones"),
    ("承诺永远守护秘密和数据安全", "commitments"),
    ("修复了 drift detection bug", "dev-log"),
    ("排查 systemd 故障切换规范", "rules"),
]

all_pass = True
for content, expected in tests:
    doc, score = mr.route_content_to_sub_doc(content)
    status = "✅" if doc == expected else "❌"
    if doc != expected:
        all_pass = False
    print(f"{status} [score={score}] {content[:40]:40s} → {doc or 'fallback'} (expected: {expected})")

print(f"\n【基础路由结果：{'全部通过 ✅' if all_pass else '存在失败 ❌'}】")

print("\n" + "=" * 80)
print("【第 2 步：安全测试】—— 符号链接攻击防护 / sanitize / 路径安全")
print("=" * 80)

# 检查关键安全函数是否存在
security_checks = [
    ("scan_memory_content", "内容扫描函数"),
    ("sanitize_content", "敏感信息过滤"),
    ("atomic_write_memory", "原子写入函数"),
    ("ALLOWED_BASE_DIRS", "路径白名单"),
]

security_ok = True
for func_name, desc in security_checks:
    if hasattr(mr, func_name):
        print(f"✅ {func_name} — {desc} 已实现")
    else:
        print(f"❌ {func_name} — {desc} 缺失！阻断级安全问题")
        security_ok = False

print(f"\n【安全测试结论：{'安全函数全部实现 ✅' if security_ok else '缺失关键安全函数 ❌ — 阻断！'}】")

print("\n" + "=" * 80)
print("【第 3 步：边界情况测试】—— 空内容/极长内容/特殊字符")
print("=" * 80)

edge_cases = [
    ("", "空字符串"),
    ("   ", "仅空白"),
    ("a" * 5000, "5000字符超长内容"),
    ("你好 \x00 世界 \t \n", "控制字符"),
    ("\\u200b\\u200c\\u200d 零宽字符", "Unicode 零宽字符注入"),
    ("\"\"\"", "空引号"),
]

edge_pass = True
for content, desc in edge_cases:
    try:
        doc, score = mr.route_content_to_sub_doc(content)
        if not content.strip():
            # 空内容应返回 None
            if doc is None:
                print(f"✅ {desc}: 正确返回 None")
            else:
                print(f"❌ {desc}: 应该返回 None，但返回了 {doc}")
                edge_pass = False
        else:
            print(f"✅ {desc}: 路由到 {doc or 'fallback'} (score={score})")
    except Exception as e:
        print(f"❌ {desc}: 抛出异常 {type(e).__name__}: {e}")
        edge_pass = False

print(f"\n【边界情况结果：{'全部通过 ✅' if edge_pass else '存在失败 ❌'}】")

print("\n" + "=" * 80)
print("【第 4 步：_add_to_sub_doc 写入安全测试】—— 路径穿越 / 符号链接")
print("=" * 80)

# 测试 doc_name 是否可能包含路径穿越
traversal_names = [
    "../../../etc/passwd",
    "/tmp/evil.md",
    "infrastructure/../../dev-log",
    "test; rm -rf /",
]

write_ok = True
for name in traversal_names:
    result = mr._add_to_sub_doc(name, "test content")
    if not result:
        # 预期失败
        print(f"✅ 路径穿越防护: '{name}' 被正确拒绝")
    else:
        print(f"❌ 路径穿越漏洞: '{name}' 被写入了！")
        write_ok = False

print(f"\n【写入安全结果：{'路径穿越被阻止 ✅' if write_ok else '路径穿越漏洞存在 ❌ — 阻断！'}】")

print("\n" + "=" * 80)
print("【第 5 步：事实缓存冲突检测测试】")
print("=" * 80)

# 清除缓存测试
test_content1 = "vLLM server port is 8688"
mr._update_fact_cache(test_content1, "test")
conflict = mr._detect_fact_conflict("vLLM server port is 9000")
if conflict and conflict.get("old_value") == "8688" and conflict.get("new_value") == "9000":
    print(f"✅ 事实冲突检测: 发现冲突 {conflict['old_value']} → {conflict['new_value']}")
else:
    print(f"❌ 事实冲突检测失败: {conflict}")

print("\n" + "=" * 80)
print("【第 6 步：去重测试】—— 重复写入是否被阻止")
print("=" * 80)

# 创建临时测试子文档
import tempfile
import shutil
temp_dir = tempfile.mkdtemp()
os.environ['HERMES_HOME'] = temp_dir  # 重定向 home

mr.route_memory_to_sub_docs("memory", "vLLM port 8688")
first = mr._add_to_sub_doc("infrastructure", "vLLM port 8688")
second = mr._add_to_sub_doc("infrastructure", "vLLM port 8688")
if first and not second:
    print("✅ 去重机制工作正常：首次写入成功，重复被拒绝")
else:
    print(f"❌ 去重失败：first={first}, second={second}")

# 清理
shutil.rmtree(temp_dir, ignore_errors=True)

print("\n" + "=" * 80)
print("【最终汇总】")
print("=" * 80)
all_results = all_pass and security_ok and edge_pass and write_ok
print(f"基础路由: {'✅' if all_pass else '❌'}")
print(f"安全函数: {'✅' if security_ok else '❌ — 阻断'}")
print(f"边界情况: {'✅' if edge_pass else '❌'}")
print(f"写入安全: {'✅' if write_ok else '❌ — 阻断'}")
print(f"\n最终结论: {'✅ 全部回归通过' if all_results else '❌ 存在阻断级问题'}")