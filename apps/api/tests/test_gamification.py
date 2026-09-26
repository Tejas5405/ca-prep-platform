"""Points ledger, levels, streaks, badges and referrals.

Includes the specific regression that a bug in the earlier implementation
introduced: 0-based vs 1-based month handling in day-gap arithmetic, which
silently reset streaks across every month and year boundary.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.gamification import (
    BADGES,
    POINTS,
    CreditBalance,
    LedgerEntry,
    PointsReason,
    StreakState,
    UserStats,
    accepted_answer_points,
    activate_referral,
    apply_activity,
    apply_credit,
    award,
    evaluate_badges,
    format_inr,
    generate_code,
    ledger_key,
    level_for,
    level_progress,
)


class TestPointsLedgerIsIdempotent:
    def test_awards_once_for_a_first_time_action(self):
        r = award(LedgerEntry("u1", PointsReason.MOCK_COMPLETED, "attempt-1"), set())
        assert r.awarded is True
        assert r.amount == POINTS[PointsReason.MOCK_COMPLETED]

    def test_refuses_to_double_award_on_a_replayed_job(self):
        entry = LedgerEntry("u1", PointsReason.MOCK_COMPLETED, "attempt-1")
        existing = {ledger_key(entry)}
        r = award(entry, existing)
        assert r.awarded is False
        assert r.amount == 0
        assert r.duplicate_of == "u1::MOCK_COMPLETED::attempt-1"

    def test_idempotency_is_scoped_per_user(self):
        a = LedgerEntry("u1", PointsReason.DAILY_LOGIN, "2026-09-24")
        b = LedgerEntry("u2", PointsReason.DAILY_LOGIN, "2026-09-24")
        assert ledger_key(a) != ledger_key(b)
        assert award(b, {ledger_key(a)}).awarded is True

    def test_null_ref_does_not_collapse_unrelated_awards(self):
        a = LedgerEntry("u1", PointsReason.DAILY_LOGIN, None)
        b = LedgerEntry("u1", PointsReason.QUESTION_ATTEMPTED, None)
        assert ledger_key(a) != ledger_key(b)


class TestAcceptedAnswerAntiGaming:
    def test_awards_when_someone_else_accepts(self):
        assert (
            accepted_answer_points("helper", "asker", "asker")
            == POINTS[PointsReason.DOUBT_ANSWER_ACCEPTED]
        )

    def test_zero_when_you_accept_your_own_answer(self):
        assert accepted_answer_points("sock", "sock", "sock") == 0

    def test_zero_when_a_helper_tries_to_self_credit(self):
        assert accepted_answer_points("helper", "asker", "helper") == 0


class TestLevels:
    @pytest.mark.parametrize(
        "points,expected",
        [
            (0, "BEGINNER"),
            (499, "BEGINNER"),
            (500, "INTERMEDIATE"),
            (1999, "INTERMEDIATE"),
            (2000, "ADVANCED"),
            (5000, "EXPERT"),
            (9999, "EXPERT"),
            (10000, "MASTER"),
            (10**9, "MASTER"),
        ],
    )
    def test_maps_points_to_documented_boundaries(self, points, expected):
        assert level_for(points) == expected

    def test_reports_progress_toward_next_level(self):
        p = level_progress(1250)
        assert p.level == "INTERMEDIATE"
        assert p.next_level == "ADVANCED"
        assert p.points_to_next == 750
        assert p.progress == pytest.approx(0.5)

    def test_caps_progress_at_top_level(self):
        p = level_progress(20_000)
        assert p.next_level is None
        assert p.progress == 1.0
        assert p.points_to_next == 0


class TestStreaks:
    def test_starts_at_one(self):
        assert apply_activity(StreakState(), date(2026, 9, 24)) == StreakState(
            1, 1, date(2026, 9, 24)
        )

    def test_is_idempotent_within_a_day(self):
        s = StreakState(4, 4, date(2026, 9, 24))
        assert apply_activity(s, date(2026, 9, 24)) == s

    def test_increments_on_consecutive_days(self):
        nxt = apply_activity(StreakState(6, 6, date(2026, 9, 24)), date(2026, 9, 25))
        assert nxt.current == 7
        assert nxt.longest == 7

    def test_resets_after_a_missed_day_but_keeps_longest(self):
        nxt = apply_activity(StreakState(30, 30, date(2026, 9, 20)), date(2026, 9, 24))
        assert nxt.current == 1
        assert nxt.longest == 30

    def test_does_not_break_on_out_of_order_processing(self):
        s = StreakState(9, 9, date(2026, 9, 24))
        assert apply_activity(s, date(2026, 9, 20)) == s

    # --- month / year / leap boundaries: the previous implementation's bug ---

    def test_month_boundary(self):
        # September has 30 days: 30 Sep -> 1 Oct is a 1-day gap.
        nxt = apply_activity(StreakState(1, 1, date(2026, 9, 30)), date(2026, 10, 1))
        assert nxt.current == 2, "a 1-day gap across a month boundary broke the streak"

    def test_year_boundary(self):
        nxt = apply_activity(StreakState(12, 12, date(2026, 12, 31)), date(2027, 1, 1))
        assert nxt.current == 13

    def test_leap_day_within_a_run(self):
        nxt = apply_activity(StreakState(5, 5, date(2028, 2, 28)), date(2028, 2, 29))
        assert nxt.current == 6
        after = apply_activity(nxt, date(2028, 3, 1))
        assert after.current == 7

    def test_gap_across_month_is_two_days_not_three(self):
        # 30 Sep -> 2 Oct = 2 days = missed a day = reset
        nxt = apply_activity(StreakState(10, 10, date(2026, 9, 30)), date(2026, 10, 2))
        assert nxt.current == 1
        assert nxt.longest == 10


class TestBadges:
    BASE = UserStats()

    def test_awards_streak_badge_at_threshold(self):
        earned = evaluate_badges(UserStats(streak_current=7), set())
        assert "STREAK_7" in [b.code for b in earned]

    def test_awards_nothing_below_threshold(self):
        assert evaluate_badges(UserStats(streak_current=6), set()) == []

    def test_does_not_re_award_owned_badges(self):
        earned = evaluate_badges(UserStats(streak_current=100), {"STREAK_7", "STREAK_30"})
        assert [b.code for b in earned] == ["STREAK_100"]

    def test_can_award_several_at_once(self):
        earned = evaluate_badges(
            UserStats(streak_current=30, questions_in_day=150, accepted_answers=60),
            set(),
        )
        assert sorted(b.code for b in earned) == [
            "COMMUNITY_50",
            "STREAK_30",
            "STREAK_7",
            "VOLUME_100_DAY",
        ]

    def test_badge_codes_are_unique(self):
        codes = [b.code for b in BADGES]
        assert len(codes) == len(set(codes))


class TestReferralCodes:
    def test_uses_name_prefix(self):
        code = generate_code("John", 42)
        assert code.startswith("JOH")
        assert code.isalnum()

    def test_generated_suffix_avoids_ambiguous_characters(self):
        # The prefix comes from a real name, so it may contain I/O/0/1.
        # The unambiguity rule applies to the part users read aloud and type.
        for seed in range(1, 501):
            suffix = generate_code("Zed", seed)[3:]
            assert not any(c in suffix for c in "IO01")

    def test_is_deterministic_for_a_seed(self):
        assert generate_code("Meera", 7) == generate_code("Meera", 7)

    def test_handles_names_without_letters(self):
        code = generate_code("!!!", 5)
        assert len(code) == 6


class TestReferralActivation:
    def _activate(self, **kw):
        base = {
            "referrer_id": "u1",
            "referee_id": "u2",
            "current_status": None,
            "new_status": None,
            "prior_activations": 0,
        }
        base.update(kw)
        from app.services.gamification import ReferralStatus

        base["current_status"] = base["current_status"] or ReferralStatus.PENDING
        base["new_status"] = base["new_status"] or ReferralStatus.SUBSCRIBED
        return activate_referral(**base)

    def test_credits_fifty_rupees_on_subscription(self):
        r = self._activate()
        assert r.awarded is True
        assert r.credit_paise == 5000
        assert format_inr(r.credit_paise) == "\u20b950"

    def test_refuses_to_credit_twice_on_replayed_webhook(self):
        from app.services.gamification import ReferralStatus

        r = self._activate(
            current_status=ReferralStatus.SUBSCRIBED,
            new_status=ReferralStatus.SUBSCRIBED,
            prior_activations=1,
        )
        assert r.awarded is False
        assert r.credit_paise == 0
        assert r.reason == "ALREADY_ACTIVATED"

    def test_pays_nothing_for_signup_alone(self):
        from app.services.gamification import ReferralStatus

        r = self._activate(new_status=ReferralStatus.ACTIVATED)
        assert r.awarded is True
        assert r.credit_paise == 0

    def test_blocks_self_referral(self):
        r = self._activate(referrer_id="u1", referee_id="u1")
        assert r.awarded is False
        assert r.reason == "SELF_REFERRAL"

    def test_grants_three_months_at_exactly_five_activations(self):
        assert self._activate(prior_activations=4).tier_bonus_months == 3

    def test_no_bonus_at_four_or_six(self):
        assert self._activate(prior_activations=3).tier_bonus_months == 0
        assert self._activate(prior_activations=5).tier_bonus_months == 0


class TestCreditArithmetic:
    def test_adds_credit_and_tracks_lifetime_earned(self):
        b = apply_credit(CreditBalance(), 5000)
        assert b.balance_paise == 5000
        assert b.lifetime_earned_paise == 5000

    def test_spends_credit_and_tracks_lifetime_spent(self):
        b = apply_credit(CreditBalance(8000, 8000, 0), -3000)
        assert b.balance_paise == 5000
        assert b.lifetime_spent_paise == 3000

    def test_never_allows_a_negative_balance(self):
        with pytest.raises(ValueError, match="negative"):
            apply_credit(CreditBalance(1000, 1000, 0), -2000)

    def test_is_exact_over_many_operations(self):
        b = CreditBalance()
        for _ in range(1000):
            b = apply_credit(b, 5000)
        for _ in range(1000):
            b = apply_credit(b, -4999)
        assert b.balance_paise == 1000
        assert isinstance(b.balance_paise, int)


class TestFormatInr:
    def test_renders_rupees(self):
        assert format_inr(0) == "\u20b90"
        assert format_inr(99_900) == "\u20b9999"
        assert format_inr(123_456) == "\u20b91,234.56"
