"""The command bot, on in-memory services and a mocked Bot API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from telegram_fake import CHAT, FakeTelegram

from fin_application.bot import BotCommands
from fin_application.errors import ChannelError
from fin_application.watchlist import ChannelService, Opportunities, WatchlistService
from fin_domain.market_data import SecurityUpdate
from fin_domain.scoring import Criterion, ScoreResult
from fin_domain.shared import Ticker
from fin_domain.signals import ScoreDetails, ScoreEntry
from fin_telegram import TelegramBot
from fin_testkit import FakeClock, InMemoryStore, in_memory_uow_factory

NOW = datetime(2025, 3, 10, 12, 0, tzinfo=UTC)


def graham(score, passed, total):
    names = [
        "P/B below limit",
        "P/E below limit",
        "current ratio",
        "debt",
        "dividend",
        "growth",
        "x",
    ]
    criteria = [Criterion(names[i], i >= total - passed) for i in range(total)]
    return ScoreResult("graham", score, criteria)


LYNCH = ScoreResult("lynch", 100.0, [Criterion(n, True) for n in ("peg", "growth", "debt")])
BUFFETT = ScoreResult("buffett", 0.0, explanation="too little history", evaluated=False)


def score(ticker, conviction, g):
    return ScoreEntry(
        ticker=Ticker(ticker),
        as_of=datetime(2025, 3, 10, 23, 59, 59, tzinfo=UTC),
        conviction=conviction,
        all_abstained=False,
        signal_blocked=False,
        explanation=f"Conviction from: graham {g.score:.0f}, lynch 100.",
        details=ScoreDetails(
            philosophies={"graham": g, "lynch": LYNCH, "buffett": BUFFETT},
            guardrail_notes=("buyback-driven",),
            warnings=("share count is old",),
        ),
        computed_at=NOW,
    )


@pytest.fixture
def bot_env():
    clock = FakeClock(NOW)
    store = InMemoryStore(clock)
    factory = in_memory_uow_factory(store)
    with factory() as uow:
        uow.securities.upsert([SecurityUpdate(Ticker(t)) for t in ("AAA", "BBB", "NEW")])
        uow.scores.add(score("AAA", 78.5, graham(57.0, 4, 7)))
        uow.scores.add(score("BBB", 92.8, graham(86.0, 6, 7)))
    commands = BotCommands(
        factory,
        WatchlistService(factory),
        ChannelService(factory, clock),
        Opportunities(factory),
    )
    fake = FakeTelegram()
    bot = TelegramBot(fake.client(), commands, now=lambda: NOW)
    return bot, fake, store, factory


def say(bot, text, chat=CHAT, update_id=1):
    return bot.handle_update(
        {"update_id": update_id, "message": {"chat": {"id": int(chat)}, "text": text}}
    )


def test_other_chats_and_plain_text_are_ignored_silently(bot_env):
    bot, fake, *_ = bot_env
    assert say(bot, "/top", chat="999") is None  # a stranger gets no answer, not even "denied"
    assert say(bot, "hello there") is None
    assert bot.handle_update({"update_id": 1}) is None and fake.calls == []


def test_help_and_unknown_commands(bot_env):
    bot, *_ = bot_env
    assert "/watch T" in say(bot, "/help") and "/watch T" in say(bot, "/start")
    assert say(bot, "/dance").startswith("Unknown command.")


def test_watch_unwatch_and_watchlist(bot_env):
    bot, _, store, _ = bot_env
    assert say(bot, "/watch aaa") == "Now watching AAA."
    assert say(bot, "/watch AAA") == "AAA is already watched."
    assert say(bot, "/watch@MyBot BBB") == "Now watching BBB."  # command with the bot's @name
    assert say(bot, "/watchlist") == "Watching: AAA, BBB"
    assert say(bot, "/unwatch AAA") == "Stopped watching AAA."
    assert say(bot, "/unwatch AAA") == "AAA was not watched."
    assert "Unknown ticker ZZZ" in say(bot, "/watch zzz")
    assert say(bot, "/watch") == "Usage: /watch TICKER"
    assert sorted(str(t) for t in store.watched) == ["BBB"]
    assert say(bot, "/unwatch BBB") and say(bot, "/watchlist") == "The watchlist is empty."


def test_top_ranks_by_conviction_with_the_philosophy_breakdown(bot_env):
    bot, *_ = bot_env
    assert say(bot, "/top") == (
        "1. BBB 93  (graham 86, lynch 100)\n2. AAA 78  (graham 57, lynch 100)"
    )
    assert say(bot, "/top 1").startswith("1. BBB 93") and "AAA" not in say(bot, "/top 1")
    assert say(bot, "/top x") == "Usage: /top [n]"


def test_top_with_nothing_scored_says_so():
    clock = FakeClock(NOW)
    factory = in_memory_uow_factory(InMemoryStore(clock))
    commands = BotCommands(
        factory, WatchlistService(factory), ChannelService(factory, clock), Opportunities(factory)
    )
    bot = TelegramBot(FakeTelegram().client(), commands)
    assert say(bot, "/top") == "No scored securities yet."


def test_why_explains_the_latest_score(bot_env):
    bot, *_ = bot_env
    text = say(bot, "/why AAA")
    assert text.startswith("AAA: conviction 78.5 (as of 2025-03-10)")
    assert "- graham 57 (4/7 criteria; failing: P/B below limit" in text
    assert "- lynch 100 (3/3 criteria)" in text
    assert "- buffett: not evaluated (too little history)" in text
    assert "* buyback-driven" in text and "* share count is old" in text
    assert say(bot, "/why NEW") == "No score yet for NEW."
    assert say(bot, "/why zzz") == "Unknown ticker ZZZ."
    assert say(bot, "/why") == "Usage: /why TICKER"


def test_why_says_when_nothing_could_be_evaluated(bot_env):
    bot, _, _, factory = bot_env
    with factory() as uow:
        uow.scores.add(
            ScoreEntry(
                Ticker("NEW"),
                NOW,
                None,
                True,
                False,
                "No philosophy could be evaluated.",
                computed_at=NOW,
            )  # fmt: skip
        )
    assert say(bot, "/why NEW").startswith("NEW: no conviction (nothing could be evaluated)")


def test_mute_and_unmute_set_and_clear_the_channel_state(bot_env):
    bot, _, store, _ = bot_env
    assert "muted until 2025-03-10 14:00 UTC" in say(bot, "/mute 2")
    assert store.channels["telegram"].muted_until == NOW + timedelta(hours=2)
    assert "until 2025-03-11 12:00 UTC" in say(bot, "/mute")  # default 24 hours
    assert say(bot, "/mute abc") == "Usage: /mute [hours]"
    assert say(bot, "/mute 0").startswith("Choose between")
    assert say(bot, "/mute 100000").startswith("Choose between")
    assert say(bot, "/unmute") == "Telegram alerts resumed."
    assert store.channels["telegram"].muted_until is None


def test_a_failing_command_gets_a_generic_reply_not_a_crash(bot_env, monkeypatch):
    bot, *_ = bot_env
    monkeypatch.setattr(bot, "_top", lambda args: 1 / 0)
    assert say(bot, "/top") == "Something went wrong handling that command."


def test_poll_once_answers_commands_and_returns_the_next_offset(bot_env):
    bot, fake, *_ = bot_env
    fake.updates = [
        {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/watch AAA"}},
        {"update_id": 11, "message": {"chat": {"id": 999}, "text": "/watch BBB"}},  # stranger
        {"update_id": 12, "message": {"chat": {"id": 42}, "text": "no command"}},
    ]
    assert bot.poll_once(None, timeout=1) == 13
    assert fake.sent_texts() == ["Now watching AAA."]  # one reply; nothing for the stranger
    assert fake.calls[0][1] == "getUpdates" and "offset" not in fake.calls[0][2]
    fake.updates = []
    assert bot.poll_once(13, timeout=1) == 13
    assert fake.calls[-1][2]["offset"] == 13  # acknowledges what it has already handled


def test_the_offset_is_persisted_per_update_before_its_command_runs(bot_env, monkeypatch):
    bot, fake, *_ = bot_env
    assert bot.load_offset() is None
    seen = []

    def spy(update):
        seen.append(bot.load_offset())  # what a crash inside the command would leave behind
        return "ok"

    monkeypatch.setattr(bot, "handle_update", spy)
    fake.updates = [
        {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/watch AAA"}},
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/watch BBB"}},
    ]
    assert bot.poll_once(None, timeout=1) == 12
    assert seen == [11, 12]  # already acknowledged when each command started
    assert bot.load_offset() == 12


def test_a_failing_reply_never_replays_a_command(bot_env, monkeypatch):
    bot, fake, store, _ = bot_env
    fake.updates = [{"update_id": 5, "message": {"chat": {"id": 42}, "text": "/watch AAA"}}]

    def broken(text):
        raise ChannelError("telegram is down")

    monkeypatch.setattr(bot.client, "send_message", broken)
    assert bot.poll_once(None, timeout=1) == 6  # no exception escapes
    assert bot.load_offset() == 6
    assert sorted(str(t) for t in store.watched) == ["AAA"]  # the command ran exactly once
    # a restart resumes from the stored offset instead of asking for the update again
    fake.updates = []
    bot.poll_once(bot.load_offset(), timeout=1)
    assert fake.calls[-1][2]["offset"] == 6


def test_an_unreadable_offset_means_starting_fresh_and_a_unwritable_one_is_not_fatal(bot_env):
    bot, fake, *_ = bot_env

    def boom(*a):
        raise OSError("storage down")

    bot.commands.load_offset = boom
    bot.commands.save_offset = boom
    assert bot.load_offset() is None
    fake.updates = [{"update_id": 3, "message": {"chat": {"id": 42}, "text": "/help"}}]
    assert bot.poll_once(None, timeout=1) == 4  # still answers


def test_updates_without_an_id_are_skipped(bot_env):
    bot, fake, *_ = bot_env
    fake.updates = [{"message": {"chat": {"id": 42}, "text": "/watch AAA"}}]
    assert bot.poll_once(7, timeout=1) == 7 and fake.sent_texts() == []
