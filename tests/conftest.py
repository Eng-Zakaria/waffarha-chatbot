# pytest collection rules for the tests/ tree.
#
# `test_conversations.py` and `memory_comprehensive_test.py` are runnable
# scripts (`python tests/test_conversations.py`), not pytest modules: they
# import RagEngine at module level, so letting pytest collect them would
# force a full embedding-model + vector-index load just to import.
collect_ignore = ["test_conversations.py", "memory_comprehensive_test.py"]