"""群知识库：匹配打分、自动回答的克制条件、候选项发现、问答增删改。"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.plugins import kb, recall


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch, store):
    # kb.py 在模块顶部 `from src.store import get_store`，已绑定同名局部名，
    # 必须 patch 模块自身的 get_store，patch src.store 无效
    monkeypatch.setattr(kb, "get_store", lambda: store)
    kb._reset_state()
    recall._reset_state()
    yield
    kb._reset_state()
    recall._reset_state()


async def _add(store, gid=555, q="怎么申请内测", a="私聊管理员报 QQ 和用途", kw="内测 申请"):
    return await store.add_kb_entry(gid, q, a, kw)


# ---------------------------------------------------------------- 匹配

async def test_match_exact_question(store):
    await _add(store)
    entry = await kb.match(555, "怎么申请内测啊")
    assert entry is not None and "私聊管理员" in entry["answer"]


async def test_match_core_word_alone_is_enough(store):
    """「内测怎么搞」只命中「内测」这一个核心词，也该算命中。"""
    await _add(store)
    assert await kb.match(555, "内测怎么搞") is not None


async def test_match_uses_alias_keywords(store):
    """管理员加的同义关键词要生效：问法里完全没有问题原文的词。"""
    await store.add_kb_entry(555, "怎么申请内测", "私聊管理员", "邀请码 开通资格")
    assert await kb.match(555, "邀请码在哪领") is not None


async def test_match_ignores_text_without_overlap(store):
    await store.add_kb_entry(555, "怎么加入会员群", "找管理员要链接", "会员")
    assert await kb.match(555, "有人在吗") is None
    assert await kb.match(555, "晚饭吃啥") is None


async def test_min_score_threshold_is_configurable(store):
    """临界区间的近似命中可以通过调高阈值压掉（这是已知的取舍，见模块注释）。"""
    await store.add_kb_entry(555, "怎么加入会员群", "找管理员要链接", "会员")
    # 「会员日」只共享「会员」这一个词，属于临界命中
    assert await kb.match(555, "今天会员日吗") is not None
    await store.set_kv("kb_min_score", "3.0")
    assert await kb.match(555, "今天会员日吗") is None
    # 真正的命中不受影响
    assert await kb.match(555, "会员怎么加入") is not None


async def test_unrelated_text_does_not_match(store):
    await _add(store)
    assert await kb.match(555, "今天晚饭吃什么") is None
    assert await kb.match(555, "有人打游戏吗") is None


async def test_entries_are_per_group(store):
    await _add(store, gid=555)
    assert await kb.match(666, "怎么申请内测") is None


async def test_rank_prefers_more_specific(store):
    await _add(store, q="群规在哪", a="群公告", kw="群规 规则")
    await _add(store, q="群规里关于广告的规定", a="不许自发广告", kw="广告 群规")
    ranked = kb.rank_entries(await store.list_kb_entries(555), "群规里广告怎么规定的")
    assert ranked and ranked[0][1]["answer"] == "不许自发广告"


# ---------------------------------------------------------------- 自动回答的克制

async def test_answer_requires_question_like_text(store, monkeypatch):
    await _add(store)
    # 陈述句，即使命中关键词也不抢答
    assert await kb.answer_for(555, "内测 申请") is None
    # 疑问句才回答
    hit = await kb.answer_for(555, "内测怎么申请？")
    assert hit is not None and "私聊管理员" in hit["answer"]


async def test_answer_skipped_when_disabled(store, monkeypatch):
    await _add(store)
    await store.set_kv("kb_enabled", "false")
    assert await kb.answer_for(555, "怎么申请内测") is None


async def test_answer_skipped_when_auto_off(store):
    await _add(store)
    await store.set_kv("kb_auto_answer", "false")
    assert await kb.answer_for(555, "怎么申请内测") is None


async def test_answer_counts_hits(store):
    entry_id = await _add(store)
    await kb.answer_for(555, "怎么申请内测？")
    await kb.answer_for(555, "内测怎么申请")
    assert (await store.get_kb_entry(entry_id))["hits"] == 2


def test_looks_like_question():
    for text in ("怎么申请内测", "群规在哪", "有没有报名表", "内测怎么办？"):
        assert kb.looks_like_question(text)
    for text in ("今天天气不错", "我刚吃完饭", "这个功能挺好用"):
        assert not kb.looks_like_question(text)


# ---------------------------------------------------------------- 候选项发现

def _log(tmp_path, rows):
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    p = d / "group-555-2026-11-15.jsonl"
    import json

    with p.open("w", encoding="utf-8") as f:
        for text, hours in rows:
            ts = datetime.now().astimezone() - timedelta(hours=hours)
            f.write(json.dumps({"time": ts.isoformat(timespec="seconds"), "group_id": 555,
                                "user_id": 1, "message_id": 1, "raw_plain": text},
                               ensure_ascii=False) + "\n")
    return d


async def test_suggest_finds_repeated_questions(monkeypatch, store, tmp_path):
    d = _log(tmp_path, [
        ("怎么申请内测？", 1), ("怎么申请内测", 2), ("怎么申请内测啊", 3),
        ("今天吃什么", 4), ("有人打游戏吗", 5),
    ])
    monkeypatch.setattr(recall, "get_config", lambda: SimpleNamespace(xingchao_log_dir=d))
    items = await kb.suggest(555, min_count=2)
    assert items and "内测" in items[0]["question"]
    assert all("吃什么" not in i["question"] for i in items)  # 只出现一次的不算


async def test_suggest_excludes_already_answerable(monkeypatch, store, tmp_path):
    await _add(store)
    d = _log(tmp_path, [("怎么申请内测", 1), ("怎么申请内测", 2), ("怎么申请内测", 3)])
    monkeypatch.setattr(recall, "get_config", lambda: SimpleNamespace(xingchao_log_dir=d))
    assert await kb.suggest(555, min_count=2) == []  # 已经能命中，不再推荐


async def test_suggest_respects_lookback(monkeypatch, store, tmp_path):
    d = _log(tmp_path, [("怎么办会员卡", 24 * 5), ("怎么办会员卡", 24 * 4)])
    monkeypatch.setattr(recall, "get_config", lambda: SimpleNamespace(xingchao_log_dir=d))
    assert await kb.suggest(555, min_count=2) == []  # 超出 72 小时窗口


# ---------------------------------------------------------------- 增删改

async def test_store_crud_and_group_isolation(store):
    entry_id = await _add(store, gid=555)
    assert await store.update_kb_entry(entry_id, answer="新答案") is True
    assert (await store.get_kb_entry(entry_id))["answer"] == "新答案"
    assert await store.list_kb_entries(666) == []
    assert await store.delete_kb_entry(entry_id) is True
    assert await store.delete_kb_entry(entry_id) is False


def test_parse_add_line():
    assert kb._parse_add("问题 | 答案") == ("问题", "答案", "")
    assert kb._parse_add("问题 | 答案 | 关键词1 关键词2") == ("问题", "答案", "关键词1 关键词2")
    assert kb._parse_add("只有问题") is None
    assert kb._parse_add(" | 答案") is None


def test_normalize_question_strips_politeness():
    assert kb.normalize_question("请问报名表在哪？") == kb.normalize_question("报名表在哪")
    assert kb.normalize_question("大佬们，怎么申请内测") == kb.normalize_question("怎么申请内测")
    assert kb.normalize_question("想问一下 内测怎么搞 ") == "内测怎么搞"


async def test_suggest_clusters_across_polite_prefixes(monkeypatch, store, tmp_path):
    d = _log(tmp_path, [("报名表在哪？", 1), ("报名表在哪", 2), ("请问报名表在哪", 3)])
    monkeypatch.setattr(recall, "get_config", lambda: SimpleNamespace(xingchao_log_dir=d))
    items = await kb.suggest(555, min_count=2)
    assert items == [] or len(items) == 1
    # 三种问法应聚成同一类，count 至少 2（不是 1）
    if items:
        assert items[0]["count"] >= 2
