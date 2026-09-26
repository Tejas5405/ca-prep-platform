/**
 * `/terms` — the terms of use.
 *
 * The two clauses that carry real weight here, and why they are written plainly
 * rather than in the usual register:
 *
 *   1. NO AFFILIATION AND NO OUTCOME CLAIM. This is study material built from
 *      published past papers. It is not ICAI material, and nobody can promise that
 *      using it will pass an attempt. A terms page that implies either is the kind
 *      of claim a student would be right to hold against the product.
 *   2. THE CONTENT IS NOT YOURS TO REDISTRIBUTE. The platform's own organisation,
 *      extraction, review and interface are the product; the questions come from
 *      papers ICAI publishes. Bulk-downloading the bank and re-uploading it
 *      elsewhere is the one form of use that would end this service for everyone
 *      else, so it is the one restriction stated as a prohibition rather than as a
 *      request.
 */

import { PublicLayout } from '../../components/public/PublicLayout'
import { Seo } from '../../components/public/Seo'
import {
  ContactLine,
  LegalCrossLink,
  LegalHeader,
  LegalList,
  LegalSection,
} from '../../components/public/Legal'
import { Section } from '../../components/public/sections'

export default function TermsPage() {
  return (
    <PublicLayout>
      <Seo
        title="Terms of use — accounts, content and payment | CA Prep"
        description="The terms for using CA Prep: what the service is and is not, acceptable use of the question bank, account and payment terms, and the limits of the platform's liability."
        path="/terms"
      />

      <Section labelledBy="terms-heading">
        <div id="terms-heading">
          <LegalHeader
            eyebrow="Terms"
            title="Terms of use"
            intro="Plain terms for a study tool. By using CA Prep you accept these; if you do not, the correct action is to stop using it rather than to keep an account open."
          />
        </div>

        <LegalSection id="service" heading="What the service is">
          <LegalList
            items={[
              <>
                CA Prep is a preparation and practice service for the CA examinations: a question bank
                assembled from published ICAI past papers, chapter-wise practice, timed mock papers,
                revision scheduling, a study planner and progress reporting.
              </>,
              <>
                It is study material and software. It is <strong>not</strong> a coaching course, and it
                is not affiliated with, endorsed by or connected to the Institute of Chartered
                Accountants of India. ICAI&rsquo;s notifications on icai.org are the authority on exam
                dates, syllabus and format, and they override anything stated here or inside the
                product.
              </>,
              <>
                The service is provided as-is and may change: features are added, corrected or
                withdrawn, and content is edited when a reviewer finds a problem with it.
              </>,
            ]}
          />
        </LegalSection>

        <LegalSection id="accounts" heading="Your account">
          <LegalList
            items={[
              <>
                One account per person. Sharing a login is how one person&rsquo;s practice history ends
                up shaping another person&rsquo;s plan, and the results become meaningless for both.
              </>,
              <>
                You are responsible for keeping your sign-in credentials private. The platform never
                asks for your password outside the sign-in screen, and staff will never ask you for it.
              </>,
              <>
                Accounts may be suspended for abuse — scraping, attempting to reach other
                students&rsquo; data, or circumventing an entitlement check. Where a payment is
                involved, suspension follows the process below rather than an immediate lockout.
              </>,
              <>
                You may close your account at any time; the deletion behaviour is described in the{' '}
                <a href="/privacy" className="font-medium text-brand-600 hover:underline">
                  privacy page
                </a>
                .
              </>,
            ]}
          />
        </LegalSection>

        <LegalSection id="use" heading="Acceptable use of the content">
          <p>
            Your account lets you practise the questions, see the explanations and keep your own study
            record. It does not transfer ownership of the bank to you.
          </p>
          <LegalList
            items={[
              <>
                <strong>Do not</strong> bulk-download, scrape or systematically export the question
                bank, with or without automation. Personal use of individual questions — revising,
                making notes, discussing a question with a friend — is the point of the product and is
                fine.
              </>,
              <>
                <strong>Do not</strong> republish or resell questions, solutions or explanations, or
                present them as your own material or as ICAI&rsquo;s.
              </>,
              <>
                <strong>Do not</strong> upload material you have no right to upload. The ingestion
                pipeline is for papers you are entitled to process, and the account that uploads a file
                is recorded against it.
              </>,
              <>
                <strong>Do not</strong> attempt to break, overload or probe the service, or access data
                belonging to another account.
              </>,
            ]}
          />
          <p>
            The questions themselves originate in papers ICAI publishes; the organisation, extraction,
            review, metadata, explanations and interface are the product&rsquo;s own work.
          </p>
        </LegalSection>

        <LegalSection id="payment" heading="Plans and payment">
          <LegalList
            items={[
              <>
                Paid access is a prepaid period, shown on the pricing page, for the tier you choose.
                Prices come from the platform&rsquo;s catalogue; the amount charged is looked up on the
                server rather than accepted from the browser.
              </>,
              <>
                Payments are processed by Razorpay. Your access is activated only when a
                signature-verified webhook confirms the payment — never because a browser reported
                success. If a payment is taken and access does not follow, contact the address below and
                it will be reconciled against Razorpay&rsquo;s records, which are the source of truth.
              </>,
              <>
                Paid access runs to the end of its period and then falls back to the free tier. Your
                practice history, bookmarks and collections are not deleted when a paid period ends.
              </>,
              <>
                If the service is discontinued, notice is given before the end of the period you have
                paid for, and no renewal is charged after that notice.
              </>,
            ]}
          />
        </LegalSection>

        <LegalSection id="accuracy" heading="Content accuracy">
          <p>
            Every question that reaches students has been reviewed by a person and carries its source
            and verifier. That is a high standard and it is not a guarantee: ICAI materials contain
            errors, and taxation questions depend on the law in force for the year they were written.
          </p>
          <LegalList
            items={[
              <>
                Historical questions are labelled with the Finance Act year they belong to and are not
                presented as current law. If you are studying for a current attempt, treat those
                questions as pattern practice, not as the present position.
              </>,
              <>
                If something looks wrong, report it in the product. Reports go to the same queue the
                reviewers work from.
              </>,
              <>
                Answers are marked by the platform for objective questions. Descriptive answers are
                marked by a person, and the marks shown for them are indicative rather than an ICAI
                result.
              </>,
            ]}
          />
        </LegalSection>

        <LegalSection id="liability" heading="Limits">
          <p>
            The service is provided for preparation and study. No outcome is promised or implied: exam
            results depend on you, and on the exam. To the extent permitted by law, the operator is not
            liable for indirect or consequential loss arising from use of the service, including lost
            marks or a missed attempt. Where liability cannot be excluded, it is limited to the amount
            paid for the period in which the problem arose.
          </p>
          <p>
            Nothing here excludes liability that cannot lawfully be excluded, and nothing here removes
            your statutory rights as a consumer.
          </p>
        </LegalSection>

        <LegalSection id="availability" heading="Availability">
          <p>
            The service is operated on hosted infrastructure and will occasionally be unavailable for
            maintenance or because of a fault. Where a fault is significant, it is fixed rather than
            hidden; where planned work will interrupt access, it is scheduled outside peak study hours
            where possible.
          </p>
        </LegalSection>

        <LegalSection id="changes" heading="Changes to these terms">
          <p>
            When these terms change materially — a change to payment, entitlement or acceptable use —
            the date at the top changes and the change is announced in the product. Continuing to use
            the service after that is acceptance of the new terms.
          </p>
        </LegalSection>

        <div className="border-t border-slate-200 pt-8">
          <ContactLine />
          <div className="mt-2">
            <LegalCrossLink to="/privacy" label="Privacy" />
          </div>
        </div>
      </Section>
    </PublicLayout>
  )
}
