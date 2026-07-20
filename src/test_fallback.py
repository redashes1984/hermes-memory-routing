#!/usr/bin/env python3
"""Test fallback mechanism."""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.dirname(__file__))
import memory_routing as mr

temp_dir = tempfile.mkdtemp()
os.environ['HERMES_HOME'] = temp_dir

# Test 1: keyword score 0 -> fallback to dev-log
result = mr.route_memory_to_sub_docs('memory', 'Today the weather is nice')
devlog_path = os.path.join(temp_dir, 'memory', 'dev-log.md')
has_entry = os.path.exists(devlog_path) and 'weather' in open(devlog_path).read()
print(f'Test 1 - keyword score 0 fallback: {"PASS" if has_entry else "FAIL"}')

# Test 2: llm_classify_memory with unreachable endpoint -> dev-log fallback
doc, conf = mr.llm_classify_memory('test content')
print(f'Test 2 - LLM unreachable fallback: doc={doc}, conf={conf} -> {"PASS" if doc == "dev-log" and conf == 0.0 else "FAIL"}')

# Test 3: valid match should NOT go to dev-log fallback
result2 = mr.route_memory_to_sub_docs('memory', 'vLLM server on port 8688')
infra_path = os.path.join(temp_dir, 'memory', 'infrastructure.md')
infra_has = os.path.exists(infra_path) and 'vLLM' in open(infra_path).read()
print(f'Test 3 - valid routing not fallback: {"PASS" if infra_has else "FAIL"}')

# Test 4: route_memory_to_sub_docs returns True on fallback (score 0)
shutil.rmtree(temp_dir, ignore_errors=True)
temp_dir = tempfile.mkdtemp()
os.environ['HERMES_HOME'] = temp_dir
result3 = mr.route_memory_to_sub_docs('memory', 'Some random unrelated content')
print(f'Test 4 - fallback returns True: {"PASS" if result3 else "FAIL"}')

shutil.rmtree(temp_dir, ignore_errors=True)
print('Done.')