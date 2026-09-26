/**
 * `/privacy` — what the platform collects and what it does with it.
 *
 * WRITTEN FROM THE SCHEMA, NOT FROM A TEMPLATE
 *
 * Every paragraph below describes something the code actually does: which fields
 * exist, which service holds them, and what happens on delete. That is the standard
 * this page is held to, because a privacy policy assembled from boilerplate is
 * worse than none — it makes claims about retention and sharing that nobody has
 * verified against the system.
 *
 * The two honest specifics worth calling out, because they are the ones a student
 * would not guess:
 *
 *   * the study record (every answer, and whether it was wrong) is the product's
 *     core data. It exists so practice can be marked and revision scheduled, and it
 *     is not sold, shared or used for advertising.
 *   * uploaded PDFs live in a PRIVATE bucket and are served only through signed
 *     URLs. There is no public URL for a source paper, which matters because these
 *     are ICAI's papers and the platform's right to hold them is a licence to
 *     process, not to republish.
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

export default function PrivacyPage() {
  return (
    <PublicLayout>
      <Seo
        title="Privacy — what CA Prep stores and why | CA Prep"
        description="What data CA Prep collects, which service holds it, how uploaded papers are stored, how long things are kept, and how to have your account and its data removed."
        path="/privacy"
      />

      <Section labelledBy="privacy-heading">
        <div id="privacy-heading">
          <LegalHeader
            eyebrow="Privacy"
            title="What this platform stores, and why"
            intro="CA Prep is a study tool, which means it necessarily keeps a record of what you practise and what you get wrong. This page describes exactly what is stored, which service holds it, and how to have it removed. It describes the system as it is, not as a template wishes it were."
          />
        </div>

        <LegalSection id="collected" heading="What is collected">
          <LegalList
            items={[
              <>
                <strong>Account details.</strong> Your email address, and your name if your sign-in
                provider supplies one. Passwords are never stored by this platform — they are held by
                Supabase Auth, and the platform stores only the identifier that links your profile to
                that account.
              </>,
              <>
                <strong>Study record.</strong> Every question you answer, the option you chose, whether
                it was correct, how long you took, whether you used a hint, and per-chapter accuracy
                summaries derived from those answers. This is the data that makes marking, revision
                scheduling and progress reporting work.
              </>,
              <>
                <strong>Your own inputs.</strong> Doubts you post, replies, bookmarks, collections you
                create, your target attempt date and study hours, and any profile details you fill in.
              </>,
              <>
                <strong>Uploaded papers, if you have a content role.</strong> A PDF you upload, its
                extracted text, and the draft questions produced from it, together with the review
                decisions made on them.
              </>,
              <>
                <strong>Payment records, if you subscribe.</strong> The identifiers Razorpay returns
                for an order or payment, the amount, the status and the resulting entitlement window.
                Card, UPI and bank details never reach this platform.
              </>,
              <>
                <strong>Operational logs.</strong> Requests to the API, with a generated request
                identifier, the route, the response status and any error. Logs are used to diagnose
                faults and are not used to build advertising profiles.
              </>,
            ]}
          />
        </LegalSection>

        <LegalSection id="why" heading="Why it is collected">
          <p>
            Three purposes, and nothing beyond them: to authenticate you, to mark your practice and
            schedule your revision, and to operate and secure the service. The study record is what
            makes the product work — a practice tool that forgets your answers cannot tell you what to
            revise.
          </p>
          <p>
            The platform does not sell personal data, does not share it with advertisers, and does not
            use your answers to train models.
          </p>
        </LegalSection>

        <LegalSection id="who" heading="Which services hold it">
          <LegalList
            items={[
              <>
                <strong>Supabase</strong> — authentication (email and OAuth identities) and file
                storage for uploaded papers and question media.
              </>,
              <>
                <strong>The application database (PostgreSQL)</strong> — profiles, the study record,
                content, ingestion jobs and subscription state.
              </>,
              <>
                <strong>Render</strong> — runs the API and the background worker that processes
                uploaded papers.
              </>,
              <>
                <strong>Vercel</strong> — serves the interface you are reading.
              </>,
              <>
                <strong>Razorpay</strong> — payment processing, when a paid plan is in use.
              </>,
              <>
                <strong>Error monitoring and analytics</strong> — error reports are sent to Sentry, and
                product analytics to PostHog, when those are enabled on a deployment. Analytics are
                limited to product events (a page viewed, a practice set started) and are not used for
                advertising.
              </>,
            ]}
          />
          <p>
            Each of these is a processor acting for the operator of this deployment; none of them is
            given your data to use for its own purposes.
          </p>
        </LegalSection>

        <LegalSection id="uploads" heading="Uploaded papers and private storage">
          <p>
            A source paper is stored in a private bucket. It is reachable only through a signed URL
            that expires, and only by an account with a content role. There is no public link to a
            source paper, and the file bytes are uploaded directly from the browser to storage rather
            than through the API.
          </p>
          <p>
            This matters beyond confidentiality: the papers are ICAI&rsquo;s, and the platform&rsquo;s
            basis for holding them is processing them into reviewed questions, not republishing them.
            Signed URLs exist so that a paper is available to the reviewer working on it and not to
            anyone else.
          </p>
        </LegalSection>

        <LegalSection id="cookies" heading="Cookies and local storage">
          <p>
            The platform sets no advertising or tracking cookies. Signing in stores a session in your
            browser&rsquo;s storage, issued by Supabase Auth, which is what keeps you signed in between
            visits and is sent to the API as a bearer token. Clearing site data or signing out removes
            it. No third-party cookie is required for the product to work.
          </p>
        </LegalSection>

        <LegalSection id="retention" heading="How long it is kept">
          <LegalList
            items={[
              <>
                Your account and study record are kept while your account exists. There is no automatic
                expiry.
              </>,
              <>
                Deleting your account removes the profile and everything keyed to it — answers,
                bookmarks, collections and doubts — through cascading deletes in the database, and the
                authentication identity is removed at Supabase.
              </>,
              <>
                Payment records are retained after cancellation, because tax and accounting obligations
                require a record of a completed transaction even when the account is gone.
              </>,
              <>
                Operational logs are retained for a bounded period on the hosting provider&rsquo;s
                platform and then rotated out.
              </>,
            ]}
          />
        </LegalSection>

        <LegalSection id="rights" heading="Access, correction and deletion">
          <p>
            You can see and edit your profile in the app, and your study history is visible on the
            progress and report screens. To have your account and its data removed, or to ask what is
            held, use the contact address below; requests are actioned on the account rather than by
            exporting a copy elsewhere.
          </p>
          <p>
            If you are a minor, use this platform with the involvement of a parent or guardian — CA
            aspirants are sometimes under 18, and the account is created in the student&rsquo;s own email
            address.
          </p>
        </LegalSection>

        <LegalSection id="security" heading="Security">
          <p>
            Sign-in is handled by Supabase Auth; the API verifies the signed token on every request and
            enforces roles server-side. Access to other people&rsquo;s data is refused at the database
            layer, not by the interface hiding a button. Uploaded files are private and served through
            time-limited signed URLs. Payment webhooks are signature-verified before any entitlement
            changes.
          </p>
          <p>
            No system is perfect. If you believe you have found a security problem, report it through
            the contact address rather than testing it against another student&rsquo;s data.
          </p>
        </LegalSection>

        <LegalSection id="changes" heading="Changes to this policy">
          <p>
            When something changes in what is stored or who processes it, this page is updated and the
            date at the top changes with it. Material changes — a new processor, a new category of
            data — are announced in the product rather than only here.
          </p>
        </LegalSection>

        <div className="border-t border-slate-200 pt-8">
          <ContactLine />
          <div className="mt-2">
            <LegalCrossLink to="/terms" label="Terms of use" />
          </div>
        </div>
      </Section>
    </PublicLayout>
  )
}
