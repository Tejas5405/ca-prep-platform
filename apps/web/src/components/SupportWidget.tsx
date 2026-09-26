import { useState } from "react";

import { createTicket } from "../lib/campus";

/**
 * A ticket box, not a chat with a person who is online.
 * The server saves the text. It does not send email.
 */
export default function SupportWidget() {
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setNote(null);
    try {
      const saved = await createTicket(subject.trim(), body.trim());
      setNote(saved.emailSent ? "Unexpected: the server said an email was sent." : saved.note);
      setSubject("");
      setBody("");
    } catch (caught) {
      setNote(caught instanceof Error ? caught.message : "The ticket was not saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed bottom-4 right-4 z-20 w-72">
      {open && (
        <form
          className="mb-2 rounded-lg border border-slate-200 bg-white p-3 shadow-lg"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <p className="text-sm font-medium text-slate-900">Support</p>
          <p className="mt-1 text-xs text-slate-500">
            Saved for an operator to read. No email is sent.
          </p>
          <input
            className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            placeholder="Subject"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
          <textarea
            className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            rows={3}
            placeholder="What happened"
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
          <button
            type="submit"
            className="mt-2 rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            disabled={busy || subject.trim().length < 2 || body.trim().length < 2}
          >
            {busy ? "Saving" : "Save ticket"}
          </button>
          {note && <p className="mt-2 text-xs text-slate-600">{note}</p>}
        </form>
      )}
      <button
        type="button"
        className="rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow"
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "Close support" : "Support"}
      </button>
    </div>
  );
}
