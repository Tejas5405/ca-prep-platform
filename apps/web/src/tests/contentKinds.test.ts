import { describe, expect, it } from 'vitest'

import { DOCUMENT_KINDS, documentKindLabel } from '../lib/contentKinds'

/**
 * The document-kind vocabulary is shared across a language boundary, and it drifted.
 *
 * `ck_document_kind` accepted six kinds; the web console offered six, two of which the
 * constraint rejected. The API validated length only, so "Past paper" - the second most
 * obvious thing to upload to a CA preparation library - was refused by PostgreSQL and
 * surfaced to the operator as a 500. The server side is now a single enum
 * (`app.models.enums.DocumentKind`) asserted against the CHECK constraint and the
 * OpenAPI document in `tests/test_schema_contract.py`.
 *
 * This is the other half of that guard. There is no build step that reads the Python
 * enum, so the list is pinned instead: `DOCUMENT_KINDS` in `src/lib/contentKinds.ts` is
 * the only place the web build names a kind, and changing it without changing the enum
 * (or the reverse) fails one of the two suites rather than an upload.
 */
describe('document kinds', () => {
  it('is exactly the vocabulary the API accepts', () => {
    expect([...DOCUMENT_KINDS]).toEqual([
      'STUDY_MATERIAL',
      'NOTES',
      'PAST_PAPER',
      'QUESTION_BANK',
      'TEST_SERIES',
      'SYLLABUS',
      'REFERENCE',
      'OTHER',
    ])
  })

  it('has no duplicates, and every value is already upper snake case', () => {
    // The value goes into a JSON body and into a CHECK constraint: a lower-case or
    // repeated kind would be accepted by the form and refused by the database.
    expect(new Set(DOCUMENT_KINDS).size).toBe(DOCUMENT_KINDS.length)
    for (const kind of DOCUMENT_KINDS) {
      expect(kind).toMatch(/^[A-Z][A-Z_]*$/)
    }
  })

  it('labels every kind for the operator, naming the ones that need it', () => {
    // The three that are easy to confuse get a parenthetical: PAST_PAPER vs
    // QUESTION_BANK vs TEST_SERIES are all "a set of questions" to a new operator, and
    // the difference matters, because the pipeline files them differently.
    expect(documentKindLabel('PAST_PAPER')).toBe('Past paper (ICAI attempt paper)')
    expect(documentKindLabel('QUESTION_BANK')).toBe('Question bank')
    expect(documentKindLabel('TEST_SERIES')).toBe('Test series paper')
    expect(documentKindLabel('SYLLABUS')).toBe('Syllabus mapping')
    for (const kind of DOCUMENT_KINDS) {
      expect(documentKindLabel(kind)).not.toBe('')
      expect(documentKindLabel(kind)).not.toContain('_')
    }
  })

  it('falls back to a readable name for a kind it does not know', () => {
    // A document stored under a kind this build predates (an older row, or a newer
    // server) must still render as words rather than as an identifier.
    expect(documentKindLabel('LEGACY_KIND')).toBe('legacy kind')
  })
})
