# What was built, and what is still not in the product

Checked against the code, not against an older report. No live Razorpay charge was made. CI has not run. Nothing was deployed.

## Razorpay

Checkout was already wired. What was missing was a way to enter the keys once, and a price the order route actually reads.

The owner can save the keys under Admin → Payments, or set the same names on the API. Environment variables win if they are set. A saved secret is stored on the server and is not returned to the browser.

Checkout needs both of these. If either is missing, the order route answers 503 and names the missing one:

- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`

Webhooks also need `RAZORPAY_WEBHOOK_SECRET`. The browser confirmation path does not. A webhook only arrives if Razorpay can reach the deployed API. A local machine cannot receive that call.

This is not proven against a real Razorpay account. Entering a test key should open Checkout and the server will verify the signature and read the payment back. That last step has not been executed here, because no key was supplied and a captured payment must not be invented.

## Prices

Admin → Plans can save an amount. The next order and the public pricing page read that save. An order already created keeps the amount stored on that order. Premium Plus still defaults to ₹1,299. Entitlements are not edited on that screen.

## Intermediate papers

Students on CA Intermediate no longer see Taxation or the combined Paper 6. They see Direct Tax, Indirect Tax, Financial Management and Strategic Management. Foundation and Final are unchanged. A student cannot put the combined papers back by adding a query parameter. Staff can still see the parent papers, because questions are filed under them.

A question assigned to one split does not appear in the other. A question with no split and no mapped chapter appears in neither. Historical attempts were not rewritten.

## Law notices

An editor can record a citation. Published questions whose text contains that citation are flagged for review. The answer is not changed, and the notice cannot claim that it was. The platform does not invent a new statute.

## Leaderboard

The caller's own row now includes how many points are needed to pass the next person. It still does not return an email or a user id.

## Built in this pass

These exist as routes and tables. They do not do more than the sentence says.

- Question review states are `CURRENT`, `VERIFICATION_REQUIRED`, `NEEDS_UPDATE`, and `PENDING_ADMIN_REVIEW`. They are a column, not a replacement for published/draft. Admin → Question review runs the job. Open historical flags and law-notice matches become `NEEDS_UPDATE`. Other open flags become `PENDING_ADMIN_REVIEW`. The answer is not rewritten and nothing is auto-published. There is no clock that runs the job by itself.
- Study groups, mentorship requests, a forum, a daily challenge drawn from a published MCQ, formula notes, and a glossary. Formula notes are empty until an editor publishes one. The glossary shipped with product definitions, not statutes. Forum posts are student discussion, not rulings. A mentorship request does not assign a mentor and does not send a message.
- Practice trend at `/progress/projection`. It is recent practice accuracy. `examScore` is always null. Fewer than five graded attempts returns no rate.
- Exam mode on a mock (`?exam=1`) records the sitting and tab-hidden events. It does not open a camera or lock the browser. The mock route already hides answers until submit.
- A calendar of events the student adds. It is not synced to Google or ICAI.
- A pomodoro timer in the browser. Saving it records what the student reported. The server did not time it.
- Referral codes can be redeemed. That records a relationship. It does not grant Premium.
- The assistant calls Gemini only when `features.ai_assistant` is on, `AI_PROVIDER_API_KEY` is set on the server, and an excerpt the student may read was found. The reply is labelled a study suggestion, not a legal authority. No excerpts, a failed call, or a missing key leaves `answer` null. The key is not sent to the browser. It was pasted in chat, so rotate it before real users.
- An experiment assignment is stored only after an owner creates an experiment and turns it on. None is created by default, so the study page reports that nothing was assigned. It does not measure a conversion.
- The support widget saves a ticket. `emailSent` is constrained false.
- Marketplace listings can be added by an owner. `takes_payment` is constrained false. Checkout remains the upgrade page, and it still needs a Razorpay key pair.
- A web app manifest is linked. The service worker registers only in a production build, and it does not cache `/api`. The only icon is the SVG favicon, so some browsers will not offer a home-screen install.

## Still not in the product

- Video solutions. Those stay removed.
- A live Razorpay charge. No key pair was used to capture a payment.
- Email delivery. Tickets and mentorship requests are stored only.
- A camera, a locked browser, or a remote proctor.
- A predicted ICAI mark.
- A formula that this platform invented. Editors have to write notes.
- A scheduled job runner. Re-verification runs when an operator presses the button.
- CI. It has not run. Nothing was deployed.

## Proof run for this pass

- Gemini `gemini-3.8-flash` answered a one-word probe. `gemini-2.5-flash` returned 404 for this key. The server default is now `gemini-3.8-flash`. The key was not printed and is not in the OpenAPI file.
- `tests/integration/test_postgres_campus.py`: a referral does not grant Premium, a ticket does not send email, the practice trend has no exam score, and the review job marks a historical flag `NEEDS_UPDATE` without changing the answer or publishing.
- Assistant studio test still sees `answer` null and `generatesAnswers` false when the key is absent. That is the test environment. The local `caprep` database has `features.ai_assistant` on. The API process must be restarted to read the key from the root `.env`.
- OpenAPI was regenerated. `alembic check` reported no new upgrade operations. The known `document_pages.search_vector` warning remains and was not "fixed".
- The full API suite and the frontend suite were not re-run. CI has not run. Nothing was deployed. No Razorpay charge was made.

## Proof run here

- `tests/integration/test_postgres_spec_remainder.py`: 5 passed. Student list hides the combined papers. A GST chapter question appears under Indirect Tax and not Direct Tax. A saved price of ₹1,499 is what the catalogue then shows. A saved Razorpay secret is absent from the response. A law notice does not change the answer.
- Payment HTTP tests and the studio PostgreSQL tests passed, including the revision test that a later key does not rewrite a student's mark.
- OpenAPI was regenerated. The contract test passed. `alembic check` reported no new upgrade operations.
- Frontend: eslint clean on the edited screens. vitest admin console and achievements: 27 passed.

The full suite was not re-run.
