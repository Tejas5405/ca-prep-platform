/**
 * Student reads of the gamification endpoints.
 *
 * These are reads over data the practice loop already writes. Nothing here awards
 * points or decides a rank: both come back computed, and a second computation in
 * the browser would be the one that drifts.
 *
 * The leaderboard payload is aggregate on purpose — a display name, points, a rank.
 * There is no user id and no email in the type, so a screen cannot render one by
 * accident. `isYou` is the only identity the caller is given about a row.
 */

import { api, type ApiSuccess } from './api'
import { QueryError } from './queries'

export interface PointsEntry {
  points: number
  reason: string
  referenceId: string | null
  createdAt: string | null
}

export interface PointsSummary {
  total: number
  lastSevenDays: number
  recent: PointsEntry[]
}

export interface Streak {
  current: number
  longest: number
  activeDays: number
}

export interface BadgeProgress {
  code: string
  name: string
  description: string
  icon: string | null
  criteriaKind: string
  criteriaValue: number | null
  pointsReward: number
  earned: boolean
  earnedAt: string | null
  /**
   * Count toward the criterion, or null when the server cannot reconstruct it.
   * Null is not zero: a bar at 0% would claim the student has not started, which
   * is a different fact from "we do not know".
   */
  progress: number | null
}

export interface BadgeBoard {
  badges: BadgeProgress[]
  earnedCount: number
  totalCount: number
}

export interface AchievementSummary {
  points: number
  badgesEarned: number
  badgesAvailable: number
  rank: number
  totalRanked: number
}

export type LeaderboardWindow = 'week' | 'month' | 'all'

export interface LeaderboardEntry {
  rank: number
  name: string
  points: number
  avatarUrl: string | null
  isYou: boolean
}

export interface Leaderboard {
  window: LeaderboardWindow
  entries: LeaderboardEntry[]
  you: { rank: number; points: number; pointsToNext?: number | null }
}

const REASONS: Record<string, string> = {
  QUESTION_CORRECT: 'Correct answer',
  CHAPTER_COMPLETE: 'Chapter finished',
  MOCK_COMPLETE: 'Mock finished',
  MOCK_HIGH_SCORE: 'High mock score',
  STREAK_DAY: 'Streak day',
  STREAK_MILESTONE: 'Streak milestone',
  DAILY_CHALLENGE: 'Daily challenge',
  BADGE_AWARDED: 'Badge awarded',
  REFERRAL_SIGNUP: 'Referral',
  REFERRAL_CONVERSION: 'Referral converted',
  ADMIN_ADJUSTMENT: 'Adjustment',
}

export function pointsReasonLabel(reason: string): string {
  return REASONS[reason] ?? reason.replace(/_/g, ' ').toLowerCase()
}

function unwrap<T>(path: string, signal?: AbortSignal): Promise<T> {
  return api
    .get<ApiSuccess<T>>(path, signal)
    .then((body) => body.data)
    .catch((error: unknown) => {
      throw new QueryError(error)
    })
}

export function fetchPoints(signal?: AbortSignal) {
  return unwrap<PointsSummary>('/gamification/points', signal)
}

export function fetchStreak(signal?: AbortSignal) {
  return unwrap<Streak>('/gamification/streak', signal)
}

export function fetchBadgeBoard(signal?: AbortSignal) {
  return unwrap<BadgeBoard>('/gamification/badges', signal)
}

export function fetchAchievements(signal?: AbortSignal) {
  return unwrap<AchievementSummary>('/gamification/achievements', signal)
}

export function fetchLeaderboard(window: LeaderboardWindow, signal?: AbortSignal) {
  const query = new URLSearchParams({ window, limit: '25' })
  return unwrap<Leaderboard>(`/gamification/leaderboard?${query}`, signal)
}
