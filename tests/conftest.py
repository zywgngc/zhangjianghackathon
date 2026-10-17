import pytest

from game_harness.config import Settings
from game_harness.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, api_key="test-key", max_repairs=1, concurrency=1)


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture
def request_data():
    return {"description": "做一个中文接星星游戏，100分胜利，3条生命。", "engine": "canvas"}


@pytest.fixture
def contracts():
    return {
        "brief": {"title": "接星星", "rewritten_prompt": "移动飞船接星星，100分胜利，3条生命，支持手机和键盘。",
                  "core_loop": ["移动", "接星星"], "user_requirements": ["100分胜利"], "assumptions": [],
                  "acceptance_criteria": ["可开始", "可暂停", "可重开"]},
        "design": {"engine": "canvas", "engine_reason": "动作游戏", "mechanics": ["星星10分", "3条命", "100分胜利"],
                   "controls": ["左右移动"], "win_condition": "100分", "lose_condition": "生命为0", "visual_style": "太空",
                   "responsive_strategy": "横竖屏自适应", "acceptance_criteria": ["可开始", "可暂停", "可重开"], "knowledge_ids": []},
        "review": {"verdict": "pass", "summary": "已检查", "defects": [], "limitations": ["仅冒烟测试"]},
    }
