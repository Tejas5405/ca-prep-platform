/**
 * The document kinds the API accepts, in one place.
 *
 * THE LIST WAS ALREADY IN THREE PLACES and had already drifted from the database: the
 * bulk-upload select and the access-rule select offered "Past paper" and "Syllabus"
 * while `ck_document_kind` accepted neither, and the API validated only the string
 * length. Choosing "Past paper" for a past paper therefore reached PostgreSQL, failed
 * the CHECK, and surfaced as a 500 carrying a database message.
 *
 * It must stay equal to `app.models.enums.DocumentKind` on the server, which is now the
 * single source for the model CHECK, the migration and the request schemas.
 * `src/tests/contentKinds.test.ts` pins this list so a change to one side without the
 * other fails a test rather than an operator's upload.
 */
export const DOCUMENT_KINDS = [
  'STUDY_MATERIAL',
  'NOTES',
  'PAST_PAPER',
  'QUESTION_BANK',
  'TEST_SERIES',
  'SYLLABUS',
  'REFERENCE',
  'OTHER',
] as const

export type DocumentKind = (typeof DOCUMENT_KINDS)[number]

const LABELS: Record<DocumentKind, string> = {
  STUDY_MATERIAL: 'Study material',
  NOTES: 'Notes',
  PAST_PAPER: 'Past paper (ICAI attempt paper)',
  QUESTION_BANK: 'Question bank',
  TEST_SERIES: 'Test series paper',
  SYLLABUS: 'Syllabus mapping',
  REFERENCE: 'Reference (bare Act, standard)',
  OTHER: 'Other',
}

export function documentKindLabel(kind: string): string {
  return LABELS[kind as DocumentKind] ?? kind.replace(/_/g, ' ').toLowerCase()
}
